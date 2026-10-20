# matches: REST API

The full API conventions (ADR-0039), the endpoint table, and the ANG-01b / ANG-01d-1 / ANG-01e slices with their service modules.
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
  (never 403, ADR-0038)**, bad page; 403 `not_authenticated` (anonymous — stays 403, not 401, until
  ANG-03's JWT) / `permission_denied` (CSRF on a session write); 405 `method_not_allowed` (keeps the
  `Allow` header); 409 — a raised `APIException` subclass for a state conflict (ANG-01b
  `job_not_complete`, ANG-01c `map_in_use`, ANG-01d-1 `season_in_progress` / `manager_fired` /
  `season_not_draft` / `no_completed_season` / `not_career_league`); 415 `unsupported_media_type`.
- **Filtering.** `django-filter` (`django_filters` in `INSTALLED_APPS`) with an explicit
  `filterset_class` per viewset, plus `AliasOrderingFilter` (`?ordering=a,-b` over the viewset's
  public `ordering_fields`, mapped through `ordering_aliases`, unknown terms silently dropped, then
  `ordering_tiebreak` appended for stable pagination). A typed filter (number / choice / date / range)
  with a bad value ⇒ **400** with `field` = the param name (range filters report the base name, e.g.
  `date_played`, `timestamp`) — unlike the HTML pages' forgiving fallback. Boolean junk is ignored.
  Every viewset mixes in `ListOnlyFilterMixin` first, so filters apply to `list` only and query params
  never 404 / 400 a detail or write lookup.
- **Ownership.** `get_queryset()` is built on `owned_queryset` / `owned_match_q` /
  `owned_game_round_q`, and DRF `get_object()` does the rest ⇒ 404 cross-Account. All viewsets keep
  `@method_decorator(login_not_required, name="dispatch")` + settings-level `IsAuthenticated`.
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
| `GET /api/matches/<id>/` | `MatchSerializer` | Same — full match fields + round PK list |
| `GET /api/rounds/` | `GameRoundListSerializer` | Slim — no `player_states`, no `event_log`. Filters: `match`, `arena_map`. Ordering `date_played`, `round_number`, `id`; default `-date_played`, tiebreak `-id` |
| `POST /api/rounds/` | `GameRoundSerializer` | **ANG-01b.** Binds `SingleRoundSetupForm(user=request.user)`; standalone detailed round ⇒ **201**, `manager` = requester |
| `GET /api/rounds/<id>/` | `GameRoundSerializer` | Full — nested `player_states` array |
| `GET /api/rounds/<id>/events/` | `GameEventSerializer` | Always paginated, fixed order `timestamp, id` (no `?ordering`). Filters (`GameEventFilterSet`): `event_type` (bad choice ⇒ 400), `actor`, `target`, `timestamp_min` / `timestamp_max` (raw ticks, inclusive). **ANG-01b:** upgrades the round to `full` fidelity first |
| `GET /api/rounds/<id>/missile-log/` | — (builder dict) | **ANG-01b.** RES-03 `{round_id, fired, hit, efficiency, rows}`; upgrades to `combat` |
| `GET /api/rounds/<id>/heatmap/` | — (builder dict) | **ANG-01b.** RES-04 `{round_id, has_map, arena_map: {id, name} \| null, zone_size, processed_image_url, cell_occupancy_json, player_roster}`; upgrades to `full` |
| `GET /api/rounds/<id>/report/` | — (raw PDF) | **ANG-01b.** RV-03 PDF attachment, **not enveloped**, no fidelity upgrade |
| `GET /api/rounds/compare/?round_a=&round_b=` | — (builder dict) | **ANG-01b.** RV-01 `{round_a, round_b, stat_keys, team_ids, points_series, deltas}` |
| `POST /api/simulate-batch/<job_id>/save/` | — | **ANG-01b.** Enqueue `save_games_task` ⇒ 200 `{job_id}` |
| `GET /api/simulate-batch/save/<save_job_id>/` | — | **ANG-01b.** Save Job status `{status, error, round_ids}` |
| `GET /api/leagues/` | `LeagueSerializer` | **ANG-01d-1.** Owned + Unmanaged Leagues. Filters (`LeagueFilterSet`): `state`, `mode`. Ordering `name`, `created_at`, `id`; default / tiebreak `-id` |
| `POST /api/leagues/` | `LeagueSerializer` + `draft_season`, `conference_ids` | **ANG-01d-1.** `template` key ⇒ `LeagueTemplateForm` path; else Advanced `CreateLeagueForm` ⇒ **201**, `manager` = requester |
| `GET /api/leagues/templates/` | — (dataclass dicts) | **ANG-01d-1.** The 5 `LEAGUE_TEMPLATES`, not paginated |
| `GET` / `DELETE /api/leagues/<id>/` | `LeagueSerializer` | **ANG-01d-1.** DELETE career-only (`teardown_league`) ⇒ 204; non-career ⇒ 409 `not_career_league` |
| `GET /api/leagues/<id>/dashboard/` · `/history/` · `/teams/<team_id>/schedule/` | — (builder dicts) | **ANG-01d-1.** Reads; history paginated |
| `POST /api/leagues/<id>/next-season/` · `/reassign-team/`; `GET /new-team-candidates/` | `SeasonSerializer` (POSTs) | **ANG-01d-1.** Rollover ⇒ 201; 409 conflict codes |
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

HX-03 / HX-04 head-to-head, HX-01 career and HX-02 benchmark endpoints live on the `teams` viewsets
(`/api/teams/head-to-head/`, `/api/players/head-to-head/`, `/api/players/<id>/career/`,
`/api/players/benchmarks/`) but their builders are in `matches/services.py` / `teams/services.py` —
see **ANG-01b** below and `teams/CLAUDE.md`.

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

**`MatchSerializer`** — exposes `round_ids` as a `PrimaryKeyRelatedField` (source=`game_rounds`). Uses `fields = "__all__"` since Match has no sensitive or volatile fields.

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
  `build_round_report_data(gr)` (the 15-key RV-03 `report_data`, **no** fidelity upgrade),
  `round_report_filename(gr)`.
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
now ⇒ `full` (before, a `scores` round served an empty event list); the report does **not** upgrade.

**HTML views changed (output byte-identical for owned data).** `create_match` / `create_single_round`
/ `simulate_batch` pass `user=request.user` and delegate to the services (team choices narrow);
`save_batch_games` passes `manager_id` (saved rounds now owned by the saver); `compare_rounds`,
`head_to_head`, `player_head_to_head` scope their pickers **and** preselect lookups to owned rows
(closes a team-name leak) and delegate to the builders; `missile_log`, `movement_heatmap`,
`export_round_report` delegate to the builders. Lenient HTML param parsing is unchanged.

**Known follow-ups (PLAN.md Deferred Items).** The H2H / career / benchmark corpora are not
ownership-filtered (a foreign Account's Match between two Unmanaged Teams can surface in
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
  `season` + `league` refs. No play-control keys.
- **History** — `history_seasons` → the in-progress Season (if any) then completed ones, paginated
  with `EnvelopePagination`; `build_history_row` runs **only on the page slice**; Team / winner
  instances flattened to refs. In-progress row appears once (page 1), not pinned per page.
- **Team schedule** — `get_owned_or_404(Team, …, pk=team_id)` (no enrolment check, HTML parity); no
  displayed Season ⇒ 404.
- **Next season** — `@transaction.atomic`; `start_next_season` exceptions ⇒ 409s; 201
  `SeasonSerializer`. On a 409 the lazy ensure writes **roll back** (HTML commits them) — harmless,
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
precedent; next-season / reassign); `ManagerFired` ⇒ `manager_fired` (next-season); `NoCompletedSeason`
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
`LeagueViewSet`; every action starts with `self.get_object()` ⇒ cross-Account 404, anonymous 403).
- **Params** are strict (bad `season` / `rate` / `ordering` / `team_id` ⇒ 400). Paging is the shared
  `EnvelopePagination` (`page`, `page_size`), not the HTML `per_page`. Sorting is `?ordering=<key>` /
  `-<key>` over the HTML's keys with each screen's HTML default.
- **`meta.season_scope`** = `{selected: season_ref | "career" | null, options: [season_ref]}` on every
  Season-scoped screen, merged beside the page keys via the new
  `EnvelopePagination.get_paginated_response(data, extra_meta=None)` (non-paginated screens set
  `response.envelope_meta`).
- **Every Player row carries `watched`** for the requesting Account; rating rows carry the 19
  lowercase `stats`.
- **Team-scoped paths** (`teams/<team_id>/…`) use `enrolled_team_or_404` — no displayed Season or a
  Team not enrolled in it ⇒ 404. No current-team fallback (HTML-only).
- **League player** is lenient (decision 17): 404 only for a missing / foreign League or Player; one
  response with bio, overall, 19 stats, per-Season + Career rows, awards and ratings history.
- **`leaders/`** returns all four boards (top 10), no sort params. **`playoffs/`** rejects `career`.
- **Watch list writes:** `PUT` / `DELETE watch-list/<player_id>/` ⇒ `{player, watched}`, idempotent;
  `DELETE watch-list/` ⇒ `{removed: n}`. Player not in the League ⇒ **400 `player_not_in_league`**
  (field `player_id`).
- **Finances:** `GET teams/<team_id>/finances/` — finances off ⇒ only `{finance_enabled, team,
  editable}`. `PATCH` order: 404 (not enrolled) → **409 `finance_disabled`** → **409
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
