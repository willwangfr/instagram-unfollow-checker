#!/usr/bin/env python3
"""One row for every person in the graph. Nothing filtered, nothing hidden.

Merges the nine complete snapshots, Instagram's own follow timestamps, every
browser check run so far, the ghost grading, and the shortlist classification.
"""

import argparse, csv, datetime, html as html_mod, json, os, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import igpaths
from follow_timeline import load_snapshot, export_generated_at

HERE = None      # set from the config in main()
NOW = None       # export generation time, read from the export
SPAM_KW = re.compile(r"academic|essay|assignment|homework|thesis|writer|dissertation"
                     r"|coursework|exam|tutor|grade|paper", re.I)


def num(v):
    """Display counts are strings: '1,457', '230K', '2.7M'. Sorting needs ints."""
    if v in (None, ""):
        return None
    m = re.fullmatch(r"([\d.]+)\s*([KMB])?", str(v).strip().replace(",", ""))
    if not m:
        return None
    n = float(m.group(1))
    return int(n * {"K": 1e3, "M": 1e6, "B": 1e9}[m.group(2)]) if m.group(2) else int(n)


NAME_PAIR = re.compile(
    r'<td[^>]*>Name</td>\s*<td[^>]*>([^<]*)</td>\s*</tr>\s*<tr>\s*'
    r'<td[^>]*>Username</td>\s*<td[^>]*>([A-Za-z0-9_.]+)</td>', re.I)


def main():
    global HERE, NOW
    ap = argparse.ArgumentParser()
    igpaths.add_config_arg(ap)
    cfg = igpaths.load(ap.parse_args().config)
    HERE, NOW = cfg.work_dir, export_generated_at(cfg.latest_zip)
    S = {d: load_snapshot(p) for d, p in cfg.snapshots}
    dates = [d for d, _ in cfg.snapshots]
    SD = {d: datetime.datetime.strptime(d, "%Y-%m-%d") for d in dates}
    fo, fl = S["2026-08-31"]
    graph = sorted(fo | fl)

    fdates = json.loads((HERE / "follow_dates.json").read_text())
    fo_on, fl_on = fdates["following"], fdates["followers"]

    checks = {}
    for f in ("results.json", "blockcheck/results.json", "blocksuspects/results.json",
              "fullgraph/results.json"):
        p = HERE / f
        if p.exists():
            for r in json.loads(p.read_text()).get("results", []):
                # A later, richer run wins: it carries the private flag.
                if r["username"] not in checks or r.get("verified") is not None:
                    checks[r["username"]] = r

    ghost = {}
    gp = HERE / "ghosts/ghost_profiles.csv"
    if gp.exists():
        ghost = {r["username"]: r["tier"] for r in csv.DictReader(gp.open())}

    dm = {}
    dmp = HERE / "dm_index.json"
    if dmp.exists():
        for d in json.loads(dmp.read_text()):
            if d["matched_username"]:
                cur = dm.get(d["matched_username"])
                if not cur or d["messages"] > cur["messages"]:
                    dm[d["matched_username"]] = d

    names = {}
    for fn in sorted(os.listdir(cfg.connections_dir)):
        if fn.endswith(".html"):
            for nm, u in NAME_PAIR.findall(
                    Path(cfg.connections_dir, fn).read_text(encoding="utf-8")):
                if nm.strip():
                    names.setdefault(u, nm.strip())

    rows = []
    for u in graph:
        in_fo, in_fl = u in fo, u in fl
        rel = ("mutual" if in_fo and in_fl
               else "you_follow_only" if in_fo else "they_follow_only")
        seen_fl = [d for d in dates if u in S[d][1]]
        ever = bool(seen_fl)
        left = None
        if ever and not in_fl:
            i = dates.index(seen_fl[-1])
            left = f"{seen_fl[-1]} .. {dates[i+1]}" if i + 1 < len(dates) else None
        fd = fo_on.get(u)
        age = (NOW - datetime.datetime.fromisoformat(fd)).days if fd else None
        cont = ""
        if fd:
            exp = [d for d in dates if SD[d] >= datetime.datetime.fromisoformat(fd)]
            cont = ("continuous" if exp and all(u in S[d][0] for d in exp)
                    else "gap" if exp else "followed_after_last_snapshot")
        r = checks.get(u, {})
        status = r.get("status", "unchecked")
        g = ghost.get(u, "")
        if rel != "you_follow_only":
            action = "not a candidate — " + ("mutual" if rel == "mutual" else "fan")
        elif g == "deleted" or status == "NOT_FOUND":
            action = "unfollow: gone"
        elif g in ("empty_public", "near_empty", "low_signal"):
            action = "unfollow: empty shell"
        elif ever:
            action = "unfollow: followed you then left"
        elif age is not None and age <= 30:
            action = "excluded: too recent"
        elif cont == "gap":
            action = "excluded: handle renamed"
        elif age is not None and age > 365:
            action = "unfollow: never followed back, over a year"
        else:
            action = "unfollow: never followed back, under a year"

        fl_n, fo_n = num(r.get("followers")), num(r.get("following"))
        ratio = round(fo_n / max(fl_n, 1), 1) if (fo_n is not None and fl_n is not None) else ""
        name_for_kw = r.get("full_name") or names.get(u) or ""
        spam = bool(fo_n and fo_n >= 1000 and isinstance(ratio, float) and ratio >= 3
                    and (SPAM_KW.search(u) or SPAM_KW.search(name_for_kw) or ratio >= 10))
        rows.append({
            "username": u, "profile_url": f"https://www.instagram.com/{u}/",
            "relationship": rel, "verdict": action,
            "you_followed_on": (fd or "")[:10],
            "days_you_have_followed": age if age is not None else "",
            "they_followed_you_on": (fl_on.get(u) or "")[:10],
            "currently_follows_you": in_fl, "you_currently_follow": in_fo,
            "ever_followed_you": ever, "left_between": left or "",
            "handle_continuity": cont, "profile_status": status,
            "ghost_tier": g,
            "full_name": r.get("full_name") or names.get(u) or "",
            "bio": (r.get("bio") or "").replace("\n", " / "),
            "external_link": r.get("external_link") or "",
            "followers": r.get("followers") or "", "following": r.get("following") or "",
            "posts": r.get("posts") if r.get("posts") is not None else "",
            "verified": r.get("verified") if r.get("verified") is not None else "",
            "profile_pic": r.get("profile_pic") or "",
            "followers_n": fl_n if fl_n is not None else "",
            "following_n": fo_n if fo_n is not None else "",
            "posts_n": num(r.get("posts")) if num(r.get("posts")) is not None else "",
            "following_to_follower_ratio": ratio,
            "likely_spam": spam,
            "dm_messages": dm.get(u, {}).get("messages", ""),
            "dm_last": dm.get(u, {}).get("last_message", ""),
            "dm_from_you": dm.get(u, {}).get("from_you", ""),
            "dm_from_them": dm.get(u, {}).get("from_them", ""),
            "dm_unanswered": (not dm[u]["you_spoke_last"]) if u in dm else "",
        })

    cols = list(rows[0].keys())
    with (HERE / "everyone.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)
    (HERE / "everyone.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False))

    # Searchable page. The table is built client-side from embedded JSON so the
    # file stays a few MB rather than tens.
    slim = [[r["username"], r["relationship"], r["verdict"], r["full_name"],
             r["you_followed_on"], r["they_followed_you_on"], r["profile_status"],
             r["posts_n"] if r["posts_n"] != "" else None,
             r["followers_n"] if r["followers_n"] != "" else None,
             r["following_n"] if r["following_n"] != "" else None,
             r["following_to_follower_ratio"] if r["following_to_follower_ratio"] != "" else None,
             r["days_you_have_followed"] if r["days_you_have_followed"] != "" else None,
             r["left_between"], r["bio"][:160], bool(r["likely_spam"]),
             r["dm_messages"] if r["dm_messages"] != "" else None,
             r["dm_last"] or None, bool(r["dm_unanswered"]) if r["dm_unanswered"] != "" else False]
            for r in rows]
    (HERE / "everyone.html").write_text(PAGE.replace("__DATA__", json.dumps(slim, ensure_ascii=False)))

    from collections import Counter
    print(f"{len(rows)} people\n")
    for k, n in Counter(r["relationship"] for r in rows).most_common():
        print(f"  {k:20s} {n:5d}")
    print()
    for k, n in Counter(r["verdict"] for r in rows).most_common():
        print(f"  {k:46s} {n:5d}")
    print(f"\n  profile checked: {sum(1 for r in rows if r['profile_status']!='unchecked')}"
          f" / {len(rows)}")
    print("Wrote everyone.csv, everyone.json, everyone.html")


PAGE = """<html><head><title>Everyone — 9,586</title><style>
body{font-family:monospace;font-size:13px;padding:18px;background:#111;color:#eee;margin:0}
h2{color:#4fc3f7;margin:0 0 4px}
.note{color:#888;margin:0 0 12px;line-height:1.6}
#ctl{position:sticky;top:0;background:#111;padding:10px 0;border-bottom:1px solid #262626;z-index:5}
input,select{background:#1c1c1c;color:#eee;border:1px solid #333;border-radius:6px;padding:6px 10px;font-family:monospace;font-size:13px}
input#q{width:250px}
button{background:#2a2a2a;color:#888;border:1px solid #3a3a3a;border-radius:4px;padding:5px 10px;cursor:pointer;font-family:monospace}
#count{color:#66bb6a;margin-left:10px}#donec{color:#66bb6a;margin-left:10px}
table{border-collapse:collapse;width:100%;margin-top:10px}
th{text-align:left;color:#777;font-weight:normal;border-bottom:1px solid #333;padding:6px 8px 6px 0;
   position:sticky;top:52px;background:#111;cursor:pointer;user-select:none;white-space:nowrap}
th:hover{color:#fff}
th .ar{color:#4fc3f7}
td{padding:3px 8px 3px 0;border-bottom:1px solid #1a1a1a;vertical-align:top}
td.nm{color:#bbb;max-width:150px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
td.bio{color:#666;max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
td.meta{color:#777;white-space:nowrap}
td.n{text-align:right;color:#9ccc65;white-space:nowrap}
a{color:#4fc3f7;text-decoration:none}a:hover{color:#fff;text-decoration:underline}
a.done{color:#4a4a4a !important;text-decoration:line-through}
.mutual{color:#ab47bc}.you_follow_only{color:#4fc3f7}.they_follow_only{color:#66bb6a}
.gone{color:#ff5252}.priv{color:#ffca28}.spam{color:#ff7043}
</style></head><body>
<h2>Everyone &mdash; every account you follow or that follows you</h2>
<p class="note">Nothing filtered out. Click any column heading to sort; click again to reverse.
Counts are parsed to numbers, so sorting is numeric, not alphabetical.
"unchecked" means the full-graph scrape has not reached them yet.</p>
<div id="ctl">
  <input id="q" placeholder="search username, name, bio..." oninput="render()">
  <select id="rel" onchange="render()"><option value="">all relationships</option>
    <option value="mutual">mutual</option>
    <option value="you_follow_only">you follow, they don't</option>
    <option value="they_follow_only">they follow, you don't</option></select>
  <select id="v" onchange="render()"><option value="">all verdicts</option></select>
  <label style="color:#888"><input type="checkbox" id="spam" onchange="render()"> spam only</label>
  <label style="color:#888"><input type="checkbox" id="dm" onchange="render()"> DMs only</label>
  <label style="color:#888"><input type="checkbox" id="un" onchange="render()"> unanswered</label>
  <label style="color:#888"><input type="checkbox" id="hide" onchange="render()"> hide done</label>
  <button onclick="reset()">reset clicks</button>
  <span id="count"></span><span id="donec"></span>
</div>
<table><thead><tr id="hr"></tr></thead><tbody id="b"></tbody></table>
<script>
const DATA = __DATA__;
// col: [label, index, type]
const COLS = [["username",0,"s"],["name",3,"s"],["relationship",1,"s"],["verdict",2,"s"],
  ["posts",7,"n"],["followers",8,"n"],["following",9,"n"],["fo/fl ratio",10,"n"],
  ["days followed",11,"n"],["DMs",15,"n"],["last DM",16,"s"],
  ["you followed",4,"s"],["they followed",5,"s"],
  ["status",6,"s"],["left between",12,"s"],["bio",13,"s"]];
let sortCol = 8, sortDir = -1;   // default: most followers first
const KEY='ig_unfollow_done';
const getDone=()=>{try{return JSON.parse(localStorage.getItem(KEY))||{}}catch(e){return {}}};
const setDone=d=>localStorage.setItem(KEY,JSON.stringify(d));
function mark(el){const d=getDone();d[el.dataset.user]=Date.now();setDone(d);render();}
function reset(){if(confirm('Clear click history shared with the shortlist page?')){setDone({});render();}}
const esc = s => (s===null||s===undefined?"":String(s)).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const fmt = v => v===null||v===undefined ? '<span style="color:#444">—</span>' : Number(v).toLocaleString();
const vs=[...new Set(DATA.map(r=>r[2]))].sort();
document.getElementById('v').innerHTML += vs.map(v=>`<option value="${esc(v)}">${esc(v)}</option>`).join('');
function sortBy(i){ if(sortCol===i){sortDir=-sortDir;} else {sortCol=i;sortDir=(COLS.find(c=>c[1]===i)[2]==='n')?-1:1;} render(); }
function header(){
  document.getElementById('hr').innerHTML = '<th>#</th>' + COLS.map(([lab,i])=>
    `<th onclick="sortBy(${i})">${lab}${sortCol===i?` <span class="ar">${sortDir>0?'\u2191':'\u2193'}</span>`:''}</th>`).join('');
}
function render(){
  const q=document.getElementById('q').value.toLowerCase();
  const rel=document.getElementById('rel').value, vv=document.getElementById('v').value;
  const spamOnly=document.getElementById('spam').checked;
  const done=getDone(), hideDone=document.getElementById('hide').checked;
  let list=DATA.filter(r=>{
    if(rel && r[1]!==rel) return false;
    if(vv && r[2]!==vv) return false;
    if(spamOnly && !r[14]) return false;
    if(document.getElementById('dm').checked && !r[15]) return false;
    if(document.getElementById('un').checked && !r[17]) return false;
    if(hideDone && done[r[0]]) return false;
    if(q && !(r[0].toLowerCase().includes(q)||(r[3]||'').toLowerCase().includes(q)
        ||(r[13]||'').toLowerCase().includes(q)||r[2].toLowerCase().includes(q))) return false;
    return true;});
  const type=(COLS.find(c=>c[1]===sortCol)||[])[2]||'s';
  list.sort((a,b)=>{
    let x=a[sortCol], y=b[sortCol];
    // nulls always sink, whichever way the column is sorted
    if(x===null||x===undefined||x==='') return 1;
    if(y===null||y===undefined||y==='') return -1;
    if(type==='n') return (x-y)*sortDir;
    return String(x).localeCompare(String(y))*sortDir;});
  const out=[];
  list.slice(0,3000).forEach((r,n)=>{
    const st=r[6]==='NOT_FOUND'?'<span class="gone">gone</span>'
            :r[6]==='EXISTS_PRIVATE'?'<span class="priv">private</span>'
            :r[6]==='EXISTS'?'public':'<span style="color:#555">unchecked</span>';
    out.push(`<tr><td style="color:#555">${n+1}</td>`
      +`<td><a href="https://www.instagram.com/${esc(r[0])}/" target="_blank" `
      +`data-user="${esc(r[0])}" onclick="mark(this)" class="${done[r[0]]?'done':''}">${esc(r[0])}</a>`
      +`${r[14]?' <span class="spam">spam?</span>':''}</td>`
      +`<td class="nm">${esc(r[3])}</td><td class="${r[1]}">${r[1].replace(/_/g,' ')}</td>`
      +`<td class="meta">${esc(r[2])}</td>`
      +`<td class="n">${fmt(r[7])}</td><td class="n">${fmt(r[8])}</td><td class="n">${fmt(r[9])}</td>`
      +`<td class="n">${r[10]===null?'<span style="color:#444">—</span>':r[10]}</td>`
      +`<td class="n">${fmt(r[11])}</td>`
      +`<td class="n">${fmt(r[15])}</td>`
      +`<td class="meta">${esc(r[16])}${r[17]?' <span style="color:#ff9800">unanswered</span>':''}</td>`
      +`<td class="meta">${esc(r[4])}</td><td class="meta">${esc(r[5])}</td>`
      +`<td class="meta">${st}</td><td class="meta">${esc(r[12])}</td>`
      +`<td class="bio">${esc(r[13])}</td></tr>`);});
  document.getElementById('b').innerHTML=out.join('');
  document.getElementById('count').textContent =
    list.length + ' of ' + DATA.length + (list.length>3000?' (showing first 3000)':'');
  document.getElementById('donec').textContent = Object.keys(done).length + ' clicked';
  header();
}
header(); render();
</script></body></html>"""


if __name__ == "__main__":
    main()
