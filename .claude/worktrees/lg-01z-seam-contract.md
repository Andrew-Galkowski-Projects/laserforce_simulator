# LG-01z — Sidebar placeholder screens · consolidated seam contract

Builds real pages for the 17 league-sidebar "coming soon" placeholders
(LG-01z-a..q). 11 substantive screens are built by per-screen agents; 6
blocked screens become enriched explainer pages owned centrally.

All screens are **read-only**, **GET-only**, **no model change, no
migration, no simulator touch, no RNG, no Score Calibration re-baseline**.
Watch List uses the Django session only (no model). Determinism: views run
no simulation and consume no RNG.

---

## 1. Isolation model (zero shared-file edits by agents)

Each agent creates **only NEW files**, all namespaced to its screen key:

- **View module:** `matches/league_screens/<key>.py` — exposes one view
  function `<key>(request, league_id)`. May import Django/ORM. Heavy
  aggregation goes in a sibling **pure module** the agent also creates,
  `matches/<key>_logic.py` (frozen import allowlist: `dataclasses`,
  `typing`, `collections` only — NO Django/ORM/RNG/IO; defended by a
  `TestNoDjangoImportsLeaked` subprocess check mirroring the HX-01 /
  RES-04 precedent). Screens with trivial aggregation may keep logic in
  the view module and skip the pure module.
- **Template:** `templates/leagues/<key>.html` — extends `base.html`,
  renders the league shell `<div class="d-flex">{% include
  "_partials/league_sidebar.html" %}<main>…</main></div>`.
- **Test file:** `matches/tests/test_lg01z_<key>.py`.

**Agents must NOT touch:** `matches/league_urls.py`,
`matches/league_views.py`, `matches/views.py`, `_build_league_sidebar_links`,
`_FEATURE_REGISTRY`, `templates/_partials/league_sidebar.html`,
`templates/base.html`, `templates/_placeholder.html`, or any other agent's
files. They may **read** them.

**Central owner (me), after agents land:** create
`matches/league_screens/__init__.py`; add a real `path(...)` route per
screen to `matches/league_urls.py`; repoint each screen's entry in
`_build_league_sidebar_links` from `_cs("coming_soon_<x>")` to
`reverse("<real_name>", …)`; remove the superseded `coming_soon_<x>` routes
+ `_FEATURE_REGISTRY` entries for the 11 live screens; enrich the 6 blocked
`_FEATURE_REGISTRY` entries with a `blocker` note + render it in
`_placeholder.html`.

## 2. Shared view contract (every agent screen)

The view function body, in order:
1. `if request.method != "GET": return HttpResponseNotAllowed(["GET"])`
2. `league = get_object_or_404(League, pk=league_id)`
3. `request.session["last_league_id"] = league.id`
4. `displayed_season = league.active_season or
   league.seasons.filter(state="completed").order_by("-id").first()`
5. `sidebar_links = _build_league_sidebar_links(league, displayed_season,
   sidebar_active="<this screen's key>")` — import from
   `matches.league_views`.
6. screen-specific aggregation.
7. `return render(request, "leagues/<key>.html", {"league": league,
   "displayed_season": displayed_season, "sidebar_links": sidebar_links,
   "sidebar_active": "<key>", … screen context …})`.

If `displayed_season is None` (League has no Season), render an empty-state
notice (DOM id `<key>-empty-notice`, substring `"No Season"`) instead of
the body — the sidebar still renders.

**URL shape:** all routes are `/leagues/<int:league_id>/…` (central owner
adds them, reusing the existing placeholder paths verbatim). Team-scoped
screens resolve the Team via an optional `?team_id=<id>` query param,
defaulting to `league.current_team` (use the existing
`_resolve_current_team_for_sidebar(league, displayed_season)` helper as the
default when `current_team` is null).

**Screenshots (MANDATORY):** before building the template, open the
matching PNG in `Screenshots_and_video_examples/` with the Read tool and
match its layout. Suggested file per screen is in §4. If the suggested file
doesn't match, browse the folder and pick the right one.

**Reused helpers** (import, do not reimplement): `compute_leaders`
(`matches/season_dashboard.py`); `compute_standings`
(`matches/standings.py`); LG-00c sort helpers `_coerce_sort` / `_coerce_dir`
/ `_SORT_KEYS` / `_SORT_KEYS_DISPLAY` (`teams/views.py`) for sortable
screens; `_coerce_per_page` / `_coerce_page` (`matches/league_views.py`)
for pagination; `Team.active_players` / `Team.bench_players` /
`Player.overall_rating` (`teams/models.py`); `PlayerRoundState.get_mvp` /
`get_accuracy` properties.

**Time display:** ticks → seconds is `÷2` at the HTML boundary only
(TIME-01). Wall-clock `date_played` uses `|date:"Y-m-d"`.

## 3. Locked URL/view/template names (central owner wires routes)

| key | URL name | path | view module |
|-----|----------|------|-------------|
| b power_rankings | `league_power_rankings` | `/leagues/<id>/power-rankings/` | `league_screens/power_rankings.py` |
| l game_log | `stats_game_log` | `/leagues/<id>/stats/game-log/` | `league_screens/game_log.py` |
| m league_leaders | `stats_league_leaders` | `/leagues/<id>/stats/league-leaders/` | `league_screens/league_leaders.py` |
| n player_ratings | `stats_player_ratings` | `/leagues/<id>/stats/player-ratings/` | `league_screens/player_ratings.py` |
| o player_stats | `stats_player_stats` | `/leagues/<id>/stats/player-stats/` | `league_screens/player_stats.py` |
| p team_stats | `stats_team_stats` | `/leagues/<id>/stats/team-stats/` | `league_screens/team_stats.py` |
| q statistical_feats | `stats_statistical_feats` | `/leagues/<id>/stats/statistical-feats/` | `league_screens/statistical_feats.py` |
| c team_roster | `team_roster` | `/leagues/<id>/team/roster/` | `league_screens/team_roster.py` |
| e team_history | `team_history` | `/leagues/<id>/team/history/` | `league_screens/team_history.py` |
| f free_agents | `players_free_agents` | `/leagues/<id>/players/free-agents/` | `league_screens/free_agents.py` |
| j watch_list | `players_watch_list` | `/leagues/<id>/players/watch-list/` | `league_screens/watch_list.py` |

Blocked (central, no agent): `league_finances`, `team_finances`,
`players_trade`, `players_trading_block`, `players_prospects`,
`players_hall_of_fame` — stay on `coming_soon` + `_placeholder.html` with a
new `blocker` note.

## 4. Per-screen specs

### b · Power Rankings  (screenshot: `league_power_rankings_view.png`)
Pure module `matches/power_rankings_logic.py`. Power score = sum of THREE
min-max-normalized (per-League, to [0,1]) components: (1) team mean
`overall_rating` over `active_players`; (2) win% from completed Matches
(`compute_standings` wins/(wins+losses+ties), 0 when none); (3) avg score
diff per Round (mean of `red_points-blue_points` from the team's
perspective). Highest sum = rank #1. Show rank, team, the 3 component
values, and the composite. Ties broken by team name asc. Only teams
enrolled in `displayed_season`. DOM: `power-rankings-table`,
`power-rankings-row-{team_id}`, `power-rankings-empty-notice`.

### l · Game Log  (screenshot: `league_game_log.png`)
View-only. One row per played `GameRound` in `displayed_season`
(chronological), columns: matchday, date, red team, blue team, score,
winner. Optional `?team_id=` Team filter (dropdown of enrolled teams; bad
id ignored). Each row deep-links to the Round detail
(`/matches/game-round/<id>/`). DOM: `game-log-table`,
`game-log-row-{round_id}`, `game-log-team-filter`, `game-log-empty-notice`.

### m · League Leaders  (screenshot: `leauge_stat_leaders.png`)
Extend `matches/season_dashboard.py::compute_leaders` with new stat verbs:
`avg_tags` (mean tags_made), `avg_score` (mean points_scored),
`fewest_tagged` (mean times_tagged ASC — least-tagged leads),
`tag_ratio` (existing). Render FOUR top-10 leaderboards (one per verb) over
all players in `displayed_season`'s completed Rounds. Each leader links to
their career page. Reuse the `LeaderRow` dataclass; the extension preserves
the existing 3 verbs + `TestNoDjangoImportsLeaked`. DOM:
`leaders-avg-tags` / `leaders-avg-score` / `leaders-fewest-tagged` /
`leaders-tag-ratio`, `leaders-empty-notice`.

### n · Player Ratings  (screenshot: `league_player_stats.png` — confirm vs `player_detail.png`)
View-only, reuse the LG-00c sortable pattern (`_coerce_sort` / `_coerce_dir`
/ `_SORT_KEYS`, `?sort=&dir=` forgiving fallback, pagination via
`_coerce_per_page`/`_coerce_page`). Scope: players on teams enrolled in
`displayed_season`. Columns: name, team, 19 stat ratings + `overall_rating`.
Server-side sort. DOM: `player-ratings-table`,
`player-ratings-th-{key}`, `player-ratings-empty-notice`.

### o · Player Stats  (screenshot: `league_player_stats.png`)
Pure module `matches/season_player_stats.py`. Per-player PERFORMANCE
aggregated across `displayed_season`'s Rounds — the HX-01 STAT_KEYS set
(`points_scored, mvp, tags_made, times_tagged, accuracy, final_lives,
resupplies_given, missiles_landed, specials_used, follow_up_shots,
reaction_shots, combo_resupply_count`). Sortable (LG-00c pattern over these
keys). `mvp` via `get_mvp`, `accuracy` via `get_accuracy`. Scope: players in
enrolled teams. DOM: `player-stats-table`, `player-stats-th-{key}`,
`player-stats-empty-notice`.

### p · Team Stats  (screenshot: confirm — likely none; model on `league_player_stats.png` table style)
Pure module `matches/team_stats_logic.py`. Per-team aggregates over
`displayed_season`'s Rounds + GameEvents: avg points-for, avg
points-against, avg margin, avg survivors (final_lives>0), total tags
landed, total times tagged, base captures, missiles fired, missiles hit,
nukes fired, nukes landed, cancelled-nuke count. Event-derived columns scan
`GameEvent` (`base_capture`, `locking`/`missiled`, `special` nuke
detonation, `nuke_cancelled`). Sortable columns. Enrolled teams only. DOM:
`team-stats-table`, `team-stats-row-{team_id}`, `team-stats-empty-notice`.

### q · Statistical Feats  (screenshots: `league_player_statistic_feats.png`, `league_player_single_game_statistical_feats.png`, `league_team_statistic_feats.png`)
Pure module `matches/stat_feats.py`, one predicate function per feat over a
list of per-Round dicts (PlayerRoundState + that Round's GameEvents). Ship:
triple-nuke game, Medic 0-tagged shutout (times_tagged==0), perfect-accuracy
Heavy round (shots_missed==0 & tags_made>0), highest single-game MVP,
highest single-game score, longest tag streak, most resupplies in a round,
most missiles landed, comeback win. Each feat record: kind, label, player or
team name, value, round_id (deep-link to Round). Scope: `displayed_season`.
DOM: `stat-feats-list`, `stat-feats-empty-notice`, per-feat
`stat-feat-{kind}`.

### c · Team Roster  (screenshot: `league_roster_view.png`)
View-only. Team via `?team_id=` (default `league.current_team` →
`_resolve_current_team_for_sidebar`). Team-picker dropdown of enrolled
teams. Show starting six (`Team.active_players` / `slot_*`) and bench
(`Team.bench_players`); each player links to their career page. DOM:
`roster-team-picker`, `roster-starting-table`, `roster-bench-table`,
`roster-empty-notice`.

### e · Team History  (screenshot: `league_team_history_view.png`)
**3-tab page** (Bootstrap tabs; tab state client-side). Pure module
`matches/team_history_logic.py`.
- **Overall tab:** the team's all-time W-L-T across every Season it played
  (round-level record from completed Matches), playoff appearances
  (placeholder count = 0 until LG-02), championships (count of Seasons where
  `champion_team == team`).
- **Seasons tab:** one row per Season the team enrolled in (derived from
  `Season.starting_team_ids_json` containing the team id): year
  (`start_date.year`), record that season, final standing/rank.
- **Players tab:** every Player who has appeared for the team (derived from
  `PlayerRoundState` rows in the team's GameRounds). Colour: **green** =
  currently on the team (`Player.team == team`); **blue** = now on another
  team (`Player.team != team`); retired/Hall-of-Fame = deferred (no such
  flag yet — render none). Per player: total games played on the team,
  career-long aggregate stats, last season played. Each links to career
  page.
Team via `?team_id=` default `current_team`. DOM:
`team-history-tabs`, `team-history-overall`, `team-history-seasons`,
`team-history-players`, `team-history-empty-notice`.

### f · Free Agents  (screenshot: `league_free_agent_view.png`)
View-only. Sortable list (LG-00c pattern) of free agents = players whose
team is the "Free Agents" pool team OR not enrolled in `displayed_season`.
Each player links to their career/player page. No sign action (deferred with
the cap model). DOM: `free-agents-table`, `free-agents-th-{key}`,
`free-agents-empty-notice`.

### j · Watch List  (screenshot: `league_watch_list.png`)
**Session-scoped** (no model, no migration). Store a list of player ids in
`request.session["watch_list"]` (keyed globally, not per-League — note in
the template it's a browser-local list). Add/remove via POST to a small
companion endpoint... **EXCEPTION to GET-only:** Watch List needs an
add/remove action. Use a single `?action=add|remove&player_id=<id>` GET
toggle that mutates the session then redirects back (`POST` would need CSRF
plumbing; GET toggle is acceptable for a session-local convenience list —
note this in the contract). Render the watched players (links to player
page) + a remove button each, and an add-by-search/select control listing
players. DOM: `watch-list-table`, `watch-list-row-{player_id}`,
`watch-list-add`, `watch-list-empty-notice`.

## 5. Tests
Each screen: `matches/tests/test_lg01z_<key>.py` (Django `TestCase`) —
200 + sidebar rendered + `sidebar_active` correct + the screen's locked DOM
ids + empty-state (no Season) + 405 on POST (except Watch List) + 404 on
bad league_id. Pure-module screens add a pure-unit class + the
`TestNoDjangoImportsLeaked` subprocess check. Tests must NOT touch
`simulate_*` / `save_games`. Hand-construct `Match`/`GameRound`/
`PlayerRoundState` fixtures (LG-01c precedent). Use small fixtures, fixed
data — no unseeded simulation.
