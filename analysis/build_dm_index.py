#!/usr/bin/env python3
# Copyright (C) 2026 William Wang
# Licensed under the GNU AGPL v3 or later. See LICENSE.
# Run a modified version as a network service and you must offer users its source.
"""Per-person DM metadata: how much you talk, when you last did, who spoke last.

Reads only the local export. Extracts counts and dates, not message bodies —
these are private conversations with other people, so the default is metadata.
Pass --with-preview to include the first 120 chars of the most recent message.
"""

import argparse, csv, json, os, re, sys
from collections import Counter
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import igpaths
from follow_timeline import load_snapshot

HERE = None      # set from the config in main()
ME = set()       # your own handles, from the config

# Each message block: <h2>sender</h2> ... <div class="_3-94 _a6-o">timestamp</div>
MSG = re.compile(r'<h2[^>]*>([^<]*)</h2>(.*?)<div class="_3-94 _a6-o">([^<]*)</div>', re.S)
TEXT = re.compile(r'<div>([^<>]{1,4000})</div>')


def norm(u):
    return re.sub(r"[^a-z0-9]", "", (u or "").lower())


def parse_thread(folder, want_preview):
    files = sorted(f for f in os.listdir(folder) if re.fullmatch(r"message_\d+\.html", f))
    msgs = []
    for f in files:
        h = Path(folder, f).read_text(encoding="utf-8", errors="replace")
        for sender, blob, ts in MSG.findall(h):
            try:
                t = datetime.strptime(ts.strip(), "%b %d, %Y %I:%M %p")
            except ValueError:
                t = None
            body = ""
            if want_preview:
                m = TEXT.search(blob)
                body = (m.group(1).strip() if m else "")
            msgs.append((sender.strip(), t, body))
    return msgs


def main():
    global HERE, ME, INBOX
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-preview", action="store_true")
    igpaths.add_config_arg(ap)
    args = ap.parse_args()
    cfg = igpaths.load(args.config)
    HERE, ME = cfg.work_dir, set(cfg.me)
    INBOX = cfg.activity_dir / "messages" / "inbox"

    fo, fl = load_snapshot(cfg.latest_zip)
    graph = fo | fl
    by_norm = {}
    for u in graph:
        by_norm.setdefault(norm(u), u)

    rows, unmatched = [], 0
    threads = sorted(os.listdir(INBOX))
    for i, t in enumerate(threads):
        folder = os.path.join(INBOX, t)
        if not os.path.isdir(folder):
            continue
        handle = re.sub(r"_\d+$", "", t)
        matched = by_norm.get(norm(handle))
        msgs = parse_thread(folder, args.with_preview)
        if not msgs:
            continue
        times = [m[1] for m in msgs if m[1]]
        senders = Counter(m[0] for m in msgs)
        mine = sum(n for s, n in senders.items() if s.lower() in ME)
        last = max(times) if times else None
        first = min(times) if times else None
        last_msg = max((m for m in msgs if m[1]), key=lambda m: m[1], default=None)
        rows.append({
            "thread": t, "handle_in_folder": handle,
            "matched_username": matched or "",
            "in_your_graph": bool(matched),
            "participants": " | ".join(sorted(s for s in senders if s.lower() not in ME)),
            "messages": len(msgs), "from_you": mine, "from_them": len(msgs) - mine,
            "first_message": first.isoformat()[:10] if first else "",
            "last_message": last.isoformat()[:10] if last else "",
            "last_sender": (last_msg[0] if last_msg else ""),
            "you_spoke_last": bool(last_msg and last_msg[0].lower() in ME),
            "last_preview": (last_msg[2] if (last_msg and args.with_preview) else ""),
        })
        if not matched:
            unmatched += 1
        if (i + 1) % 1000 == 0:
            print(f"  ...{i+1}/{len(threads)} threads")

    rows.sort(key=lambda r: (r["last_message"] or ""), reverse=True)
    cols = list(rows[0].keys())
    with (HERE / "dm_index.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)
    (HERE / "dm_index.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False))

    inb = [r for r in rows if r["in_your_graph"]]
    print(f"\n{len(rows)} threads parsed, {sum(r['messages'] for r in rows):,} messages")
    print(f"  matched to someone in your graph : {len(inb)}")
    print(f"  not in your graph                : {unmatched}")
    print(f"  you spoke last                   : {sum(1 for r in rows if r['you_spoke_last'])}")
    print(f"  they spoke last (unanswered)     : {sum(1 for r in rows if not r['you_spoke_last'])}")
    print("\n  most-messaged people in your graph:")
    for r in sorted(inb, key=lambda r: -r["messages"])[:10]:
        print(f"    {r['matched_username']:24s} {r['messages']:6,} msgs  "
              f"last {r['last_message']}  ({r['from_you']} you / {r['from_them']} them)")
    print("\nWrote dm_index.csv and dm_index.json")


if __name__ == "__main__":
    main()
