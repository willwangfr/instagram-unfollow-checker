#!/usr/bin/env python3
"""One row per person in your Instagram graph, with everything known about them.

Three sources, in descending richness: the scraped profiles (name, bio, link,
counts), the display names Instagram already ships in the linkless table pages
of the export, and the follow timestamps present on every edge.
"""

import argparse, csv, json, os, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import igpaths
from follow_timeline import load_snapshot

HERE = None      # set from the config in main()

NAME_PAIR = re.compile(
    r'<td[^>]*>Name</td>\s*<td[^>]*>([^<]*)</td>\s*</tr>\s*<tr>\s*'
    r'<td[^>]*>Username</td>\s*<td[^>]*>([A-Za-z0-9_.]+)</td>', re.I)


def harvest_names(export_dir):
    """Display names Instagram already gave you, no requests needed."""
    names = {}
    for fn in sorted(os.listdir(export_dir)):
        if fn.endswith(".html"):
            h = Path(export_dir, fn).read_text(encoding="utf-8")
            for nm, u in NAME_PAIR.findall(h):
                if nm.strip():
                    names.setdefault(u, nm.strip())
    return names


def main():
    global HERE
    ap = argparse.ArgumentParser()
    igpaths.add_config_arg(ap)
    cfg = igpaths.load(ap.parse_args().config)
    HERE = cfg.work_dir
    following, followers = load_snapshot(cfg.latest_zip)
    graph = sorted(following | followers)
    dates = json.loads((HERE / "follow_dates.json").read_text())
    fo_on, fl_on = dates["following"], dates["followers"]
    bios = {p["username"]: p
            for p in json.loads((HERE / "bios.json").read_text())["profiles"]}
    checks = {}
    for f in ("results.json", "blockcheck/results.json", "blocksuspects/results.json"):
        pth = HERE / f
        if pth.exists():
            for r in json.loads(pth.read_text()).get("results", []):
                checks[r["username"]] = r
    names = harvest_names(cfg.connections_dir)

    rows = []
    for u in graph:
        in_fo, in_fl = u in following, u in followers
        rel = ("mutual" if in_fo and in_fl
               else "you_follow_only" if in_fo else "they_follow_only")
        b = bios.get(u, {})
        chk = checks.get(u, {})
        name = b.get("full_name") or names.get(u) or ""
        source = ("scraped" if b else "export_name" if u in names else "none")
        rows.append({
            "username": u, "relationship": rel,
            "profile_url": f"https://www.instagram.com/{u}/",
            "you_followed_on": (fo_on.get(u) or "")[:10],
            "they_followed_you_on": (fl_on.get(u) or "")[:10],
            "full_name": name, "bio": (b.get("bio") or "").replace("\n", " / "),
            "external_link": b.get("external_link") or "",
            "followers": b.get("followers") or "", "following": b.get("following") or "",
            "posts": b.get("posts") if b.get("posts") is not None else "",
            "profile_status": chk.get("status", "unchecked"),
            "data_source": source,
        })

    cols = list(rows[0].keys())
    with (HERE / "ig_graph_master.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    (HERE / "ig_graph_master.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False))

    from collections import Counter
    print(f"{len(rows)} people in your graph")
    for k, n in Counter(r["relationship"] for r in rows).most_common():
        print(f"   {k:18s} {n:5d}")
    print()
    for k, n in Counter(r["data_source"] for r in rows).most_common():
        print(f"   {k:18s} {n:5d}")
    named = sum(1 for r in rows if r["full_name"])
    print(f"\n   with a display name {named:5d}  ({named/len(rows)*100:.1f}%)")
    print(f"   with a bio          {sum(1 for r in rows if r['bio']):5d}")
    print(f"   with a follow date  {sum(1 for r in rows if r['you_followed_on'] or r['they_followed_you_on']):5d}")
    print("\nWrote ig_graph_master.csv and ig_graph_master.json")


if __name__ == "__main__":
    main()
