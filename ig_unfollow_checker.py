# Copyright (C) 2026 William Wang
# Licensed under the GNU AGPL v3 or later. See LICENSE.
# Run a modified version as a network service and you must offer users its source.
"""
Instagram Unfollow Checker — All-in-One

Modes:
  1. ZIP MODE:  Give it your Instagram data export zip and it:
     - Shows who's not following you back
     - Shows who follows you but you don't follow back (fans)
     - Shows mutual follows, pending requests, recent unfollows
     - Checks which "not following back" accounts are deleted/deactivated

  2. LIST MODE:  Give it a text file of usernames and it checks if they exist

  3. DIFF MODE:  Compare two exports over time to find:
     - Who unfollowed you (possible blocks)
     - New followers
     - Who you started/stopped following

Requirements:
  pip install playwright
  python -m playwright install chromium

Usage:
  # From Instagram export zip
  python3 ig_unfollow_checker.py export.zip

  # From a plain text list of usernames
  python3 ig_unfollow_checker.py --check-list usernames.txt

  # Just analyze the zip (no browser checks)
  python3 ig_unfollow_checker.py export.zip --analyze-only

  # Resume after rate limit
  python3 ig_unfollow_checker.py export.zip --start-at 500

  # Compare two exports to find who unfollowed you (possible blocks)
  python3 ig_unfollow_checker.py --diff old-export.zip new-export.zip
"""

import re
import sys
import time
import json
import html as html_mod
import random
import argparse
import zipfile
from pathlib import Path
from datetime import datetime
from playwright.sync_api import sync_playwright

# Valid Instagram username pattern
USERNAME_RE = re.compile(r'^[a-zA-Z0-9_.]{1,30}$')


# --- Config ---
MIN_DELAY = 4.5
MAX_DELAY = 9
BATCH_SIZE = 60
BATCH_PAUSE = 180
PAGE_TIMEOUT = 45000
# The profile data we read is in the DOM well before this; measured against
# 2500/1200/600ms, the og:description counts were intact at 1200 and the
# extra 1.3s bought nothing.
SETTLE_MS = 1200
# Navigation and extraction on top of the settle wait. Timed at ~2s per
# profile; the estimate below understated a full run by ~75% without it.
NETWORK_OVERHEAD = 2.0
# The 8% chance of an extra 6-20s pause in Pacer.sleep_seconds, amortised.
JITTER_OVERHEAD = 0.08 * 13


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def extract_usernames_from_zip(zip_path: str) -> tuple[set, set]:
    """Extract followers and following usernames from an IG export zip."""
    with zipfile.ZipFile(zip_path, 'r') as z:
        names = z.namelist()

        following_file = next((n for n in names if n.endswith('following.html') and 'followers_and_following' in n), None)
        followers_file = next((n for n in names if n.endswith('followers_1.html') and 'followers_and_following' in n), None)

        if not following_file or not followers_file:
            print("Error: Could not find followers/following HTML files in zip.")
            print(f"  Found: {[n for n in names if 'follow' in n.lower()]}")
            sys.exit(1)

        with z.open(following_file) as f:
            following_html = f.read().decode('utf-8')
        with z.open(followers_file) as f:
            followers_html = f.read().decode('utf-8')

    # Following uses /_u/username, followers uses /username — anchor to href= context
    following = set(re.findall(r'href="https://www\.instagram\.com/_u/([a-zA-Z0-9_.]+)"', following_html))
    followers = set(re.findall(r'href="https://www\.instagram\.com/([a-zA-Z0-9_.]+)"', followers_html))

    # If the /_u/ pattern doesn't match, try the direct pattern for following too
    if not following:
        following = set(re.findall(r'href="https://www\.instagram\.com/([a-zA-Z0-9_.]+)"', following_html))

    return following, followers


# Followers and following are rendered as anchors, but every other list in the
# export (pending requests, recent unfollows, close friends) is a borderless
# table with no links at all. Matching only on anchors silently returned an
# empty list for all of them.
_TABLE_USER_RE = re.compile(
    r'<td[^>]*>Username</td>\s*<td[^>]*>([A-Za-z0-9_.]+)</td>', re.I)


def usernames_from_export_html(html: str) -> list[str]:
    """Usernames from an export page, in either the anchor or table layout."""
    users = re.findall(r'href="https://www\.instagram\.com/_u/([a-zA-Z0-9_.]+)"', html)
    if not users:
        users = re.findall(r'href="https://www\.instagram\.com/([a-zA-Z0-9_.]+)"', html)
    if not users:
        users = _TABLE_USER_RE.findall(html)
    return users


def extract_extra_lists_from_zip(zip_path: str) -> dict[str, list[str]]:
    """Extract additional lists from the export: pending requests, recent unfollows, etc."""
    extras = {}
    with zipfile.ZipFile(zip_path, 'r') as z:
        names = z.namelist()

        mapping = {
            "pending_follow_requests": "Pending Follow Requests (you sent)",
            "recent_follow_requests": "Recent Follow Requests (you sent)",
            "recently_unfollowed_profiles": "Recently Unfollowed by You",
            "follow_requests_you've_received": "Follow Requests You've Received",
            "blocked_profiles": "Blocked by You",
            "restricted_profiles": "Restricted by You",
        }

        for filename_part, label in mapping.items():
            match = next((n for n in names if filename_part in n and n.endswith('.html')), None)
            if match:
                with z.open(match) as f:
                    html = f.read().decode('utf-8')
                users = usernames_from_export_html(html)
                if users:
                    extras[label] = sorted(set(users))

    return extras


def load_usernames_from_file(filepath: str) -> list[str]:
    """Load usernames from a plain text file (one per line)."""
    content = Path(filepath).read_text()
    usernames = []
    skipped = []
    for line in content.splitlines():
        line = line.strip().lstrip("@")
        # Strip full URLs down to username
        line = re.sub(r'https?://(?:www\.)?instagram\.com/(?:_u/)?', '', line).rstrip('/')
        if not line or line.startswith("#"):
            continue
        if not USERNAME_RE.match(line):
            skipped.append(line)
            continue
        usernames.append(line)
    if skipped:
        print(f"  Skipped {len(skipped)} invalid usernames (not matching Instagram format):")
        for s in skipped[:5]:
            print(f"    {s!r}")
        if len(skipped) > 5:
            print(f"    ... and {len(skipped) - 5} more")
    return usernames


# ---------------------------------------------------------------------------
# Account checking
# ---------------------------------------------------------------------------

# Bio text sits in the header <section>; the profile page renders it logged out,
# so reading it costs no extra request beyond the one check_account already
# makes. Everything below is best-effort: a missing bio is a normal profile,
# not an error, and must never change the EXISTS/NOT_FOUND verdict.
BIO_NOISE_RE = re.compile(
    r"^(followers?|following|posts?|Follow|Message|"
    r"Show more posts|Accounts you might like|See all|Log ?[Ii]n|Sign ?up)$")
COUNT_RE = re.compile(r"^[\d.,]+[KMB]?\s+(followers?|following|posts?)$", re.I)


def _clean_bio_lines(lines: list[str], username: str) -> list[str]:
    """Strip the handle, the counts and the button chrome; keep the rest.

    The handle is dropped only ONCE. Plenty of accounts set a display name
    that differs from the handle by case alone ("nasa" / "NASA"), and
    filtering every case-insensitive match threw the display name away.
    """
    out = []
    dropped_handle = False
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if not dropped_handle and line.lower() == username.lower():
            dropped_handle = True
            continue
        if BIO_NOISE_RE.match(line) or COUNT_RE.match(line):
            continue
        out.append(line)
    return out


def extract_og_counts(page) -> dict:
    """Counts from the og:description meta tag.

    The logged-out header renders followers and following but omits the post
    count entirely; the meta tag carries all three, so it is the only
    anonymous source for "has this account ever posted".
    """
    out = {}
    try:
        content = page.locator('meta[property="og:description"]').first.get_attribute(
            "content", timeout=3000)
    except Exception:
        return out
    if not isinstance(content, str):
        return out
    for pattern, key in ((r"([\d.,]+[KMB]?)\s+Followers", "followers"),
                         (r"([\d.,]+[KMB]?)\s+Following", "following"),
                         (r"([\d.,]+[KMB]?)\s+Posts?", "posts")):
        m = re.search(pattern, content, re.I)
        if m:
            out[key] = m.group(1)
    return out


def name_from_title(title: str, username: str):
    """Display name from the page title.

    The title carries it even when the header fails to render, so it is a free
    fallback. Instagram wraps names containing right-to-left script in
    directional marks, which are invisible but not whitespace.
    """
    if not isinstance(title, str):
        return None
    m = re.match(r"^(.*?)\s*\(@" + re.escape(username) + r"\)", title, re.I)
    if not m:
        return None
    return m.group(1).strip().strip("\u200e\u200f").strip() or None


PROFILE_JS = """(expand) => {
    const q = s => Array.from(document.querySelectorAll(s));
    const header = document.querySelector('header');
    // Long bios are collapsed behind a "more" control; without expanding it the
    // captured bio is silently truncated mid-sentence.
    let hasMore = false;
    if (header) {
        for (const el of Array.from(header.querySelectorAll('span,div,button'))) {
            if (el.innerText && el.innerText.trim() === 'more' && el.offsetParent) {
                hasMore = true;
                if (expand) el.click();
                break;
            }
        }
    }
    const highlights = q('main img[alt]')
        .filter(i => (i.getAttribute('alt') || '').includes('highlight story'))
        .map(i => {
            let n = i.closest('li') || i.parentElement;
            for (let k = 0; k < 5 && n; k++) {
                const t = (n.innerText || '').trim();
                if (t && t.length < 40 && t.indexOf(String.fromCharCode(10)) < 0) return t;
                n = n.parentElement;
            }
            return null;
        }).filter(Boolean);
    // Bio links are wrapped in an l.instagram.com redirect whose ?u= parameter
    // holds the real destination; the visible text is truncated.
    const external = [...new Set(q('header a[href]')
        .map(a => a.getAttribute('href') || '')
        .filter(h => h.includes('l.instagram.com'))
        .map(h => { try { return decodeURIComponent(new URL(h, location.href)
                        .searchParams.get('u') || ''); } catch (e) { return ''; } })
        .filter(Boolean))];
    const threads = (q('header a[href*="threads.com/@"], header a[href*="threads.net/@"]')
        .map(a => (a.getAttribute('href') || '').match(/threads\\.(?:com|net)\\/@([A-Za-z0-9_.]+)/))
        .filter(Boolean).map(m => m[1]))[0] || null;
    const shortcodes = [...new Set(q('a[href*="/p/"], a[href*="/reel/"]')
        .map(a => (a.getAttribute('href') || '').match(/\\/(?:p|reel)\\/([^/]+)/))
        .filter(Boolean).map(m => m[1]))];
    const pic = q('meta[property="og:image"]')[0];
    return {
        header_text: header ? header.innerText : '',
        emails: [...new Set(((header ? header.innerText : '').match(
            /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g) || []))],
        highlight_titles: highlights,
        external_links: external,
        threads_handle: threads,
        verified: q('svg[aria-label="Verified"]').length > 0,
        profile_pic: pic ? pic.getAttribute('content') : null,
        recent_shortcodes: shortcodes,
        // The bare /explore/locations/ link is Instagram's site footer and is
        // present on every page; a real tag carries an id after the prefix.
        has_location_tag: q('a[href*="/explore/locations/"]')
            .some(a => /\/explore\/locations\/[0-9]/.test(a.getAttribute('href') || '')),
        has_more: hasMore,
    };
}"""


def extract_bio(page, username: str) -> dict:
    """Best-effort display name, bio text, link and counts from a loaded page."""
    info = {"full_name": None, "bio": None, "external_link": None,
            "followers": None, "following": None, "posts": None,
            "verified": None, "profile_pic": None, "highlight_count": None,
            "highlight_titles": None, "threads_handle": None,
            "external_links": None, "emails": None,
            "recent_shortcodes": None, "has_location_tag": None}
    info.update(extract_og_counts(page))
    try:
        d = page.evaluate(PROFILE_JS, False)
        # Instagram collapses long bios behind a "more" control. Expanding costs
        # a click and a re-read, so only profiles that actually have one pay it.
        if isinstance(d, dict) and d.get("has_more"):
            page.evaluate(PROFILE_JS, True)
            page.wait_for_timeout(350)
            expanded = page.evaluate(PROFILE_JS, False)
            if isinstance(expanded, dict) and len(
                    expanded.get("header_text") or "") > len(d.get("header_text") or ""):
                d = expanded
    except Exception:
        d = {}
    if not isinstance(d, dict):
        d = {}

    info["highlight_titles"] = d.get("highlight_titles") or []
    info["highlight_count"] = len(info["highlight_titles"])
    info["threads_handle"] = d.get("threads_handle")
    info["external_links"] = d.get("external_links") or []
    info["emails"] = d.get("emails") or []
    info["verified"] = d.get("verified")
    info["profile_pic"] = d.get("profile_pic")
    info["recent_shortcodes"] = d.get("recent_shortcodes")
    info["has_location_tag"] = d.get("has_location_tag")
    links = d.get("external_links") or []
    if links:
        info["external_link"] = links[0]
        if len(links) > 1:
            info["external_links_all"] = links

    header = d.get("header_text") or ""
    if not header:
        try:
            header = page.locator("header").first.inner_text(timeout=4000)
        except Exception:
            header = ""
    if isinstance(header, str) and header:
        lines = header.splitlines()
        # og:description is the primary source for counts; the header still
        # carries followers and following if that tag is missing.
        for l in lines:
            for pat, key in ((r"^([\d.,]+[KMB]?)\s+followers?$", "followers"),
                             (r"^([\d.,]+[KMB]?)\s+following$", "following"),
                             (r"^([\d.,]+[KMB]?)\s+posts?$", "posts")):
                m = re.match(pat, l.strip(), re.I)
                if m and not info.get(key):
                    info[key] = m.group(1)
        # Highlight titles render after the bio inside the same header; without
        # dropping them they were captured as extra bio lines.
        drop = {t.lower() for t in info["highlight_titles"]} | {"more", "highlights"}
        body = [l for l in _clean_bio_lines(lines, username)
                if l.strip().lower() not in drop]
        if body:
            info["full_name"] = body[0]
            rest = [l for l in body[1:] if not l.startswith("www.")
                    and not re.match(r"^https?://", l)]
            if rest:
                info["bio"] = "\n".join(rest)
            if not info["external_link"]:
                lk = [l for l in body[1:]
                      if l.startswith("www.") or re.match(r"^https?://", l)]
                if lk:
                    info["external_link"] = lk[0]
    if not info["full_name"]:
        try:
            info["full_name"] = name_from_title(page.title(), username)
        except Exception:
            pass
    return info


# Instagram auth cookies. Their presence means the browser is acting AS the
# user, which turns an anonymous, IP-throttled page load into an authenticated
# action attributable to the account. Public bios do not need any of them, so
# their presence is a bug to abort on rather than a convenience to exploit.
AUTH_COOKIES = {"sessionid", "ds_user_id", "csrftoken", "ig_did", "shbid"}

# "This Account is Private" was the wording this checker was written against.
# Instagram now serves "This profile is private", which silently turned every
# private account into a plain EXISTS.
PRIVATE_MARKERS = ("This profile is private", "This Account is Private",
                   "This account is private", "This Profile is Private")


def assert_logged_out(context) -> None:
    """Refuse to continue if the browser carries an Instagram session."""
    live = {c["name"] for c in context.cookies()
            if "instagram" in c.get("domain", "")}
    leaked = live & {"sessionid", "ds_user_id"}
    if leaked:
        raise RuntimeError(
            "Refusing to run: the browser holds Instagram session cookies "
            f"({', '.join(sorted(leaked))}). Public profile data needs no "
            "login, and sending a session makes every request attributable "
            "to your account.")


def check_account(page, username: str) -> dict:
    """Navigate to profile and determine if it exists from page title."""
    url = f"https://www.instagram.com/{username}/"
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(SETTLE_MS)

        title = page.title()
        final_url = page.url

        if "accounts/login" in final_url:
            return {"username": username, "status": "LOGIN_WALL", "title": title}

        if "Profile isn't available" in title or "Page Not Found" in title:
            return {"username": username, "status": "NOT_FOUND", "title": title}

        # Private profiles serve the same title as public ones, so privacy has
        # to be read from the body. Instagram's wording has drifted over time;
        # match every form rather than the one that happened to be current.
        body_text = ""
        try:
            body_text = page.locator("body").inner_text(timeout=4000)[:1500]
        except Exception:
            pass
        private = any(m in body_text for m in PRIVATE_MARKERS)

        if f"(@{username})" in title or "Instagram photos and videos" in title:
            return {"username": username,
                    "status": "EXISTS_PRIVATE" if private else "EXISTS",
                    "title": title, **extract_bio(page, username)}

        if private:
            return {"username": username, "status": "EXISTS_PRIVATE", "title": title,
                    **extract_bio(page, username)}

        return {"username": username, "status": "UNKNOWN", "title": title}

    except Exception as e:
        return {"username": username, "status": "ERROR", "error": str(e)}


class Pacer:
    """Adaptive delay driven by the throttle signal we already collect.

    A fixed conservative pause is a guess about thresholds nobody published,
    and it costs about seven hours across a full run. LOGIN_WALL is Instagram
    telling us directly that we are being throttled, so start fast, slow down
    only when it appears, and recover when it stops.
    """

    def __init__(self, base_min=MIN_DELAY, base_max=MAX_DELAY):
        self.base_min, self.base_max = base_min, base_max
        self.mult = 1.0
        self.clean = 0

    def record(self, status: str) -> None:
        if status == "LOGIN_WALL":
            # Back off hard and immediately; this is the only unambiguous
            # signal that the address is being rate-limited.
            self.mult = min(self.mult * 1.6, 6.0)
            self.clean = 0
        elif status == "ERROR":
            self.mult = min(self.mult * 1.15, 6.0)
            self.clean = 0
        else:
            self.clean += 1
            # Recover after 10 consecutive clean checks. Tuned against a
            # simulation: backing off 2.5x with a 25-check recovery ratcheted
            # up and stayed there, turning an 18-hour run into 144 hours at a
            # 2% wall rate - worse than the fixed pause it replaced.
            if self.clean >= 10 and self.mult > 1.0:
                self.mult = max(1.0, self.mult / 1.5)
                self.clean = 0

    def sleep_seconds(self) -> float:
        d = random.uniform(self.base_min, self.base_max) * self.mult
        # A perfectly uniform interval is itself a fingerprint.
        if random.random() < 0.08:
            d += random.uniform(6, 20)
        return d

    def cooldown_seconds(self):
        """A real pause only once we have actually been throttled."""
        if self.mult <= 1.0:
            return None
        return BATCH_PAUSE * random.uniform(0.6, 1.2) * min(self.mult, 4.0) / 2


def _cap(names: list[str], limit) -> list[str]:
    """Trim the work list for a trial run, leaving resume semantics intact."""
    return names[:limit] if limit else names


def load_checkpoint(results_path: Path) -> tuple[list[dict], set]:
    """Previous results and the usernames already decided.

    Resuming by POSITION breaks whenever the input list changes - a fresh
    export reorders and re-numbers everything, so --start-at 500 silently
    skips a different 500 people. Resuming by USERNAME cannot drift.
    """
    if not results_path.exists():
        return [], set()
    try:
        prev = json.loads(results_path.read_text())
    except (json.JSONDecodeError, OSError):
        return [], set()
    results = prev.get("results", [])
    # Only settled verdicts count as done. ERROR and LOGIN_WALL are retried:
    # they mean "we could not tell", not "this account does not exist".
    done = {r["username"] for r in results
            if r.get("status") in ("EXISTS", "EXISTS_PRIVATE", "NOT_FOUND")}
    return results, done


def write_bios(results: list[dict], bios_path: Path) -> int:
    """Write everyone whose bio we have, so a partial run is still useful."""
    rows = [{k: r.get(k) for k in
             ("username", "status", "full_name", "bio", "external_link",
              "followers", "following", "posts", "verified", "profile_pic",
              "highlight_count", "highlight_titles", "threads_handle",
              "external_links", "emails",
              "recent_shortcodes", "has_location_tag")}
            for r in results if r.get("full_name") or r.get("bio")]
    tmp = bios_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(
        {"generated": datetime.now().isoformat(), "count": len(rows),
         "profiles": rows}, indent=2, ensure_ascii=False))
    # Atomic replace: a crash mid-write leaves the previous file intact
    # rather than a truncated one.
    tmp.replace(bios_path)
    return len(rows)


def run_checks(usernames: list[str], start_at: int, results_path: Path,
               show_browser: bool, resume: bool = False,
               bios_path: Path = None) -> tuple[list[dict], bool]:
    """Run Playwright checks on a list of usernames. Returns (results, stopped_early)."""
    all_results, done = [], set()
    if resume:
        all_results, done = load_checkpoint(results_path)
        if done:
            print(f"  Resuming: {len(done)} already decided, retrying any "
                  f"errors and rate-limited entries.")
        to_check = [u for u in usernames if u not in done]
    else:
        to_check = usernames[start_at:]
        if start_at > 0 and results_path.exists():
            prev = json.loads(results_path.read_text())
            all_results = prev.get("results", [])
            print(f"  Loaded {len(all_results)} previous results")
    if not to_check:
        print("All accounts already checked.")
        return all_results, False

    print(f"\nChecking {len(to_check)} accounts with Playwright...")
    print("For best safety, use a VPN (see README).")
    per_account = ((MIN_DELAY + MAX_DELAY) / 2 + JITTER_OVERHEAD
                   + SETTLE_MS / 1000 + NETWORK_OVERHEAD)
    est = len(to_check) * per_account / 60
    print(f"Estimated time: {est:.0f} min")
    print("-" * 60)

    login_wall_streak = 0
    stopped_early = False

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not show_browser)
        # storage_state is deliberately omitted: a fresh context starts with
        # no cookies, no localStorage and no service workers, so nothing can
        # carry a session in from a previous run or the real browser profile.
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
            storage_state=None,
        )
        assert_logged_out(context)
        page = context.new_page()
        pacer = Pacer()
        next_pause = random.randint(int(BATCH_SIZE * 0.7), int(BATCH_SIZE * 1.3))
        print("  Anonymous: no session cookies present.")

        for i, username in enumerate(to_check):
            result = check_account(page, username)
            all_results.append(result)

            status = result["status"]
            icon = {"EXISTS": "+", "EXISTS_PRIVATE": "~", "NOT_FOUND": "X",
                    "LOGIN_WALL": "L", "UNKNOWN": "?", "ERROR": "E"}
            idx = i + 1 + start_at
            total = len(usernames)
            print(f"  [{icon.get(status, '?')}] {idx}/{total}  {username:30s}  {status}")

            if status == "LOGIN_WALL":
                login_wall_streak += 1
                if login_wall_streak >= 3:
                    print(f"\n*** Rate limited. Resume with: --start-at {idx}")
                    stopped_early = True
                    break
            else:
                login_wall_streak = 0

            # Save after every check
            results_path.write_text(json.dumps({
                "timestamp": datetime.now().isoformat(),
                "total_checked": len(all_results),
                "results": all_results,
            }, indent=2))
            if bios_path is not None:
                write_bios(all_results, bios_path)

            # A redirect through the login wall can plant cookies mid-run.
            if (i + 1) % 25 == 0:
                assert_logged_out(context)

            pacer.record(status)
            cool = pacer.cooldown_seconds() if i + 1 >= next_pause else None
            if cool and i + 1 < len(to_check):
                print(f"\n  --- Backing off {cool / 60:.1f}min "
                      f"(delay x{pacer.mult:.1f}) ---\n")
                time.sleep(cool)
                next_pause = i + 1 + random.randint(
                    int(BATCH_SIZE * 0.7), int(BATCH_SIZE * 1.3))
            elif i + 1 >= next_pause:
                next_pause = i + 1 + random.randint(
                    int(BATCH_SIZE * 0.7), int(BATCH_SIZE * 1.3))
            else:
                time.sleep(pacer.sleep_seconds())

        browser.close()

    return all_results, stopped_early


# ---------------------------------------------------------------------------
# Diff mode — compare two exports
# ---------------------------------------------------------------------------

def diff_exports(old_zip: str, new_zip: str) -> tuple[dict[str, list[str]], dict[str, int]]:
    """Compare two IG exports and return changes."""
    old_following, old_followers = extract_usernames_from_zip(old_zip)
    new_following, new_followers = extract_usernames_from_zip(new_zip)

    return {
        "Lost Followers (unfollowed you or blocked you)": sorted(old_followers - new_followers),
        "New Followers": sorted(new_followers - old_followers),
        "You Unfollowed": sorted(old_following - new_following),
        "You Started Following": sorted(new_following - old_following),
        # Accounts that were following you AND you were following them, but now
        # they disappeared from your followers — strong block signal
        "Possible Blocks (were mutual, now they don't follow you)": sorted(
            (old_followers & old_following) - new_followers
        ),
    }, {
        "old_following": len(old_following),
        "old_followers": len(old_followers),
        "new_following": len(new_following),
        "new_followers": len(new_followers),
    }


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------

def generate_html(accounts: list[str], title: str, subtitle: str, color: str, filename: str, track_clicks: bool = False):
    """Generate a clickable HTML file."""
    esc_title = html_mod.escape(title)
    esc_subtitle = html_mod.escape(subtitle)
    html = f'''<html><head><meta charset="utf-8"><title>{esc_title} ({len(accounts)})</title>
<style>
body{{font-family:monospace;font-size:14px;padding:20px;background:#111;color:#eee}}
a{{color:{color};text-decoration:none}}
a:hover{{text-decoration:underline;color:#fff}}
a.clicked{{color:#888}}a.clicked-2{{color:#666}}a.clicked-3{{color:#444}}
li{{padding:5px 0}}
h2{{color:#4fc3f7}}
.note{{color:#888;margin-bottom:20px}}
.count{{color:#ff9800;font-size:11px;margin-left:8px}}
.stats{{position:fixed;top:10px;right:20px;background:#222;padding:12px 18px;border-radius:8px;border:1px solid #333}}
</style></head><body>
'''
    if track_clicks:
        html += f'<div class="stats">Clicked: <span id="clicked-count">0</span> / {len(accounts)}</div>\n'

    html += f'<h2>{esc_title} — {len(accounts)}</h2>\n'
    html += f'<p class="note">{esc_subtitle}</p>\n<ol>\n'

    for u in accounts:
        eu = html_mod.escape(u)
        onclick = f' data-user="{eu}" onclick="trackClick(this, event)"' if track_clicks else ''
        count_span = f'<span class="count" id="count-{eu}"></span>' if track_clicks else ''
        html += f'<li><a href="https://www.instagram.com/{eu}/" target="_blank"{onclick}>{eu}</a>{count_span}</li>\n'

    html += '</ol>\n'

    if track_clicks:
        html += '''<script>
const KEY = 'ig_click_tracker';
function getData() { try { return JSON.parse(localStorage.getItem(KEY)) || {}; } catch(e) { return {}; } }
function saveData(d) { localStorage.setItem(KEY, JSON.stringify(d)); }
function trackClick(el, e) {
  const user = el.dataset.user;
  const data = getData();
  data[user] = (data[user] || 0) + 1;
  saveData(data);
  updateDisplay(user, data[user]);
  updateStats();
}
function updateDisplay(user, count) {
  const el = document.querySelector(`a[data-user="${user}"]`);
  const countEl = document.getElementById('count-' + user);
  if (el) { el.classList.add('clicked'); if (count >= 2) el.classList.add('clicked-2'); if (count >= 3) el.classList.add('clicked-3'); }
  if (countEl) countEl.textContent = count > 0 ? ` (${count}x)` : '';
}
function updateStats() {
  const data = getData();
  document.getElementById('clicked-count').textContent = Object.keys(data).filter(k => data[k] > 0).length;
}
(function() { const data = getData(); for (const [user, count] of Object.entries(data)) { updateDisplay(user, count); } updateStats(); })();
</script>
'''

    html += '</body></html>'
    Path(filename).write_text(html, encoding="utf-8")


def generate_summary_html(stats: dict, lists: dict, filename: str):
    """Generate a dashboard HTML with all lists and stats."""
    html = '''<html><head><meta charset="utf-8"><title>Instagram Analysis</title>
<style>
body{font-family:monospace;font-size:14px;padding:20px;background:#111;color:#eee;max-width:900px;margin:0 auto}
h1{color:#4fc3f7;border-bottom:1px solid #333;padding-bottom:10px}
h2{color:#4fc3f7;margin-top:30px}
.stat-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin:20px 0}
.stat{background:#1a1a2e;padding:16px;border-radius:8px;border:1px solid #333}
.stat .num{font-size:28px;font-weight:bold;color:#4fc3f7}
.stat .label{color:#888;margin-top:4px}
a{color:#4fc3f7;text-decoration:none}a:hover{text-decoration:underline;color:#fff}
.file-link{display:inline-block;background:#1a1a2e;padding:8px 16px;border-radius:6px;border:1px solid #333;margin:4px}
.section{margin:20px 0}
details{margin:8px 0}
details summary{cursor:pointer;color:#4fc3f7;padding:4px 0}
details summary:hover{color:#fff}
ul{padding-left:20px}li{padding:2px 0}
</style></head><body>
'''
    html += '<h1>Instagram Account Analysis</h1>\n'

    # Stats grid
    html += '<div class="stat-grid">\n'
    for label, value in stats.items():
        html += f'<div class="stat"><div class="num">{value}</div><div class="label">{html_mod.escape(label)}</div></div>\n'
    html += '</div>\n'

    # File links
    html += '<h2>Generated Reports</h2>\n'
    file_descriptions = {
        "not_following_back.html": ("Not Following You Back", "#4fc3f7"),
        "fans_you_dont_follow.html": ("Fans (Follow You, You Don't Follow Back)", "#66bb6a"),
        "mutuals.html": ("Mutuals", "#ab47bc"),
        "active_not_following_back.html": ("Active (Not Following Back, Verified Existing)", "#4fc3f7"),
        "deleted_accounts.html": ("Deleted / Deactivated Accounts", "#ff5252"),
        "inconclusive_accounts.html": ("Inconclusive (Rate Limited)", "#ff9800"),
    }
    for fname, (desc, _) in file_descriptions.items():
        html += f'<a class="file-link" href="{fname}">{desc}</a>\n'

    # Extra lists
    for label, users in lists.items():
        html += f'\n<details><summary>{html_mod.escape(label)} ({len(users)})</summary><ul>\n'
        for u in users:
            eu = html_mod.escape(u)
            html += f'<li><a href="https://www.instagram.com/{eu}/" target="_blank">{eu}</a></li>\n'
        if len(users) > 200:
            html += f'<li>... and {len(users) - 200} more</li>\n'
        html += '</ul></details>\n'

    html += '</body></html>'
    Path(filename).write_text(html, encoding="utf-8")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

REPORT_DIRNAME = "ig-reports"


def default_output_dir(input_path: str | None) -> Path:
    """Pick where reports go when --output-dir isn't given.

    Reports name real people, so they must never be written loose next to this
    script -- a checkout of this repo is a normal place to run it from, and a
    stray `git add -A` there would publish them. Reports land beside the export
    they came from, or in the home directory if that would put them inside the
    checkout.
    """
    base = Path(input_path).resolve().parent if input_path else Path.cwd().resolve()
    tool_dir = Path(__file__).resolve().parent

    if base == tool_dir or tool_dir in base.parents:
        return Path.home() / "ig-unfollow-checker-reports"
    return base / REPORT_DIRNAME


def main():
    parser = argparse.ArgumentParser(
        description="Instagram Unfollow Checker",
        epilog="DISCLAIMER: This tool is provided as-is for personal use. "
               "Use at your own risk. The author is not responsible for any "
               "account restrictions, rate limiting, or actions taken by Instagram. "
               "Using a VPN is recommended."
    )
    parser.add_argument("zipfile", nargs="?", help="Path to Instagram data export zip")
    parser.add_argument("--check-list", help="Check a plain text file of usernames (one per line)")
    parser.add_argument("--diff", nargs=2, metavar=("OLD_ZIP", "NEW_ZIP"), help="Compare two exports to find who unfollowed/blocked you")
    parser.add_argument("--analyze-only", action="store_true", help="Only analyze the zip, skip browser checks")
    parser.add_argument("--start-at", type=int, default=0, help="Resume from position N")
    parser.add_argument("--resume", action="store_true",
                        help="Continue from results.json, skipping accounts already "
                             "decided and retrying errors/rate-limited entries")
    parser.add_argument("--limit", type=int,
                        help="Check at most N accounts this run (for a trial run)")
    parser.add_argument("--output-dir", default=None,
                        help=f"Where to write reports (default: {REPORT_DIRNAME}/ "
                             "beside the export you point it at)")
    parser.add_argument("--show-browser", action="store_true")
    args = parser.parse_args()

    if not args.zipfile and not args.check_list and not args.diff:
        parser.error("Provide an Instagram export zip, --check-list <file>, or --diff <old> <new>")

    if args.output_dir:
        out = Path(args.output_dir)
    else:
        source = args.zipfile or args.check_list or (args.diff[1] if args.diff else None)
        out = default_output_dir(source)
        print(f"Writing reports to {out}")
        print("  (these list real usernames -- use --output-dir to put them elsewhere)\n")
    out.mkdir(parents=True, exist_ok=True)
    results_path = out / "results.json"
    bios_path = out / "bios.json"

    # =======================================================================
    # MODE 0: Diff two exports
    # =======================================================================
    if args.diff:
        old_zip, new_zip = args.diff
        for zp in [old_zip, new_zip]:
            if not Path(zp).exists():
                print(f"Error: {zp} not found.")
                sys.exit(1)

        print(f"Comparing exports...")
        print(f"  Old: {Path(old_zip).name}")
        print(f"  New: {Path(new_zip).name}")

        changes, counts = diff_exports(old_zip, new_zip)

        print(f"\n  Old: {counts['old_following']} following, {counts['old_followers']} followers")
        print(f"  New: {counts['new_following']} following, {counts['new_followers']} followers")
        print(f"  Net followers: {counts['new_followers'] - counts['old_followers']:+d}")
        print(f"  Net following: {counts['new_following'] - counts['old_following']:+d}")

        for label, users in changes.items():
            print(f"\n  {label}: {len(users)}")
            color_map = {
                "Lost Followers": "#ff5252",
                "New Followers": "#66bb6a",
                "You Unfollowed": "#ff9800",
                "You Started Following": "#4fc3f7",
                "Possible Blocks": "#ff1744",
            }
            color = "#4fc3f7"
            for key, c in color_map.items():
                if key in label:
                    color = c
                    break
            safe_name = re.sub(r'[^a-z0-9]+', '_', label.lower()).strip('_') + ".html"
            track = "Possible Blocks" in label or "Lost Followers" in label
            generate_html(users, label, f"Between {Path(old_zip).stem} and {Path(new_zip).stem}",
                          color, str(out / safe_name), track_clicks=track)
            (out / safe_name.replace('.html', '.txt')).write_text("\n".join(users) + "\n")

        # Optionally check if "lost followers" accounts still exist
        lost = changes.get("Lost Followers (unfollowed you or blocked you)", [])
        possible_blocks = changes.get("Possible Blocks (were mutual, now they don't follow you)", [])
        check_candidates = sorted(set(lost + possible_blocks))

        if check_candidates and not args.analyze_only:
            print(f"\n  Checking {len(check_candidates)} lost/possibly-blocked accounts...")
            all_results, stopped_early = run_checks(_cap(check_candidates, args.limit), args.start_at, results_path,
                                                 args.show_browser, args.resume, bios_path)

            exists = sorted([r['username'] for r in all_results if r['status'] in ('EXISTS', 'EXISTS_PRIVATE')])
            not_found = sorted([r['username'] for r in all_results if r['status'] == 'NOT_FOUND'])

            generate_html(exists,
                          "Lost Followers — Account Still Active",
                          "They unfollowed you (or blocked you). Account exists.",
                          "#ff5252", str(out / "lost_followers_active.html"), track_clicks=True)
            generate_html(not_found,
                          "Lost Followers — Account Deleted",
                          "They deactivated or deleted their account.",
                          "#888", str(out / "lost_followers_deleted.html"))

            print(f"\n  Still active (unfollowed or blocked you): {len(exists)}")
            print(f"  Account deleted/deactivated: {len(not_found)}")
            if stopped_early:
                print(f"\n  Resume: python3 ig_unfollow_checker.py --diff {old_zip} {new_zip} --start-at {len(all_results)}")

        print("\nDONE")
        return

    # =======================================================================
    # MODE 1: Check a plain list of usernames
    # =======================================================================
    if args.check_list:
        filepath = args.check_list
        if not Path(filepath).exists():
            print(f"Error: {filepath} not found.")
            sys.exit(1)

        usernames = load_usernames_from_file(filepath)
        print(f"Loaded {len(usernames)} usernames from {filepath}")

        all_results, stopped_early = run_checks(_cap(usernames, args.limit), args.start_at, results_path,
                                                 args.show_browser, args.resume, bios_path)

        exists = sorted([r['username'] for r in all_results if r['status'] in ('EXISTS', 'EXISTS_PRIVATE')])
        not_found = sorted([r['username'] for r in all_results if r['status'] == 'NOT_FOUND'])
        inconclusive = sorted([r['username'] for r in all_results if r['status'] in ('LOGIN_WALL', 'ERROR', 'UNKNOWN')])

        generate_html(exists, "Existing Accounts", "These accounts still exist.", "#4fc3f7", str(out / "existing_accounts.html"))
        generate_html(not_found, "Deleted / Unavailable", "These profiles no longer exist.", "#ff5252", str(out / "deleted_accounts.html"))
        if inconclusive:
            generate_html(inconclusive, "Inconclusive", "Could not determine. Recheck with a fresh VPN.", "#ff9800", str(out / "inconclusive_accounts.html"))

        print("\n" + "=" * 60)
        print("DONE")
        print("=" * 60)
        print(f"  Existing:     {len(exists)}")
        print(f"  Deleted:      {len(not_found)}")
        print(f"  Inconclusive: {len(inconclusive)}")
        if stopped_early:
            print(f"\nResume: python3 ig_unfollow_checker.py --check-list {filepath} --start-at {len(all_results)}")
        return

    # =======================================================================
    # MODE 2: Full analysis from Instagram export zip
    # =======================================================================
    zip_path = args.zipfile
    if not Path(zip_path).exists():
        print(f"Error: {zip_path} not found.")
        sys.exit(1)

    # --- Step 1: Extract and analyze ---
    print(f"Extracting from {zip_path}...")
    following, followers = extract_usernames_from_zip(zip_path)
    extras = extract_extra_lists_from_zip(zip_path)

    not_following_back = sorted(following - followers)
    fans = sorted(followers - following)
    mutuals = sorted(following & followers)

    print(f"  Following:             {len(following)}")
    print(f"  Followers:             {len(followers)}")
    print(f"  Mutuals:               {len(mutuals)}")
    print(f"  Not following back:    {len(not_following_back)}")
    print(f"  Fans (you don't follow): {len(fans)}")
    for label, users in extras.items():
        print(f"  {label}: {len(users)}")

    # Generate all analysis HTMLs
    generate_html(not_following_back,
                  "Not Following You Back",
                  f"You follow them, they don't follow you. ({Path(zip_path).stem})",
                  "#4fc3f7",
                  str(out / "not_following_back.html"),
                  track_clicks=True)

    generate_html(fans,
                  "Fans — Follow You But You Don't Follow Back",
                  "These people follow you but you haven't followed them back.",
                  "#66bb6a",
                  str(out / "fans_you_dont_follow.html"),
                  track_clicks=True)

    generate_html(mutuals,
                  "Mutuals",
                  "You follow each other.",
                  "#ab47bc",
                  str(out / "mutuals.html"))

    # Extra lists from export
    for label, users in extras.items():
        safe_name = re.sub(r'[^a-z0-9]+', '_', label.lower()).strip('_') + ".html"
        generate_html(users, label, f"From your Instagram export.", "#ff9800", str(out / safe_name))

    # Save raw text lists
    (out / "not_following_back.txt").write_text("\n".join(not_following_back) + "\n")
    (out / "fans_you_dont_follow.txt").write_text("\n".join(fans) + "\n")
    (out / "mutuals.txt").write_text("\n".join(mutuals) + "\n")

    # Summary dashboard
    stats = {
        "Following": len(following),
        "Followers": len(followers),
        "Mutuals": len(mutuals),
        "Not Following Back": len(not_following_back),
        "Fans": len(fans),
    }
    generate_summary_html(stats, extras, str(out / "dashboard.html"))

    print(f"\n  Generated: dashboard.html, not_following_back.html, fans_you_dont_follow.html, mutuals.html")

    if args.analyze_only:
        print("\n--analyze-only: skipping browser checks.")
        print("DONE")
        return

    if not not_following_back:
        print("\nEveryone follows you back!")
        return

    # --- Step 2: Check which "not following back" accounts still exist ---
    all_results, stopped_early = run_checks(_cap(not_following_back, args.limit), args.start_at, results_path,
                                             args.show_browser, args.resume, bios_path)

    # --- Step 3: Generate checked output ---
    exists = sorted([r['username'] for r in all_results if r['status'] in ('EXISTS', 'EXISTS_PRIVATE')])
    not_found = sorted([r['username'] for r in all_results if r['status'] == 'NOT_FOUND'])
    inconclusive = sorted([r['username'] for r in all_results if r['status'] in ('LOGIN_WALL', 'ERROR', 'UNKNOWN')])

    generate_html(exists,
                  "Active Accounts Not Following You Back",
                  "These accounts exist and are NOT following you back.",
                  "#4fc3f7",
                  str(out / "active_not_following_back.html"),
                  track_clicks=True)

    generate_html(not_found,
                  "Deleted / Unavailable Accounts",
                  "Ghost entries — these profiles no longer exist.",
                  "#ff5252",
                  str(out / "deleted_accounts.html"))

    if inconclusive:
        generate_html(inconclusive,
                      "Inconclusive (Rate Limited / Error)",
                      "Could not determine status. Recheck with a fresh VPN.",
                      "#ff9800",
                      str(out / "inconclusive_accounts.html"))

    # Final summary
    print("\n" + "=" * 60)
    print("DONE")
    print("=" * 60)
    print(f"  Following:           {len(following)}")
    print(f"  Followers:           {len(followers)}")
    print(f"  Mutuals:             {len(mutuals)}")
    print(f"  Not following back:  {len(not_following_back)}")
    print(f"  Fans:                {len(fans)}")
    print(f"  Confirmed active:    {len(exists)}")
    print(f"  Confirmed deleted:   {len(not_found)}")
    print(f"  Inconclusive:        {len(inconclusive)}")
    print()
    print("Output files:")
    print(f"  dashboard.html                 — overview of everything")
    print(f"  not_following_back.html         — {len(not_following_back)} accounts")
    print(f"  fans_you_dont_follow.html       — {len(fans)} accounts")
    print(f"  mutuals.html                   — {len(mutuals)} accounts")
    print(f"  active_not_following_back.html  — {len(exists)} accounts (verified existing)")
    print(f"  deleted_accounts.html           — {len(not_found)} accounts")
    if inconclusive:
        print(f"  inconclusive_accounts.html      — {len(inconclusive)} accounts")
    print(f"  results.json                    — raw check data")

    if stopped_early:
        print(f"\nResume: python3 ig_unfollow_checker.py {zip_path} --start-at {len(all_results)}")


if __name__ == "__main__":
    main()
