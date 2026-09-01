#!/usr/bin/env python3
# Copyright (C) 2026 William Wang
# Licensed under the GNU AGPL v3 or later. See LICENSE.
# Run a modified version as a network service and you must offer users its source.
"""Grade checked accounts into ghost tiers.

`results.json` only records whether a profile still resolves. That answers
"deleted or not" but not "is anyone home" — an account can exist, follow
2000 people, and have never posted. This reads the same results and sorts
them into tiers from confirmed-dead to plainly active.
"""

import argparse
import csv
import html as html_mod
import json
import re
from pathlib import Path

# Counts arrive as display strings: "1,234", "12.5K", "1.2M".
_SUFFIX = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}


def parse_count(value):
    if value is None:
        return None
    s = str(value).strip().replace(",", "")
    m = re.fullmatch(r"([\d.]+)\s*([KMB])?", s, re.I)
    if not m:
        return None
    try:
        n = float(m.group(1))
    except ValueError:
        return None
    if m.group(2):
        n *= _SUFFIX[m.group(2).upper()]
    return int(n)


TIERS = [
    ("deleted", "Deleted or deactivated", "#ff5252",
     "The profile no longer resolves. Hard ghost."),
    # Logged out, Instagram does not expose the private flag: private profiles
    # serve the same title and body as public ones, so the checker cannot tell
    # them apart. Post counts ARE reported for both, so "zero posts" is sound
    # even though "public" would not be.
    ("empty_public", "Zero posts", "#ff7043",
     "Account exists but has never posted. Public or private is undetermined."),
    ("empty_private", "Private, zero posts", "#ffa726",
     "Only reachable if a future logged-out check can detect the private flag."),
    ("bot_like", "Follow-farm shaped", "#ffca28",
     "Follows a crowd, is followed by almost nobody, barely posts."),
    ("near_empty", "Near-empty shell", "#d4e157",
     "One or two posts, no bio, no link — signed up and stopped."),
    ("low_signal", "Low signal", "#9ccc65",
     "Few posts and a small audience. Dormant rather than dead."),
    ("active", "Active", "#4fc3f7",
     "A real, populated account that simply does not follow you back."),
    ("inconclusive", "Inconclusive", "#888",
     "Rate limited or errored. Re-run these before drawing conclusions."),
]
TIER_ORDER = [t[0] for t in TIERS]


def classify(r):
    """Return (tier, reason) for one result record."""
    status = r.get("status")
    if status in ("LOGIN_WALL", "ERROR", "UNKNOWN"):
        return "inconclusive", f"status {status}"
    if status == "NOT_FOUND":
        return "deleted", "profile does not resolve"

    posts = parse_count(r.get("posts"))
    followers = parse_count(r.get("followers"))
    following = parse_count(r.get("following"))
    bio = (r.get("bio") or "").strip()
    link = (r.get("external_link") or "").strip()
    private = status == "EXISTS_PRIVATE"

    if posts == 0:
        return ("empty_private" if private else "empty_public"), "0 posts"

    # A header that never rendered leaves every count None. Saying nothing is
    # better than reading the blank as emptiness.
    if posts is None and followers is None and following is None:
        return "inconclusive", "profile header did not render"

    if (following is not None and following >= 1000
            and (not followers or following / max(followers, 1) >= 5)
            and (posts is not None and posts <= 5)):
        return "bot_like", f"{following} following vs {followers} followers, {posts} posts"

    if posts is not None and posts <= 2 and not bio and not link:
        return "near_empty", f"{posts} posts, no bio or link"

    if (posts is not None and posts <= 5
            and followers is not None and followers < 50):
        return "low_signal", f"{posts} posts, {followers} followers"

    return "active", f"{posts} posts, {followers} followers"


def render_html(rows, out_path, source_label):
    counts = {t: 0 for t in TIER_ORDER}
    for r in rows:
        counts[r["tier"]] += 1
    total = len(rows)

    doc = ['<html><head><title>Ghost Profiles (%d)</title><style>' % total]
    doc.append("""
body{font-family:monospace;font-size:14px;padding:20px;background:#111;color:#eee;max-width:1100px;margin:0 auto}
h2{color:#4fc3f7}h3{margin-top:38px;border-bottom:1px solid #333;padding-bottom:6px}
a{text-decoration:none}a:hover{text-decoration:underline;color:#fff}
.note{color:#888;margin-bottom:24px}
.legend{display:flex;flex-wrap:wrap;gap:10px;margin:20px 0 30px}
.chip{background:#1c1c1c;border:1px solid #333;border-radius:8px;padding:10px 14px}
.chip b{font-size:20px;display:block}
table{border-collapse:collapse;width:100%}
td{padding:4px 10px 4px 0;vertical-align:top;border-bottom:1px solid #1e1e1e}
td.n{color:#555;text-align:right;width:44px}
td.meta{color:#777;font-size:12px}
td.name{color:#bbb;font-size:12px;max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
""")
    doc.append('</style></head><body>')
    doc.append('<h2>Ghost profiles &mdash; %d accounts checked</h2>' % total)
    doc.append('<p class="note">%s</p>' % html_mod.escape(source_label))

    doc.append('<div class="legend">')
    for key, label, color, _ in TIERS:
        doc.append('<div class="chip" style="border-color:%s"><b style="color:%s">%d</b>%s</div>'
                   % (color, color, counts[key], html_mod.escape(label)))
    doc.append('</div>')

    for key, label, color, blurb in TIERS:
        group = [r for r in rows if r["tier"] == key]
        if not group:
            continue
        doc.append('<h3 style="color:%s">%s &mdash; %d</h3>' % (color, html_mod.escape(label), len(group)))
        doc.append('<p class="note">%s</p><table>' % html_mod.escape(blurb))
        for i, r in enumerate(group, 1):
            u = html_mod.escape(r["username"])
            doc.append(
                '<tr><td class="n">%d</td>'
                '<td><a href="https://www.instagram.com/%s/" target="_blank" style="color:%s">%s</a></td>'
                '<td class="name">%s</td><td class="meta">%s</td></tr>'
                % (i, u, color, u,
                   html_mod.escape(r.get("full_name") or ""),
                   html_mod.escape(r["reason"])))
        doc.append('</table>')

    doc.append('</body></html>')
    Path(out_path).write_text("\n".join(doc))


def main():
    ap = argparse.ArgumentParser(description="Grade checked accounts into ghost tiers")
    ap.add_argument("results", help="results.json from ig_unfollow_checker")
    ap.add_argument("--output-dir", default=".")
    args = ap.parse_args()

    data = json.loads(Path(args.results).read_text())
    results = data.get("results", data)
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    rows = []
    for r in results:
        tier, reason = classify(r)
        rows.append({
            "username": r.get("username"),
            "tier": tier,
            "reason": reason,
            "status": r.get("status"),
            "full_name": r.get("full_name"),
            "bio": (r.get("bio") or "").replace("\n", " / "),
            "posts": r.get("posts"),
            "followers": r.get("followers"),
            "following": r.get("following"),
            "external_link": r.get("external_link"),
        })
    rows.sort(key=lambda r: (TIER_ORDER.index(r["tier"]), r["username"]))

    with (out / "ghost_profiles.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    for key, label, _, _ in TIERS:
        names = [r["username"] for r in rows if r["tier"] == key]
        if names:
            (out / f"ghosts_{key}.txt").write_text("\n".join(names) + "\n")

    render_html(rows, out / "ghost_profiles.html",
                f"Graded from {Path(args.results).name} "
                f"({data.get('timestamp', 'unknown time')}).")

    print(f"Graded {len(rows)} accounts")
    for key, label, _, _ in TIERS:
        n = sum(1 for r in rows if r["tier"] == key)
        if n:
            print(f"  {label:26s} {n:5d}")
    print(f"\nWrote ghost_profiles.html, ghost_profiles.csv and per-tier .txt to {out}")


if __name__ == "__main__":
    main()
