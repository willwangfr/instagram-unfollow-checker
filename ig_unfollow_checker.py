"""
Instagram Unfollow Checker — All-in-One

Takes an Instagram data export zip file and:
1. Extracts followers & following lists
2. Computes who's not following you back
3. Checks each account via Playwright (through your VPN) to see if it still exists
4. Outputs clickable HTML files for active + deleted accounts

Requirements:
  pip install playwright
  python -m playwright install chromium

Usage:
  1. Download your data from Instagram (Settings > Your Activity > Download Your Information > HTML format)
  2. Turn on your VPN
  3. Run:
     python3 ig_unfollow_checker.py instagram-yourname-2026-04-04-XXXXXXXX.zip

  Resume after rate limit:
     python3 ig_unfollow_checker.py instagram-yourname-2026-04-04-XXXXXXXX.zip --start-at 500
"""

import re
import sys
import time
import json
import random
import argparse
import zipfile
import tempfile
from pathlib import Path
from datetime import datetime
from playwright.sync_api import sync_playwright


# --- Config ---
MIN_DELAY = 4
MAX_DELAY = 9
BATCH_SIZE = 60
BATCH_PAUSE = 180
PAGE_TIMEOUT = 45000
SETTLE_MS = 2500


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

    # Following uses /_u/username, followers uses /username
    following = set(re.findall(r'instagram\.com/_u/([a-zA-Z0-9_.]+)', following_html))
    followers = set(re.findall(r'instagram\.com/([a-zA-Z0-9_.]+)', followers_html))

    # If the /_u/ pattern doesn't match, try the direct pattern for following too
    if not following:
        following = set(re.findall(r'instagram\.com/([a-zA-Z0-9_.]+)', following_html))

    return following, followers


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

        if f"(@{username})" in title or "Instagram photos and videos" in title:
            return {"username": username, "status": "EXISTS", "title": title}

        body_text = page.locator("body").inner_text()[:1000]
        if "This Account is Private" in body_text or "This account is private" in body_text:
            return {"username": username, "status": "EXISTS_PRIVATE", "title": title}

        return {"username": username, "status": "UNKNOWN", "title": title}

    except Exception as e:
        return {"username": username, "status": "ERROR", "error": str(e)[:200]}


def generate_html(accounts: list[str], title: str, subtitle: str, color: str, filename: str, track_clicks: bool = False):
    """Generate a clickable HTML file."""
    html = f'''<html><head><title>{title} ({len(accounts)})</title>
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

    html += f'<h2>{title} — {len(accounts)}</h2>\n'
    html += f'<p class="note">{subtitle}</p>\n<ol>\n'

    for u in accounts:
        onclick = f' data-user="{u}" onclick="trackClick(this, event)"' if track_clicks else ''
        count_span = f'<span class="count" id="count-{u}"></span>' if track_clicks else ''
        html += f'<li><a href="https://www.instagram.com/{u}/" target="_blank"{onclick}>{u}</a>{count_span}</li>\n'

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
    Path(filename).write_text(html)


def main():
    parser = argparse.ArgumentParser(description="Instagram Unfollow Checker")
    parser.add_argument("zipfile", help="Path to Instagram data export zip")
    parser.add_argument("--start-at", type=int, default=0, help="Resume from position N")
    parser.add_argument("--output-dir", default=".", help="Output directory")
    parser.add_argument("--show-browser", action="store_true")
    args = parser.parse_args()

    zip_path = args.zipfile
    if not Path(zip_path).exists():
        print(f"Error: {zip_path} not found.")
        sys.exit(1)

    out = Path(args.output_dir)
    out.mkdir(exist_ok=True)

    # --- Step 1: Extract from zip ---
    print(f"Extracting from {zip_path}...")
    following, followers = extract_usernames_from_zip(zip_path)
    not_following_back = sorted(following - followers)

    print(f"  Following: {len(following)}")
    print(f"  Followers: {len(followers)}")
    print(f"  Not following you back: {len(not_following_back)}")

    # Save the raw list
    (out / "usernames.txt").write_text("\n".join(not_following_back) + "\n")
    generate_html(not_following_back,
                  "Not Following You Back",
                  f"From export: {Path(zip_path).stem}",
                  "#4fc3f7",
                  str(out / "all_not_following_back.html"))

    if not not_following_back:
        print("Everyone follows you back!")
        return

    # --- Step 2: Check accounts ---
    usernames = not_following_back[args.start_at:]
    if not usernames:
        print("All accounts already checked.")
        return

    # Load previous results if resuming
    results_path = out / "results.json"
    if args.start_at > 0 and results_path.exists():
        prev = json.loads(results_path.read_text())
        all_results = prev.get("results", [])
        print(f"  Loaded {len(all_results)} previous results")
    else:
        all_results = []

    print(f"\nChecking {len(usernames)} accounts with Playwright...")
    print("MAKE SURE YOUR VPN IS ON.")
    est = len(usernames) * (MIN_DELAY + MAX_DELAY) / 2 / 60
    print(f"Estimated time: {est:.0f} min")
    print("-" * 60)

    login_wall_streak = 0
    stopped_early = False

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.show_browser)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
        )
        page = context.new_page()

        for i, username in enumerate(usernames):
            result = check_account(page, username)
            all_results.append(result)

            status = result["status"]
            icon = {"EXISTS": "+", "EXISTS_PRIVATE": "~", "NOT_FOUND": "X",
                    "LOGIN_WALL": "L", "UNKNOWN": "?", "ERROR": "E"}
            idx = i + 1 + args.start_at
            total = len(not_following_back)
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

            if (i + 1) % BATCH_SIZE == 0 and i + 1 < len(usernames):
                print(f"\n  --- Batch pause ({BATCH_PAUSE // 60}min) ---\n")
                time.sleep(BATCH_PAUSE)
            else:
                time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))

        browser.close()

    # --- Step 3: Generate output ---
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

    # Summary
    print("\n" + "=" * 60)
    print("DONE")
    print("=" * 60)
    print(f"  Active accounts:    {len(exists)}")
    print(f"  Deleted accounts:   {len(not_found)}")
    print(f"  Inconclusive:       {len(inconclusive)}")
    print()
    print("Output files:")
    print(f"  active_not_following_back.html  — {len(exists)} accounts (click-tracked)")
    print(f"  deleted_accounts.html           — {len(not_found)} accounts")
    if inconclusive:
        print(f"  inconclusive_accounts.html      — {len(inconclusive)} accounts")
    print(f"  results.json                    — full raw data")

    if stopped_early:
        resume_at = args.start_at + len(all_results) - (0 if args.start_at == 0 else len(all_results) - len(usernames))
        print(f"\nResume: python3 ig_unfollow_checker.py {zip_path} --start-at {len(all_results)}")


if __name__ == "__main__":
    main()
