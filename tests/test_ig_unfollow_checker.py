"""
Tests for ig_unfollow_checker.py

Unit tests use mock data (no network, no Playwright).
Smoke tests use a real browser (marked with @pytest.mark.smoke).

Run:
  pytest tests/                    # unit tests only (fast, no network)
  pytest tests/ -m smoke           # smoke tests only (needs Playwright + internet)
  pytest tests/ -m "not smoke"     # explicitly skip smoke
  pytest tests/ --run-smoke        # all tests including smoke
"""

import json
import os
import sys
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from ig_unfollow_checker import (
    extract_usernames_from_zip,
    extract_extra_lists_from_zip,
    load_usernames_from_file,
    diff_exports,
    check_account,
    extract_bio,
    assert_logged_out,
    load_checkpoint,
    write_bios,
    _cap,
    Pacer,
    generate_html,
    generate_summary_html,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FOLLOWING_HTML_TEMPLATE = """
<html><body>
{links}
</body></html>
"""

FOLLOWERS_HTML_TEMPLATE = """
<html><body>
{links}
</body></html>
"""


def _make_following_link(username):
    return f'<a href="https://www.instagram.com/_u/{username}">{username}</a>'


def _make_follower_link(username):
    return f'<a href="https://www.instagram.com/{username}">{username}</a>'


@pytest.fixture
def make_ig_zip(tmp_path):
    """Factory fixture: creates a fake IG export zip with given followers/following."""

    def _make(following: list[str], followers: list[str]) -> Path:
        following_html = FOLLOWING_HTML_TEMPLATE.format(
            links="\n".join(_make_following_link(u) for u in following)
        )
        followers_html = FOLLOWERS_HTML_TEMPLATE.format(
            links="\n".join(_make_follower_link(u) for u in followers)
        )

        zip_path = tmp_path / "instagram-test-2026-04-05-XXXXXXXX.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr(
                "connections/followers_and_following/following.html",
                following_html,
            )
            zf.writestr(
                "connections/followers_and_following/followers_1.html",
                followers_html,
            )
        return zip_path

    return _make


@pytest.fixture
def mock_page():
    """Create a mock Playwright page object."""
    page = MagicMock()
    page.wait_for_timeout = MagicMock()
    return page


# ---------------------------------------------------------------------------
# Unit Tests — extract_usernames_from_zip
# ---------------------------------------------------------------------------


class TestExtractUsernames:
    def test_basic_extraction(self, make_ig_zip):
        following = ["alice", "bob", "charlie"]
        followers = ["alice", "dave"]
        zp = make_ig_zip(following, followers)

        got_following, got_followers = extract_usernames_from_zip(str(zp))

        assert got_following == {"alice", "bob", "charlie"}
        assert got_followers == {"alice", "dave"}

    def test_not_following_back(self, make_ig_zip):
        following = ["alice", "bob", "charlie", "dave"]
        followers = ["alice", "charlie"]
        zp = make_ig_zip(following, followers)

        got_following, got_followers = extract_usernames_from_zip(str(zp))
        not_following_back = got_following - got_followers

        assert not_following_back == {"bob", "dave"}

    def test_everyone_follows_back(self, make_ig_zip):
        users = ["alice", "bob"]
        zp = make_ig_zip(users, users)

        got_following, got_followers = extract_usernames_from_zip(str(zp))
        assert got_following - got_followers == set()

    def test_empty_lists(self, make_ig_zip):
        zp = make_ig_zip([], [])
        got_following, got_followers = extract_usernames_from_zip(str(zp))
        assert got_following == set()
        assert got_followers == set()

    def test_usernames_with_dots_and_underscores(self, make_ig_zip):
        following = ["user.name", "_under_score_", "dots.and_underscores.mix"]
        followers = ["user.name"]
        zp = make_ig_zip(following, followers)

        got_following, got_followers = extract_usernames_from_zip(str(zp))
        assert "user.name" in got_following
        assert "_under_score_" in got_following
        assert "dots.and_underscores.mix" in got_following

    def test_usernames_with_numbers(self, make_ig_zip):
        following = ["user123", "456user", "us3r_n4m3"]
        zp = make_ig_zip(following, [])

        got_following, _ = extract_usernames_from_zip(str(zp))
        assert got_following == {"user123", "456user", "us3r_n4m3"}

    def test_duplicate_usernames_deduplicated(self, make_ig_zip):
        """If the same username appears multiple times, it should be deduplicated."""
        following = ["alice", "alice", "bob"]
        zp = make_ig_zip(following, [])

        got_following, _ = extract_usernames_from_zip(str(zp))
        assert got_following == {"alice", "bob"}

    def test_missing_files_in_zip(self, tmp_path):
        """Should exit if required files are not in the zip."""
        zip_path = tmp_path / "bad.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("some_other_file.html", "<html></html>")

        with pytest.raises(SystemExit):
            extract_usernames_from_zip(str(zip_path))

    def test_large_list(self, make_ig_zip):
        """Handles a list of 1000+ usernames."""
        following = [f"user_{i}" for i in range(1500)]
        followers = [f"user_{i}" for i in range(1000)]
        zp = make_ig_zip(following, followers)

        got_following, got_followers = extract_usernames_from_zip(str(zp))
        not_following_back = got_following - got_followers

        assert len(not_following_back) == 500
        assert "user_1000" in not_following_back
        assert "user_0" not in not_following_back


# ---------------------------------------------------------------------------
# Unit Tests — check_account (mocked Playwright)
# ---------------------------------------------------------------------------


class TestCheckAccount:
    def test_exists(self, mock_page):
        mock_page.title.return_value = "Cristiano Ronaldo (@cristiano) • Instagram photos and videos"
        mock_page.url = "https://www.instagram.com/cristiano/"

        result = check_account(mock_page, "cristiano")
        assert result["status"] == "EXISTS"
        assert result["username"] == "cristiano"

    def test_not_found(self, mock_page):
        mock_page.title.return_value = "Profile isn't available • Instagram"
        mock_page.url = "https://www.instagram.com/deleteduser123/"

        result = check_account(mock_page, "deleteduser123")
        assert result["status"] == "NOT_FOUND"

    def test_not_found_page_not_found_title(self, mock_page):
        mock_page.title.return_value = "Page Not Found • Instagram"
        mock_page.url = "https://www.instagram.com/gone_user/"

        result = check_account(mock_page, "gone_user")
        assert result["status"] == "NOT_FOUND"

    def test_login_wall(self, mock_page):
        mock_page.title.return_value = "Instagram"
        mock_page.url = "https://www.instagram.com/accounts/login/?next=%2Fsomeuser%2F"

        result = check_account(mock_page, "someuser")
        assert result["status"] == "LOGIN_WALL"

    def test_private_account(self, mock_page):
        mock_page.title.return_value = "Instagram"  # private accounts don't show name in title
        mock_page.url = "https://www.instagram.com/private_user/"
        locator = MagicMock()
        locator.inner_text.return_value = "This Account is Private\nFollow to see their photos"
        mock_page.locator.return_value = locator

        result = check_account(mock_page, "private_user")
        assert result["status"] == "EXISTS_PRIVATE"

    def test_network_error(self, mock_page):
        mock_page.goto.side_effect = Exception("net::ERR_CONNECTION_TIMED_OUT")

        result = check_account(mock_page, "timeout_user")
        assert result["status"] == "ERROR"
        assert "ERR_CONNECTION_TIMED_OUT" in result["error"]

    def test_unknown_status(self, mock_page):
        mock_page.title.return_value = "Instagram"
        mock_page.url = "https://www.instagram.com/weird_page/"
        locator = MagicMock()
        locator.inner_text.return_value = "Something unexpected happened"
        mock_page.locator.return_value = locator

        result = check_account(mock_page, "weird_page")
        assert result["status"] == "UNKNOWN"

    def test_exists_via_photos_and_videos(self, mock_page):
        """Account matched via 'Instagram photos and videos' in title without @ pattern."""
        mock_page.title.return_value = "Some Name • Instagram photos and videos"
        mock_page.url = "https://www.instagram.com/somename/"

        result = check_account(mock_page, "somename")
        assert result["status"] == "EXISTS"


# ---------------------------------------------------------------------------
# Unit Tests — generate_html
# ---------------------------------------------------------------------------


class TestGenerateHtml:
    def test_basic_output(self, tmp_path):
        outfile = str(tmp_path / "test.html")
        generate_html(
            ["alice", "bob"],
            "Test Title",
            "Test subtitle",
            "#ff0000",
            outfile,
        )

        content = Path(outfile).read_text()
        assert "<html>" in content
        assert "Test Title" in content
        assert "Test subtitle" in content
        assert "alice" in content
        assert "bob" in content
        assert "instagram.com/alice/" in content
        assert "instagram.com/bob/" in content

    def test_empty_list(self, tmp_path):
        outfile = str(tmp_path / "empty.html")
        generate_html([], "Empty", "No accounts", "#fff", outfile)

        content = Path(outfile).read_text()
        assert "Empty — 0" in content

    def test_click_tracking(self, tmp_path):
        outfile = str(tmp_path / "tracked.html")
        generate_html(
            ["user1", "user2"],
            "Tracked",
            "With tracking",
            "#4fc3f7",
            outfile,
            track_clicks=True,
        )

        content = Path(outfile).read_text()
        assert "trackClick" in content
        assert "localStorage" in content
        assert "clicked-count" in content
        assert 'data-user="user1"' in content

    def test_no_click_tracking(self, tmp_path):
        outfile = str(tmp_path / "untracked.html")
        generate_html(
            ["user1"],
            "Untracked",
            "No tracking",
            "#ff5252",
            outfile,
            track_clicks=False,
        )

        content = Path(outfile).read_text()
        assert "trackClick" not in content
        assert "localStorage" not in content

    def test_special_characters_in_title(self, tmp_path):
        outfile = str(tmp_path / "special.html")
        generate_html(
            ["user1"],
            "Title with <special> & \"chars\"",
            "Subtitle",
            "#fff",
            outfile,
        )
        assert Path(outfile).exists()

    def test_account_count_in_title(self, tmp_path):
        outfile = str(tmp_path / "count.html")
        generate_html(["a", "b", "c"], "Accounts", "Sub", "#fff", outfile)

        content = Path(outfile).read_text()
        assert "Accounts — 3" in content
        assert "<title>Accounts (3)</title>" in content


# ---------------------------------------------------------------------------
# Unit Tests — load_usernames_from_file
# ---------------------------------------------------------------------------


class TestLoadUsernamesFromFile:
    def test_plain_usernames(self, tmp_path):
        f = tmp_path / "users.txt"
        f.write_text("alice\nbob\ncharlie\n")
        result = load_usernames_from_file(str(f))
        assert result == ["alice", "bob", "charlie"]

    def test_with_at_signs(self, tmp_path):
        f = tmp_path / "users.txt"
        f.write_text("@alice\n@bob\n")
        result = load_usernames_from_file(str(f))
        assert result == ["alice", "bob"]

    def test_with_full_urls(self, tmp_path):
        f = tmp_path / "users.txt"
        f.write_text("https://www.instagram.com/alice/\nhttps://instagram.com/_u/bob\n")
        result = load_usernames_from_file(str(f))
        assert result == ["alice", "bob"]

    def test_comments_and_blank_lines(self, tmp_path):
        f = tmp_path / "users.txt"
        f.write_text("# this is a comment\nalice\n\n  \n# another comment\nbob\n")
        result = load_usernames_from_file(str(f))
        assert result == ["alice", "bob"]

    def test_mixed_formats(self, tmp_path):
        f = tmp_path / "users.txt"
        f.write_text("alice\n@bob\nhttps://www.instagram.com/charlie/\n# skip\ndave\n")
        result = load_usernames_from_file(str(f))
        assert result == ["alice", "bob", "charlie", "dave"]

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_text("")
        result = load_usernames_from_file(str(f))
        assert result == []

    def test_rejects_xss_payload(self, tmp_path):
        f = tmp_path / "xss.txt"
        f.write_text('"><script>alert(1)</script><a x="\nalice\n')
        result = load_usernames_from_file(str(f))
        assert result == ["alice"]

    def test_rejects_invalid_characters(self, tmp_path):
        f = tmp_path / "bad.txt"
        f.write_text("valid_user\ninvalid user\nhas@symbol\nalso-dashes\ngood.name\n")
        result = load_usernames_from_file(str(f))
        assert result == ["valid_user", "good.name"]


# ---------------------------------------------------------------------------
# Unit Tests — extract_extra_lists_from_zip
# ---------------------------------------------------------------------------


class TestExtractExtraLists:
    def test_with_recently_unfollowed(self, tmp_path):
        zip_path = tmp_path / "test.zip"
        unfollowed_html = '<html><body><a href="https://www.instagram.com/old_friend" target="_blank">old_friend</a></body></html>'
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("connections/followers_and_following/following.html", "<html></html>")
            zf.writestr("connections/followers_and_following/followers_1.html", "<html></html>")
            zf.writestr("connections/followers_and_following/recently_unfollowed_profiles.html", unfollowed_html)

        extras = extract_extra_lists_from_zip(str(zip_path))
        assert "Recently Unfollowed by You" in extras
        assert "old_friend" in extras["Recently Unfollowed by You"]

    def test_empty_extras(self, make_ig_zip):
        zp = make_ig_zip(["alice"], ["bob"])
        extras = extract_extra_lists_from_zip(str(zp))
        assert isinstance(extras, dict)


# ---------------------------------------------------------------------------
# Unit Tests — generate_summary_html
# ---------------------------------------------------------------------------


class TestGenerateSummaryHtml:
    def test_basic_dashboard(self, tmp_path):
        outfile = str(tmp_path / "dashboard.html")
        stats = {"Following": 100, "Followers": 200, "Mutuals": 80}
        lists = {"Recently Unfollowed by You": ["alice", "bob"]}
        generate_summary_html(stats, lists, outfile)

        content = Path(outfile).read_text()
        assert "100" in content
        assert "200" in content
        assert "80" in content
        assert "alice" in content
        assert "Recently Unfollowed" in content

    def test_empty_dashboard(self, tmp_path):
        outfile = str(tmp_path / "dashboard.html")
        generate_summary_html({}, {}, outfile)
        assert Path(outfile).exists()


# ---------------------------------------------------------------------------
# Unit Tests — relationship diffs
# ---------------------------------------------------------------------------


class TestRelationshipDiffs:
    def test_fans_list(self, make_ig_zip):
        following = ["alice", "bob"]
        followers = ["alice", "charlie", "dave"]
        zp = make_ig_zip(following, followers)

        got_following, got_followers = extract_usernames_from_zip(str(zp))
        fans = got_followers - got_following

        assert fans == {"charlie", "dave"}

    def test_mutuals_list(self, make_ig_zip):
        following = ["alice", "bob", "charlie"]
        followers = ["alice", "charlie", "dave"]
        zp = make_ig_zip(following, followers)

        got_following, got_followers = extract_usernames_from_zip(str(zp))
        mutuals = got_following & got_followers

        assert mutuals == {"alice", "charlie"}

    def test_all_diffs_consistent(self, make_ig_zip):
        following = ["a", "b", "c", "d"]
        followers = ["c", "d", "e", "f"]
        zp = make_ig_zip(following, followers)

        got_following, got_followers = extract_usernames_from_zip(str(zp))
        not_following_back = got_following - got_followers
        fans = got_followers - got_following
        mutuals = got_following & got_followers

        assert not_following_back == {"a", "b"}
        assert fans == {"e", "f"}
        assert mutuals == {"c", "d"}
        # No overlaps
        assert not (not_following_back & fans)
        assert not (not_following_back & mutuals)
        assert not (fans & mutuals)


# ---------------------------------------------------------------------------
# Integration Test — full zip-to-results pipeline (no network)
# ---------------------------------------------------------------------------


class TestEndToEnd:
    def test_extract_and_diff(self, make_ig_zip):
        """Full pipeline from zip to not-following-back list."""
        following = ["alice", "bob", "charlie", "dave", "eve"]
        followers = ["alice", "charlie", "eve"]
        zp = make_ig_zip(following, followers)

        got_following, got_followers = extract_usernames_from_zip(str(zp))
        not_following_back = sorted(got_following - got_followers)

        assert not_following_back == ["bob", "dave"]

    def test_extract_and_generate_html(self, make_ig_zip, tmp_path):
        """Zip → extract → diff → HTML generation."""
        following = ["real_user", "deleted_user", "ghost_account"]
        followers = ["other_person"]
        zp = make_ig_zip(following, followers)

        got_following, got_followers = extract_usernames_from_zip(str(zp))
        not_following_back = sorted(got_following - got_followers)

        outfile = str(tmp_path / "output.html")
        generate_html(
            not_following_back,
            "Not Following Back",
            "Test export",
            "#4fc3f7",
            outfile,
            track_clicks=True,
        )

        content = Path(outfile).read_text()
        assert "deleted_user" in content
        assert "ghost_account" in content
        assert "real_user" in content
        assert "other_person" not in content  # follower, not in "not following back"

    def test_results_json_structure(self, tmp_path):
        """Verify results.json has the expected structure."""
        results = {
            "timestamp": "2026-04-05T12:00:00",
            "total_checked": 3,
            "results": [
                {"username": "alice", "status": "EXISTS", "title": "Alice (@alice)"},
                {"username": "bob", "status": "NOT_FOUND", "title": "Profile isn't available"},
                {"username": "charlie", "status": "LOGIN_WALL", "title": "Instagram"},
            ],
        }

        results_path = tmp_path / "results.json"
        results_path.write_text(json.dumps(results, indent=2))

        loaded = json.loads(results_path.read_text())
        assert loaded["total_checked"] == 3

        exists = [r for r in loaded["results"] if r["status"] == "EXISTS"]
        not_found = [r for r in loaded["results"] if r["status"] == "NOT_FOUND"]
        assert len(exists) == 1
        assert len(not_found) == 1
        assert exists[0]["username"] == "alice"
        assert not_found[0]["username"] == "bob"


# ---------------------------------------------------------------------------
# Unit Tests — diff_exports
# ---------------------------------------------------------------------------


class TestDiffExports:
    def _make_zip(self, tmp_path, name, following, followers):
        """Helper to create a zip with given following/followers."""
        following_html = "<html><body>\n" + "\n".join(
            f'<a href="https://www.instagram.com/_u/{u}" target="_blank">{u}</a>' for u in following
        ) + "\n</body></html>"
        followers_html = "<html><body>\n" + "\n".join(
            f'<a href="https://www.instagram.com/{u}" target="_blank">{u}</a>' for u in followers
        ) + "\n</body></html>"

        zip_path = tmp_path / name
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("connections/followers_and_following/following.html", following_html)
            zf.writestr("connections/followers_and_following/followers_1.html", followers_html)
        return zip_path

    def test_lost_followers(self, tmp_path):
        old = self._make_zip(tmp_path, "old.zip", ["a"], ["a", "b", "c"])
        new = self._make_zip(tmp_path, "new.zip", ["a"], ["a", "c"])
        changes, counts = diff_exports(str(old), str(new))
        lost = changes["Lost Followers (unfollowed you or blocked you)"]
        assert "b" in lost
        assert "a" not in lost

    def test_new_followers(self, tmp_path):
        old = self._make_zip(tmp_path, "old.zip", [], ["a"])
        new = self._make_zip(tmp_path, "new.zip", [], ["a", "b", "c"])
        changes, _ = diff_exports(str(old), str(new))
        assert sorted(changes["New Followers"]) == ["b", "c"]

    def test_you_unfollowed(self, tmp_path):
        old = self._make_zip(tmp_path, "old.zip", ["a", "b", "c"], [])
        new = self._make_zip(tmp_path, "new.zip", ["a"], [])
        changes, _ = diff_exports(str(old), str(new))
        assert sorted(changes["You Unfollowed"]) == ["b", "c"]

    def test_you_started_following(self, tmp_path):
        old = self._make_zip(tmp_path, "old.zip", ["a"], [])
        new = self._make_zip(tmp_path, "new.zip", ["a", "b"], [])
        changes, _ = diff_exports(str(old), str(new))
        assert changes["You Started Following"] == ["b"]

    def test_possible_blocks(self, tmp_path):
        """Mutual who disappears from followers = possible block."""
        old = self._make_zip(tmp_path, "old.zip", ["a", "b", "c"], ["a", "b", "c"])
        new = self._make_zip(tmp_path, "new.zip", ["a", "b", "c"], ["a", "c"])
        changes, _ = diff_exports(str(old), str(new))
        blocks = changes["Possible Blocks (were mutual, now they don't follow you)"]
        assert "b" in blocks

    def test_no_changes(self, tmp_path):
        old = self._make_zip(tmp_path, "old.zip", ["a", "b"], ["a", "b"])
        new = self._make_zip(tmp_path, "new.zip", ["a", "b"], ["a", "b"])
        changes, counts = diff_exports(str(old), str(new))
        for label, users in changes.items():
            assert users == [], f"{label} should be empty but got {users}"

    def test_counts(self, tmp_path):
        old = self._make_zip(tmp_path, "old.zip", ["a", "b"], ["x", "y", "z"])
        new = self._make_zip(tmp_path, "new.zip", ["a", "b", "c"], ["x", "y"])
        _, counts = diff_exports(str(old), str(new))
        assert counts["old_following"] == 2
        assert counts["old_followers"] == 3
        assert counts["new_following"] == 3
        assert counts["new_followers"] == 2


# ---------------------------------------------------------------------------
# Smoke Tests — real Playwright + real Instagram (requires network + VPN)
# ---------------------------------------------------------------------------


@pytest.mark.smoke
class TestSmoke:
    """These tests hit real Instagram. Run with: pytest --run-smoke"""

    def test_real_existing_account(self):
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
            ).new_page()

            result = check_account(page, "instagram")
            browser.close()

        assert result["status"] in ("EXISTS", "LOGIN_WALL"), f"Got: {result}"

    def test_real_nonexistent_account(self):
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
            ).new_page()

            result = check_account(page, "zzznonexistentuser999xyzqrs1234567")
            browser.close()

        assert result["status"] in ("NOT_FOUND", "LOGIN_WALL"), f"Got: {result}"

    def test_real_zip_extraction(self):
        """Smoke test with a real IG export zip if available."""
        # Look for any real export zip in the project dir
        project_dir = Path(__file__).parent.parent
        zips = sorted(project_dir.glob("instagram-*-*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)

        if not zips:
            pytest.skip("No real IG export zip found in project dir")

        following, followers = extract_usernames_from_zip(str(zips[0]))
        assert len(following) > 0, "Following list should not be empty"
        assert len(followers) > 0, "Followers list should not be empty"


# ---------------------------------------------------------------------------
# Unit Tests — bio capture
#
# The profile page renders name, bio and counts while logged out, so reading
# them costs no request beyond the one check_account already makes. These
# tests pin the two rules that matter: a bio must never change the
# exists/not-found verdict, and a missing bio is a normal profile.
# ---------------------------------------------------------------------------


def _header(text):
    """A mock page whose <header> yields this text."""
    page = MagicMock()
    loc = MagicMock()
    first = MagicMock()
    first.inner_text.return_value = text
    loc.first = first
    page.locator.return_value = loc
    return page


NASA_HEADER = (
    "nasa\n104M followers\n92 following\nNASA\n"
    "Making the seemingly impossible, possible.\nwww.nasa.gov"
)


class TestExtractBio:
    def test_pulls_name_bio_link_and_counts(self):
        got = extract_bio(_header(NASA_HEADER), "nasa")
        assert got["full_name"] == "NASA"
        assert got["bio"] == "Making the seemingly impossible, possible."
        assert got["external_link"] == "www.nasa.gov"
        assert got["followers"] == "104M"
        assert got["following"] == "92"

    def test_username_and_chrome_are_not_mistaken_for_bio(self):
        got = extract_bio(_header(
            "someone\n1,204 followers\n83 following\nFollow\nMessage\n"
            "Show more posts\nAccounts you might like"), "someone")
        assert got["full_name"] is None
        assert got["bio"] is None

    def test_a_profile_with_no_bio_is_not_an_error(self):
        got = extract_bio(_header("plainuser\n12 followers\n30 following\nPlain User"),
                          "plainuser")
        assert got["full_name"] == "Plain User"
        assert got["bio"] is None

    def test_multiline_bio_is_joined(self):
        got = extract_bio(_header(
            "x\n5 followers\n5 following\nX Person\nline one\nline two"), "x")
        assert got["bio"] == "line one\nline two"

    def test_a_header_that_will_not_load_returns_empty_not_raise(self):
        page = MagicMock()
        page.locator.side_effect = Exception("detached")
        got = extract_bio(page, "whoever")
        assert got == {"full_name": None, "bio": None, "external_link": None,
                       "followers": None, "following": None}


class TestBioDoesNotChangeVerdict:
    def test_exists_still_exists_and_gains_bio(self, mock_page):
        mock_page.title.return_value = "NASA (@nasa) • Instagram photos and videos"
        mock_page.url = "https://www.instagram.com/nasa/"
        loc = MagicMock(); first = MagicMock()
        first.inner_text.return_value = NASA_HEADER
        loc.first = first; mock_page.locator.return_value = loc

        r = check_account(mock_page, "nasa")
        assert r["status"] == "EXISTS"
        assert r["bio"] == "Making the seemingly impossible, possible."

    def test_not_found_is_untouched_by_bio_code(self, mock_page):
        mock_page.title.return_value = "Profile isn't available • Instagram"
        mock_page.url = "https://www.instagram.com/gone/"
        r = check_account(mock_page, "gone")
        assert r["status"] == "NOT_FOUND"
        assert "bio" not in r

    def test_a_broken_header_cannot_break_the_verdict(self, mock_page):
        mock_page.title.return_value = "Someone (@someone) • Instagram photos and videos"
        mock_page.url = "https://www.instagram.com/someone/"
        mock_page.locator.side_effect = Exception("boom")
        r = check_account(mock_page, "someone")
        assert r["status"] == "EXISTS"
        assert r["bio"] is None


# ---------------------------------------------------------------------------
# Unit Tests — the no-login guarantee
#
# Public bios need no session. A session would make every page load an
# authenticated action attributable to the account, so its presence is a
# reason to abort, not something to work around.
# ---------------------------------------------------------------------------


def _ctx(cookies):
    ctx = MagicMock()
    ctx.cookies.return_value = cookies
    return ctx


class TestAssertLoggedOut:
    def test_a_clean_context_passes(self):
        assert_logged_out(_ctx([])) is None

    def test_harmless_cookies_from_other_sites_are_ignored(self):
        assert_logged_out(_ctx([
            {"name": "sessionid", "domain": ".example.com"},
            {"name": "_ga", "domain": ".instagram.com"},
        ])) is None

    def test_a_session_cookie_aborts(self):
        with pytest.raises(RuntimeError, match="session"):
            assert_logged_out(_ctx([
                {"name": "sessionid", "domain": ".instagram.com"}]))

    def test_a_user_id_cookie_aborts(self):
        with pytest.raises(RuntimeError, match="ds_user_id"):
            assert_logged_out(_ctx([
                {"name": "ds_user_id", "domain": "www.instagram.com"}]))

    def test_the_error_never_prints_the_cookie_value(self):
        with pytest.raises(RuntimeError) as e:
            assert_logged_out(_ctx([
                {"name": "sessionid", "domain": ".instagram.com",
                 "value": "SUPERSECRETSESSIONVALUE"}]))
        assert "SUPERSECRETSESSIONVALUE" not in str(e.value)


# ---------------------------------------------------------------------------
# Unit Tests — checkpoint / resume / incremental bios
# ---------------------------------------------------------------------------


class TestCheckpoint:
    def test_no_checkpoint_yet(self, tmp_path):
        results, done = load_checkpoint(tmp_path / "nope.json")
        assert results == [] and done == set()

    def test_only_settled_verdicts_count_as_done(self, tmp_path):
        p = tmp_path / "results.json"
        p.write_text(json.dumps({"results": [
            {"username": "a", "status": "EXISTS"},
            {"username": "b", "status": "NOT_FOUND"},
            {"username": "c", "status": "EXISTS_PRIVATE"},
            {"username": "d", "status": "ERROR"},
            {"username": "e", "status": "LOGIN_WALL"},
        ]}))
        results, done = load_checkpoint(p)
        assert len(results) == 5
        # ERROR and LOGIN_WALL mean "could not tell", so they are retried.
        assert done == {"a", "b", "c"}

    def test_a_truncated_checkpoint_does_not_crash_the_run(self, tmp_path):
        p = tmp_path / "results.json"
        p.write_text('{"results": [{"username": "a", "stat')
        assert load_checkpoint(p) == ([], set())

    def test_bios_file_holds_only_profiles_with_content(self, tmp_path):
        p = tmp_path / "bios.json"
        n = write_bios([
            {"username": "a", "status": "EXISTS", "full_name": "A", "bio": "hi"},
            {"username": "b", "status": "NOT_FOUND"},
            {"username": "c", "status": "EXISTS"},
        ], p)
        assert n == 1
        data = json.loads(p.read_text())
        assert data["count"] == 1
        assert data["profiles"][0]["username"] == "a"

    def test_bios_write_is_atomic(self, tmp_path):
        p = tmp_path / "bios.json"
        write_bios([{"username": "a", "full_name": "A"}], p)
        first = p.read_text()
        write_bios([{"username": "a", "full_name": "A"},
                    {"username": "b", "full_name": "B"}], p)
        assert json.loads(p.read_text())["count"] == 2
        assert not (tmp_path / "bios.tmp").exists()
        assert first != p.read_text()

    def test_cap_limits_a_trial_run(self):
        assert _cap(["a", "b", "c"], 2) == ["a", "b"]
        assert _cap(["a", "b", "c"], None) == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# Unit Tests — adaptive pacing
#
# LOGIN_WALL is Instagram telling us directly that the address is throttled.
# Pacing should be driven by that signal rather than by a guessed constant,
# so the run stays fast while it is welcome and slows only when it is not.
# ---------------------------------------------------------------------------


class TestPacer:
    def test_starts_fast_with_no_cooldown(self):
        p = Pacer()
        assert p.mult == 1.0
        # Nothing has gone wrong yet, so there is nothing to back off from.
        assert p.cooldown_seconds() is None

    def test_a_login_wall_backs_off_immediately(self):
        p = Pacer()
        p.record("LOGIN_WALL")
        assert p.mult > 1.0
        assert p.cooldown_seconds() > 0

    def test_backoff_is_bounded(self):
        p = Pacer()
        for _ in range(50):
            p.record("LOGIN_WALL")
        assert p.mult <= 20.0

    def test_errors_back_off_more_gently_than_login_walls(self):
        a, b = Pacer(), Pacer()
        a.record("ERROR")
        b.record("LOGIN_WALL")
        assert 1.0 < a.mult < b.mult

    def test_recovery_needs_a_sustained_clean_run(self):
        p = Pacer()
        p.record("LOGIN_WALL")
        hot = p.mult
        for _ in range(9):
            p.record("EXISTS")
        assert p.mult == hot          # a short clean streak is not enough
        p.record("EXISTS")            # the tenth trips recovery
        assert p.mult < hot

    def test_recovery_never_goes_below_baseline(self):
        p = Pacer()
        for _ in range(500):
            p.record("EXISTS")
        assert p.mult == 1.0

    def test_delay_scales_with_the_multiplier(self):
        # Compare MEANS, not min against max: roughly one delay in twelve
        # carries an extra 6-20s of jitter, so the tails of the two
        # distributions overlap by design.
        slow, fast = Pacer(), Pacer()
        for _ in range(3):
            slow.record("LOGIN_WALL")
        n = 400
        s = sum(slow.sleep_seconds() for _ in range(n)) / n
        f = sum(fast.sleep_seconds() for _ in range(n)) / n
        assert s > f * 2
