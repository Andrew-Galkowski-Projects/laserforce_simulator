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
| `last_downed_time` | Tick at which the player last lost a life; drives the `RESPAWN_TICKS=16` respawn cooldown |
| `was_eliminated_at` | Tick of final elimination; `1801` (`SURVIVED_SENTINEL`) means survived the round (was `901` pre-TIME-01) |
| `special_active_until` | Tick until which the scout's rapid-fire (or commander's shield) special is active |
| `last_shot_time` | Transient; set every time the player fires; used by `_shot_cooldown` to enforce shot-speed limits |
| `last_chosen_action` | Action chosen on the previous tick (`"tag_player"`, `"hide"`, etc.); read by `choose_goal_cell` to make movement action-aware (MAP-05) |
| `movement_trail` | **MOVE-01** transient list of compact `(start_cell, end_cell, timestamp)` Advance steps, appended by `_move_player_in_memory` only when the cell actually changed; **no DB column / no migration** — flushed to compact `GameEvent(event_type="movement")` rows by `_flush_to_db` only when a round is saved. Reconstructs the player's **Movement trail** (CONTEXT.md); the exact intermediate route is recomputed at replay via deterministic A* `start → end` |
| `_committed_goal` | **MOVE-04** transient steady-state Goal cell commitment (**`BatchSimulator` only** — [ADR-0010](../../docs/adr/0010-goal-commitment-via-tick-cadence-throttling.md), CONTEXT.md **Goal commitment**). `Optional[tuple[tuple[int,int], bool, int]]`, **default `None`** — either `None` or a `(cell, from_action_driven, expires_at_tick)` 3-tuple: `cell` is the committed Goal cell, `from_action_driven` is `True` when the cell came from `_goal_from_action` (tag/missile/resupply/hide target — clear on Down) and `False` when it came from `_goal_from_role` / enemy-base default / `only_move`-driven (positioning intent — survives Down because the player keeps **Advancing** through the **Respawn cooldown**), `expires_at_tick` is `tick + GOAL_RECOMPUTE_PERIOD_TICKS` set per-player at recompute time (expiry-based phase staggers naturally — **not** a global `tick % N == 0`). **No DB column / no migration** (mirrors `_path_cache` / `movement_trail`); default `None` so it is never a required ctor arg and never crosses the parallel-worker process boundary. A fresh per-round `PlayerState` starts uncommitted. Only the steady-state positioning layer of `choose_goal_cell` (steps 2/3/4 — `_goal_from_action`, `_goal_from_role`, enemy-base default) reads/writes this slot; the reactive overrides (step 0 nuke-reaction, step 1 critical-resource lives/shots ≤ 30%, step 1b score-broadcast `seek_medic`) bypass it and continue to fire every tick. **Force-recompute triggers** beyond cadence expiry: no prior commitment, Goal cell reached, exiting **Stationary** (hide → not / hold → not), a reactive override firing this tick, **Down**/respawn iff `from_action_driven`. Parallels `_path_cache` (separate slot, separate invalidation policy: the route cache invalidates **iff a Goal commitment recompute changes the Goal cell** — re-picking the same cell leaves `_path_cache` untouched). RBS never sets it |
| `_path_cache` | **MOVE-02** transient goal-keyed A* route cache (**`BatchSimulator` only** — [ADR-0008](../../docs/adr/0008-path-commitment-via-goal-keyed-cache.md), CONTEXT.md **Path commitment**). `Optional[tuple]`, **default `None`** — either `None` or a `(cached_goal, remaining_cells, anchor)` 3-tuple: `remaining_cells` is the route still to walk, `anchor` is the cell the previous re-step left the player on (used to detect off-route displacement; a legacy 2-tuple with no anchor is tolerated for hand-built test caches). **No DB column / no migration** (mirrors `movement_trail`); default `None` so it is never a required ctor arg and never crosses the parallel-worker process boundary. A fresh per-round `PlayerState` starts uncached (effectively "cleared at round start"); cleared back to `None` at every BatchSim tag / follow-up / reaction / missile / nuke life-loss site via the shared `BatchSimulator._record_down(player, second)` helper (knocked off-path → recompute). Managed by `pathfinding.astar_advance_cached`; RBS never sets it |
| `is_holding` | **MOVE-03** transient bool, **default `False`** (mirrors `is_hiding`; **no DB column / no migration**). Set `True` on a `hold` Action roll — the player is in **Overwatch** (CONTEXT.md) and **Stationary** (joins `is_hiding` / `capture_base` in the `_advance_player` predicate, no **Advance**). **Carries over**: stays `True` until a non-`hold` Action is rolled, or a Down/respawn force-clears it via the shared `BatchSimulator._record_down(player, second)` helper (same hook that drops `_path_cache`, so every life-loss site is covered structurally). Shared by both simulators' Action selection, but only `BatchSimulator` resolves the Overwatch shot (RBS treats `hold` as a Stationary no-op). [ADR-0009](../../docs/adr/0009-hold-overwatch.md) |
| `_last_step_cells` | **MOVE-03** transient `list[tuple[int,int]]` (default empty), **no DB column / no migration**. Populated each move tick by `pathfinding.astar_advance_cached` with the committed-route cells it popped this tick (the player's traversed cells this Advance); consumed by the `BatchSimulator` Overwatch resolution step to test whether a mover's traversal crossed any **Hold**ing player's **Line of sight** (the "moved *through* LoS in one Advance" guarantee — the exact intermediate route is otherwise discarded by MOVE-01). Read-only signal; consuming it uses **no RNG**. `BatchSimulator` only — RBS's `astar_advance` never sets it. [ADR-0009](../../docs/adr/0009-hold-overwatch.md) / [ADR-0008](../../docs/adr/0008-path-commitment-via-goal-keyed-cache.md) |

### Uptime breakdown fields

Accumulated each tick by the simulation loop (not stored in the DB):

- `ticks_active` — player is alive and fully active
- `ticks_reset_window` — taggable-but-not-"active" portion of the respawn cooldown after a life loss
- `ticks_not_targetable` — untargetable (in-transit) portion immediately after a life loss

(TIME-01 rename from `seconds_*`; values are now ticks.) Dead time (after elimination) is derived at report time as `1800 - was_eliminated_at`. The four together (`ticks_active + ticks_reset_window + ticks_not_targetable + dead-time`) reconcile to exactly 1800 ticks per player.

### Player stat fields

Baked from `Player.stat_for_simulation(<stat>, role)` at construction (no per-tick ORM): `accuracy`, `survival`, `player_awareness`, `game_awareness`, `resource_awareness`, `decision_making`, `stamina`, `special_usage`, `resupply_efficiency`, `resupply_synergy`, `teamwork`, `communication`, **`speed`** (default 50; cells traversed per move tick via `pathfinding.cells_to_move` — STAT-03 Phase 1). The full set is enumerated in `simulation._SIMULATION_STATS`, which the parallel `_precompute_roster` path must keep in sync with every stat `_make_players` reads (a missing entry → worker `KeyError` only under `--workers > 1`; regression: `test_batch_sim.py::TestPrecomputeRosterParity`).

### Aggregate stat fields

`points_scored`, `tags_made`, `times_tagged`, `shots_missed`, `times_missiled`, `resupplies_given`, `specials_used`, `times_tagged_in_reset_window`, `missile_points`, `follow_up_shots`, `reaction_shots`, `combo_resupply_count` (number of times this player received a combo resupply — both lives and shots in the same tick; default 0).

### MECH-06 transient fields (no DB columns)

| Field | Type | Purpose |
|-------|------|---------|
| `player_memory` | `dict[str, dict]` | `{tag_id: {"cell": (r,c), "timestamp": s, "role": role}}` — last-known cell per player from LOS observations and broadcasts |
| `medic_hit_times` | `list[float]` | Tick timestamps of the two most recent hits received (medic-under-fire alert — 2 hits within `MEDIC_ALERT_WINDOW_TICKS`, 12 s) |
| `score_broadcast_state` | `str \| None` | Outcome of the last score broadcast: `"losing"`, `"hide"`, `"seek_medic"`, or `None` |
| `score_broadcast_next` | `float` | Simulation tick at which the next score broadcast fires (first at `SCORE_BROADCAST_PERIOD_TICKS = 360`, i.e. 180 s) |

### Role stat lookups

`_ROLE_STATS`, `_MAX_LIVES`, `_MAX_SHOTS`, `_SPECIAL_COST` are imported from `matches.sim_helpers.role_constants` (with `_`-prefixed aliases to preserve existing callsites). `role_constants` has no Django imports so the zero-dependency guarantee is maintained.

### Duck-type interface helpers

`tag_id_key` — `@property` returning `self.tag_id` (the string tag identity). Exists so `choose_tag_target` in `mechanics.py` can access this attribute the same way on both `PlayerState` and `PlayerRoundState`.

---

## weights.py

One function per role: `_get_medic_weights`, `_get_ammo_weights`, `_get_scout_weights`, `_get_heavy_weights`, `_get_commander_weights`. Each mutates the `weights` list in-place and returns it.

### Weight array layout

Index 0–8 map to: `tag_player`, **`only_move`**, `hide`, `capture_base`, `use_special`, `resupply_ally`, `missile_player`, `request_resupply`, **`hold`**. **MOVE-01:** index 1 was renamed `change_zone` → **`only_move`** (same slot, per-role weight tuning preserved). It no longer gates movement — every non-**Stationary** player **Advances** every tick regardless of the chosen Action; the `only_move` Action merely *doubles* that tick's Advance. See [ADR-0007](../../docs/adr/0007-movement-decoupled-from-action.md) and CONTEXT.md. **MOVE-03:** index 8 **`hold`** is the new 9th slot — a `hold` roll puts the player in **Overwatch** (CONTEXT.md). The slot is shared by **both** simulators' Action selection, but Overwatch *resolution* is `BatchSimulator`-only (RBS no-op). See [ADR-0009](../../docs/adr/0009-hold-overwatch.md) and CONTEXT.md (**Hold**, **Overwatch**, **Overwatch shot**).

The caller (`BatchSimulator._plan_action`) passes a baseline of `[70, 30, 0, 0, 0, 0, 0, 0, 0]` (**MOVE-03:** widened from 8 to 9 slots — the trailing `0` is index 8 `hold`). Role functions apply deltas from there. **All weights must remain ≥ 0** — `random.choices` raises `ValueError` on negative weights.

### Critical weight-safety rules

- Before subtracting from a weight, check that the result can't go below zero given the baseline.
- The not-active blocks zero out `tag_player` and/or `resupply_ally` and redistribute to `hide`/`only_move` (index 1, formerly `change_zone`). They must not push any other weight negative.
- Tests in `matches/tests/test_weights.py::TestWeightFunctions` cover representative state combinations for each role. Run these whenever changing weights.

### Role baselines (after role-adjustment, before situational modifiers)

| Role | tag_player | only_move | resupply_ally | hold (idx 8) |
|------|-----------|-----------|---------------|--------------|
| Medic | 10 | 0 | 90 | 0 |
| Ammo | 35 | 0 | 95 | 20 |
| Scout | 50 | 50 | 0 | 10 |
| Heavy | 70 | 25 | 0 | 20 |
| Commander | 70 | 30 | 0 | 10 |

**MOVE-01:** the `only_move` (index 1, formerly `change_zone`) column is preserved unchanged — a `0` baseline (Medic/Ammo/Commander-at-baseline) no longer means "never moves". Movement is decoupled from this weight: every non-**Stationary** player **Advances** toward their **Goal cell** every tick; `only_move` now only *doubles* that tick's Advance. The baseline `0` roles still traverse the map.

**MOVE-03:** the `hold` (index 8) column is the new **Overwatch** weight ([ADR-0009](../../docs/adr/0009-hold-overwatch.md), CONTEXT.md). Sources of the redistributed weight: **Medic 0** (no source — Medic stays support-focused, never holds at baseline); **Ammo +20 from `tag_player`** (35→15 effective, 20 to hold); **Scout +10**, **Heavy +20**, **Commander +10** each taken **from `only_move`**. All weights stay **≥ 0** (the `random.choices` non-negative invariant — Medic/Ammo `only_move` is `0` so their hold weight is sourced from `tag_player` instead). Numbers are tunable; calibration is deferred (folds into the single pending post-MOVE-01 re-baseline). The slot is shared by both simulators; Overwatch resolution is `BatchSimulator`-only.

### Stat wiring in weights.py

`resupply_efficiency` scales the `request_resupply` weight (index 7) for all roles — the weight is only non-zero when the player needs resources (has room to receive lives or shots). `resupply_synergy` scales the `resupply_ally` weight (index 5) for Medic and Ammo players — higher synergy pushes support players toward fulfilling requests. Both stats are fully wired as of MECH-01; the former TODO/skeleton blocks have been removed.

`teamwork` and `communication` are fully wired as of MECH-06 — former skeleton TODO blocks removed. `teamwork` (>50) applies a bias in goal selection (see pathfinding.py `_apply_teamwork_bias`); `communication` is a per-tick broadcast probability handled in the simulator tick loop, not in `weights.py` directly.

**`_apply_score_broadcast_weights(player, weights)`** (MECH-06) — adjusts the weight vector based on the player's current `score_broadcast_state`: `"losing"` → `tag_player` weight +10; `"hide"` → `hide` weight +20; `"seek_medic"` → movement override handled in `pathfinding.choose_goal_cell` (no weight change here). Called from each role's weight function when `score_broadcast_state` is set.

**TIME-01:** the endgame-rush trigger (`ENDGAME_RUSH_TICKS`, was `second >= 840`) and the score-broadcast period (`SCORE_BROADCAST_PERIOD_TICKS`) are imported from `time_constants.py` and compared against the tick cursor — no inline second literals remain in `weights.py`.

`_commander_nuke_gate(sp, ga)` gates the Commander `use_special` weight based on the awareness-tier stacking table (MECH-03): ga<30→fire at sp>20; ga<50→fire at sp>40; ga<70→fire at sp>60; always fire at sp>80. When the gate is closed, weight stays 0 and the Commander stacks SP toward the next threshold. The `# MECH-06:` situational-override hook inside `_get_commander_weights` is now populated — MECH-06 memory checks can cause the gate to open early when conditions are favourable.

### Known pre-existing test failure

`test_medic_can_capture_base_prioritises_capture` expects `capture_base == 50` but the medic weight code only adds +5. This predates current work and is not a regression.

---

## pathfinding.py

Cell-aware movement helpers shared by both simulators. Used when `arena_map` is provided; 3-zone fallback is used otherwise.

**MOVE-04 — Goal commitment ([ADR-0010](../../docs/adr/0010-goal-commitment-via-tick-cadence-throttling.md), CONTEXT.md), `BatchSimulator` only.** With MOVE-02's route cache in place, the residual per-tick map-mode cost is the goal-selection cascade itself (no A*, but `_goal_from_action` / `_goal_from_role` / teamwork-bias / memory / LOS-count scans run for every non-**Stationary** player every tick). MOVE-04 throttles only the **steady-state positioning** layer of `choose_goal_cell` (cascade steps 2/3/4 — `_goal_from_action`, `_goal_from_role`, enemy-base default) to a per-player `GOAL_RECOMPUTE_PERIOD_TICKS = 4` ticks (2 s) cadence; the **reactive** layer (step 0 MECH-04 nuke-reaction, step 1 critical-resource lives/shots ≤ 30%, step 1b score-broadcast `seek_medic`) **still fires every tick** so time-sensitive overrides are never delayed. The committed cell lives on the transient `PlayerState._committed_goal: Optional[tuple[(int,int), bool, int]] = None` (`(cell, from_action_driven, expires_at_tick)` — see player_state.py table above). **Force-recompute triggers** beyond cadence expiry: no prior commitment, Goal cell reached, exiting **Stationary** (hide/hold → not), a reactive override firing this tick, **Down**/respawn iff `from_action_driven` (positioning goals survive a Down because the player keeps **Advancing** through the **Respawn cooldown**). Phase is **expiry-based** per-player (`expires_at_tick = tick + N`), **not** a global `tick % N == 0` — load staggers naturally across the window without hashing. The route cache (**Path commitment**, MOVE-02) invalidates **iff** a Goal commitment recompute changes the Goal cell — re-picking the same cell leaves `_path_cache` untouched. RBS keeps per-tick goal selection (DB-bound, removed by SIM-09 — same MOVE-02/03 scoping precedent). Cadence + source marker consume **no RNG**, so the SIM-07/SIM-08 *internal* contract holds in form (same seed + Orientation + rosters + map ⇒ identical game, serial == parallel, faithful **Replay**); seeded games **differ from pre-MOVE-04** (staler goals deliberately shift pursuit/positioning) and the delta folds into the already-pending post-MOVE-01 Score Calibration re-baseline (no new obligation). See [ADR-0010](../../docs/adr/0010-goal-commitment-via-tick-cadence-throttling.md), CONTEXT.md (**Goal commitment**, and the superseded "Goal cell is recomputed every tick" ambiguity).

**MOVE-01 — movement decoupled from the weighted Action ([ADR-0007](../../docs/adr/0007-movement-decoupled-from-action.md), CONTEXT.md).** On the map path, every non-**Stationary** player **Advances** toward their **Goal cell** **every tick**, regardless of which Action the weighted roll picked — so `choose_goal_cell` is now consulted every tick (previously only on the `change_zone` roll, which left zero-weight Commander/Medic/Ammo frozen on spawn). **Stationary** = no Advance = `is_hiding` True OR chosen Action == `capture_base`. The renamed **`only_move`** Action (was `change_zone`) no longer gates movement — it devotes the tick entirely to repositioning by **doubling** that tick's Advance (`cells_to_move(speed) * 2` cells in one advance). Advance and A* consume **no RNG** (SIM-07/SIM-08 contract preserved in form).

**MOVE-02 — Path commitment: goal-keyed A* route cache, `BatchSimulator` only ([ADR-0008](../../docs/adr/0008-path-commitment-via-goal-keyed-cache.md), CONTEXT.md).** MOVE-01's per-tick from-scratch A* over the ~3,700-cell graph (~8× slower with a map) is replaced — *in `BatchSimulator` only* — by re-stepping a cached route via `astar_advance_cached` (below). The player follows the single route computed when its **Goal cell** was set; `choose_goal_cell` still runs **every tick** (it does no A* — only the route is cached, not goal selection). Recompute triggers: cache `None`/empty, live goal ≠ cached goal, next cached cell ∉ `adj`; the route is also cleared on Down/respawn (knocked off-path) by the BatchSim caller. The `only_move` 2× multiplier consumes `2×steps` along the **same** committed route — **not** a recompute trigger. `ResourceBasedSimulator` is deliberately **not** cached (DB-bound, removed by SIM-09 — keeps per-tick `astar_advance`). Re-stepping consumes **no RNG** (serial == parallel, faithful Replay still hold), but the cache deliberately changes which equal-cost route is walked, so seeded games **differ from pre-MOVE-02** — the contract is *internal* determinism, **not** identity to pre-MOVE-02 (the PLAN.md "no behavioural change" wording was contradictory, superseded by ADR-0008). The seeded delta is folded into the already-pending post-MOVE-01 Score Calibration re-baseline.

### Functions

**`build_movement_adjacency(zone_data)`** — builds a 4-connected adjacency dict `{cell: [neighbor, ...]}` for every movement-passable cell. Uses module constant `_MOVEMENT_PASSABLE = {1, 2, 3}` (floor + legacy red/blue zones). High wall (0), low wall (4), and windowed wall (5) all block movement and are excluded entirely, so `cell in adj` doubles as a passability check.

**`astar_path(start, goal, adj, elevation_data=None)`** — core A* (Manhattan heuristic, optional elevation cost). Returns the ordered list of cells from `start` to `goal` **excluding `start`** (last element is `goal`); `[]` when `start == goal`, no path exists, or `start` is not in the adjacency graph. `astar_next_step` and `astar_advance` are thin wrappers over it.

**`astar_next_step(start, goal, adj, elevation_data=None)`** — `astar_path(...)[0]` (or `start` when the path is empty). Behaviour unchanged from the pre-refactor implementation (regression-guarded by `test_map.py::TestAstarPathAndAdvance` + the legacy `TestMap02CellMovement` astar tests).

**`astar_advance(start, goal, adj, steps, elevation_data=None)`** — returns the cell reached after walking up to `steps` cells along the A* path (stops at `goal`, no overshoot). Returns `start` when `steps <= 0`, no path, or non-navigable start. **Recomputes A* from `start` every call** (no caching). RBS's `_move_to_cell` calls this (per-tick recompute, kept for its short remaining life). Behaviour **unchanged by MOVE-02** — left intact so RBS and the existing `test_map.py::TestAstarPathAndAdvance` regression tests are untouched.

**`astar_advance_cached(player, current, goal, adj, steps, elevation_data=None)` (MOVE-02, [ADR-0008](../../docs/adr/0008-path-commitment-via-goal-keyed-cache.md))** — the **`BatchSimulator`-only** path-commitment variant. Manages `player._path_cache` (transient `Optional[tuple]` — a `(cached_goal, remaining_cells, anchor)` 3-tuple, where `remaining_cells` is the route still to walk, same shape `astar_path` returns: excludes `current`, ends at `goal`; `anchor` is the cell the previous re-step left the player on). Runs a full `astar_path` recompute **iff** any of: cache `None`/empty, live `goal` ≠ cached goal, live `current` ≠ `anchor` (off-route displacement — *enforces* the off-path invariant rather than relying on it; no mechanic does this today), or `remaining[0] ∉ adj` (next cell blocked — map adjacency is immutable per round so this never fires in production); the BatchSim caller separately clears `_path_cache = None` at every tag / follow-up / reaction / missile / nuke life-loss site via the shared `BatchSimulator._record_down(player, second)` helper (knocked off-path → falls into the "cache None" recompute branch). A legacy 2-tuple cache (no anchor) is tolerated for hand-built test caches (the anchor check is skipped). Otherwise it **re-steps the committed route**: pops up to `steps` cells, stops at `goal` (no overshoot — identical traversal semantics to `astar_advance`), and clears the cache to `None` once the route is consumed (player has reached `goal`; next tick's fresh `choose_goal_cell` drives recompute-or-idle). Cache exhaustion and an `only_move` 2× `steps` are **not** recompute triggers. Consumes **no RNG**, so the SIM-07/SIM-08 serial == parallel / faithful Replay contract still holds (the transient cache never crosses the parallel-worker process boundary; the round is re-simulated in-worker). Called by `BatchSimulator._move_player_in_memory` in place of `astar_advance`. **MOVE-03 ([ADR-0009](../../docs/adr/0009-hold-overwatch.md)):** the cells popped this tick (the player's traversed cells for this Advance, between the start and end cell) are also recorded on the transient `player._last_step_cells` so the `BatchSimulator` Overwatch resolution step can test whether the traversal crossed any **Hold**ing player's **Line of sight** — MOVE-01 otherwise discards the intermediate route (`movement_trail` keeps only `(start, end, tick)`), so this committed-route exposure is what makes the "moved *through* LoS in one Advance" guarantee resolvable. Recording it is pure bookkeeping — still **no RNG**, contract unchanged.

**`max_movement_for_map(zone_data)`** — cells-per-tick ceiling scaled by map size: `max(rows, cols) // 10` clamped to **5..10** (PLAN.md STAT-03 Phase 1). `None`/empty → 5.

**`cells_to_move(speed, zone_data)`** — `max(1, ceil(speed/100 * max_movement_for_map(zone_data)))`. The PLAN.md `speed`-stat formula; floored at 1 so a moving player is never frozen by a low `speed`. Called by `BatchSimulator._move_player_in_memory` and `ResourceBasedSimulator._move_to_cell` with `getattr(player, "speed", 50)` (a baked `PlayerState.speed` field for BatchSim; a `PlayerRoundState.speed` forwarding property for RBS). **MOVE-01:** on an `only_move` tick the move functions pass `cells_to_move(...) * 2` (one single 2× step, no other deliberate effect); every other non-**Stationary** Action still Advances the normal `cells_to_move(...)` distance. **MOVE-02:** RBS passes this to `astar_advance` (per-tick recompute); BatchSim passes it to `astar_advance_cached`, where the doubled `steps` is consumed along the **same** committed route (the 2× is not a recompute trigger).

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

**`_STALE_THRESHOLD`** — module-level dict mapping role strings to their memory staleness thresholds in **ticks** (TIME-01; sourced from `time_constants.py`): `Heavy/Medic/Ammo → 120` (60 s), `Scout/Commander → 30` (15 s).

**`_cell_from_memory(player, tag_id, movement_ctx) -> tuple[int,int] | None`** — looks up `tag_id` in `player.player_memory`; returns the stored cell if the entry is fresh (within the role's staleness threshold), `None` if stale or absent. Stale slow-role entries (Heavy/Medic/Ammo) return the last-known cell anyway; stale fast-role entries return `None` to let callers fall through to role defaults.

**`_known_enemies_from_memory(player, all_alive, movement_ctx) -> list`** — returns all enemy `PlayerState` objects whose last-known cell is fresh enough to use, substituting the memory cell for the player's actual cell in a lightweight proxy so callers don't need to distinguish real vs remembered positions.

**`_apply_teamwork_bias(player, candidates, movement_ctx) -> tuple[int,int] | None`** — when `player.teamwork > 50`, filters `candidates` (high-LOS cells) to those also within LOS of ≥1 alive ally; returns the nearest qualifying cell, or `None` when no ally-visible high-LOS cell exists (caller falls through to unbiased selection).

**`_goal_from_action(player, all_alive, enemy_color, cell_row, cell_col, intended_action, movement_ctx) -> tuple[int,int] | None`** — unchanged signature; now uses `_known_enemies_from_memory` instead of direct `all_alive` iteration when selecting a tag/missile target so goal selection uses memory rather than perfect knowledge.

**`_goal_from_role(player, all_alive, enemy_color, cell_row, cell_col, movement_ctx) -> tuple[int,int] | None`** — unchanged signature; internally calls `_apply_teamwork_bias` after identifying role-specific candidate cells (Scout, Heavy-healthy paths) before returning.

**`choose_goal_cell(player, all_alive, spawn_cells, movement_ctx=None, intended_action="")`** — duck-typed goal selector shared by both simulators (MAP-05). **MOVE-01: now consulted every tick** a player is not **Stationary** (was only reached from the old `change_zone` branch), so the nuke / critical-resource / score-broadcast overrides below are live for all roles every tick. Priority order:
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

`_ACTION_IDX` and `_CHOICES` define the **9-slot** action array (indices 0–8): `tag_player`, **`only_move`** (renamed from `change_zone`, MOVE-01 — same index 1), `hide`, `capture_base`, `use_special`, `resupply_ally`, `missile_player`, `request_resupply`, **`hold`** (MOVE-03 — index 8). `request_resupply` (index 7) is available to all 5 roles; weight is non-zero only when the player needs resources (Ammo players are locked to requesting lives; Medic players to requesting shots). Fulfilled asynchronously by `resolve_resupply_requests` in `resupply_queue.py` at end of tick. **MOVE-03 ([ADR-0009](../../docs/adr/0009-hold-overwatch.md), CONTEXT.md):** `hold` (index 8) puts the player in **Overwatch** — it sets the transient `is_holding` flag, **carries over** like `is_hiding` (until a non-`hold` Action is rolled or a Down/respawn force-clears it), and is **Stationary** (no **Advance** while holding, both simulators). The slot is shared by both simulators' Action selection (all weights stay ≥ 0 — the `random.choices` invariant), but the **Overwatch shot** resolution is `BatchSimulator`-only (it reads the path-commitment route cache; RBS treats `hold` as a Stationary no-op).

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

## time_constants.py

Pure Python, **zero imports** (no Django, no other `sim_helpers` modules). The single source of truth for every absolute time constant in the simulator, introduced by TIME-01 so the constant-by-constant audit is one reviewable file and future raw-seconds regressions are blocked at import.

| Constant | Value | Purpose |
|----------|-------|---------|
| `TICKS_PER_ROUND` | `1800` | Round duration (15 min at 0.5 s/tick) |
| `SURVIVED_SENTINEL` | `1801` | `was_eliminated_at` value meaning "never eliminated" (ticks + 1) |
| `RESPAWN_TICKS` | `16` | Full respawn cooldown after a life loss (8 s) |
| `NOT_TARGETABLE_TICKS` | `8` | Not-targetable (cannot-be-Tagged) front portion of the cooldown (4 s); gates `is_taggable_at`. The Reset window is the derived `[NOT_TARGETABLE_TICKS, RESPAWN_TICKS)` span. |
| `ENDGAME_RUSH_TICKS` | `1680` | Tick at which `weights.py` triggers the endgame rush (was `second >= 840`) |
| `SCORE_BROADCAST_PERIOD_TICKS` | `360` | MECH-06 score-broadcast period (180 s) |
| `GOAL_RECOMPUTE_PERIOD_TICKS` | `4` | MOVE-04 **Goal commitment** cadence (2 s) — steady-state `choose_goal_cell` recompute period; reactive overrides bypass this and run every tick. Phase is **expiry-based** per-player (`expires_at_tick = tick + N`), **not** a global `tick % N == 0`. See [ADR-0010](../../docs/adr/0010-goal-commitment-via-tick-cadence-throttling.md) |
| staleness | `120` / `30` | MECH-06 memory staleness — Heavy/Medic/Ammo `120`, Scout/Commander `30` (60 s / 15 s) |

(Other constants follow the same pattern, e.g. the medic-under-fire alert window.) Imported by `weights.py` (endgame rush, score broadcast), `tick_engine.py`, `pathfinding.py` (`_STALE_THRESHOLD`), `player_state.py`, and both simulators — all now consume tick-valued constants from here rather than inline numeric literals.

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

All return `(ready_now, still_pending)` typed lists. Resolution logic (what to do with ready items) stays in each simulator. Post-TIME-01 the `second` cursor argument and the `complete_time`/`fire_at` fields it splits on are tick-valued for BatchSim (RBS converts at its persist boundary); the split arithmetic is unit-agnostic.

### Parallel batch workers (SIM-07 / SIM-08)

`BatchSimulator._run_parallel` fans rounds out to `batch_round_worker`, the process-pool worker. **SIM-07:** `batch_round_worker` takes a per-round **int** seed and `random.seed(it)`s before simulating, so a given master seed yields identical games whether the batch runs serially or in parallel (guaranteed, tested property). Per-round seeds are derived from a deterministic `random.Random(master_seed)` seed chain in `run`. **SIM-08:** `batch_round_worker` additionally accepts the per-game `flipped` flag (the Orientation, deterministic by game index — never RNG-derived); when `flipped` is true it **swaps the precomputed red/blue rosters** before simulating, so the worker plays the same Orientation the serial path would. Serial and parallel runs therefore produce identical team-position aggregates **and identical `side_advantage`** for a given master seed. `score_round_worker` (the `score_averages` command path) remains out of SIM-07/SIM-08 scope — it does not take or seed an int seed, nor flip sides; seeding stays `random.getstate()`-based. It now takes the parent-built `MapContext` (or `None`) as a 4th args-tuple element and threads it into `_simulate_round`, so `score_averages --map` works under `--workers > 1`; this is the only change to it.

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
