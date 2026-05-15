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
| `last_chosen_action` | Action chosen on the previous tick (`"tag_player"`, `"hide"`, etc.); read by `choose_goal_cell` to make movement action-aware (MAP-05) |

### Uptime breakdown fields

Accumulated each tick by the simulation loop (not stored in the DB):

- `seconds_active` — player is alive and fully active
- `seconds_reset_window` — 4–7 s after a life loss (taggable but not "active")
- `seconds_not_targetable` — 0–3 s after a life loss (in transit, untargetable)

Dead time (after elimination) is derived at report time as `900 - was_eliminated_at`.

### Aggregate stat fields

`points_scored`, `tags_made`, `times_tagged`, `shots_missed`, `times_missiled`, `resupplies_given`, `specials_used`, `times_tagged_in_reset_window`, `missile_points`, `follow_up_shots`, `reaction_shots`, `combo_resupply_count` (number of times this player received a combo resupply — both lives and shots in the same tick; default 0).

### MECH-06 transient fields (no DB columns)

| Field | Type | Purpose |
|-------|------|---------|
| `player_memory` | `dict[str, dict]` | `{tag_id: {"cell": (r,c), "timestamp": s, "role": role}}` — last-known cell per player from LOS observations and broadcasts |
| `medic_hit_times` | `list[float]` | Timestamps of the two most recent hits received (for medic-under-fire alert — 2 hits within 12 s) |
| `score_broadcast_state` | `str \| None` | Outcome of the last score broadcast: `"losing"`, `"hide"`, `"seek_medic"`, or `None` |
| `score_broadcast_next` | `float` | Simulation second at which the next score broadcast fires (initialised to 180.0) |

### Role stat lookups

`_ROLE_STATS`, `_MAX_LIVES`, `_MAX_SHOTS`, `_SPECIAL_COST` are imported from `matches.sim_helpers.role_constants` (with `_`-prefixed aliases to preserve existing callsites). `role_constants` has no Django imports so the zero-dependency guarantee is maintained.

### Duck-type interface helpers

`tag_id_key` — `@property` returning `self.tag_id` (the string tag identity). Exists so `choose_tag_target` in `mechanics.py` can access this attribute the same way on both `PlayerState` and `PlayerRoundState`.

---

## weights.py

One function per role: `_get_medic_weights`, `_get_ammo_weights`, `_get_scout_weights`, `_get_heavy_weights`, `_get_commander_weights`. Each mutates the `weights` list in-place and returns it.

### Weight array layout

Index 0–7 map to: `tag_player`, `change_zone`, `hide`, `capture_base`, `use_special`, `resupply_ally`, `missile_player`, `request_resupply`.

The caller (`BatchSimulator._plan_action`) passes a baseline of `[70, 30, 0, 0, 0, 0, 0, 0]`. Role functions apply deltas from there. **All weights must remain ≥ 0** — `random.choices` raises `ValueError` on negative weights.

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

### Stat wiring in weights.py

`resupply_efficiency` scales the `request_resupply` weight (index 7) for all roles — the weight is only non-zero when the player needs resources (has room to receive lives or shots). `resupply_synergy` scales the `resupply_ally` weight (index 5) for Medic and Ammo players — higher synergy pushes support players toward fulfilling requests. Both stats are fully wired as of MECH-01; the former TODO/skeleton blocks have been removed.

`teamwork` and `communication` are fully wired as of MECH-06 — former skeleton TODO blocks removed. `teamwork` (>50) applies a bias in goal selection (see pathfinding.py `_apply_teamwork_bias`); `communication` is a per-tick broadcast probability handled in the simulator tick loop, not in `weights.py` directly.

**`_apply_score_broadcast_weights(player, weights)`** (MECH-06) — adjusts the weight vector based on the player's current `score_broadcast_state`: `"losing"` → `tag_player` weight +10; `"hide"` → `hide` weight +20; `"seek_medic"` → movement override handled in `pathfinding.choose_goal_cell` (no weight change here). Called from each role's weight function when `score_broadcast_state` is set.

`_commander_nuke_gate(sp, ga)` gates the Commander `use_special` weight based on the awareness-tier stacking table (MECH-03): ga<30→fire at sp>20; ga<50→fire at sp>40; ga<70→fire at sp>60; always fire at sp>80. When the gate is closed, weight stays 0 and the Commander stacks SP toward the next threshold. The `# MECH-06:` situational-override hook inside `_get_commander_weights` is now populated — MECH-06 memory checks can cause the gate to open early when conditions are favourable.

### Known pre-existing test failure

`test_medic_can_capture_base_prioritises_capture` expects `capture_base == 50` but the medic weight code only adds +5. This predates current work and is not a regression.

---

## pathfinding.py

Cell-aware movement helpers shared by both simulators. Used when `arena_map` is provided; 3-zone fallback is used otherwise.

### Functions

**`build_movement_adjacency(zone_data)`** — builds a 4-connected adjacency dict `{cell: [neighbor, ...]}` for every movement-passable cell. Uses module constant `_MOVEMENT_PASSABLE = {1, 2, 3}` (floor + legacy red/blue zones). High wall (0), low wall (4), and windowed wall (5) all block movement and are excluded entirely, so `cell in adj` doubles as a passability check.

**`astar_next_step(start, goal, adj, elevation_data=None)`** — returns the immediate next cell on the shortest path from `start` to `goal` using A* with a Manhattan heuristic. Returns `start` unchanged when `start == goal`, no path exists, or `start` is not in the adjacency graph.

**`_find_role(all_alive, team_color, role) -> Any`** — returns the first alive player on `team_color` with the given `role`, or `None`. Return type is `Any` (not `object`) because callers access duck-typed attributes (`cell_row`, `cell_col`, etc.).

**`_goal_from_action(player, all_alive, enemy_color, cell_row, cell_col, intended_action, movement_ctx) -> tuple[int,int] | None`** — returns a goal cell driven by the player's previously chosen action, or `None`:
- `tag_player` / `missile_player`: nearest enemy (Commander → enemy medic first).
- `resupply_ally`: Medic → neediest ally by lives ratio; Ammo → neediest ally by shots ratio.
- `hide`: adjacent cell with lowest LOS count.

**`_goal_from_role(player, all_alive, enemy_color, cell_row, cell_col, movement_ctx) -> tuple[int,int] | None`** — returns a role-specific positioning goal, or `None`:
- Scout → nearest high-LOS cell (top 25% by LOS count).
- Heavy (healthy >50% lives and shots) → nearest strong spot; otherwise → nearest allied Medic or Ammo.
- Medic → lowest-LOS cell within the allied Heavy's visible set (sheltered position near Heavy).
- Ammo → highest-LOS cell within the allied Heavy's visible set (exposed support position near Heavy).
- Commander → enemy medic cell.

**`_STALE_THRESHOLD`** — module-level dict mapping role strings to their memory staleness thresholds in seconds: `Heavy/Medic/Ammo → 60`, `Scout/Commander → 15`.

**`_cell_from_memory(player, tag_id, movement_ctx) -> tuple[int,int] | None`** — looks up `tag_id` in `player.player_memory`; returns the stored cell if the entry is fresh (within the role's staleness threshold), `None` if stale or absent. Stale slow-role entries (Heavy/Medic/Ammo) return the last-known cell anyway; stale fast-role entries return `None` to let callers fall through to role defaults.

**`_known_enemies_from_memory(player, all_alive, movement_ctx) -> list`** — returns all enemy `PlayerState` objects whose last-known cell is fresh enough to use, substituting the memory cell for the player's actual cell in a lightweight proxy so callers don't need to distinguish real vs remembered positions.

**`_apply_teamwork_bias(player, candidates, movement_ctx) -> tuple[int,int] | None`** — when `player.teamwork > 50`, filters `candidates` (high-LOS cells) to those also within LOS of ≥1 alive ally; returns the nearest qualifying cell, or `None` when no ally-visible high-LOS cell exists (caller falls through to unbiased selection).

**`_goal_from_action(player, all_alive, enemy_color, cell_row, cell_col, intended_action, movement_ctx) -> tuple[int,int] | None`** — unchanged signature; now uses `_known_enemies_from_memory` instead of direct `all_alive` iteration when selecting a tag/missile target so goal selection uses memory rather than perfect knowledge.

**`_goal_from_role(player, all_alive, enemy_color, cell_row, cell_col, movement_ctx) -> tuple[int,int] | None`** — unchanged signature; internally calls `_apply_teamwork_bias` after identifying role-specific candidate cells (Scout, Heavy-healthy paths) before returning.

**`choose_goal_cell(player, all_alive, spawn_cells, movement_ctx=None, intended_action="")`** — duck-typed goal selector shared by both simulators (MAP-05). Priority order:
1. **MECH-04 nuke-reaction override** (highest priority): when `player.reacting_to_nuke` is `True`, Medic/Ammo rush toward the neediest ally. Non-support players with lives ≤ 30% of max → allied Medic cell (survival mode); lives > 30% → seeks enemy Commander's last-known cell from `player_memory` (MECH-06 fills the former TODO hook) to attempt a tag-cancel; falls through to step 2 if memory is absent/stale.
2. **Score-broadcast seek-medic override**: when `player.score_broadcast_state == "seek_medic"`, movement is overridden to the allied Medic's last-known cell from memory.
3. Critical-resource override (non-support): lives ≤ 30% → seek allied Medic; shots ≤ 30% → seek allied Ammo.
4. Action-driven movement via `_goal_from_action` (uses `intended_action`, which is the action chosen on the previous tick).
5. Role-specific positioning via `_goal_from_role` (includes teamwork bias via `_apply_teamwork_bias`).
6. Default: enemy base cell from `spawn_cells`.

### Elevation model (stub)

**`_elevation_at(r, c, elevation_data=None)`** — returns 0 for all cells until MAP-09 populates real elevation data.

**`_movement_cost(from_cell, to_cell, elevation_data=None)`** — uphill costs 1.5×, flat/downhill costs 1.0. Wired into the A* edge cost.

### Tests

`matches/tests/test_map.py::TestMap02CellMovement` covers adjacency building, A* correctness, elevation stubs, movement cost, goal-cell selection, and the batch-simulator code path.

---

## mechanics.py

Pure game-mechanic functions shared by both simulators. No Django imports. Both player types satisfy the duck-typed interface.

**`shot_cooldown(player, second) -> float`** — returns the minimum gap between shots: 0.0 for rapid-fire scouts (special active), 1.0 for heavies, 0.5 for everyone else.

**`choose_tag_target(player, all_alive, second, movement_ctx=None, *, los_filter=None) -> player | None`** — returns a random weighted enemy target. `los_filter` is a callable `(actor, candidates, movement_ctx) -> list`; falls back to same-zone filtering when not provided. Role weights: Heavy=8, Commander=5, Ammo=Scout=3, Medic=1.

**`choose_resupply_target(player, all_alive, second) -> player | None`** — returns the neediest same-zone teammate to resupply weighted by resource deficit × role. Returns `None` when all teammates are at full resources.

**`choose_zone_change(player, all_alive) -> int | None`** — returns a target zone index when the player is critically low (≤ 30%) on lives (seek Medic) or shots (seek Ammo). Returns `None` when no reactive movement is warranted.

### Tests

`matches/tests/test_mechanics.py` covers all four public functions.

---

## combat.py

Shared combat resolution used by both simulators. No Django imports — operates on duck-typed player state objects and emits events through an optional `emit_event` callable rather than writing to a specific storage backend.

### Visibility helpers (moved from `simulation.py`)

**`_can_tag_through_windowed_wall(r1, c1, r2, c2, zone_grid, wall_meta) -> bool`** — Bresenham line walk. High wall (0) → always False. Windowed wall (5): checks facing vs attack axis.

**`_get_los_targets(actor, candidates, movement_ctx) -> list`** — Returns candidates visible to actor. Uses `sight_data` frozenset lookup, extended by windowed-wall aperture check. Falls back to same-zone when no map is active.

**`_get_base_interaction(player, movement_ctx) -> int | None`** — Returns `base_id` (15=neutral, 14/13=opposing) of the first capturable base in range, or `None`.

**`elevation_hit_modifier(attacker_elev, target_elev) -> float`** — public pure formula: `max(0.5, 1 - 0.1 * max(0, target_elev - attacker_elev))`. Importable for testing.

**`_elevation_hit_modifier(attacker_row, attacker_col, defender_row, defender_col, movement_ctx) -> float`** — MAP-09 wrapper; returns 1.0 when no map or either cell is None.

### Action index constants

`_ACTION_IDX` and `_CHOICES` define the 8-slot action array (indices 0–7): `tag_player`, `change_zone`, `hide`, `capture_base`, `use_special`, `resupply_ally`, `missile_player`, `request_resupply`. `request_resupply` (index 7) is available to all 5 roles; weight is non-zero only when the player needs resources (Ammo players are locked to requesting lives; Medic players to requesting shots). Fulfilled asynchronously by `resolve_resupply_requests` in `resupply_queue.py` at end of tick.

### Combat actions

**`plan_action(player, all_alive, second, movement_ctx=None, *, save_player=None) -> list`** — Returns a list of planned action dicts for the player at this tick. Updates `player.last_chosen_action`; clears `is_hiding` (calling `save_player(player)` when provided). Used by both simulators' per-tick loop.

**`attempt_resupply(tagger, teammate, second, *, emit_event=None) -> None`** — Applies a resupply: Ammo restores shots, Medic restores lives (per `_AMMO_CHART`/`_MEDIC_CHART`). Cancels any active special on the teammate. Nuke-cancel stat tracking is the caller's responsibility.

**`capture_base(player, base_id, second, movement_ctx=None, *, emit_event=None) -> bool`** — Range-checks the player's cell against `base_sight_data`, deducts 3 shots, awards 1001 pts, and updates `neutral_base_destroyed` / `opposing_base_destroyed`. Returns `True` on success.

**`award_bases(player, second, *, emit_event=None) -> None`** — Awards any uncaptured bases to a surviving player at round end.

**`start_missile_lock(attacker, defender, second, *, emit_event=None) -> PendingMissile | None`** — Rolls dodge (45% chance); returns a `PendingMissile(complete_time, attacker, defender)` on success, `None` on dodge or invalid state.

---

## role_constants.py

Pure Python, no imports. Single source of truth for all role-level constants.

| Constant | Type | Purpose |
|----------|------|---------|
| `ROLE_STATS` | `dict[str, dict[str, int]]` | `shot_power` and `shield` per role |
| `MAX_LIVES` | `dict[str, int]` | Maximum life count per role |
| `MAX_SHOTS` | `dict[str, int]` | Maximum shot count per role |
| `SPECIAL_COST` | `dict[str, int]` | Special-charge cost to fire a nuke/power-boost per role |

Imported by `teams/models.py`, `matches/models.py`, and `matches/sim_helpers/player_state.py`. Changing a role's stats here propagates everywhere automatically.

---

## score_calculator.py

**`calculate_mvp(player_state) -> float`** — SM5 MVP formula extracted from `PlayerRoundState.get_mvp`. Accepts any duck-typed object exposing the standard `PlayerRoundState` attributes (works with both ORM instances and `PlayerState` dataclasses). `PlayerRoundState.get_mvp` now delegates here. Test with `matches/tests/test_mvp.py::TestCalculateMvp` — no Django ORM or test DB required for pure formula tests.

---

## map_context.py

`MapContext` is a typed `@dataclass` that replaces the former 11-key `movement_ctx` plain dict. It is constructed once per round by `ResourceBasedSimulator._build_movement_ctx` (or the unified `_load_map_context`) and passed through the simulation call chain. All callers access it via domain-level methods rather than dict key lookups.

### Fields (mirror the old dict keys)

`adj`, `spawn_cells`, `zone_data`, `sight_data`, `base_sight_data`, `cell_los_counts`, `high_los_cells`, `strong_spots`, `wall_meta`, `team_spawn_pools`, `elevation_grid`.

### Domain-level accessors

- `can_see(from_cell, to_cell) -> bool` — frozenset lookup in `sight_data`.
- `elevation_at(r, c) -> float` — safe `elevation_grid` access, returns 0.0 on None/OOB.
- `base_in_range(cell) -> int | None` — checks `base_sight_data`; returns 15/14/None.
- `get_adjacency()`, `get_spawn_cells()`, `get_zone_data()`, `get_wall_meta()`, `get_los_count(cell)`, `get_high_los_cells()`, `get_strong_spots()`, `get_team_spawn_pools()`.

### Backward-compat bridges

- `MapContext.from_dict(d)` — construct from the legacy 11-key dict (used in tests).
- `to_dict()` — serialize back to dict format.
- `.get(key, default)`, `.__getitem__(key)`, `.__contains__(key)` — dict-style shims so old `movement_ctx.get("sight_data")` call sites still work without migration.

When `arena_map` is `None` (3-zone fallback), `movement_ctx` remains `None` — `MapContext` is only constructed when a map is active.

---

## pending_events.py

Typed `@dataclass` classes for the four pending-event queues used by both simulators. Replacing raw positional tuples with named fields so new attributes (e.g. a nuke ID for MECH-05 cancellation tracking) can be added in one place.

| Class | Fields | Replaces |
|-------|--------|---------|
| `PendingMissile` | `complete_time`, `attacker`, `defender` | `(float, player, player)` |
| `PendingNuke` | `complete_time`, `player` | `(float, player)` |
| `PendingFollowup` | `fire_at`, `attacker`, `defender`, `chain_depth` | `(float, player, player, int)` |
| `PendingReaction` | `fire_at`, `attacker`, `defender` | `(float, player, player)` |

`combat.py::start_missile_lock` returns a `PendingMissile` (was a raw 3-tuple).

---

## tick_engine.py

Shared drain/split helpers for the four pending-event queues. Both simulators call these at the start of each tick instead of duplicating the filter pattern inline.

- `drain_missiles(pending, second) -> (ready, still)` — splits by `PendingMissile.complete_time`.
- `drain_nukes(pending, second) -> (ready, still)` — splits by `PendingNuke.complete_time`.
- `drain_reactions(pending, second) -> (ready, still)` — splits by `PendingReaction.fire_at`.
- `drain_followups(pending, second) -> (ready, still)` — splits by `PendingFollowup.fire_at`.

All return `(ready_now, still_pending)` typed lists. Resolution logic (what to do with ready items) stays in each simulator.

---

## spawn_assigner.py

Spawn cell assignment logic shared by `ResourceBasedSimulator._initialize_players` and `BatchSimulator._make_players`. Extracted from `_build_spawn_assignments` so the implementation lives in one place.

**`assign_spawn_cells(roster_roles, team_color, spawn_cells, team_spawn_pools) -> dict[int, tuple[int,int] | None]`** — role-priority, no-replacement drawing from the team's spawn pool. Returns `{roster_index: (row, col) | None}`. `None` means fall back to 3-zone placement.

Role priority:
1. Commander / Heavy → front of pool (closest to enemy base)
2. Medic / Ammo → back of pool (farthest from enemy base)
3. Scout → remaining cells

Private helpers `_draw_front`, `_draw_back`, `_overflow` replace the inner closures that previously captured outer-scope state.

`ResourceBasedSimulator._build_spawn_assignments` is now a one-line delegation shim that calls `assign_spawn_cells`.

Tests: `matches/tests/test_spawn_assigner.py` — 15 unit tests, no DB required.

---

## resupply_queue.py

End-of-tick resupply fulfillment. Called by both simulators after all players have chosen their action for the tick. No Django imports — operates on duck-typed player state objects.

### Public function

**`resolve_resupply_requests(requestors, all_alive, second, movement_ctx, *, emit_event=None) -> None`** — Processes all players whose `last_chosen_action == "request_resupply"` for the current tick. Mutates player state in-place; emits `GameEvent`-compatible dicts via the optional `emit_event` callable.

Parameters:
- `requestors` — iterable of players whose action this tick was `request_resupply`.
- `all_alive` — all currently alive players (both teams); used to find candidate supporters.
- `second` — current simulation timestamp; used for cooldown checks and event timestamps.
- `movement_ctx` — `MapContext | None`; LOS checks use `movement_ctx.can_see` when a map is active, fall back to same-zone when `None`.
- `emit_event` — optional callable `(event_dict) -> None`; when provided, a `GameEvent`-compatible dict is emitted for every resupply resolved.

### Private helpers

**`_priority_param(player) -> int`** — returns a numeric priority score for a requestor based on role: Heavy=4, Commander=3, Scout=2, Ammo=1, Medic=0. Used to build the priority queue.

**`_queue_priority(player) -> tuple`** — returns a sort key `(-_priority_param(player), player.tag_id)` for stable ordering in the queue.

### Fulfillment rules (same-tick)

A support player (Medic or Ammo) can fulfill a request in the current tick only when all of the following hold:
1. The supporter is alive and not currently deactivated (not in the reset window or respawning).
2. The supporter is in LOS of the requestor (via `movement_ctx.can_see` or same-zone fallback).
3. The supporter has `final_shots > 0` (has resources to give).
4. The supporter is not on a resupply cooldown for this tick.

### Stress failure formula

When a supporter has already fulfilled at least one request this tick (`prior_count ≥ 1`), each additional request has a chance of failing:

```
failure_pct = min(100, (dm + teamwork) / 10 × prior_count)
```

where `dm` and `teamwork` are the supporter's stats. A `random.random() * 100 < failure_pct` check determines failure. On failure the requestor receives nothing this tick.

### Combo resupply

A combo resupply occurs when both an Ammo and a Medic are available for the same requestor in the same tick. The chance of a combo (rather than fulfilling each independently) is:

```
combo_chance = min(0.95, 0.20 + ammo_syn/100 × medic_syn/100 + ammo_eff/100 × medic_eff/100)
```

where `ammo_syn`/`medic_syn` are the respective `resupply_synergy` stats and `ammo_eff`/`medic_eff` are the `resupply_efficiency` stats of the two supporters. When the combo fires:
- Both supporters fulfill the request simultaneously; the requestor receives lives and shots.
- `player.combo_resupply_count` is incremented on the requestor.
- A `GameEvent(event_type="combo_resupply", metadata={"medic_tag": ..., "ammo_tag": ...})` is emitted.

When the combo roll fails, a fallback gives a 75% chance of fulfillment by the priority-ranked supporter and a 25% chance by the other. Standard `resupply_lives`/`resupply_ammo` events are emitted as normal.
