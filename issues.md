# Web testing — LG-06e (Statistical Feats per-game feed)

Date: 2026-06-02
Branch: `lg-06e-statistical-feats-feed`
Scope: smoke-test the reshaped Statistical Feats screen (per-(Player,Round) feed)
plus the surrounding league screens, with real league data.

## Severity legend
- 🔴 critical — broken feature, data loss, crash
- 🟠 warning — visible bug, no data loss
- 🟡 minor — cosmetic / pre-existing nit
- 🔵 environment — host/cache/tooling, not the code under test
- ✅ verified working

## Summary

| Severity | Surface | Finding |
|---|---|---|
| ✅ | Statistical Feats feed (League 22, Season 1, 23 feat rows) | Table renders: 21 sortable headers, feat badges, Opp/Result/Season, deep-links — no console errors, all 200 |
| ✅ | Feat badges | `high_tags`/`high_resupplies`/`high_missiles` etc. render; season-best badge styled distinctly ("Missiles (season best)", `bg-warning` + `season-best` class) |
| ✅ | Default sort | Most-recent-first (round_id desc) — round 132 rows lead; bug fixed pre-smoke (see RESOLVED-1) |
| ✅ | Sort (all columns) | `?sort=points_scored&dir=desc` → [13482,9722,9662…]; `dir=asc` → [1841,2002…]; invalid `?sort=BOGUS` falls back to round-desc |
| ✅ | Pagination + page-size | `per_page=10` → "Page 1 of 3"; `per_page=25` → 23 rows, nav omitted (single page) |
| ✅ | Season + Career filter | `?season=career` aggregates this League's Seasons (1 season → 23 rows) |
| ✅ | Team filter | `?team_id=98` (Aurora Aces #9) → 5 rows, all that team |
| ✅ | Empty state | League 19 (no games played) → `stat-feats-empty-notice` "No statistical feats recorded yet in this Season." |
| ✅ | Sidebar / main pages | `/`, `/teams/`, `/matches/`, league dashboard/history/player-stats/league-leaders/game-log all 200, no console errors |
| 🟡 | Feat badge as CSS class not DOM id | Contract pinned id `stat-feat-badge-<kind>`; Code used class `stat-feat-badge-<kind>` + `season-best`. Sound deviation — ids must be unique, many rows share a kind. Tests pass against the class substrings. No fix needed — see DEV-1 |
| 🟡 | Wide table overflows page on mobile (720px) | Feats 21-col table → docScrollW 1794 > 720. Pre-existing cross-cutting pattern: Player Stats (LG-06d) also overflows (1365). Not an LG-06e regression — out of scope. See PE-1 |
| 🔵 | Stale runserver served old template | A pre-edit `runserver` (pid 19004) held :8000; first smoke pass showed the OLD template. Killed + restarted clean. Tooling artifact, not code. See ENV-1 |

Run context: existing seeded data — League 22 "Per-League Pool A" (Season 1, 13 teams,
2 rounds played → 23 feat rows). Server `127.0.0.1:8000`.

## Notes

- **RESOLVED-1** (was 🟠, fixed during triage): the view defaulted to `round` **asc**
  because `_coerce_dir(request.GET.get("dir"))` uses `teams.views._coerce_dir`'s
  built-in `default="asc"`, contradicting the locked default `("round","desc")`.
  Two view tests caught it (`test_default_order_is_round_desc`,
  `test_invalid_sort_falls_back_to_round_default`). Fixed at
  `matches/league_screens/statistical_feats.py` by passing the `"desc"` default:
  `direction = _coerce_dir(request.GET.get("dir"), "desc")`. Full suite green after.
- **DEV-1**: feat badges render as `<span class="badge ... stat-feat-badge stat-feat-badge-<kind> [season-best]">`
  (class, not id). Defensible — a page has many badges of the same kind; ids must be
  unique. Tests assert the class substrings. Accepted, no change.
- **PE-1**: wide league stats tables (`d-flex` sidebar + `.table-responsive`) push the
  document past the viewport on narrow screens; the wrapper doesn't bound the flex
  `main`. Shared by Player Stats / Game Log / League Leaders — a cross-cutting
  responsive nit, not introduced by LG-06e. Candidate for a future responsive pass.
- **ENV-1**: not a code bug. A leftover dev server from before this branch's edits was
  serving stale code; resolved by killing pid 19004/8136 and restarting.

---

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