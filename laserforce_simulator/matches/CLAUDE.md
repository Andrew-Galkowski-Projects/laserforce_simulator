# matches/

Handles match creation, game round simulation, event logging, and result views.

## Models (`matches/models.py`)

**`Match`**: Two `GameRound`s; teams swap colors between rounds. Winner is determined by rounds won, then total cumulative points. A 10,000-point bonus is awarded for eliminating the opposing team entirely.

**`GameRound`**: One of the two rounds in a match; represents a 15-minute simulation. Has an optional `arena_map` FK (`core.ArenaMap`, null/blank, SET_NULL) and `zone_size` IntegerField (null/blank). Both are set by the simulator when a map is provided; null means the round ran with the 3-zone fallback.

**`PlayerRoundState`**: Starting resources are role-dependent (lives, shots, special, missiles). Tracks final resource counts, tags, misses, zone visits, MVP score. `was_eliminated_at` stores seconds into the round (901 = survived the full round). The MVP formula is role-specific and weighted heavily toward that role's primary contribution. Also tracks `follow_up_shots`, `reaction_shots`, and uptime breakdown fields (`seconds_active`, `seconds_not_targetable`, `seconds_reset_window`). Cell position: `cell_row` and `cell_col` (IntegerFields, null/blank) store the player's current cell when a map is used — updated each tick after movement. `zone_fallback` (was `current_zone` DB column) stores the zone index (0=red, 1=neutral, 2=blue); `current_zone` is a `@property` that reads `zone_fallback`. The simulator updates `zone_fallback` after each cell move via `player.save(update_fields=["cell_row", "cell_col", "zone_fallback"])`.

**`GameEvent`**: Every action (tag, missile, special, miss, resupply, base capture, elimination, movement) is logged here with an actor, optional target, timestamp in seconds, points, and a JSON `metadata` field. Movement events (`event_type="movement"`) carry `cell_row`, `cell_col`, `new_zone`, and `actor_role` in `metadata` for replay.

## Simulation Engine (`matches/simulation.py`)

Two simulators live in `matches/simulation.py`:

**`ResourceBasedSimulator`** — DB-backed, writes `GameEvent` rows and `PlayerRoundState`. Runs in 2-second ticks. Used for full match simulation with event replay. Prefer this when you need the game event log or a persisted round. All match and single-round creation views use this exclusively — the legacy `SimpleMatchSimulator` has been removed.

Public methods accept an optional keyword-only `arena_map` parameter:
- `simulate_match(team_red, team_blue, match_type="friendly", *, arena_map=None)`
- `simulate_single_round_detailed(team_red, team_blue, *, arena_map=None)`
- `simulate_detailed_round(team_red, team_blue, match=None, round_number=1, *, arena_map=None)`

Static helpers:
- `_resolve_map_data(arena_map)` — validates the map's confirmed zone config, unwraps `zone_data` dict format (`{"zones": [...], "blocked_edges": {...}}` in production; raw list in older/test data), batches base config queries, and loads `SightLineConfig` and `BaseSightLineConfig`. Returns `(zone_size, spawn_cells, zone_grid, sight_data, base_sight_data)` or `(None, {}, None, None, {})` when `arena_map` is `None`. Raises `ValueError` if the map has no confirmed config, a missing base, no computed sight lines, or no computed base sight lines.
- `_zone_from_cell(zone_data, row, col)` — maps cell type to zone index: 2→0 (red), 3→2 (blue), else 1 (neutral).
- `_build_movement_ctx(zone_data, spawn_cells, sight_data=None, base_sight_data=None)` — returns `{"adj": ..., "spawn_cells": ..., "zone_data": ..., "sight_data": ..., "base_sight_data": ...}` or `None` when `zone_data` is `None`. All dicts are built once per round and passed through the call chain.

Module-level helpers:
- `_get_los_targets(actor, candidates, movement_ctx)` — returns the subset of `candidates` visible to `actor`. With a map, looks up the actor's cell key in `sight_data` (a `{"r,c": frozenset}` dict) and filters to candidates whose cell is in the visible set. Falls back to same-zone equality when `movement_ctx` is `None`, `sight_data` is absent, or the actor has no cell position. Used by both simulators' `_choose_tag_target` methods.
- `_get_base_interaction(player, movement_ctx)` — returns the `base_id` of the first uncaptured base the player is in range of (`15`=neutral, `14`=red player at blue base, `13`=blue player at red base), or `None`. Checks neutral bases first (priority order `neutral_1`…`neutral_4`), then the opposing base. Skips bases already captured by that player. Returns `None` when no map is active, player has no cell position, or `base_sight_data` is absent. Used by both simulators' `_plan_action` methods to gate `capture_base` actions.

Cell-aware movement (MAP-02, active when `movement_ctx is not None` and `player.cell_row is not None`):
- `_choose_goal_cell(player, all_alive, movement_ctx)` — delegates to `pathfinding.choose_goal_cell`; default goal is the enemy base cell; overridden by medic's or ammo's cell when resources are critical.
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
- `test_map.py` — map-related tests: adjacency building, A* pathfinding, movement events, cell-aware movement, batch-sim with map (`TestMap02CellMovement`); LOS target filtering and wall-blocking acceptance tests (`TestMap03LOSTargeting`, `TestMap03DBIntegration`); base-sight gate unit tests and DB integration (`TestMap04BaseInteraction`, `TestMap04DBIntegration`)
- `test_roster.py` — team/player roster validation
- `test_mvp.py` — MVP scoring formulas
- `test_weights.py` — weight function unit tests (`TestWeightFunctions`)
- `views_tests.py` — view behaviour: URL routing, form submissions, context keys
- `test_serializers.py` — unit tests for all five serializer classes (including list vs detail split)
- `test_apis.py` — HTTP-level tests for `/api/matches/` and `/api/rounds/` (including `/events/` action)
- `conftest.py` — shared `make_team_with_slots(prefix)` helper

## Sub-packages

- [`sim_helpers/CLAUDE.md`](sim_helpers/CLAUDE.md) — `BatchSimulator` helper modules (`PlayerState`, action weights, pathfinding)
- [`management/commands/CLAUDE.md`](management/commands/CLAUDE.md) — `score_averages` and `game_analysis` management commands