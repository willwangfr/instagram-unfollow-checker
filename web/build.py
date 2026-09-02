#!/usr/bin/env python3
# Copyright (C) 2026 William Wang
# Licensed under the GNU AGPL v3 or later. See LICENSE.
"""Generate standalone.html — one file that works by double-clicking.

index.html loads ES modules, and browsers refuse to load modules over file://.
A reader who downloads this repo and opens index.html gets a blank page, which
is the worst possible failure for the people it is meant to serve. This bundles
the same code into a single file with no imports, so it opens straight from
disk and can be saved, emailed or carried on a stick.
"""

import re
from pathlib import Path

HERE = Path(__file__).parent
ORDER = ["zipreader.js", "parsers.js", "analysis.js", "app.js"]

def strip_modules(src: str) -> str:
    src = re.sub(r'^import\s+\{[^}]*\}\s+from\s+["\'][^"\']+["\'];?\s*$', "", src, flags=re.M)
    src = re.sub(r'^import\s+[^;\n]+;\s*$', "", src, flags=re.M)
    src = re.sub(r'^export\s+(?=(async\s+)?function|const|let|class)', "", src, flags=re.M)
    return src

def main():
    html = (HERE / "index.html").read_text()
    body = "\n".join(f"/* ==== {n} ==== */\n{strip_modules((HERE / n).read_text())}"
                     for n in ORDER)
    out = html.replace('<script type="module" src="app.js"></script>',
                       "<script>\n" + body + "\n</script>")
    assert "<script>" in out and 'src="app.js"' not in out, "bundling failed"
    (HERE / "standalone.html").write_text(out)
    kb = len(out) / 1024
    print(f"standalone.html written — {kb:.0f} KB, no imports, opens from file://")

if __name__ == "__main__":
    main()
