# ANG-01b seam contract — Match / Round actions + analytics API

Branch `ang-01b-match-round-analytics-api`. Binds the code, tests and docs agents. Conventions:
ADR-0039 + `matches/CLAUDE.md` "API conventions (ANG-01a, project-wide)". **Views raise, never
build a 4xx `Response`.** Cross-Account ⇒ 404. No migration, no simulation change, no Score
Calibration re-baseline. All paths below are relative to `laserforce_simulator/` (the Django project).

## 0. Facts verified in code (drive several decisions)

- `MatchSetupForm` / `SingleRoundSetupForm` / `BatchSimulateForm.__init__` already narrow
  `team_red`/`team_blue` to `Team.objects.all()` filtered by `is_valid_roster`. So an
  invalid-roster team reaches the API as **400 `invalid_choice`** on the field, not as the
  roster message. The roster check in `check_matchup` (§2) is defensive only, reachable just through
  `resolve_batch_teams` (the batch paths have no Form).
- `BatchSimulateForm` is **not** ownership-scoped today ⇒ it gets scoped too (§4).
- `BatchSimulator.save_games(..., fidelity="scores")` ⇒ batch-saved rounds are `scores` fidelity
  and today **Unmanaged** (no stamp). `ensure_fidelity` no-ops when `roster_snapshot_json is None`
  (hand-built test rounds) or when the tier is already met.
- Eager Celery under pytest with `CELERY_TASK_STORE_EAGER_RESULT = True` ⇒ `AsyncResult(id)` reads
  real state/result in tests.
- DRF `SimpleRouter` emits list → **detail=False actions** → detail → detail=True actions, so
  `rounds/compare/`, `teams/head-to-head/`, `players/head-to-head/`, `players/benchmarks/` already
  route before `<pk>/`. No ordering work is needed in the router.
- Tests import `_shared_team_ids`, `_comparison_row`, `_cumulative_team_points`, `_player_row`
  from `matches.views` ⇒ moved helpers **must be re-exported** there. No test patches
  `matches.views.*` / `teams.views.*` call targets, so moving code does not defeat any mock.

## 1. Module map

| Module | Status | Contents |
|---|---|---|
| `matches/services.py` | **new** | create/batch validation, the six `build_*` builders, moved private helpers |
| `matches/api_forms.py` | **new** | read-only param Forms: `CompareRoundsParamsForm`, `TeamH2HParamsForm`, `PlayerH2HParamsForm` |
| `teams/api_forms.py` | **new** | `BenchmarkParamsForm` (career + benchmarks) |
| `matches/forms.py` | changed | `user` kwarg on 3 setup forms; new `SaveBatchGamesForm` |
| `matches/tasks.py` | changed | `save_games_task(..., *, manager_id=None)` |
| `matches/api_views.py` | changed | creates, 4 GameRound actions, events fidelity fix, batch scoping, 2 save views, `JobNotComplete` |
| `teams/api_views.py` | changed | `TeamViewSet.head_to_head`, `PlayerViewSet.head_to_head/career/benchmarks` |
| `teams/services.py` | changed | `build_career_stats`, `build_role_benchmarks`, moved HX-01/HX-02 helpers |
| `matches/views.py`, `teams/views.py` | changed | delegate to services; re-export moved names |
| `laserforce_simulator/api_urls.py` | changed | 2 new paths |

Pure modules (`h2h_stats`, `player_h2h_stats`, `round_comparison`, `missile_log_stats`,
`round_summary`, `career_stats`, `role_benchmarks*`, `sim_helpers/pdf_report`) are **untouched**.

## 2. `matches/services.py` (new)

```python
class MatchSetupError(Exception):
    """A matchup the sandbox refuses. str(exc) is the exact user-facing message."""

def check_matchup(team_red: Team, team_blue: Team) -> None
    # Raises MatchSetupError, in this order (= the HTML order):
    #   team_red == team_blue        -> "A team cannot play against itself!"
    #   team_red.roster_errors       -> f"{team_red.name} has an invalid roster: {'; '.join(errs)}"
    #   team_blue.roster_errors      -> same, blue

def resolve_batch_teams(
    team_red_id: int, team_blue_id: int, user: "AbstractBaseUser | None"
) -> tuple[Team, Team]
    # 1. ids equal -> MatchSetupError("A team cannot play against itself!")
    # 2. per slot red then blue:
    #    owned_queryset(Team.objects.all(), user).filter(id=tid).first() is None
    #    -> MatchSetupError(f"Team {tid} does not exist.")  (foreign == missing; existence never revealed)
    # 3. check_matchup(red, blue)  (roster messages)
    # Uses Team.objects.all(), not regular(), to keep ANG-01a batch behaviour (Free Agents -> roster error).

def create_sandbox_match(
    team_red: Team, team_blue: Team, match_type: str, *,
    arena_map: "ArenaMap | None", user: "AbstractBaseUser | None",
) -> Match
    # check_matchup -> BatchSimulator().simulate_match(team_red, team_blue, match_type, arena_map=arena_map)
    # ValueError from the simulator -> re-raised as MatchSetupError(str(exc))
    # stamp_manager(match, user) post-hoc; returns the stamped Match.

def create_sandbox_round(
    team_red: Team, team_blue: Team, *,
    arena_map: "ArenaMap | None", user: "AbstractBaseUser | None",
) -> GameRound
    # Same as above via simulate_single_round_detailed; stamp_manager(game_round, user).

def shared_team_ids(round_a: GameRound, round_b: GameRound) -> list[int]
    # = today's views._shared_team_ids (moved verbatim, renamed public).

def build_round_comparison(
    round_a: GameRound, round_b: GameRound, user: "AbstractBaseUser | None"
) -> dict
    # Precondition: round_a.id != round_b.id and shared_team_ids(...) non-empty, else ValueError.
    # Each shared team resolved as owned (owned_queryset(Team.objects.all(), user)); not owned -> Http404
    # (same as today's get_owned_or_404(Team, ...)).
    # Returns {"team_ids": list[int],
    #          "points_series": list[{"team_id": int, "team_name": str, "a": list[list], "b": list[list]}],
    #          "deltas": list[dict]}          # round_comparison.player_stat_deltas output, verbatim

def build_team_h2h(
    team_a: Team, team_b: Team, *,
    provenance: str, date_from: "date | None", date_to: "date | None",
) -> dict
    # Precondition: team_a.id != team_b.id. Keys (exactly today's results-mode context.update keys minus "mode"):
    # match_record, round_record, score_margin, avg_survivors, top_impactful, per_map_breakdown,
    # detail_list, margin_series, cumulative_wl_series. No model instances in the output.

def build_player_h2h(
    player_a: Player, player_b: Player, *,
    role: "str | None", provenance: str, date_from: "date | None", date_to: "date | None",
) -> dict
    # Precondition: distinct players. Keys: round_record, score_margin, tag_stats, per_role_breakdown,
    # per_map_breakdown, detail_list, margin_series, cumulative_wl_series.
    # role semantics unchanged (_filter_by_role_both: None/unknown -> passthrough).

def build_missile_log(game_round: GameRound) -> dict
    # Calls BatchSimulator().ensure_fidelity(game_round, "combat") FIRST.
    # Returns missile_log_stats.summarize_missile_log(...) verbatim: {"fired","hit","efficiency","rows"}.

def build_heatmap(game_round: GameRound) -> dict
    # Calls BatchSimulator().ensure_fidelity(game_round, "full") FIRST.
    # Returns {"cell_occupancy_json": dict, "player_roster": list[{"id","name","role","team_color"}],
    #          "has_map": bool, "arena_map": ArenaMap | None  (MODEL INSTANCE),
    #          "zone_size": <GameRound.zone_size>, "processed_image_url": str | None}

def build_round_report_data(game_round: GameRound) -> dict
    # The 15-key report_data dict export_round_report builds today, verbatim:
    # round_id, round_label, date_played (preformatted str), map_name, red_team_name, blue_team_name,
    # red_points, blue_points, red_eliminated, blue_eliminated, winner_name, red_players, blue_players,
    # red_totals, blue_totals. NO ensure_fidelity.

def round_report_filename(game_round: GameRound) -> str
    # f"round-{game_round.pk}-{slugify(red.name) or 'red'}-vs-{slugify(blue.name) or 'blue'}.pdf"
```

**Moved into `matches/services.py`** (bodies verbatim, private): `_comparison_row`,
`_cumulative_team_points`, `_player_row`, `_h2h_matches_qs`, `_h2h_rounds_qs`, `_team_a_or_b`,
`_normalize_round`, `_build_player_rounds`, `_build_detail_list`, `_VALID_PLAYER_H2H_ROLES`,
`_player_h2h_rounds_qs`, `_build_player_h2h_tag_counts`, `_normalize_player_round`,
`_filter_by_role_both`, `_build_player_h2h_detail_list`. `matches/views.py` re-exports
`_player_row`, `_comparison_row`, `_cumulative_team_points`, and `_shared_team_ids = shared_team_ids`.
`_parse_provenance` / `_parse_date` / `_H2H_PROVENANCE_VALUES` **stay in views** (the HTML
lenient parsing). `services.py` must not import `matches.views` (to avoid a cycle).
`views.py` imports `services`.

## 3. `teams/services.py` additions

```python
def build_career_stats(player: Player, *, min_rounds: int, display: str) -> dict
    # Keys = today's player_career_stats context minus "player":
    # total_rounds:int, career:dict, per_role:list[dict], trend:list[list], has_rounds:bool,
    # min_rounds:int, display:str, stat_keys:tuple[str,...], per_role_with_benchmarks:list[dict]
    # (overlay only when total_rounds > 0, via get_all_benchmark_data(); else []).

def build_role_benchmarks(*, min_rounds: int, display: str) -> dict
    # Keys = today's role_benchmarks context verbatim:
    # min_rounds, display, roles, benchmarks:{role: list[{stat,mean,median,p25,p75,p90,n}]},
    # stat_keys, benchmarks_by_role:list[tuple[str, list]]  (template-only), any_data:bool
```

**Moved from `teams/views.py`** (verbatim, re-exported from `teams.views`):
`_round_dict_from_state`, `_build_per_role_overlay`, `_HX01_DISPLAY_STAT_KEYS`,
`_HX01_TO_BENCHMARK_STAT`, `_HX01B_STAT_ROW_SPEC`. `_coerce_threshold` / `_coerce_display` stay in views.

## 4. Changed signatures

```python
# matches/forms.py — all three, keyword-only:
MatchSetupForm.__init__(self, *args, user: "AbstractBaseUser | None" = None, **kwargs)
SingleRoundSetupForm.__init__(self, *args, user=None, **kwargs)
BatchSimulateForm.__init__(self, *args, user=None, **kwargs)
#   team_red/team_blue queryset = valid-roster subset of owned_queryset(Team.objects.regular(), user)
#   (user=None -> Unmanaged only, per owned_queryset). arena_map queryset unchanged.
#   Foreign / Free Agents / per-League pool / invalid-roster id -> 400 invalid_choice on that field.

# matches/tasks.py
save_games_task(self, team_red_id: int, team_blue_id: int, seeds: list, n: int,
                arena_map_id: int | None = None, *, manager_id: int | None = None) -> dict
#   After BatchSimulator().save_games(...) returns: if manager_id is not None:
#   GameRound.objects.filter(id__in=round_ids).update(manager_id=manager_id). Simulator untouched.
#   Return value unchanged: {"round_ids": [...]}. Positional .apply(args=(5 items)) still works.

# matches/views.py
build_save_status_response(async_result: AsyncResult) -> dict   # renamed public from _build_save_status_response
_build_save_status_response = build_save_status_response        # alias kept
```

New API-only write Form (in `matches/forms.py`; API-03 has no HTML Form for save, so ADR-0039 is met with a new one):

```python
class SaveBatchGamesForm(forms.Form):
    team_red  = forms.IntegerField(min_value=1)
    team_blue = forms.IntegerField(min_value=1)
    arena_map = forms.IntegerField(min_value=1, required=False)   # passthrough, like batch POST
    game_type = forms.ChoiceField(choices=[("avg", "avg"), ("outlier", "outlier")])
    n         = forms.IntegerField(min_value=1, max_value=10)     # strict; HTML keeps its clamp
```

## 5. Read-only param Forms

Each one is bound as `Form(data=plain_form_data(Form, request.query_params))` and then
`validate_form_or_raise(form)`. The Forms take **ints**. The view resolves the rows with
`get_owned_or_404` **after** validation, so the same id twice gives 400 even when that id is unknown.
`from`/`to` are Python keywords, so they are added after the class is defined:
`Form.base_fields["from"] = forms.DateField(required=False, input_formats=["%Y-%m-%d"])` (and the
same for `to`). A shared helper `_add_date_range(form_cls) -> form_cls` in `matches/api_forms.py`
does this.

| Form (module) | Field | Type / rule | Default when absent |
|---|---|---|---|
| `CompareRoundsParamsForm` (matches/api_forms) | `round_a`, `round_b` | `IntegerField(min_value=1)`, required | — |
| | `clean()` | equal ⇒ `ValidationError("Pick two different rounds to compare.", code="invalid")` | |
| `TeamH2HParamsForm` (matches/api_forms) | `team_a`, `team_b` | `IntegerField(min_value=1)`, required | — |
| | `provenance` | `ChoiceField(all/real/sim, required=False)` | `"all"` |
| | `from`, `to` | `DateField(required=False, %Y-%m-%d)` | `None` |
| | `clean()` | equal ⇒ `"Pick two different teams to compare."`, code `invalid` | |
| `PlayerH2HParamsForm` (matches/api_forms) | `player_a`, `player_b` | `IntegerField(min_value=1)`, required | — |
| | `role` | `ChoiceField(commander/heavy/scout/medic/ammo, required=False)` | `None` |
| | `provenance`, `from`, `to` | as above | `"all"`, `None`, `None` |
| | `clean()` | equal ⇒ `"Pick two different players to compare."`, code `invalid` | |
| `BenchmarkParamsForm` (teams/api_forms) | `threshold` | `IntegerField(min_value=0, required=False)` | `5` |
| | `display` | `ChoiceField(mean/median, required=False)` | `"mean"` |

Defaults are applied in `clean_<field>` so that `cleaned_data` is always complete. Codes come from
Django unchanged: missing ⇒ `required`, bad int/date ⇒ `invalid`, below min ⇒ `min_value`, bad choice
⇒ `invalid_choice`, and a `clean()` error ⇒ `field: null`. A negative threshold is a 400 `min_value`
in the API, while HTML still clamps it to 0 (lenient vs strict, as ADR-0039 §3 allows).

## 6. Endpoints

API `data` flattens model instances through private helpers in `matches/api_views.py`. They are
imported by `teams/api_views.py`, or kept as a tiny shared module such as `matches/api_refs.py`, as
the code agent prefers:
`team_ref(t) -> {"id", "name"}`; `player_ref(p) -> {"id", "name", "team": team_ref | None}`;
`round_ref(gr) -> {"id", "round_number", "match_id", "date_played", "team_red": team_ref, "team_blue": team_ref}`.
Pure-module and builder outputs are passed through **verbatim**. That includes `detail_list[*].detail_url`
(an HTML path), `deltas[*].cells`, and dates and datetimes, which become ISO strings via the DRF
encoder. Ticks stay raw and nothing is paginated. Every view keeps `login_not_required` plus the
settings-level `IsAuthenticated`, so an anonymous request gets 403 `not_authenticated`.

| # | Method + path | View / action (`url_path`) | Route name | OK |
|---|---|---|---|---|
| 1 | `POST /api/matches/` | `MatchViewSet.create` | `match-list` | 201 |
| 2 | `POST /api/rounds/` | `GameRoundViewSet.create` | `gameround-list` | 201 |
| 3 | `GET /api/rounds/<pk>/missile-log/` | `GameRoundViewSet.missile_log` (`missile-log`) | `gameround-missile-log` | 200 |
| 4 | `GET /api/rounds/<pk>/heatmap/` | `GameRoundViewSet.heatmap` (`heatmap`) | `gameround-heatmap` | 200 |
| 5 | `GET /api/rounds/<pk>/report/` | `GameRoundViewSet.report` (`report`) | `gameround-report` | 200 PDF |
| 6 | `GET /api/rounds/compare/` | `GameRoundViewSet.compare` (detail=False, `compare`) | `gameround-compare` | 200 |
| 7 | `GET /api/teams/head-to-head/` | `TeamViewSet.head_to_head` (detail=False, `head-to-head`) | `team-head-to-head` | 200 |
| 8 | `GET /api/players/head-to-head/` | `PlayerViewSet.head_to_head` (detail=False, `head-to-head`) | `player-head-to-head` | 200 |
| 9 | `GET /api/players/<pk>/career/` | `PlayerViewSet.career` (detail=True, `career`) | `player-career` | 200 |
| 10 | `GET /api/players/benchmarks/` | `PlayerViewSet.benchmarks` (detail=False, `benchmarks`) | `player-benchmarks` | 200 |
| 11 | `POST /api/simulate-batch/<job_id>/save/` | `SaveBatchGamesAPIView.post` | `api_save_batch_games` | 200 |
| 12 | `GET /api/simulate-batch/save/<save_job_id>/` | `SaveBatchStatusAPIView.get` | `api_save_batch_status` | 200 |
| — | `GET /api/rounds/<pk>/events/` | `events` (existing) | `gameround-events` | 200 |

All actions pass explicit `methods=["get"]` (or POST) and explicit `url_name=` so the names above are pinned.

**`api_urls.py` order** (the extra paths list): `simulate-batch/` → **`simulate-batch/save/<str:save_job_id>/`**
→ `simulate-batch/<str:job_id>/save/` → `simulate-batch/<str:job_id>/`. The save-status path is placed
before `<job_id>/save/` because `simulate-batch/save/save/` matches both patterns. Celery ids are UUIDs,
so a job id of literally `save` does not happen in practice.

### Per-endpoint behaviour, `data`, errors

**1 / 2 — creates.** `MatchViewSet` / `GameRoundViewSet` stay `ReadOnlyModelViewSet` and **gain a
`create` method** (the TeamViewSet precedent, with no `CreateModelMixin`), so PUT/PATCH/DELETE stay
405 with the `Allow` header. The flow is: bind `MatchSetupForm(data=plain_form_data(...), user=request.user)`
(or `SingleRoundSetupForm`) → `validate_form_or_raise` → `create_sandbox_match` / `create_sandbox_round(..., user=request.user)`
→ on `MatchSetupError` raise DRF `ValidationError(str(exc))` → re-fetch through the owned queryset
→ 201 `MatchSerializer` / `GameRoundSerializer` (the detail serializer). `GameRoundViewSet.get_serializer_class`
returns `GameRoundSerializer` for `retrieve` **and** `create`, and `get_queryset` prefetches
`player_states` for both. The row's `manager` is the requesting Account. `match_type` is required (no default).

| Case | Status | code | field |
|---|---|---|---|
| missing team_red / team_blue / match_type | 400 | `required` | the field |
| foreign, unknown, Free Agents or invalid-roster team id; bad match_type; arena_map unconfirmed | 400 | `invalid_choice` | the field |
| same team both sides | 400 | `invalid` "A team cannot play against itself!" | null |
| simulator `ValueError` | 400 | `invalid` str(exc) | null |
| non-object JSON body | 400 | `parse_error` | null |

**3 missile-log.** `get_object()` → `build_missile_log(gr)` (upgrades to combat). `data =
{"round_id", "fired", "hit", "efficiency", "rows"}`.
**4 heatmap.** `get_object()` → `build_heatmap(gr)` (upgrades to full). `data = {"round_id",
"has_map", "arena_map": {"id","name"} | null, "zone_size", "processed_image_url",
"cell_occupancy_json", "player_roster"}`.
**5 report.** `get_object()` → `build_round_report(build_round_report_data(gr),
watermark=gr.is_simulated)` → a raw Django `HttpResponse(content_type="application/pdf")` with
`Content-Disposition: attachment; filename="<round_report_filename(gr)>"`. It is **not enveloped**
(raw-file exemption) and does **no** fidelity upgrade. Caveat: a request with `Accept: application/pdf`
only gets a 406 from DRF content negotiation, the same as the roster-template CSV. Clients send
`*/*` or json.
**events (fix).** `BatchSimulator().ensure_fidelity(game_round, "full")` runs right after `get_object()`,
before the queryset is built.
Errors for 3/4/5/events: unknown or cross-Account pk ⇒ 404 `not_found`; non-GET ⇒ 405.

**6 compare.** Validate `CompareRoundsParamsForm` → `get_owned_or_404(GameRound, request, id=…)` for
a then b → if `not shared_team_ids(a, b)` ⇒ `EnvelopeValidationError([{"code": "invalid",
"message": "These rounds share no team, so there is nothing to compare.", "field": None}])` →
`build_round_comparison(a, b, request.user)`. `data = {"round_a": round_ref, "round_b": round_ref,
"stat_keys": list(COMPARE_STAT_KEYS), "team_ids", "points_series", "deltas"}`.

**7 team h2h.** Validate the Form → `get_owned_or_404(Team, …)` a then b → `build_team_h2h`.
`data = {"team_a": team_ref, "team_b": team_ref, "provenance", "date_from", "date_to",
"match_record", "round_record", "score_margin", "avg_survivors", "top_impactful",
"per_map_breakdown", "detail_list", "margin_series", "cumulative_wl_series"}`.

**8 player h2h.** Same pattern with `get_owned_or_404(Player, …)` and `build_player_h2h`.
`data = {"player_a": player_ref, "player_b": player_ref, "role", "provenance", "date_from",
"date_to", "round_record", "score_margin", "tag_stats", "per_role_breakdown", "per_map_breakdown",
"detail_list", "margin_series", "cumulative_wl_series"}`. An empty corpus gives 200 with
zeroed aggregates.

Errors for 6/7/8:

| Case | Status | code | field |
|---|---|---|---|
| id param missing | 400 | `required` | the param |
| id non-int | 400 | `invalid` | the param |
| id < 1 | 400 | `min_value` | the param |
| bad `provenance` / `role` | 400 | `invalid_choice` | the param |
| bad `from` / `to` date | 400 | `invalid` | `from` / `to` |
| same id twice | 400 | `invalid` | null |
| (6) no shared team | 400 | `invalid` | null |
| unknown / cross-Account id (after validation) | 404 | `not_found` | null |
| (6) a shared Team that is not owned | 404 | `not_found` | null |

**9 career.** `get_object()` (owned Player queryset) → validate `BenchmarkParamsForm` →
`build_career_stats(player, min_rounds=threshold, display=display)`. `data = {"player": player_ref,
"total_rounds", "has_rounds", "career", "per_role", "trend", "min_rounds", "display", "stat_keys",
"per_role_with_benchmarks"}`.
**10 benchmarks.** Validate the Form → `build_role_benchmarks(...)`. `data` = the builder dict
**minus `benchmarks_by_role`**: `{"min_rounds", "display", "roles", "stat_keys", "benchmarks", "any_data"}`.
Errors for 9/10: `threshold` non-int ⇒ `invalid`, negative ⇒ `min_value`, bad `display` ⇒
`invalid_choice`, and (9) unknown or cross-Account pk ⇒ 404.

**11 batch save.** The Job id is a capability token with no owner check on it; ownership is
enforced on the inputs. The order is:
(a) `SaveBatchGamesForm(data=plain_form_data(...))` → `validate_form_or_raise`;
(b) `resolve_batch_teams(team_red, team_blue, request.user)`, where `MatchSetupError` becomes
`ValidationError(str(exc))`;
(c) `AsyncResult(job_id)`, and `state != "SUCCESS"` (including PENDING for an unknown id, and FAILURE)
⇒ raise `JobNotComplete`;
(d) take `result.get("avg_seeds" | "outlier_seeds") or []`, sliced `[:n]`, and an empty list ⇒
`ValidationError("No saved seeds for this category.")`;
(e) `save_games_task.delay(team_red_id=, team_blue_id=, seeds=, n=, arena_map_id=, manager_id=request.user.pk)`.

It returns **200** `data = {"job_id": <save job id>}`. 200 matches the existing API-03 batch POST
and the HTML save body (both 200 `{job_id}`), so every Job-enqueue endpoint agrees on one status.
The session is not used.

```python
class JobNotComplete(APIException):           # matches/api_views.py
    status_code = 409
    default_code = "job_not_complete"
    default_detail = "The batch job is not complete."
```

| Case | Status | code | field |
|---|---|---|---|
| missing field | 400 | `required` | the field |
| bad int | 400 | `invalid` | the field |
| `n` outside 1..10 | 400 | `min_value` / `max_value` | `n` |
| bad game_type | 400 | `invalid_choice` | `game_type` |
| same team | 400 | `invalid` "A team cannot play against itself!" | null |
| foreign / missing team | 400 | `invalid` "Team {tid} does not exist." | null |
| roster error | 400 | `invalid` roster message | null |
| job not SUCCESS | 409 | `job_not_complete` | null |
| no seeds | 400 | `invalid` "No saved seeds for this category." | null |

**12 save status.** `data = build_save_status_response(AsyncResult(save_job_id))` =
`{"status", "error", "round_ids"}`, byte-identical to the HTML `save_batch_status` JSON. It never
4xxs (unknown id ⇒ `running`).

**`SimulateBatchAPIView` (changed).** The inline same-team / `Team.objects.get` / roster block is
replaced by `resolve_batch_teams(team_red_id, team_blue_id, request.user)` →
`ValidationError(str(exc))`. Messages and order are unchanged, but a foreign team id is now
"Team {tid} does not exist." This closes the ANG-01a follow-up.

## 7. HTML views changed

| View | Change | Output guarantee |
|---|---|---|
| `create_match`, `create_single_round` | Form gets `user=request.user`; body → `create_sandbox_*`, `except MatchSetupError as exc: messages.error(request, str(exc))` | Same template, context, messages and redirect. Only the team **choices** narrow (to owned + Unmanaged, regular()) |
| `simulate_batch` | `BatchSimulateForm(request.POST or None, user=request.user)`; may use `check_matchup` (same JSON messages) | Choices narrow; JSON byte-identical |
| `save_batch_games` | Also passes `manager_id=getattr(manager_or_none(request), "pk", None)` | JSON byte-identical. **Behaviour change:** saved rounds are now owned by the saver, not Unmanaged |
| `save_batch_status` | Calls the renamed `build_save_status_response` | byte-identical |
| `compare_rounds` | `all_rounds` → `GameRound.objects.filter(owned_game_round_q(request.user))…`; compare mode → `context.update(build_round_comparison(a, b, request.user))` (minus `team_ids`) | Byte-identical for owned data; picker narrows |
| `head_to_head` | `all_teams` → `owned_queryset(Team.objects.all(), request.user).order_by("name")`; picker preselect lookups go through the same owned qs (closes a name leak); results → `build_team_h2h` | Byte-identical for owned data |
| `player_head_to_head` | `all_players` → `owned_queryset(Player.objects.select_related("team"), request.user, path="team").order_by("team__name","name")`; preselect lookups scoped; results → `build_player_h2h` | Byte-identical for owned data |
| `missile_log` | `{"round": gr, **build_missile_log(gr)}` | byte-identical |
| `movement_heatmap` | `{"game_round": gr, **build_heatmap(gr)}` | byte-identical |
| `export_round_report` | `build_round_report_data` + `round_report_filename` | PDF bytes and header identical |
| `game_round_detail` | `_player_row` is now imported from services | byte-identical |
| `player_career_stats` | `{"player": player, **build_career_stats(...)}` | byte-identical |
| `role_benchmarks` | `build_role_benchmarks(...)` as the context | byte-identical |

The lenient HTML param parsing (`_parse_provenance`, `_parse_date`, `_coerce_threshold`,
`_coerce_display`, picker/error modes at 200) is **unchanged**.

## 8. Out of scope — flagged, not changed (orchestrator may promote)

- The H2H corpora (`_h2h_matches_qs`, `_h2h_rounds_qs`, `_player_h2h_rounds_qs`), the career round
  set and the benchmark population are **not ownership-filtered**. A foreign Account's Match between
  two Unmanaged Teams can appear in `detail_list` (scores plus round ids, which then 404). Fixing it
  means passing `user` into the builders and filtering by `owned_match_q` / `owned_game_round_q`. That
  changes HTML output, so it is left as is to keep the byte-identical guarantee.
- HTML `save_batch_games` trusts the session's team ids, which come from the status-poll query
  params without an ownership check. It could reuse `resolve_batch_teams`; this is not required here.

## 9. Test boundary

All simulating tests patch `BatchSimulator.ROUND_TICKS` small (`patch.object(BatchSimulator,
"ROUND_TICKS", 40)`). The cross-Account fixture is a second `User` whose Team / Match / GameRound
rows are stamped to it. Assertions go on envelope `data` / `errors[*].{code,field,message}` and status.

| File (new unless noted) | Asserts |
|---|---|
| `matches/tests/test_api_match_create.py` | 201 + serializer keys for both creates; `manager` == request user; same-team / required / invalid_choice (foreign, Free Agents, invalid roster) table; simulator `ValueError` ⇒ 400 (patch `simulate_match` to raise: this is a failure-mode test); PUT/PATCH/DELETE detail ⇒ 405; anonymous ⇒ 403 |
| `matches/tests/test_api_round_analytics.py` | missile-log / heatmap / report / events: `data` key sets; **fidelity upgrade as real state** (a batch-saved `scores` round becomes `combat` after missile-log, `full` after heatmap and after events, and stays `scores` after report); report `Content-Type` + exact `Content-Disposition`, body starts `%PDF`, no envelope; cross-Account ⇒ 404 |
| `matches/tests/test_api_compare_rounds.py` | data keys + `deltas`/`points_series` equal to `build_round_comparison`; error table (required, invalid, same id ⇒ field null, no shared team, 404 foreign) |
| `matches/tests/test_api_h2h.py` | team + player h2h: data keys, refs flattened, defaults (`provenance` "all", `role` null), error table, a same unknown id twice ⇒ 400 (not 404), foreign ⇒ 404, `/api/teams/head-to-head/` not swallowed by the pk route |
| `matches/tests/test_api_batch_save.py` | end-to-end eager: batch POST → save POST 200 `{job_id}` → status `complete` with `round_ids`; saved rounds' `manager` == user; 409 `job_not_complete` for an unknown id; empty-seed 400 (stub `AsyncResult` in `matches.api_views` with SUCCESS + `{"avg_seeds": []}`); foreign team ⇒ 400 "Team {id} does not exist." for **both** batch POST and save; Form error table |
| `matches/tests/test_batch_tasks.py` (extend) | `save_games_task(..., manager_id=u.pk)` stamps every returned round; default leaves rounds Unmanaged |
| `matches/tests/test_services.py` | `check_matchup` order/messages; `resolve_batch_teams` (foreign == missing message); `create_sandbox_*` stamps the manager and maps `ValueError`; each builder's key set; `build_round_comparison` raises `ValueError` without a shared team |
| `matches/tests/test_setup_form_scoping.py` | the 3 Forms with `user=` exclude foreign + Free Agents; HTML `create_match` with a foreign id ⇒ form error (no Match created); HTML h2h / player-h2h / compare pickers exclude foreign rows; HTML batch save stamps the manager |
| `teams/tests/test_api_career.py` | career + benchmarks: data keys, defaults (5 / "mean"), `benchmarks_by_role` absent, error table, foreign Player ⇒ 404 |
| `teams/tests/test_services.py` (extend) | `build_career_stats` / `build_role_benchmarks` key sets, and no-rounds ⇒ `per_role_with_benchmarks == []` |

Internal, so not asserted directly: the private `_ref` helpers, the moved `_h2h_*` helpers (they are
covered through the builders), and the exact way `from`/`to` are injected into the Forms.

**Blast radius (existing tests that must change):**
- `matches/tests/test_apis.py::TestMatchAPI::test_write_methods_not_allowed` (line 60) and
  `TestGameRoundAPI::test_write_methods_not_allowed` (line 170): POST to the list is now **400
  `required`**, not 405. Rewrite them to assert POST {} ⇒ 400 and PUT/PATCH/DELETE on the detail ⇒ 405.
- Any HTML test that posts the create / batch forms with a Team stamped to a **different** Account
  now fails with a form error. Expected to be none, because the autouse login is the shared manager
  and test Teams are Unmanaged.
- `matches/tests/test_batch_views.py` save spy: unaffected (the extra `manager_id` kwarg is ignored
  by its merged-dict check). `test_batch_tasks.py` positional `apply(args=(…5…))`: unaffected.
- `views_tests.py` imports of `_shared_team_ids` / `_comparison_row` / `_cumulative_team_points` /
  `_player_row` from `matches.views` keep working through the re-exports.
- Docs: `matches/CLAUDE.md` "(any write ⇒ 405)", the REST table, the URL list, and the "Known
  follow-up" batch-scoping note are now stale (docs agent).
