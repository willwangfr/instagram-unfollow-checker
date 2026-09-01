// Copyright (C) 2026 William Wang
// Licensed under the GNU AGPL v3 or later. See LICENSE.
//
// Runs the browser code under Node against real export ZIPs, so the parsing
// can be checked against known-good numbers without a browser:
//   node web/verify.mjs export-a.zip export-b.zip

import { openAsBlob } from "node:fs";
import { readCentralDirectory, readText } from "./zipreader.js";
import { parseUsernames, parseFollowDates, parseDatedUsers, parseGeneratedAt, locate } from "./parsers.js";
import { analyse } from "./analysis.js";

async function snap(path) {
  const t0 = Date.now();
  const file = await openAsBlob(path);
  const names = await readCentralDirectory(file);
  const f = locate(names.keys());
  const foH = await readText(file, names.get(f.following));
  let flH = ""; for (const n of f.followers) flH += await readText(file, names.get(n));
  const fo = parseUsernames(foH), fl = parseUsernames(flH);
  const dates = parseFollowDates(foH);
  let log = new Map();
  if (f.unfollowLog) { const r = parseDatedUsers(await readText(file, names.get(f.unfollowLog))); if (r.ok) log = r.rows; }
  const g = parseGeneratedAt(flH, dates.dates); const gen = g.at;
  console.log(`${path.split("/").pop()}`);
  console.log(`   ${(file.size/1073741824).toFixed(2)} GB, ${names.size.toLocaleString()} entries, read in ${((Date.now()-t0)/1000).toFixed(1)}s`);
  console.log(`   following ${fo.users.length}  followers ${fl.users.length}  followDates ${dates.dates.size}  unfollowLog ${log.size}  generated ${gen?.toISOString().slice(0,10)} (${g.source})`);
  return { date:(gen||new Date()).toISOString().slice(0,10), generatedAt:gen,
           following:new Set(fo.users), followers:new Set(fl.users), followDates:dates.dates, unfollowLog:log };
}

const snaps = [];
for (const p of process.argv.slice(2)) snaps.push(await snap(p));
snaps.sort((a,b)=>a.date.localeCompare(b.date));
const r = analyse(snaps);
console.log("\nANALYSIS");
console.log("  ", r.counts);
const t = {}; for (const row of r.rows) if (row.tier) t[row.tier] = (t[row.tier]||0)+1;
console.log("   tiers:", t);
console.log("   block candidates:", r.blocks.usable ? r.blocks.candidates.length : `unusable — ${r.blocks.why}`);
if (r.blocks.usable) console.log("   sample:", r.blocks.candidates.slice(0,5).map(c=>c.username).join(", "));
