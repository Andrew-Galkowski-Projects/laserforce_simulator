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
