// Copyright (C) 2026 William Wang
// Licensed under the GNU AGPL v3 or later. See LICENSE.
//
// Ported from the Python parsers, including the lessons they learned the hard
// way. Every function reports whether it found the shape it expected, so a
// format change surfaces as "could not parse" instead of "you have no data" —
// the failure that silently hid 2,659 pending requests in the Python version.

const A_UNDERSCORE = /href="https:\/\/www\.instagram\.com\/_u\/([A-Za-z0-9_.]+)"/g;
const A_PLAIN      = /href="https:\/\/www\.instagram\.com\/([A-Za-z0-9_.]+)"/g;
// Followers and following are anchors; every other export page is a borderless
// table with no links in it at all.
const TABLE_USER   = /<td[^>]*>Username<\/td>\s*<td[^>]*>([A-Za-z0-9_.]+)<\/td>/g;
// Each entry carries the moment the follow happened, right after the anchor.
const PAIR_DATED   = /href="https:\/\/www\.instagram\.com\/(?:_u\/)?([A-Za-z0-9_.]+)"[^>]*>[\s\S]*?<\/a><\/div>\s*<div>([^<]+)<\/div>/g;
const TABLE_DATED  = /<td[^>]*>Username<\/td>\s*<td[^>]*>([A-Za-z0-9_.]+)<\/td>[\s\S]*?<div class="_3-94 _a6-o">([^<]*)<\/div>/g;

const all = (re, s) => { re.lastIndex = 0; return [...s.matchAll(re)]; };

export function parseUsernames(html) {
  for (const [re, shape] of [[A_UNDERSCORE, "anchor"], [A_PLAIN, "anchor"], [TABLE_USER, "table"]]) {
    const m = all(re, html);
    if (m.length) return { users: m.map(x => x[1]), shape, ok: true };
  }
  return { users: [], shape: null, ok: false,
           why: "No Instagram links or Username table cells found in this page." };
}

// "Aug 30, 2026 7:29 pm" — the only date format the export uses.
const MONTHS = {Jan:0,Feb:1,Mar:2,Apr:3,May:4,Jun:5,Jul:6,Aug:7,Sep:8,Oct:9,Nov:10,Dec:11};
export function parseDate(s) {
  const m = /^([A-Z][a-z]{2}) (\d{1,2}), (\d{4}) (\d{1,2}):(\d{2})\s*(am|pm)$/i.exec((s||"").trim());
  if (!m) return null;
  let h = +m[4] % 12;
  if (/pm/i.test(m[6])) h += 12;
  return new Date(+m[3], MONTHS[m[1]], +m[2], h, +m[5]);
}

/** username -> Date the follow happened. */
export function parseFollowDates(html) {
  const out = new Map();
  for (const [, u, t] of all(PAIR_DATED, html)) {
    const d = parseDate(t);
    if (d) out.set(u, d);
  }
  if (out.size) return { dates: out, ok: true };
  return { dates: out, ok: false,
           why: "Found no username/timestamp pairs in the expected layout." };
}

/**
 * Dated username lists, e.g. the "recently unfollowed" log.
 *
 * Instagram has shipped two different shapes for this page. Exports up to
 * early 2026 use anchors with a date beside them; later ones use a borderless
 * table with no links at all. Both appear in the wild — the same account's
 * February and August exports differ — so try each.
 */
export function parseDatedUsers(html) {
  for (const [re, shape] of [[PAIR_DATED, "anchor"], [TABLE_DATED, "table"]]) {
    const out = new Map();
    for (const [, u, t] of all(re, html)) {
      const d = parseDate(t);
      if (d) out.set(u, d);
    }
    if (out.size) return { rows: out, shape, ok: true };
  }
  return { rows: new Map(), shape: null, ok: false,
           why: "No dated username entries found in either known layout." };
}

// Kept for callers that only want the table shape.
export const parseTableWithDates = parseDatedUsers;

/**
 * When the export was built. Older exports carry no <time> stamp at all, so
 * fall back to the newest follow in the file — an export cannot predate its
 * own newest entry. Falling back to "now" instead would sort an old export
 * after a recent one and silently invert the whole timeline.
 */
export function parseGeneratedAt(html, followDates) {
  const m = /<time datetime="(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})/.exec(html);
  if (m) return { at: new Date(`${m[1]}T${m[2]}:${m[3]}:00Z`), source: "stamp" };
  if (followDates && followDates.size) {
    let max = null;
    for (const d of followDates.values()) if (!max || d > max) max = d;
    if (max) return { at: max, source: "newest follow (no timestamp in this export)" };
  }
  return { at: null, source: null };
}

/** Locate the files we care about, whatever the export's folder layout. */
export function locate(names) {
  // Materialise first: names is often an iterator, and spreading it more than
  // once silently yields nothing after the first pass.
  const all = [...names];
  const find = (pred) => all.find(pred);
  const inConn = n => n.includes("followers_and_following");
  return {
    following: find(n => n.endsWith("following.html") && inConn(n)),
    followers: all.filter(n => /followers_\d+\.html$/.test(n) && inConn(n)).sort(),
    unfollowLog: find(n => n.includes("recently_unfollowed_profiles") && n.endsWith(".html")),
    pending: find(n => n.includes("pending_follow_requests") && n.endsWith(".html")),
    blocked: find(n => n.includes("blocked_profiles") && n.endsWith(".html")),
  };
}
