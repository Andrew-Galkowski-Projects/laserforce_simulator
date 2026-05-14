# matches/

Handles match creation, game round simulation, event logging, and result views.

## Models (`matches/models.py`)

**`Match`**: Two `GameRound`s; teams swap colors between rounds. Winner is determined by rounds won, then total cumulative points. A 10,000-point bonus is awarded for eliminating the opposing team entirely.

**`GameRound`**: One of the two rounds in a match; represents a 15-minute simulation. Has an optional `arena_map` FK (`core.ArenaMap`, null/blank, SET_NULL) and `zone_size` IntegerField (null/blank). Both are set by the simulator when a map is provided; null means the round ran with the 3-zone fallback.

**`PlayerRoundState`**: Starting resources are role-dependent (lives, shots, special, missiles). Tracks final resource counts, tags, misses, zone visits, MVP score. `was_eliminated_at` stores seconds into the round (901 = survived the full round). The MVP formula is role-specific and weighted heavily toward that role's primary contribution. Also tracks `follow_up_shots`, `reaction_shots`, and uptime breakdown fields (`seconds_active`, `seconds_not_targetable`, `seconds_reset_window`). Cell position: `cell_row` and `cell_col` (IntegerFields, null/blank) store the player's current cell when a map is used — updated each tick after movement. `zone_fallback` (was `current_zone` DB column) stores the zone index (0=red, 1=neutral, 2=blue); `current_zone` is a `@property` that reads `zone_fallback`. The simulator updates `zone_fallback` after each cell move via `player.save(update_fields=["cell_row", "cell_col", "zone_fallback"])`.

Forwarding properties added to satisfy the duck-type interface required by `sim_helpers/combat.py` and `mechanics.py` (so shared functions work with both `PlayerRoundState` and `PlayerState`): `accuracy`, `survival`, `name`, `player_awareness` (all delegating to `self.player`), `last_shot_time` (backed by a transient `_last_shot_time` attribute, default −99.0), and `tag_id_key` (returns `self.get_tag_id()`). No DB fields — no migration required.

`get_mvp` is now a thin delegating property that calls `calculate_mvp(self)` from `matches.sim_helpers.score_calculator`. `max_lives` and `max_shots` now look up `MAX_LIVES`/`MAX_SHOTS` from `matches.sim_helpers.role_constants` rather than inline dicts.

**`GameEvent`**: Every action (tag, missile, special, miss, resupply, base capture, elimination, movement) is logged here with an actor, optional target, timestamp in seconds, points, and a JSON `metadata` field. Movement events (`event_type="movement"`) carry `cell_row`, `cell_col`, `new_zone`, and `actor_role` in `metadata` for replay.

## Simulation Engine (`matches/simulation.py`)

Two simulators live in `matches/simulation.py`:

**`ResourceBasedSimulator`** — DB-backed, writes `GameEvent` rows and `PlayerRoundState`. Runs in 2-second ticks. Used for full match simulation with event replay. Prefer this when you need the game event log or a persisted round. All match and single-round creation views use this exclusively — the legacy `SimpleMatchSimulator` has been removed.

Public methods accept an optional keyword-only `arena_map` parameter:
- `simulate_match(team_red, team_blue, match_type="friendly", *, arena_map=None)`
- `simulate_single_round_detailed(team_red, team_blue, *, arena_map=None)`
- `simulate_detailed_round(team_red, team_blue, match=None, round_number=1, *, arena_map=None)`

Static helpers:
- `_resolve_map_data(arena_map)` — validates the map's confirmed zone config, unwraps `zone_data` dict format (`{"zones": [...], "blocked_edges": {...}, "wall_meta": {...}, "elevation": [...]}` in production; raw list in older/test data), batches base config queries, and loads `SightLineConfig`, `BaseSightLineConfig`, `MapCellRankingConfig`, and `HeavyStrongSpotsConfig`. Returns a `MapData` dataclass (see below) or a zeroed `MapData` when `arena_map` is `None`. `spawn_pools` is `{"red": [(r,c),...], "blue": [(r,c),...]}` read from `zone_data["red_spawn"]`/`zone_data["blue_spawn"]` — no extra DB query. `elevation_grid` is the 2D float array from `zone_data["elevation"]`; defaults to `None` (treated as all-zero) when the key is absent. Raises `ValueError` if the map has no confirmed config, a missing base, no computed sight lines, or no computed base sight lines.
- `_build_spawn_assignments(roster_roles, team_color, spawn_cells, team_spawn_pools) -> dict[int, tuple | None]` — MAP-08 entry point. Pre-computes spawn cells for an entire team using role-priority, no-replacement drawing. Pool is sorted ascending by distance to the enemy base (most-aggressive first); a `deque` provides O(1) front/back draws. Commander/Heavy draw from the front (closest to enemy); Medic/Ammo draw from the back (farthest); Scouts draw from whatever is left. Overflow players (pool exhausted, base cell not yet drawn) share the base cell; if the base cell was already drawn, they receive `None` and fall back to 3-zone.
- `_zone_from_cell(row, col, spawn_cells: dict | None)` — returns zone index (0=red, 1=neutral, 2=blue) by Manhattan-distance proximity to base cells. Nearest base type wins; neutral bases take precedence when closer than or equidistant to both team bases. Returns 1 (neutral) when `spawn_cells` is None/empty or red/blue base is absent. (Replaces the old cell-value lookup 2→0/3→2.)
- `_build_movement_ctx(zone_data, spawn_cells, sight_data=None, base_sight_data=None, cell_ranking=None, strong_spots=None, wall_meta=None, elevation_grid=None)` — returns `{"adj": ..., "spawn_cells": ..., "zone_data": ..., "sight_data": ..., "base_sight_data": ..., "cell_los_counts": ..., "high_los_cells": ..., "strong_spots": ..., "wall_meta": ..., "elevation_grid": ...}` or `None` when `zone_data` is `None`. `wall_meta` defaults to `{}` and `elevation_grid` defaults to `None` when not provided. `elevation_grid` is the same 2D float list read from `zone_data["elevation"]`; when `None`, all elevation lookups treat cells as 0.0. All dicts are built once per round and passed through the call chain.

Module-level class:
- `MapData` — dataclass holding all map-derived data needed for one simulation round: `zone_size`, `spawn_cells`, `zone_data`, `sight_data`, `base_sight_data`, `cell_ranking`, `strong_spots`, `wall_meta`, `spawn_pools`, `elevation_grid`. Returned by `_resolve_map_data`; passed into `_build_movement_ctx` and spawn-assignment helpers.

Visibility, elevation, and base-interaction helpers have moved to `matches/sim_helpers/combat.py` — see [`sim_helpers/CLAUDE.md`](sim_helpers/CLAUDE.md) for their full descriptions. `simulation.py` now imports them from `combat` rather than defining them inline.

Cell-aware movement (MAP-02/05, active when `movement_ctx is not None` and `player.cell_row is not None`):
- `_choose_goal_cell(player, all_alive, movement_ctx, intended_action="")` — delegates to `pathfinding.choose_goal_cell`; passes the player's `last_chosen_action` as `intended_action` so movement is action-aware (MAP-05). Default goal is the enemy base cell.
- `_move_to_cell(player, second, goal_cell, movement_ctx)` — calls `astar_next_step`, updates `cell_row`/`cell_col`/`zone_fallback`, saves to DB, writes a `GameEvent(event_type="movement")`.

When no map is assigned (`movement_ctx is None`), the old `_change_zone` 3-zone fallback is used (MAP-06 compatibility).

**`BatchSimulator`** — pure in-memory, no DB writes. Uses `PlayerState` dataclasses (see `matches/sim_helpers/player_state.py`). Runs in **0.5-second ticks** to model real shot speeds. Used by `score_averages` and batch win-rate analysis. A round typically runs in ~25 ms vs ~9 s for the DB-backed simulator.

`run(team_red, team_blue, n=100, *, arena_map=None)` — accepts an optional `arena_map` keyword argument; when provided, resolves map data, builds a `movement_ctx`, and passes it to `_simulate_round` so players navigate by A* rather than the 3-zone fallback. `_make_players` accepts `spawn_cells` and `zone_data` kwargs and initialises `cell_row`/`cell_col` from the team's spawn cell. `_move_player_in_memory` mirrors `_move_to_cell` but updates `player.current_zone` directly without any DB writes.

Both simulators follow the same per-tick loop:

1. Process pending missiles/nukes that have completed their delay
2. Process pending deferred follow-up and reaction shots (shots scheduled by shot-cooldown logic)
3. Each active player picks an action (weighted random by role, zone, remaining resources)
4. Resolve the action — update state and optionally write a `GameEvent`
5. Check for team eliminations

Action weights are in `matches/sim_helpers/weights.py`. See [`sim_helpers/CLAUDE.md`](sim_helpers/CLAUDE.md) for details.

## Shot Speed & Follow-up Mechanics (BatchSimulator)

Real Laserforce shot speeds are modelled in `BatchSimulator`:

| Class | Shot cooldown | Notes |
|-------|--------------|-------|
| Scout with rapid fire | 0.0 s | Unlimited; follow-ups fire in the same tick |
| All others | 0.5 s | 2 shots/second |
| Heavy | 1.0 s | 1 shot/second |

`_shot_cooldown(player, second)` returns the cooldown. `_plan_action` zeroes the `tag_player` weight when `second - player.last_shot_time < cooldown`. `last_shot_time` is updated on every fired shot (hit, miss, or hidden-miss).

**Follow-up shots**: when a hit does NOT down the defender (shields > 0 after impact), the attacker may fire again. The follow-up is scheduled into `pending_followups` at `second + cooldown` and processed at the start of the next eligible tick. Rapid-fire scouts chain immediately in the same tick. Chain depth is capped at 2. A hit that takes shields to 0 is never eligible — a heavy always downs its target in one shot so never generates follow-ups.

**Reaction shots**: after being tagged or missed, the defender may fire back (rolled against `player_awareness`). Same cooldown scheduling logic applies.

## Role Mechanics

| Role | Shields / Shot Power | Has Missiles | Can Resupply |
|------|---------------------|--------------|--------------|
| Commander | 3 / 2 | Yes | No |
| Heavy | 3 / 3 | Yes | No |
| Scout | 1 / 1 | No | No |
| Medic | 1 / 1 | No | Yes (lives) |
| Ammo | 1 / 1 | No | Yes (shots) |

Shields absorb damage; a hit that reduces shields to 0 costs the defender one life and resets shields to max. Respawn after a life loss requires an 8-second cooldown (4 seconds taggable in the "reset window", 4 more seconds until fully active). Zone values: 0 = red_zone, 1 = neutral_zone, 2 = blue_zone.

**Heavy nerf**: heavies have 1 shot/second (vs 2/s for other roles) and always down their target in one hit, so they never generate follow-up shots.

**Scout rapid fire**: when the scout's special is active (`special_active_until > second`), `_shot_cooldown` returns 0.0, giving unlimited fire rate.

## Score Calibration Targets

Used by `score_averages` to measure simulation accuracy against real-world averages:

| Role | Target score |
|------|-------------|
| Commander | 9,952 |
| Heavy | 6,482 |
| Scout | 5,102 |
| Ammo | 3,242 |
| Medic | 2,282 |

## REST API (`matches/serializers.py`, `matches/api_views.py`)

Read-only DRF endpoints registered under `/api/`:

| Endpoint | Serializer | Notes |
|----------|-----------|-------|
| `GET /api/matches/` | `MatchSerializer` | Includes `round_ids` (PK list, not nested) |
| `GET /api/matches/<id>/` | `MatchSerializer` | Same — full match fields + round PK list |
| `GET /api/rounds/` | `GameRoundListSerializer` | Slim — no `player_states`, no `event_log` |
| `GET /api/rounds/<id>/` | `GameRoundSerializer` | Full — nested `player_states` array |
| `GET /api/rounds/<id>/events/` | `GameEventSerializer` | Paginated, ordered by timestamp |

**Serializer split:** `GameRoundListSerializer` (list) omits `player_states` to prevent serializing up to 240 objects per page. `GameRoundSerializer` (detail) adds the full nested `player_states`. Both share `_GAME_ROUND_FIELDS` and explicitly exclude `event_log` (legacy text dump).

**`MatchSerializer`** — exposes `round_ids` as a `PrimaryKeyRelatedField` (source=`game_rounds`). Uses `fields = "__all__"` since Match has no sensitive or volatile fields.

**`GameEventSerializer`** / **`PlayerRoundStateSerializer`** — exclude the parent FK (`game_round`) since events and states are always accessed through their parent round.

**N+1 guard:** the `/events/` action adds `.select_related("actor", "target")` so actor/target player lookups are batched. The `GameRoundViewSet.get_queryset()` only adds `.prefetch_related("player_states")` for the `retrieve` action — list and events requests skip the prefetch.

## URLs

```
/matches/                            → match list, create, detail
/matches/create/                     → create a full 2-round match
/matches/single-round/create/        → create a standalone game round (always detailed)
/matches/game-round/<id>/            → detailed round view
/matches/game-round/<id>/events/     → event timeline/filtering
/matches/team/<id>/history/          → team win/loss history
/matches/simulate-batch/             → run N in-memory simulations

/api/matches/                        → MatchViewSet (list, detail)
/api/rounds/                         → GameRoundViewSet (list, detail, events action)
/api/rounds/<id>/events/             → paginated GameEvent list for that round
```

## Forms (`matches/forms.py`)

**`MatchSetupForm`** and **`SingleRoundSetupForm`** both include an optional `arena_map` `ModelChoiceField` (empty_label="No map (3-zone fallback)"). The queryset is populated in `__init__` via `_maps_with_confirmed_config()` which returns only `ArenaMap` objects with at least one confirmed `MapZoneConfig`. Rounds without a map fall back to the existing 3-zone logic.

The corresponding views (`create_match`, `create_single_round`) extract `arena_map = form.cleaned_data.get("arena_map")`, pass it keyword-only to the simulator, and catch `ValueError` (missing config, missing base, or missing sight lines) to display a form error without crashing.

## Templates

All templates live in `laserforce_simulator/templates/`. The `game_round_events.html` template has event filtering and color-coded display; `game_round_detail.html` shows per-player stats and MVP scores. Both `enhanced_match_setup.html` and `enhanced_single_round_setup.html` include the optional `arena_map` picker field.

## Tests

`matches/tests/` package:
- `test_sim_core.py` — `ResourceBasedSimulator` mechanics, game events, round outcomes
- `test_batch_sim.py` — `BatchSimulator` mechanics
- `test_map.py` — map-related tests: adjacency building, A* pathfinding, movement events, cell-aware movement, batch-sim with map (`TestMap02CellMovement`); LOS target filtering and wall-blocking acceptance tests (`TestMap03LOSTargeting`, `TestMap03DBIntegration`); base-sight gate unit tests and DB integration (`TestMap04BaseInteraction`, `TestMap04DBIntegration`); `compute_high_los_ranking` sort correctness and strong-spots view endpoints (`TestMap05ComputeHighLosRanking`, `TestMap05StrongSpotsViews`); 25 pure unit + 2 DB tests for MAP-07 wall types (`TestMap07WallTypes`, `TestMap07DBIntegration`)
- `test_goal_selection.py` — MAP-05 tests: `TestMap05RoleAwareGoal` (17 pure unit tests for Scout→high-LOS, Heavy healthy→strong spots, Heavy unhealthy→medic/ammo, Medic→low-LOS in Heavy's sight, Ammo→high-LOS in Heavy's sight, Commander→enemy medic, action-driven tag/resupply/hide, critical-resource override, default-enemy-base); `TestMap05DBIntegration` (4 DB tests for `_resolve_map_data` 10-tuple, `_build_movement_ctx` MAP-05 keys, empty-list fallback when configs absent)
- `test_map09_high_ground.py` — MAP-09 tests: elevation/wall-height DB round-trips, `can_shoot_over_wall` formula, `_has_los` elevation-aware LOS (including asymmetric LOS regression), `elevation_hit_modifier` formula, backwards-compat with no elevation key, `_resolve_map_data` returns `elevation_grid` at index 9
- `test_mechanics.py` — pure-unit tests for `mechanics.py` functions (`shot_cooldown`, `choose_tag_target`, `choose_resupply_target`, `choose_zone_change`); no DB required
- `test_roster.py` — team/player roster validation
- `test_mvp.py` — MVP scoring formulas; `TestCalculateMvp` tests `calculate_mvp` directly without the ORM
- `test_weights.py` — weight function unit tests (`TestWeightFunctions`)
- `views_tests.py` — view behaviour: URL routing, form submissions, context keys
- `test_serializers.py` — unit tests for all five serializer classes (including list vs detail split)
- `test_apis.py` — HTTP-level tests for `/api/matches/` and `/api/rounds/` (including `/events/` action)
- `conftest.py` — shared `make_team_with_slots(prefix)` helper

## Sub-packages

- [`sim_helpers/CLAUDE.md`](sim_helpers/CLAUDE.md) — `BatchSimulator` helper modules: `PlayerState` dataclass, action weights, pathfinding, `mechanics.py` (pure game mechanics), `combat.py` (shared combat resolution), `role_constants.py` (canonical role stats), `score_calculator.py` (MVP formula)
- [`management/commands/CLAUDE.md`](management/commands/CLAUDE.md) — `score_averages` and `game_analysis` management commands