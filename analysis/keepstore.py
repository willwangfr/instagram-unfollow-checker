# Copyright (C) 2026 William Wang
# Licensed under the GNU AGPL v3 or later. See LICENSE.
"""A keep-list: accounts never to suggest unfollowing, whatever the numbers say.

Instagram's close-friends and favourites lists cover most of this and are read
straight from the export. A keep-list covers everyone else. Names are starred in
the browser pages, exported as JSON, and read back here, so a decision made in
a browser survives a cleared cache and reaches the reports.
"""

import json
import re
from pathlib import Path

_PROFILE_URL = re.compile(r"^https?://(www\.)?instagram\.com/", re.I)


def normalise(name) -> str:
    """'@x', 'x' and 'https://www.instagram.com/x/' all mean the same account."""
    n = _PROFILE_URL.sub("", str(name).strip())
    return n.split("/")[0].split("?")[0].lstrip("@").strip()


def load_keep(path) -> set:
    """Read a keep-list from the browser's JSON export or a plain text file."""
    text = Path(path).read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        names = [line.split("#")[0] for line in text.splitlines()]
    else:
        names = data if isinstance(data, list) else data.get("keep", [])
    return {n for n in (normalise(x) for x in names) if n}


# Shared by every page that shows names. Stars and click counts both live in
# localStorage; export bundles them into one file and import merges one back.
KEEP_JS = r"""
const KKEY='ig_keep', CLICKS_KEY='ig_unfollow_done';
const ksLoad=(k,d)=>{try{return JSON.parse(localStorage.getItem(k))??d}catch(e){return d}};
const ksSave=(k,v)=>{try{localStorage.setItem(k,JSON.stringify(v))}catch(e){}};
const keepSet=()=>new Set(ksLoad(KKEY,[]));
const ksNorm=u=>String(u).trim().replace(/^https?:\/\/(www\.)?instagram\.com\//i,'')
  .split(/[/?]/)[0].replace(/^@/,'').trim();
function toggleKeep(u){const s=keepSet(); s.has(u)?s.delete(u):s.add(u); ksSave(KKEY,[...s].sort());}
function exportKeep(){
  const payload={format:'ig-keep-v1', exported_at:new Date().toISOString(),
    keep:[...keepSet()].sort(), clicks:ksLoad(CLICKS_KEY,{})};
  const a=document.createElement('a');
  a.href=URL.createObjectURL(new Blob([JSON.stringify(payload,null,2)],{type:'application/json'}));
  a.download='ig-keep-list.json'; a.click(); URL.revokeObjectURL(a.href);
}
// Merges, never replaces: importing adds to what this browser already holds,
// and a click count only ever goes up.
function importKeep(file, done){
  const r=new FileReader();
  r.onload=()=>{
    const text=String(r.result);
    let keep=[], clicks={};
    try{ const j=JSON.parse(text);
      if(Array.isArray(j)) keep=j; else { keep=j.keep||[]; clicks=j.clicks||{}; } }
    catch(e){ keep=text.split(/\r?\n/).map(l=>l.split('#')[0]); }
    keep=keep.map(ksNorm).filter(Boolean);
    const s=keepSet(); keep.forEach(u=>s.add(u)); ksSave(KKEY,[...s].sort());
    const one=v=>{const n=Number(v)||0; return n>1e10?1:n;};
    const c=ksLoad(CLICKS_KEY,{});
    for(const [u,n] of Object.entries(clicks)) c[u]=Math.max(one(c[u]), one(n));
    ksSave(CLICKS_KEY,c);
    done(keep.length, Object.keys(clicks).length);
  };
  r.readAsText(file);
}
"""
