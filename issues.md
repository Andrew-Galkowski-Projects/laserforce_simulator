# Web testing — LG-01z (sidebar placeholder screens)

Date: 2026-05-29
Branch: `lg-01z-sidebar-screens`
Scope: smoke-test the 11 new LG-01z league screens + the blocked explainer pages,
with real league data.

## Severity legend
- 🔴 critical — broken feature, data loss, crash
- 🟠 warning — visible bug, no data loss
- 🟡 minor — cosmetic / pre-existing nit
- 🔵 environment — host/cache/tooling, not the code under test
- ✅ verified working

## Summary

| Severity | Surface | Finding |
|---|---|---|
| ✅ | 11 new screens (live) | All render with real data, no console errors, no 500s |
| ✅ | Blocked explainer pages | Render with `Blocked:` dependency note |
| ✅ | LG-01k topnav + LG-01h sidebar (live wiring) | Every LG-01z entry repointed to its live URL in-browser |
| ✅ | Watch List session GET-toggle | `?action=add&player_id=601` adds the row and redirects |
| ✅ | Responsive (720px) | Navbar collapses; wide tables scroll in `.table-responsive` |
| ✅ | Console (whole session) | Zero error/warn/issue messages |
| 🟡 | Dashboard "View all leaders" link | Pre-existing LG-01c raw-href, out of scope — see PE-1 |

Run context: fresh league via `/leagues/create/` (League 20 / Season 19, 4 teams),
Started + Played One Week (2 rounds) for populated data. Server `127.0.0.1:8060`.

## Per-screen (League 20)

- ✅ **Power Rankings** `/leagues/20/power-rankings/` — 4 teams ranked by composite
  power score; 3 normalized components shown; win% degenerates to 0.000 (no
  completed Matches yet) as documented.
- ✅ **Game Log** `/stats/game-log/` — 2 rows (the 2 played rounds) + team filter.
- ✅ **League Leaders** `/stats/league-leaders/` — 4 boards (`leaders-avg-tags` /
  `-avg-score` / `-fewest-tagged` / `-tag-ratio`), 10 rows each.
- ✅ **Player Ratings** `/stats/player-ratings/` — 23-column sortable table, 10/page.
- ✅ **Player Stats** `/stats/player-stats/` — STAT_KEYS table, 10/page.
- ✅ **Team Stats** `/stats/team-stats/` — 4 teams, all 13 columns incl. event-derived
  (base captures / missiles / nukes / cancelled) populated.
- ✅ **Statistical Feats** `/stats/statistical-feats/` — 6 feats detected (triple_nuke,
  top_mvp, top_score, tag_streak, most_resupplies, most_missiles); medic_shutout /
  perfect_heavy / comeback_win correctly absent (no qualifying data in 2 rounds).
- ✅ **Team Roster** `/team/roster/` — defaults to `current_team` (Aurora Aces), team
  picker, 6 starting + bench.
- ✅ **Team History** `/team/history/` — all 3 tabs render (Overall / Seasons /
  Players), 6 players with green/blue colour flags.
- ✅ **Free Agents** `/players/free-agents/` — sortable list, 10/page.
- ✅ **Watch List** `/players/watch-list/` — empty state + add control; add toggle works.
- ✅ **Blocked explainer** (e.g. `/finances/`) — "Finances — Coming Soon" + blocker note.

## Pre-existing (out of LG-01z scope)

- 🟡 **PE-1** — the LG-01c **dashboard** "View all leaders" anchor renders the raw href
  `/seasons/<id>/leaders/`, which 404s. sub-plan.md flagged LG-01z-m's league-scoped
  URL (`/leagues/<id>/stats/league-leaders/`, now live and reachable via the sidebar)
  as the replacement. The LG-01z-m screen itself is correct; only the old dashboard
  raw-href was not repointed (it has LG-01c tests pinning the literal href, so it's a
  separate small change). Recommended follow-up: point that anchor at
  `{% url 'stats_league_leaders' %}` and update the LG-01c dashboard tests.

## Test data created during this session

- League 20 ("ChromeTest LG01z League") + Season 19 + 4 generated Teams (88–91) +
  their Players, with 2 played GameRounds. Teardown by `TeamNamePrefix=ChromeTest`.

---

# Web testing — LG-06d (season selector + rate toggle)

Date: 2026-06-02
Branch: `lg-06d-season-rate-toggles`
Scope: smoke-test the `?season=` selector (each Season + Career) on the 6
season-derived league stats screens, and the `?rate=` toggle (Totals / Per Game /
Per 10 min) on Player Stats. Read against the pre-existing dev server on
`127.0.0.1:8000` (no test data created — GETs only). League 22 ("Per-League Pool A")
is the only season-scoped league with `PlayerRoundState` data (1 Season → Career ==
Season 1 numerically, which is correct).

## Summary

| Severity | Surface | Finding |
|---|---|---|
| ✅ | All 6 screens (player-stats, team-stats, league-leaders, statistical-feats, game-log, power-rankings) | `*-season-filter-form` + `*-season-filter-select` render with options `[<season id>, career]`; no console errors; all network 200 |
| ✅ | Player Stats rate toggle | `player-stats-rate-select` options `[total, per_game, per_10]`; `?rate=per_10` transforms count columns only and leaves MVP/Acc%/Tag Ratio/Survival untouched |
| ✅ | Per-10 math (per-player uptime denominator) | Wilson Points 13482 (Totals) → 8988 under Per-10 (× 600/900s uptime); Tags 102 → 68; second row used its own 531s uptime denominator — confirms denominator is per-player survival, not a constant |
| ✅ | Sort-on-displayed-value | Per-10 table still ordered by displayed Points desc (8988 > 7173) |
| ✅ | `?season=career` | Power Rankings `?season=career` selects Career and renders 4 scoped rows |
| ✅ | Empty-Season screens (League 19) | Selector still renders (Season 1 + Career); body shows the existing empty-state notice — no crash |
| ✅ | Responsive (720px) | Sidebar + `.table-responsive` table intact; navbar collapses to hamburger <992px as designed |
| ✅ | Console + network (whole pass) | Zero error/warn/issue; all requests 200 (Bootstrap CDN + page) |

Note (not a bug): Power Rankings lives at `/leagues/<id>/power-rankings/` (LEAGUE
section), **not** under `/stats/` like the other five — by design (its sidebar/
topbar entry is in the LEAGUE group). Initial `/stats/power-rankings/` probe 404'd
as expected.

## Test data created during this session

- None. LG-06d is read-only; only GET navigation was performed against the
  already-running dev server. No teardown required.

---