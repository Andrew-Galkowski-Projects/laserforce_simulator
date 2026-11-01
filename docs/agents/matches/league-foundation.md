# matches: League and Season foundation

LG-01 models / schedule / standings, LG-01b create, CAR-01 manager team, LG-01c dashboards, LG-01d play season, LG-01e next season, LG-01f history.
Moved verbatim from `laserforce_simulator/matches/CLAUDE.md`; the app index there points here.

## LG-01 league / season foundation

Foundation layer for single-player **League mode** (GLOSSARY.md `### League and seasons` — domain terms **League**, **Season**, **Standings**). Ships two new models, a `Match.season` FK, two pure modules, one new simulator entry point, and admin registrations. Decisions are locked in [ADR-0014](../../adr/0014-league-season-foundation.md) (model + state machine) and [ADR-0015](../../adr/0015-schedule-on-demand-no-fixture-rows.md) (algorithm surface, no `ScheduleEntry` table).

**Models** (`matches/models.py`). `League(name, mode, state, created_at)` with `mode ∈ {sandbox, league, multiplayer}` (default `"league"`), `state ∈ {active, archived}` (default `"active"`), and an `active_season` `@property` returning the single non-`completed` Season per League. `Season(league, name, start_date, teams, state, schedule_format, starting_team_ids_json, champion_team, created_at)` — `league FK(League, on_delete=CASCADE, related_name="seasons")`, **required `start_date: DateField()`** (no default), `teams M2M(Team, related_name="enrolled_seasons")`, `state ∈ {draft, active, completed}` (default `"draft"`), `schedule_format ∈ {single_round_robin}` (default `"single_round_robin"`, `max_length=32` headroom), `starting_team_ids_json: JSONField(null=True, blank=True, default=None)` (snapshotted at activation, sorted ascending), `champion_team FK(Team, on_delete=SET_NULL, related_name="seasons_won")`, `__str__ = f"{league.name} — {name}"` (em-dash U+2014). `Match.season` adds `FK(Season, null=True, blank=True, on_delete=SET_NULL, related_name="matches")` — sandbox Matches stay `season=NULL` forever, no backfill (mirrors [ADR-0004](../../adr/0004-simulation-data-is-disposable.md)); deleting a Season SET_NULLs its Matches rather than cascading them out of history. Three Season methods (signatures pinned): `clean() -> None` enforces the **Active-Season invariant** (≤ 1 non-`completed` Season per League — raises `django.core.exceptions.ValidationError`); `start_season() -> None` (`@transaction.atomic`) flips `draft → active`, raises `ValidationError` on `< 2` teams, and snapshots `starting_team_ids_json = sorted([t.id for t in self.teams.all()])`; `complete_if_finished() -> None` (`@transaction.atomic`, **idempotent**) — no-op on non-`active`, otherwise compares persisted `GameRound`s against `generate_schedule(...)` fixtures via the **Side-agnostic frozenset key** (`frozenset({team_red_id, team_blue_id}) == frozenset({fixture.team_a_id, fixture.team_b_id})` AND matching `round_number`) and on all-played flips `state="completed"` + computes Standings + stamps `champion_team = Team.objects.get(pk=rows[0].team_id)`. State machine: `draft → active` (explicit, M2M locked at activation — to change roster, start the next Season); `active → completed` (auto-transition the moment the last fixture's `GameRound` persists). Imports added: `from django.core.exceptions import ValidationError`, `from django.db import transaction`.

**Pure module `matches/schedule_generator.py`** ships `SCHEDULE_FORMATS: tuple[str, ...] = ("single_round_robin",)`, the frozen dataclass `ScheduleFixture(matchday: int, round_number: int, team_a_id: int, team_b_id: int)`, and `generate_schedule(team_ids: list[int], schedule_format: str = "single_round_robin") -> list[ScheduleFixture]`. Algorithm: sort ascending → reject `< 2` with `ValueError` → odd N appends bye sentinel `-1` (internal, never appears in output) → **circle method** with team-at-index-0 fixed and the remaining slots rotating → each fixture normalised so `team_a_id = min(pair)` / `team_b_id = max(pair)` → round-1 matchdays `1..N-1` mirrored into round-2 matchdays `N..2*(N-1)` (strict mirror, no interleaving — see [ADR-0015](../../adr/0015-schedule-on-demand-no-fixture-rows.md)) → bye-involving fixtures dropped → output sorted by `(matchday, team_a_id)`. Output is a function of the *set* (input order independent — `generate_schedule([5,1,3,7]) == generate_schedule([1,3,5,7])`). N=4 ⇒ 12 fixtures (6 per round); N=8 ⇒ 56 fixtures; N=5 (odd) ⇒ 10 matchdays × 2 played fixtures = 20.

**Pure module `matches/standings.py`** ships the frozen dataclass `StandingsRow(team_id, matches_played, wins, losses, ties, league_points, round_wins, total_score, rank)` (**9 fields, pinned order**) and `compute_standings(completed_matches: list[dict], enrolled_teams: list[tuple[int, str]]) -> list[StandingsRow]`. The **input dict shape** is **8 frozen keys**: `match_id, team_red_id, team_blue_id, winner_team_id` (`None` ⇒ tie), `red_rounds_won, blue_rounds_won, red_total_points` (includes team-elim bonus via the existing `@property`), `blue_total_points`. Aggregation: `league_points = 3*W + 1*T + 0*L`; defensive HX-03 precedent — `winner_team_id` neither team's id counts as a tie; teams in `enrolled_teams` with no matches get a fully-zeroed row. Sort ladder (in order): `league_points` desc, `round_wins` desc, `total_score` desc, `team_name` asc — the **alphabetical tiebreak lives INSIDE the pure module** (the `(team_id, team_name)` tuple shape carries the name across the seam) so the full ordering is unit-testable with zero DB. `rank` is 1-based, dense, in iteration order.

Both pure modules carry a **frozen import allowlist** (`dataclasses`, `typing`, optionally `collections` — **NO** Django, NO `random`, NO `datetime`, NO I/O, NO logging) defended by a `TestNoDjangoImportsLeaked` subprocess fresh-import + `sys.modules` walk that mirrors the HX-01 / HX-02 / HX-03 / HX-04 / RES-04 / RV-03 / LG-00 / LG-00b precedent.

**Simulator entry point** (`matches/simulation/entrypoints.py`) — the sole writer for Season Matches: `BatchSimulator.simulate_scheduled_round(self, season, team_a, team_b, round_number, *, arena_map=None) -> GameRound` (`@transaction.atomic`). Guards (in order): `ValueError` when `season.state != "active"`, `ValueError` when `round_number not in (1, 2)`, `ValueError` when `round_number == 2` and no existing Match. **Side-agnostic Match lookup** is inlined (no helper method): two ORM queries `(season=…, team_red=team_a, team_blue=team_b).first()` ELSE `(season=…, team_red=team_b, team_blue=team_a).first()` — round-1 `(team_a, team_b)` and round-2 `(team_b, team_a)` resolve to the same row. Round 1 find-or-creates the Match (`is_completed=False`), delegates to the per-Round entry point used by `simulate_match` byte-for-byte (same `arena_map` resolution, same seed-handling, same `_flush_to_db` parameters — **no new RNG draws**), persists `GameRound(round_number=1, …)`, writes `match.red_round1_*` / `blue_round1_*` / `round1_eliminated_at`, leaves `is_completed=False`. Round 2 looks up Side-agnostically (raise on missing), delegates with **args reversed** (`team_red=team_b, team_blue=team_a` — mirrors the per-Match colour swap in `simulate_match` verbatim; the round-2 `GameRound` persists with team_b as physical red), persists `GameRound(round_number=2, …)`, writes the `*_round2_*` fields, **sets `is_completed=True`** + `match.save()` (triggers the existing `calculate_winner` via the `save()` override). After persistence (either round), calls `season.complete_if_finished()` (idempotent — no-op except on the final fixture). The existing `simulate_match` (both-Rounds atomic, sandbox-Match entry point) is **kept verbatim** — sandbox Matches with `season=NULL` use it; Season Matches must use the new method. No simulation mechanics change → **no SIM-07 / SIM-08 contract interaction, no Score Calibration re-baseline**.

**Standings and schedule reads** live in `league_services.build_season_standings` / `build_season_schedule`, served at `GET /api/seasons/<id>/standings/` / `schedule/`. A draft Season gets zero-filled preview rows sorted by team strength; an active / completed one runs `compute_standings` over its completed Matches. Matchday `n` is dated `season.start_date + (n - 1) * 7 days`.

**Admin** (`matches/admin.py` — inserted AFTER existing registrations, no existing registration modified). `LeagueAdmin.list_display = ("name", "mode", "state", "created_at")`; `SeasonAdmin.list_display = ("name", "league", "state", "schedule_format", "start_date")` + **`filter_horizontal = ("teams",)`** (the M2M dual-select widget).

**Migration**. Single file `matches/migrations/0029_league_season_match_fk.py` depending on `("matches", "0028_gameround_is_simulated")` + the latest `teams` migration at branch-cut time. Operations in pinned order: `CreateModel(League)` → `CreateModel(Season)` → `AddField(Match, season)`. **No `RunPython`, no `RunSQL`, no backfill**.

**Tests** live in four NEW files under `matches/tests/` (full listing in the `## Tests` section below): `test_schedule_generator.py` (pure-unit), `test_standings.py` (pure-unit), `test_league_models.py` (Django `TestCase`), `test_league_simulator.py` (Django `TestCase`). Simulator tests use small-N seeded simulations (N=2 / N=3) and assert on schema-level outcomes, not exact score totals.

**Determinism / scope.** `simulate_scheduled_round` is pure orchestration over the existing per-Round simulator — per-Round RNG consumption is byte-for-byte identical to `simulate_match` at round-1 and round-2 time separately. **No Score Calibration re-baseline.** The Active-Season invariant is the only data-integrity rule enforced at the model layer; schedule determinism is enforced by the `starting_team_ids_json` snapshot at activation plus the pure module's input sort (defence in depth). **No GLOSSARY.md edit** (the `League` / `Season` / `Standings` glossary entries were added at grilling time). **No ADR write** ([ADR-0014](../../adr/0014-league-season-foundation.md) + [ADR-0015](../../adr/0015-schedule-on-demand-no-fixture-rows.md) were both written at grilling time). **Scope-out (deferred to LG-01a..g)**: mode picker landing, `/leagues/` list, create-League flow, dashboards, Play Next, "Start Next Season" chain, League history, per-Team game log. **Scope-out (within LG-01)**: no `simulate_match` change, no `Player.is_bench` field, no `Season.matchday_cadence_days`, no `League.owner_user`, no `Match.state` enum, no API / DRF endpoint, no batch-sim / Celery touch, no backfill, no `Player` / `Team` app touch beyond the auto-generated reverse accessors `Team.enrolled_seasons` + `Team.seasons_won`.

**Locked names.** Models `matches.models.League` + `matches.models.Season` + the `Match.season` FK; related names `seasons` / `enrolled_seasons` / `seasons_won` / `matches`; pure modules `matches/schedule_generator.py` + `matches/standings.py`; dataclasses `ScheduleFixture(matchday, round_number, team_a_id, team_b_id)` + `StandingsRow(team_id, matches_played, wins, losses, ties, league_points, round_wins, total_score, rank)`; functions `generate_schedule` + `compute_standings`; constant `SCHEDULE_FORMATS = ("single_round_robin",)`; bye sentinel `-1` (internal); match dict 8 keys `match_id, team_red_id, team_blue_id, winner_team_id, red_rounds_won, blue_rounds_won, red_total_points, blue_total_points`; simulator method `BatchSimulator.simulate_scheduled_round(self, season, team_a, team_b, round_number, *, arena_map=None) -> GameRound`; admin classes `matches.admin.LeagueAdmin` + `matches.admin.SeasonAdmin`; migration `matches/migrations/0029_league_season_match_fk.py`; test files `matches/tests/test_schedule_generator.py` + `matches/tests/test_standings.py` + `matches/tests/test_league_models.py` + `matches/tests/test_league_simulator.py`. Seam contract: [`.claude/worktrees/lg-01-seam-contract.md`](../../../.claude/worktrees/lg-01-seam-contract.md).

## LG-01b create-league flow

Creates a `League(state=active)` + initial `Season(state=draft)` + auto-generated `Team`s (via `teams.services._generate_teams`) enrolled into the Season's M2M.

**Form.** `matches.forms.CreateLeagueForm(forms.Form)` is appended to the existing `matches/forms.py` (alongside `MatchSetupForm` / `SingleRoundSetupForm`) with **7 fields in pinned order**: `league_name = forms.CharField(max_length=100)` (required, no uniqueness validation — duplicate League names allowed and differ only by `id`), `season_name = forms.CharField(max_length=100, initial="Season 1")`, `start_date = forms.DateField(initial=django.utils.timezone.localdate)` (callable initial, evaluated per-bind), `num_teams = forms.TypedChoiceField(choices=[(4, "4"), (8, "8"), (12, "12"), (16, "16")], coerce=int, empty_value=None, initial=4)` (so `cleaned_data["num_teams"]` is an `int`, not a `str`), `schedule_format = forms.ChoiceField(choices=[("single_round_robin", "Single round-robin")], disabled=True, initial="single_round_robin")` (single-option `disabled=True` — Django serves the initial value regardless of POST content so a tampered POST cannot inject a different format, no extra `clean_schedule_format` guard), `mean = forms.IntegerField(min_value=0, max_value=100, initial=50)`, and `std_dev = forms.IntegerField(min_value=1, max_value=40, initial=15)`. **`players_per_team` is NOT a form field** — it is fixed at the **literal `6`** server-side inline in the writer body (locked, not configurable per create). No `Meta`, no `__init__` override, no custom widgets pinned.

**Writer.** `league_services.create_league_and_season(form, *, manager=None) -> Season` (`@transaction.atomic`; the sole creation path since CRE-01, called by `POST /api/leagues/`, see `league-lifecycle.md`). It builds `rng = random.Random()` (default-seeded, intentionally random per create) plus defensive `list(TEAM_NAMES)` / `list(PLAYER_NAMES)` pool copies, calls `_generate_teams(cleaned["num_teams"], 6, ...)` (the locked **literal `6`** is `players_per_team`), creates `League(mode="league", state="active")` and `Season(state="draft")`, and bulk-adds the Teams to `season.teams`.

**Tests:** `matches/tests/test_api_leagues.py::TestLeagueCreate` (the API create path) and `matches/tests/test_league_create.py`.

**Locked names.** Form class `matches.forms.CreateLeagueForm`; locked literals `players_per_team = 6` / `mode = "league"` / `state = "active"` (League) / `state = "draft"` (Season) / `schedule_format = "single_round_robin"`. Seam contract: [`.claude/worktrees/lg-01b-seam-contract.md`](../../../.claude/worktrees/lg-01b-seam-contract.md).

## CAR-01 manager team assignment

Lets the manager **name their own team at create-League time**; that named team becomes one of the N generated teams **and** the League's `current_team` (the manager's career team). Single-player `league` mode **IS** career mode — there is **NO new mode value** and **NO `Manager` / `User` model** (the manager is the implicit local user; both deferred to UX-01). **Reuses the existing `League.current_team` FK** — no new model field, no migration. Seam contract: [`.claude/worktrees/car-01-manager-team-assignment-seam-contract.md`](../../../.claude/worktrees/car-01-manager-team-assignment-seam-contract.md).

**Form.** ONE new optional field on `matches.forms.CreateLeagueForm` (the LG-01b form): `manager_team_name = forms.CharField(max_length=100, required=False, label="Your team name")`, inserted **after `league_name` / before `season_name`** in the pinned field order. **No `clean()` change, no uniqueness validation** (team names are not globally unique). Blank is valid — the LG-01b 7-field validation surface is otherwise untouched.

**Writer wiring** (`league_services.create_league_and_season`, inside its `@transaction.atomic`). The manager's generated team becomes `league.current_team`, and a **non-blank** (stripped) `cleaned["manager_team_name"]` renames it (CRE-01 replaced the alphabetical pick with the difficulty pick, `league-lifecycle.md`). The named team **stays one of the N** (league size unchanged = `num_teams`). The rename + `current_team` assignment run **before** `Season.objects.create` / `season.teams.add(*created_teams)` / the phase loop / `_write_baseline_ratings`.

**Scope-out (locked).** The **per-team scouting budget is DEFERRED** (out of CAR-01 scope — LG-05's FIXED `DEFAULT_SCOUTING_BUDGET = 50` stands). **No migration, no model field** (reuses `League.current_team`), **no new mode value**, **no `Manager` / `User` model**, **no simulator change**, **no Score Calibration re-baseline**, **no ADR**.

**Tests** live in `matches/tests/test_league_create.py::TestCar01ManagerTeamName` (the LG-01b create-flow test file).

## LG-01c league / season dashboard

Read-only dashboard body over the LG-01 foundation, served at `GET /api/leagues/<id>/dashboard/` and `GET /api/seasons/<id>/dashboard/`: top-3 standings snippet + next round + round count + three leaders snippets, branching on the displayed Season's `state` (`draft` / `active` / `completed`) plus the league-only `none` branch when the League has zero Seasons. **No model change, no migration, no ADR, no GLOSSARY.md edit, no simulator touch.**

**Service** (`matches/league_services.py`). The League dashboard's **season pick** is `pick_displayed_season(league)`: `league.active_season` (the LG-01 `@property`) when non-`None`, else the most recent completed Season, else `None`; `season_mode_for(season)` maps it to `"none"` / `"draft"` / `"completed"` / `"active"`. The Season dashboard uses `season_mode = season.state` (never `"none"`). Both delegate to `build_dashboard_context(displayed_season: Season | None, season_mode: str) -> dict`.

**Branch-specific population.** `"none"` (league-only — no Season): `standings_snippet = []`, `next_fixture = None`, `round_count_* = 0`, `leaders_* = []`, `action_button_label = "No Season"`, `action_button_state = "none"`. `"draft"`: standings snippet is the zero-filled top-3 from `displayed_season.teams.all()` sorted by name ascending, each row a dict with the 9 LG-01 standings keys all zeroed (`team_id, matches_played=0, wins=0, losses=0, ties=0, league_points=0, round_wins=0, total_score=0, rank=i+1`) paired with its `team`; `next_fixture = None`, `round_count_* = 0`, `leaders_* = []`, `action_button_label = "Start Season"`, `action_button_state = "start_season"`. `"active"`: standings via `compute_standings(...)` (LG-01) over `Match.objects.filter(season=displayed_season, is_completed=True)`, top 3 rows paired with their Teams via a single `Team.objects.in_bulk(...)`; `fixtures = generate_schedule(displayed_season.starting_team_ids_json, displayed_season.schedule_format)`; `played_keys = {(frozenset({gr.match.team_red_id, gr.match.team_blue_id}), gr.round_number) for gr in GameRound.objects.filter(match__season=displayed_season).select_related("match")}`; `fixture = find_next_fixture(fixtures, played_keys)` (`None` ⇒ `next_fixture = None`, else built into the 7-key frozen `next_fixture` dict); `round_count_completed, round_count_total = round_progress(fixtures, played_keys)`; `leaders_* = compute_leaders(player_rounds, stat, limit=3)` per stat; `action_button_label = "Play Next"`, `action_button_state = "play_next"`. `"completed"`: standings same as `"active"`; `find_next_fixture` returns `None` on an all-played Season (the LG-01 `complete_if_finished` completion invariant) so `next_fixture = None`; `round_progress` returns `(len(fixtures), len(fixtures))`; leaders same as `"active"`; `action_button_label = "Start Next Season"`, `action_button_state = "start_next_season"`.

**Pure module `matches/season_dashboard.py`** ships the leader-aggregation + next-fixture + round-progress helpers. **Frozen import allowlist** (`dataclasses`, `typing`, optionally `collections.defaultdict`) — **NO** Django, NO ORM, NO `random` / `secrets`, NO `datetime`, NO file I/O, NO logging, NO `matches.schedule_generator` (the module consumes `ScheduleFixture` instances passed in by the caller via `TYPE_CHECKING` / string annotations — the dataclass shape is the cross-module contract, the import allowlist stays truly frozen). Defended by `TestNoDjangoImportsLeaked` (subprocess fresh-import + `sys.modules` walk, mirrors the HX-03 / HX-04 / RES-04 / LG-01 / LG-01a / LG-01b precedent). The module surfaces the frozen dataclass **`LeaderRow(player_id, player_name, role, team_id, team_name, value, games_played, rank)`** (`@dataclass(frozen=True)`, **8 fields in pinned order**), plus three functions: `compute_leaders(player_rounds: list[dict], stat: str, limit: int = 3) -> list[LeaderRow]` (aggregates one entry per `PlayerRoundState` row into a ranked top-`limit` leaders snippet; stat vocabulary is the **3 locked strings** `"points_per_game"` ⇒ `mean(points_scored)`, `"tags_per_game"` ⇒ `mean(tags_made)`, `"tag_ratio"` ⇒ `sum(tags_made) / max(sum(times_tagged), 1)` — the canonical GLOSSARY.md sum/sum form, **NOT** mean of per-row ratios; the `max(..., 1)` denominator clamp avoids div-by-zero and matches the existing `Player.career_stats` rule, `value` is `float` even when both sums are 0 (`0 / 1 = 0.0`); deterministic sort ladder `value` desc → `games_played` desc → `player_id` asc; `rank` populated 1-based dense in iteration order; **empty input ⇒ `[]`** immediately; **unknown stat string ⇒ `ValueError(f"Unknown stat {stat!r}; expected one of points_per_game, tags_per_game, tag_ratio")`**; defensive "last row wins" for inconsistent role / team across a player's group — the caller passes rows in `id` ascending so "last" == most-recent `PlayerRoundState`), `find_next_fixture(fixtures, played_keys) -> Optional[ScheduleFixture]` (returns the first `ScheduleFixture` whose `(frozenset({team_a_id, team_b_id}), round_number)` is NOT in `played_keys` — side-agnostic `frozenset` match; empty `fixtures` ⇒ `None`, all played ⇒ `None`), and `round_progress(fixtures, played_keys) -> tuple[int, int]` (`completed` = count of fixtures matched against `played_keys`, `total = len(fixtures)`; `completed` is **NOT** `len(played_keys)` — extra `GameRound` rows that don't correspond to a fixture are not double-counted, the defensive HX-03 precedent for data-drift; empty fixtures ⇒ `(0, 0)`).

**Player-round seam dict** (the only thing crossing the service ↔ pure-module seam for leader aggregation, **frozen 7 keys, every key required**): `player_id, player_name, role, team_id, team_name, tags_made, times_tagged, points_scored`. `league_services._compute_dashboard_leaders` materialises one entry per `PlayerRoundState` row in the Season's completed Rounds via the locked queryset `PlayerRoundState.objects.filter(game_round__match__season=displayed_season).select_related("player", "game_round", "game_round__match").order_by("id")` (single `select_related`-flattened query, `order_by("id")` is what makes the "last row wins" defensive fallback deterministic); `player_name` reads via `prs.player.name`, `team_id` / `team_name` resolve from `prs.game_round.team_red` / `team_blue` keyed off `prs.team_color` (`"red"` ⇒ `team_red`, `"blue"` ⇒ `team_blue`). In the `"none"` branch `displayed_season is None` and the queryset is **not** issued — `leaders_* = []` directly.

**`next_fixture` dict shape** (frozen 7 keys, built in `build_dashboard_context` from a `ScheduleFixture` + the two Teams via a single `Team.objects.in_bulk(...)`): `matchday` (1-based from `ScheduleFixture.matchday`), `round_number` (1 or 2), `team_a_id`, `team_a_name`, `team_b_id`, `team_b_name`, `date` — the `date` derived as `season.start_date + timedelta(days=(matchday - 1) * 7)` mirroring the `build_season_schedule` per-matchday date derivation byte-for-byte so the dashboard and the schedule agree on matchday dates.

**Determinism / scope.** The dashboard body is a **pure read-derivation** — no writes, no RNG, no simulation kicked off, no `_flush_to_db` touch, no SIM-07 / SIM-08 contract interaction, no Score Calibration re-baseline obligation. The pure module consumes no RNG, no Django, no ORM, no `datetime` — it is unit-testable with zero DB. The 4 `season_mode` literals (`"draft" / "active" / "completed" / "none"`) and the 4 `action_button_state` literals (`"start_season" / "play_next" / "start_next_season" / "none"`) are implementation enums — NOT domain language, NOT added to GLOSSARY.md.

**Locked names.** Shared body-context builder `matches.league_services.build_dashboard_context`; pure module `matches/season_dashboard.py`; dataclass `season_dashboard.LeaderRow(player_id, player_name, role, team_id, team_name, value, games_played, rank)` (8 fields in pinned order); functions `season_dashboard.compute_leaders` + `season_dashboard.find_next_fixture` + `season_dashboard.round_progress`; stat vocabulary literals `"points_per_game"` + `"tags_per_game"` + `"tag_ratio"`; season-mode literals `"draft"` + `"active"` + `"completed"` + `"none"` (`"none"` is league-only); action-button-state literals `"start_season"` + `"play_next"` + `"start_next_season"` + `"none"`; player-round seam dict 7 keys `player_id, player_name, role, team_id, team_name, tags_made, times_tagged, points_scored`; `next_fixture` seam dict 7 keys `matchday, round_number, team_a_id, team_a_name, team_b_id, team_b_name, date`; test file `matches/tests/test_season_dashboard_logic.py` (pure-unit) with test classes `TestComputeLeadersEmpty` / `TestComputeLeadersSinglePlayer` / `TestComputeLeadersTiebreak` / `TestComputeLeadersDeterministic` / `TestComputeLeadersRoleMix` / `TestComputeLeadersStatVocabulary` / `TestComputeLeadersLimit` / `TestComputeLeadersDefensiveLastWins` / `TestFindNextFixture` / `TestRoundProgress` / `TestNoDjangoImportsLeaked`; the API dashboards are covered in `matches/tests/test_api_leagues.py::TestLeagueDashboard`. Seam contract: [`.claude/worktrees/lg-01c-seam-contract.md`](../../../.claude/worktrees/lg-01c-seam-contract.md).

## LG-01d play season

Write surface over the LG-01c dashboards, now the `/api/seasons/<id>/` actions on `league_api_views.SeasonViewSet` over `league_play_services`: `POST start/` (`start_season`, draft → active; 409 `season_not_draft` otherwise), `POST play-week/` (`play_week`: one inline `with transaction.atomic():` around every Round of the next unplayed matchday, so any inner exception rolls back the whole matchday), `POST play-two-months/` / `play-until-end/` (`play_season_run(season, max_matchdays=8 | None)` enqueues `play_season_task` and returns a Job ref `{job_id, season_id, status_url}`), and `GET play-jobs/<job_id>/` (polling; an unknown `job_id` resolves to Celery `PENDING` ⇒ `"running"`). The sync actions return the refreshed Season dashboard body; `_run_sync_play` / `_SERVICE_ERRORS` map the service errors to 409s and a model `ValidationError` / simulator `ValueError` to 400s.

**Celery task** `matches/tasks.py::play_season_task` — `@shared_task(bind=True, name="matches.play_season")`, signature `(self, season_id: int, max_matchdays: int | None = None) -> dict`. Body steps (locked, in order): `import django.db` (API-03 `close_old_connections` precedent), deferred imports of `Season` / `BatchSimulator` / `generate_schedule` / `select_play_fixtures` / `Team` (mirrors the existing `_resolve_arena_map` deferred-import pattern keeping `matches/tasks.py`'s module-level surface lean), load `season = Season.objects.get(id=season_id)`, materialise `fixtures = generate_schedule(season.starting_team_ids_json or [], season.schedule_format)`, build the Side-agnostic `played_keys = {(frozenset({gr.match.team_red_id, gr.match.team_blue_id}), gr.round_number) for gr in GameRound.objects.filter(match__season=season).select_related("match")}`, call `to_play = select_play_fixtures(fixtures, played_keys, max_matchdays)`, then loop `for k, fixture in enumerate(to_play)` calling `BatchSimulator().simulate_scheduled_round(season, team_a, team_b, fixture.round_number)` (no `arena_map` kwarg — deferred to LG-01j) and emitting `self.update_state(state="PROGRESS", meta={"completed": k + 1, "total": n})` AFTER each Round (so `completed` is the count of Rounds already committed). Empty to-play list ⇒ returns `{"completed": 0, "total": 0}` immediately. `finally: django.db.close_old_connections()`. **No outer `@transaction.atomic` on the task body** — each Round's atomic commit is the existing `simulate_scheduled_round` decorator. A mid-loop exception propagates (Celery records `FAILURE`); every completed Round survives because it was its own atomic commit; the Season stays `active`; the user re-clicking Play resumes from where the last successful Round left off. **This is a load-bearing decision recorded in [ADR-0016](../../adr/0016-play-season-job-execution-model.md).**

**Pure module extension** — `matches/season_dashboard.py` (the LG-01c file, **frozen import allowlist unchanged**: `dataclasses` / `typing` / `collections` only, no Django / no ORM / no `random` / no `datetime` / no I/O / no logging / no `matches.schedule_generator` import — `ScheduleFixture` instances cross the seam as duck-typed input). The defensive `TestNoDjangoImportsLeaked` subprocess check **must keep passing** after the two new appended functions. **`find_next_matchday(fixtures, played_keys) -> Optional[int]`** walks `fixtures` in canonical `generate_schedule(...)` iteration order and returns the `matchday` of the first fixture whose Side-agnostic `(frozenset({team_a_id, team_b_id}), round_number)` key is NOT in `played_keys`; empty / all-played ⇒ `None`. **`select_play_fixtures(fixtures, played_keys, max_matchdays) -> list`** walks `fixtures` once collecting unplayed fixtures whose `matchday` is among the first `max_matchdays` distinct unplayed matchdays seen (or **all** unplayed fixtures when `max_matchdays is None`); canonical iteration order preserved in the output. The algorithm uses a single sweep — once the distinct-matchday set reaches `max_matchdays`, the sweep stops accepting fixtures whose `matchday` is not already in the set.

**Polling JSON shape** (`league_play_services.build_play_job_status(job_id)` → `job_status_fields`): `{"status": str, "completed": int, "total": int, "cancelled": bool, "error": str | None}`. **`status`** ∈ `"running" | "complete" | "error"` via the shared `matches.job_status.celery_state_to_job_status` helper (`PENDING` / `STARTED` / `PROGRESS` / `RETRY` / unknown ⇒ `"running"`; `SUCCESS` ⇒ `"complete"`; `FAILURE` / `REVOKED` ⇒ `"error"`). **`completed` / `total`** are Round-level counts (NOT matchday-level): read from `async_result.info["completed"]` / `["total"]` on `PROGRESS`, from `async_result.result["completed"]` / `["total"]` on `SUCCESS`, `0` / `0` otherwise. **`error`** = `str(async_result.info)` on `FAILURE` / `REVOKED`, `None` otherwise.

**Determinism / scope.** LG-01d is pure orchestration over the existing `simulate_scheduled_round` per-Round entry point — per-Round RNG consumption is byte-for-byte unchanged. **No simulation mechanics change → no Score Calibration re-baseline.** Concurrency at the DB layer is handled by the existing Side-agnostic Match find-or-create in `simulate_scheduled_round`; the task body does not need additional locking. The 5-key polling JSON shape, the 3-value status vocabulary, and the per-Round atomic commits decision are all locked at the seam contract.

**Scope-out (locked).** **No model change, no migration** (`matches/models.py` / `matches/migrations/` read-only at LG-01d). **No `simulate_match` / `simulate_scheduled_round` change.** **No edit to `GLOSSARY.md` beyond the two pinned edits** (extend **Job**, add **Matchday**). **No new ADR beyond `0016-play-season-job-execution-model.md`.** **No `Season.matchday_cadence_days` field** (the 7-day cadence stays hardcoded in the schedule + `next_fixture.date` derivations). **No edit to the LG-01c pure module's 3 existing functions** (`compute_leaders` / `find_next_fixture` / `round_progress`); only `find_next_matchday` and `select_play_fixtures` are appended.

**Tests** live in `matches/tests/test_league_play.py`: `TestFindNextMatchday` + `TestSelectPlayFixtures` (`SimpleTestCase` pure-unit, locally-stubbed fixture shape, no `matches.schedule_generator` import) and `TestPlaySeasonTaskHappyPath` + `TestPlaySeasonTaskMaxMatchdays` + `TestPlaySeasonTaskPerRoundCommit` + `TestPlaySeasonTaskTeamLookup` (Django `TestCase`, `play_season_task` under `CELERY_TASK_ALWAYS_EAGER=True` via the API-03 `LF_CELERY_EAGER=1` conftest). The API actions are covered in `test_api_sync_play.py` / `test_api_play_runs.py`. Tests use small-N seeded simulations (N=2 / N=3) per the project rule.

**Locked names.** API actions `POST /api/seasons/<id>/start/` + `play-week/` + `play-two-months/` + `play-until-end/` + `GET /api/seasons/<id>/play-jobs/<job_id>/`; services `league_play_services.start_season` / `play_week` / `play_season_run` / `build_play_job_status`; Celery task `matches.tasks.play_season_task` (`@shared_task(bind=True, name="matches.play_season")`, `(self, season_id, max_matchdays=None) -> dict`); Celery broker name `"matches.play_season"`; pure functions `matches.season_dashboard.find_next_matchday` + `matches.season_dashboard.select_play_fixtures`; `max_matchdays` literals `1` (One Week) / `8` (Two Months) / `None` (Until End); polling status vocabulary `"running"` / `"complete"` / `"error"`; `played_keys` shape `set[tuple[frozenset[int], int]]` (Side-agnostic); ADR file `docs/adr/0016-play-season-job-execution-model.md`; GLOSSARY.md edits — **Job** entry extended ("Three kinds today: Batch run job, Save-games job, Play Season job …") + **Matchday** term added to `### League and seasons`; `arena_map` policy — `simulate_scheduled_round` called without the kwarg (default `None`, 3-zone fallback), deferred to LG-01j; test file `matches/tests/test_league_play.py` with classes `TestFindNextMatchday` / `TestSelectPlayFixtures` (pure-unit), `TestPlaySeasonTaskHappyPath` / `TestPlaySeasonTaskMaxMatchdays` / `TestPlaySeasonTaskPerRoundCommit` / `TestPlaySeasonTaskTeamLookup` (Celery EAGER). Seam contract: [`.claude/worktrees/lg-01d-seam-contract.md`](../../../.claude/worktrees/lg-01d-seam-contract.md).

## LG-01e start next season

`league_services.start_next_season(league) -> Season` creates a fresh draft `Season` inside the same `League` with copied teams + auto-generated name + Jan-1-next-year `start_date`. `POST /api/leagues/<id>/next-season/` (`@transaction.atomic`) runs it through `league_play_services.roll_and_start_next_season` (UXP-18 below) and returns 201. **No model change, no migration, no ADR, no GLOSSARY.md edit, no new pure module, no simulator touch, no async / Celery, no admin change.**

**Service.** `start_next_season` is undecorated (the caller owns the atomic boundary). Its guards, in order: `SeasonInProgressError` when `league.active_season` (the LG-01 `@property`) is non-`None` (API 409 `season_in_progress`); `NoCompletedSeasonError("No completed Season in this League.")` when `latest_completed_season(league)` is `None` (API 409 `no_completed_season`); `ManagerFiredError` from the CAR-02 verdict gate (API 409 `manager_fired`). The body is `run_season_rollover(league, latest_completed)`: (a) `name = f"Season {len(all_seasons) + 1}"`, counted BEFORE the create so the new Season takes the next sequential index; (b) `start_date = date(latest_completed.start_date.year + 1, 1, 1)` — calendar-year jump, Jan 1 of next year (NOT a `+ 365 days` calculation); (c) `schedule_format = latest_completed.schedule_format` carried over verbatim; (d) `state = "draft"` explicit on `Season.objects.create(...)`; (e) **NOT set** on the new Season: `starting_team_ids_json` (snapshotted by `Season.start_season()` at activation time) and `champion_team` (only stamped by `complete_if_finished`); (f) **copy teams from the snapshot (NOT the live M2M)**: `team_ids = latest_completed.starting_team_ids_json or []`, `Team.objects.filter(id__in=team_ids)`, bulk-added to `new_season.teams`. The **snapshot-as-source-of-truth rule** is load-bearing: teams admin-added directly to the completed Season's live M2M post-completion do NOT leak into the next Season. Missing-team ids (e.g. an admin-deleted Team) are silently dropped by the `IN` clause — the new Season simply has fewer teams. A failure in any step rolls back the new Season row + any M2M rows atomically.

**Scope-out (locked).** No model change, no migration, no ADR write ([ADR-0014](../../adr/0014-league-season-foundation.md) + [ADR-0015](../../adr/0015-schedule-on-demand-no-fixture-rows.md) + [ADR-0016](../../adr/0016-play-season-job-execution-model.md) cover the foundation, the schedule surface, and the play job-execution model). No GLOSSARY.md edit ("Start Next Season" is a UI label, not a domain term). **No "Archive League" toggle** — admin-only (`LeagueAdmin` already supports the `state="archived"` flip). **No edit-draft surface** — editing a `draft` Season's roster / start_date / name is admin-only (`SeasonAdmin` supports the M2M via `filter_horizontal` and default ModelAdmin form fields for the scalars). **No `Season.state="archived"` value** — completed Seasons are already effectively read-only per the LG-01 invariants (idempotent `complete_if_finished`, M2M frozen by `starting_team_ids_json` snapshot). **No simulator touch, no RNG consumption, no SIM-07 / SIM-08 contract interaction, no Score Calibration re-baseline.** No backfill — existing completed Seasons are not touched.

**Tests** live in `matches/tests/test_league_next_season.py` (Django `TestCase`, driving `POST /api/leagues/<id>/next-season/`), classes `TestNextSeasonHappyPath` + `TestNextSeasonNameFormat` + `TestNextSeasonStartDate` + `TestNextSeasonScheduleFormatCarry` + `TestNextSeasonTeamsCopiedFromSnapshot` + `TestNextSeasonActiveSeasonGuard` + `TestNextSeasonNoCompletedGuard` + `TestNextSeasonAtomicity`. Tests must NOT touch `simulate_scheduled_round` / `simulate_match` / `save_games` or any simulator entry point (LG-01e runs no simulation, a test that accidentally enters the simulator is a scope leak and locked out). Tests must NOT `mock.patch` the ORM beyond the single `Season.objects.create` patch in `TestNextSeasonAtomicity` (which forces the rollback path — the LG-01b transaction-rollback precedent); every other test exercises the real ORM path end-to-end so signature drift between LG-01e's call sites and the ORM surfaces as a test failure rather than a silent mock pass.

**Determinism / scope.** LG-01e is a thin CRUD write — no writes to `_flush_to_db`, no simulation, no RNG draw, no `BatchSimulator` call. **No SIM-07 / SIM-08 contract interaction, no Score Calibration re-baseline obligation.** The active-Season guard is the only concurrency consideration (a double-submit race gets a 409 `season_in_progress`); the atomic boundary rolls back the new Season + M2M rows atomically on any mid-flow exception.

**Locked names.** API action `POST /api/leagues/<id>/next-season/` (`LeagueViewSet.next_season`, `@transaction.atomic`, 201); services `league_services.start_next_season(league) -> Season` + `run_season_rollover(league, latest_completed) -> Season` + `latest_completed_season(league)`; errors `SeasonInProgressError` / `NoCompletedSeasonError` / `ManagerFiredError`; active-Season check `league.active_season` (the LG-01 `@property`); locked literals `name = f"Season {len(all_seasons) + 1}"` (counted BEFORE the create), `state = "draft"`, `start_date = date(latest_completed.start_date.year + 1, 1, 1)`, `schedule_format = latest_completed.schedule_format`; error literal `"No completed Season in this League."`; snapshot read `latest_completed.starting_team_ids_json or []`; team resolution `Team.objects.filter(id__in=team_ids)` (single `IN` query, missing ids silently dropped); test file `matches/tests/test_league_next_season.py`. Seam contract: [`.claude/worktrees/lg-01e-seam-contract.md`](../../../.claude/worktrees/lg-01e-seam-contract.md).

### UXP-18 — Start Next Season also starts the Season

`league_play_services.roll_and_start_next_season(league) -> NextSeasonResult(season,
needs_conferences)` runs `start_next_season` (its three errors propagate) and then starts the
rolled Season, so the confirming click lands on an **active** Season. Two cases keep the draft:

- **The previous Season had Conferences.** The rollover carries none and they are editable only
  in draft, so it returns the draft with `needs_conferences=True` for the Manager to re-partition.
- **The model refuses the start** (`ValidationError`, e.g. fewer than 2 teams). The activation
  runs inside its own savepoint, so the rolled draft still commits.

The caller owns the outer atomic. `POST /api/leagues/<id>/next-season/` calls it, so its 201 is an
**active** Season (draft only in the two cases above). The service `start_next_season` /
`run_season_rollover` still return a draft, and the fired-Manager `reassign_team` roll still lands
on a draft.

## LG-01f league history

Read-only **League History**: one row per Season in the League (newest first, the in-progress Season first with live standings), served at `GET /api/leagues/<id>/history/` (`LeagueViewSet.history`, paginated by the API's `page` / `page_size`). **No model change, no migration, no new pure module, no simulator touch, no admin change, no GLOSSARY.md edit.** Design decisions live in [ADR-0017](../../adr/0017-league-context-nav-shape.md).

**Service.** `league_services.history_seasons(league) -> (in_progress_season, completed_seasons, teams_by_id)`: one `league.seasons.select_related("champion_team").prefetch_related(...)` query over `state__in=["active", "draft", "completed"]` ordered `-id`, then `teams_by_id = Team.objects.in_bulk(team_ids)` over `starting_team_ids_json` plus the M2M of an active / draft Season (LG-01 invariant: ≤ 1 non-completed Season per League). `build_history_row(season, teams_by_id, *, is_in_progress, award_set=None) -> dict` builds one row with zero DB hits (keyword-only `is_in_progress` prevents positional inversion); **`compute_standings(...)` runs in-Python** on each Season's prefetched `season.matches.all()` filtered to `is_completed=True`. `pick_displayed_season(league)` (`league.active_season`, else the most recent completed Season) is the Season the League nav's Standings / Schedule entries target.

**Row-dict shape** (the LG-01f keys below; LG-03 adds `season_mvp` / `finals_mvp`, and ANG-08 dropped `season_url`): `season_id: int, season_name: str, start_date: datetime.date, teams_enrolled: int, matches_played: int, champion: Team | None, runner_up: Team | None, tournament_champion: None, top_three: list[Team | None], is_in_progress: bool`. `teams_enrolled = len(season.starting_team_ids_json or [])` for completed, `season.teams.count()` via the prefetch when `starting_team_ids_json is None` on a draft Season; `matches_played = len([m for m in season.matches.all() if m.is_completed])` over the prefetch (NOT a `.filter(is_completed=True).count()` query); `champion` is `season.champion_team` with a `teams_by_id.get(standings[0].team_id)` fallback when `champion_team is None` (defensive — the LG-01 invariant fills it at completion, but pre-LG-01 data drift is possible); `runner_up = teams_by_id.get(standings[1].team_id)` when `len(standings) >= 2` else `None`; `tournament_champion` is **always `None`** at LG-01f (LG-02 reservation); `top_three` is exactly length 3 (`None` padding when fewer than 3 teams have played). Runner-Up and top-3 carry live standings for the in-progress row too — informative even when no champion has been crowned.

`compute_standings` is the LG-01 pure module `matches.standings.compute_standings` — consumed verbatim; the row reads `standings[i].team_id`, and `teams_by_id.get(...)` (NOT `[]`) so a stale team-id from an admin-deletion does not crash. **Pagination:** the API pages the in-progress Season and the completed ones as one list, so the in-progress row comes on page 1 only.

**Determinism / scope.** Read-only — no writes, no RNG, no simulation, no `_flush_to_db` touch, **no SIM-07 / SIM-08 contract interaction, no Score Calibration re-baseline obligation**.

**Tests:** `matches/tests/test_league_history.py` (`TestHistoryRowCells`, `TestHistoryRowChampionFallback`, `TestLg07aMemberNightHistoryExclusion`) and `matches/tests/test_api_leagues.py::TestLeagueHistory`.

**Locked names.** Services `matches.league_services.history_seasons` / `build_history_row` / `pick_displayed_season`; API action `GET /api/leagues/<id>/history/`. ADR [ADR-0017](../../adr/0017-league-context-nav-shape.md). Seam contract: [`.claude/worktrees/lg-01f-seam-contract.md`](../../../.claude/worktrees/lg-01f-seam-contract.md).

## PERF-01 constant-query League reads

Five League reads used to run a batch of queries per Season or per Team. They now run a
constant number, with payloads unchanged (spec #313). The guardrail is
`laserforce_simulator/api/tests/test_detail_action_query_counts.py`: its `KNOWN_N_PLUS_ONE`
map is now empty, so a new N+1 on any detail action fails CI. Each fix lives in the service
behind the API action.

- **Batched award sets.** `league_services.compute_season_award_sets(seasons) -> dict[int, AwardSet]`
  equals `compute_season_award_set` per Season.
  - It is built on the generic `season_snapshot.completed_seasons_cached(seasons, key, compute_one, *, load)`.
  - Every fresh `summary_snapshot_json` payload is refreshed with **one** Player and **one**
    Team name query (`_fetch_names`). `_refresh_names` takes optional `player_names` /
    `team_names` maps.
  - An in-progress Season, or a snapshot that is missing, at another version, or stale
    (a referenced Player or Team gone), falls back to `compute_season_award_set`. That keeps
    write-on-first-read in one place.
- **League History.** `build_history_row(..., award_set=None)` takes the batched set. The
  `history` action batches the page's Seasons, the in-progress one included, so
  `?page_size=100` costs no more queries than 20.
- **League player page.** `build_player_detail` makes one League-wide `build_round_dicts`
  call. It tags rows with `_season_id`, which `aggregate_player_stats` ignores, and groups them
  in Python: id-ascending within a Season, Seasons `-id`. The Season-id set replaces the
  per-Season `exists()`. Awards come through `compute_season_award_sets`.
- **Team history.**
  - `_team_season_rows` reads every enrolled Team name in one query, and every completed,
    non-member-night Match in one query (`game_rounds` prefetched, `order_by("id")`), grouped
    by Season.
  - `team_history_seasons` prefetches `teams`, and PERF-03 narrows it to the Team's own
    Seasons (below). `test_team_history_query_counts.py` covers its draft-Season cost, which
    the guardrail cannot see.
- **Power Rankings.** `build_power_rankings` uses `select_related` on the six active slots
  (`_ACTIVE_SLOT_FIELDS`).
- **Owner evaluation / finance writer.** `_ensure_team_finances` is used by the
  `owner-evaluation` action, next-season rollover and
  `manager_awaiting_reassignment`.
  - **Early exit:** it loads the present `(team_id, season_id, hype)` rows in one query. It
    returns when every required pair exists. Required means each id from
    `_enrolled_team_ids_by_season`: the starting ids, else the M2M, with deleted Teams ignored.
  - **Slow path:** it memoises standings per Season (`_team_wins_games_by_team`) and reads
    them lazily, only for Seasons a missing row needs. A repeated id in `starting_team_ids_json`
    credits profit once.
  - Rows are never updated after they are written.

## PERF-03 narrowed Team history Season scan

`league_screen_services.team_history_seasons(team) -> dict[int, Season]` reads only the Seasons
the Team has a `teams` M2M link to (`Season.objects.filter(teams=team)`, `league` selected,
`teams` prefetched), not every Season in the database (spec #320). It keeps the
`enrolled_team_ids` re-check, so "enrolled" keeps its one definition.

- **Why the M2M filter is enough:** `start_season` snapshots `starting_team_ids_json` from the
  M2M, and app code never removes `Season.teams` rows, so snapshot ⊆ M2M. The re-check drops a
  Team added to the M2M after the start.
- **Exception:** Django admin (`SeasonAdmin`) can remove a Team from a started Season; that
  Season then drops out of the Team's history. Accepted under ADR-0004.
- **Rejected:** a JSON `__contains` lookup on the snapshot raises `NotSupportedError` on SQLite.
- **Tests:** `TestTeamHistorySeasonsNarrowedScan` in `matches/tests/test_team_history_query_counts.py`,
  including a call-through spy on `enrolled_team_ids` that must never see an unlinked Season.

## ANG-06-14 League core in Angular

Spec: GitHub issue #409 (tickets #410-#414). The SPA's `leagues/` feature ports the League list,
create (template chooser + Advanced), delete, dashboard, History, the fired Manager's new-team
picker, Start next Season and the League sidebar. The backend gaps it needed (delete-summary, nav,
five dashboard keys, UXP-18 on the API, `?simulation_ready=`) are in `api.md`, **ANG-06-14 League
core API gaps**. Frontend layout: `frontend/CLAUDE.md`.

**Routes** (`leagues/leagues.routes.ts`, `LEAGUES_ROUTES`, lazy from the Shell at `leagues`; the
Shell sidenav lists **Leagues** first):
- `/leagues` — `league-list`: Active and Archived tables (`listAll` per `state`), name links to
  the dashboard, a `league-state` badge (`shared/_badges.scss`), Delete on career rows, "No Leagues
  yet." when empty, a "Create League" link.
- `/leagues/new` — `league-create`: `template` (over `templates()`), `league_name` (required,
  max 100), `difficulty` (easy / medium / hard, default medium, the Django help text in
  `league-create/difficulty.ts`), an "Advanced..." link. 201 ⇒ `/leagues/<id>`; 400 through
  `applyApiErrors`.
- `/leagues/new/advanced` — `league-create-advanced`: every `CreateLeagueForm` field but
  `visibility` and `schedule_format`, with the Form's limits and defaults. The phase composer
  (`phase-composer/`) builds `phases` through the pure `phase-wire.ts` (table-tested);
  `map-rotation-composer/` joins
  ordered ids into `map_rotation`. Map pool and rotation read `/api/maps/?simulation_ready=true`.
- `/leagues/:id` — `LeagueLayout` (loads `nav(id)`; the League nav is a column from 768px and
  collapses behind a "League menu" toggle below; 404 / load error via `notFoundOrError`) with
  children `''` (`league-dashboard`), `history` (`league-history`) and `new-team`
  (`league-new-team`).

**League nav** (`leagues/league-nav.ts`): `LEAGUE_NAV_ENTRIES`, 23 entries (1 top + LEAGUE 6 +
TEAM 4 + PLAYERS 6 + STATS 6). ANG-06-14 shipped Dashboard and History
live; ANG-06-15..18 turned more live (see their sections). Since ANG-06-18, 19 are live and 4 (Trade,
Trading Block, Prospects, Hall of Fame) render through `shared/soon-badge` (`app-soon-badge`: a
disabled `<span>` with a "Soon" badge, never an `<a>`). They stay Soon entries: ANG-06-20 ruled out
a shared "Coming soon" route (spec #460), so they wait for their own features. The dashboard's and
History's Season links ("View bracket", the Season name, "Manage Conferences", member-night "View
results", Awards) went live with ANG-06-18; "View all leaders" links to League Leaders since
ANG-06-15.

**Dashboard.** Renders the `/dashboard/` values: "You" on the snippet row where
`team_id === managed_team.id` and on leader rows whose `player_id` is the Account's Self-insert;
leader names link to `/players/:id/stats`; "View past evaluations" (only with
`has_owner_evaluations`) goes to `history`. Writes: **Start Season** (draft) ⇒
`SeasonsService.start`, snackbar `{season} started — {n} Round(s) scheduled.` (`round_count_total`),
reload; **Start next Season** (completed, not awaiting reassignment) ⇒ `nextSeason`, reload, a 409
`manager_fired` navigates to `new-team`; **Choose new team** (`manager_awaiting_reassignment`) links
to `new-team`.

**Delete** (`leagues/league-delete.ts`, `LeagueDelete` + `canDeleteLeague`): shared by the list
and the dashboard, offered only when `mode === 'league'`. It fetches `deleteSummary`, opens
`ConfirmDialog` (`Delete "<name>"?`, the five counts through the dialog's new optional `details`
list, "Delete permanently"), then DELETE, `League "<name>" deleted.` and `/leagues`.

**History.** `history(id, page, pageSize)`, page size 10 / 25 / 50 / 100 (default 10, always sent),
Prev / Next. The in-progress row (with an "In progress" badge) comes on page 1 only, the API's
behaviour (not pinned per page). MVPs link to `/players/:id/stats` with "You".

**New-team picker.** `newTeamCandidates` as a radio table (rank, team, W-L-T), first row checked;
"Take Over This Team" ⇒ `reassignTeam`, 201 ⇒ `/leagues/:id`; the 409s show their message with a
link back to the dashboard.

**Services / models.** `api/leagues.service.ts` (`LeaguesService`: `list`, `get`, `templates`,
`create`, `delete`, `deleteSummary`, `dashboard`, `nav`, `history`, `newTeamCandidates`,
`reassignTeam`, `nextSeason`), `api/seasons.service.ts` (`SeasonsService.start`),
`api/leagues.models.ts` (every League / Season type and label map),
`MapListParams.simulation_ready`. Spec helpers: `leagues/testing/` (`league-fixtures`,
`releaseLeaguesLoads`).

**Out of scope.** The topnav Play menu and play controls (ANG-06-19/20), owner evaluation and
Conferences screens (shipped with ANG-06-18), stats / team / finance screens (ANG-06-15..17).

## ANG-06-15 League stats in Angular

Spec: GitHub issue #417 (tickets #418-#425). Ports League Leaders, Player Stats, Player Ratings, Team Stats, Statistical Feats, Game Log, Power Rankings and Playoffs as
children of `/leagues/:id`, and turns their eight League nav entries live. The three API additions
(`season_scope.options[].year`, `meta.team_options`, `team_feats[].team_name`) are in `api.md`,
**ANG-01e league screens API**.

**Routes** (lazy, each in its own `leagues/<screen>/` folder): `stats/league-leaders`,
`stats/player-stats`, `stats/player-ratings`, `stats/team-stats`, `stats/statistical-feats`,
`stats/game-log`, `power-rankings`, `playoffs`.

**Shared kit** (`leagues/stats-shared/`; each file's header says how a screen uses it):
- `stats-query.ts`: the URL state `?season=&team=&rate=&sort=&dir=&page=&per_page=`, parsed
  leniently (a bad value takes the default); `statsApiParams` (`ordering` as `-key` / `key`, `team`
  sent as `team_id`, `page` + `page_size` always sent when paged; a `PagedStatsQueryConfig` types
  them required); `withSort` / `withSeason` (also clears `team`) / `withTeam` / `withRate` /
  `withPerPage` (each back to page 1) / `withPage`; `recoverFrom`: a 400 on `season`, `team_id`,
  `rate` or `ordering` drops that param, a 404 past page 1 goes to page 1, anything else is `null`
  (the error box). Page sizes 10 / 25 / 50 / 100, default 10.
- `stats-screen.ts` (`injectStatsScreen(config, load)`): reads `:id` and the query, loads with
  `switchMap` (a new request supersedes the one in flight), applies `recoverFrom` with
  `replaceUrl`, else sets `loadError`. Exposes `query`, `result`, `loadError`, and the template
  helpers `sortParams(key)`, `arrow(key)`, `pickSeason` / `pickTeam` / `pickRate` / `pickPerPage` /
  `pickPage`.
- `league-context.ts` (`LeagueContext`, provided by `LeagueLayout`): the nav, for headings that
  name the League and Player Ratings' `displayed_season` check.
- `season-picker` (`name (year)`, Career optional), `team-picker` (configurable "All Teams"
  label), `stats-paginator` + `app-per-page-picker`, `player-cell` (link to `/players/:id/stats`,
  read-only watched star, "You"), `_stats-page.scss` (host, filters, scroll, table, notice, error,
  footer). `shared/_badges.scss` gained the `feat` mixin (`[data-season-best]` variant).
- Data: `api/league-stats.service.ts` (`LeagueStatsService`, one method per screen) and
  `api/league-stats.models.ts`; `ApiClient.getWithMeta` and the meta-generic `Page<T, M>` /
  `list<T, M>`. Spec fixtures: `leagues/testing/league-stats-fixtures.ts`.

**Screens:**
- **League Leaders:** four top-10 boards, each sorted on the client (component state, not the
  URL); the subheading names the selected scope ("Career" or the Season), not the displayed Season.
- **Player Stats:** the 17 `_PLAYER_STATS_COLUMNS`, the Rate picker (`rate=total|per_game|per_10`),
  Team picker, paging. In the empty states the paging footer is hidden, and the Team picker shows
  only while a Team filter is set (so it can be cleared).
- **Player Ratings:** no Season picker; `RATING_SORT_KEYS_DISPLAY` headers (full name as `title`)
  plus Age, Site, Ht, GP, Start; no MMR / Rank. Team links to `/teams/:id`. The table waits for the
  League nav, so a League with no Season never flashes it.
- **Team Stats, Game Log, Power Rankings:** not paged; Game Log's Red / Blue / Score link to
  `/rounds/:id`; Power Rankings' Team links to `/teams/:id`.
- **Statistical Feats:** paged feed (`data` is `{feats, team_feats}`) with feat badges and a "Team
  Feats" list; a Team feat links its Team only when `team` is set.
- **Playoffs:** per bracket, the conference, stage badge, champion (the bracket still draws under
  it), pending notice, or the reused tournaments `app-bracket`; then the Worlds
  table. No Career option.

**Links:** since ANG-06-16, Player names go to the League player page `/leagues/:id/players/:id`
and the ★ toggles the Watch flag (both through `player-cell`); the Ratings Team cell still links
to `/teams/:id`.

**Out of scope.** The watch-flag toggle, League player detail and roster (shipped in ANG-06-16),
MMR / Rank (STAT-PROXY-01), sort params on the Leaders API.

## ANG-06-16 League team & player in Angular

Spec: GitHub issue #428 (tickets #429-#435). Ports Team Roster, Team Schedule, Team History, League player detail, Free Agents and Watch List as children of
`/leagues/:id`. It turns their five League nav entries live (Roster, TEAM Schedule, TEAM History,
Free Agents, Watch List), and makes the Watch flag a real toggle on every League table. The
three API additions (`player_bio` on roster rows, `meta.team_options` on the three team actions,
`can_link_self_insert`) are in `api.md`, **ANG-01e league screens API**.

**Routes** (lazy, one folder each): `team/roster` (`team-roster/`, `LeagueTeamRoster`),
`team/schedule` (`team-schedule/`), `team/history` (`team-history/`), `players/free-agents`
(`free-agents/`), `players/watch-list` (`watch-list/`), `players/:playerId` (`league-player/`,
`LeaguePlayerDetail`). The three team screens read `?team=`; without it they show
`nav.schedule_team` (`resolveTeamParam`).

**Data:**
- `api/league-team.service.ts`: `LeagueTeamService.roster` / `schedule` / `history` /
  `historyPlayers`.
- `api/league-players.service.ts`: `LeaguePlayersService.freeAgents` / `watchList` / `player` /
  `watch` (PUT) / `unwatch` (DELETE) / `clearWatchList` (DELETE `watch-list/`).
- Fixtures: `leagues/testing/league-team-fixtures.ts`, `league-players-fixtures.ts`.
- `releaseLeagueTeamPlayerLoads`.

**Watch flag** (`leagues/watch/`):
- `WatchState` is provided by `LeagueLayout` beside `LeagueContext`.
  - `watched(id, seed)`: the state this visit last saved, else the row's seed.
  - `toggle(id, current)`: PUT / DELETE by the current state. A failure keeps the state and shows
    the `apiMessage` snackbar. A toggle while one is pending is ignored.
  - `clearAll()`: Remove All.
- `app-watch-flag` is a `<button aria-pressed>`: ☆ in `--lf-text-muted` when off, ★ in
  `--lf-accent` when on. Its label is "Watch <name>" / "Stop watching <name>", and it is disabled
  while pending.
- `player-cell` renders it and links the name to `/leagues/:id/players/:id`. The League id comes
  from the new `LeagueContext.leagueId`, which the layout sets from `:id` before the nav loads.

**Kit additions** (`leagues/stats-shared/`):
- `player-columns.ts`: the Ratings / Stats columns moved out of Player Ratings / Player Stats, plus
  `statCell`, `potentialText`, `orDash`, `roleTitle`.
- `rate-picker.ts`, moved from `player-stats/`.
- `team-param.ts`: `resolveTeamParam`, `teamQueryParam`.
- `TeamPicker [allowAll]=false`, for the three team screens (no "All Teams").
- `LeagueContext.noSeason`.
- `injectStatsScreen(config, load, { waitForSeason: true })`: waits for the nav and sends nothing
  with no Season, and gains `retry()`. Free Agents and Watch List opt in; the ANG-06-15 screens
  keep the old, ungated behaviour.
- `shared/line-chart.ts` gained `[xLabel]`, which names a `count` axis's ticks and tooltip title.

**Screens:**
- **Roster:**
  - Starting Six / Bench, with MMR and Rank as "-".
  - The no-Season and no-team states hide the picker and both tables.
  - A 404 on a stale `?team=` is the no-team state.
- **Team Schedule:**
  - Upcoming / Completed / Member Night Appearances (that section only when non-empty).
  - Round links go to `/rounds/:id`.
  - With no Season it shows the no-Season notice.
- **Team History:**
  - Overall, then a client-sorted Seasons table (`?seasons_sort=&seasons_dir=`, default year
    desc), then the server-paged Players table (kit `sort` / `dir` / `page` / `per_page`). Neither
    sort resets the other.
  - Rows are tinted by `on_team`, with a visually hidden "(current)" / "(former)".
  - "No history yet." is not ported (unreachable once a Team resolves).
- **League player:**
  - Header: ★, h1, You, "Career stats (global)", and This is me (`can_link_self_insert`) /
    Unlink.
  - Overall / Potential cards; five rating groups; Regular Season
    plus a bold Career row.
  - Playoffs / Salaries / Transactions show the Coming-soon text.
  - Ratings history: the `count` chart with Season-name `xLabel`, plus a table.
  - Awards: each Season name links to that Season's awards (ANG-06-18; a Soon badge before).
  - A 404 shows "Player not found".
- **Free Agents:** the Player Ratings columns plus MMR / Rank "-", the count, paging. No sign
  action.
- **Watch List:**
  - Player Stats columns, with the Season and Rate pickers in every state.
  - Remove All asks through `ConfirmDialog`, then shows the empty-list state.
  - An un-starred row stays until reload.

**Out of scope.** Sign / release free agents, Trade / Trading Block / Prospects / Hall of Fame
(still Soon entries after ANG-06-20), real MMR / Rank (STAT-PROXY-01). Follow-up #436: one
nav-gated load helper for Roster / Schedule / History / League player.

## ANG-06-17 League finances in Angular

Spec: GitHub issue #439 (tickets #440, #441). Ports League Finances and Team Finances as
children of `/leagues/:id`, and turns the LEAGUE > Finances and TEAM >
Finances nav entries live. The three API additions (`meta.team_options` on the Team Finances GET /
PATCH, `max_level` / `default_level` on its full payload, `finance_enabled` on `nav/`) are in
`api.md`.

**Routes** (lazy): `finances` (`league-finances/`, `LeagueFinances`) and `team/finances`
(`team-finances/`, `LeagueTeamFinances`; `?team=`, else `nav.schedule_team` via
`resolveTeamParam`).

**Data:** `api/league-finances.service.ts` (`LeagueFinancesService.league` / `team` (`getWithMeta`)
/ `updateTeam` (`patch`: no meta, the screen keeps the GET's `team_options`)), the types in
`api/league-finances.models.ts` (`INJURY_POLICY_LABELS` included), fixtures in
`leagues/testing/league-finances-fixtures.ts`. `shared/money-k.ts` `moneyK` formats money
as `$7.2k` / `-$7.2k` (no sign on a zero); both screens use it, so the League table's
old bare `7.2k` now reads `$7.2k`. `releaseLeagueTeamPlayerLoads` also releases both finance GETs.

**States** (both screens, in this order): nav `finance_enabled: false` ⇒ "Finances are
disabled for this League."; no displayed Season ⇒ the no-Season notice; (Team only) no Team, or a
404 on `?team=` ⇒ "No team available to display in this League's current Season."; none of these
sends a request. A loaded `finance_enabled: false` also shows the disabled notice. Any other load
failure shows the error with Retry.

**League Finances:** Season name line, then Team / Revenue / Profit / Cash / Payroll; each `null`
money cell is a muted `—` on its own; "No enrolled teams.".

**Team Finances:**
- Tiles in a grid (all five in one row at 1280px, two per row at 768px): Wins `W–L`, Hype (2 dp,
  a `--lf-accent` bar clamped to 0–100%, `role="progressbar"`), Revenue / Expenses from
  `history[0]` or `—`, Cash.
- Expense Settings: four Budget level fields (required, whole number, 1..`max_level`) with the cost /
  level / neutral hint, Injury policy, Ticket price (required, >= 0, `step` 0.5).
  Client errors use Django's Form wording. A 400 lands on its control; any 409, and a 400 field with no
  control, lists under "Expense settings not saved:" as `<Field label>: <message>` (bare when
  non-field). Success: the "Expense settings saved." snackbar and the PATCH response's values. The
  form and Save are disabled while a save is in flight; a Team switch, Retry or leaving the screen
  cancels it.
- `editable: false` (not `current_team`): "Only your own team's expense settings can be changed."
  and every control disabled.
- Player Salaries (Total = `live_payroll`), Player availability, Season history, with the "You"
  badge on the Self-insert.

**Out of scope.** Finance charts, history sort / paging, the per-line money breakdown, live
projections, Owner Evaluation (its own screen, ANG-06-18). The form's "Scouting & coaching are
cost-only for now" text is stale: FIN-02 / FIN-03 made both live. The
nav-gated load pipeline both screens repeat is follow-up #436's.

## ANG-06-18 Season screens in Angular

Spec: GitHub issue #444 (tickets #445-#451). Ports the Season dashboard, standings, schedule,
awards, owner evaluation and manage conferences screens under `/leagues/:id/seasons/:seasonId`,
inside `LeagueLayout`. The one API change is `member_night_progress` on the Season dashboard
(`api.md`).

**Routes** (`leagues/seasons/seasons.routes.ts`, `SEASON_ROUTES`, lazily loaded as the `:id`
child `seasons/:seasonId`): `''` (`SeasonDashboard`, title `Season`), `standings`
(`SeasonStandings`), `schedule` (`SeasonSchedule`), `awards` (`SeasonAwards`, title `Season
Awards`), `owner-evaluation` (`OwnerEvaluation`), `conferences` (`ManageConferences`), one folder
each.

**Kit** (`leagues/seasons/`):
- `SeasonLayout` loads `SeasonsService.get(:seasonId)` once per id and renders the screen only
  once the Season has loaded and `season.league.id === :id`; a 404 or another League's Season shows
  "Season not found" + "← Back to League" (I1), any other failure the error with Retry (I2).
- `SeasonContext` (provided by the layout): `season`, `seasonId`, `leagueId`,
  `requireSeasonId()` / `requireLeagueId()` (non-null inside a screen), `link(...screen)` (the
  router commands for `/leagues/:id/seasons/:seasonId/<screen>`).
- `injectSeasonLoad(fetch, { params?, splitNotFound? })` (`season-load.ts`): the shared load +
  Retry chain (`data`, `loadError`, `notFound`, `retry`); `params` re-fetches on each emission
  (Standings' sort), `splitNotFound` routes a 404 to `notFound` (Owner Evaluation). Manage
  Conferences keeps its own pipeline (its maps GET follows the Conferences GET and Retry re-sends
  whichever failed).
- `SeasonBreadcrumb` (`<League> › <Season>`) above every sub-screen's h1.
- Spec helpers in `seasons/testing/`: `season-fixtures` (`season()`: Season 11 of League 1,
  active; a builder per screen body), `season-loads` (`releaseSeasonLayout`,
  `releaseSeasonScreenLoads`, which also releases Standings' `leagues/{id}/` and Conferences'
  `maps/` side loads).
- `leagues/dashboard-cards/` (`DashboardCards`): the body cards both dashboards render (Top
  standings, member night, Next round, Rounds played, the three leader cards, "View all leaders");
  inputs `body`, `managedTeamId`, `memberNight`, `leadersLink`, `resultsLink` ("View results" is
  hidden when null).

**Data:** `api/seasons.service.ts` (`SeasonsService`: `start`, `get`, `dashboard`, `standings`,
`schedule`, `awards`, `ownerEvaluation`, `conferences`, `replaceConferences`), the types in
`api/seasons.models.ts` (`Season` / `SeasonDashboard` stay in `leagues.models.ts`).

**Screens:**
- **Season dashboard:** h1 `<League> — <Season>`, state badge, "View past evaluations" (always),
  "View bracket" (`/leagues/:id/playoffs?season=`), the map line, Awards, draft-only Manage
  Conferences, then `DashboardCards` with no managed Team; "View all leaders" goes to League
  Leaders `?season=<id>`; member-night "View results" to `schedule#member-nights`.
- **Standings:** `?sort=&dir=` in the URL (only the 17 known keys and `asc` / `desc` are sent),
  sort arrows, first click ascending; one table per Conference group; the draft preview note
  and "No teams enrolled in this Season yet."; "You" on `current_team` in a career League only
  (from `LeaguesService.get`, best-effort: a failed League GET leaves the table unmarked);
  "Champion: <name>".
- **Schedule:** per-matchday tables, results linked to `/rounds/:id`, "Unplayed"; the member-nights
  section (`id="member-nights"`) scrolls into view when the route fragment is `member-nights`.
- **Awards:** the 11 rows, `—` for an unwon award, "You" on the Self-insert; winner names are
  plain text.
- **Owner Evaluation:** verdict badge (key upper-cased), hot-seat / fired texts, the Factor table
  (3 / 3 / 1 decimals via `players/shared/benchmark-format.ts` `fixed()`); "Start Next Season" ⇒
  `LeaguesService.nextSeason`, then the new Season's dashboard (409 `manager_fired` ⇒ `new-team`,
  other failures inline); "Choose New Team" when fired and not reassigned; a 404 shows "Owner
  evaluation not found" with "← Back to Season".
- **Manage Conferences:** editable (draft): rows by `ordinal`, rename / Remove / "+ Add
  conference", each row's `MapRotationComposer` (maps `?simulation_ready=true`; the composer gained a
  `disabled` input and now wraps so the picker keeps a full row in a narrow card), Team assignment as
  native selects in an outline `mat-form-field` (`matNativeControl`; option values are stable row
  keys, so removing a row unassigns only its Teams; a blank name lists as "Conference <n>"), Save
  ⇒ `replaceConferences` (400 / 409 messages listed above the form, edits kept; 200 ⇒ the response
  and "Conferences saved."), the form locked while saving. Read-only (started Season): cards with
  Team names and the numbered rotation (`starting_map_rotation_ids ?? map_rotation_ids`, names
  from all maps), "No rotation maps.", the no-Conferences note and the locked note.

**Placeholders turned live:** the League nav's Standings / Schedule (to the nav's
`displayed_season`; with none, `link()` is null and the entry is not rendered); the League
dashboard's Season name, "View bracket", "Manage Conferences" and member-night "View results";
History's Season name and Awards; the League player's award Season names.

**Seed:** `seed_browser_check --league` also builds "Browser Check Conferences" (sandbox, a draft
Season of A's four Teams in North / South with rotations, map mode `rotate_by_conference`) for the
editable Conferences screen and the draft Standings preview.

**Out of scope.** Play controls and the topnav Play menu (ANG-06-19), a past-evaluations list,
any engine / model / Season-lifecycle change.
