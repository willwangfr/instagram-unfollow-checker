#!/usr/bin/env python3
# Copyright (C) 2026 William Wang
# Licensed under the GNU AGPL v3 or later. See LICENSE.
"""Keep checking until the list is finished, surviving rate limits on its own.

The checker stops after three consecutive login walls, which is correct — but it
means a long run needs a person to restart it every few thousand accounts. This
restarts it, and waits long enough first.

Waiting matters more than it looks. Resuming a few minutes after a wall gets you
walled again almost immediately: in one measured session a run restarted right
after a stop managed 60 accounts, while the same list after a long pause managed
2,459. So the cooldown starts long and grows each time the run comes back walled,
and resets once a run ends for any other reason.
"""

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent


def count_done(out_dir):
    p = Path(out_dir) / "results.json"
    if not p.exists():
        return 0
    try:
        return len(json.loads(p.read_text()).get("results", []))
    except (json.JSONDecodeError, OSError):
        return 0


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check-list", required=True)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--cooldown", type=int, default=45,
                    help="minutes to wait after a rate-limited stop (default 45)")
    ap.add_argument("--max-cooldown", type=int, default=240,
                    help="cap on the growing cooldown, in minutes (default 240)")
    ap.add_argument("--max-hours", type=float, default=24)
    args = ap.parse_args()

    total = len([l for l in Path(args.check_list).read_text().splitlines() if l.strip()])
    started = time.time()
    cooldown = args.cooldown
    attempt = 0

    while True:
        attempt += 1
        before = count_done(args.output_dir)
        if before >= total:
            log(f"all {total} accounts checked — done")
            return 0
        log(f"pass {attempt}: {before}/{total} done, starting")

        r = subprocess.run(
            [sys.executable, "-u", str(HERE / "ig_unfollow_checker.py"),
             "--check-list", args.check_list, "--resume",
             "--output-dir", args.output_dir],
            capture_output=True, text=True)
        after = count_done(args.output_dir)
        gained = after - before
        walled = "Rate limited" in r.stdout

        if r.returncode != 0 and not walled:
            log(f"checker exited {r.returncode}: {(r.stderr or '').strip()[-300:]}")
            return r.returncode
        if after >= total:
            log(f"all {total} accounts checked — done ({gained} this pass)")
            return 0

        elapsed = (time.time() - started) / 3600
        if elapsed > args.max_hours:
            log(f"stopping: {args.max_hours}h limit reached at {after}/{total}")
            return 0

        if walled:
            # Came back walled: the address needs longer than last time.
            log(f"rate limited after {gained} accounts ({after}/{total}). "
                f"waiting {cooldown} min")
            time.sleep(cooldown * 60)
            cooldown = min(cooldown * 2, args.max_cooldown)
        else:
            log(f"stopped after {gained} accounts without a wall; retrying shortly")
            time.sleep(60)
            cooldown = args.cooldown


if __name__ == "__main__":
    sys.exit(main())
