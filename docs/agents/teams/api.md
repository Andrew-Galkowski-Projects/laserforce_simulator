# teams: REST API detail

ANG-01b analytics reads, list filters + ordering, the ANG-01b service builders, the ANG-01a roster-import ownership fix and the serializer split. The endpoint table, write rules and the ANG-01a services table stay in the app index.
Moved verbatim from `laserforce_simulator/teams/CLAUDE.md`; the app index there points here.

## Analytics reads (ANG-01b)

**Analytics reads (ANG-01b).** Model instances are flattened to refs — `team_ref = {id, name}`,
`player_ref = {id, name, team: team_ref | null}`; builder output is passed through verbatim
(including `detail_list[*].detail_url`, an HTML path). Query params are **strict** param Forms bound
via `plain_form_data(Form, request.query_params)` + `validate_form_or_raise` (the HTML pages keep their
lenient parsing): missing id ⇒ `required`, non-int / bad date ⇒ `invalid`, id < 1 / negative
`threshold` ⇒ `min_value`, bad `provenance` / `role` / `display` ⇒ `invalid_choice`, the same id twice
⇒ `invalid` with `field` null (checked **before** the lookup, so an unknown id repeated is 400, not
404). Rows then resolve through `get_owned_or_404` (head-to-head) or `get_object()` (career) — unknown
/ cross-Account ⇒ 404. Defaults: `provenance` "all", `role` null, `threshold` 5, `display` "mean".
`BenchmarkParamsForm` lives in **`teams/api_forms.py`** (new). Known gap (PLAN.md Deferred Items): the
head-to-head round sets and the career page's own round set are not ownership-filtered. The
**benchmark population** (the standalone page and the career overlay) **is**, since UXP-14 — see
**HX-02 role benchmarks → Ownership scope**.

## Filters + ordering

**Filters + ordering** (`teams/filters.py`, list only — detail / write lookups ignore query params,
so the Free Agents Team is still reachable by id):

- `TeamFilterSet`: `name` (icontains); `include_free_agents` (boolean). **By default the list excludes
  every free-agent pool Team** — the global "Free Agents" Team and per-League pools, exactly
  `Team.objects.regular()`, matching the HTML `team_list`; `?include_free_agents=true` includes them.
  Ordering: `name` (default), `created_date`, `wins`, `losses`, `id`; tiebreak `id`.
- `PlayerFilterSet`: `team` (id; a foreign / nonexistent id ⇒ 200 empty, no existence leak; `team=abc`
  ⇒ 400), `name` (icontains). Ordering keys = exactly the 23 LG-00c `_SORT_KEYS` (public
  `offensive_synergy` → ORM `Offensive_synergy`, `team` → `team__name`, `overall_rating` → the
  `overall_rating_db` annotation from `annotate_overall_rating`); `preferred_roles` and the raw
  `Offensive_synergy` are not valid terms (silently dropped). Default `team__name, name`; tiebreak
  `name, id`. The `overall_rating_db` annotation never appears in the output; since ANG-06b
  `PlayerSerializer` returns the model property as `overall_rating` instead.

## ANG-01b service builders

**ANG-01b additions.** Two context builders shared by the HTML `player_career_stats` /
`role_benchmarks` views (output byte-identical) and the API:

| Name | What |
|---|---|
| `build_career_stats(player, *, min_rounds, display, user) -> dict` | the HX-01 context minus `player`: `total_rounds`, `career`, `per_role`, `trend`, `has_rounds`, `min_rounds`, `display`, `stat_keys`, `per_role_with_benchmarks` (benchmark overlay only when `total_rounds > 0`, else `[]`) |
| `build_role_benchmarks(*, min_rounds, display, user) -> dict` | the HX-02 context verbatim (UXP-14: over `user`'s own + Unmanaged Rounds): `min_rounds`, `display`, `roles`, `benchmarks`, `stat_keys`, `benchmarks_by_role` (template-only), `any_data` |

The HX-01 / HX-02 helpers `_round_dict_from_state`, `_build_per_role_overlay`,
`_HX01_DISPLAY_STAT_KEYS`, `_HX01_TO_BENCHMARK_STAT`, `_HX01B_STAT_ROW_SPEC` **moved here** and are
re-exported from `teams/views.py`. The team / player head-to-head builders live in
`matches/services.py` (see `docs/agents/matches/api.md` **ANG-01b**).

## Roster-import ownership fix (ANG-01a)

**Roster-import ownership fix (ANG-01a).** Before ANG-01a, `_check_db_slot_collisions` and
`_apply_roster` looked Teams up by bare name (`Team.objects.get_or_create(name=...)`), ignoring
ownership: a CSV naming **another Account's** Team appended Players to it, and a slot collision there
echoed that Account's player names in the error. Both helpers now take a **required keyword
`manager`** (no default, so no caller can silently keep the leak) and resolve through
`resolve_import_teams`: a name matching only a foreign Team is a miss ⇒ a **new Team stamped with the
importer** is created and no foreign name can appear in any message. Appending to an **Unmanaged**
Team (including the global Free Agents Team) stays legal. `get_or_create` is gone (it also raised
`MultipleObjectsReturned` on duplicate names). The HTML `import_roster`, the API import and the
Tournament participant import (`matches.tournament_services.setup.import_participants`, since ANG-01f
called by both `matches/tournament_views/setup.py` and `POST /api/tournaments/<id>/import-participants/`)
all pass `manager_or_none(request)`.

## Serializers

**Serializer split:** `TeamListSerializer` (list) nests `PlayerInlineSerializer` (id, name, preferred_roles) to keep list payloads small. `TeamSerializer` (detail) nests the full `PlayerSerializer` with explicit stat fields. Both share `_TEAM_BASE_FIELDS` and `_PLAYER_STAT_FIELDS` constants so the field lists are defined once. Both also carry read-only `is_valid_roster` (bool) and `roster_errors` (string list) straight from the model properties (ANG-06a, so the Angular app never re-implements the roster rules); `TeamViewSet.get_queryset` `select_related`s the six slot FKs so the list adds no per-Team queries (`test_api_roster_validity.py`).

**`PlayerInlineSerializer`** — minimal player representation (id, name, preferred_roles) for use anywhere a full player is not needed.

**`PlayerSerializer`** — all 19 stats; explicit field list guarding against accidental exposure of future model fields. **ANG-06b** added read-only `age`, `started_playing_age`, `total_games`, `home_site`, `height` and `overall_rating` (a `FloatField` over the `Player.overall_rating` property), so the Angular Edit Player screen can pre-fill; writes still bind `PlayerForm` (tests: `test_apis.py::TestPlayerProfileFields`). `potential`, `salary` and `games_unavailable` stay out.
