// Copyright (C) 2026 William Wang
// Licensed under the GNU AGPL v3 or later. See LICENSE.
//
// The judgements. Every verdict states how much the data supports it, because
// "doesn't follow you back" hides three different situations that deserve
// different decisions.

export const TIERS = {
  dropped:      { label: "Followed you, then left",            colour: "#ff9800" },
  confirmed:    { label: "Never followed back — confirmed",    colour: "#66bb6a" },
  probable:     { label: "Never followed back — probable",     colour: "#4fc3f7" },
  too_recent:   { label: "Too recent to judge",                colour: "#888"    },
  renamed:      { label: "Cannot verify — handle changed",     colour: "#7e57c2" },
};

const DAY = 86400000;

/**
 * @param snaps [{date:'YYYY-MM-DD', following:Set, followers:Set,
 *                followDates:Map, unfollowLog:Map}] oldest first
 */
export function analyse(snaps) {
  const latest = snaps[snaps.length - 1];
  const now = latest.generatedAt || new Date();
  const nfb = [...latest.following].filter(u => !latest.followers.has(u)).sort();
  const fans = [...latest.followers].filter(u => !latest.following.has(u)).sort();
  const mutuals = [...latest.following].filter(u => latest.followers.has(u)).sort();

  const rows = [];
  for (const u of [...latest.following, ...latest.followers].sort()) {
    const inFo = latest.following.has(u), inFl = latest.followers.has(u);
    const followedOn = latest.followDates.get(u) || null;
    const age = followedOn ? Math.floor((now - followedOn) / DAY) : null;

    // Did they ever appear as a follower in any snapshot we hold?
    let lastSeenFollowing = null;
    for (const s of snaps) if (s.followers.has(u)) lastSeenFollowing = s.date;
    const ever = lastSeenFollowing !== null;
    let leftBetween = null;
    if (ever && !inFl) {
      const i = snaps.findIndex(s => s.date === lastSeenFollowing);
      if (i + 1 < snaps.length) leftBetween = `${lastSeenFollowing} .. ${snaps[i + 1].date}`;
    }

    // A rename makes a handle vanish from your own following list and come
    // back under a new name. Continuity since the follow began rules it out —
    // asking "present in every snapshot" instead would wrongly fail everyone
    // you simply started following later.
    let continuity = "n/a";
    if (followedOn && inFo) {
      const expected = snaps.filter(s => new Date(s.date) >= followedOn);
      continuity = expected.length === 0 ? "n/a"
        : expected.every(s => s.following.has(u)) ? "continuous" : "gap";
    }

    let tier = null;
    if (inFo && !inFl) {
      if (ever) tier = "dropped";
      else if (age !== null && age <= 30) tier = "too_recent";
      else if (continuity === "gap") tier = "renamed";
      else if (age !== null && age > 365) tier = "confirmed";
      else tier = "probable";
    }
    rows.push({ username: u, mutual: inFo && inFl, youFollow: inFo, followsYou: inFl,
                followedOn, age, ever, leftBetween, continuity, tier });
  }

  return { rows, counts: { nfb: nfb.length, fans: fans.length, mutuals: mutuals.length,
                           following: latest.following.size, followers: latest.followers.size },
           blocks: detectBlocks(snaps) };
}

/**
 * Instagram logs the follows YOU removed. A follow that disappeared from your
 * following list with no matching log entry was removed by someone else, which
 * is what a block leaves behind. It is a strong signal, never proof: a block is
 * invisible to a logged-out visitor, and a deleted account looks the same.
 */
export function detectBlocks(snaps) {
  const log = snaps[snaps.length - 1].unfollowLog;
  if (!log || log.size === 0) {
    return { usable: false,
             why: "No 'recently unfollowed' log in this export, so departures you caused "
                + "cannot be separated from departures caused by someone else.",
             candidates: [] };
  }
  // Only windows the log actually covers can be judged.
  const oldest = [...log.values()].reduce((a, b) => a < b ? a : b);
  const out = [];
  for (let i = 0; i < snaps.length - 1; i++) {
    const a = snaps[i], b = snaps[i + 1];
    if (new Date(a.date) < oldest) continue;
    for (const u of a.following) {
      if (b.following.has(u) || log.has(u)) continue;
      const wasMutual = a.followers.has(u);
      out.push({ username: u, window: `${a.date} .. ${b.date}`, wasMutual,
                 alsoLostFollower: wasMutual && !b.followers.has(u) });
    }
  }
  return { usable: true, coveredFrom: oldest, candidates: out };
}

export function toCSV(rows) {
  const cols = ["username","relationship","verdict","you_followed_on","days_following",
                "ever_followed_you","left_between","handle_continuity"];
  const esc = v => {
    const s = v === null || v === undefined ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const line = r => [r.username,
    r.mutual ? "mutual" : r.youFollow ? "you_follow_only" : "they_follow_only",
    r.tier ? TIERS[r.tier].label : "",
    r.followedOn ? r.followedOn.toISOString().slice(0, 10) : "",
    r.age ?? "", r.ever, r.leftBetween || "", r.continuity].map(esc).join(",");
  return [cols.join(","), ...rows.map(line)].join("\n");
}
