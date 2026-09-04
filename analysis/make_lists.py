#!/usr/bin/env python3
# Copyright (C) 2026 William Wang
# Licensed under the GNU AGPL v3 or later. See LICENSE.
# Run a modified version as a network service and you must offer users its source.
"""Write every bucket and tier out as plain text plus one clickable page."""

import argparse, csv, datetime, html as html_mod, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import igpaths
from follow_timeline import load_snapshot, export_generated_at

EXPORT_TIME = None    # export generation time, read from the export
HERE = None      # set from the config in main()

AGE_BUCKETS = [("under_7_days", 7, "under 7 days"),
               ("7_to_30_days", 30, "7–30 days"),
               ("1_to_3_months", 90, "1–3 months"),
               ("3_to_12_months", 365, "3–12 months"),
               ("1_to_3_years", 1095, "1–3 years"),
               ("over_3_years", 10**9, "over 3 years")]

TIER_META = [
    ("dropped", "Followed you, then left", "#ff9800",
     "Present in an archived followers list, absent from later ones."),
    ("confirmed", "Never followed back — confirmed", "#66bb6a",
     "Handle unchanged across all six snapshots, followed over a year."),
    ("probable", "Never followed back — probable", "#4fc3f7",
     "Same rename-proof test, followed under a year."),
    ("too_recent", "Too recent to judge", "#888",
     "You followed them within the last 30 days."),
    ("unverifiable", "Cannot verify", "#7e57c2",
     "Handle not continuously present; a rename could explain the absence."),
]


def load_checks(paths):
    seen = {}
    for p in paths:
        f = HERE / p
        if f.exists():
            for r in json.loads(f.read_text()).get("results", []):
                seen[r["username"]] = r
    return seen


def main():
    global HERE
    ap = argparse.ArgumentParser()
    igpaths.add_config_arg(ap)
    cfg = igpaths.load(ap.parse_args().config)
    HERE = cfg.work_dir
    global EXPORT_TIME
    EXPORT_TIME = export_generated_at(cfg.latest_zip)
    tl = json.loads((HERE / "follow_timeline.json").read_text())["not_following_back"]
    # Ghost tiers take precedence over the confidence tiers, the same way they
    # do in build_everyone. Without this the two reports disagree on how many
    # people "followed you, then left", because one counts empty shells there
    # and the other does not.
    ghost = {}
    gp = HERE / "ghosts" / "ghost_profiles.csv"
    if gp.exists():
        ghost = {r["username"]: r["tier"] for r in csv.DictReader(gp.open())}
    GHOSTED = {"empty_public", "empty_private", "near_empty", "low_signal", "bot_like"}
    fd = json.loads((HERE / "follow_dates.json").read_text())["following"]
    # "Present in every snapshot" fails anyone you simply started following
    # later, which has nothing to do with renames. The right question is whether
    # the handle stayed continuous since the follow began.
    cont = json.loads((HERE / "continuity_v2.json").read_text())["continuity"]
    checks = load_checks(["results.json", "blockcheck/results.json",
                          "blocksuspects/results.json", "fullgraph/results.json",
                          "tier1_enriched/results.json", "results_merged.json"])
    fo, fl = load_snapshot(cfg.latest_zip)
    nfb = sorted(fo - fl)

    recs = {}
    for u in nfb:
        r = checks.get(u)
        status = r["status"] if r else None
        on = fd.get(u)
        age = ((EXPORT_TIME - datetime.datetime.fromisoformat(on)).days
               if on else None)
        ever = tl[u]["ever_followed"]
        if status == "NOT_FOUND":
            tier = None                      # dead handle, excluded from tiers
        elif ghost.get(u) in GHOSTED:
            tier = None                      # empty or bot-shaped: graded elsewhere
        elif ever:
            tier = "dropped"
        elif age is not None and age <= 30:
            tier = "too_recent"
        elif cont.get(u) == "gap":
            tier = "unverifiable"
        elif age is not None and age > 365:
            tier = "confirmed"
        else:
            tier = "probable"
        bucket = None
        if not ever and age is not None:
            for key, lim, _ in AGE_BUCKETS:
                if age <= lim:
                    bucket = key
                    break
        recs[u] = {"username": u, "tier": tier, "age_bucket": bucket,
                   "ever_followed_you": ever, "left_between": tl[u]["left_between"],
                   "you_followed_on": on[:10] if on else None, "days_following": age,
                   "status": status or "unchecked",
                   "full_name": (r or {}).get("full_name") or "",
                   "posts": (r or {}).get("posts"),
                   "followers": (r or {}).get("followers")}

    out = HERE / "lists"
    out.mkdir(exist_ok=True)

    def dump(name, users):
        (out / f"{name}.txt").write_text("\n".join(users) + "\n" if users else "")
        return users

    sections = []
    for key, label, color, blurb in TIER_META:
        us = sorted([u for u, r in recs.items() if r["tier"] == key])
        dump(f"tier_{key}", us)
        sections.append((f"tier_{key}", label, color, blurb, us))

    for key, _, label in AGE_BUCKETS:
        us = sorted([u for u, r in recs.items() if r["age_bucket"] == key],
                    key=lambda u: -(recs[u]["days_following"] or 0))
        dump(f"age_{key}", us)
        sections.append((f"age_{key}",
                         f"Following for {label}", "#ffca28",
                         "Never appeared in any followers snapshot. "
                         "Ordered longest-followed first.", us))

    with (HERE / "not_following_back_master.csv").open("w", newline="") as f:
        cols = ["username", "tier", "age_bucket", "ever_followed_you", "left_between",
                "you_followed_on", "days_following", "status", "full_name",
                "posts", "followers"]
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for u in sorted(recs):
            w.writerow(recs[u])

    parts = ['''<html><head><title>Every List — Not Following Back</title><style>
body{font-family:monospace;font-size:13px;padding:24px;background:#111;color:#eee;max-width:1150px;margin:0 auto}
h2{color:#4fc3f7}h3{margin-top:16px;padding:8px 0;border-bottom:1px solid #333}
a{text-decoration:none}a:hover{text-decoration:underline;color:#fff}
.note{color:#888;line-height:1.6;margin:6px 0 14px}
details{margin-bottom:10px;border:1px solid #262626;border-radius:8px;padding:10px 14px;background:#151515}
summary{cursor:pointer;font-size:15px;outline:none}
summary::marker{color:#555}
table{border-collapse:collapse;width:100%;margin-top:10px}
td{padding:3px 10px 3px 0;border-bottom:1px solid #1e1e1e;vertical-align:top}
td.n{color:#555;text-align:right;width:52px}
td.nm{color:#bbb;font-size:12px;max-width:210px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
td.meta{color:#777;font-size:11px;white-space:nowrap}
.dead{color:#ff5252}.priv{color:#ffca28}
</style></head><body>
<h2>Every list, in full</h2>
<p class="note">Five confidence tiers over the accounts you follow that do not
follow you back, then the six follow-age buckets. Click a heading to expand.
Dead handles are excluded from the tiers but kept in the age buckets, marked in
red, so the bucket totals match what you were quoted. Same data as
<code>not_following_back_master.csv</code> and the text files in
<code>lists/</code>.</p>''']

    for name, label, color, blurb, users in sections:
        parts.append(f'<details><summary style="color:{color}">{html_mod.escape(label)} '
                     f'&mdash; {len(users)}</summary>')
        parts.append(f'<p class="note">{html_mod.escape(blurb)} '
                     f'&middot; <code>lists/{name}.txt</code></p><table>')
        for i, u in enumerate(users, 1):
            r = recs[u]
            eu = html_mod.escape(u)
            meta = []
            if r["you_followed_on"]:
                d = r["days_following"]
                span = f"{d/365:.1f}y" if d and d >= 365 else f"{d}d"
                meta.append(f'you followed {r["you_followed_on"]} · {span} ago')
            if r["left_between"] and r["ever_followed_you"]:
                meta.append(f'left {r["left_between"]}')
            cls = ""
            if r["status"] == "NOT_FOUND":
                cls = ' class="dead"'
                meta.append("handle gone")
            elif r["status"] == "EXISTS_PRIVATE":
                meta.append("private")
            parts.append(
                f'<tr><td class="n">{i}</td>'
                f'<td><a href="https://www.instagram.com/{eu}/" target="_blank"'
                f'{cls or f" style=color:{color}"}>{eu}</a></td>'
                f'<td class="nm">{html_mod.escape(r["full_name"])}</td>'
                f'<td class="meta">{html_mod.escape(" · ".join(meta))}</td></tr>')
        parts.append('</table></details>')
    parts.append('</body></html>')
    (HERE / "all_lists.html").write_text("\n".join(parts))

    print("TIERS")
    for key, label, _, _, us in sections[:5]:
        print(f"  {label:34s} {len(us):5d}   lists/{key}.txt")
    print("\nFOLLOW-AGE BUCKETS (never in any followers snapshot)")
    for name, label, _, _, us in sections[5:]:
        print(f"  {label:34s} {len(us):5d}   lists/{name}.txt")
    print(f"\nTotal in age buckets: {sum(len(s[4]) for s in sections[5:])}")
    print("Wrote all_lists.html, not_following_back_master.csv, lists/*.txt")


if __name__ == "__main__":
    main()
