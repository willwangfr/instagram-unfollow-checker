# ig-unfollow-checker

Find out which Instagram accounts in your "not following back" list are **deleted or deactivated** — so you don't waste time clicking dead profiles.

Takes your Instagram data export zip, computes who's not following you back, then checks each profile with a headless browser to see if it still exists. Outputs clickable HTML files.

## How it works

1. Parses followers/following from your Instagram HTML export
2. Computes the "not following back" diff
3. Opens each profile in a headless Chromium browser (via Playwright)
4. Checks the page title to determine: **exists**, **deleted**, **private**, or **rate-limited**
5. Generates clickable HTML reports with click-tracking

## Setup

```bash
pip install playwright
python -m playwright install chromium
```

## Usage

### 1. Download your Instagram data

Instagram → Settings → Your Activity → Download Your Information → **HTML format**

### 2. Turn on a VPN

Instagram blocks Tor and rate-limits aggressively. Use any VPN (ProtonVPN Free works fine). This hides your real IP.

### 3. Run

```bash
python3 ig_unfollow_checker.py your-export.zip
```

### 4. If rate-limited (login wall), switch VPN server and resume

```bash
python3 ig_unfollow_checker.py your-export.zip --start-at 500
```

## Output files

| File | Description |
|------|-------------|
| `active_not_following_back.html` | Real accounts not following you back (with click tracking) |
| `deleted_accounts.html` | Deleted/deactivated ghost accounts |
| `inconclusive_accounts.html` | Couldn't determine (rate-limited) |
| `results.json` | Raw data for all checks |
| `usernames.txt` | Plain list of "not following back" usernames |

## Options

```
--start-at N       Resume from position N (after rate limit)
--output-dir DIR   Save output files to DIR
--show-browser     Show the browser window (for debugging)
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

## FAQ

**Why not just use an app?**
Every Instagram follower-checker app requires your IG login. Many are sketchy. This tool uses no login at all.

**Why Playwright instead of HTTP requests?**
Instagram serves identical HTML shells for all profiles and renders content via JavaScript. Raw HTTP requests can't distinguish real from deleted accounts. Playwright runs the actual browser JavaScript.

**Why VPN instead of Tor?**
Instagram blocks all Tor exit nodes. VPN IPs are treated as normal users.

**Can I get banned?**
You're not logged in, not using your real IP, and the tool is conservative with rate limiting. The worst that happens is the VPN IP gets temporarily blocked (login wall) — just switch servers.
