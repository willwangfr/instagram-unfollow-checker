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
    check_account,
    generate_html,
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
    return f'<a href="https://www.instagram.com/_u/{username}" target="_blank">{username}</a>'


def _make_follower_link(username):
    return f'<a href="https://www.instagram.com/{username}" target="_blank">{username}</a>'


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
        mock_page.title.return_value = "Private User (@private_user) • Instagram"
        mock_page.url = "https://www.instagram.com/private_user/"
        # Title doesn't match the exact pattern, so falls through to body check
        locator = MagicMock()
        locator.inner_text.return_value = "This Account is Private\nFollow to see their photos"
        mock_page.locator.return_value = locator

        result = check_account(mock_page, "private_user")
        assert result["status"] in ("EXISTS", "EXISTS_PRIVATE")

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
