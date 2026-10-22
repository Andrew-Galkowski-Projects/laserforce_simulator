# teams/

Manages teams, players, and rosters, mounted at `/teams/` (the homepage `/` is the `core` landing
mode-picker). The Angular SPA's Teams screens (ANG-06a: list, detail, create, edit, assign slots; ANG-06b: add /
edit player) read
the same data through `/api/teams/`; see `frontend/CLAUDE.md`. The Django templates stay until ANG-08.

This file is the **index**. Feature detail lives in `docs/agents/teams/` — read the doc a pointer
names before touching that feature, and add new feature notes there, not here.

## Models (`teams/models.py`)

**`Team`**: Has exactly 6 `Player` slots — one each of Commander, Heavy, Scout, Medic, Ammo, plus one duplicate role.

**`Player`**: Belongs to a team and has an assigned role. Carries 19 numeric stats (0–100) used as weights by the simulator:

| Category | Fields |
|----------|--------|
| Awareness | `player_awareness`, `game_awareness`, `resource_awareness` |
| Decision-making | `decision_making` |
| Physical | `positioning`, `stamina`, `speed`, `flexibility`, `adaptability` |
| Team | `communication`, `teamwork` |
| Role | `Offensive_synergy`, `defensive_synergy`, `midfield_synergy`, `resupply_synergy`, `resupply_efficiency`, `accuracy`, `survival`, `special_usage` |

`overall_rating` is a `@property` returning the unweighted mean of all 19 stats.

Player field history (LG-04 development, LG-05 `potential`, FIN-01 `salary`, FIN-04 `games_unavailable`),
`stat_for_simulation`, `PlayerForm`, `_random_player_profile()`, `Team.is_draw_team` (LG-02x-1), the Team
finance fields (FIN-01 / FIN-04), `Team.manager` (UX-01) and the Self-insert link (UX-02):
`docs/agents/teams/models-and-fields.md`.

> ### ⚠️ Naming hazard: `Team.manager` is NOT `Team.managed_in_leagues`
>
> | Name | What it is | Direction |
> |---|---|---|
> | `Team.manager` | **NEW (UX-01).** The FK above — the **Account** that owns this Team. | Team → Account |
> | `Team.managed_in_leagues` | **PRE-EXISTING.** The reverse accessor of `League.current_team` — the Leagues in which this Team is the manager's **career seat**. | Team → Leagues |
>
> Near-homographs pointing in **opposite directions** at unrelated concepts. `Team.manager` is set on
> **every** generated AI Team, so it is emphatically *not* the career seat; the career seat is
> `League.current_team` (and its reverse, `managed_in_leagues`).
>
> Second hazard: the **Manager** (this Account FK) is **not** the **Owner** — that is the fictional
> boss of [ADR-0026](../../docs/adr/0026-manager-firing-owner-mood.md) (`OwnerEvaluation`,
> `owner_mood.py`), never a login and deliberately unrenamed. Do not introduce the word "owner" for
> an Account.

Team.manager detail: `docs/agents/teams/models-and-fields.md`; permission seam:
[`../accounts/CLAUDE.md`](../accounts/CLAUDE.md), [ADR-0038](../../docs/adr/0038-accounts-and-uniform-manager-ownership.md).

## REST API (`teams/api_views.py`, `teams/filters.py`, `teams/serializers.py`, `teams/services.py`)

**ANG-01a** turned this into a read/write API. Project-wide conventions (envelope, error codes,
pagination, Form-bound writes, `django-filter`) live in the `laserforce_simulator/api/` package and
[ADR-0039](../../docs/adr/0039-rest-api-envelope-and-form-bound-writes.md) — see **API conventions**
in [`matches/CLAUDE.md`](../matches/CLAUDE.md). Every response here is
`{"data": ..., "meta": {...}}` / `{"errors": [{code, message, field}], "meta": {}}`. Serializers are
**output-only**; every write binds the same Django `Form` the HTML view binds. Ownership:
`get_queryset()` is `owned_queryset(...)` (Players via `path="team"`), so another Account's Team /
Player is **404 on every read and write**, and the row is left unchanged.

| Method + path | Action (route name) | Input (Form) | Success |
|---|---|---|---|
| GET `/api/teams/` | `list` (`team-list`) | — | 200 paginated `TeamListSerializer` |
| POST `/api/teams/` | `create` (`team-list`) | `TeamForm`; stamps `manager_or_none(request)` | 201 `TeamSerializer` |
| GET `/api/teams/<pk>/` | `retrieve` (`team-detail`) | — | 200 `TeamSerializer` |
| PATCH `/api/teams/<pk>/` | `partial_update` (`team-detail`) | `TeamForm` over `merged_form_data` (only sent fields change; `{}` = no-op) | 200 `TeamSerializer` |
| PUT `/api/teams/<pk>/slots/` | `slots` (`team-slots`) | `TeamSlotForm(instance=team)` — **full replace**: an omitted or `null` slot is cleared | 200 `TeamSerializer` |
| POST `/api/teams/<pk>/players/` | `add_player` (`team-players`) | `PlayerForm` over `{**player_form_initial(), **sent}` + `full_clean()` | 201 `PlayerSerializer` |
| POST `/api/teams/generate/` | `generate` (`team-generate`) | `GenerateLeagueForm` (no defaults merged — omitted `num_teams` ⇒ 400 `required`) | 201 `{created_teams: [TeamList], free_agent_count}` |
| POST `/api/teams/import/` | `import_roster` (`team-import`) | `RosterImportForm` — **multipart only** (JSON ⇒ 415) | 201 `{created_teams, appended_teams, player_count, row_count}` |
| GET `/api/teams/import/template/` | `import_template` (`team-import-template`) | — | 200 raw `text/csv`, **not enveloped**, byte-identical (body + `Content-Disposition`) to the HTML `import_roster_template` |
| GET `/api/players/` | `list` (`player-list`) | — | 200 paginated `PlayerSerializer` |
| GET `/api/players/<pk>/` | `retrieve` (`player-detail`) | — | 200 `PlayerSerializer` |
| PATCH `/api/players/<pk>/` | `partial_update` (`player-detail`) | `PlayerForm` over `merged_form_data` + `full_clean()`; `team` is not a form field (cannot move a Player) | 200 `PlayerSerializer` |
| DELETE `/api/players/<pk>/` | `destroy` (`player-detail`) | — | **204, empty body** (slot FKs `SET_NULL`) |
| GET `/api/teams/head-to-head/?team_a=&team_b=[&provenance=&from=&to=]` | `head_to_head` (`team-head-to-head`, ANG-01b) | `TeamH2HParamsForm` (`matches/api_forms.py`) | 200 `{team_a, team_b, provenance, date_from, date_to, match_record, round_record, score_margin, avg_survivors, top_impactful, per_map_breakdown, detail_list, margin_series, cumulative_wl_series}` via `matches.services.build_team_h2h` |
| GET `/api/players/head-to-head/?player_a=&player_b=[&role=&provenance=&from=&to=]` | `head_to_head` (`player-head-to-head`, ANG-01b) | `PlayerH2HParamsForm` (`matches/api_forms.py`) | 200 `{player_a, player_b, role, provenance, date_from, date_to, round_record, score_margin, tag_stats, per_role_breakdown, per_map_breakdown, detail_list, margin_series, cumulative_wl_series}` via `matches.services.build_player_h2h`; empty corpus ⇒ zeroed aggregates |
| GET `/api/players/<pk>/career/[?threshold=&display=]` | `career` (`player-career`, ANG-01b) | `BenchmarkParamsForm` (`teams/api_forms.py`) | 200 `{player, total_rounds, has_rounds, career, per_role, trend, min_rounds, display, stat_keys, per_role_with_benchmarks}` via `build_career_stats` |
| GET `/api/players/benchmarks/[?threshold=&display=]` | `benchmarks` (`player-benchmarks`, ANG-01b) | `BenchmarkParamsForm` | 200 `{min_rounds, display, roles, stat_keys, benchmarks, any_data}` via `build_role_benchmarks(..., user=request.user)` (the template-only `benchmarks_by_role` is dropped); **UXP-14:** scoped to the caller's own + Unmanaged Rounds |

**405s (locked):** PUT / DELETE `/api/teams/<pk>/` (**there is no team DELETE**), GET on `slots/` and
`players/`, POST / PUT `/api/players/`, PUT `/api/players/<pk>/`, GET on `generate/` and `import/`,
POST on `import/template/`. The three `detail=False` actions register before the detail route, so
`generate` / `import` never resolve as a `pk` (the ANG-01b `head-to-head` / `benchmarks` actions
likewise).

**Write rules worth knowing.** A slot holding a player not on this Team (including another
Account's) ⇒ 400 `invalid_choice`, `field: "slot_<x>"`, and the message **never names** the foreign
player. Out-of-range stats ⇒ 400 `max_value` / `min_value` on that field. `GenerateLeagueForm.clean`'s
cross-field error ⇒ `field: null`. Free-agent mode (`num_teams="0"`) returns `created_teams: []`,
`free_agent_count: N`, still 201. Roster parse / DB-collision errors map one-per-`RowError` to
`{"code": "roster_invalid", "message": e.message, "field": "row:<n>:<column>" | "row:<n>"}` via
`roster_import_errors_to_envelope` (`row:0` = file-level / header errors); the import is atomic, so
any error writes nothing.

**Services (`teams/services.py`, ANG-01a).** Shared by the HTML views and the API so both run one
code path (HTML output byte-identical):

| Name | What |
|---|---|
| `run_player_generation(cleaned_data, *, manager) -> GenerationResult` | `@transaction.atomic`; the post-validation half of `generate_players`, same RNG order; `GenerationResult(created_teams, free_agent_count)` (frozen dataclass) |
| `run_roster_import(csv_text, *, manager) -> RosterImportResult` | parse → `_check_db_slot_collisions` → `_apply_roster` inside one `transaction.atomic()`; `RosterImportResult(created_teams, appended_teams, player_count, row_count)`; `RosterImportError` propagates after rollback |
| `resolve_import_teams(names, *, manager) -> dict[str, Team]` | name → Team over `owned_queryset(...)` (own + Unmanaged), highest id wins on a duplicated name |
| `fill_preferred_slot(team, player) -> str \| None` | UXP-19: after HTML `player_add` / API `add_player` saves a Player, put it in the first **empty** slot of its `preferred_roles` (in list order; Scout = first empty of `slot_scout_1` / `slot_scout_2`); never displaces; returns the slot field or `None` (bench) |
| `player_form_initial() -> dict` | the 19 stat defaults (50) + one `_random_player_profile()` draw — the HTML `player_add` GET initial **and** the API add-player defaults |
| `annotate_overall_rating(qs)` | `overall_rating_db = (sum of 19 stats) / 19.0`, lifted from `player_list` |
| `build_roster_template_csv() -> str` | the LG-00b template body (`ALL_COLUMNS` + `_ROSTER_TEMPLATE_ROWS`) |

The LG-00 / LG-00b private helpers **moved here** (`_pop_unique_name`, `_team_name_exists`,
`_player_name_exists_on_team`, `_resolve_count_marker`, `_build_player_kwargs`, `_assign_team_slots`,
`_generate_teams`, `_generate_free_agents`, `_check_db_slot_collisions`, `_apply_roster`,
`_ROSTER_TEMPLATE_ROWS`) and are **re-exported from `teams/views.py`**, so
`from teams.views import _generate_teams, _apply_roster, ...` (League / Tournament views, tests) still
works. To monkey-patch a dependency such as `draw_stats`, patch `teams.services`, not `teams.views`.
`_SORT_KEYS` / `_coerce_*` stay in `teams/views.py`.

Analytics reads, filters + ordering, the ANG-01b builders, the roster-import ownership fix and the
serializers: `docs/agents/teams/api.md`.

## URLs

```
/teams/     → team list, team CRUD, player management (`/` is core's landing page)

/api/teams/                    → TeamViewSet (list, create, detail, PATCH)
/api/teams/<id>/slots/         → PUT: full-replace the 6 roster slots
/api/teams/<id>/players/       → POST: add a Player to the Team
/api/teams/generate/           → POST: LG-00 generation
/api/teams/import/             → POST (multipart): LG-00b roster import
/api/teams/import/template/    → GET: roster template CSV (raw, not enveloped)
/api/teams/head-to-head/       → GET: ANG-01b HX-03 team head-to-head ("team-head-to-head")
/api/players/                  → PlayerViewSet (list, detail, PATCH, DELETE)
/api/players/head-to-head/     → GET: ANG-01b HX-04 player head-to-head ("player-head-to-head")
/api/players/<id>/career/      → GET: ANG-01b HX-01 career stats ("player-career")
/api/players/benchmarks/       → GET: ANG-01b HX-02 role benchmarks ("player-benchmarks")
```

## Tests

`teams/tests/` package — split by concern:
- `test_models.py` — roster validation (FIX-01 coverage)
- `test_serializers.py` — PlayerSerializer, PlayerInlineSerializer, TeamSerializer, TeamListSerializer
- `test_apis.py` — HTTP-level tests for `/api/teams/` and `/api/players/` (reads through the ANG-01a envelope: `body["data"]`, `body["meta"]["count"]`)
- `test_api_writes.py` — ANG-01a writes: team create / PATCH / slots PUT / add player, generate (both modes), roster import (multipart, `row:<n>:<field>` errors, rollback, 415 on JSON), template CSV byte-equality, player PATCH / DELETE 204, every locked 405, cross-Account 404 on every write, manager stamping
- `test_api_filters.py` — ANG-01a `TeamFilterSet` / `PlayerFilterSet` (free-agent default exclusion, `include_free_agents`, typed-filter 400s) and player ordering over the 23 `_SORT_KEYS`
- `test_services.py` — ANG-01a `teams/services.py` (`GenerationResult` / `RosterImportResult` fields, stamping, rollback, `resolve_import_teams` scope, `player_form_initial`, `annotate_overall_rating`, `build_roster_template_csv`)
- `test_roster_import_ownership.py` — ANG-01a leak regression across HTML, API and service: importing a CSV that names another Account's Team creates a new stamped Team and never echoes the foreign player's name
- `test_forms.py` — `PlayerForm` stat field completeness (all 19 fields present, defaults to 50) and save behavior
- `test_models.py` — `_random_player_profile()` output validation (keys, value ranges, source lists); `stat_for_simulation` boost logic; roster validation
- `test_career_stats.py` — HX-01 pure-unit tests for `teams/career_stats.py` (4 public functions, empty inputs, sum/sum tag-ratio direction, Heavy `SPECIAL_COST` fallback, `was_eliminated_at=1801` capping to 1800, role ordering, `rolling_mean` partial-then-full window, `points_trend` `(date, game_round_id)` tiebreaker, "no Django imports leaked" defensive check) **plus** Django `TestCase` view tests for `/players/<player_id>/stats/` (200 + 6 context keys, 200 empty state, 404 missing player, "Career stats" link on player detail)
- `test_role_benchmarks.py` — HX-02 pure-unit tests for `teams/role_benchmarks.py` (6 public functions × empty / single-player / multi-player populations; threshold filtering; `mvp` derived-stat per-round-mean rule; `accuracy` sum/sum ratio shape pinned against an asymmetric two-player case; subject-inclusion policy — standalone row equals overlay cell for the same player; percentile-of-max equals `100.0`; "no Django imports leaked" defensive check mirroring the HX-01 / RES-04 / RV-03 precedent)
- `test_role_benchmarks_view.py` — HX-02 Django `TestCase` view tests (`GET /players/benchmarks/` 200 + 5 `benchmark-table-{role}` ids in body; `?threshold=` + `?display=` query-param parsing; malformed-param fallback to defaults; `benchmark-no-data-notice` substring on empty DB; `— (need N+ rounds)` substring on below-threshold subjects; HX-01 page `/players/<id>/stats/` surfaces the new `per_role_with_benchmarks` / `min_rounds` / `display` / `stat_keys` context keys and the `role-benchmarks-link` anchor)
- `test_role_benchmarks_cache.py` — HX-02 cache-behaviour tests (`PlayerRoundState.save()` + `.delete()` bump `role_benchmark_version` via the `teams/signals.py` handler; `BatchSimulator._flush_to_db` invokes `invalidate_role_benchmarks()` so a `bulk_create` round still busts the cache; single-scan-fills-all-keys hit-rate check — second request after invalidation hits cache for every `(role, stat)`; threshold-independence — toggling `?threshold=` does not bust the cache)

## Feature docs (`docs/agents/teams/`)

Paths below are relative to the repo root.

- Player field history (LG-04 development, LG-05 `potential`, FIN-01 `salary`, FIN-04 `games_unavailable`), `stat_for_simulation`, `PlayerForm`, `_random_player_profile()`, `ROLE_STATS`, `Team.is_draw_team` + draw-team `roster_errors` relaxation, Team finance fields / budgets / `injury_policy`, `Team.manager` (UX-01), Self-insert / "You" badge (UX-02), `PLAYER_NAMES` / `LASERFORCE_SITES` constants — `docs/agents/teams/models-and-fields.md`
- API analytics reads (head-to-head, career, benchmarks params), `TeamFilterSet` / `PlayerFilterSet` + ordering, `build_career_stats` / `build_role_benchmarks`, roster-import ownership fix, serializer split / `PlayerSerializer` fields (ANG-01a, ANG-01b, ANG-06a, ANG-06b) — `docs/agents/teams/api.md`
- Career stats page, `teams/career_stats.py`, 12-stat benchmark extension, Role benchmarks, percentile rank, benchmark cache + ownership scope (HX-01, HX-01b, HX-02, UXP-14) — `docs/agents/teams/career-and-benchmarks.md`
- Bulk generation, `GenerateLeagueForm`, `teams/player_generator.py`, League spread / tier means, CSV roster import + template (LG-00, CRE-02, LG-00b) — `docs/agents/teams/generation-and-import.md`
- Sortable / paginated `/players/` tab, `_SORT_KEYS`, `?per_page=` (LG-00c) — `docs/agents/teams/players-tab.md`
