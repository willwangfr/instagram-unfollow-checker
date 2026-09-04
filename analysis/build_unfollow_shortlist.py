#!/usr/bin/env python3
# Copyright (C) 2026 William Wang
# Licensed under the GNU AGPL v3 or later. See LICENSE.
# Run a modified version as a network service and you must offer users its source.
"""Ranked unfollow candidates, safest call first.

Only accounts you follow. Anyone the data cannot judge — a follow younger than
30 days, or a handle that may have been renamed — is deliberately left out.
"""

import argparse, csv, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import igpaths
from follow_timeline import load_snapshot, export_generated_at
import html as html_mod

HERE = None      # set from the config in main()


TRACKER = """
<div id="bar">Done: <b id="done">0</b> / <span id="tot">0</span>
  <label><input type="checkbox" id="hide" onchange="render()"> hide done</label>
  <button onclick="reset()">reset</button></div>
<style>
#bar{position:fixed;top:12px;right:20px;background:#1c1c1c;border:1px solid #333;
     border-radius:8px;padding:10px 14px;font-size:12px;z-index:99}
#bar b{color:#66bb6a;font-size:16px}
#bar label{margin-left:12px;color:#888}
#bar button{margin-left:10px;background:#2a2a2a;color:#888;border:1px solid #3a3a3a;
            border-radius:4px;padding:2px 8px;cursor:pointer}
a.done{color:#4a4a4a !important;text-decoration:line-through}
.hits{color:#ff9800;font-size:11px;margin-left:6px}
tr.hidden{display:none}
</style>
<script>
// Kept in this page's own localStorage: no account access, nothing leaves the
// browser. Clicking a name is what marks it, so the list doubles as a worklist.
const KEY='ig_unfollow_done';
// Stored per name as a click count. Earlier versions stored a timestamp, so a
// value big enough to be one is read as a single visit rather than shown as
// "1756094400x".
const asCount=v=>{const n=Number(v)||0;return n>1e10?1:n};
const get=()=>{try{return JSON.parse(localStorage.getItem(KEY))||{}}catch(e){return {}}};
const set=d=>localStorage.setItem(KEY,JSON.stringify(d));
function mark(el){const d=get();d[el.dataset.user]=asCount(d[el.dataset.user])+1;set(d);render();}
function reset(){if(confirm('Clear all click history on this page?')){set({});render();}}
function render(){
  const d=get(), hide=document.getElementById('hide').checked;
  let n=0;
  document.querySelectorAll('a[data-user]').forEach(a=>{
    const c=asCount(d[a.dataset.user]), done=c>0;
    a.classList.toggle('done',done);
    let tag=a.nextElementSibling;
    if(!tag||!tag.classList.contains('hits')){
      tag=document.createElement('span'); tag.className='hits';
      a.insertAdjacentElement('afterend',tag);
    }
    tag.textContent = c ? c+'\u00d7' : '';
    a.closest('tr').classList.toggle('hidden',done&&hide);
    if(done)n++;
  });
  document.getElementById('done').textContent=n;
  document.getElementById('tot').textContent=document.querySelectorAll('a[data-user]').length;
}
document.addEventListener('DOMContentLoaded',render);
</script>
"""


def main():
    global HERE
    ap = argparse.ArgumentParser()
    igpaths.add_config_arg(ap)
    cfg = igpaths.load(ap.parse_args().config)
    HERE = cfg.work_dir
    global NOW
    NOW = export_generated_at(cfg.latest_zip)
    following, followers = load_snapshot(cfg.latest_zip)
    v2 = json.loads((HERE / "continuity_v2.json").read_text())
    cont, everf = v2["continuity"], v2["ever_followed"]
    tl = json.loads((HERE / "follow_timeline.json").read_text())["not_following_back"]
    # Ghost tiers come from the profile checker, which is optional: the export
    # analysis has to stand on its own without ever touching Instagram.
    gp = HERE / "ghosts/ghost_profiles.csv"
    ghost = ({r["username"]: r["tier"] for r in csv.DictReader(gp.open())}
             if gp.exists() else {})
    if not ghost:
        print("  (no ghost grading found — run the profile checker and "
              "ghost_grade.py to separate dead accounts from live ones)")
    stable = set(json.loads((HERE / "handle_stability.json").read_text())["stable_handles"])
    dates = json.loads((HERE / "follow_dates.json").read_text())["following"]
    bp = HERE / "bios.json"
    bios = ({p["username"]: p
             for p in json.loads(bp.read_text())["profiles"]} if bp.exists() else {})
    import datetime

    def age(u):
        d = dates.get(u)
        return (NOW - datetime.datetime.fromisoformat(d)).days if d else None

    groups = {k: [] for k in ("dead", "empty", "stale", "dropped", "probable")}
    excluded = {"too_recent": [], "renamed": []}
    for u in sorted(tl):
        g, a = ghost.get(u), age(u)
        if g == "deleted":
            groups["dead"].append(u)
        elif g in ("empty_public", "empty_private", "near_empty", "low_signal",
                   "bot_like"):
            groups["empty"].append(u)
        elif everf.get(u):
            groups["dropped"].append(u)
        elif a is not None and a <= 30:
            excluded["too_recent"].append(u)
        elif cont.get(u) != "continuous":
            # The handle vanished from your following after you followed them
            # and came back, so it changed. Their absence from your followers
            # may be that rename rather than any choice of theirs.
            excluded["renamed"].append(u)
        elif a is not None and a > 365:
            groups["stale"].append(u)
        else:
            groups["probable"].append(u)

    for k in groups:
        groups[k].sort(key=lambda u: -(age(u) or 0))

    SECTIONS = [
        ("dead", "Gone — unfollow costs you nothing", "#ff5252",
         "The profile no longer resolves. Deleted, deactivated or renamed. "
         "These are pure dead weight in your following count."),
        ("empty", "Empty shells", "#ff7043",
         "The account exists but has never posted, or has almost nothing and "
         "no audience. Not following you back either."),
        ("stale", "Never followed back, over a year", "#ffca28",
         "Handle unchanged across every snapshot since February, so no rename "
         "explains it. You have followed them more than a year and they have "
         "never once appeared in your followers list. Longest first."),
        ("dropped", "They followed you, then left", "#ff9800",
         "They did follow you at one point and stopped. Still active accounts. "
         "Your call whether that matters."),
        ("probable", "Never followed back, under a year", "#4fc3f7",
         "Same rename-proof test, but a shorter run. Weaker case."),
    ]

    total = sum(len(groups[k]) for k, *_ in SECTIONS)
    parts = [f'''<html><head><title>Unfollow Shortlist ({total})</title><style>
body{{font-family:monospace;font-size:13px;padding:24px;background:#111;color:#eee;max-width:1100px;margin:0 auto}}
h2{{color:#4fc3f7}}
a{{text-decoration:none}}a:hover{{text-decoration:underline;color:#fff}}
.note{{color:#888;line-height:1.6;margin:6px 0 14px}}
details{{margin-bottom:12px;border:1px solid #262626;border-radius:8px;padding:12px 16px;background:#151515}}
summary{{cursor:pointer;font-size:15px}}
table{{border-collapse:collapse;width:100%;margin-top:10px}}
td{{padding:3px 10px 3px 0;border-bottom:1px solid #1e1e1e;vertical-align:top}}
td.n{{color:#555;text-align:right;width:52px}}
td.nm{{color:#bbb;font-size:12px;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
td.meta{{color:#777;font-size:11px;white-space:nowrap}}
.excl{{background:#151515;border:1px solid #262626;border-radius:8px;padding:12px 16px;color:#888;line-height:1.7}}
</style></head><body>
<h2>Unfollow shortlist &mdash; {total} candidates</h2>
<p class="note">Ranked by how confidently the data supports the call. Click a
heading to expand; every name opens the profile. Nothing here is acted on
automatically &mdash; this is a list, not a queue.</p>''']
    parts.append(TRACKER)

    for key, label, color, blurb in SECTIONS:
        us = groups[key]
        parts.append(f'<details><summary style="color:{color}">{html_mod.escape(label)} '
                     f'&mdash; {len(us)}</summary><p class="note">{html_mod.escape(blurb)}</p><table>')
        for i, u in enumerate(us, 1):
            b = bios.get(u, {})
            a = age(u)
            meta = []
            if a:
                meta.append(f"followed {a/365:.1f}y ago" if a >= 365 else f"followed {a}d ago")
            if b.get("posts") is not None:
                meta.append(f'{b["posts"]} posts')
            if b.get("followers"):
                meta.append(f'{b["followers"]} followers')
            if tl[u]["ever_followed"] and tl[u]["left_between"]:
                meta.append(f'left {tl[u]["left_between"]}')
            eu = html_mod.escape(u)
            parts.append(
                f'<tr><td class="n">{i}</td>'
                f'<td><a href="https://www.instagram.com/{eu}/" target="_blank" '
                f'data-user="{eu}" onclick="mark(this)" '
                f'style="color:{color}">{eu}</a></td>'
                f'<td class="nm">{html_mod.escape(b.get("full_name") or "")}</td>'
                f'<td class="meta">{html_mod.escape(" · ".join(meta))}</td></tr>')
        parts.append('</table></details>')
        (HERE / "lists").mkdir(parents=True, exist_ok=True)
        (HERE / "lists" / f"unfollow_{key}.txt").write_text("\n".join(us) + "\n" if us else "")

    (HERE / "lists").mkdir(parents=True, exist_ok=True)
    for k in excluded:
        excluded[k].sort(key=lambda u: -(age(u) or 0))
        (HERE / "lists" / f"excluded_{k}.txt").write_text(
            "\n".join(excluded[k]) + "\n" if excluded[k] else "")

    def excl_table(us, color):
        rows = ['<table>']
        for i, u in enumerate(us, 1):
            b = bios.get(u, {})
            a = age(u)
            meta = [f"followed {a}d ago" if a and a < 365 else f"followed {a/365:.1f}y ago"]
            if b.get("posts") is not None:
                meta.append(f'{b["posts"]} posts')
            if b.get("followers"):
                meta.append(f'{b["followers"]} followers')
            eu = html_mod.escape(u)
            rows.append(f'<tr><td class="n">{i}</td>'
                        f'<td><a href="https://www.instagram.com/{eu}/" target="_blank" '
                        f'style="color:{color}">{eu}</a></td>'
                        f'<td class="nm">{html_mod.escape(b.get("full_name") or "")}</td>'
                        f'<td class="meta">{html_mod.escape(" · ".join(meta))}</td></tr>')
        return "\n".join(rows) + '</table>'

    parts.append(f'''<h3 style="color:#7e57c2">Deliberately left off &mdash; {sum(len(v) for v in excluded.values())}</h3>
<details><summary style="color:#888">Too recent to judge &mdash; {len(excluded["too_recent"])}</summary>
<p class="note">You started following them within the last 30 days. They have not
had a fair chance to follow back. Newest follows last.</p>
{excl_table(excluded["too_recent"], "#888")}</details>
<details><summary style="color:#7e57c2">Handle changed since you followed them &mdash; {len(excluded["renamed"])}</summary>
<p class="note">The handle disappeared from your following list at some point
after you followed them and later returned, which means it was renamed. Their
absence from your followers list may be that rename rather than a decision.
Checked against nine complete snapshots from 2024-10-12 onward.</p>
{excl_table(excluded["renamed"], "#7e57c2")}</details>
<p class="note">Also left off: all {len(followers & following)} mutuals and the
{len(followers - following)} people who follow you that you do not follow back.</p>
</body></html>''')

    out = HERE / "unfollow_shortlist.html"
    out.write_text("\n".join(parts))
    print(f"{total} candidates")
    for key, label, *_ in SECTIONS:
        print(f"   {label:42s} {len(groups[key]):5d}")
    print(f"\nleft off: {len(excluded['too_recent'])} too recent, "
          f"{len(excluded['renamed'])} renamed")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
