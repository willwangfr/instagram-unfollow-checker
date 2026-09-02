"""Regenerate the README screenshots.

Runs the real pipeline over a synthetic Instagram export, so the reports and the
console log in the README are genuine output -- only check_account() is stubbed,
since hitting Instagram 152 times to take a screenshot would be rude and slow.
The stub's status mix is taken from a real 1066-account run.

    python3 docs/make_screenshots.py
"""

import functools
import http.server
import io
import os
import random
import re
import socketserver
import sys
import tempfile
import threading
import types
import zipfile
from contextlib import redirect_stdout
from pathlib import Path

from playwright.sync_api import sync_playwright

DOCS = Path(__file__).resolve().parent
PROJ = DOCS.parent
OUT = DOCS / "screenshots"
PORT = 8731

sys.path.insert(0, str(PROJ))
import ig_unfollow_checker as ig  # noqa: E402

# --- Synthetic handles -------------------------------------------------------
# Every handle ends in `.demo` so no screenshot can implicate a real account.

PREFIX = """nina marco kaitlyn tyler jenna andre sofia devon priya lucas harper omar
elena jasper maya colin rosa felix amara tobias iris hugo noor silas wren dario june
kofi lila arjun esme rafa theo yuki mira otis nadia beau zara emil quinn thea milo
sana rhys imani cleo bodhi anouk ravi selma pico tomas edda kiran linh nell osric
paloma gustav ingrid mateo saoirse bram yara casper freya duke lumen ochre basil
tundra fennel cobalt willow marlow bishop sable orson vesper juniper haven ansel
tuesday clover moss birch atlas sorrel kestrel plover mabel odile leif hattie
soren nova wilder august indigo cassia rowan lark ember flint onyx pearl reed
sage tally umber vale winnow xander yarrow zinnia alder briar cedar dune eldr
ferro gale halcyon isla jade koa loam merle nash opal quill rook stellan tarn
ulric vireo wrenna xenia yonder zephyr abel bay cove drift echo fable glen hollow
inlet jetty kite ledge mesa north orchard pike quarry ridge shale terrace""".split()

TAIL = ["", ".jpg", "_films", ".studio", "_reads", ".ceramics", "_run", ".draws",
        "_climbs", ".bakes", "_type", ".plants", "_dev", ".co", "_ldn", ".nyc"]

N_NOT_BACK = 152
N_MUTUAL = 453
N_EXTRA_FOLLOWERS = 120

# Viewport for the raw report captures. The height crops between list rows
# (first row at y=118, 26px apart) so the list never ends mid-item.
RAW_W, RAW_H = 800, 552


def showcase(n, seed):
    """n handles with distinct prefixes -- these are the ones a reader sees."""
    r = random.Random(seed)
    words = sorted(set(PREFIX))
    r.shuffle(words)
    if len(words) < n:
        raise SystemExit(f"need {n} distinct prefixes, PREFIX has {len(words)}")
    return [f"{w}{r.choice(TAIL)}.demo" for w in words[:n]]


def filler(n, seed, avoid):
    """Bulk handles that never appear in output -- they only need to be unique."""
    r = random.Random(seed)
    out, seen = [], set(avoid)
    while len(out) < n:
        h = f"{r.choice(PREFIX)}{r.randint(2, 999)}{r.choice(TAIL)}.demo"
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out


def build_export(path):
    """Write a zip shaped like a real Instagram HTML export."""
    not_back = showcase(N_NOT_BACK, 1)
    mutual = filler(N_MUTUAL, 2, not_back)
    extra = filler(N_EXTRA_FOLLOWERS, 3, not_back + mutual)

    def page(users, prefix):
        rows = "\n".join(
            f'<div><a href="https://www.instagram.com/{prefix}{u}" target="_blank">{u}</a></div>'
            for u in users)
        return f"<html><body>{rows}</body></html>"

    with zipfile.ZipFile(path, "w") as z:
        base = "connections/followers_and_following"
        z.writestr(f"{base}/following.html", page(mutual + not_back, "_u/"))
        z.writestr(f"{base}/followers_1.html", page(mutual + extra, ""))
        # The extra lists the dashboard surfaces as collapsible sections.
        for name, count, seed in [
            ("pending_follow_requests", 7, 11),
            ("recent_follow_requests", 12, 12),
            ("recently_unfollowed_profiles", 9, 13),
            ("follow_requests_you've_received", 4, 14),
        ]:
            z.writestr(f"{base}/{name}.html",
                       page(filler(count, seed, not_back + mutual + extra), "_u/"))


def run_pipeline(workdir):
    """Run the real main() with the network check stubbed; return its stdout."""
    rng = random.Random(7)

    def fake_check(page, username):
        roll = rng.random()
        if roll < 0.705:
            status = "EXISTS"
        elif roll < 0.775:
            status = "EXISTS_PRIVATE"
        elif roll < 0.985:
            status = "NOT_FOUND"
        else:
            status = "LOGIN_WALL"
        exists = status.startswith("EXISTS")
        title = (f"{username.split('.')[0].title()} (@{username}) • Instagram photos and videos"
                 if exists else f"{'Page Not Found' if status == 'NOT_FOUND' else 'Login'} • Instagram")
        return {"username": username, "status": status, "title": title}

    class _NoBrowser:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def launch(self, **k): return self
        def new_context(self, **k): return self
        def new_page(self): return self
        def close(self): pass
        chromium = property(lambda self: self)

    ig.check_account = fake_check
    ig.sync_playwright = lambda: _NoBrowser()
    # Leave MIN_DELAY/MAX_DELAY alone -- the log prints a time estimate from
    # them -- and make only the sleeping itself free.
    ig.time = types.SimpleNamespace(sleep=lambda *a, **k: None)

    zip_name = "instagram-export.zip"
    build_export(workdir / zip_name)
    os.chdir(workdir)                      # keeps absolute paths out of the log
    sys.argv = ["ig_unfollow_checker.py", zip_name, "--output-dir", "demo_out"]

    buf = io.StringIO()
    with redirect_stdout(buf):
        ig.main()
    return buf.getvalue()


def parse_totals(log):
    """Pull the run's numbers out of the log so the graphics can't drift from it."""
    def find(pattern):
        m = re.search(pattern, log)
        if not m:
            raise SystemExit(f"could not parse {pattern!r} out of the run log")
        return int(m.group(1))

    return {
        "following": find(r"Following:\s+(\d+)"),
        "followers": find(r"Followers:\s+(\d+)"),
        "mutuals": find(r"Mutuals:\s+(\d+)"),
        "not_back": find(r"Not following back:\s+(\d+)"),
        "fans": find(r"Fans:\s+(\d+)"),
        "active": find(r"Confirmed active:\s+(\d+)"),
        "deleted": find(r"Confirmed deleted:\s+(\d+)"),
        "unknown": find(r"Inconclusive:\s+(\d+)"),
    }


def terminal_page(log, head_lines=30, tail_lines=16):
    """Render the captured stdout as a terminal window."""
    lines = log.splitlines()
    head, tail = lines[:head_lines], lines[-tail_lines:]
    elided = len(lines) - head_lines - tail_lines

    def esc(s):
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    body = esc("\n".join(head)) + \
        f'\n<span class="gap">      ⋮        ({elided} lines omitted)</span>\n' + \
        esc("\n".join(tail))

    return f"""<html><head><meta charset="utf-8"><style>
* {{ box-sizing: border-box; }}
body {{ margin:0; padding:46px 44px; background:linear-gradient(140deg,#1b2430,#101318 60%,#181320);
        font-family:'SF Mono','JetBrains Mono',Menlo,monospace; }}
.win {{ border-radius:11px; overflow:hidden;
        box-shadow:0 26px 60px rgba(0,0,0,.55),0 0 0 1px rgba(255,255,255,.07); }}
.bar {{ background:#2c2f36; height:32px; display:flex; align-items:center; padding:0 13px; gap:8px; }}
.dot {{ width:12px; height:12px; border-radius:50%; }}
.t {{ flex:1; text-align:center; color:#9aa1ac; font-size:12px; margin-left:-52px; }}
.body {{ background:#14161a; padding:16px 20px 20px; color:#d3d7de; font-size:12.5px;
         line-height:1.62; white-space:pre; }}
.p {{ color:#6ee7a8; }} .d {{ color:#7dd3fc; }} .gap {{ color:#5a616c; }}
</style></head><body>
<div class="win"><div class="bar">
  <div class="dot" style="background:#ff5f57"></div>
  <div class="dot" style="background:#febc2e"></div>
  <div class="dot" style="background:#28c840"></div>
  <div class="t">ig-unfollow-checker — zsh</div>
</div><div class="body"><span class="d">~/ig-unfollow-checker</span> <span class="p">❯</span> \
python3 ig_unfollow_checker.py instagram-export.zip
{body}</div></div></body></html>"""


# Which list positions to mark as clicked, and how many times -- shows off the
# three fade levels and the counter without needing anyone to sit and click.
CLICKED = {0: 3, 1: 1, 3: 2, 4: 1, 6: 1, 7: 3, 9: 2, 10: 1, 12: 1, 14: 2,
           15: 1, 17: 1, 20: 3, 21: 1, 23: 2, 26: 1, 28: 1, 31: 1, 33: 2,
           36: 1, 39: 1, 42: 1}

SEED_CLICKS = """() => {
  const links = [...document.querySelectorAll('a[data-user]')];
  const data = {};
  for (const [i, n] of Object.entries(%s)) if (links[i]) data[links[i].dataset.user] = n;
  localStorage.setItem('ig_click_tracker', JSON.stringify(data));
}""" % str(CLICKED).replace("'", '"')


# --- Presentation ------------------------------------------------------------
# Shared look for the summary card and the framed report shots.

PAGE_CSS = """
* { box-sizing: border-box; }
body { margin:0; padding:34px 30px;
       background:linear-gradient(140deg,#1b2430,#101318 60%,#181320);
       font-family:'SF Mono','JetBrains Mono',Menlo,monospace; color:#d3d7de; }
.win { border-radius:11px; overflow:hidden; position:relative;
       box-shadow:0 26px 60px rgba(0,0,0,.55),0 0 0 1px rgba(255,255,255,.07); }
.bar { background:#2c2f36; height:34px; display:flex; align-items:center;
       padding:0 13px; gap:8px; }
.dot { width:12px; height:12px; border-radius:50%; }
.pill { flex:1; margin:0 14px 0 6px; height:20px; border-radius:10px;
        background:#212429; color:#9aa1ac; font-size:11px; display:flex;
        align-items:center; padding:0 10px; gap:7px; }
.pill .lock { color:#6b7280; }
.shot { display:block; width:100%; }
.note { position:absolute; border-radius:8px; padding:9px 12px; font-size:11.5px;
        line-height:1.5; max-width:330px; background:rgba(20,23,28,.94);
        border:1px solid rgba(255,255,255,.13); color:#c9ced8; }
.note b { color:#fff; font-weight:600; }
.note.blue { border-color:rgba(79,195,247,.55); }
.note.red  { border-color:rgba(255,82,82,.55); }
svg.arrows { position:absolute; inset:0; width:100%; height:100%;
             pointer-events:none; overflow:visible; }
"""


def browser_frame(img_src, img_w, img_h, filename, notes=(), arrows=()):
    """A browser window around a screenshot, with callouts drawn over it.

    notes:  (css_position, extra_class, html) -- position is inline CSS.
    arrows: (x1, y1, x2, y2, colour) in screenshot pixel coordinates; the
            image sits below a 34px title bar, which this offsets for.
    """
    bar_h = 34
    note_html = "\n".join(
        f'<div class="note {cls}" style="{pos}">{html}</div>' for pos, cls, html in notes)

    paths = []
    for x1, y1, x2, y2, colour in arrows:
        y1, y2 = y1 + bar_h, y2 + bar_h
        cx, cy = (x1 + x2) / 2, min(y1, y2) - abs(x2 - x1) * 0.16 - 8
        paths.append(
            f'<path d="M{x1},{y1} Q{cx},{cy} {x2},{y2}" fill="none" stroke="{colour}" '
            f'stroke-width="1.6" opacity=".85" marker-end="url(#head-{colour[1:]})"/>')
    heads = "".join(
        f'<marker id="head-{c[1:]}" viewBox="0 0 10 10" refX="8" refY="5" '
        f'markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
        f'<path d="M0,1 L9,5 L0,9 z" fill="{c}"/></marker>'
        for c in {a[4] for a in arrows})

    return f"""<html><head><meta charset="utf-8"><style>{PAGE_CSS}
.frame {{ width:{img_w}px; }}
</style></head><body>
<div class="win frame">
  <div class="bar">
    <div class="dot" style="background:#ff5f57"></div>
    <div class="dot" style="background:#febc2e"></div>
    <div class="dot" style="background:#28c840"></div>
    <div class="pill"><span class="lock">file://</span>{filename}</div>
  </div>
  <img class="shot" src="{img_src}" width="{img_w}" height="{img_h}">
  <svg class="arrows" viewBox="0 0 {img_w} {img_h + bar_h}"><defs>{heads}</defs>
    {"".join(paths)}
  </svg>
  {note_html}
</div></body></html>"""


def summary_card(t):
    """The funnel: what goes in, what comes out, and why you care."""
    total = t["active"] + t["deleted"] + t["unknown"]
    pct = lambda n: n / total * 100
    return f"""<html><head><meta charset="utf-8"><style>{PAGE_CSS}
body {{ padding:40px 38px; }}
.card {{ background:#14161a; border-radius:12px; padding:30px 34px 28px;
         box-shadow:0 26px 60px rgba(0,0,0,.55),0 0 0 1px rgba(255,255,255,.07); }}
.kicker {{ font-size:11px; letter-spacing:.18em; text-transform:uppercase;
           color:#6b7280; margin-bottom:22px; }}
.step {{ display:flex; align-items:baseline; gap:18px; }}
.num {{ font-size:34px; font-weight:700; color:#e8ecf2; letter-spacing:-.02em; }}
.lbl {{ font-size:13px; color:#aeb6c2; }}
.rail {{ margin:7px 0 7px 12px; padding-left:26px; border-left:2px solid #2b313a;
         font-size:11.5px; color:#6f7885; line-height:1.9; }}
.split {{ display:flex; gap:44px; margin-top:14px; }}
.split div {{ font-size:12px; color:#8b93a1; }}
.split b {{ display:block; font-size:26px; font-weight:700; letter-spacing:-.02em;
            margin-bottom:2px; }}
.bar2 {{ display:flex; height:13px; border-radius:7px; overflow:hidden;
         margin:20px 0 0; background:#20242b; }}
.punch {{ margin-top:24px; padding-top:20px; border-top:1px solid #232830;
          font-size:14.5px; color:#e8ecf2; line-height:1.6; }}
.punch b {{ color:#ff6b6b; }}
</style></head><body>
<div class="card">
  <div class="kicker">ig-unfollow-checker &nbsp;·&nbsp; one export, one command</div>

  <div class="step"><div class="num">{t['following']}</div>
    <div class="lbl">accounts you follow</div></div>
  <div class="rail">minus the {t['mutuals']} of them who follow you back too</div>

  <div class="step"><div class="num" style="color:#4fc3f7">{t['not_back']}</div>
    <div class="lbl">not following you back</div></div>
  <div class="rail">each one opened in a real headless browser, no login</div>

  <div class="split">
    <div><b style="color:#4fc3f7">{t['active']}</b>still active</div>
    <div><b style="color:#ff5252">{t['deleted']}</b>deleted or deactivated</div>
    <div><b style="color:#ff9800">{t['unknown']}</b>inconclusive</div>
  </div>
  <div class="bar2">
    <div style="width:{pct(t['active']):.1f}%;background:#4fc3f7"></div>
    <div style="width:{pct(t['deleted']):.1f}%;background:#ff5252"></div>
    <div style="width:{pct(t['unknown']):.1f}%;background:#ff9800"></div>
  </div>

  <div class="punch"><b>{t['deleted']} of them no longer exist.</b>
    Instagram still counts them in your following list and gives you no way to
    tell — this finds them so you can clear them without opening a dead profile.</div>
</div></body></html>"""


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix="ig-shots-"))

    log = run_pipeline(workdir)
    totals = parse_totals(log)
    print(" ".join(f"{k}={v}" for k, v in totals.items()))
    (workdir / "terminal.html").write_text(terminal_page(log), encoding="utf-8")

    # Serve the reports over http so their localStorage click tracking works.
    handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                                directory=str(workdir))
    socketserver.TCPServer.allow_reuse_address = True
    srv = socketserver.TCPServer(("127.0.0.1", PORT), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{PORT}"

    with sync_playwright() as p:
        browser = p.chromium.launch()

        def shot(url, dest, w, h, seed=None, full_page=False):
            ctx = browser.new_context(viewport={"width": w, "height": h},
                                      device_scale_factor=2)
            page = ctx.new_page()
            page.goto(url)
            if seed:
                page.evaluate(seed)
                page.reload()
            page.wait_for_timeout(350)
            page.screenshot(path=str(dest), full_page=full_page)
            ctx.close()
            try:
                print("wrote", Path(dest).relative_to(PROJ))
            except ValueError:
                pass                                   # intermediate, not shipped

        def framed(page_html, name, w, h):
            """Render a wrapper page (frame + callouts) to a final screenshot."""
            (workdir / f"{name}.html").write_text(page_html, encoding="utf-8")
            shot(f"{base}/{name}.html", OUT / f"{name}.png", w, h, full_page=True)

        # 1. Raw report screenshots, later framed and annotated.
        shot(f"{base}/demo_out/active_not_following_back.html",
             workdir / "raw-active.png", RAW_W, RAW_H, seed=SEED_CLICKS)
        shot(f"{base}/demo_out/deleted_accounts.html",
             workdir / "raw-deleted.png", RAW_W, RAW_H)
        shot(f"{base}/demo_out/dashboard.html",
             workdir / "raw-dashboard.png", 720, 660)

        # 2. The summary card and the terminal window.
        framed(summary_card(totals), "summary", 880, 520)
        framed(browser_frame("raw-dashboard.png", 720, 660, "dashboard.html"),
               "dashboard", 780, 300)
        shot((workdir / "terminal.html").as_uri(), OUT / "terminal-run.png",
             720, 600, full_page=True)

        # 3. The reports, in a browser frame with callouts over the empty space.
        framed(browser_frame(
            "raw-active.png", RAW_W, RAW_H, "active_not_following_back.html",
            notes=[
                ("right:16px; top:150px;", "blue",
                 "<b>Counts every profile you've opened.</b> Shared across all "
                 "the reports, and survives closing the tab."),
                ("left:380px; top:300px;", "blue",
                 "<b>(3x) = opened three times.</b> Links fade as you visit "
                 "them, so you always know where you stopped."),
            ],
            arrows=[(700, 116, 742, 50, "#4fc3f7"),
                    (386, 286, 300, 302, "#4fc3f7")],
        ), "active-report", RAW_W + 60, 300)

        framed(browser_frame(
            "raw-deleted.png", RAW_W, RAW_H, "deleted_accounts.html",
            notes=[
                ("left:400px; top:150px;", "red",
                 "<b>None of these profiles exist any more.</b> Instagram keeps "
                 "them in your following list and never says which ones are "
                 "gone — this is the list you can clear on sight, without "
                 "opening a single dead page."),
            ],
            arrows=[(406, 178, 322, 132, "#ff5252")],
        ), "deleted-report", RAW_W + 60, 300)

        browser.close()

    srv.shutdown()


if __name__ == "__main__":
    main()
