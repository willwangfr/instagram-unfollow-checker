# ig-unfollow-checker

Analyze your Instagram following/followers and find out which accounts are **deleted, deactivated, or ghost profiles** — without logging into any sketchy third-party app.

**What it does:**
- **Not following you back** — who you follow that doesn't follow you
- **Fans** — who follows you that you don't follow back
- **Mutuals** — accounts you follow each other
- **Ghost detector (!!!) ** — checks each profile with a real browser to see if it still exists
- **Pending requests, recent unfollows** — everything in your export
- **Bulk username checker** — give it any list of usernames to check if they still exist

No Instagram login. No third-party access. Your data stays on your machine.

## How it works

1. Parses followers/following from your Instagram HTML export (or a plain text list)
2. Computes all relationship diffs (not following back, fans, mutuals)
3. Opens each profile in a headless Chromium browser (via Playwright)
4. Checks the page title to determine: **exists**, **deleted**, **private**, or **rate-limited**
5. Generates clickable HTML reports with click-tracking and a summary dashboard

## Setup

```bash
pip install playwright
python -m playwright install chromium
```

## Usage

### 1. Download your Instagram data (HTML format)

You need the official data export from Instagram. Here's how:

<details>
<summary><b>On iPhone / Android</b> (click to expand)</summary>

1. Open Instagram and tap your **profile picture** (bottom right)
2. Tap the **hamburger menu** (three lines, top right)
3. Tap **Accounts Center** (under "Meta" at the top)
4. Tap **Your information and permissions**
5. Tap **Download your information**
6. Tap **Download or transfer information**
7. Select your Instagram account, tap **Next**
8. Choose **Some of your information**
9. Scroll down and check only **Followers and following** (under "Connections")
10. Tap **Next**
11. Choose **Download to device**
12. Set format to **HTML** and quality to **Low** (we only need text data)
13. Tap **Create files**
14. Wait for Instagram's email (usually 5-30 minutes, can take up to 48 hours for large accounts)
15. Open the email, tap the link, download the `.zip` file

</details>

<details>
<summary><b>On Desktop / Web Browser</b> (click to expand)</summary>

1. Go to [instagram.com](https://www.instagram.com) and log in
2. Click **More** (bottom left sidebar) → **Your Activity**
3. Click **Download your information**
4. Click **Request a download**
5. Select your Instagram account
6. Choose **Some of your information**
7. Check only **Followers and following**
8. Click **Next**
9. Set format to **HTML**, date range to **All time**
10. Click **Submit request**
11. Wait for the email from Instagram
12. Click the link in the email to download the `.zip` file

</details>

> **Tip:** Only select "Followers and following" — you don't need posts, messages, etc. This makes the export much smaller and faster.

> **Official help page:** [help.instagram.com/181231772500920](https://help.instagram.com/181231772500920)

### 2. (Recommended) Turn on a VPN

Using a VPN is **strongly recommended** to protect your IP address. Instagram may rate-limit IPs that make too many requests. A VPN means only the VPN's IP gets throttled, not yours.

**Free VPN options:**

| VPN | Data limit | Link |
|-----|-----------|------|
| **ProtonVPN Free** | Unlimited | [protonvpn.com/free-vpn](https://protonvpn.com/free-vpn) |
| **Windscribe Free** | 10 GB/month | [windscribe.com](https://windscribe.com) |
| **Cloudflare WARP (1.1.1.1)** | Unlimited | [1.1.1.1](https://one.one.one.one) |

ProtonVPN Free is the best option — unlimited data, no-logs policy, and easy to switch servers when rate-limited. Just install the app, connect, and run the script.

> **Note:** Tor does NOT work. Instagram blocks all Tor exit nodes.

### 3. Run

```bash
# Full analysis from Instagram export (analyze + check accounts)
python3 ig_unfollow_checker.py your-export.zip

# Just analyze (no browser checks — instant, no VPN needed)
python3 ig_unfollow_checker.py your-export.zip --analyze-only

# Check a custom list of usernames
python3 ig_unfollow_checker.py --check-list usernames.txt
```

The `--check-list` file can contain usernames in any format:
```
someuser
@anotheruser
https://www.instagram.com/thirduser/
# comments are ignored
```

### 4. If rate-limited, switch VPN server and resume

```bash
python3 ig_unfollow_checker.py your-export.zip --start-at 500
```

### 5. (Power Users) Compare exports over time — detect unfollows and possible blocks

If you save your Instagram exports periodically, you can compare them to see exactly who unfollowed you, who you gained, and who might have **blocked** you.

```bash
python3 ig_unfollow_checker.py --diff old-export.zip new-export.zip
```

This generates:
- **Lost followers** — people who were following you before but aren't now
- **New followers** — people who started following you
- **You unfollowed** — accounts you stopped following
- **You started following** — new accounts you followed
- **Possible blocks** — accounts that were **mutual** (you followed each other) but now they don't follow you anymore. This is the strongest signal of a block, since mutuals rarely just unfollow.

It then optionally checks each "lost follower" with the browser to determine if they deleted their account or are still active (and therefore either unfollowed or blocked you).

> **Tip:** Export your data monthly and keep the zips. Name them by date (Instagram does this automatically). The more snapshots you have, the more useful this feature becomes.

## Output files

| File | Description |
|------|-------------|
| `dashboard.html` | Summary overview with stats and links to all reports |
| `not_following_back.html` | Accounts you follow that don't follow you (click-tracked) |
| `fans_you_dont_follow.html` | Accounts that follow you but you don't follow back (click-tracked) |
| `mutuals.html` | Accounts you follow each other |
| `active_not_following_back.html` | Verified existing accounts not following you back |
| `deleted_accounts.html` | Confirmed deleted/deactivated ghost accounts |
| `inconclusive_accounts.html` | Couldn't determine status (rate-limited) |
| `results.json` | Raw data for all browser checks |
| `.txt` files | Plain text versions of each list |

Additional HTML files are generated for any pending requests, recent unfollows, or received follow requests found in your export.

## Options

```
--analyze-only          Just analyze the zip (no browser checks, no VPN needed)
--check-list FILE       Check a plain text file of usernames instead of a zip
--diff OLD_ZIP NEW_ZIP  Compare two exports to detect unfollows/blocks
--start-at N            Resume browser checks from position N (after rate limit)
--output-dir DIR        Save output files to DIR
--show-browser          Show the browser window (for debugging)
```

## Tests

```bash
pip install pytest
pytest tests/                    # unit tests (fast, no network)
pytest tests/ --run-smoke        # + smoke tests (hits real Instagram)
```

## How rate limiting works

- **4-9 second random delay** between each check
- **3-minute pause** every 60 checks
- **Auto-stops** after 3 consecutive login walls
- Results save after every check — nothing lost on interruption
- If rate-limited: switch VPN servers and resume with `--start-at`

## FAQ

**Why not just use an app?**
Every Instagram follower-checker app requires your IG login. Many are sketchy and have been caught harvesting credentials. This tool uses no login at all.

**Why Playwright instead of HTTP requests?**
Instagram serves identical HTML shells for all profiles and renders content via JavaScript. Raw HTTP requests can't distinguish real from deleted accounts. Playwright runs the actual browser JavaScript.

**Why VPN instead of Tor?**
Instagram blocks all Tor exit nodes. VPN IPs are treated as normal residential users.

**Can I get banned?**
You're not logged in, not using your real IP, and the tool is conservative with rate limiting. The worst that happens is the VPN IP gets temporarily blocked (login wall) — just switch servers.

**How long does it take?**
`--analyze-only` is instant. Browser checks take ~4-9 seconds per account. 1,000 accounts = roughly 2-3 hours with batch pauses.

## Disclaimer

**This tool is provided as-is, for personal and educational use only.** By using this software, you acknowledge and agree that:

- **Use at your own risk.** The author(s) are **not responsible** for any account restrictions, rate limiting, shadowbans, temporary blocks, or any other actions taken by Instagram/Meta against your account or IP address as a result of using this tool.
- This tool interacts with Instagram's public-facing web pages in a way that may violate Instagram's [Terms of Use](https://help.instagram.com/581066165581870) or [Community Guidelines](https://help.instagram.com/477434105621119). You are solely responsible for ensuring your use complies with applicable terms and laws.
- **No warranty** is provided, express or implied. The software is provided "as is" without warranty of any kind.
- The author(s) make **no guarantees** about the accuracy of results. Instagram's web interface changes frequently and may break detection at any time.
- **If you want to be extra safe:** always use a VPN (see [free options above](#2-recommended-turn-on-a-vpn)), use `--analyze-only` for zero risk, and keep delays conservative.

## License

MIT
