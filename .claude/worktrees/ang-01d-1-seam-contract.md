# ANG-01d-1 seam contract — League lifecycle + reads API

Branch `ang-01d-1-league-lifecycle-api` (stacked on ANG-01c). Binds the code, tests and docs agents.
Conventions: ADR-0039 + `matches/CLAUDE.md` "API conventions". **Views raise, never build a 4xx
`Response`.** Every League-tree row resolves through `owned_queryset` / `get_owned_or_404`
(cross-Account ⇒ 404, anonymous ⇒ 403). No migration, no simulation change, no Score Calibration
re-baseline, no ADR. The API never writes (or pops) `request.session["last_league_id"]`. All paths
are relative to `laserforce_simulator/`. **ANG-01d-2 (the play loop) is out of scope** — see §9.

Items marked **[DBC]** are DECIDED BY CONTRACT (beyond the locked grill list) — review them.

## 0. Facts verified in code (they drive several decisions)

- `league_views.py` imports `_generate_teams`, `_generate_free_agents`, `_coerce_dir` from
  `teams.views` and `_celery_state_to_job_status` from `.views`. `teams.views` imports nothing
  from `matches`, so `league_services` may import the two generators at module scope.
- **Test patch targets on `matches.league_views.*`** (whole repo): `random.Random` ×17,
  `AsyncResult` ×16, `play_playoffs_task.delay` ×4. The last two are play-loop (stay). The
  `random.Random` patch resolves `matches.league_views.random` → the **global** `random` module, so
  it reaches moved code too — *provided* `league_services` calls `random.Random()` through
  `import random` (never `from random import Random`) and `league_views` keeps `import random`
  (it still needs it for `resolve_injuries_for_fixture` / `member_night_setup`).
- **No test patches a moved helper.** Tests and modules *import* moved names from
  `matches.league_views` (`test_league_create`, `test_league_templates`, `test_league_spread`,
  `test_ownership`, `test_league_next_season`, `test_reassign_team`, `test_regional_playoffs`,
  `test_injury_resolution`, `test_team_finances_writer`, `test_finance_money_axis`,
  `test_finance_toggle`, `test_owner_evaluations_writer`, `test_league_dashboard`,
  `test_league_history`, `test_manage_conferences`, `test_regional_playoffs_drain`,
  `test_worlds_tournament`; `league_screens/{team_finances,player_detail}.py`) ⇒ every moved name
  is re-exported (§2.3). `matches/models.py` mentions moved helpers in docstrings only.
- `_build_play_controls_context` (stays — it is 01d-2 territory) calls `_playoff_cursor_keys`
  (moves); `_render_live_play_panels` and `_render_season_dashboard_error` (stay) call
  `_build_dashboard_context` (moves). They import the moved names back from `league_services`.
- HTML `next_season` is `@transaction.atomic` and the fired branch is a **redirect**, so the lazy
  `_ensure_team_finances` / `_ensure_owner_evaluations` writes **commit**. Both writers are
  idempotent and deterministic (no RNG), so re-deriving them later yields identical rows.
- HTML `reassign_team` has **no active-Season guard** (a POST while a Season is in progress makes a
  second non-completed Season — `Season.objects.create` bypasses `Season.clean`) and **no fired
  check**. `new_team_picker` / `reassign_team` / `league_delete` answer non-career with **400**.
- `_validate_conference_partition` iterates **enrolled** ids only (a non-enrolled id is silently
  ignored) and takes a `dict` (duplicate membership is unrepresentable). The HTML indexed parse can
  produce neither case; a JSON body can.
- `core.forms.ParsedJSONField` (`empty_values=[None]`, no `json.loads`) is the ANG-01c precedent.
- `CreateLeagueForm` bound with data does **not** fall back to `initial`: `league_name`,
  `season_name`, `start_date`, `num_teams`, `mean`, `std_dev`, `map_mode` are required;
  `schedule_format` is `disabled` (initial always wins); booleans accept JSON bools;
  `map_pool` accepts a JSON id list.
- DRF `SimpleRouter` routes detail=False actions before `<pk>/`, so `leagues/templates/` is safe.
- The envelope handler maps a **dict** `detail` key straight to `field` (`_flatten`). That is the
  mechanism for `season_in_progress`'s `season:<id>` pointer (the `roster_invalid` `row:<n>`
  precedent).
- `League.active_season` is a property (1 query). `_build_history_row` issues one
  `PlayerRoundState` query per row (award set).

## 1. Module map

| Module | Status | Contents |
|---|---|---|
| `matches/league_services.py` | **new** | moved League/Season helpers (§2.1) + new builders/orchestrators (§2.2) + 3 domain exceptions |
| `matches/league_api_views.py` | **new** [DBC] | `LeagueViewSet`, `SeasonViewSet`, the 5 `APIException`s, private row flatteners |
| `matches/serializers.py` | changed | `LeagueSerializer`, `SeasonListSerializer`, `SeasonSerializer`, `SeasonPhaseSerializer`, `ConferenceSerializer` |
| `matches/filters.py` | changed | `LeagueFilterSet`, `SeasonFilterSet` |
| `matches/forms.py` | changed | `ConferencePartitionForm` (shared), `LeagueTemplateForm` + `ReassignTeamForm` (API-only) |
| `matches/api_forms.py` | changed | `StandingsParamsForm` |
| `matches/api_refs.py` | changed | `league_ref`, `season_ref`, `conference_ref` |
| `laserforce_simulator/api_urls.py` | changed | `router.register("leagues", …, basename="league")`, `("seasons", …, basename="season")` |
| `matches/league_views.py` | changed | moved bodies deleted; re-export block; 9 HTML views delegate (§7) |
| models, migrations, templates, `league_urls.py`, `season_urls.py`, `tasks.py`, `league_screens/*`, `core/*` | **unchanged** | |

`league_services.py` must **not** import `matches.league_views` (views import services). It imports
`CreateLeagueForm` under `TYPE_CHECKING` only (runtime it only reads `form.cleaned_data`).
`matches/forms.py` imports `_validate_conference_partition` **lazily inside**
`ConferencePartitionForm.clean_conferences` (no import-time `forms ↔ league_services` edge).

## 2. `matches/league_services.py`

### 2.1 Moved verbatim (bodies byte-unchanged; only the name changes where listed)

| Old name in `league_views` | Name in `league_services` | Area |
|---|---|---|
| `SEED_BUDGET_MIN` / `_MAX` / `_SINGLE` | same | creation |
| `_compute_team_overall` | same | creation + standings |
| `_rank_teams_by_strength`, `_pick_manager_team`, `_seed_team_budgets_by_strength`, `_write_baseline_ratings` | same | creation |
| `_template_to_form_data` | **`template_to_form_data`** | creation |
| `_create_league_and_season` | **`create_league_and_season`** (still `@transaction.atomic`) | creation |
| `_developing_players`, `_coaching_effect_by_team`, `_scouting_budget_by_team`, `_develop_league_for_new_season` | same | rollover |
| `_team_winp_for_season`, `_team_wins_games_for_season`, `_classify_playoffs_for_team` | same | rollover |
| `_ensure_team_finances`, `_ensure_owner_evaluations` | same | rollover |
| `_is_career_league` | **`is_career_league`** | rollover |
| `_run_season_rollover` | **`run_season_rollover`** (still undecorated, caller owns atomic) | rollover |
| `WORST_N_ELIGIBLE`; `_eligible_new_teams` | same; **`eligible_new_teams`** | rollover |
| `_delete_team_if_orphaned`, `_league_teardown_targets`, `_league_match_q` | same | teardown |
| `_teardown_league` | **`teardown_league`** | teardown |
| `_build_dashboard_context` | **`build_dashboard_context`** | reads |
| `_playoff_cursor_keys`, `_build_map_config_label` | same | reads |
| `_pick_displayed_season` | **`pick_displayed_season`** | reads |
| `_BRACKET_FINALS_FORMATS`, `_award_round_dict`, `_season_regular_round_dicts`, `_finals_deciding_node`, `_season_finals_mvp`, `_KD_ROLE_LABELS`, `_player_award_labels` | same | reads |
| `_compute_season_award_set` | **`compute_season_award_set`** | reads |
| `_build_history_row` | **`build_history_row`** (13 keys, unchanged) | reads |
| `_member_night_game_dict`, `_member_night_games_for_season`, `_member_night_appearances_for_team` | same | reads |
| `_render_fixture_sides`, `_build_team_schedule_rows` | same | reads |
| `_validate_conference_partition` | same (signature + 3 strings unchanged) | conferences |
| `_STANDINGS_SORT_KEYS` | **`STANDINGS_SORT_KEYS`** | standings |
| `_standings_row_attr`, `_streak_sort_value`, `_standings_sort_value` | same | standings |

**Stay in `league_views`:** `RATING_SORT_KEYS_DISPLAY`, `_STANDINGS_SORT_KEYS_DISPLAY`,
`_manage_conferences_context`, `_build_league_sidebar_links`, every `_coerce_*`,
`_resolve_season_scope`, `_watched_player_ids`, `_build_play_controls_context`,
`_resolve_live_cursor`, `_alive_playoff_node`, `_next_matchday_to_play`,
`_render_season_dashboard_error`, `_build_play_status_response`, `_render_live_play_panels`,
`_redirect_after_create`, the FIN-04 injury helpers (`_INJURY_SLOT_FIELDS`,
`resolve_injuries_for_fixture`, `restore_after_fixture`, `_pick_substitute`, …), and every view.

Import rule for moved code: `import random` + `random.Random()` (patch-safety, §0). Moved imports
come along (`collections.defaultdict`, `dataclasses.replace`, `datetime.date/timedelta`,
`math.ceil`, `development`, `finance`, `owner_mood`, `STAT_FIELDS`, `season_awards`,
`season_dashboard`, `standings.compute_standings`, `LEAGUE_SPREAD_DELTAS`, `compute_tier_means`,
`TEAM_NAMES`, `PLAYER_NAMES`, `_generate_teams`, `_generate_free_agents`, models).

### 2.2 New public surface (signatures and return shapes)

```python
class SeasonInProgressError(Exception):            # .season: Season (the non-completed one)
    def __init__(self, season: Season) -> None
class NoCompletedSeasonError(Exception): ...
class ManagerFiredError(Exception):                # .evaluation: OwnerEvaluation
    def __init__(self, evaluation: OwnerEvaluation) -> None

def season_mode_for(season: Season | None) -> str
    # None -> "none"; state "draft" -> "draft"; "completed" -> "completed"; else "active"
    # (exactly today's league_dashboard ladder).

def latest_completed_season(league: League) -> Season | None
    # max([s for s in league.seasons.all() if s.state == "completed"], key=id) — today's expression.

def start_next_season(league: League) -> Season
    # NOT decorated — the caller owns the atomic boundary (like run_season_rollover).
    # 1. league.active_season is not None      -> raise SeasonInProgressError(league.active_season)
    # 2. latest_completed_season(league) None  -> raise NoCompletedSeasonError
    # 3. _ensure_team_finances(league, latest); _ensure_owner_evaluations(league, latest)
    # 4. eval = OwnerEvaluation(league, latest).first(); eval and eval.verdict == "fired" and
    #    league.current_team_id == eval.team_managed_id   -> raise ManagerFiredError(eval)
    # 5. return run_season_rollover(league, latest)

def reassign_and_roll(league: League, team: Team, latest_completed: Season) -> Season
    # league.current_team = team; league.save(update_fields=["current_team"]);
    # return run_season_rollover(league, latest_completed). Caller owns atomic + all validation.

def ensure_owner_evaluation(season: Season) -> OwnerEvaluation | None
    # _ensure_team_finances(league, season); _ensure_owner_evaluations(league, season);
    # return OwnerEvaluation(league=season.league, season=season).first()  (None: not completed / non-career)

def build_season_standings(season: Season, *, sort: str = "rank", direction: str = "asc") -> dict
    # Today's season_standings body from `is_draft_preview` through the groups loop, verbatim
    # (_draft_group / _live_group / _sort_and_pair become nested or private helpers). Trusts sort
    # (a STANDINGS_SORT_KEYS member) and direction ("asc"|"desc"). Returns, in this key order:
    # {"is_draft_preview": bool,
    #  "standings_groups": [{"conference": Conference | None, "name": str | None,
    #                        "rows_with_teams": list[tuple[StandingsRow | dict, Team | None]]}],
    #  "rows": list[StandingsRow | dict], "rows_with_teams": list[tuple[...]]}
    # Draft rows are the 17-key zeroed dicts; live rows the 17-field StandingsRow.

def build_season_schedule(season: Season) -> dict
    # Today's season_schedule body. Returns {"matchdays": list[dict], "member_night_games": list[dict]}.
    # matchdays[i] = {"matchday", "date", "fixtures"}; fixture dicts keep today's 11 keys incl.
    # Team instances "team_a"/"team_b". < 2 teams -> matchdays [].

def build_team_schedule(displayed_season: Season, team: Team) -> dict
    # Today's team_schedule middle: fixtures + played rounds + _build_team_schedule_rows.
    # Returns {"upcoming": list[dict] (7 keys), "completed": list[dict] (11 keys),
    #          "member_night_appearances": list[dict]}.

def history_seasons(league: League) -> tuple[Season | None, list[Season], dict[int, Team]]
    # Today's league_history query block verbatim: (in_progress_season, completed_seasons
    # newest-first, teams_by_id) — same select_related/prefetch_related.

def build_season_awards(season: Season) -> dict
    # {"awards": AwardSet, "kd_rows": list[tuple[role, label, AwardWinner | None]], "has_rounds": bool}
    # — exactly today's season_awards computation.

def build_conferences_state(season: Season) -> dict
    # {"editable": season.state == "draft", "map_mode": season.map_mode,
    #  "teams": list[Team] (enrolled, sorted by name),
    #  "conferences": [{"id", "name", "ordinal",
    #                   "team_ids": sorted live-M2M ids,
    #                   "map_rotation_ids": list(conf.map_rotation_ids_json or []),
    #                   "starting_team_ids": conf.starting_team_ids_json,           # None pre-activation
    #                   "starting_map_rotation_ids": conf.starting_map_rotation_ids_json}],
    #  "unassigned_team_ids": sorted enrolled ids in no Conference}

@transaction.atomic
def replace_conferences(season: Season, specs: list[tuple[str, list[int], list[int]]]) -> None
    # season.conferences.all().delete(); for ordinal, (name, team_ids, rotation) in enumerate(specs, 1):
    #   Conference.objects.create(season=, name=, ordinal=, map_rotation_ids_json=rotation); conf.teams.set(team_ids)
    # == today's manage_conferences write. [] clears.
```

### 2.3 Re-export block in `league_views.py`

One `from .league_services import (...)  # noqa: F401 - re-exports` importing every name in §2.1
and §2.2 that views code or callers use, then aliases for the renamed ones:
`_template_to_form_data = template_to_form_data`, `_create_league_and_season =
create_league_and_season`, `_is_career_league = is_career_league`, `_run_season_rollover =
run_season_rollover`, `_eligible_new_teams = eligible_new_teams`, `_teardown_league =
teardown_league`, `_build_dashboard_context = build_dashboard_context`, `_pick_displayed_season =
pick_displayed_season`, `_compute_season_award_set = compute_season_award_set`,
`_build_history_row = build_history_row`, `_STANDINGS_SORT_KEYS = STANDINGS_SORT_KEYS`. Every
`from matches.league_views import _x` in §0 keeps working; `league_screens/*` and
`core.context_processors` are **not** repointed [DBC].

**Patch-target hazard (forward rule).** After the move, `patch("matches.league_views._x")` no
longer intercepts calls made from `league_services` or from HTML views that now delegate there.
No current test does this (§0). New tests patch `matches.league_services.<name>` if they must patch
at all; `matches.league_views.random.Random` stays valid (global module).

## 3. Forms

### 3.1 `ConferencePartitionForm` (`matches/forms.py`, shared by HTML and API)

```python
class ConferencePartitionForm(forms.Form):
    conferences = ParsedJSONField()          # from core.forms; required (None/absent -> "required")
    def __init__(self, *args, season: Season, **kwargs) -> None
    def clean_conferences(self) -> list[tuple[str, list[int], list[int]]]
```

`clean_conferences`, in this **locked order** (all errors code `invalid`, field `conferences`):

1. **Shape** — value is a list of dicts; each dict's keys ⊆ `{"name", "team_ids",
   "map_rotation_ids"}` with `name` (str) and `team_ids` (list of ints, bool excluded) required;
   `map_rotation_ids` optional (absent/None ⇒ `""`) and is a **list or a str**. Any violation ⇒
   raise exactly one: `conferences must be a list of {"name", "team_ids", "map_rotation_ids"} objects.`
2. **Membership** [DBC, API-only reachable] — per team id (ascending) appearing more than once
   across all lists: `f"Team {tid} is in more than one conference."`; per id not enrolled in the
   Season: `f"Team {tid} is not enrolled in this Season."`. Any ⇒ raise them all, stop.
3. **Partition** — `names = [c["name"] …]`, `team_to_conf_idx = {tid: i}`, enrolled =
   `{t.id for t in season.teams.all()}`, `min_per_conference = MIN_BRACKET_PARTICIPANTS if
   season.phases.filter(phase_type="tournament").exists() else 2` →
   `_validate_conference_partition(...)`; append its errors (strings unchanged:
   `Conference names cannot be empty.` / `Every team must be assigned to a conference.` /
   `Each conference needs at least 2 teams.` / the `MIN_BRACKET_PARTICIPANTS` variant).
4. **Rotation** — per conference in order: a **str** goes through
   `parse_rotation_ids(raw, valid_map_ids)` unchanged; a **list**: each JSON int is checked against
   `valid_map_ids`, anything else (bool, float, numeric string) ⇒ `Map rotation contains an invalid
   id.`, unknown int ⇒ `Map rotation contains an unknown map id.`, order + duplicates kept.
   `valid_map_ids = set(_maps_with_confirmed_config().values_list("id", flat=True))`.
5. **Guard** — `season.map_mode == "rotate_by_conference" and conferences and any(not ids …)` ⇒
   append `Each conference needs at least 1 rotation map.` **once**.
6. Any errors from 3–5 ⇒ `raise ValidationError([...])` in that order. Else return
   `[(name.strip(), sorted team_ids, rotation_ids), …]` (`[]` for `[]`).

Steps 3–5 reproduce today's HTML list exactly (same strings, same order).

### 3.2 API-only write Forms (`matches/forms.py`) [DBC]

```python
class LeagueTemplateForm(forms.Form):
    template = forms.ChoiceField(choices=[(t.key, t.label) for t in LEAGUE_TEMPLATES])
    # unknown -> invalid_choice; ""/absent -> required. HTML chooser NOT refactored onto it.

class ReassignTeamForm(forms.Form):
    team_id = forms.TypedChoiceField(coerce=int, choices=())
    def __init__(self, *args, eligible_team_ids: list[int], **kwargs) -> None
    # choices = [(i, str(i)) for i in eligible_team_ids]; non-int / ineligible -> invalid_choice,
    # absent -> required. HTML reassign_team keeps its own int parse + three 400 strings.
```

### 3.3 `StandingsParamsForm` (`matches/api_forms.py`)

Bound as `Form(data=plain_form_data(Form, request.query_params))`.

| Field | Rule | Default |
|---|---|---|
| `sort` | `ChoiceField(choices=sorted STANDINGS_SORT_KEYS, required=False)` | `"rank"` (in `clean_sort`) |
| `dir` | `ChoiceField(choices=[("asc","asc"),("desc","desc")], required=False)` | `"asc"` (in `clean_dir`) |

Bad value ⇒ 400 `invalid_choice`, field `sort` / `dir`.

## 4. Serializers + refs (output only)

`matches/api_refs.py`: `league_ref(league) -> {"id","name"}`, `season_ref(season) -> {"id","name"}`,
`conference_ref(conf) -> {"id","name"}` (beside the existing `team_ref` / `player_ref` / `round_ref`).

| Serializer (`matches/serializers.py`) | Fields (this order) |
|---|---|
| `LeagueSerializer` | `id, name, mode, state, visibility, finance_enabled, challenge_fired_luxury_tax, manager` (pk), `current_team` (method → `team_ref`\|null), `free_agent_pool` (pk), `active_season` (method → `season_ref`\|null), `created_at` |
| `SeasonListSerializer` | `id, league` (method → `league_ref`), `name, start_date, state, schedule_format, map_mode, champion_team` (method → `team_ref`\|null), `created_at` |
| `SeasonSerializer` | list fields + `team_ids` (pk list, `source="teams"`), `map_pool_ids` (pk list, `source="map_pool"`), `starting_team_ids_json, starting_map_pool_ids_json, map_rotation_ids_json, starting_map_rotation_ids_json, phases` (nested), `conferences` (nested) |
| `SeasonPhaseSerializer` | `id, ordinal, phase_type, schedule_format, tournament_mode, tournament_format, tournament_cut, final_series_length, semifinal_series_length, quarterfinal_series_length, earlier_series_length, wb_advancers, lb_advancers, swiss_rounds, tournament` (pk\|null) |
| `ConferenceSerializer` | `id, name, ordinal, team_ids` (pk list, `source="teams"`) |

Excluded [DBC]: `Season.active_play_job_id` / `play_cancel_requested` (01d-2 play state).
`LeagueSerializer.active_season` costs one query per League (≤ page size) — accepted [DBC].

## 5. Filter sets (`matches/filters.py`)

| FilterSet | Params | Ordering on the viewset |
|---|---|---|
| `LeagueFilterSet` | `state` (`ChoiceFilter(League.STATE_CHOICES)`), `mode` (`ChoiceFilter(League.MODE_CHOICES)`) | `ordering_fields=("name","created_at","id")`, `ordering=("-id",)`, `ordering_tiebreak=("-id",)` |
| `SeasonFilterSet` | `league` (`NumberFilter(field_name="league")`), `state` (`ChoiceFilter(Season.STATE_CHOICES)`) | `ordering_fields=("start_date","created_at","id")`, `ordering=("-id",)`, `ordering_tiebreak=("-id",)` |

Bad choice ⇒ 400 `invalid_choice` (field = param). A foreign `?league=` id ⇒ empty page (base
queryset already scoped).

## 6. Viewsets + endpoints (`matches/league_api_views.py`)

```python
class SeasonInProgress(APIException):   status_code=409; default_code="season_in_progress"
    default_detail = "The League already has a Season in progress."
    def __init__(self, season: Season) -> None:   # detail = {f"season:{season.pk}": default_detail}
        # -> envelope {"code":"season_in_progress","message":…,"field":"season:<id>"}  [DBC]
class ManagerFired(APIException):       409, "manager_fired",
    "The Manager was fired. Choose a new team before starting the next Season."
class SeasonNotDraft(APIException):     409, "season_not_draft",
    "Conferences can only be edited while the Season is in draft."      # == HTML 400 text
class NoCompletedSeason(APIException):  409, "no_completed_season", "No completed Season in this League."
class NotCareerLeague(APIException):    409, "not_career_league",
    "This action is only available for career (league-mode) Leagues."
    # raised with the HTML string per site: delete -> "Delete League is only available for career
    # (league-mode) Leagues."; candidates/reassign -> "Manager firing applies only in single-player career mode."

@method_decorator(login_not_required, name="dispatch")
class LeagueViewSet(ListOnlyFilterMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                    mixins.DestroyModelMixin, viewsets.GenericViewSet):
    queryset = League.objects.select_related("current_team")       # router introspection
    serializer_class = LeagueSerializer; filterset_class = LeagueFilterSet
    def get_queryset(self): owned_queryset(League.objects.select_related("current_team"), user)
    def create(self, request) -> Response
    def perform_destroy(self, instance: League) -> None   # not career -> NotCareerLeague; teardown_league

@method_decorator(login_not_required, name="dispatch")
class SeasonViewSet(ListOnlyFilterMixin, ReadOnlyModelViewSet):
    queryset = Season.objects.select_related("league", "champion_team")
    filterset_class = SeasonFilterSet
    def get_queryset(self): owned_queryset(Season.objects.select_related("league","champion_team"),
                                           user, path="league")
                            # retrieve: + prefetch_related("phases","conferences__teams","teams","map_pool")
    def get_serializer_class(self): SeasonSerializer for retrieve, else SeasonListSerializer
```

No `update` on either ⇒ PUT/PATCH detail **405**; `POST /api/seasons/` **405** (Seasons are made
by League create / next-season only) [DBC]; `DELETE /api/seasons/<pk>/` **405**. Every action passes
explicit `detail=, methods=, url_path=, url_name=` and resolves via `self.get_object()`.

| # | Method + path | Handler (`url_path`) | Route name | OK |
|---|---|---|---|---|
| 1 | `GET /api/leagues/` | `list` | `league-list` | 200 paginated |
| 2 | `POST /api/leagues/` | `create` | `league-list` | 201 |
| 3 | `GET /api/leagues/templates/` | `league_templates` (detail=False, `templates`) | `league-templates` | 200 |
| 4 | `GET /api/leagues/<pk>/` | `retrieve` | `league-detail` | 200 |
| 5 | `DELETE /api/leagues/<pk>/` | `destroy` | `league-detail` | 204 |
| 6 | `GET /api/leagues/<pk>/dashboard/` | `dashboard` (`dashboard`) | `league-dashboard` | 200 |
| 7 | `GET /api/leagues/<pk>/history/` | `history` (`history`) | `league-history` | 200 paginated |
| 8 | `GET /api/leagues/<pk>/teams/<team_id>/schedule/` | `team_schedule` (`teams/(?P<team_id>[0-9]+)/schedule`) | `league-team-schedule` | 200 |
| 9 | `POST /api/leagues/<pk>/next-season/` | `next_season` (`next-season`) | `league-next-season` | 201 |
| 10 | `GET /api/leagues/<pk>/new-team-candidates/` | `new_team_candidates` (`new-team-candidates`) | `league-new-team-candidates` | 200 |
| 11 | `POST /api/leagues/<pk>/reassign-team/` | `reassign_team` (`reassign-team`) | `league-reassign-team` | 201 |
| 12 | `GET /api/seasons/` | `list` | `season-list` | 200 paginated |
| 13 | `GET /api/seasons/<pk>/` | `retrieve` | `season-detail` | 200 |
| 14 | `GET /api/seasons/<pk>/dashboard/` | `dashboard` | `season-dashboard` | 200 |
| 15 | `GET /api/seasons/<pk>/standings/` | `standings` | `season-standings` | 200 |
| 16 | `GET /api/seasons/<pk>/schedule/` | `schedule` | `season-schedule` | 200 |
| 17 | `GET /api/seasons/<pk>/awards/` | `awards` | `season-awards` | 200 |
| 18 | `GET /api/seasons/<pk>/owner-evaluation/` | `owner_evaluation` (`owner-evaluation`) | `season-owner-evaluation` | 200 |
| 19 | `GET /api/seasons/<pk>/conferences/` | `conferences` (`conferences`, `parser_classes=[JSONParser]`) | `season-conferences` | 200 |
| 20 | `PUT /api/seasons/<pk>/conferences/` | `@conferences.mapping.put` → `replace_conferences` | `season-conferences` | 200 |

### Per-endpoint behaviour and `data`

Flatteners (private in `league_api_views.py`): `_standings_row(row, team) -> dict` = the 17
standings keys in `StandingsRow` field order (tuples ⇒ JSON arrays) + `"team": team_ref | null`
(works for `StandingsRow` via `dataclasses.asdict` and the 17-key draft dict);
`_leader(row) -> dataclasses.asdict(LeaderRow)` (8 keys); `_winner(w) -> asdict(AwardWinner) |
None` (6 keys); `_snippet_row(row_dict, team) -> {**row_dict, "team": team_ref | null}`.
Dates/datetimes ⇒ ISO strings via the DRF encoder. HTML paths in builder output
(`season_url`) pass through verbatim (ANG-01b `detail_url` precedent).

1. **list** — `LeagueSerializer`, owned + Unmanaged Leagues, filters/ordering per §5.
2. **create** — `plain_form_data(LeagueTemplateForm, request.data)` first (non-object ⇒
   `parse_error`). **If the key `"template"` is in the body** (template path): validate
   `LeagueTemplateForm` (⇒ 400 `invalid_choice` / `required`, field `template`, before anything
   else), then bind `CreateLeagueForm(data=template_to_form_data(template,
   league_name=request.data.get("league_name", ""), difficulty=request.data.get("difficulty",
   "medium")))`; every other body key is ignored [DBC]. **Else** (Advanced path):
   `CreateLeagueForm(data=plain_form_data(CreateLeagueForm, request.data))` — no defaults injected,
   missing required fields ⇒ `required` [DBC]. Then `validate_form_or_raise` →
   `season = create_league_and_season(form, manager=request.user)` → re-fetch the League through
   `get_queryset()` → 201 `data = {**LeagueSerializer(league).data, "draft_season":
   season_ref(season), "conference_ids": [c.id for c in season.ordered_conferences()]}`
   (`conference_ids` always present, `[]` when none [DBC]). `manager` == requester.
3. **templates** — `data = [dataclasses.asdict(t) for t in LEAGUE_TEMPLATES]` (keys `key, label,
   num_teams, phases, finance_enabled, challenge_fired_luxury_tax, mean, std_dev, map_mode`), not
   paginated, 5 entries in tuple order.
4. **retrieve** — `LeagueSerializer`.
5. **destroy** — `perform_destroy`: `not is_career_league(league)` ⇒ `NotCareerLeague(<delete
   string>)` (nothing deleted); else `teardown_league(league)` ⇒ 204 empty body. Session untouched.
6. **league dashboard** — `ds = pick_displayed_season(league)`, `mode = season_mode_for(ds)`,
   `body = build_dashboard_context(ds, mode)`. `data = {"league": league_ref, "displayed_season":
   season_ref | null, "season_mode", "standings_snippet": [_snippet_row…], "next_fixture" (dict |
   null, verbatim), "round_count_completed", "round_count_total", "leaders_points"/"leaders_tags"/
   "leaders_ratio": [_leader…], "map_config_label", "playoff_tournament_id"}`. **No** play-control
   keys (`_build_play_controls_context` is 01d-2).
7. **history** — `(in_progress, completed, teams_by_id) = history_seasons(league)`; the list
   `([in_progress] if in_progress else []) + completed` is paginated with `self.paginate_queryset`
   (EnvelopePagination, `?page` / `?page_size`); rows are built **only for the page slice** with
   `build_history_row(s, teams_by_id, is_in_progress=(s is in_progress))`. The in-progress row
   appears once (first item of page 1), **not pinned on every page** like HTML [DBC]. Each `data`
   item = the 13 row keys with `champion` / `runner_up` ⇒ `team_ref | null`, `top_three` ⇒ list of
   3 `team_ref | null`, `season_mvp` / `finals_mvp` ⇒ `_winner(...)`.
8. **team schedule** — `team = get_owned_or_404(Team, request, pk=team_id)` (no enrolment check —
   HTML parity [DBC]); `ds = pick_displayed_season(league)`, `None` ⇒ `NotFound("No Season in this
   League.")`; `data = {"league": league_ref, "season": season_ref(ds), "team": team_ref,
   **build_team_schedule(ds, team)}` (row dicts verbatim — already plain).
9. **next-season** — `@transaction.atomic` around the handler. Order: `start_next_season(league)`;
   `SeasonInProgressError` ⇒ `SeasonInProgress(exc.season)`; `NoCompletedSeasonError` ⇒
   `NoCompletedSeason()`; `ManagerFiredError` ⇒ `ManagerFired()`. 201 `data =
   SeasonSerializer(new_season)` (re-fetched with the retrieve prefetch). **Atomicity:** the
   success path matches HTML (one transaction). On a 409 the exception propagates out of the
   atomic block, so the lazy ensure writes **roll back** (HTML commits them) — harmless, they are
   idempotent and deterministic and re-derived on the next read [DBC]. No request body.
10. **new-team-candidates** — `not is_career_league` ⇒ `NotCareerLeague(<firing string>)`;
    `latest_completed_season` None ⇒ `NoCompletedSeason`. `data = {"latest_completed":
    season_ref, "candidates": [_standings_row(row, team) for row, team in
    eligible_new_teams(league, latest)]}` (worst-first, ≤ `WORST_N_ELIGIBLE`, current team
    excluded). No fired check (HTML parity) [DBC].
11. **reassign-team** — `@transaction.atomic`. Order: not career ⇒ `NotCareerLeague`;
    `league.active_season` ⇒ `SeasonInProgress` (**new vs HTML**, which would create a 2nd
    non-completed Season) [DBC]; no completed ⇒ `NoCompletedSeason`; bind
    `ReassignTeamForm(data=plain_form_data(...), eligible_team_ids=[row.team_id for row, _ in
    eligible_new_teams(...)])` ⇒ 400 `required` / `invalid_choice` field `team_id`;
    `team = get_owned_or_404(Team, request, pk=team_id)`; `new = reassign_and_roll(league, team,
    latest)` ⇒ 201 `SeasonSerializer(new)`. No fired check (HTML parity) [DBC].
12/13. **season list / retrieve** — per §4/§5.
14. **season dashboard** — `body = build_dashboard_context(season, season.state)`; `data =
    {"season": season_ref, "league": league_ref, **flattened body (same keys as #6 minus
    "league")}`.
15. **standings** — `StandingsParamsForm` → `build_season_standings(season, sort=, direction=)`.
    `data = {"season": season_ref, "is_draft_preview", "sort", "dir", "groups": [{"conference":
    conference_ref | null, "name", "rows": [_standings_row(row, team) …]}]}`. Zero-Conference ⇒
    exactly one group with `conference: null`. `rank` is the frozen standings position (never
    renumbered by sort).
16. **schedule** — `b = build_season_schedule(season)`; `data = {"season": season_ref,
    "matchdays": [{"matchday", "date", "fixtures": [fixture with "team_a"/"team_b" ⇒ team_ref |
    null]}], "member_night_games": b["member_night_games"] (verbatim)}`.
17. **awards** — `b = build_season_awards(season)`; `data = {"season": season_ref, "has_rounds",
    "most_points", "best_accuracy", "kd_by_role": {role: _winner(...) for role in ROLE_KEYS},
    "best_medic", "most_efficient_nuke", "season_mvp", "finals_mvp"}` (each winner `_winner(...)`).
18. **owner-evaluation** — `ev = ensure_owner_evaluation(season)` (performs the same lazy
    finance + evaluation writes as HTML); `None` ⇒ `NotFound("No owner evaluation for this
    Season.")` (not completed, or non-career). `data = {"season": season_ref, "league": league_ref,
    "team_managed": team_ref | null, "wins_delta", "playoffs_delta", "money_delta", "wins_total",
    "playoffs_total", "money_total", "overall_mood" (sum of the 3 totals, float), "verdict",
    "hot_seat_level", "fired_reason", "is_fired", "reassigned", "created_at"}` (`is_fired` /
    `reassigned` exactly as HTML).
19. **conferences GET** — `s = build_conferences_state(season)`; `data = {"season": season_ref,
    "editable", "map_mode", "teams": [team_ref…], "conferences": s["conferences"] (verbatim),
    "unassigned_team_ids"}`.
20. **conferences PUT** — `season.state != "draft"` ⇒ `SeasonNotDraft` (409 before any body
    validation [DBC]); `form = ConferencePartitionForm(data=plain_form_data(…, request.data),
    season=season)` → `validate_form_or_raise` → `replace_conferences(season,
    form.cleaned_data["conferences"])` → `data` = the #19 payload (saved state). `{"conferences":
    []}` clears (flat Season). Multipart ⇒ 415.

### Error table

| Case | Status | code | field |
|---|---|---|---|
| unknown / cross-Account League, Season or Team (`team_id`) | 404 | `not_found` | null |
| team-schedule League with no Season; owner-evaluation not completed / non-career | 404 | `not_found` | null |
| history `?page` out of range / non-int | 404 | `not_found` | null |
| anonymous | 403 | `not_authenticated` | null |
| PUT/PATCH league or season detail; POST/DELETE seasons; wrong method on an action | 405 | `method_not_allowed` | null |
| conferences PUT multipart | 415 | `unsupported_media_type` | null |
| non-object JSON body (create, reassign, conferences PUT) | 400 | `parse_error` | null |
| create: template unknown / empty | 400 | `invalid_choice` / `required` | `template` |
| create: any `CreateLeagueForm` error (incl. `clean()` map-mode / conference / phases rules) | 400 | Django's code (`required`, `invalid_choice`, `min_value`, `invalid`, …) | the field |
| list filter bad choice / non-int | 400 | `invalid_choice` / `invalid` | `state` / `mode` / `league` |
| standings bad `sort` / `dir` | 400 | `invalid_choice` | `sort` / `dir` |
| reassign `team_id` missing / non-int / ineligible | 400 | `required` / `invalid_choice` / `invalid_choice` | `team_id` |
| conferences body shape / membership / partition / rotation / guard | 400 | `invalid` (`required` when absent) | `conferences` |
| next-season or reassign while a Season is not completed | 409 | `season_in_progress` | `season:<id>` |
| next-season, Manager fired and not reassigned | 409 | `manager_fired` | null |
| next-season / candidates / reassign with no completed Season | 409 | `no_completed_season` | null |
| delete / candidates / reassign on a non-career League | 409 | `not_career_league` | null |
| conferences PUT on a non-draft Season | 409 | `season_not_draft` | null |

## 7. HTML views (`matches/league_views.py`) — what changes

Rule: each view keeps its guards, `last_league_id` write, sidebar, template and every context key;
only the computation moves behind the service. **Output byte-identical** for every input
(same context values, same status, same redirect, same DB writes).

| View | Change |
|---|---|
| `league_dashboard` | mode ladder ⇒ `season_mode_for(displayed_season)` |
| `season_standings` | body ⇒ `build_season_standings(season, sort=sort, direction=direction)`; keeps `_coerce_sort_key` / `_coerce_dir`, `querystring_without_sort_dir`, `_STANDINGS_SORT_KEYS_DISPLAY`, sidebar; context keys `rows, rows_with_teams, standings_groups, is_draft_preview` from the dict |
| `season_schedule` | body ⇒ `build_season_schedule(season)` (`matchdays`, `member_night_games`) |
| `team_schedule` | rows + appearances ⇒ `build_team_schedule(displayed_season, team)`; `HttpResponseNotFound` + picker + `current_team` unchanged |
| `league_history` | query block ⇒ `history_seasons(league)`; Django `Paginator` + pinned in-progress row unchanged |
| `season_awards` | ⇒ `build_season_awards(season)` (`awards`, `kd_rows`, `has_rounds`) |
| `owner_evaluation` | the two ensures + read ⇒ `ensure_owner_evaluation(season)`; `Http404` unchanged |
| `next_season` | after the 405/404/session steps: `try: new = start_next_season(league)` — `SeasonInProgressError` ⇒ `redirect("season_dashboard", season_id=exc.season.id)`; `NoCompletedSeasonError` ⇒ `HttpResponseBadRequest("No completed Season in this League.")`; `ManagerFiredError` ⇒ `redirect("new_team_picker", league_id=league.id)`. Still `@transaction.atomic`; the exceptions are caught **inside** it, so ensure writes commit exactly as today |
| `reassign_team` | final three statements ⇒ `reassign_and_roll(league, picked, latest_completed)`; its existing guards/400 strings unchanged. **USER-APPROVED BUG FIX:** add an active-Season guard mirroring `next_season` — after the non-career 400 guard and before any write, `if league.active_season is not None: return redirect("season_dashboard", season_id=league.active_season.id)` (302, no second non-completed Season created). Regression test in `test_reassign_team.py`: POST while a draft/active Season exists ⇒ 302 to that Season's dashboard, Season count unchanged, `current_team` unchanged. |
| `manage_conferences` POST | keeps the indexed parse (`names`, `rotations`, `team_to_conf_idx`, the non-draft 400); binds `ConferencePartitionForm(data={"conferences": [{"name": names[i], "team_ids": [t.id for t in teams if team_to_conf_idx.get(t.id) == i], "map_rotation_ids": rotations[i] if i < len(rotations) else ""} for i in range(len(names))]}, season=season)`; invalid ⇒ `errors = [m for e in form.errors.as_data()["conferences"] for m in e.messages]` + today's re-render; valid ⇒ `replace_conferences(season, form.cleaned_data["conferences"])` + success message + redirect |

Unchanged: `league_create`, `league_create_advanced`, `league_list`, `league_delete` (uses the
re-exported teardown names), `new_team_picker`, `season_dashboard`, all play views, the 01z
`league_screens`, `core.context_processors`.

## 8. Test boundary

Tests assert on status, envelope `data` key sets / `errors[*].{code,field,message}`, row counts and
FK identity — **never simulated point totals**. Completed-Season fixtures are hand-built (Match +
GameRound rows, `state="completed"`, `starting_team_ids_json` set) or simulated with
`patch.object(BatchSimulator, "ROUND_TICKS", 40)`. League create uses the real unseeded
generator ⇒ assert structure only. Cross-Account fixture: a second `User` + `APIClient.force_login`.
No mock-returns-mock; patch only `matches.league_services.*` if a patch is unavoidable.

| File (new) | Asserts |
|---|---|
| `matches/tests/test_league_services.py` (TestCase) | `season_mode_for` ladder; `start_next_season` raises each of the 3 errors (carrying `.season` / `.evaluation`) and on success returns a draft Season; `reassign_and_roll` sets `current_team` + rolls; `build_season_standings` key set, one group for 0 Conferences / N for N, sort honoured with `rank` frozen; `build_season_schedule` / `build_team_schedule` / `build_season_awards` / `build_conferences_state` key sets; `ensure_owner_evaluation` None for draft + a row for completed; `replace_conferences` replace / clear; `ConferencePartitionForm`: each step-1/2 message, the 3 partition strings + bracket-floor variant, str vs list rotation (both error strings, order kept), rotate-by-conference guard once, **combined error order == today's HTML list**, `[]` ⇒ `[]` |
| `matches/tests/test_api_leagues.py` (APIClient) | list scoping (foreign hidden), `state`/`mode` filters + 400, ordering; retrieve 200/404; templates (5, key set); create template path 201 (`manager`, `draft_season`, `conference_ids`), unknown/empty template 400, Advanced path 201 + `required` errors + a `clean()` error (map-mode rule), `number_of_conferences=2` ⇒ 2 `conference_ids`; DELETE career 204 + rows gone, non-career 409 `not_career_league`, foreign 404; PUT/PATCH 405; dashboard key set (no play keys) for none/draft/completed; history pagination meta + in-progress first; team schedule 200 / no-Season 404 / foreign team 404; anonymous 403; session `last_league_id` NOT written |
| `matches/tests/test_api_league_lifecycle.py` | next-season: 409 `season_in_progress` (`field == f"season:{id}"`), 409 `no_completed_season`, 409 `manager_fired` (fired fixture via a hand-built `OwnerEvaluation(verdict="fired")`) **with no new Season created**, 201 Season serializer + draft state; candidates 200 (excludes current team, ≤ 5), 409 non-career / no-completed; reassign 400 missing / non-int / ineligible (`invalid_choice`, `team_id`), 409 in-progress / non-career / no-completed, 201 + `current_team` updated |
| `matches/tests/test_api_seasons.py` | list filters (`league`, `state`) + scoping; retrieve nested `phases` / `conferences` / no play fields; season dashboard; standings default + `?sort=team&dir=desc` + 400s + per-Conference groups; schedule shape (team refs, member nights); awards key set incl. 5 `kd_by_role`; owner-evaluation 200 completed (writes the row) / 404 draft / 404 non-career |
| `matches/tests/test_api_conferences.py` | GET state; PUT create / replace / clear 200 returning saved state; PUT on active ⇒ 409 `season_not_draft`; shape / duplicate / non-enrolled / partition / rotation errors (codes + fields); `rotate_by_conference` guard; multipart 415; array body 400 `parse_error`; foreign Season 404 |

**Existing tests (blast radius):** none should change — every moved name is re-exported and HTML
output is byte-identical. The HTML regression net is the existing suites
(`test_manage_conferences`, `test_league_next_season`, `test_reassign_team`, `test_league_history`,
`test_season_awards_view`, `test_owner_evaluation_view`, `test_team_schedule`,
`test_league_dashboard`, `test_season_views`, `test_standings`, `views_tests`). Run them unchanged.

**Docs (docs agent):** new `matches/CLAUDE.md` "ANG-01d-1 League lifecycle + reads API" section
(the §6 table, conflict codes, `league_services` move list) + REST table rows + URL list; the
`CLAUDE.md` REST API paragraph (`league_services.py` sharing, ANG-01d-1 done, 01d-2 pending);
`PLAN.md` ANG-01d split into 01d-1 (done) / 01d-2 (play loop).

## 9. Out of scope / flagged

- **ANG-01d-2 (play loop):** start season, play week / two-months / until-end / playoffs / single
  round, One Week (Live), Job status + cancel, member-night setup/play, the topnav play-control
  keys. None of `_build_play_controls_context`, the play views or `tasks.py` move or change.
- HTML `reassign_team` gains the active-Season guard (user-approved fix, see §7); it still has no
  fired check (HTML parity with the API, which also has none).
- HTML `team_schedule` and the API resolve any owned Team, enrolled or not (parity).
- History API does not pin the in-progress row on every page.
- `LeagueSerializer.active_season` is per-row; acceptable at page size 20.
- `league_screens/*` / `core.context_processors` still import via the `league_views` re-exports.
