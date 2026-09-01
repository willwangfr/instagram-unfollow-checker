#!/usr/bin/env python3
# Copyright (C) 2026 William Wang
# Licensed under the GNU AGPL v3 or later. See LICENSE.
# Run a modified version as a network service and you must offer users its source.
"""Clickable list of who you follow that doesn't follow back, ghosts removed.

Merges three sources: the follow timeline (did they ever follow you, and in
which window did they leave), every completed browser check (does the profile
still resolve), and the ghost grading (is anyone home).
"""

import argparse
import datetime
import html as html_mod
import json
from pathlib import Path

# The oldest snapshot. Absence from the follower lists says nothing about
# anyone you started following after it.
SNAPSHOTS = ["2026-02-10", "2026-02-24", "2026-03-27",
             "2026-08-12", "2026-08-24", "2026-08-31"]
EXPORT_TIME = datetime.datetime(2026, 8, 30, 21, 39)

DEAD = {"NOT_FOUND"}
LIVE = {"EXISTS", "EXISTS_PRIVATE"}


def load_checks(paths):
    seen = {}
    for p in paths:
        f = Path(p)
        if not f.exists():
            continue
        for r in json.loads(f.read_text()).get("results", []):
            seen[r["username"]] = r
    return seen


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeline", default="follow_timeline.json")
    ap.add_argument("--checks", nargs="+", default=["results.json"])
    ap.add_argument("--dates", default="follow_dates.json")
    ap.add_argument("--stability", default="handle_stability.json")
    ap.add_argument("--out", default="not_following_back_live.html")
    args = ap.parse_args()

    tl = json.loads(Path(args.timeline).read_text())["not_following_back"]
    checks = load_checks(args.checks)
    fd = json.loads(Path(args.dates).read_text())["following"]
    stable = set(json.loads(Path(args.stability).read_text())["stable_handles"])

    rows = []
    for u, rec in tl.items():
        r = checks.get(u)
        status = r["status"] if r else None
        if status in DEAD:
            continue
        followed_on = fd.get(u)
        age = ((EXPORT_TIME - datetime.datetime.fromisoformat(followed_on)).days
               if followed_on else None)
        if rec["ever_followed"]:
            tier = "dropped"
        elif age is not None and age <= 30:
            tier = "too_recent"
        elif u not in stable:
            tier = "unverifiable"
        elif age is not None and age > 365:
            tier = "confirmed"
        else:
            tier = "probable"
        rows.append({
            "username": u, "status": status, "tier": tier,
            "private": status == "EXISTS_PRIVATE", "checked": status is not None,
            "left": rec["left_between"], "followed_on": followed_on, "age": age,
            "name": (r or {}).get("full_name") or "",
            "posts": (r or {}).get("posts"), "followers": (r or {}).get("followers"),
        })

    removed = len(tl) - len(rows)
    unchecked = sum(1 for r in rows if not r["checked"])
    by = lambda t: sorted([r for r in rows if r["tier"] == t],
                          key=lambda r: (-(r["age"] or 0), r["username"]))
    dropped = sorted([r for r in rows if r["tier"] == "dropped"],
                     key=lambda r: (r["left"] or "", r["username"]), reverse=True)

    def table(items, color, show_left=False):
        out = ['<table>']
        for i, r in enumerate(items, 1):
            u = html_mod.escape(r["username"])
            tags = []
            if r["private"]:
                tags.append('<span class="tag priv">private</span>')
            if not r["checked"]:
                tags.append('<span class="tag unk">not yet checked</span>')
            meta = []
            if r["posts"] is not None:
                meta.append(f'{html_mod.escape(str(r["posts"]))} posts')
            if r["followers"] is not None:
                meta.append(f'{html_mod.escape(str(r["followers"]))} followers')
            when = ''
            if show_left and r["left"]:
                when = f'<span class="when">left {html_mod.escape(r["left"])}</span>'
            elif r["followed_on"]:
                yrs = (r["age"] or 0) / 365.0
                span = f'{yrs:.1f}y' if yrs >= 1 else f'{r["age"]}d'
                when = ('<span class="when">you followed '
                        f'{html_mod.escape(r["followed_on"][:10])} · {span} ago</span>')
            out.append(
                f'<tr><td class="n">{i}</td>'
                f'<td><a href="https://www.instagram.com/{u}/" target="_blank" '
                f'style="color:{color}">{u}</a></td>'
                f'<td class="nm">{html_mod.escape(r["name"])}</td>'
                f'<td class="meta">{" · ".join(meta)}</td>'
                f'<td>{"".join(tags)}{when}</td></tr>')
        out.append('</table>')
        return "\n".join(out)

    conf, prob, recent, unver = by("confirmed"), by("probable"), by("too_recent"), by("unverifiable")
    doc = f'''<html><head><title>Not Following Back — Live Accounts ({len(rows)})</title><style>
body{{font-family:monospace;font-size:14px;padding:24px;background:#111;color:#eee;max-width:1150px;margin:0 auto}}
h2{{color:#4fc3f7}}h3{{margin-top:40px;border-bottom:1px solid #333;padding-bottom:6px}}
a{{text-decoration:none}}a:hover{{text-decoration:underline;color:#fff}}
.note{{color:#888;margin-bottom:10px;line-height:1.6}}
table{{border-collapse:collapse;width:100%}}
td{{padding:4px 10px 4px 0;border-bottom:1px solid #1e1e1e;vertical-align:top}}
td.n{{color:#555;text-align:right;width:48px}}
td.nm{{color:#bbb;font-size:12px;max-width:230px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
td.meta{{color:#777;font-size:12px;white-space:nowrap}}
.tag{{font-size:11px;padding:1px 6px;border-radius:4px;margin-right:6px}}
.priv{{background:#3a2f10;color:#ffca28}}.unk{{background:#2a2a2a;color:#888}}
.when{{color:#ff9800;font-size:11px}}
</style></head><body>
<h2>Not following you back — {len(rows)} reachable accounts</h2>
<p class="note">{removed} dead handles removed. {unchecked} still queued for the
existence check. Grouped by how well the data actually supports the label,
because "never followed back" is an absence claim and absence is easy to fake:
a rename, or a follow you made last week, both produce it.</p>

<h3 style="color:#ff9800">Followed you, then left — {len(dropped)}</h3>
<p class="note">Present in an archived followers list with Instagram's own
timestamp, absent from later ones. Instagram never logs who unfollows you, so
the departure is a window between exports, not a date.</p>
{table(dropped, "#ff9800", show_left=True)}

<h3 style="color:#66bb6a">Never followed back — confirmed — {len(conf)}</h3>
<p class="note">Handle unchanged in your following across all six snapshots, so
no rename explains it, and you have followed them over a year. Absent from every
followers list in that span.</p>
{table(conf, "#66bb6a")}

<h3 style="color:#4fc3f7">Never followed back — probable — {len(prob)}</h3>
<p class="note">Same rename-proof test, but you have followed them under a year.</p>
{table(prob, "#4fc3f7")}

<h3 style="color:#888">Too recent to judge — {len(recent)}</h3>
<p class="note">You followed them within the last 30 days. They may simply not
have gotten to it.</p>
{table(recent, "#888")}

<h3 style="color:#7e57c2">Cannot verify — {len(unver)}</h3>
<p class="note">Their handle was not continuously present in your following, so a
rename could account for their absence from the follower lists.</p>
{table(unver, "#7e57c2")}
</body></html>'''

    Path(args.out).write_text(doc)
    print(f"{len(rows)} reachable accounts")
    for label, grp in (("dropped you", dropped), ("never — confirmed", conf),
                       ("never — probable", prob), ("too recent", recent),
                       ("cannot verify", unver)):
        print(f"  {label:20s} {len(grp):5d}")
    print(f"{removed} dead handles filtered out; {unchecked} still unchecked")
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
