# Web testing — LG-01k (top nav modes)

Date: 2026-05-29
Branch: `lg-01k-top-nav-modes`
Scope: smoke-test the LG-01k 3-mode topnav restructure (`start` / `sandbox` / `league`).

## Severity legend
- 🔴 critical — broken feature, data loss, crash
- 🟠 warning — visible bug, no data loss
- 🟡 minor — cosmetic, copy nit
- 🔵 environment — host/cache/tooling, not the code under test
- ✅ verified working

## Summary

| Severity | Surface | Finding |
|---|---|---|
| ✅ | `GET /` (start mode) | Topnav contains ONLY `tools-nav-link` + `help-nav-link`. Tools at HTML offset 667, Help at 1398 → Tools-before-Help confirmed. No `dashboard-nav-link`, `league-nav-link`, `team-nav-link`, `players-nav-link`, `stats-nav-link`, `leagues-nav-link` (retired), or `player-list-nav-link` ids present. 4 Tools child items + 6 Help child items all present. |
| ✅ | `GET /teams/` (sandbox mode) | Topnav contains exactly 6 flat hrefs in pinned order: `/teams/`, `/players/`, `/matches/`, `/matches/simulate-batch/`, `/teams/create/`, `/maps/`. LG-01a `player-list-nav-link` id preserved on Players link. Tools (offset 603) before Help (1334). No `League ▾` text, no `leagues-nav-link`, no `league-nav-link`/`team-nav-link`/`players-nav-link`/`stats-nav-link` ids. |
| ✅ | `GET /leagues/` (league mode) | `dashboard-nav-link` present with text content `⌂` (U+2302) and `href="/leagues/19/"` (the session-pinned League). All 4 section dropdown toggles (`league-nav-link`, `team-nav-link`, `players-nav-link`, `stats-nav-link`) render. Tools (offset 8983) before Help (9714). No retired `leagues-nav-link`. |
| ✅ | League ▾ dropdown items (6) | Pinned order verified: Standings → `/seasons/18/standings/` (`topbar-league-standings`), Schedule → `/seasons/18/schedule/` (`topbar-league-schedule`), Playoffs → `/leagues/19/playoffs/` (`topbar-league-playoffs`), Finances → `/leagues/19/finances/` (`topbar-league-finances`), History → `/leagues/19/history/` (`topbar-league-history`), Power Rankings → `/leagues/19/power-rankings/` (`topbar-league-power_rankings` — note underscore matching the helper's `key="power_rankings"`). All LIVE (no disabled spans). |
| ✅ | Team ▾ dropdown items (4) | Pinned order verified: Roster → `topbar-team-roster`, Schedule → `topbar-team-schedule_team` (`_team` suffix per LG-01g key-collision rule), Finances → `topbar-team-finances_team`, History → `topbar-team-history_team`. All LIVE. |
| ✅ | Players ▾ dropdown items (6) | Pinned order verified: Free Agents, Trade, Trading Block, Prospects, Watch List, Hall of Fame. Ids `topbar-players-free_agents` / `_trade` / `_trading_block` / `_prospects` / `_watch_list` / `_hall_of_fame`. All LIVE. |
| ✅ | Stats ▾ dropdown items (6) | Pinned order verified: Game Log, League Leaders, Player Ratings, Player Stats, Team Stats, Statistical Feats. Ids `topbar-stats-game_log` / `_league_leaders` / `_player_ratings` / `_player_stats` / `_team_stats` / `_statistical_feats`. All LIVE. |
| ✅ | `GET /leagues/19/` (dashboard target) | Home icon `href` resolves to itself (`/leagues/19/`); page returns 200; `⌂` text content confirmed; all 4 section toggles render. Page title `"sample — League"` confirms `league_dashboard` view rendered. |
| ✅ | Section dropdown sourcing | All 22 in-dropdown ids match the `topbar-{section}-{key}` pattern (the seam contract's mirror of LG-01f `sidebar-{section}-{key}`). The top Dashboard entry of `top_bar_links` is filtered out of the regrouped iteration — no `topbar-top-dashboard` id present. |
| ✅ | Console | Zero console messages across all 4 pages (`/`, `/teams/`, `/leagues/`, `/leagues/19/`). |
| ✅ | Network | All page GETs return 200. Only external requests are the 2 Bootstrap 5.3.0 CDN assets (CSS + JS bundle, both 200). |
| ✅ | Tools-before-Help order | Confirmed on all 3 modes via HTML offset comparison. |

## Test data created during this session

None. LG-01k is a nav-only restructure — no DB writes by the smoke test (no forms submitted, no League / Season / Team / Match creation). Pre-existing dev-DB data (League 19 "sample", visible via the dashboard icon target) is unmodified.

## Flows NOT exercised

- **Disabled dropdown entries**: The current dev DB has a League with `last_league_id` pinned and an active Season, so every LEAGUE / TEAM / PLAYERS / STATS entry is LIVE. The disabled-entry branch (`<span class="dropdown-item disabled">` when `displayed_season is None`) is covered by `TestLg01kLeagueNavContextProcessor::test_displayed_season_is_none_disables_standings_and_schedule` end-to-end and is rendered correctly by the existing LG-01f sidebar partial template branch which the topbar reuses.
- **2+ Leagues no-session-pin fallback**: would render `top_bar_links == []` and `top_bar_dashboard_url == reverse("league_list")` per the LG-01k processor. Covered by `TestLg01kLeagueNavContextProcessor::test_top_bar_links_is_empty_with_two_leagues_no_session_pin` + `test_top_bar_dashboard_url_falls_back_to_league_list`.
- **Mobile responsive layout**: out of LG-01k scope (Bootstrap navbar-toggler collapse behaviour is unchanged from LG-01h; only the inner content shape changed).

## Verdict

LG-01k ships without browser-visible defects. All 3 mode topnav shapes render byte-equal to the seam contract specification. Tools-before-Help confirmed everywhere; `⌂` home icon, all 22 dropdown items, all 4 section toggles, and all retired-id absences confirmed. Zero console errors, zero non-2xx network requests.
