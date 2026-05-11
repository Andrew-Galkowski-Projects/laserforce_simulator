# matches/sim_helpers

Helper modules used by both `ResourceBasedSimulator` and `BatchSimulator` in `matches/simulation.py`. No Django ORM or DB access — everything is pure Python.

## player_state.py

`PlayerState` is an in-memory dataclass that mirrors the `PlayerRoundState` ORM model. `BatchSimulator` uses it instead of DB objects so rounds run in ~25 ms rather than ~9 s.

### Key fields

| Field | Purpose |
|-------|---------|
| `tag_id` | Unique string per player per round (`"red_commander"`, `"blue_scout_1"`) |
| `final_lives / final_shots / final_special / final_missiles` | Current resource levels (decremented during simulation) |
| `shields` | Current shield count; hits decrement this; reaching 0 costs a life and resets to `max_shields` |
| `last_downed_time` | Second at which the player last lost a life; drives the 8-second respawn cooldown |
| `was_eliminated_at` | Second of final elimination; 901 means survived the round |
| `special_active_until` | Second until which the scout's rapid-fire (or commander's shield) special is active |
| `last_shot_time` | Transient; set every time the player fires; used by `_shot_cooldown` to enforce shot-speed limits |

### Uptime breakdown fields

Accumulated each tick by the simulation loop (not stored in the DB):

- `seconds_active` — player is alive and fully active
- `seconds_reset_window` — 4–7 s after a life loss (taggable but not "active")
- `seconds_not_targetable` — 0–3 s after a life loss (in transit, untargetable)

Dead time (after elimination) is derived at report time as `900 - was_eliminated_at`.

### Aggregate stat fields

`points_scored`, `tags_made`, `times_tagged`, `shots_missed`, `times_missiled`, `resupplies_given`, `specials_used`, `times_tagged_in_reset_window`, `missile_points`, `follow_up_shots`, `reaction_shots`.

### Role stat lookups

`_ROLE_STATS`, `_MAX_LIVES`, `_MAX_SHOTS`, `_SPECIAL_COST` are local dicts that mirror `teams.models.ROLE_STATS`. They are kept here so `PlayerState` has zero Django imports.

---

## weights.py

One function per role: `_get_medic_weights`, `_get_ammo_weights`, `_get_scout_weights`, `_get_heavy_weights`, `_get_commander_weights`. Each mutates the `weights` list in-place and returns it.

### Weight array layout

Index 0–6 map to: `tag_player`, `change_zone`, `hide`, `capture_base`, `use_special`, `resupply_ally`, `missile_player`.

The caller (`BatchSimulator._plan_action`) passes a baseline of `[70, 30, 0, 0, 0, 0, 0]`. Role functions apply deltas from there. **All weights must remain ≥ 0** — `random.choices` raises `ValueError` on negative weights.

### Critical weight-safety rules

- Before subtracting from a weight, check that the result can't go below zero given the baseline.
- The not-active blocks zero out `tag_player` and/or `resupply_ally` and redistribute to `hide`/`change_zone`. They must not push any other weight negative.
- Tests in `matches/tests/test_weights.py::TestWeightFunctions` cover representative state combinations for each role. Run these whenever changing weights.

### Role baselines (after role-adjustment, before situational modifiers)

| Role | tag_player | change_zone | resupply_ally |
|------|-----------|-------------|---------------|
| Medic | 10 | 0 | 90 |
| Ammo | 35 | 0 | 95 |
| Scout | 50 | 50 | 0 |
| Heavy | 70 | 25 | 0 |
| Commander | 70 | 30 | 0 |

### Known pre-existing test failure

`test_medic_can_capture_base_prioritises_capture` expects `capture_base == 50` but the medic weight code only adds +5. This predates current work and is not a regression.

---

## pathfinding.py

Cell-aware movement helpers shared by both simulators. Used when `arena_map` is provided; 3-zone fallback is used otherwise.

### Functions

**`build_movement_adjacency(zone_data)`** — builds a 4-connected adjacency dict `{cell: [neighbor, ...]}` for every non-wall cell (value ≠ 0). Wall cells are excluded entirely so `cell in adj` doubles as a passability check.

**`astar_next_step(start, goal, adj, elevation_data=None)`** — returns the immediate next cell on the shortest path from `start` to `goal` using A* with a Manhattan heuristic. Returns `start` unchanged when `start == goal`, no path exists, or `start` is not in the adjacency graph.

**`choose_goal_cell(player, all_alive, spawn_cells)`** — duck-typed goal selector shared by both simulators. Default goal: enemy base cell from `spawn_cells`. Overrides: allied medic's cell when lives ≤ 30% of max (non-medic only); allied ammo's cell when shots ≤ 30% of max.

### Elevation model (stub)

**`_elevation_at(r, c, elevation_data=None)`** — returns 0 for all cells until MAP-09 populates real elevation data.

**`_movement_cost(from_cell, to_cell, elevation_data=None)`** — uphill costs 1.5×, flat/downhill costs 1.0. Wired into the A* edge cost.

### Tests

`matches/tests/test_map.py::TestMap02CellMovement` covers adjacency building, A* correctness, elevation stubs, movement cost, goal-cell selection, and the batch-simulator code path.
