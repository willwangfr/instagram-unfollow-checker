#!/usr/bin/env python3
"""Reconstruct each account's follow history across every export snapshot.

A single export says who follows you now. A stack of them says when each
person arrived and when they left, which is the only way to date an unfollow
— Instagram logs the follows you remove, never the ones removed from you.
"""

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import igpaths

TABLE_USER_RE = re.compile(
    r'<td[^>]*>Username</td>\s*<td[^>]*>([A-Za-z0-9_.]+)</td>', re.I)


def _users(html):
    u = re.findall(r'href="https://www\.instagram\.com/_u/([a-zA-Z0-9_.]+)"', html)
    if not u:
        u = re.findall(r'href="https://www\.instagram\.com/([a-zA-Z0-9_.]+)"', html)
    if not u:
        u = TABLE_USER_RE.findall(html)
    return u


def export_generated_at(zip_path):
    """When Instagram built this export.

    Every page carries it in a <time datetime="..."> stamp. Ages ("you have
    followed them N days") must be measured from that instant, not from today,
    or they drift every time the analysis is re-run.
    """
    with zipfile.ZipFile(zip_path) as z:
        n = next(x for x in z.namelist()
                 if x.endswith("followers_1.html") and "followers_and_following" in x)
        head = z.read(n).decode("utf-8", "replace")[:60000]
    m = re.search(r'<time datetime="([0-9]{4}-[0-9]{2}-[0-9]{2})T([0-9]{2}):([0-9]{2})', head)
    if not m:
        raise SystemExit(f"No generation timestamp found in {zip_path}")
    import datetime as _dt
    return _dt.datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}:{m.group(3)}")


def load_snapshot(zip_path):
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        fo = next(n for n in names if n.endswith('following.html') and 'followers_and_following' in n)
        fl = next(n for n in names if n.endswith('followers_1.html') and 'followers_and_following' in n)
        following = set(_users(z.read(fo).decode('utf-8')))
        followers = set(_users(z.read(fl).decode('utf-8')))
    return following, followers


# Every follower and following entry carries the moment the follow happened,
# rendered next to the link. Nothing downstream can date a relationship without
# it, so this is step one of the pipeline.
_DATED = re.compile(
    r'href="https://www\.instagram\.com/(?:_u/)?([A-Za-z0-9_.]+)"[^>]*>.*?'
    r'</a></div>\s*<div>([^<]+)</div>', re.S)


def follow_dates_from_zip(zip_path):
    """{"following": {user: iso}, "followers": {user: iso}} from one export."""
    import datetime as _dt
    out = {}
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        for key, suffix in (("following", "following.html"),
                            ("followers", "followers_1.html")):
            n = next((x for x in names if x.endswith(suffix)
                      and "followers_and_following" in x), None)
            got = {}
            if n:
                for u, t in _DATED.findall(z.read(n).decode("utf-8", "replace")):
                    try:
                        got[u] = _dt.datetime.strptime(
                            t.strip(), "%b %d, %Y %I:%M %p").isoformat()
                    except ValueError:
                        pass
            out[key] = got
    return out


def handle_continuity(snaps, dates, follow_dates):
    """Has each handle been continuously present since you followed it?

    A rename makes someone vanish from your following list and reappear under a
    new handle, which is indistinguishable from an unfollow unless you ask this
    question. Asking "present in every snapshot" instead is wrong: it fails
    anyone you simply started following later.
    """
    import datetime as _dt
    snap_at = {d: _dt.datetime.strptime(d, "%Y-%m-%d") for d in dates}
    out = {}
    for u in sorted(snaps[dates[-1]][0]):
        iso = follow_dates.get(u)
        if not iso:
            out[u] = "unknown_follow_date"
            continue
        since = _dt.datetime.fromisoformat(iso)
        expected = [d for d in dates if snap_at[d] >= since]
        if not expected:
            out[u] = "followed_after_last_snapshot"
        elif all(u in snaps[d][0] for d in expected):
            out[u] = "continuous"
        else:
            out[u] = "gap"
    return out


def load_unfollow_log(zip_path):
    """Instagram's own record of accounts YOU unfollowed: {username: timestamp}."""
    pat = re.compile(
        r'<td[^>]*>Username</td>\s*<td[^>]*>([A-Za-z0-9_.]+)</td>.*?'
        r'<div class="_3-94 _a6-o">([^<]*)</div>', re.S)
    with zipfile.ZipFile(zip_path) as z:
        n = next((x for x in z.namelist()
                  if 'recently_unfollowed_profiles' in x and x.endswith('.html')), None)
        if not n:
            return {}
        return dict(pat.findall(z.read(n).decode('utf-8')))


def main():
    ap = argparse.ArgumentParser(
        description="Step 1: read the exports and write what every other "
                    "script here depends on.")
    ap.add_argument("snapshots", nargs="*",
                    help="date=path pairs, oldest first (default: from --config)")
    ap.add_argument("--output-dir", help="default: work_dir from the config")
    igpaths.add_config_arg(ap)
    args = ap.parse_args()

    if args.snapshots:
        dates, paths = [], []
        for s in args.snapshots:
            d, p = s.split("=", 1)
            dates.append(d)
            paths.append(p)
        out = Path(args.output_dir or ".")
    else:
        cfg = igpaths.load(args.config)
        if not cfg.snapshots:
            raise SystemExit("Your config lists no snapshots. Add at least one.")
        dates = [d for d, _ in cfg.snapshots]
        paths = [str(z) for _, z in cfg.snapshots]
        out = Path(args.output_dir) if args.output_dir else cfg.work_dir
    out.mkdir(parents=True, exist_ok=True)

    snaps = {}
    for d, p in zip(dates, paths):
        snaps[d] = load_snapshot(p)
        print(f"  {d}: {len(snaps[d][0])} following, {len(snaps[d][1])} followers")

    latest = dates[-1]
    new_fo, new_fl = snaps[latest]
    log = load_unfollow_log(paths[-1])
    print(f"  unfollow log: {len(log)} entries\n")

    nfb = sorted(new_fo - new_fl)

    records = {}
    for u in nfb:
        seen_as_follower = [d for d in dates if u in snaps[d][1]]
        if not seen_as_follower:
            records[u] = {"ever_followed": False, "left_between": None,
                          "last_seen_following_you": None}
            continue
        last = seen_as_follower[-1]
        i = dates.index(last)
        after = dates[i + 1] if i + 1 < len(dates) else None
        records[u] = {
            "ever_followed": True,
            "last_seen_following_you": last,
            "left_between": f"{last} .. {after}" if after else "still following",
        }

    # Accounts that left YOUR following list without you unfollowing them.
    # Instagram removes your follow when someone blocks you, so an exit with
    # no matching log entry is the signature a block leaves behind.
    block_candidates = {}
    for i in range(len(dates) - 1):
        older, newer = dates[i], dates[i + 1]
        left = snaps[older][0] - snaps[newer][0]
        for u in sorted(left):
            if u in log:
                continue
            block_candidates.setdefault(u, []).append(f"{older} .. {newer}")

    fd = follow_dates_from_zip(paths[-1])
    (out / "follow_dates.json").write_text(json.dumps(fd, indent=2))
    cont = handle_continuity(snaps, dates, fd["following"])
    (out / "continuity_v2.json").write_text(json.dumps(
        {"snapshots": dates, "continuity": cont,
         "ever_followed": {u: any(u in snaps[d][1] for d in dates)
                           for u in sorted(new_fo - new_fl)}}, indent=2))
    stable = sorted(set.intersection(*[snaps[d][0] for d in dates])) if len(dates) > 1 \
        else sorted(snaps[dates[0]][0])
    (out / "handle_stability.json").write_text(json.dumps(
        {"snapshots": dates, "stable_handles": stable}, indent=2))
    print(f"  follow dates: {len(fd['following'])} following, "
          f"{len(fd['followers'])} followers")
    print(f"  handle continuity: "
          f"{sum(1 for v in cont.values() if v == 'continuous')} continuous, "
          f"{sum(1 for v in cont.values() if v == 'gap')} renamed")

    (out / "follow_timeline.json").write_text(json.dumps(
        {"snapshots": dates,
         "not_following_back": records,
         "left_your_following_without_your_action": block_candidates,
         "your_unfollow_log_size": len(log)}, indent=2))

    never = [u for u, r in records.items() if not r["ever_followed"]]
    dropped = [u for u, r in records.items() if r["ever_followed"]]
    print(f"Not following you back: {len(nfb)}")
    print(f"  never followed you in any snapshot : {len(never)}")
    print(f"  followed you at some point, then left: {len(dropped)}")
    from collections import Counter
    c = Counter(records[u]["left_between"] for u in dropped)
    for window, n in sorted(c.items()):
        print(f"     left between {window}: {n}")
    print(f"\nLeft your following with no unfollow-log entry: {len(block_candidates)}")
    (out / "block_candidates_all_windows.txt").write_text(
        "\n".join(sorted(block_candidates)) + "\n")
    print(f"Wrote follow_timeline.json and block_candidates_all_windows.txt to {out}")


if __name__ == "__main__":
    main()
