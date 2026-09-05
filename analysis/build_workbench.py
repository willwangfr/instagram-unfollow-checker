#!/usr/bin/env python3
# Copyright (C) 2026 William Wang
# Licensed under the GNU AGPL v3 or later. See LICENSE.
"""A spreadsheet over everyone.csv: reorder, resize, hide, sort, filter, expand.

The reporting pages answer fixed questions. This one answers whatever question
you have: every column is present, you arrange them how you like, and the
layout is remembered. Long values are clipped for the row height, never for the
data — clicking a row shows every field in full.
"""

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import igpaths

NUMERIC = {"days_you_have_followed", "followers_n", "following_n", "posts_n",
           "following_to_follower_ratio", "dm_messages", "dm_from_you",
           "dm_from_them", "group_connections", "times_checked"}
BOOL = {"currently_follows_you", "you_currently_follow", "ever_followed_you",
        "verified", "likely_spam", "dm_unanswered"}
# Shown by default; everything else starts hidden but is one click away.
DEFAULT_ON = ["username", "full_name", "relationship", "verdict", "followers_n",
              "following_n", "posts_n", "dm_messages", "dm_last",
              "group_connections", "you_followed_on", "bio"]


def main():
    ap = argparse.ArgumentParser()
    igpaths.add_config_arg(ap)
    ap.add_argument("--out", default="workbench.html")
    args = ap.parse_args()
    cfg = igpaths.load(args.config)

    src = cfg.work_dir / "everyone.csv"
    rows = list(csv.DictReader(src.open()))
    cols = list(rows[0].keys())

    def cell(k, v):
        if k in NUMERIC:
            try:
                return float(v) if "." in v else int(v)
            except (ValueError, TypeError):
                return None
        if k in BOOL:
            return v == "True"
        return v

    data = [[cell(k, r[k]) for k in cols] for r in rows]
    meta = [{"key": k,
             "type": "n" if k in NUMERIC else "b" if k in BOOL else "s",
             "on": k in DEFAULT_ON} for k in cols]

    html = PAGE.replace("__COLS__", json.dumps(meta)) \
               .replace("__DATA__", json.dumps(data, ensure_ascii=False)) \
               .replace("__N__", str(len(rows)))
    out = cfg.work_dir / args.out
    out.write_text(html)
    print(f"{len(rows)} rows x {len(cols)} columns -> {out}")


PAGE = r"""<!doctype html>
<meta charset="utf-8">
<title>Workbench</title>
<style>
:root{--bg:#111;--fg:#eee;--dim:#888;--line:#2a2a2a;--accent:#4fc3f7}
*{box-sizing:border-box}
body{font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;background:var(--bg);color:var(--fg);margin:0;padding:14px}
h1{font-size:17px;margin:0 0 8px}
.bar{display:flex;flex-wrap:wrap;gap:7px;align-items:center;margin-bottom:9px}
input,select,button{background:#1c1c1c;color:var(--fg);border:1px solid #333;border-radius:6px;padding:5px 9px;font:inherit}
input#q{min-width:230px}
button{cursor:pointer}button:hover{border-color:#555;color:#fff}
#count{color:#66bb6a}
#cols{position:relative}
#colmenu{display:none;position:absolute;z-index:30;background:#161616;border:1px solid #333;border-radius:8px;
  padding:8px;max-height:60vh;overflow:auto;min-width:250px;box-shadow:0 8px 26px #000a}
#colmenu.open{display:block}
#colmenu label{display:block;padding:3px 5px;color:#bbb;cursor:pointer;white-space:nowrap}
#colmenu label:hover{background:#202020;color:#fff}
.wrap{overflow:auto;max-height:74vh;border:1px solid var(--line);border-radius:8px}
table{border-collapse:separate;border-spacing:0;width:max-content;min-width:100%}
th{position:sticky;top:0;z-index:2;background:#181818;color:var(--dim);font-weight:400;text-align:left;
  padding:7px 9px;border-bottom:1px solid #333;border-right:1px solid #222;white-space:nowrap;
  cursor:grab;user-select:none;position:relative}
th:hover{color:#fff}
th.drag{opacity:.4}
th.over{border-left:2px solid var(--accent)}
th .grip{position:absolute;right:0;top:0;height:100%;width:6px;cursor:col-resize}
td{padding:4px 9px;border-bottom:1px solid #191919;border-right:1px solid #191919;
  max-width:var(--w,320px);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;vertical-align:top}
td.n{text-align:right;color:#9ccc65}
tr:hover td{background:#151515}
tr.sel td{background:#17242b}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline;color:#fff}
a.done{color:#555 !important;text-decoration:line-through}
.hits{color:#ff9800;font-size:11px;margin-left:5px}
#detail{margin-top:10px;border:1px solid var(--line);border-radius:8px;padding:12px 14px;background:#151515;display:none}
#detail.open{display:block}
#detail h3{margin:0 0 8px;font-size:14px;color:var(--accent)}
#detail dl{display:grid;grid-template-columns:max-content 1fr;gap:3px 16px;margin:0}
#detail dt{color:var(--dim)}
#detail dd{margin:0;white-space:pre-wrap;word-break:break-word}
.note{color:var(--dim);margin:0 0 10px}
</style>

<h1>Workbench &mdash; <span id="count"></span></h1>
<p class="note">Drag a column heading to move it, drag its right edge to resize, click it to sort.
Click any row to see every field in full. Layout is remembered in this browser.</p>

<div class="bar">
  <input id="q" placeholder="search anything…">
  <span id="cols"><button id="colbtn">Columns ▾</button><div id="colmenu"></div></span>
  <button id="csv">Export view as CSV</button>
  <button id="resetlayout">Reset layout</button>
  <button id="resetclicks">Reset clicks</button>
</div>

<div class="wrap"><table><thead><tr id="hr"></tr></thead><tbody id="tb"></tbody></table></div>
<div id="detail"></div>

<script>
const COLS = __COLS__, DATA = __DATA__, TOTAL = __N__;
const IDX = Object.fromEntries(COLS.map((c,i)=>[c.key,i]));
const LKEY='ig_workbench_layout', CKEY='ig_unfollow_done';
const load=(k,d)=>{try{return JSON.parse(localStorage.getItem(k))??d}catch(e){return d}};
const save=(k,v)=>{try{localStorage.setItem(k,JSON.stringify(v))}catch(e){}};
const asCount=v=>{const n=Number(v)||0;return n>1e10?1:n};

let L = load(LKEY, null) || {order:COLS.map(c=>c.key), on:COLS.filter(c=>c.on).map(c=>c.key), w:{}, sort:{k:'followers_n',d:-1}};
// A column added since the layout was saved must still appear in the order.
for(const c of COLS) if(!L.order.includes(c.key)) L.order.push(c.key);
const esc=s=>(s===null||s===undefined?'':String(s)).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const type=k=>(COLS.find(c=>c.key===k)||{}).type;
const shown=()=>L.order.filter(k=>L.on.includes(k));

function header(){
  const hr=document.getElementById('hr');
  hr.innerHTML = shown().map(k=>{
    const s = L.sort.k===k ? (L.sort.d>0?' ↑':' ↓') : '';
    const w = L.w[k] ? `style="--w:${L.w[k]}px;width:${L.w[k]}px"` : '';
    return `<th draggable="true" data-k="${k}" ${w}>${k.replace(/_/g,' ')}${s}<span class="grip"></span></th>`;
  }).join('');
  hr.querySelectorAll('th').forEach(th=>{
    const k=th.dataset.k;
    th.onclick=e=>{ if(e.target.classList.contains('grip'))return;
      L.sort = L.sort.k===k ? {k,d:-L.sort.d} : {k,d:type(k)==='n'?-1:1}; save(LKEY,L); render(); };
    th.ondragstart=e=>{e.dataTransfer.setData('text/plain',k);th.classList.add('drag')};
    th.ondragend=()=>th.classList.remove('drag');
    th.ondragover=e=>{e.preventDefault();th.classList.add('over')};
    th.ondragleave=()=>th.classList.remove('over');
    th.ondrop=e=>{e.preventDefault();th.classList.remove('over');
      const from=e.dataTransfer.getData('text/plain'); if(from===k)return;
      L.order = L.order.filter(x=>x!==from);
      L.order.splice(L.order.indexOf(k),0,from);
      save(LKEY,L); render();};
    const grip=th.querySelector('.grip');
    grip.onmousedown=e=>{e.preventDefault();e.stopPropagation();
      const x0=e.clientX, w0=th.offsetWidth;
      const mv=ev=>{const w=Math.max(60,w0+ev.clientX-x0); L.w[k]=w; th.style.width=w+'px'; th.style.setProperty('--w',w+'px');};
      const up=()=>{document.removeEventListener('mousemove',mv);document.removeEventListener('mouseup',up);save(LKEY,L);render();};
      document.addEventListener('mousemove',mv);document.addEventListener('mouseup',up);};
  });
}

function colMenu(){
  document.getElementById('colmenu').innerHTML = L.order.map(k=>
    `<label><input type="checkbox" data-k="${k}" ${L.on.includes(k)?'checked':''}> ${k.replace(/_/g,' ')}</label>`).join('');
  document.querySelectorAll('#colmenu input').forEach(cb=>cb.onchange=()=>{
    const k=cb.dataset.k;
    L.on = cb.checked ? [...L.on,k] : L.on.filter(x=>x!==k);
    save(LKEY,L); render();});
}

let VIEW=[];
function render(){
  const q=document.getElementById('q').value.toLowerCase();
  const done=load(CKEY,{});
  VIEW = q ? DATA.filter(r=>r.some(v=>v!==null&&String(v).toLowerCase().includes(q))) : DATA.slice();
  const si=IDX[L.sort.k], t=type(L.sort.k);
  VIEW.sort((a,b)=>{
    let x=a[si], y=b[si];
    if(x===null||x===undefined||x==='')return 1;
    if(y===null||y===undefined||y==='')return -1;
    return (t==='n'?x-y:String(x).localeCompare(String(y)))*L.sort.d;});
  const ks=shown();
  document.getElementById('tb').innerHTML = VIEW.slice(0,1500).map((r,i)=>
    '<tr data-i="'+i+'">' + ks.map(k=>{
      const v=r[IDX[k]], w=L.w[k]?`style="--w:${L.w[k]}px"`:'';
      if(k==='username'){
        const c=asCount(done[v]);
        return `<td ${w}><a href="https://www.instagram.com/${esc(v)}/" target="_blank" rel="noopener" data-u="${esc(v)}" class="${c?'done':''}">${esc(v)}</a>${c?`<span class="hits">${c}×</span>`:''}</td>`;
      }
      if(type(k)==='n') return `<td class="n" ${w}>${v===null?'':Number(v).toLocaleString()}</td>`;
      if(type(k)==='b') return `<td ${w}>${v?'yes':''}</td>`;
      return `<td ${w} title="${esc(v)}">${esc(v)}</td>`;
    }).join('') + '</tr>').join('');
  document.getElementById('count').textContent =
    `${VIEW.length.toLocaleString()} of ${TOTAL.toLocaleString()} rows` + (VIEW.length>1500?' (showing 1500)':'');
  header(); colMenu();
}

document.getElementById('tb').addEventListener('click',e=>{
  const a=e.target.closest('a[data-u]');
  if(a){const d=load(CKEY,{});d[a.dataset.u]=asCount(d[a.dataset.u])+1;save(CKEY,d);render();return;}
  const tr=e.target.closest('tr'); if(!tr)return;
  document.querySelectorAll('#tb tr').forEach(x=>x.classList.remove('sel'));
  tr.classList.add('sel');
  const r=VIEW[+tr.dataset.i];
  const det=document.getElementById('detail');
  det.className='open';
  det.innerHTML = `<h3>${esc(r[IDX.username])}</h3><dl>` + COLS.map(c=>{
    const v=r[IDX[c.key]];
    if(v===null||v===''||v===false)return '';
    return `<dt>${c.key.replace(/_/g,' ')}</dt><dd>${esc(v)}</dd>`;}).join('') + '</dl>';
});

document.getElementById('q').oninput=render;
document.getElementById('colbtn').onclick=()=>document.getElementById('colmenu').classList.toggle('open');
document.addEventListener('click',e=>{ if(!e.target.closest('#cols')) document.getElementById('colmenu').classList.remove('open'); });
document.getElementById('resetlayout').onclick=()=>{
  L={order:COLS.map(c=>c.key), on:COLS.filter(c=>c.on).map(c=>c.key), w:{}, sort:{k:'followers_n',d:-1}};
  save(LKEY,L); render();};
document.getElementById('resetclicks').onclick=()=>{ if(confirm('Clear click history?')){save(CKEY,{});render();} };
document.getElementById('csv').onclick=()=>{
  const ks=shown();
  const esc2=v=>{const s=v===null||v===undefined?'':String(v);return /[",\n]/.test(s)?'"'+s.replace(/"/g,'""')+'"':s;};
  const body=[ks.join(','),...VIEW.map(r=>ks.map(k=>esc2(r[IDX[k]])).join(','))].join('\n');
  const a=document.createElement('a');
  a.href=URL.createObjectURL(new Blob([body],{type:'text/csv'}));
  a.download='workbench-view.csv'; a.click(); URL.revokeObjectURL(a.href);};
render();
</script>
"""

if __name__ == "__main__":
    main()
