# matches/

Match creation, game-round simulation, event logging, result views, and everything League-shaped:
Seasons, phases, tournaments, Conferences, career mode, finance, and the `/api/` surface for all of it.

This file is the **index**. Feature detail lives in `docs/agents/matches/` — read the doc a pointer
names before touching that feature, and add new feature notes there, not here.

## Model hierarchy (brief)

```
League (Ownership root; `manager` FK)
  └── Season (draft → active → completed; teams M2M, frozen `starting_*_json` snapshots)
        ├── SeasonPhase (ordered: round_robin / tournament / member_night)
        ├── Conference (optional disjoint partition of the Season's Teams)
        └── Match (2 rounds; winner by rounds then points)
              └── GameRound (one 1800-tick simulation)
                    ├── PlayerRoundState (one per player: resources, stats)
                    └── GameEvent (chronological action log)
Tournament (bracket; standalone sandbox, or embedded via SeasonPhase)
  └── BracketNode → SeriesMatch → Match
```

Sandbox `Match` / standalone `GameRound` / `Tournament` / `League` carry `manager`; everything else
derives ownership from its parent (see `accounts-ownership.md`).

## Where things live

- `models.py` — every model above plus `PlayerSeasonRating`, `OwnerEvaluation`, `TeamSeasonFinance`, `TournamentPlayerEntry`, `WatchedPlayer`.
- `simulation/` — `BatchSimulator`, the sole engine: `round_loop.py` (per-tick), `entrypoints.py` (simulate/run/save/replay), `persistence.py` (`flush_to_db`).
- `sim_helpers/` — pure engine helpers (see Sub-packages).
- `views.py` — sandbox match / round / batch / analytics HTML views.
- `league_views.py` — League + Season HTML views, play loop, rollover, owner evaluation.
- `league_screens/` — one module per League sidebar screen (thin; logic in `league_screen_services.py`).
- `tournament_views/` — sandbox Tournament HTML views, one module per area (`list_create`, `detail`, `setup`, `draw`, `play`); thin, logic in `tournament_services/`.
- `tournament_services/` (ANG-01f) — shared HTML + API Tournament logic: `errors`, `detail` (bracket / standings builders, the API payloads), `seeding`, `create`, `setup`, `draw`, `play`. `tournament_engine.py` — the per-node bracket drain.
- `tournament_api_views/` (ANG-01f) — `/api/tournaments/`: `base` (`TournamentViewSet`), `errors` (the `APIException`s + `translate_tournament_errors`), and the `create` / `setup` / `draw` / `play` action mixins.
- `tasks.py` — Celery tasks (batch, save, play season / playoffs / tournament / member night).
- Pure modules (Django-free, `TestNoDjangoImportsLeaked`-guarded): `bracket.py`, `standings.py`, `schedule_generator.py`, `season_dashboard.py`, `phase_composer.py`, `draw.py`, `member_night.py`, `worlds.py`, `development.py`, `finance.py`, `injury.py`, `owner_mood.py`, `season_awards.py`, `h2h_stats.py`, `player_h2h_stats.py`, `stat_feats.py`, `*_logic.py`.
- `api_views.py`, `league_api_views.py`, `league_screen_api_views.py`, `tournament_api_views/`, `serializers.py`, `filters.py`, `api_forms.py`, `api_refs.py` — the REST API.

## Service layer

Shared by the HTML views and the API; services must not import the view modules that import them.

- `matches/services.py` (ANG-01b) — sandbox Match/Round creation, batch-team resolution, round / head-to-head analytics builders.
- `matches/league_services.py` (ANG-01d-1) — League create / next-season rollover / reassign / teardown, League + Season read builders (dashboard, history, standings, schedule, awards, owner evaluation), Conference replace. Moved helpers are re-exported from `league_views.py`: patch `matches.league_services.*`, not `league_views`.
- `matches/league_play_services.py` (ANG-01d-2) — the League play loop and single active play run, shared by the HTML play views and the `/api/seasons/` play endpoints.
- `matches/league_screen_services.py` (ANG-01e) — the builders for all 15 League screens, enrolment, the per-Account `WatchedPlayer` watch list, team finances; shared by `league_screens/*` and `/api/leagues/<id>/…`. Patch `matches.league_screen_services.*`.
- `matches/tournament_services/` (ANG-01f) — sandbox Tournament create / seeds / lock / CSV import / Player pool / Draw / play, the strict Forms both surfaces bind, the embedded / setup / active guards and the single active play run; shared by `tournament_views/*` and `/api/tournaments/`. No `__init__` re-exports and no view imports: import from each submodule, and patch `matches.tournament_services.<module>.*` (the moved view helpers were deleted, not re-exported).
- `teams/services.py` — Team/Player writes plus HX-01 career / HX-02 benchmark builders.
- `core/services.py` (ANG-01c) — the map editor behind `/api/maps/`; maps stay shared, unfiltered data.

## API conventions (project-wide)

Every `/api/` endpoint in every app follows
[ADR-0039](../../docs/adr/0039-rest-api-envelope-and-form-bound-writes.md). The machinery lives in
`laserforce_simulator/api/` and is wired through `REST_FRAMEWORK` settings, not per view. Full text
with every example: `docs/agents/matches/api.md`.

- **Envelope.** `{"data", "meta"}` on success; paginated lists put `{page, page_size, count, total_pages}` in `meta`; errors are `{"errors": [{"code", "message", "field"}], "meta"}` with `field` null for non-field errors. Exempt: 204, raw file responses (`HttpResponse` / `FileResponse`), unhandled 500s.
- **Writes bind the existing Django `Form`.** Serializers are output-only. POST binds `plain_form_data(...)`, PATCH `merged_form_data(...)` (merges over the instance), PUT a full dict; then `validate_form_or_raise(form)`. **Views raise; they never build a 4xx `Response`.** No HTML Form for a write ⇒ add an API-only Form, not a serializer. Validated query params bind a strict param Form (`api_forms.py`).
- **Status codes.** 400 = the Form / filter error code verbatim (`parse_error` for bad JSON). 404 `not_found` for a missing row, **a cross-Account row (never 403)**, or a bad page. 401 for anonymous (`WWW-Authenticate: Bearer`, ADR-0041); 403 for CSRF. 405 keeps `Allow`. 409 = a raised `APIException` subclass for a state conflict. 415 for a wrong media type.
- **Filtering.** `django-filter` with an explicit `filterset_class` per viewset plus `AliasOrderingFilter` (`?ordering=a,-b`, unknown terms dropped, tiebreak appended). A typed filter with a bad value ⇒ 400 with `field` = the param. Every viewset mixes in `ListOnlyFilterMixin` first.
- **Ownership.** `get_queryset()` builds on `owned_queryset` / `owned_match_q` / `owned_game_round_q`; DRF `get_object()` then 404s cross-Account. Viewsets keep `login_not_required` on `dispatch` plus settings-level `IsAuthenticated`. `ArenaMap` is shared data, the one exception.
- **CORS (ANG-02).** Only `/api/` answers cross-origin, with credentials, for `CORS_ALLOWED_ORIGINS`. That list comes from the env; it defaults to `http://localhost:4200` under `DEBUG` and is empty otherwise, and it is also `CSRF_TRUSTED_ORIGINS`. `CorsMiddleware` stays first in `MIDDLEWARE`.
- **JWT (ANG-03).** `JWTAuthentication` is first and `SessionAuthentication` second, so either works; anonymous is 401. Login / refresh / logout live at `/api/token/` (`accounts/token_api_views.py`): the access token is in the body, the refresh token only in the httpOnly `refresh_token` cookie scoped to `/api/token/` ([ADR-0041](../../docs/adr/0041-jwt-api-auth-with-httponly-refresh-cookie.md)).

## Tests

Test files live in `matches/tests/` (one file per feature, e.g. `test_season_playoffs.py`,
`test_league_<screen>.py`); pure-module tests are `SimpleTestCase` plus `TestNoDjangoImportsLeaked`.
Assert schema-level outcomes (row counts, states, ids, DOM ids), never raw simulated point totals.
Inventory: `docs/agents/matches/tests.md`.

## Feature docs (`docs/agents/matches/`)

Paths below are relative to the repo root.

- Engine, `BatchSimulator`, models' field history, TIME-01 ticks, roles, shot resolver, EventLog, Score Calibration, GEN-01 fidelity tiers — `docs/agents/matches/simulation-engine.md`
- REST API endpoints, ANG-01b match/round actions, ANG-06c team / winner names + Match totals on the match/round serializers and the `?single_round=` filter, ANG-01d-1 league lifecycle API, ANG-01e league screens API, ANG-01f tournaments API, ANG-02 CORS, ANG-03 JWT auth / token endpoints, full API conventions — `docs/agents/matches/api.md`
- URL table (`/matches/`, `/api/`) — `docs/agents/matches/urls.md`
- Celery, batch jobs, Job status, polling JSON (API-03), task connection cleanup + close-in-transaction test guard (CI-01) — `docs/agents/matches/async-celery.md`
- Round pages, event log windowing, playback map, heatmap, round compare, highlights, PDF report + charts (RV-03, RV-05), head-to-head — `docs/agents/matches/round-views-and-analytics.md`
- League / Season models, schedule, standings, create League, dashboards, play season, next season, League history, CAR-01 — `docs/agents/matches/league-foundation.md`
- Topnav modes, `Play` dropdown, live stats + Stop, One Week (Live) — `docs/agents/matches/nav-and-play.md`
- Season map modes, map pool, rotate_by_matchday (LG-01j, SUB-01) — `docs/agents/matches/map-modes.md`
- League sidebar screens, watch list, watch flag, standings form, League player page (LG-01z, LG-06f..h) — `docs/agents/matches/league-screens.md`
- Delete League, League templates, difficulty, League spread (DEL-01, CRE-01, CRE-02) — `docs/agents/matches/league-lifecycle.md`
- Single / double elimination, best-of-N Series, Series escalation, CSV import, play-all — `docs/agents/matches/tournaments-elimination.md`
- Round robin, RR → double elimination, Swiss, Player-pool draw / Random Draw — `docs/agents/matches/tournaments-rr-swiss-draw.md`
- Season phases, phase composer, RR → playoff embed, multi-round-robin (Part2a..2c-2) — `docs/agents/matches/season-phases.md`
- Double round robin, tournament_mode, mid-season tournaments, per-tournament config, finals formats, playoff pacing (Part2c-3a..f) — `docs/agents/matches/season-phases-part2c-3.md`
- Member night (LG-07a) — `docs/agents/matches/member-night.md`
- Season awards, player development, ratings history, potential (LG-03..05) — `docs/agents/matches/awards-and-development.md`
- Owner mood firing, career isolation, finance, budgets, injuries, luxury tax (CAR-02/03, FIN-01..05) — `docs/agents/matches/career-and-finance.md`
- Conferences, regional playoffs, manage conferences, rotate_by_conference (CONF-01/02/05/06) — `docs/agents/matches/conferences.md`
- Worlds qualification, last-chance qualifier, Worlds phase (CONF-03/04) — `docs/agents/matches/worlds.md`
- `manager` ownership roots, `League.visibility`, Self-insert badges (UX-01, UX-02) — `docs/agents/matches/accounts-ownership.md`

## Sub-packages

- [`sim_helpers/CLAUDE.md`](sim_helpers/CLAUDE.md) — `BatchSimulator` helper modules: `PlayerState` dataclass, action weights, pathfinding, `mechanics.py` (pure game mechanics), `combat.py` (shared combat resolution), `role_constants.py` (canonical role stats), `score_calculator.py` (MVP formula), `map_context.py` (typed map wrapper), `map_loader.py` (map-loading helpers extracted from RBS by SIM-09), `pending_events.py` (typed pending-queue dataclasses), `spawn_assigner.py` (spawn logic)
- [`management/commands/CLAUDE.md`](management/commands/CLAUDE.md) — `score_averages`, `game_analysis` and `seed_browser_check` (browser-check data) management commands
