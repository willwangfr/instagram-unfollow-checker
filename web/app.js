// Copyright (C) 2026 William Wang
// Licensed under the GNU AGPL v3 or later. See LICENSE.

import { readCentralDirectory, readText } from "./zipreader.js";
import { parseUsernames, parseFollowDates, parseDatedUsers,
         parseGeneratedAt, locate } from "./parsers.js";
import { analyse, TIERS, toCSV } from "./analysis.js";

const $ = id => document.getElementById(id);
const logEl = $("log");
let LOG = [];
function log(msg, cls) {
  LOG.push(cls ? `<span class="${cls}">${msg}</span>` : msg);
  logEl.innerHTML = LOG.join("\n");
  logEl.scrollTop = logEl.scrollHeight;
}

// Clicked-through state, per browser. Never leaves this machine.
const KEY = "ig_web_done";
const getDone = () => { try { return JSON.parse(localStorage.getItem(KEY)) || {}; } catch { return {}; } };
const setDone = d => { try { localStorage.setItem(KEY, JSON.stringify(d)); } catch {} };

let RESULT = null, SORT = { col: "username", dir: 1 };

const COLS = [
  ["username",    r => r.username,                          "s"],
  ["relationship",r => r.mutual ? "mutual" : r.youFollow ? "you follow only" : "they follow only", "s"],
  ["verdict",     r => r.tier ? TIERS[r.tier].label : "",   "s"],
  ["you followed",r => r.followedOn ? r.followedOn.toISOString().slice(0,10) : "", "s"],
  ["days",        r => r.age,                               "n"],
  ["left between",r => r.leftBetween || "",                 "s"],
  ["handle",      r => r.continuity,                        "s"],
];

async function loadSnapshot(file) {
  log(`\n${file.name} — ${(file.size/1048576).toFixed(1)} MB`);
  const names = await readCentralDirectory(file);
  log(`  ${names.size.toLocaleString()} entries in the archive`);
  const found = locate(names.keys());
  if (!found.following || !found.followers.length) {
    log("  Could not find followers/following pages. Did you request " +
        '"Followers and following" in HTML format?', "err");
    return null;
  }

  const followingHtml = await readText(file, names.get(found.following));
  let followersHtml = "";
  for (const f of found.followers) followersHtml += await readText(file, names.get(f));
  if (found.followers.length > 1)
    log(`  followers split across ${found.followers.length} files — read all of them`);

  const fo = parseUsernames(followingHtml), fl = parseUsernames(followersHtml);
  if (!fo.ok || !fl.ok) { log(`  Parse failed: ${(fo.why || fl.why)}`, "err"); return null; }

  const dates = parseFollowDates(followingHtml);
  if (!dates.ok) log("  No follow timestamps found — ages and tiers will be limited.", "warn");

  let unfollowLog = new Map();
  if (found.unfollowLog) {
    const r = parseDatedUsers(await readText(file, names.get(found.unfollowLog)));
    if (r.ok) { unfollowLog = r.rows; log(`  unfollow log: ${r.rows.size} entries (${r.shape} layout)`, "ok"); }
    else log(`  unfollow log present but unreadable: ${r.why}`, "warn");
  } else {
    log("  no 'recently unfollowed' page in this export — block detection off", "warn");
  }

  const gen = parseGeneratedAt(followersHtml, dates.dates);
  if (!gen.at) { log("  Could not date this export — skipping it.", "err"); return null; }
  if (gen.source !== "stamp") log(`  dated from ${gen.source}`, "warn");
  const generatedAt = gen.at;
  const date = generatedAt.toISOString().slice(0, 10);
  log(`  ${fo.users.length.toLocaleString()} following, ${fl.users.length.toLocaleString()} followers, dated ${date}`, "ok");
  return { date, generatedAt, following: new Set(fo.users), followers: new Set(fl.users),
           followDates: dates.dates, unfollowLog };
}

async function handleFiles(files) {
  LOG = []; log(`Reading ${files.length} file${files.length > 1 ? "s" : ""}…`);
  const snaps = [];
  for (const f of files) {
    try { const s = await loadSnapshot(f); if (s) snaps.push(s); }
    catch (e) { log(`  ${f.name}: ${e.message}`, "err"); }
  }
  if (!snaps.length) { log("\nNothing usable was found.", "err"); return; }
  snaps.sort((a, b) => a.date.localeCompare(b.date));
  const uniq = [];
  for (const s of snaps) if (!uniq.some(u => u.date === s.date)) uniq.push(s);
  if (uniq.length === 1)
    log("\nOne export only. Add older exports to date departures, spot renames and detect blocks.", "warn");

  RESULT = analyse(uniq);
  log(`\nAnalysed ${RESULT.rows.length.toLocaleString()} people across ${uniq.length} snapshot(s).`, "ok");
  render();
  $("out").classList.remove("hidden");
}

function render() {
  const { counts, rows, blocks } = RESULT;
  $("cards").innerHTML = [
    ["Following", counts.following], ["Followers", counts.followers],
    ["Mutuals", counts.mutuals], ["Don't follow back", counts.nfb],
    ["You don't follow back", counts.fans],
  ].map(([k, v]) => `<div class="card"><b>${v.toLocaleString()}</b><span class="note">${k}</span></div>`).join("");

  const tsel = $("tier");
  if (tsel.options.length === 1)
    for (const [k, t] of Object.entries(TIERS))
      tsel.insertAdjacentHTML("beforeend", `<option value="${k}">${t.label}</option>`);

  $("hr").innerHTML = "<th>#</th>" + COLS.map(([lab]) =>
    `<th data-c="${lab}">${lab}${SORT.col === lab ? (SORT.dir > 0 ? " ↑" : " ↓") : ""}</th>`).join("");
  [...$("hr").querySelectorAll("th[data-c]")].forEach(th => th.onclick = () => {
    const c = th.dataset.c;
    SORT = { col: c, dir: SORT.col === c ? -SORT.dir : 1 };
    render();
  });

  const q = $("q").value.toLowerCase(), rel = $("rel").value, tier = $("tier").value;
  const hide = $("hide").checked, done = getDone();
  const relOf = r => r.mutual ? "mutual" : r.youFollow ? "you_follow_only" : "they_follow_only";
  let list = rows.filter(r =>
    (!rel || relOf(r) === rel) && (!tier || r.tier === tier) &&
    (!hide || !done[r.username]) && (!q || r.username.toLowerCase().includes(q)));

  const col = COLS.find(c => c[0] === SORT.col) || COLS[0];
  list.sort((a, b) => {
    const x = col[1](a), y = col[1](b);
    if (x === null || x === undefined || x === "") return 1;
    if (y === null || y === undefined || y === "") return -1;
    return (col[2] === "n" ? x - y : String(x).localeCompare(String(y))) * SORT.dir;
  });

  $("rows").innerHTML = list.slice(0, 2000).map((r, i) => {
    const t = r.tier ? TIERS[r.tier] : null;
    return `<tr><td class="meta">${i + 1}</td>` + COLS.map(([lab, get, ty], ci) => {
      const v = get(r);
      if (ci === 0) return `<td><a href="https://www.instagram.com/${r.username}/" target="_blank" rel="noopener"
        data-u="${r.username}" class="${done[r.username] ? "done" : ""}">${r.username}</a></td>`;
      if (lab === "verdict" && t)
        return `<td><span class="pill" style="color:${t.colour};border-color:${t.colour}">${t.label}</span></td>`;
      return `<td class="${ty === "n" ? "n" : "meta"}">${v === null || v === undefined ? "—" : v}</td>`;
    }).join("") + "</tr>";
  }).join("");

  $("count").textContent = `${list.length.toLocaleString()} shown${list.length > 2000 ? " (first 2000)" : ""} · ${Object.keys(done).length} clicked`;

  const b = blocks;
  $("blocknote").innerHTML = b.usable
    ? `Follows that vanished from your following list with no matching entry in your own unfollow log — someone else removed them. A block is invisible to a logged-out visitor, so this is a strong signal, never proof. Log covers from ${b.coveredFrom.toISOString().slice(0,10)}.`
    : b.why;
  $("blocks").innerHTML = b.candidates.slice(0, 300).map((c, i) =>
    `<tr><td class="meta">${i + 1}</td><td><a href="https://www.instagram.com/${c.username}/" target="_blank" rel="noopener">${c.username}</a></td>
     <td class="meta">${c.window}</td><td class="meta">${c.wasMutual ? "yes" : "no"}</td></tr>`).join("");
}

// wiring
const drop = $("drop"), fileInput = $("f");
fileInput.onchange = e => handleFiles([...e.target.files]);
["dragenter", "dragover"].forEach(ev => drop.addEventListener(ev, e => {
  e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach(ev => drop.addEventListener(ev, e => {
  e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", e => handleFiles([...e.dataTransfer.files]));
document.addEventListener("click", e => {
  const a = e.target.closest("a[data-u]");
  if (!a) return;
  const d = getDone(); d[a.dataset.u] = Date.now(); setDone(d);
  a.classList.add("done");
  $("count").textContent = $("count").textContent.replace(/\d+ clicked/, `${Object.keys(d).length} clicked`);
});
["q", "rel", "tier", "hide"].forEach(id => $(id).addEventListener("input", () => RESULT && render()));
$("csv").onclick = () => {
  const blob = new Blob([toCSV(RESULT.rows)], { type: "text/csv" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = "instagram-analysis.csv"; a.click();
  URL.revokeObjectURL(a.href);
};
$("reset").onclick = () => { if (confirm("Clear all clicked marks?")) { setDone({}); render(); } };
