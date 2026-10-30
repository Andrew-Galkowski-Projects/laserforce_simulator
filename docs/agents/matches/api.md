# matches: REST API

The full API conventions (ADR-0039), the endpoint table, and the ANG-01b / ANG-01d-1 / ANG-01e / ANG-01f slices with their service modules.
Moved verbatim from `laserforce_simulator/matches/CLAUDE.md`; the app index there points here.

## API conventions (ANG-01a, project-wide)

Every `/api/` endpoint in every app follows
[ADR-0039](../../adr/0039-rest-api-envelope-and-form-bound-writes.md); the machinery lives in the
`laserforce_simulator/api/` package (beside `settings.py`) and is wired through `REST_FRAMEWORK`
settings, **not per view**. New API slices (ANG-01b..g) must follow it. Seam contract:
[`.claude/worktrees/ang-01a-seam-contract.md`](../../../.claude/worktrees/ang-01a-seam-contract.md).

- **Envelope.** Success `{"data": <obj|list>, "meta": {}}`; paginated lists put
  `{page, page_size, count, total_pages}` in `meta` (empty list ⇒ `count: 0, total_pages: 1, page: 1`);
  errors `{"errors": [{"code", "message", "field"}], "meta": {}}`, `field` null for non-field errors,
  never an empty list. Exempt: **204** (empty body — player DELETE, ANG-01c map DELETE), raw file
  responses built as a Django `HttpResponse` / `FileResponse` (the roster template CSV, the ANG-01b
  round report PDF, the ANG-01c map `processed-image/` PNG), and unhandled **500**s (Django default).
- **Modules (`laserforce_simulator/api/`).** `renderers.EnvelopeJSONRenderer` (default renderer;
  the browsable API shows the envelope too), `pagination.EnvelopePagination` (`page_size` 20,
  `?page_size=` clamped to 100, invalid / out-of-range `?page=` ⇒ 404 `not_found`),
  `exceptions.envelope_exception_handler` + `EnvelopeValidationError` + `form_errors_to_envelope` /
  `django_validation_error_to_envelope` / `validate_form_or_raise`, `forms.plain_form_data` /
  `merged_form_data`, `filters.AliasOrderingFilter`, `viewsets.ListOnlyFilterMixin`.
- **Writes bind the existing Django `Form`** (serializers are output-only). A write view binds the
  Form to `plain_form_data(...)` (POST), `merged_form_data(...)` (**PATCH merges over the instance** —
  only sent fields change) or a full dict (**PUT = full replace**), then `validate_form_or_raise(form)`;
  `Model.full_clean()` failures raise `EnvelopeValidationError(django_validation_error_to_envelope(e))`.
  **Views raise; they never hand-build a 4xx `Response`.** API-03's `SimulateBatchRequestSerializer`
  is the one grandfathered input serializer. Where no HTML Form exists for a write, a new API-only
  Form is added instead of a serializer (ANG-01b `SaveBatchGamesForm`). **Read-only query params**
  that need validation bind a strict param Form (`plain_form_data(Form, request.query_params)` →
  `validate_form_or_raise`) — 400 on a bad value, unlike the lenient HTML parsing (ANG-01b
  `matches/api_forms.py`, `teams/api_forms.py`).
- **Status / code table.** 400 — the Form / serializer / filter error code verbatim (`required`,
  `invalid`, `invalid_choice`, `max_value`, …; `code=None` ⇒ `invalid`), `parse_error` (bad JSON),
  `roster_invalid` (`field = row:<n>[:<column>]`); 404 `not_found` — missing row, **cross-Account row
  (never 403, ADR-0038)**, bad page; 401 `not_authenticated` (anonymous, with
  `WWW-Authenticate: Bearer` since ANG-03 / ADR-0041; it was 403 before) / `token_not_valid` (bad,
  expired or blacklisted JWT); 403 `permission_denied` (CSRF on a session write); 405 `method_not_allowed` (keeps the
  `Allow` header); 409 — a raised `APIException` subclass for a state conflict (ANG-01b
  `job_not_complete`, ANG-01c `map_in_use`, ANG-01d-1 `season_in_progress` / `manager_fired` / `manager_not_fired` (UXP-03) /
  `season_not_draft` / `no_completed_season` / `not_career_league`, ANG-01f `tournament_embedded` /
  `tournament_not_setup` / `tournament_not_active` / `no_playable_node` / `play_in_progress`); 415
  `unsupported_media_type`; 503 `broker_unavailable` (ANG-01f play-all).
- **Filtering.** `django-filter` (`django_filters` in `INSTALLED_APPS`) with an explicit
  `filterset_class` per viewset, plus `AliasOrderingFilter` (`?ordering=a,-b` over the viewset's
  public `ordering_fields`, mapped through `ordering_aliases`, unknown terms silently dropped, then
  `ordering_tiebreak` appended for stable pagination). A typed filter (number / choice / date / range)
  with a bad value ⇒ **400** with `field` = the param name (range filters report the base name, e.g.
  `date_played`, `timestamp`) — unlike the HTML pages' forgiving fallback. Boolean junk is ignored
  (exception: ANG-01f's `embedded` is a strict boolean, junk ⇒ 400 `invalid`).
  Every viewset mixes in `ListOnlyFilterMixin` first, so filters apply to `list` only and query params
  never 404 / 400 a detail or write lookup.
- **Ownership.** `get_queryset()` is built on `owned_queryset` / `owned_match_q` /
  `owned_game_round_q`, and DRF `get_object()` does the rest ⇒ 404 cross-Account. Authentication is
  the settings-level `IsAuthenticated`. DRF 3.15+ exempts every DRF view from the login middleware
  itself, so the `login_not_required` decorators are redundant. Any anonymous-allowed view must go on
  the allowlist in `accounts/tests/test_login_exempt_surfaces.py`.
  `ArenaMap` is the deliberate exception (shared reference data — see `core/CLAUDE.md`): since
  **ANG-01c** `/api/maps/` is a full read / write map-editor API (upload, delete with a 409
  `map_in_use` guard, zones, zone-config, sight lines, ranked cells, strong spots, spawn cells,
  processed image, multi-round heatmap) that any Account may use on any map; only the multi-round
  heatmap's **rounds** are scoped by `owned_game_round_q`.

## REST API (`matches/serializers.py`, `matches/api_views.py`, `matches/filters.py`)

DRF endpoints registered under `/api/`, enveloped per **API conventions** above. `MatchViewSet` /
`GameRoundViewSet` are still `ReadOnlyModelViewSet`s plus a hand-written `create` (ANG-01b — no
`CreateModelMixin`), so PUT / PATCH / DELETE stay **405** with the `Allow` header:

| Endpoint | Serializer | Notes |
|----------|-----------|-------|
| `GET /api/matches/` | `MatchSerializer` | Includes `round_ids` (PK list, not nested). Filters: `team` (either side), `season`, `match_type` (bad choice ⇒ 400), `date_played_after` / `date_played_before` (`YYYY-MM-DD`, inclusive, bad date ⇒ 400). Ordering `date_played`, `id`; default `-date_played`, tiebreak `-id` |
| `POST /api/matches/` | `MatchSerializer` | **ANG-01b.** Binds `MatchSetupForm(user=request.user)` (`team_red`, `team_blue`, `match_type` required, `arena_map` optional); simulates synchronously ⇒ **201**, `manager` = requester |
| `GET /api/matches/<id>/` | `MatchSerializer` | Same — full match fields + round PK list (`round_ids`, ordered by `round_number` since ANG-06d: `round_ids[i]` is Round `i + 1`) |
| `GET /api/rounds/` | `GameRoundListSerializer` | Slim — no `player_states`, no `event_log`. Filters: `match`, `arena_map`, **ANG-06c** `single_round` (`true` ⇒ only **Single Rounds**, `match IS NULL`; `false` ⇒ only a Match's Rounds). Ordering `date_played`, `round_number`, `id`; default `-date_played`, tiebreak `-id` |
| `POST /api/rounds/` | `GameRoundSerializer` | **ANG-01b.** Binds `SingleRoundSetupForm(user=request.user)`; standalone detailed round ⇒ **201**, `manager` = requester |
| `GET /api/rounds/<id>/` | `GameRoundSerializer` | Full — nested `player_states` array, plus **ANG-06d** read-only `scoreboard` (also on the `POST` 201): `round_scoreboard(gr)` ⇒ `{red, blue: {players, survivors, eliminated, elim_bonus}}`, each row the 28 `PLAYER_ROW_KEYS` + `player_id`, grouped by `team_color`, ordered `-points_scored, role, player__name`; `elim_bonus` = `BatchSimulator.elimination_bonus` when the other Side is eliminated, else 0; a Side with no rows (a legacy Round) is never eliminated. Built from the retrieve's `Prefetch("player_states", select_related("player"))` (constant query count). Not on list rows |
| `GET /api/rounds/<id>/events/` | `GameEventSerializer` | Always paginated, fixed order `timestamp, id` (no `?ordering`). Filters (`GameEventFilterSet`): `event_type` (bad choice ⇒ 400), `actor`, `target`, `timestamp_min` / `timestamp_max` (raw ticks, inclusive). **ANG-01b:** upgrades the round to `full` fidelity first |
| `GET /api/rounds/<id>/timeline/` | — (`round_timeline` dict) | **ANG-06e.** The whole **Event log**, unpaginated, no params, GET only: `{events, players, highlights, summary}`. `events` remaps the `round_playback_payload` rows (movement and the four unlisted types included, `(timestamp, id)` order), each `{tick, event_type, points, description, actor_id, actor_name, actor_side, target_id, target_name, target_side, metadata}`; Sides from `PlayerRoundState.team_color`; no target ⇒ the three `target_*` are `null`. `players` `{id, name, side, role}` in `PlayerRoundState.id` order; `highlights` = `highlights_json or []`; `summary` = `get_event_summary()`. Upgrades to `full` first; constant query count |
| `GET /api/rounds/<id>/playback/` | — (`matches.playback.round_playback` dict) | **ANG-06f** (ADR-0044). The Round's **Playback snapshots**, no params, GET only: `{duration_ticks (1800), not_targetable_ticks (8), respawn_ticks (16), players, map}`. `players` `[{id, snapshots}]` in `PlayerRoundState.id` order; each snapshot `{tick, score, lives, shots, missiles, sp, tags, misses, downed_at, eliminated, rapid_fire, nuking}` — a tick-0 opening one from the `starting_*` fields, then one per tick the player changed (same-tick events collapsed). Values are read from the logged after-event metadata (`actor_*` / `sp`, `target_*`, `special.targets[]`); only missiles (−1 per `locking`, −1 more per `missiled` hit, MECH-16), eliminated, tags, misses, `downed_at`, `rapid_fire` and `nuking` are derived. The last snapshot equals the player's `PlayerRoundState` (seeded invariant test). `map` = `build_playback_map` (`{zone_size, arena_map: {id, name}, players: [{id, name, team, role, scout, spawn, moves}]}`) or `null` for a map-less Round. Upgrades to `full` first; names / Sides / events stay on `/timeline/` |
| `GET /api/rounds/<id>/missile-log/` | — (builder dict) | **ANG-01b.** RES-03 `{round_id, fired, hit, efficiency, rows}`; upgrades to `combat` |
| `GET /api/rounds/<id>/heatmap/` | — (builder dict) | **ANG-01b.** RES-04 `{round_id, has_map, arena_map: {id, name} \| null, zone_size, processed_image_url, cell_occupancy_json, player_roster}`; upgrades to `full` |
| `GET /api/rounds/<id>/report/` | — (raw PDF) | **ANG-01b.** RV-03 PDF attachment, **not enveloped**. **RV-05:** upgrades the round to `full` fidelity first (`ensure_fidelity(..., "full")`, as `/events/` does) so the chart section plots the same events |
| `GET /api/rounds/compare/?round_a=&round_b=` | — (builder dict) | **ANG-01b.** RV-01 `{round_a, round_b, stat_keys, team_ids, points_series, deltas}` |
| `POST /api/simulate-batch/<job_id>/save/` | — | **ANG-01b.** Enqueue `save_games_task` ⇒ 200 `{job_id}` |
| `GET /api/simulate-batch/save/<save_job_id>/` | — | **ANG-01b.** Save Job status `{status, error, round_ids}` |
| `GET /api/leagues/` | `LeagueSerializer` | **ANG-01d-1.** Owned + Unmanaged Leagues. Filters (`LeagueFilterSet`): `state`, `mode`. Ordering `name`, `created_at`, `id`; default / tiebreak `-id` |
| `POST /api/leagues/` | `LeagueSerializer` + `draft_season`, `conference_ids` | **ANG-01d-1.** `template` key ⇒ `LeagueTemplateForm` path; else Advanced `CreateLeagueForm` ⇒ **201**, `manager` = requester |
| `GET /api/leagues/templates/` | — (dataclass dicts) | **ANG-01d-1.** The 5 `LEAGUE_TEMPLATES`, not paginated |
| `GET` / `DELETE /api/leagues/<id>/` | `LeagueSerializer` | **ANG-01d-1.** DELETE career-only (`teardown_league`) ⇒ 204; non-career ⇒ 409 `not_career_league` |
| `GET /api/leagues/<id>/delete-summary/` | — (`league_delete_summary` dict) | **ANG-06-14.** `{seasons, matches, tournaments, teams, players}`, the counts `DELETE` would remove; non-career ⇒ 409 `not_career_league` |
| `GET /api/leagues/<id>/nav/` | — (dict) | **ANG-06-14.** The League nav context: `{league: {id, name, mode}, displayed_season: ref \| null, schedule_team: ref \| null, finance_enabled}` (`finance_enabled`: ANG-06-17) |
| `GET /api/leagues/<id>/dashboard/` · `/history/` · `/teams/<team_id>/schedule/` | — (builder dicts) | **ANG-01d-1.** Reads; history paginated. **ANG-06-14:** the League dashboard gains `mode`, `managed_team`, `has_owner_evaluations`, `member_night_progress`, `manager_awaiting_reassignment` |
| `POST /api/leagues/<id>/next-season/` · `/reassign-team/`; `GET /new-team-candidates/` | `SeasonSerializer` (POSTs) | **ANG-01d-1.** Rollover ⇒ 201; 409 conflict codes. **ANG-06-14:** next-season's 201 is an **active** Season (UXP-18), a draft only when the previous Season had Conferences or the start is refused |
| `GET /api/seasons/` | `SeasonListSerializer` | **ANG-01d-1.** Filters (`SeasonFilterSet`): `league`, `state`. Ordering `start_date`, `created_at`, `id`; default / tiebreak `-id`. No POST / PUT / PATCH / DELETE (405) |
| `GET /api/seasons/<id>/` | `SeasonSerializer` | **ANG-01d-1.** Nested `phases` / `conferences`; no play-state fields |
| `GET /api/seasons/<id>/dashboard/` · `/standings/` · `/schedule/` · `/awards/` · `/owner-evaluation/` | — (builder dicts) | **ANG-01d-1.** Reads; standings takes `?sort=&dir=` |
| `GET` / `PUT /api/seasons/<id>/conferences/` | — (state dict) | **ANG-01d-1.** PUT = full replace via `ConferencePartitionForm`; non-draft ⇒ 409 `season_not_draft` |
| `GET` / `DELETE /api/leagues/<id>/watch-list/`; `PUT` / `DELETE …/watch-list/<player_id>/` | — (builder rows) | **ANG-01e.** Paginated Player-Stats rows (`?season=&rate=&ordering=`); DELETE clears ⇒ `{removed}`; PUT / DELETE one ⇒ `{player, watched}`; 400 `player_not_in_league` |
| `GET /api/leagues/<id>/player-stats/` · `/player-ratings/` · `/free-agents/` | — (builder rows) | **ANG-01e.** Paginated; `?ordering=`, plus `season` / `rate` / `team_id` (stats), `team_id` (ratings) |
| `GET /api/leagues/<id>/leaders/` · `/team-stats/` · `/power-rankings/` · `/game-log/` · `/statistical-feats/` · `/playoffs/` | — (builder dicts) | **ANG-01e.** `?season=<id>\|career` (playoffs: no career); feats paginated |
| `GET /api/leagues/<id>/players/<player_id>/` | — (builder dict) | **ANG-01e.** League player page, lenient (404 only for a missing / foreign League or Player) |
| `GET /api/leagues/<id>/teams/<team_id>/roster/` · `/history/` · `/history/players/` | — (builder dicts) | **ANG-01e.** Team must be enrolled in the displayed Season, else 404; history players paginated |
| `GET` / `PATCH /api/leagues/<id>/teams/<team_id>/finances/`; `GET /api/leagues/<id>/finances/` | — (builder dicts) | **ANG-01e.** PATCH binds `TeamFinancesForm`; 409 `finance_disabled` / `team_not_editable` before 400 |
| `GET /api/tournaments/` | `TournamentSerializer` | **ANG-01f.** Owned + Unmanaged. Filters (`TournamentFilterSet`): `state`, `format`, `team_assembly` (bad choice ⇒ 400), `embedded` (strict bool, junk ⇒ 400). Ordering `name`, `created_at`, `id`; default / tiebreak `-id` |
| `POST /api/tournaments/` | — (detail payload) | **ANG-01f.** Binds `TournamentCreateForm(user=)` ⇒ **201**, `manager` = requester |
| `GET /api/tournaments/<id>/` | — (detail payload) | **ANG-01f.** `build_tournament_detail`; PUT / PATCH / DELETE ⇒ 405 |
| `PUT …/seeds/` · `POST …/lock/` · `POST …/import-participants/` | — (detail payload) | **ANG-01f.** Setup-only (409 `tournament_not_setup`); import is multipart ⇒ 201, `roster_invalid` |
| `GET …/pool/`; `POST …/pool/players/` · `/pool/generate/` · `/pool/import/`; `DELETE …/pool/players/<player_id>/` | — (pool entries) | **ANG-01f.** GET paginated; POSTs ⇒ **201** with only the new entries; DELETE ⇒ 204 |
| `POST …/draw/`; `PATCH …/draw/entries/<player_id>/` | — (detail / one entry) | **ANG-01f.** Draw ⇒ 200 detail; entry PATCH ⇒ 200 `{player, tier, drawn_team}` |
| `POST …/play-next/` · `…/play-all/`; `GET …/play-jobs/<job_id>/` | — (dicts) | **ANG-01f.** play-next ⇒ `{node, match_ids, state, champion}`; play-all ⇒ 200 `{job_id, tournament_id, status_url}`; 409 `play_in_progress`, 503 `broker_unavailable` |

HX-03 / HX-04 head-to-head, HX-01 career and HX-02 benchmark endpoints live on the `teams` viewsets
(`/api/teams/head-to-head/`, `/api/players/head-to-head/`, `/api/players/<id>/career/`,
`/api/players/benchmarks/`) but their builders are in `matches/services.py` / `teams/services.py` —
see **ANG-01b** below, `teams/CLAUDE.md` (Services) and `docs/agents/teams/api.md`.
Team head-to-head `detail_list` rows also carry `match_id` / `round_id` (each null on the other
kind, ANG-06-9) so the SPA links by id instead of `detail_url`.

**Team match history (ANG-06-9).** `GET /api/teams/<id>/history/` is a detail action on
`TeamViewSet` (`get_object()` ⇒ cross-Account 404) over `build_team_match_history(team, user,
scopes)` in `matches/team_match_history.py`, which the HTML `team_match_history` view calls too.
Both surfaces drop another Account's Matches / Rounds (`owned_match_q` / `owned_game_round_q`).
`scope` is repeated: absent ⇒ every scope, present ⇒ that selection, `?scope=` ⇒ none; unknown keys
(`freeform`, `t<tournament id>` are the valid ones) are ignored. `team_match_history_payload` turns
the builder's model rows into the `data` shape (`team`, `scope_options[{key,label,selected}]`,
`show_scope_filter`, `has_any_games`, `stats`, `role_columns`, `roster`, `opponents`, `matches`,
`single_rounds`; both lists newest first; every Match and Single Round row carries `result`
`won` / `lost` / `tie`, a null winner being a tie). The builder returns a frozen
`TeamMatchHistory` dataclass (`stats` a `HistoryStats`); the HTML view spreads its fields into
the template context. The roster query `select_related`s `game_round` (`get_mvp` reads it): the
`("teams", "history")` scaler in `test_detail_action_query_counts.py` pins the constant query
count. Tests: `matches/tests/test_api_team_match_history.py`.

`MatchFilterSet` / `GameRoundFilterSet` / `GameEventFilterSet` live in `matches/filters.py`. The
`events` action resolves the round with `get_object()` (cross-Account ⇒ 404) and validates the
FilterSet by hand (`translate_validation` ⇒ 400 envelope). `get_queryset()` no longer carries its own
`.order_by(...)` — ordering comes from the backend.

**Batch endpoints (API-03) are enveloped too.** `POST /api/simulate-batch/` (still **200**) and
`GET /api/simulate-batch/<job_id>/` return `{"data": <bare JSON>, "meta": {}}` where `data` is
**byte-identical** to the pre-ANG-01a body (`_BATCH_POST_KEYS` / `_BATCH_STATUS_KEYS`, i.e. the UI
polling JSON). The three hand-built `Response({"detail": msg}, status=400)` (same-team, roster errors,
unknown team) became `raise ValidationError(msg)` — same messages ⇒
`{"code": "invalid", "message": msg, "field": null}`; serializer failures give per-field entries.
**Closed by ANG-01b:** the `SimulateBatchRequestSerializer` team lookup is now ownership-scoped —
the inline same-team / `Team.objects.get` / roster block was replaced by
`matches.services.resolve_batch_teams(team_red_id, team_blue_id, request.user)`; messages and order
are unchanged except that a foreign team id now reads "Team {id} does not exist." (existence is never
revealed).

**Serializer split:** `GameRoundListSerializer` (list) omits `player_states` to prevent serializing up to 240 objects per page. `GameRoundSerializer` (detail) adds the full nested `player_states`. Both share `_GAME_ROUND_FIELDS` and explicitly exclude `event_log` (legacy text dump).

**`MatchSerializer`** — exposes `round_ids` as a method field over `game_rounds`, sorted by `round_number` (ANG-06d; `GameRound` has no `Meta.ordering`), so `round_ids[i]` is Round `i + 1`. `MatchViewSet` prefetches `game_rounds`, so the list runs a constant number of queries. Uses `fields = "__all__"` since Match has no sensitive or volatile fields.

**ANG-06c names and totals (read-only, additive):** `MatchSerializer`, `GameRoundListSerializer` and `GameRoundSerializer` all carry `team_red_name`, `team_blue_name` and `winner_name` (null when `winner` is null — a tie, or an unfinished League Match) through the shared `_TeamNamesMixin`. `MatchSerializer` also carries `red_total_points`, `blue_total_points`, `red_rounds_won`, `blue_rounds_won`, read straight from the `Match` properties of the same names (never re-derived). The viewsets already `select_related` both teams and the winner, so the names add no query per row. `test_api_match_create._GAME_ROUND_DETAIL_KEYS` pins the GameRound detail key set including the names.

**`GameEventSerializer`** / **`PlayerRoundStateSerializer`** — exclude the parent FK (`game_round`) since events and states are always accessed through their parent round.

**N+1 guard:** the `/events/` action adds `.select_related("actor", "target")` so actor/target player lookups are batched. The `GameRoundViewSet.get_queryset()` only adds `.prefetch_related("player_states")` for the `retrieve` and (ANG-01b) `create` actions — list and events requests skip the prefetch. `get_serializer_class` returns `GameRoundSerializer` for both `retrieve` and `create`.

## ANG-01b match / round actions + analytics API

Seam contract: [`.claude/worktrees/ang-01b-seam-contract.md`](../../../.claude/worktrees/ang-01b-seam-contract.md).
**No migration, no simulation change, no Score Calibration re-baseline, no ADR**; the pure modules
(`h2h_stats`, `player_h2h_stats`, `round_comparison`, `missile_log_stats`, `round_summary`,
`career_stats`, `role_benchmarks*`, `sim_helpers/pdf_report`) are untouched.

**`matches/services.py` (new)** — shared by the HTML views and the API; must not import
`matches.views` (the views import it).
- `MatchSetupError(Exception)` — `str(exc)` is the exact user-facing message.
- `check_matchup(team_red, team_blue) -> None` — raises, in HTML order: same team ⇒ "A team cannot
  play against itself!", then red / blue `roster_errors` ⇒ `"{name} has an invalid roster: {errs}"`.
- `resolve_batch_teams(team_red_id, team_blue_id, user) -> tuple[Team, Team]` — equal ids ⇒ same-team
  error; each slot looked up in `owned_queryset(Team.objects.all(), user)`, a miss (foreign **or**
  missing) ⇒ "Team {id} does not exist."; then `check_matchup`. Uses `Team.objects.all()` (not
  `regular()`), so a Free Agents id stays a roster error.
- `create_sandbox_match(team_red, team_blue, match_type, *, arena_map, user) -> Match` /
  `create_sandbox_round(team_red, team_blue, *, arena_map, user) -> GameRound` — `check_matchup` →
  `BatchSimulator().simulate_match` / `simulate_single_round_detailed` → `stamp_manager(row, user)`; a
  simulator `ValueError` is re-raised as `MatchSetupError`.
- `shared_team_ids(round_a, round_b)` (was `views._shared_team_ids`),
  `build_round_comparison(round_a, round_b, user) -> {team_ids, points_series, deltas}` (precondition:
  distinct rounds with a shared team, else `ValueError`; an un-owned shared Team ⇒ `Http404`).
- `build_team_h2h(team_a, team_b, *, provenance, date_from, date_to)` and
  `build_player_h2h(player_a, player_b, *, role, provenance, date_from, date_to)` — the HX-03 / HX-04
  results-mode context keys (no model instances).
- `build_missile_log(gr)` (calls `ensure_fidelity(gr, "combat")` first), `build_heatmap(gr)`
  (`ensure_fidelity(gr, "full")` first; returns the `ArenaMap` **instance** — the API flattens it),
  `build_round_report_data(gr)` (the 15-key RV-03 `report_data`, **no** fidelity upgrade: the
  `report` action upgrades first), `round_report_filename(gr)`, **ANG-06d** `round_scoreboard(gr)`
  (the Django round page context and the API `scoreboard` field).
- The RV-01 / HX-03 / HX-04 private helpers moved here verbatim; `matches/views.py` re-exports
  `_player_row`, `_comparison_row`, `_cumulative_team_points` and `_shared_team_ids = shared_team_ids`.
  The lenient HTML parsers (`_parse_provenance`, `_parse_date`, `_H2H_PROVENANCE_VALUES`) stay in views.

**Forms.** `MatchSetupForm` / `SingleRoundSetupForm` / `BatchSimulateForm` take a keyword-only
`user=None`; `team_red` / `team_blue` = the valid-roster subset of
`owned_queryset(Team.objects.regular(), user)` (`None` ⇒ Unmanaged only). Foreign / Free Agents /
per-League pool / invalid-roster ids ⇒ `invalid_choice` (HTML form error; API 400). New API-only
`SaveBatchGamesForm` (`team_red`, `team_blue` ints ≥ 1; optional `arena_map`; `game_type` avg /
outlier; strict `n` 1..10 — the HTML save keeps its clamp). `matches/api_forms.py` (new):
`CompareRoundsParamsForm` (`round_a`, `round_b`), `TeamH2HParamsForm` (`team_a`, `team_b`,
`provenance` default "all", `from` / `to`), `PlayerH2HParamsForm` (`player_a`, `player_b`, `role`
default null, `provenance`, `from` / `to`); `_add_date_range(form_cls)` injects the keyword-named
`from` / `to` `DateField`s (`%Y-%m-%d`). Equal ids ⇒ `clean()` error "Pick two different
rounds / teams / players to compare." (`field` null) — checked **before** the 404 lookup.

**Endpoint behaviour.**
- **Creates** (`POST /api/matches/`, `/api/rounds/`): Form → `create_sandbox_*` → `MatchSetupError`
  becomes `ValidationError` (400 `invalid`, field null) → re-fetch through the owned queryset → 201.
- **Actions** use `get_object()` (unknown / cross-Account ⇒ 404); `methods=["get"]` + explicit
  `url_name` pin `gameround-missile-log` / `-heatmap` / `-report` / `-compare`. `report` returns a raw
  `HttpResponse(content_type="application/pdf")` with
  `Content-Disposition: attachment; filename="round-<pk>-<red>-vs-<blue>.pdf"` (caveat: an
  `Accept: application/pdf` request gets DRF's 406, like the roster CSV — send `*/*` or json).
- **Compare** — validate → `get_owned_or_404` a then b → no shared team ⇒ 400 `invalid` "These rounds
  share no team, so there is nothing to compare." → builder. `round_a` / `round_b` are flattened refs
  `{id, round_number, match_id, date_played, team_red, team_blue}`.
- **Batch save** (`POST /api/simulate-batch/<job_id>/save/`) — **job-keyed with teams in the body; no
  session.** The Job id is a capability token (no owner check); ownership is enforced on the inputs.
  Order: Form → `resolve_batch_teams` → `AsyncResult(job_id).state != "SUCCESS"` (incl. unknown /
  PENDING / FAILURE) ⇒ **409 `job_not_complete`** (`JobNotComplete(APIException)` in
  `matches/api_views.py`) → `avg_seeds` / `outlier_seeds[:n]`, empty ⇒ 400 "No saved seeds for this
  category." → `save_games_task.delay(..., manager_id=request.user.pk)` ⇒ **200** `{job_id}` (the
  status every Job-enqueue endpoint uses).
- **Save status** (`GET /api/simulate-batch/save/<save_job_id>/`) — `build_save_status_response`,
  byte-identical to the HTML JSON; never 4xx (unknown ⇒ `running`).

**Fidelity (GEN-01).** missile-log ⇒ `combat`, heatmap ⇒ `full`, and the ANG-01a `/events/` action
now ⇒ `full` (before, a `scores` round served an empty event list); since RV-05 the report also
upgrades to `full` in the action (`build_round_report_data` itself still does not).

**HTML views changed (output byte-identical for owned data).** `create_match` / `create_single_round`
/ `simulate_batch` pass `user=request.user` and delegate to the services (team choices narrow);
`save_batch_games` passes `manager_id` (saved rounds now owned by the saver); `compare_rounds`,
`head_to_head`, `player_head_to_head` scope their pickers **and** preselect lookups to owned rows
(closes a team-name leak) and delegate to the builders; `missile_log`, `movement_heatmap`,
`export_round_report` delegate to the builders. Lenient HTML param parsing is unchanged.

**Known follow-ups (PLAN.md Deferred Items).** The H2H / career round sets are not
ownership-filtered (the HX-02 benchmark population is, since UXP-14) (a foreign Account's Match between two Unmanaged Teams can surface in
`detail_list`); the HTML `save_batch_games` still trusts the session's team ids.

## ANG-01d-1 league lifecycle + reads API

Seam contract: [`.claude/worktrees/ang-01d-1-seam-contract.md`](../../../.claude/worktrees/ang-01d-1-seam-contract.md).
**No migration, no simulation change, no Score Calibration re-baseline, no ADR**; models, templates,
`league_urls.py`, `season_urls.py`, `tasks.py`, `league_screens/*` and `core/*` unchanged. ANG-01d was
re-sliced: this is the **lifecycle + reads** half; the **play loop** (start Season, play week / two
months / until end / playoffs / single round, live watch, Job status + cancel, member nights, the
topnav play-control keys) is **ANG-01d-2** (shipped separately, PR #178, in
`matches/league_play_services.py`) — `_build_play_controls_context`, the play views and `tasks.py`
did not move in ANG-01d-1. The API **never writes or pops `request.session["last_league_id"]`**.

**`matches/league_services.py` (new)** — shared by the HTML views and the API; must **not** import
`matches.league_views` (views import services); imports `CreateLeagueForm` under `TYPE_CHECKING`
only. Moved code calls `random.Random()` through `import random` (patch safety).
- **Moved verbatim** from `league_views.py` (bodies byte-unchanged). Same name: `SEED_BUDGET_MIN` /
  `_MAX` / `_SINGLE`, `_compute_team_overall`, `_rank_teams_by_strength`, `_pick_manager_team`,
  `_seed_team_budgets_by_strength`, `_write_baseline_ratings`, `_developing_players`,
  `_coaching_effect_by_team`, `_scouting_budget_by_team`, `_develop_league_for_new_season`,
  `_team_winp_for_season`, `_team_wins_games_for_season`, `_classify_playoffs_for_team`,
  `_ensure_team_finances`, `_ensure_owner_evaluations`, `WORST_N_ELIGIBLE`,
  `_delete_team_if_orphaned`, `_league_teardown_targets`, `_league_match_q`, `_playoff_cursor_keys`,
  `_build_map_config_label`, the award helpers (`_BRACKET_FINALS_FORMATS`, `_award_round_dict`,
  `_season_regular_round_dicts`, `_finals_deciding_node`, `_season_finals_mvp`, `_KD_ROLE_LABELS`,
  `_player_award_labels`), the member-night readers (`_member_night_game_dict`,
  `_member_night_games_for_season`, `_member_night_appearances_for_team`), `_render_fixture_sides`,
  `_build_team_schedule_rows`, `_validate_conference_partition`, `_standings_row_attr`,
  `_streak_sort_value`, `_standings_sort_value`. **Renamed public:** `template_to_form_data`,
  `create_league_and_season` (still `@transaction.atomic`), `is_career_league`,
  `run_season_rollover` (undecorated — caller owns atomic), `eligible_new_teams`, `teardown_league`,
  `build_dashboard_context`, `pick_displayed_season`, `compute_season_award_set`,
  `build_history_row` (13 keys), `STANDINGS_SORT_KEYS`.
- **Re-exports.** `league_views.py` imports every moved / new name back (`# noqa: F401`) and aliases
  the renamed ones to their old underscore names (`_template_to_form_data`, `_create_league_and_season`,
  `_is_career_league`, `_run_season_rollover`, `_eligible_new_teams`, `_teardown_league`,
  `_build_dashboard_context`, `_pick_displayed_season`, `_compute_season_award_set`,
  `_build_history_row`, `_STANDINGS_SORT_KEYS`), so every existing `from matches.league_views import
  _x` (tests, `league_screens/*`, `core.context_processors`) keeps working. **Patch-target rule:**
  `patch("matches.league_views._x")` no longer intercepts calls from moved / delegating code — patch
  `matches.league_services.<name>`; `matches.league_views.random.Random` stays valid (global module).
- **Domain exceptions:** `SeasonInProgressError(season)` (`.season` = the non-completed Season),
  `NoCompletedSeasonError`, `ManagerFiredError(evaluation)` (`.evaluation`).
- **New:** `season_mode_for(season | None) -> "none" | "draft" | "completed" | "active"`;
  `latest_completed_season(league)`; `start_next_season(league) -> Season` (undecorated: active
  Season ⇒ `SeasonInProgressError`; no completed ⇒ `NoCompletedSeasonError`; ensure finances +
  evaluations; fired and still managing the evaluated team ⇒ `ManagerFiredError`; else
  `run_season_rollover`); `reassign_and_roll(league, team, latest_completed) -> Season` (sets
  `current_team`, rolls; caller owns atomic + validation); `ensure_owner_evaluation(season) ->
  OwnerEvaluation | None`; `build_season_standings(season, *, sort="rank", direction="asc") ->
  {is_draft_preview, standings_groups, rows, rows_with_teams}` (draft rows = 17-key zeroed dicts, live
  rows = `StandingsRow`); `build_season_schedule(season) -> {matchdays, member_night_games}`;
  `build_team_schedule(displayed_season, team) -> {upcoming, completed, member_night_appearances}`;
  `history_seasons(league) -> (in_progress, completed newest-first, teams_by_id)`;
  `build_season_awards(season) -> {awards, kd_rows, has_rounds}`; `build_conferences_state(season) ->
  {editable, map_mode, teams, conferences: [{id, name, ordinal, team_ids, map_rotation_ids,
  starting_team_ids, starting_map_rotation_ids}], unassigned_team_ids}`;
  `replace_conferences(season, specs)` (`@transaction.atomic`; delete-all then recreate in ordinal
  order; `[]` clears).

**Forms.** `ConferencePartitionForm(data, *, season)` (`matches/forms.py`, **shared HTML + API**;
`conferences = ParsedJSONField()`; imports `_validate_conference_partition` lazily). `clean_conferences`
returns `[(name.strip(), sorted team_ids, rotation_ids), …]`; locked order, all code `invalid`, field
`conferences`: (1) shape — one error `conferences must be a list of {"name", "team_ids",
"map_rotation_ids"} objects.` (`map_rotation_ids` optional, a list **or** a str); (2) membership
(API-only reachable) — `Team {tid} is in more than one conference.` / `Team {tid} is not enrolled in
this Season.`, stop; (3) partition via `_validate_conference_partition` (strings unchanged; bracket
floor `MIN_BRACKET_PARTICIPANTS` when a tournament phase exists, else 2); (4) rotation — str through
`parse_rotation_ids`, list ids checked against confirmed-config maps (`Map rotation contains an invalid
id.` / `… an unknown map id.`, order + duplicates kept); (5) `rotate_by_conference` guard `Each
conference needs at least 1 rotation map.` once. Steps 3–5 reproduce the HTML error list exactly.
API-only: `LeagueTemplateForm` (`template` `ChoiceField` over `LEAGUE_TEMPLATES` keys) and
`ReassignTeamForm(data, *, eligible_team_ids)` (`team_id` `TypedChoiceField(coerce=int)`).
`matches/api_forms.py` gains `StandingsParamsForm` (`sort` ∈ `STANDINGS_SORT_KEYS`, default `rank`;
`dir` asc / desc, default `asc`).

**Serializers / refs (output only).** `LeagueSerializer` (`id, name, mode, state, visibility,
finance_enabled, challenge_fired_luxury_tax, manager, current_team` ref, `free_agent_pool,
active_season` ref — one query per row, `created_at`), `SeasonListSerializer` (`id, league` ref,
`name, start_date, state, schedule_format, map_mode, champion_team` ref, `created_at`),
`SeasonSerializer` (+ `team_ids`, `map_pool_ids`, the four `starting_*` / rotation JSON fields, nested
`phases` = `SeasonPhaseSerializer`, `conferences` = `ConferenceSerializer` `{id, name, ordinal,
team_ids}`); `active_play_job_id` / `play_cancel_requested` excluded (ANG-01d-2 state).
`matches/api_refs.py` gains `league_ref` / `season_ref` / `conference_ref` (`{id, name}`).

**Endpoint behaviour** (`matches/league_api_views.py`; every row via `owned_queryset` /
`get_owned_or_404`, Seasons with `path="league"`; actions use `self.get_object()`).
- **Create** — non-object body ⇒ `parse_error`. A `template` key ⇒ `LeagueTemplateForm` first
  (`invalid_choice` / `required`, field `template`), then `CreateLeagueForm(data=template_to_form_data(
  template, league_name=, difficulty= default "medium"))`, all other keys ignored. No `template` key ⇒
  Advanced: `CreateLeagueForm(data=plain_form_data(...))`, **no defaults injected** (missing required
  ⇒ `required`; `clean()` rules surface as 400s). → `create_league_and_season(form,
  manager=request.user)` → 201 `{**LeagueSerializer, draft_season: season_ref, conference_ids: [...]}`.
- **Destroy** — non-career ⇒ 409 `not_career_league`; else `teardown_league` ⇒ 204.
- **League dashboard** — `pick_displayed_season` → `season_mode_for` → `build_dashboard_context`;
  `data = {league, displayed_season, season_mode, standings_snippet, next_fixture,
  round_count_completed, round_count_total, leaders_points, leaders_tags, leaders_ratio,
  map_config_label, playoff_tournament_id}`. **Season dashboard** = same keys minus `league`, plus
  `season` + `league` refs. No play-control keys. (ANG-06-14 adds five League-dashboard-only keys,
  see **ANG-06-14 League core API gaps** below.)
- **History** — `history_seasons` → the in-progress Season (if any) then completed ones, paginated
  with `EnvelopePagination`; `build_history_row` runs **only on the page slice**; Team / winner
  instances flattened to refs. In-progress row appears once (page 1), not pinned per page.
- **Team schedule** — `get_owned_or_404(Team, …, pk=team_id)` (no enrolment check, HTML parity); no
  displayed Season ⇒ 404.
- **Next season** — `@transaction.atomic`; `start_next_season` exceptions ⇒ 409s; 201
  `SeasonSerializer` (since ANG-06-14 through `roll_and_start_next_season`, so the Season is
  started: see below). On a 409 the lazy ensure writes **roll back** (HTML commits them) — harmless,
  idempotent + deterministic.
- **New-team candidates** — non-career ⇒ `not_career_league`; no completed ⇒ `no_completed_season`;
  `{latest_completed, candidates: [standings rows + team ref]}`. **Reassign** — `@transaction.atomic`:
  non-career → active Season ⇒ `season_in_progress` → no completed → `ReassignTeamForm` (400
  `required` / `invalid_choice`, field `team_id`) → `get_owned_or_404(Team)` → `reassign_and_roll` ⇒
  201. Neither checks the fired verdict (HTML parity).
- **Standings** — `{season, is_draft_preview, sort, dir, groups: [{conference: ref | null, name, rows}]}`;
  rows = the 17 `StandingsRow` keys + `team` ref; flat Season ⇒ exactly one `conference: null` group;
  `rank` never renumbered by sort.
- **Schedule** — `{season, matchdays: [{matchday, date, fixtures}], member_night_games}` with
  `team_a` / `team_b` flattened. **Awards** — `{season, has_rounds, most_points, best_accuracy,
  kd_by_role (5 roles), best_medic, most_efficient_nuke, season_mvp, finals_mvp}`.
- **Owner evaluation** — `ensure_owner_evaluation` (same lazy writes as HTML); `None` ⇒ 404; `data`
  carries `team_managed` ref, the three deltas + totals, `overall_mood`, `verdict`, `hot_seat_level`,
  `fired_reason`, `is_fired`, `reassigned`, `created_at`.
- **Conferences** — GET = `build_conferences_state` with `teams` as refs. PUT (`JSONParser` only;
  multipart ⇒ 415): non-draft ⇒ 409 `season_not_draft` **before** body validation →
  `ConferencePartitionForm` → `replace_conferences` → returns the GET payload.

**409 conflict codes** (`APIException`s in `league_api_views.py`): `SeasonInProgress` ⇒
`season_in_progress`, `field = "season:<id>"` (dict `detail`, the `roster_invalid` `row:<n>`
precedent; next-season / reassign); `ManagerFired` ⇒ `manager_fired` (next-season); `ManagerNotFired` ⇒ `manager_not_fired`
(UXP-03: candidates / reassign unless the latest evaluation fired the Manager and they have not
reassigned yet; the HTML picker redirects to the owner evaluation, HTML reassign is 400); `NoCompletedSeason`
⇒ `no_completed_season` (next-season / candidates / reassign); `NotCareerLeague` ⇒
`not_career_league` (delete / candidates / reassign; message = the HTML string per site);
`SeasonNotDraft` ⇒ `season_not_draft` (conferences PUT; text == the HTML 400). PUT / PATCH on a
League or Season detail, POST / DELETE on seasons ⇒ 405.

**HTML views changed (output byte-identical, same DB writes).** `league_dashboard` (mode ladder ⇒
`season_mode_for`), `season_standings`, `season_schedule`, `team_schedule`, `league_history`,
`season_awards`, `owner_evaluation`, `next_season` (catches the three service exceptions **inside** its
atomic block ⇒ same redirects / 400 as before, ensure writes still commit), `reassign_team` (final
writes ⇒ `reassign_and_roll`) and `manage_conferences` POST (keeps its indexed parse, then binds
`ConferencePartitionForm`; errors re-rendered from `form.errors.as_data()["conferences"]`) delegate
to the services. **Bug fix (user-approved):** `reassign_team` now has an **active-Season guard** —
after the non-career 400 and before any write, `league.active_season` ⇒ 302 to that Season's
dashboard (before, a POST while a Season was draft / active created a second non-completed Season,
since `Season.objects.create` bypasses `Season.clean`). Still no fired check.

**Known follow-ups (PLAN.md Deferred Items).** No fired check on candidates / reassign; team schedule
accepts any owned Team; the history API does not pin the in-progress row; `LeagueSerializer.active_season`
is per-row.

## ANG-01e league screens API

Spec: GitHub issue #180 (tickets #181-#186); grill decisions 1-19 in `PLAN.md`. All **15**
`league_screens/*` surfaces are exposed under `/api/leagues/<id>/…`. **One migration**
(`0063_watchedplayer`, no `RunPython`); no simulation change, no Score Calibration re-baseline, no ADR.
The API **never writes `request.session["last_league_id"]`** and builds no sidebar links.

**`matches/league_screen_services.py` (new)** — the builders behind every League screen, called by
both the `league_screens/*` HTML views and the API (the views no longer hold screen logic). Reads no
request; must **not** import `matches.league_views` or `matches.league_screens`. **Patch
`matches.league_screen_services.*`** — the old private helpers were deleted, not re-exported (the
plan's re-export decision was dropped).
- **Season scope:** `CAREER = "career"`; frozen `SeasonScope(seasons, selected: Season | "career" |
  None, options, round_filter)` (+ `selected_id`, the legacy HTML value); `resolve_season_scope(league,
  displayed_season, raw_season, *, allow_career=True)` raises `ValueError` on a foreign / bad id. The
  HTML wraps it forgivingly (`league_views._lenient_season_scope` falls back to the default;
  `_resolve_season_scope` keeps its 4-tuple); the API rejects it in the param Form.
- **Shared lookups:** `build_round_dicts(prs_filter, *, extra_fields=None)` (one dict per
  `PlayerRoundState`; `extra_fields(prs) -> dict` adds keys, used by Statistical Feats);
  `rating_stats(player | PlayerSeasonRating)` = the 19 stats keyed **lowercase**
  (`offensive_synergy`, though the field is `Offensive_synergy`).
- **Enrolment — the single definition:** `enrolled_team_ids(season)` = the activation snapshot
  `starting_team_ids_json`, falling back to the live `season.teams` M2M only for a draft (snapshot
  `None`). `enrolled_teams(season | None)` (by name), `enrolled_team(season, team_id)`,
  `parse_team_id(raw)`, `resolve_league_team(league, displayed_season, team_id)` (HTML picker chain:
  `?team_id=` → `current_team` → first enrolled). Every screen, team picker and `team_id` allowlist
  reads enrolment through these (before, some read the live M2M, so a Team added after the start
  leaked in).
- **Watch list:** `watched_player_ids(account, league | league_id) -> set[int]` (anonymous ⇒
  `set()`), `add_watch` (raises `PlayerNotInLeague` unless the Player is on an enrolled Team of the
  displayed Season or in `league.free_agent_pool`), `remove_watch`, `clear_watch_list -> int`
  (removed count) — all idempotent; `build_watch_list_rows(scope, watched_ids, *, rate, sort,
  direction)`.
- **Screens:** `build_player_stats`, `build_player_ratings_queryset`, `build_free_agents_queryset`,
  `LEADER_BOARDS` / `build_leaderboards(scope)`, `PlayerDetail` / `build_player_detail(league,
  player)`, `TeamRoster` / `build_team_roster`, `TeamHistory` / `team_history_seasons` /
  `build_team_history` / `build_team_history_players`, `build_team_stats`, `build_power_rankings`,
  `build_game_log`, `FEATS_SORT_KEYS` / `is_nuke_detonation` / `feat_row_sort_value` /
  `build_statistical_feats`, `PlayoffsScreen` / `build_playoffs`.
- **Finances:** `TeamFinances` / `build_team_finances(season, team)`, `build_league_finances(season)`
  (`[{team, finance | None}]` per enrolled Team), `update_team_finances(league, team, form)` — raises
  `FinanceDisabled`, then `TeamNotEditable` unless `team` is `league.current_team`, **before** looking
  at the form; an invalid form saves nothing (caller reads `form.errors`).

**`WatchedPlayer` model (new).** `account` (FK `AUTH_USER_MODEL`), `league`, `player` (all CASCADE),
`created_at`; `UniqueConstraint(account, league, player)` `uniq_account_league_watched_player`. No
ownership axis of its own — always filtered by `account`. Replaces the LG-06f session store; session
lists were dropped, not migrated (ADR-0004).

**Forms.** `matches/forms.py::TeamFinancesForm` (ModelForm over the four `budget_*`, `injury_policy`,
`ticket_price`) **rejects, never clamps**: budgets `1..finance.MAX_LEVEL`, `ticket_price >= 0`, policy
in `Team.INJURY_POLICY_CHOICES`; labels Scouting / Coaching / Facilities / Health / Injury policy /
Ticket price (the HTML error list shows `label: message`; the API keeps field names). Both callers bind
`merged_form_data(TeamFinancesForm, team, data)`, so absent fields keep the saved value.
`matches/api_forms.py`: `LeagueScreenParamsForm` (bind with `league=`; `clean_season` blank ⇒ `None`,
this League's Season id, or `"career"` when `allow_career`, else 400 `invalid_choice`; `rate` default
`total`; `ordering` default `ordering_default`), `RATE_CHOICES`, `PLAYER_STAT_SORT_KEYS`,
`ordering_choices(keys)`, `parse_ordering(value) -> (key, "asc" | "desc")`, a shared `team_id` mixin
(must be enrolled in the displayed Season), and one Form per screen (`WatchListParamsForm`,
`PlayerStatsParamsForm`, `PlayerRatingsParamsForm`, `FreeAgentsParamsForm`, `LeadersParamsForm`,
`StatisticalFeatsParamsForm`, `PlayoffsParamsForm` (`allow_career = False`),
`TeamHistoryPlayersParamsForm`, `TeamStatsParamsForm`, `PowerRankingsParamsForm`, `GameLogParamsForm`).

**Endpoint behaviour** (`matches/league_screen_api_views.py::LeagueScreensMixin`, mixed into
`LeagueViewSet`; every action starts with `self.get_object()` ⇒ cross-Account 404, anonymous 401).
- **Params** are strict (bad `season` / `rate` / `ordering` / `team_id` ⇒ 400). Paging is the shared
  `EnvelopePagination` (`page`, `page_size`), not the HTML `per_page`. Sorting is `?ordering=<key>` /
  `-<key>` over the HTML's keys with each screen's HTML default.
- **`meta.season_scope`** = `{selected: season_ref | "career" | null, options: [{id, name, year}]}`
  on every Season-scoped screen (the watch list and the Team screens included), merged beside the
  page keys via the new `EnvelopePagination.get_paginated_response(data, extra_meta=None)`
  (non-paginated screens go through `scoped_response(data, scope, *, extra_meta=None)`, which sets
  `response.envelope_meta`). ANG-06-15 added each option's `year` (`season_option(season)`: the
  `start_date` year, `null` without one) for the SPA Season picker's "Name (year)" label; `selected`
  stays a plain `season_ref`, and `season_ref` is unchanged everywhere else.
- **`meta.team_options`** = `[team_ref]` (ANG-06-15) on `player-stats/`, `player-ratings/`,
  `statistical-feats/` and `game-log/` only: `team_options_meta(displayed_season)` maps
  `enrolled_teams(displayed_season)`, so it is the Teams enrolled in the **displayed** Season, by
  name (the same set the `team_id` allowlist checks), even when `?season=` picks a past Season or
  `career`. It is `[]` when the League has no Season. `player-ratings/` keeps no `season_scope` and
  gets `team_options` beside its page keys. ANG-06-16 adds it to the non-paged
  `teams/<team_id>/roster/`, `teams/<team_id>/history/` and `teams/<team_id>/schedule/` through
  `team_picker_response(data, season)` (sets `response.envelope_meta`); `history/players/` stays
  without it. No other action (Playoffs included) carries it. It costs a fixed number of queries
  per request, never one per row.
- **`player_bio(player)`** (ANG-06-16) = `{overall_rating, potential, age, height, home_site,
  started_playing_age, total_games}`. `player_rating_rows` spreads it (shape unchanged), and so
  does every `teams/<team_id>/roster/` row: starters `{role, player, watched, ...bio}`, bench
  `{player, watched, ...bio}`.
- **Every Player row carries `watched`** for the requesting Account; rating rows carry the 19
  lowercase `stats`.
- **Team-scoped paths** (`teams/<team_id>/…`) use `enrolled_team_or_404` — no displayed Season or a
  Team not enrolled in it ⇒ 404. No current-team fallback (HTML-only).
- **League player** is lenient (decision 17): 404 only for a missing / foreign League or Player; one
  response with bio, overall, 19 stats, per-Season + Career rows, awards and ratings history.
  ANG-06-16 adds `can_link_self_insert`: `player.team.manager_id == request.user.pk` (the
  template's *This is me* rule; an unmanaged Team is false). Another Account's Player is a 404, so
  the flag is never sent false for that reason.
- **`leaders/`** returns all four boards (top 10), no sort params. **`playoffs/`** rejects `career`.
- **`statistical-feats/`** `data.team_feats[]` = `{kind, label, team_name, team: team_ref | null,
  round_id}` (`team_feats_payload`). `team_name` (ANG-06-15) is the `TeamFeatRecord`'s own winner
  name, the value the HTML shows, so it is set even when `team` is `null` (no Round 2 to resolve the
  winner's ref through).
- **Watch list writes:** `PUT` / `DELETE watch-list/<player_id>/` ⇒ `{player, watched}`, idempotent;
  `DELETE watch-list/` ⇒ `{removed: n}`. Player not in the League ⇒ **400 `player_not_in_league`**
  (field `player_id`).
- **Finances:** `GET teams/<team_id>/finances/` — finances off ⇒ only `{finance_enabled, team,
  editable}`; the full payload also carries `max_level` / `default_level` (`finance.MAX_LEVEL` /
  `DEFAULT_LEVEL`, ANG-06-17), and both GET and PATCH set `meta.team_options` (ANG-06-17). `PATCH` order: 404 (not enrolled) → **409 `finance_disabled`** → **409
  `team_not_editable`** (not `current_team`, or none) → 400 form errors → the GET payload. `GET
  finances/` — finances off ⇒ `{finance_enabled: false, rows: []}`; else one row per enrolled Team
  `{team, cash, revenue, profit, payroll}` (money `null` without a snapshot).
- Service exceptions map through `translate_screen_errors()` to `PlayerNotInLeagueError` /
  `FinanceDisabledError` / `TeamNotEditableError` (`APIException`s in the mixin module).

**HTML views changed.** All `league_screens/*` views call the builders (output unchanged except the
enrolment fix). **Team Finances POST** binds `TeamFinancesForm` against `league.current_team` only (a
posted `team_id` is ignored), re-renders 200 with `team-finances-form-errors` on bad values, and
redirects on success / when finances are off; viewing another Team disables the form
(`<fieldset disabled>`) with `team-finances-readonly-notice`. This fixes a bug where the POST saved any
enrolled Team's budgets. **Watch list:** the toggle now returns 400 `{"error": "player not in league"}`
for a Player outside the League; **Remove All** is a POST form (`watch-list-remove-all-form`) to the
new `watch_list_clear` URL (`GET ?action=clear` removed); the `core.context_processors.watch_list`
processor reads `watched_player_ids(request.user, league_id)`.

**Known follow-ups (`issues.md`).** ANG01e-D1 player-page awards recompute whole Seasons; ANG01e-D2
Team History scans every Season of every League; ANG01e-D3 long builders mix querying with assembly.

## ANG-01f tournaments API

Spec: GitHub issue #190 (tickets #191-#196); grill decisions 1-16 in `PLAN.md`. Terms **Player-pool
draw** / **Random Draw** in `GLOSSARY.md`; the single active play run per Tournament is
[ADR-0040](../../adr/0040-single-active-play-run-per-season.md) **Tournaments**. Every sandbox
Tournament screen is exposed under `/api/tournaments/`. **One migration**
(`0064_tournament_player_pool_and_active_play_job`); no engine / RNG / ordering change (play reuses
`play_next_node` / `play_tournament_task`), so no determinism test and no Score Calibration re-baseline.

**Schema.** `Tournament.active_play_job_id` (nullable `CharField(255)`, the live play-run Job id; not
exposed by any payload). `Tournament.team_assembly` choice `random_draw` is renamed **`player_pool`**
(label "Player pool draw"); the migration's `RunPython` rewrites existing rows and has a reverse.
`SeasonPhase.tournament_mode = "random_draw"` is a different column and is **unchanged** (it builds a
drawn pool into RR→DE, a Random Draw by definition).

**Packages.** `matches/tournament_views.py` became the `matches/tournament_views/` package
(`list_create`, `detail`, `setup`, `draw`, `play`; URL names and templates unchanged). The logic moved
to **`matches/tournament_services/`**, shared by HTML and API. It imports no view module and has **no
`__init__` re-exports**: import from each submodule, and **patch `matches.tournament_services.<module>.*`**
(moved view helpers were deleted, not re-exported — the ANG-01e precedent).
- `errors` — `TournamentEmbeddedError`, `TournamentNotSetupError`, `TournamentNotActiveError`,
  `NoPlayableNodeError`, `PlayInProgressError(job_id)` (`.job_id`), `BrokerUnavailableError`,
  `DrawNotReadyError(message)`; `BROKER_UNAVAILABLE_MESSAGE` (the HTML 503 text, used by both
  surfaces); guards `require_sandbox` / `require_setup` / `require_active`, which check **embedded
  first, then state** (an embedded locked Tournament reports `tournament_embedded`). Each exception
  carries a default message ("The Tournament is not active.", "The Tournament is no longer in
  setup.", "No Match is ready to play.", …) that the HTML flashes and the API sends.
- `detail` — `build_rounds(t, bracket_type)` (was the view's `_build_rounds`; `league_screen_services`
  imports it from here), `build_detail_context(t, *, user)` (the HTML template context; `user` scopes
  the pool picker), `format_label(t)` ("Random Draw" for `player_pool` + `round_robin_double_elim`,
  else the format's display label), `is_embedded(t)` (referenced by any `SeasonPhase`, or carries a
  `season_phase` — regional / Worlds), `tournament_fields(t)` (the top-level fields shared by list rows
  and the detail), `build_tournament_detail(t)`, `pool_entries(t)`, `pool_entry_payload(entry)`,
  `node_view(node)` and `node_payload(node_view)`. ANG-01e's `league_screen_api_views.bracket_payload`
  now calls `node_payload`, so the League playoffs API, the Tournament detail and play-next share one
  node shape.
- `seeding` — `team_mean_rating(team)`, `mean_ratings_by_team(team_ids)` (the talent seed order).
- `create` — `TournamentCreateForm(data, *, user)`, `create_tournament(form, *, user)`.
- `setup` — `SeedsForm(data, *, tournament)`, `apply_seeds`, `lock_tournament`, `import_participants`.
- `draw` — `require_pool_setup(t)` (`require_setup`, then a preset Tournament ⇒ Django
  `ValidationError` "This Tournament does not use a Player-pool draw."), `PoolAddPlayersForm`,
  `add_pool_players`, `PoolGenerateForm`, `generate_pool_players`, `import_pool_players`,
  `remove_pool_player` (not in the pool ⇒ `Http404`), `run_draw`, `DrawEntryForm`, `edit_draw_entry`.
- `play` — `PlayedNode` (frozen: `node`, `match_ids`, `state`, `champion`), `play_next`,
  `enqueue_play_all`, `build_play_status`, `release_stale_run`.

The API lives in **`matches/tournament_api_views/`**: `base` (`TournamentViewSet`, routed as
`tournaments`), `errors` (the `APIException`s and `translate_tournament_errors()` — a leaf module, not
`base` as the spec had it, so every mixin imports it without a cycle) and the action mixins `create` /
`setup` / `draw` / `play`. `TournamentSerializer` (output only, renders `tournament_fields`),
`TournamentFilterSet` (`matches/filters.py`) and `tournament_ref` (`{id, name}`, `api_refs.py`).

**Strict Forms (shared by HTML and API).** Absent / blank optional fields take their old defaults, but
a **present bad value is rejected**, never defaulted or clamped.
- `TournamentCreateForm` — `name` (required), `format`, `team_assembly` (`preset` / `player_pool`),
  `teams` (the requester's own + Unmanaged regular Teams with a valid Roster; another Account's Team is
  `invalid_choice`), `generate_count` (≥ 0), `generate_ppt` (≥ 6: a smaller value is now **400
  `min_value`** — it used to be clamped to 6), `rrde_combo` (`4/0`, `4/2`, `8/0`, `8/4`, `16/0`,
  `16/8`), `swiss_rounds` (≥ 0), `role_assignment_mode`, and the four series lengths (1 / 3 / 5). A
  preset Tournament needs ≥ 4 Teams counting generated ones (`too_few_teams`, field `teams`); a
  Player-pool draw ignores `teams` / `generate_*`. The field names are the HTML POST names. The
  forgiving view parsers (`_parse_rrde_combo`, `_parse_swiss_rounds` and the inline series-length /
  format / team-assembly fallbacks) are gone. Other old view helpers moved and were renamed:
  `_detail_context` ⇒ `detail.build_detail_context`, `_build_rounds` ⇒ `detail.build_rounds`,
  `_team_mean_rating` ⇒ `seeding.team_mean_rating`, `_build_tournament_play_status_response` ⇒
  `play.build_play_status`.
- `SeedsForm` — `seeds` is a `{team_id: seed}` map covering exactly the current participants with
  seeds 1..N, else 400 `invalid` on `seeds` (missing / extra team, duplicate or gapped seed, non-int).
- `PoolAddPlayersForm` — `player_ids` must be Players the Account may see (`owned_queryset(…,
  path="team")`; else `invalid_choice`) and not already pooled (**400 `already_in_pool`**).
- `PoolGenerateForm` — `count` 1..1000 (required), `mean` 0..100 (default 50), `std_dev` 1..40
  (default 15).
- `DrawEntryForm` — `tier` 1..6 and / or `drawn_team` (one of **this** Tournament's drawn Teams, else
  `invalid_choice`); at least one is required.

**Endpoints** (every action starts with `get_object()` ⇒ cross-Account 404, anonymous 401; then the
guards (409), then the Form (400), then the service — views raise, never build a 4xx).
- **List** — owned + Unmanaged rows; `embedded` is annotated and Season phases prefetched, so a page
  runs a constant number of queries. `?format=` is the bracket-format filter: the viewset's content
  negotiation **disables DRF's `URL_FORMAT_OVERRIDE`** (otherwise `?format=swiss` is a 404 renderer
  lookup). `embedded` accepts `true` / `false` / `1` / `0` (case-insensitive); anything else ⇒ 400
  `invalid`.
- **Create** — `TournamentCreateForm` ⇒ **201** with the detail payload, `manager` = requester.
- **Detail** — `tournament_fields` (`id, name, format, format_label, state, team_assembly,
  role_assignment_mode, champion` ref, `embedded, season` ref or null, the four series lengths,
  `wb_advancers, lb_advancers, swiss_rounds, manager, created_at`) + `participants` (`[{team, seed}]`
  by seed), `brackets` (`[{bracket_type, rounds: [{bracket_round, nodes}]}]` for each non-empty group of
  round_robin / swiss / winners / losers / grand_final; nodes = the `node_payload` keys `position,
  team_a, team_b, seed_a, seed_b, is_bye, wins_a, wins_b, series_length, winner, games`),
  `rr_standings`, `swiss_standings` (`StandingsRow` fields + `team` ref), `stage`, `cut_labels`
  (`{team_id: "wb" | "lb" | "out"}`, RR→DE seeding stage only), `pool_size`, `is_drawn`,
  `rr_crosstable` and `can_play` (both ANG-06-13b). `rr_crosstable` is `[{team: ref, cells: [null |
  {opponent_team_id, leg1, leg2}]}]` in Standings order, each leg `null | {team_score, opp_score,
  match_team, match_opp, played, match_id}` from the row Team's side (leg 1 at `[a][b]`, leg 2 at
  `[b][a]`, diagonal null), `[]` outside Round robin / RR→DE; its Series Matches are prefetched, so the
  query count is flat in the field size. `can_play` = active and `find_next_playable_node()` finds a
  node (`_can_play`, shared with the HTML context); `next_node` is not sent. PUT / PATCH / DELETE ⇒ 405. Embedded Tournaments are readable, with their `season` ref.
- **Seeds / lock / import-participants** — 409 `tournament_embedded` / `tournament_not_setup` first.
  Seeds (`PUT`, JSON) and lock return 200 with the detail; a lock format-rule `ValidationError` (too few
  participants, RR→DE advancers above the field, an odd Swiss field) ⇒ 400 `invalid`, field null.
  Import (multipart `csv_file`) ⇒ **201** with the detail, CSV errors 400 `roster_invalid` with
  `row:<n>[:<column>]`, imported Teams owned by the requester. On a Player-pool Tournament import is
  **400 `invalid`** "This Tournament uses a Player-pool draw; add Players to its pool instead."
- **Pool** — `GET pool/` is paginated `{player, tier, drawn_team}` entries (by tier, then Player).
  Every pool / draw write runs `require_pool_setup`: 409 embedded / not-setup, then a preset Tournament
  ⇒ **400 `invalid`** "This Tournament does not use a Player-pool draw.". Add (`{player_ids}`) /
  generate / import (multipart) ⇒ **201 with only the newly created entries**; remove ⇒ **204** (not
  in the pool ⇒ 404).
- **Draw** — `POST draw/` ⇒ **200 with the detail payload**; a pool that is not a multiple of 6 with ≥ 24
  Players ⇒ 400 `invalid` with the rule (`DrawNotReadyError`). A re-run in setup replaces the previous
  drawn Teams. `PATCH draw/entries/<player_id>/` ⇒ **200 with the one entry**.
- **Play-next** — `require_active` (409 `tournament_embedded` / `tournament_not_active`), the run guard
  (409 `play_in_progress`), then `play_next_node`; nothing ready ⇒ 409 `no_playable_node`. 200 `{node,
  match_ids, state, champion}`, where `node` carries `id`, `bracket_type`, `bracket_round` plus the
  `node_payload` keys.
- **Play-all** — the same guards, then `enqueue_play_all` ⇒ **200** `{job_id, tournament_id,
  status_url}` (the HTML keeps its 202); broker down ⇒ **503 `broker_unavailable`** with nothing
  claimed. **Play-job status** — `GET play-jobs/<job_id>/` ⇒ the HTML polling keys `{status,
  completed, total, error, tournament_id}`; the Job id is a capability token (ANG-01b / ANG-01d-2).

**Single active play run** (ADR-0040 Tournaments). `enqueue_play_all` makes up the task id and writes it
to `active_play_job_id` (an update filtered on a null column) **before**
`play_tournament_task.apply_async(task_id=…, retry=False)`. A lost claim re-reads the column: a live id
⇒ `PlayInProgressError(<id>)`; an empty one (the winner already finished) is retried once, and a failed
retry reports the current id or `"unknown"`. A broker `OperationalError` clears the claim. The task's
`finally` clears the column only while it still holds its own id. Before refusing, the guard calls
`release_stale_run`: a `SUCCESS` / `FAILURE` / `REVOKED` Job is released (update filtered on that id);
`PENDING` / `STARTED` / `RETRY` / `PROGRESS` stay blocked. **No cancel endpoint** — a never-consumed
`PENDING` run stays stuck (PLAN.md Deferred Items).

**Error codes** (`tournament_api_views/errors.py`, one translator from the service exceptions): 409
`tournament_embedded`, `tournament_not_setup`, `tournament_not_active`, `no_playable_node`;
409 `play_in_progress` with `field = job:<id>`; 503 `broker_unavailable`; `DrawNotReadyError` and a
Django `ValidationError` (lock rules, the preset / Player-pool mismatch) ⇒ 400 `invalid`, field null.

**HTML views changed** (all delegate to the services and bind the same Forms; redirects and URL names
unchanged).
- **Embedded guard:** every write on an embedded Tournament (reseed, lock, import, pool, draw,
  play-next) flashes the error and redirects; play-all answers 409 JSON. Before, `/tournaments/` could
  play an in-League bracket and bypass the Season's play run.
- **Strict errors are flashed** (create re-renders 200 with the errors; reseed, pool generate and
  draw edit flash). Reseed rejects missing / duplicate seeds — a duplicate used to be a 500
  `IntegrityError`.
- **Account leaks closed:** the create Team picker and the pool add-existing picker are scoped to the
  Account; draw edit accepts only this Tournament's drawn Teams.
- **Labels:** the create form reads "Player pool draw" and preselects RR→DE when it is picked (still
  changeable); the list shows `format_label`; the detail page has a `#tournament-format-label` badge
  ("Random Draw" for RR→DE).
- **Play:** play-all returns 409 JSON `{"error"}` while a run is live (or for a not-active / embedded
  Tournament) and keeps 202 on success; play-next flashes. The flash texts now come from the service
  exception defaults (e.g. "The Tournament is not active.").

**Tests.** API: `matches/tests/test_api_tournaments_{read,create,setup,draw,play}.py`. HTML regressions:
`test_tournament_views_{create,setup,draw,play}_ang01f.py` (plus updates to `test_tournament_views.py`).
Services / schema: `test_tournament_foundation_ang01f.py` (guards, `format_label`, the migration row
rewrite) and `test_tournament_services_boundary.py` (no view imports).

**Known follow-ups.** `issues.md`: ANG01f5-D1 generated / imported pool Players land on the global,
Unmanaged Free Agents Team; ANG01f5-D2 pool CSV import skips the DB slot-collision check; ANG01f-D3
`build_detail_context` / `build_tournament_detail` mix several jobs. PLAN.md Deferred Items: the
never-consumed `PENDING` play run.

## ANG-02 CORS configuration

Spec #203, ticket #204. The Angular dev server (`http://localhost:4200`) may call `/api/`
cross-origin with cookies. Config only: `settings.py` plus `django-cors-headers` (`>=4.4`,
`corsheaders` in `INSTALLED_APPS`). There is no new public name and no migration.

- **Middleware.** `corsheaders.middleware.CorsMiddleware` is **first** in `MIDDLEWARE`. It must sit
  above every middleware that can return a response itself. One example is `CommonMiddleware`, which
  replaces a 404 with a redirect for `/api/teams` (no slash). The browser blocks that redirect if it
  has no CORS headers. "Before `CommonMiddleware`" alone is not enough, because WhiteNoise sits
  above it.
- **Settings.**
  - `CORS_ALLOWED_ORIGINS` is read from the env var of the same name (decouple `Csv()`). The default
    is `http://localhost:4200` when `DEBUG` is on and empty when it is off. Production stays empty,
    because ANG-07 serves Angular from the same origin.
  - `CORS_ALLOW_CREDENTIALS = True`. The session cookie is sent because `localhost:4200` and `:8000`
    are the same *site* under SameSite=Lax, and ANG-03's refresh cookie will need it too.
  - `CORS_URLS_REGEX = r"^/api/.*$"`, so HTML, admin and static responses never carry CORS headers.
  - `CORS_EXPOSE_HEADERS = ["Content-Disposition"]` (ANG-06-10), so the cross-origin dev SPA can
    read a download's file name (`ApiClient.getFile`, the Round report).
  - `CSRF_TRUSTED_ORIGINS = list(CORS_ALLOWED_ORIGINS)`. A session write from an untrusted origin is a
    403 `permission_denied` ("CSRF Failed: Origin checking failed").
- **Preflight.** An anonymous `OPTIONS` to `/api/` is answered with 200 by `CorsMiddleware`. It never
  reaches the login redirect or DRF's 401 (403 before ANG-03).
- **Tests.** `laserforce_simulator/api/tests/test_cors.py` uses a shared `override_settings` that pins
  both lists. It covers seven cases:
  - allowed and disallowed origins;
  - `Content-Disposition` in `Access-Control-Expose-Headers` (ANG-06-10);
  - the APPEND_SLASH redirect carrying CORS headers (mutation-checked: it fails with `CorsMiddleware`
    below `CommonMiddleware`);
  - a non-API path;
  - an anonymous preflight;
  - a CSRF-enforcing cross-origin write, trusted and untrusted.
- **Out of scope.** JWT (ANG-03), cookie SameSite / Secure changes, HTTPS hardening, a production
  origin, and nginx (ANG-07).

## ANG-03 JWT authentication

Spec #207, ticket #208, [ADR-0041](../../adr/0041-jwt-api-auth-with-httponly-refresh-cookie.md).
The API accepts a JWT as well as the session. `djangorestframework-simplejwt` (`>=5.3`) plus its
`token_blacklist` app (third-party, additive migrations; none of ours).

- **Auth classes.** `DEFAULT_AUTHENTICATION_CLASSES = [JWTAuthentication, SessionAuthentication]`.
  DRF takes `WWW-Authenticate` from the first class, so an anonymous `/api/` request is **401**
  (`WWW-Authenticate: Bearer realm="api"`) — the Angular client's signal to refresh. The session stays
  second for the browsable API and the HTML site. Authenticated-but-forbidden 403s (CSRF) and
  cross-Account 404s are unchanged.
- **`SIMPLE_JWT`** (constants): access 15 minutes, refresh 7 days, `ROTATE_REFRESH_TOKENS`,
  `BLACKLIST_AFTER_ROTATION`, `UPDATE_LAST_LOGIN`, `AUTH_HEADER_TYPES = ("Bearer",)`. Login reads
  `USERNAME_FIELD`, so the body key is `email`.
- **Endpoints** (`accounts/token_api_views.py`, routed in `api_urls.py`). All three are
  `login_not_required`, `AllowAny`, `authentication_classes = []` (so no CSRF and no session), and
  override `get_authenticate_header`, without which DRF turns their 401s into 403s.

  | Method | Path | URL name | Behaviour |
  |---|---|---|---|
  | POST | `token/` | `api_token_obtain` | `{"email", "password"}` ⇒ 200 `{"data": {"access"}}` + refresh cookie. Bad credentials / inactive ⇒ 401 `no_active_account`; missing field ⇒ 400. No session; `last_login` updated. |
  | POST | `token/refresh/` | `api_token_refresh` | No body; reads **only** the cookie ⇒ 200 `{"access"}` + a rotated cookie (the old token is blacklisted). No cookie ⇒ 401 `not_authenticated`; bad / expired / reused ⇒ 401 `token_not_valid`. |
  | POST | `token/logout/` | `api_token_logout` | Blacklists a valid cookie's token, always deletes the cookie, 204 — also with no cookie, a bad one, or twice. |

- **Refresh cookie.** `REFRESH_COOKIE_NAME = "refresh_token"`, `REFRESH_COOKIE_PATH = "/api/token/"`;
  `HttpOnly`, `SameSite=Lax`, `Secure = not DEBUG` (read per request), `Max-Age` = the refresh
  lifetime. The refresh token never appears in a response body.
- **Envelope.** simplejwt's `InvalidToken` / `AuthenticationFailed` carry a
  `{"detail", "code", "messages"}` dict; `envelope_exception_handler` collapses it to **one** error
  entry for those two classes only. Any other `APIException` dict still expands per key.
- **Not a Form write.** The token endpoints validate with simplejwt's serializers: an auth exchange,
  exempt from ADR-0039's Form rule (recorded in ADR-0041).
- **Tests.** `accounts/tests/test_api_token.py` (login, cookie attributes, rotation, reuse / expired /
  garbage / missing cookie, logout incl. twice, no CSRF needed, `Secure` vs `DEBUG`, 15-minute access)
  and `laserforce_simulator/api/tests/test_jwt_auth.py` (anonymous 401 + Bearer challenge, garbage
  bearer 401, session still accepted). Every anonymous-403 assertion in the API tests became 401 with an
  `ADR-0041` comment; the auth-order mutation (session first) turns four of them red.
- **Out of scope.** The Angular interceptor (ANG-05), login throttling, revoking refresh tokens on
  password change (PLAN.md Deferred Items), HTML views on JWT, scheduling `flushexpiredtokens`.

## ANG-06-14 League core API gaps

Spec: GitHub issue #409 (ticket #410). The backend half of the Angular League core
(`league-foundation.md`, **ANG-06-14**). No model, migration, engine or RNG change.

**Services.**
- `league_services.league_delete_summary(league) -> dict[str, int]` — `{seasons, matches,
  tournaments, teams, players}`, built from `_league_teardown_targets` / `_league_match_q` (the
  same targets `teardown_league` deletes). The HTML `league_delete` GET renders it instead of its
  old inline dict.
- `league_services.sidebar_schedule_team(league, displayed_season) -> Team | None` — the LG-01g
  TEAM > Schedule chain (`current_team` if enrolled, else the alphabetically-first enrolled Team,
  else `None`; `None` without a Season), moved verbatim from
  `league_views._resolve_current_team_for_sidebar` (deleted). The Django sidebar
  (`_build_league_sidebar_links`) and the nav endpoint both call it.
- `league_services.managed_team_id(league) -> int | None` — UXP-08's rule (`current_team_id` in a
  `mode="league"` League, else `None`), moved from `league_views._managed_team_id` (kept as an
  alias there).
- `league_services.dashboard_awaiting_reassignment(league, season_mode) -> bool` — true only for a
  career League whose displayed Season is `completed` and whose
  `manager_awaiting_reassignment(league, latest_completed_season(league))` holds. It runs the lazy
  finance / owner-evaluation ensures, as the HTML new-team picker's GET does.
- `league_play_services.roll_and_start_next_season(league) -> NextSeasonResult(season,
  needs_conferences)` (frozen dataclass; in `league_play_services` because it already imports
  `league_services`). UXP-18's two rules, shared by HTML and API: see `league-foundation.md`,
  **UXP-18**. The caller owns the outer atomic.

**Endpoints** (`LeagueViewSet`, `get_object()` ⇒ cross-Account 404, anonymous 401).
- `GET /api/leagues/<id>/delete-summary/` (route `league-delete-summary`) — `data` =
  `league_delete_summary`. Non-career ⇒ 409 `not_career_league` with the `DELETE` message
  (`_DELETE_NOT_CAREER`).
- `GET /api/leagues/<id>/nav/` (route `league-nav`) — `{league: {id, name, mode},
  displayed_season: season_ref | null, schedule_team: ref | null, finance_enabled}`;
  `displayed_season` = `pick_displayed_season`, `schedule_team` = `sidebar_schedule_team`,
  `finance_enabled` = `league.finance_enabled` (ANG-06-17).
- `GET /api/leagues/<id>/dashboard/` gains five keys, added in `LeagueViewSet.dashboard` only
  (`_dashboard_payload`, the Season dashboard and `SEASON_DASHBOARD_KEYS` are unchanged): `mode`;
  `managed_team` (`{id, name}` of `current_team` per `managed_team_id`, else null);
  `has_owner_evaluations`; `member_night_progress` (`{played, total}` or null, from
  `build_dashboard_context`); `manager_awaiting_reassignment` (`dashboard_awaiting_reassignment`).
  **The GET therefore writes**: on a career League with a completed displayed Season it runs the
  lazy owner-evaluation / finance ensures (computed before `has_owner_evaluations`).
- `POST /api/leagues/<id>/next-season/` calls `roll_and_start_next_season`: the 201 body is an
  **active** Season, a draft only when the previous completed Season had Conferences or the model
  refuses the start. The three 409s are unchanged.
- `GET /api/maps/?simulation_ready=true` — see `core/CLAUDE.md` (filter only, no serializer field).

**Tests.** `matches/tests/test_api_leagues.py` (`TestLeagueDeleteSummary`, `TestLeagueNav`, the
dashboard key tests; `LEAGUE_DASHBOARD_KEYS` in `league_api_fixtures.py` gains the five keys),
`test_api_league_lifecycle.py` (`test_success_201_active_season`,
`test_previous_conferences_keep_a_draft_season`, `test_refused_start_keeps_the_rolled_draft`),
service tests in `test_league_delete.py`, `test_league_next_season.py`, `test_league_sidebar.py`,
and `core/test_maps_api.py` (`simulation_ready`). The N+1 registry
(`laserforce_simulator/api/tests/test_detail_action_query_counts.py`) gains `("leagues",
"delete-summary")` and `("leagues", "nav")` scalers.
