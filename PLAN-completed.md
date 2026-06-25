# Development Plan — Completed Work

Archive of shipped stories, split out of PLAN.md. See PLAN.md for active/pending work.

---

## Phase 0 — Immediate Fixes (blockers)

These are bugs and technical debt that corrupt simulation results or mislead future development. 
Fix before building anything new.

### FIX-01 · Enforce Scout-only role doubling

`teams/models.py` — `Player.clean()` currently allows 2 Commanders, 2 Medics, etc. 
Only Scout may appear twice in an SM5 roster. Fix the validation, show a clear error on the team detail page for any 
existing bad rosters, and add unit tests covering all valid and invalid compositions.
- completed

### FIX-01b · Block match creation on invalid rosters

`matches/views.py` — The match and single-round creation views must check `is_valid_roster` on both teams before 
calling the simulator. Return a form error with the specific composition problem; never pass a
broken roster to `ResourceBasedSimulator`.
- completed, currently we check roster errors and return any for both teams before attempting to run the simulator.

### FIX-02 · Derive shot_power and shield from role

`teams/models.py` — `shot_power` and `shield` are stored as DB columns but should be computed from the player's role. 
Convert to `@property` on `Player`, delete the DB columns, and update any simulator code that reads them directly.
- completed: DB columns removed in teams/0008 and matches/0016; both `PlayerRoundState` and `PlayerState` expose `shot_power` and `max_shields` as `@property` derived from `ROLE_STATS`.

### FIX-03 · Remove SingleRound legacy model and route

`matches/models.py` — `SingleRound` is superseded by `GameRound`. Remove the model, its migration, 
the `/matches/round/<id>/` route, and its view. Update any templates that still link to it.
- completed: SingleRound model removed (migration 0019), SimpleMatchSimulator removed, SingleRoundSetupForm simplified, all views and templates updated to use GameRound only.

### FIX-04 · Clean up stale TODO comments in get_mvp

Minor — remove or update the two stale TODO comments in `PlayerRoundState.get_mvp`. 
Add a docstring explaining the weighting formula. No functional change.
- completed: No TODO comments remain; `get_mvp` has a detailed docstring covering all roles and scoring rules.

---

---

## Phase 1 — Map–Simulation Integration

The map editor produces a rich cell grid with precomputed sight lines. Currently the simulator ignores it, 
using only 3 abstract zones. This phase replaces the 3-zone model with full map awareness.

### MAP-01 · Player position on the cell grid

Replace `PlayerRoundState.current_zone` (0/1/2) with a `(row, col)` cell coordinate. On round start, 
place players on or near their team's base cell. Persist the active zone_size and map for the round on 
`GameRound` so all queries are keyed consistently.

**Data changes:** `GameRound` gets `arena_map` FK and `zone_size` field. `PlayerRoundState` gets `cell_row` 
and `cell_col` integers. Keep `current_zone` as a derived property (red/neutral/blue based on cell's zone type)
for backwards compatibility with existing views.

Existing match/round data is disposable — no data migration required.
- completed
- note: `current_zone` DB column renamed to `zone_fallback` via `RenameField` migration (0020); `current_zone` re-exposed as `@property` that reads `zone_fallback` directly (MAP-02+ will derive it from live cell coordinates). Map is optional at match creation — UI lets user pick a confirmed map or run with the 3-zone fallback. `MapZoneConfig.zone_data` dual format handled: production stores `{"zones": [...], "blocked_edges": {...}}` dict; simulator unwraps with `isinstance(raw, dict)` check. `_resolve_map_data()` and `_zone_from_cell()` added as `@staticmethod` on `ResourceBasedSimulator`. Base configs queried in a single batched DB call (not per-color loop).

### MAP-02 · Cell-aware zone movement

Replace `_change_zone()` with a pathfinding step that moves a player to an adjacent passable cell each tick. 
Derive a dedicated movement adjacency list from `MapZoneConfig.zones` — do not reuse `SightLineConfig` for 
movement (LOS ≠ adjacency). Players navigate toward a goal cell (enemy base, ally position, nearest resupply) 
using a simple weighted heuristic.

Moving uphill (to a higher-elevation cell) applies a movement speed penalty. Moving downhill has no effect.
Adjacent non-wall cells are always traversable regardless of elevation difference.

**Acceptance:** A player starting at their home base will reach the enemy base in a realistic number of ticks
proportional to map size. Players never move into wall cells.
- completed: `matches/sim_helpers/pathfinding.py` added with `build_movement_adjacency` (4-connected, walls excluded) and `astar_next_step` (A* with Manhattan heuristic). Elevation stub added (`_elevation_at`, `_movement_cost`: uphill=1.5×, flat/downhill=1.0). `ResourceBasedSimulator` and `BatchSimulator` both use cell-aware movement when a map is provided via new `_build_movement_ctx`, `_move_to_cell`, and `_choose_goal_cell` methods. Default goal: enemy base cell; lives-critical → allied medic's cell; shots-critical → allied ammo's cell. Each movement step writes a `GameEvent(event_type="movement")` with `cell_row`/`cell_col` in metadata for replay. `PlayerState` dataclass gains `cell_row`/`cell_col` fields. Fallback to old 3-zone `_change_zone` when no map is assigned (MAP-06 compat). `simulation_tests.py` split into 6 focused files under `matches/tests/`; `TestMap02CellMovement` added with 7 tests covering adjacency, A*, movement events, fallback, and player-reaches-base. All 189 tests pass.

### MAP-03 · Line-of-sight targeting

Replace the current "same zone = can tag" rule with LOS-based targeting. 
A player can tag any enemy whose cell appears in the `SightLineConfig` adjacency list for the actor's current cell. 
Pull sight data from the precomputed `SightLineConfig` at round start and hold in memory for the duration.

**Acceptance:** Two players separated by a wall cannot tag each other. Players across a corridor can. 
Hit-chance formula remains the same; only target eligibility changes.
- completed
- note: `_resolve_map_data` now returns a 4-tuple `(zone_size, spawn_cells, zone_grid, sight_data)`; raises `ValueError` if `SightLineConfig` is absent for the map's zone_size. `_build_movement_ctx` gains a `sight_data` kwarg; the dict gains a `"sight_data"` key (frozenset-valued for O(1) lookup). Module-level `_get_los_targets(actor, candidates, movement_ctx)` replaces the zone equality check in both `ResourceBasedSimulator._choose_tag_target` and `BatchSimulator._choose_tag_target`; falls back to zone-based when no map is active. Simulating with a map that has no sight lines computed raises `ValueError` with a clear editor prompt. All test map fixtures updated to include `SightLineConfig`; 9 new tests added in `TestMap03LOSTargeting` and `TestMap03DBIntegration`.

### MAP-04 · Base interaction via BaseSightLineConfig

Replace the abstract base-capture zone check with `BaseSightLineConfig` lookups. A player can interact with a base 
(capture, resupply trigger) only if their current cell appears in `visible_cells` for that base. 
Load `BaseSightLineConfig` at round start alongside `SightLineConfig`.
- completed: `_resolve_map_data` now returns a 5-tuple including `base_sight_data` (a `{"base_type": frozenset}` dict); raises `ValueError` if no `BaseSightLineConfig` exists for the map's zone size. `_build_movement_ctx` gains a `base_sight_data` kwarg that is stored in the ctx dict. Module-level `_get_base_interaction(player, movement_ctx)` checks neutral bases first, then the opposing base; returns `15`/`14`/`13` or `None`. Both `ResourceBasedSimulator._capture_base` and `BatchSimulator._capture_base` guard the capture with a `base_sight_data` range check before awarding points. All existing test fixtures updated to include `BaseSightLineConfig`; 15 new tests added in `TestMap04BaseInteraction` and `TestMap04DBIntegration`. 263 tests pass.

### MAP-05 · Role-aware goal selection

Update the weight functions in `weights.py` to express goals in terms of target cells rather than abstract zones.
Each role picks a goal cell and the movement action moves one step toward it.

- **Scouts** prioritize high-LOS cells (cells with the most entries in `SightLineConfig`).
- **Heavies** use a dual-mode system: precomputed strong spots (defensive corridors between enemy entry points
  and the allied base area, stored on the map at save time, user-overridable) OR dynamic per-tick goal
  computation tracking current allied Medic/Ammo positions. The Heavy switches between modes based on player
  stats (stat wiring in Phase 2).
- **Medics/Ammos** stay withhin LoS of Heavy for ammo on High LoS squares.  medic stay within LoS of heavy or ammo on low LoS squares.

High-LOS cells and Heavy strong spots are precomputed and stored when a map is saved. Heavy strong spots
can be manually added or overridden by the map editor.
- completed
- note: goal selection is action-aware (uses `last_chosen_action` from the previous tick) with priority: (1) critical-resource override → seek allied Medic/Ammo, (2) action-driven movement via `_goal_from_action`, (3) role-specific positioning via `_goal_from_role`, (4) default enemy base. `_resolve_map_data` now returns a 7-tuple including `cell_ranking` and `strong_spots`; `_build_movement_ctx` gains `cell_los_counts`, `high_los_cells`, and `strong_spots` keys. `MapCellRankingConfig` and `HeavyStrongSpotsConfig` auto-seeded when sight lines are saved; user-editable via `/maps/<id>/strong-spots/save/` endpoint. 293 tests pass.

### MAP-06 · Fallback for rounds without a map

When `GameRound.arena_map` is null (map not assigned), fall back to the existing 3-zone logic so that existing tests
and simulations without maps continue to work. This is a compatibility shim — new matches should always have a map.
- completed: implemented as part of MAP-02. `_resolve_map_data()` returns `(None, {}, None, None, {})` when `arena_map is None`; both `ResourceBasedSimulator` and `BatchSimulator` check `movement_ctx is not None` before cell-aware movement, falling back to `_change_zone`.

### MAP-07 · Map wall hazards

Maps have three active wall types:

- **Low walls** — block movement but not sight.
- **High walls** — block both movement and sight.
- **Windowed walls** — block sight but allow tagging through them (gun-port style aperture).

Mirrored/reflective walls (shot bouncing) are **deferred** — see Deferred Items section.

Add a `wall_type` field to the map cell data and update movement and targeting logic to respect these distinctions.
- completed
- note: wall types encoded as cell values in the existing `zones` 2D array (0=high wall, 1=floor, 4=low wall, 5=windowed wall); no new DB column required. `wall_meta` JSON object `{"r,c": {"facing": "N"|"S"|"E"|"W"}}` stored alongside `zones` in `MapZoneConfig.zone_data` for windowed wall aperture directions. `_MOVEMENT_PASSABLE = {1, 2, 3}` and `_LOS_PASSABLE = {1, 2, 3}` module constants added (low/windowed wall block both movement and LOS; low wall transparent to sight only). `_has_los` updated: value 5 now blocks like 0; value 4 is transparent. `detect_zones` no longer opens the original color image (legacy RGB red/blue zone detection removed; dead pixel-classification code removed). `_resolve_map_data` now returns 8-tuple (added `wall_meta`); `_build_movement_ctx` gains `wall_meta` key. `_zone_from_cell` changed from cell-value lookup to proximity-based Manhattan distance (legacy 2/3 values no longer produced by auto-detection). New module-level `_can_tag_through_windowed_wall` for aperture targeting; `_get_los_targets` extended to check windowed wall apertures for candidates not in normal sight_data. Map editor gains wall-brush UI (Low Wall, Windowed Wall, High Wall, Floor buttons) and windowed facing picker; save payload includes `zones` grid and optional `wall_meta`.

### MAP-08 · Map-based spawn points

Spawn cells are precomputed and stored on the map at save time (base zones are static). Players spawn within
one of these precomputed spawn cells near their team's base at round start.
- completed
- note: `red_spawn`/`blue_spawn` lists stored inline in `MapZoneConfig.zone_data` JSON (no new DB column); auto-generated at sight-line save time as all passable cells within Manhattan dist ≤ 5 of each team's base cell. Each list is split into two sub-pools (closer vs farther from enemy base): Heavy/Commander draw from the closer pool; Medic/Ammo from the farther pool; Scout fills whichever pool still has room. Overflow and absent spawn data fall back to the base cell itself. User can override spawn cells in the map editor and save via the existing Save button.

### MAP-09 · High Ground

Map cells have a continuous numeric `elevation` attribute. High walls also carry a numeric `height` value.

**Shoot-over formula:** the map editor computes a default `can_shoot_over` boolean for each high wall based on
relative elevation (attacker elevation vs wall height). The map editor exposes a per-wall manual override for
cases where the formula output is incorrect.

High-ground players gain a visibility bonus (more cells visible in `SightLineConfig`). The hit-chance formula
applies a modifier making it harder to hit players on higher ground from below.
- completed
- note: `elevation` stored as a 2D float array in `MapZoneConfig.zone_data` under the `"elevation"` key (same JSON field as `zones`, `blocked_edges`, `wall_meta`, `red_spawn`, `blue_spawn`); defaults to 0.0 for all cells if absent. Wall `height` stored in `wall_meta` per cell: `{"r,c": {"facing": "N", "height": 2.0}}`; blocks (not shoot-overable) when height key is absent. Shoot-over formula: `attacker_elev - wall_cell_elev > wall_height * 0.5` — evaluated in `_has_los` (via `can_shoot_over_wall` helper) and propagated into `SightLineConfig` at sight-line save time. LOS is direction-aware: `compute_sight_lines` checks A→B and B→A independently so asymmetric elevation is correctly reflected (elevated attacker gets the link; ground-level defender does not). Hit-chance modifier: `hit_chance *= max(0.5, 1 - 0.1 * elevation_diff)` where `elevation_diff = max(0, target_elevation - attacker_elevation)` — applied uphill only; `_elevation_hit_modifier` delegates to public `elevation_hit_modifier(attacker_elev, defender_elev)`. `save_zone_config` validates client-sent elevation values are in `[0.0, 10.0]` (HTTP 400 on out-of-range). `_resolve_map_data` includes `elevation_grid` as the 10th tuple element; `_build_movement_ctx` gains an `"elevation_grid"` key (2D float list). Map editor gains an elevation brush tool (paint a numeric elevation value onto individual cells) and a ramp tool (select two cells to linearly interpolate elevation across all cells between them); both use the existing bulk drag-select pattern. Wall paint tool extended to support bulk drag-select for painting wall type and height together.

---

---

## Phase 2 — Player Stats Integration

Most of the 19 player stats exist on the model but are not used in simulation. This phase connects them.

### STAT-01 · Expose all 19 stats in the add/edit player UI

`teams/` — Both the add and edit player forms must render all 19 stat fields grouped by category 
(Awareness, Decision-making, Physical, Team, Role). New players default to 50 for all stats.
Existing player data is disposable — no backfill migration required.
Show `overall_rating` as a live-updating summary. Add a convenience "Set to Average / Elite" bulk preset.
- completed
- note: `PlayerForm` exposes all 19 stat fields (defaulting to 50) with "Set All to Average (50)" and "Set All to Elite (90)" preset buttons; `overall_rating` is shown as the saved computed value (mean of all 19 stats).

### STAT-02 · Role-preference stat multiplier

`Player` has a multi-valued `preferred_roles` field. Add `Player.stat_for_simulation(stat_name)` which returns
`stat_value × 1.2` if the player's current game role is in their `preferred_roles` set, otherwise returns
`stat_value` unmodified. This flat 20% boost across all stats is the first pass.

Per-stat-per-role weight tuning (e.g. Scout `accuracy` weight = 1.5, Medic `resupply_efficiency` weight = 2.0)
is **deferred** — see Deferred Items section. Keep `overall_rating` as the unweighted display average.
- completed
- note: `Player.stat_for_simulation(stat_name, role)` returns `min(int(raw_value * 1.2), 100)` when `role in self.preferred_roles`, raw value otherwise; invalid stat names raise `AttributeError` naturally. `PlayerRoundState.accuracy/survival/player_awareness` forwarding properties now call `stat_for_simulation` instead of reading raw fields. `BatchSimulator._make_players` bakes boosted values into `PlayerState` at construction.

### STAT-03 · Wire stats into action weight functions

Map each relevant stat to a weight modifier in `weights.py`. Stats are wired in their respective phases:

**Phase 1 (map-dependent):**
- `positioning` — biases movement toward high-value cells (pairs with MAP-05)
- `speed` — allows more cells traversed per tick (pairs with MAP-02), formula for cells moved should be celing of speed/100 * max movement where max movement is 5-10 cells per tick depending on map size

**Phase 2 (this phase):**
- `accuracy` / `survival` — already used in hit-chance formula; confirm they feed in correctly
- `decision_making` — scales the spread between actions (high = weights more concentrated on optimal action)
- `stamina` — degrades action quality / effective hit-chance in second half of round
- `special_usage` — scales special activation weight directly
- `resupply_efficiency` / `resupply_synergy` — scale resupply weight for Medic/Ammo
- `teamwork` / `communication` — scale ally-following behavior weight

**Phase 3 (nuke-mechanic-dependent):**
- `game_awareness` / `player_awareness` — scale reaction to enemy nuke (see MECH-04)

- completed
- note: `decision_making` applies a linear spread multiplier (`factor = 1 + dm/100`) on the weight vector after role weights are computed — best action × factor, others ÷ factor. `stamina` is evaluated every 10% of round; when `stamina < elapsed_%`, `stamina_penalty_count` increments (stacking −10% movement weight, −5% hit_chance via `stamina_hit_modifier`). `special_usage` scales `use_special` weight delta by `special_usage/50` for all roles. `accuracy`/`survival` confirmed correct (no change). `resupply_efficiency` and `resupply_synergy` wired in MECH-01 (skeleton TODO blocks removed). `teamwork` and `communication` have skeleton TODO blocks deferred to MECH-06. New `PlayerState` fields: `decision_making`, `stamina`, `special_usage`, `resupply_efficiency`, `resupply_synergy`, `teamwork`, `communication` (default 50) + `stamina_penalty_count`/`stamina_next_check_pct` transient tracking fields.
- note (Phase 1 follow-up): `speed` is now wired — `pathfinding.cells_to_move(speed, zone_data) = max(1, ceil(speed/100 * max_movement))` where `max_movement = max(rows,cols)//10` clamped 5..10; both simulators' move functions call `astar_advance` for that many cells per move tick instead of a single `astar_next_step`. `PlayerState.speed` is a baked field; `PlayerRoundState.speed` is a forwarding property; `speed` added to `_SIMULATION_STATS`. `positioning` remains the only un-wired Phase 1 stat. Known follow-ups (separate steps): movement is still gated behind the `change_zone` action (commander/medic/ammo have `change_zone` weight 0 ⇒ still frozen until movement is decoupled), and A* is recomputed per move (goal/path caching pending).

---

---

## Phase 3 — Simulation Mechanics

New and corrected mechanics that make the simulator more faithful to SM5 rules and more interesting strategically.

### MECH-01 · Resupply request action + combo resupply + resupply stat wiring

- completed
- note: `request_resupply` added as action index 7 in `_ACTION_IDX`/`_CHOICES` in `combat.py`; available to all 5 roles (Ammo is locked to requesting lives, Medic to requesting shots). Weight scales with `resupply_efficiency`; action is inactive when the player does not need resources. `resupply_efficiency` and `resupply_synergy` stats are now fully wired — TODO/skeleton blocks in `weights.py` removed. `resupply_efficiency` scales the `request_resupply` weight; `resupply_synergy` scales the `resupply_ally` weight for Medic/Ammo players. New module `matches/sim_helpers/resupply_queue.py` exposes `resolve_resupply_requests(requestors, all_alive, second, movement_ctx, *, emit_event=None)` called at the end of each tick in both simulators. Resolution uses a priority queue (Heavy > Commander > Scout > Ammo > Medic); support must be in LOS, not deactivated, have shots > 0, and not be on cooldown. Stress failure formula: `failure_pct = min(100, (dm + teamwork) / 10 × prior_count)`. Combo chance formula: `min(0.95, 0.20 + ammo_syn/100 × medic_syn/100 + ammo_eff/100 × medic_eff/100)`; combo fail fallback gives 75% priority resupply / 25% other. `combo_resupply_count` DB column added to `PlayerRoundState` (IntegerField default=0, migration added) and as `combo_resupply_count: int = 0` field on `PlayerState`. `GameEvent(event_type="combo_resupply")` metadata includes `medic_tag` and `ammo_tag`; single resupply events continue to use `resupply_lives`/`resupply_ammo`.

### MECH-02 · Tag of any entity resets same-target restriction

- completed
- note: `last_tagged_id` is set on every successful hit — enemy tag, missile hit, base capture, and resupply (resupply was the missing case; added to both branches of `attempt_resupply` in `combat.py`). `choose_tag_target` in `mechanics.py` enforces the restriction with a `game_awareness` gate: `>= 35` always filters the locked reset target; `< 35` filters with `game_awareness / 100` probability so unaware players occasionally waste a shot. `game_awareness` stat added to `PlayerState` dataclass, forwarded as a `@property` on `PlayerRoundState`, and wired into `BatchSimulator._make_players`. Tests in `matches/tests/test_mech02_tag_cooldown.py` (23 tests, 0 DB required).

### MECH-03 · Commander nuke stacking behavior

Currently Commanders almost never stack more than the required 20 special points for a nuke. 
High game-awareness Commanders should be more likely to continue stacking beyond the nuke threshold then drop more than 1 back to back
when we get to MECH-06 with the memory system adaptability should be able to change this behavour if the situation arises that would be
good to capitalize on (ie. player below 3-4 life threshold, enemy team low on lives/shots, enemy medic/ammo separated)
The weight of Use-special should scale with `game_awareness` and current special points.  for now implement: 
special_points > 20 and game_awareness < 30 then normal use-special weight applies (otherwise weight is 0)
special_points > 40 and game awareness < 50 then normal use-special weight appliees (otherwise weight is 0)
special_points > 60 and game awareness < 70 then normal use-special weight applies (otherwise weight is 0)
special_points > 80 then use-special weight applies regardless of game awareness
- completed
- note: `_commander_nuke_gate(sp, ga)` added to `weights.py`; gates the `use_special` weight in `_get_commander_weights` so high-awareness Commanders stack SP before firing. Thresholds: ga<30→sp>20, ga<50→sp>40, ga<70→sp>60, ga>=70→sp>80. When sp>80 the gate always opens regardless of awareness. A `# MECH-06:` hook comment marks where situational overrides (memory system) will plug in. 15 pure-unit tests in `matches/tests/test_mech03_nuke_stacking.py`.

### MECH-04 · Player reaction to incoming nukes

When a pending nuke is in flight (fuse window active), players should react based on stats. Add a nuke-awareness 
check each tick for all active players on the target team:
- High `game_awareness` + `player_awareness`: player attempts to tag the Commander to cancel the nuke 
  (raises `tag_player` weight toward the Commander specifically, overriding normal role behavior)
- High `survival`: player moves to a different cell to reduce the nuke's impact (hide weight increases)
- Low awareness stats: player ignores the nuke and continues their normal action
- completed
- note: `_apply_nuke_reaction_flags` helper (module-level in `simulation.py`) resets then sets `reacting_to_nuke` each tick for every active player on the nuke-targeted team. `reaction_probability = (game_awareness + player_awareness) / 200`. If reacting: Medic/Ammo seek the neediest ally (by lives ratio for Medic, shots ratio for Ammo) and transfer `tag_player` weight into `resupply_ally + 20`; non-support with lives ≤ 30% → allied Medic cell (survival mode); non-support with lives > 30% → `# TODO MECH-06` placeholder hook. `reacting_to_nuke` is a transient bool on `PlayerState` (no DB column). Read in `choose_goal_cell` (`pathfinding.py`) and weight boost applied in `weights.py`.

### MECH-05 · Nuke cancellation fuse window fix (SIM-03)

Verify and correct the nuke cancellation logic: a nuke must be cancelled if the firing Commander is eliminated 
during the fuse window (not just at exact timestamps). Write a regression test: Commander fires nuke at T=100, 
gets tagged at T=103 (within fuse), nuke must not detonate.
- completed
- note: `BatchSimulator` nuke resolution now checks `n.player.special_active_until >= n.complete_time` (matching `ResourceBasedSimulator`) instead of only `is_active_at`. Tick ordering fix: nuke resolution moved to after reaction/followup/tag processing so same-tick cancellations work correctly.

### MECH-06 · Player memory system + teamwork/communication stat wiring

- completed
- note: player_memory dict added to PlayerState (transient, no DB columns); staleness thresholds: Heavy/Medic/Ammo=60s, Scout/Commander=15s; stale slow-roles use last-known cell, stale fast-roles fall through to role defaults; `communication` stat = per-tick broadcast probability (0-100%) to allies within sqrt(rows²+cols²)/2 Euclidean range; `teamwork` stat (>50) biases goal toward high-LOS cells in ally LOS on non-nuke ticks; score broadcast every 180s: losing→+10 aggression, winning+low-lives+medic-dead→+20 hide, winning+low-lives+medic-alive+6min→seek-medic-cell; nuke activation broadcast updates enemy memory with Commander cell; medic-under-fire alert (2 hits in 12s) updates ally memory with medic cell; MECH-04 TODO hook filled — nuke-reacting players with fresh Commander memory seek that cell for tag-cancel. 75 new unit tests in test_mech06_player_memory.py.

Players have imperfect knowledge of the arena. Replace the current perfect-information model with a
per-player memory dict that is updated from observable events and degrades when not refreshed.

**Memory sources (what updates a player's memory):**
- **Direct LOS:** each tick, the player "sees" all enemies and allies in their current LOS and updates
  their memory entry for each (last known cell + timestamp).
- **Global broadcasts (all players on both teams hear these):**
  - Nuke activation: which team fired, fuse duration.
  - Score update: every 3 minutes, which team is winning and by how many points.
  - Medic-under-fire alert: when a Medic is hit 2 times within 12 seconds (team-only broadcast).
- **Ally communication (within ~half the map radius):** when a nearby ally communicates, the player
  receives that ally's LOS snapshot for visible enemies: e.g. "enemy Commander nuking at cell (r,c)",
  "enemy Heavy at (r,c) with N shields remaining", "enemy Medic at (r,c)".

**`communication` stat:** probability (0–100 → 0–100%) that a player broadcasts their current LOS
snapshot to allies within range when taking any action. High `communication` = frequent intel sharing.

**`teamwork` stat:** scales the weight for movement goals that keep the player within LOS of allies
(not necessarily adjacent — LOS range is sufficient). Specifically:
- During an active enemy nuke fuse window, high-`teamwork` players bias movement toward staying in
  LOS of a nuke-threatened ally rather than purely offensive goals.
- Non-critical ticks: `teamwork` adds a gentle bias toward a high-LOS cell that is also within LOS
  of at least one ally (overlapping coverage), scaled by the stat.

**Data model:** store memory as a transient dict `{player_tag_id: {"cell": (r,c), "timestamp": s}}`
on each `PlayerState` / `PlayerRoundState`. No DB columns — memory is never persisted. Memory entries
older than 30 seconds are treated as stale (player acts on best-guess or last-known position).

**Scope note:** global broadcasts and memory reads replace the current perfect-knowledge ally/enemy
lookups in `_goal_from_action`, `_goal_from_role`, and the nuke-reaction logic in MECH-04.

---

---

## Phase 3.5 — Simulator Consolidation

Replace `ResourceBasedSimulator` with `BatchSimulator` as the single simulation engine across all three use cases. RBS remains in the codebase only until all views are migrated; it is then retired.

### SIM-06 · Close `_flush_to_db` field gaps

`BatchSimulator._flush_to_db` skips several `PlayerRoundState` columns that exist on `PlayerState`. Fill in all missing fields: `follow_up_shots`, `reaction_shots`, `seconds_active`, `seconds_not_targetable`, `seconds_reset_window`, `combo_resupply_count`, `times_tagged_in_reset_window`, `missile_points`, `cell_row`, `cell_col`. Add a test that simulates a round, flushes to DB, and asserts every field is non-default on at least one player.
- completed
- note: migration added for 4 new `IntegerField(default=0)` columns (`seconds_active`, `seconds_not_targetable`, `seconds_reset_window`, `missile_points`); the other 6 fields (`follow_up_shots`, `reaction_shots`, `combo_resupply_count`, `times_tagged_in_reset_window`, `cell_row`, `cell_col`) already had DB columns and required no migration. `_flush_to_db` now writes all 10 previously-skipped fields. `save_games`/`replay_round` now accept `arena_map` so `cell_row`/`cell_col` persist on map-aware replays. Flush coverage added in `test_batch_sim.py::TestSim06FlushFields`. **Time-unit decision:** the `seconds_*` fields store **seconds** (float `+= 0.5` accumulation truncated once by `IntegerField` at flush; consistent with `db_second = int(second)` and `was_eliminated_at`). Storing raw ticks was considered and deferred — it would corrupt `score_averages` percentages, which sum these against seconds-derived dead-time. Full tick-native migration tracked as TIME-01; rationale in `docs/adr/0001-time-unit-seconds-now-tick-native-later.md`. Domain terms in `CONTEXT.md`.

### TIME-01 · Tick-native internal time model

Migrate the simulator to a fully tick-native internal time unit; seconds become a display-only concept (UI divides by 2). Touches ~22 files: rename `seconds_*` → `ticks_*`, redefine `was_eliminated_at` and its `901` survived-sentinel / `900 - x` dead-time derivation, convert every hard-coded game-logic time constant (`weights.py` endgame `840`, score broadcast `360`, MECH-06 staleness `60`/`15`, the 8 s respawn / 4 s windows, the STAT-03 stamina schedule), and flip `GameEvent.timestamp` to ticks. Update templates/`score_averages`/`game_analysis` to divide by 2 at the display boundary only.

**Sequencing:** must land **before** any Phase 4 work that builds on `GameEvent.timestamp` (SIM-05 replay, RES-02 SP timeline, RV-01 round compare) so the timestamp unit is settled before analytics depend on it. Decision and rejected alternatives recorded in `docs/adr/0001-time-unit-seconds-now-tick-native-later.md`. Needs its own grill before implementation — the constant-by-constant audit is the risky part.
- completed
- note: tick is now the canonical persisted/internal/API unit (1 round = 1800 ticks); seconds are a display-only `÷2` applied **only** at HTML templates and the `score_averages`/`game_analysis` CLI. Five locked decisions (grill, 2026-05-15): (1) tick-precision is genuine — respawn/reset/fuse/cooldown edges now evaluate at tick granularity, shifting seeded outcomes by sub-second fractions; (2) the REST API returns raw ticks (no serializer `÷2`), inverting the pre-TIME-01 "all stored/displayed time is seconds" rule; (3) all ~12 absolute time constants moved to a new zero-dependency `matches/sim_helpers/time_constants.py` (`TICKS_PER_ROUND=1800`, `SURVIVED_SENTINEL=1801`, `RESPAWN_TICKS=16`, `NOT_TARGETABLE_TICKS=8`, `ENDGAME_RUSH_TICKS=1680`, `SCORE_BROADCAST_PERIOD_TICKS=360`, staleness `120`/`30`, etc.); (4) survived sentinel `901 → 1801` applied uniformly to `PlayerRoundState.was_eliminated_at`, `Match.round1_eliminated_at`/`round2_eliminated_at`, `GameRound.eliminated_at`, dead-time derivation `900 - x → 1800 - x`; (5) `ResourceBasedSimulator(duration=…)` → `duration_ticks=…` (callsites become `duration_ticks=40`/`120`). Uptime fields renamed `seconds_active/seconds_not_targetable/seconds_reset_window → ticks_*` with a migration; the proportional stamina schedule (`int(second / round_duration * 100)`) is unit-agnostic and unchanged. BatchSimulator is fully tick-native; RBS keeps a minimal second-internal loop and converts `×2` only at the persist/`GameEvent`/API boundary. Test bar: per-player uptime (`ticks_active + ticks_reset_window + ticks_not_targetable + dead-time`) must reconcile to exactly 1800 ticks; the `test_same_seed_produces_identical_event_log` determinism harness still holds; brittle exact-total assertions on the shifted BatchSim seeded tests are replaced with structural invariants. Rationale, rejected alternatives, and the two hard-to-reverse decisions live in the ADR-0001 Amendment (2026-05-15) and the re-resolved `seconds_*` ambiguity in CONTEXT.md.

### SIM-07 · RNG seed storage on `GameRound`

Add an `rng_seed` field (JSONField, null/blank) to `GameRound`. Before calling `_simulate_round`, capture `random.getstate()` and store it on the saved round. This makes every persisted round replayable: restoring the seed and re-running `_simulate_round` must produce an identical event log (covered by the existing `test_same_seed_produces_identical_event_log` test pattern). Required for the single-game replay UI (SIM-05).
- completed
- note: stored a 63-bit integer seed, not RNG state — `GameRound.rng_seed = BigIntegerField(null=True, blank=True)`, `random.seed(seed)` before `_simulate_round`; null = round predates SIM-07 / RBS round / not replayable, no backfill (ADR-0004); new `matches/` migration. `BatchSimulator.run(..., master_seed=None)` defaults to a per-run random master (independent OS-entropy generator), optionally pinned by tests; per-round int seeds derive from a deterministic `random.Random(master_seed)` seed chain (same master ⇒ same chain ⇒ same games). `_run_parallel` + `batch_round_worker` take an int seed and `random.seed(it)` — serial and parallel produce identical games for a given master seed (guaranteed, tested property). `replay_round(red, blue, seed, movement_ctx=None)` and `save_games` (list of ints) round-trip seeds; `_flush_to_db(..., rng_seed=...)` persists each. `views.py` `_serialize_seeds`/`_deserialize_seeds` deleted — per-round seeds are plain ints in the session/batch flow. `avg_seeds`/`outlier_seeds` are now `list[int]`. `score_round_worker` (score_averages path) intentionally unchanged / out of scope. Replay is faithful **only** while the round's rosters and map config are unchanged — the seed captures randomness, not world state; roster/map snapshot explicitly deferred (not SIM-07). Rationale: seed-not-state choice in `docs/adr/0005-rng-seed-not-state-for-replay.md`; domain terms in `CONTEXT.md`.

### SIM-08 · BatchSim team side alternation

When simulating multiple games between the same two teams, alternate which team plays red vs blue so each team gets an equal number of games on each side. In `_simulate_round` the roster order determines color; the caller (views, `save_games`, `run`) should flip argument order on every other game. Add a helper or flag rather than requiring every callsite to track the alternation manually. Enforce even alternation in `save_games` so league and batch results are not biased by map-side advantage.
- completed
- note: introduces **Side alternation** (CONTEXT.md / [ADR-0006](docs/adr/0006-batch-side-alternation.md)) — `BatchSimulator.run`/`_run_parallel` flip which **Team** plays the red **Side** by game index: game `k` is **flipped** iff `k` is odd (`k=0` canonical). The choice is a deterministic function of the index and **never consumes the RNG** (rejected seed-parity alternative — `getrandbits(63)` parity is ~50/50, never an exact split). The reproducible unit of a batch game is now the pair **(RNG seed, Orientation)**: `round_seeds` entries carry `flipped`, `avg_seeds`/`outlier_seeds` become `list[[int, bool]]` (JSON-safe through the Django session), `replay_round(red_roster, blue_roster, seed, flipped, movement_ctx=None)` gained a `flipped` arg, and `save_games(team_red, team_blue, seeds: list[tuple[int,bool]], n, *, arena_map=None)` takes (seed, flipped) pairs — extending the SIM-07 contract to "same seed + Orientation + rosters + map ⇒ identical game". `run()`/`_run_parallel` result keys `red_*`/`blue_*` are **unchanged in name but redefined as team-position keyed** (the team passed as `team_red`/`team_blue`, whichever Side it played); each game's result is de-flipped before bucketing so the existing per-team win% view/template is preserved. A new `side_advantage` sub-dict exposes the raw physical-side signal (`red_side_wins`, `blue_side_wins`, `side_ties`, `red_side_win_pct`, `blue_side_win_pct`, `avg_red_side_score`, `avg_blue_side_score`, `n`). `_flush_to_db` persists the **actual** sides for flipped games (`GameRound.team_red` = the team that physically played red; `PlayerRoundState.team_color` stays consistent) — **no new GameRound column, no migration** (actual-sides storage implicitly encodes Orientation for SIM-05 replay). Even alternation is guaranteed at the `run()` level over the full ordered sequence (even n ⇒ exact 50/50; odd ⇒ ±1); `save_games` does **not** re-alternate — it replays each carried (seed, flipped) pair faithfully (rejected re-deriving Orientation from the save-list index — would replay a seed under a different Orientation and break SIM-07), so the avg/outlier subset may be slightly side-skewed but this does not bias team/league stats because every saved round records its true sides and aggregates are team-position keyed. `parallel_worker.batch_round_worker` accepts the flipped flag and swaps red/blue precomputed rosters when flipped; serial and parallel produce identical team-position aggregates **and** identical `side_advantage` for a given master_seed (guaranteed, tested property). Batch view passes `side_advantage` into the template; `batch_simulate.html` renders a map-side-advantage panel. Scope is `BatchSimulator` `run`/`_run_parallel`/`save_games` + batch view/template only: RBS `simulate_match` is untouched (its per-Match colour swap is a separate mechanism; RBS removed in SIM-09), and `score_averages`/`score_round_worker` are deferred out of scope by the SIM-07 precedent. Rationale and rejected alternatives in [ADR-0006](docs/adr/0006-batch-side-alternation.md); domain terms (Side, Side alternation, Orientation, team-position keyed) in CONTEXT.md.

### MOVE-01 · Decouple cell movement from the `change_zone` action

Identified during the SIM-08 `--map` investigation. Cell movement only executes when the weighted action roll picks `change_zone` (`combat.plan_action` → `simulation.py` `ptype == "change_zone"` branch). But `change_zone` weight is **0** for commander, medic, and ammo at baseline, so on a real map those three roles **never move** — they sit on their spawn cells for the entire round while the two teams' bases are ~111 cells apart, collapsing engagements and resupply (measured: commander 1032 vs 9952 target, ammo 33 vs 3242). The nuke / critical-resource / score-broadcast goal overrides live *inside* `choose_goal_cell`, which is only reached from the `change_zone` branch, so they are also unreachable for these roles. Make movement-toward-goal happen every tick a player isn't doing something stationary (independent of the weighted action choice), so `choose_goal_cell` is consulted each tick and all roles advance with their team. Keep `hide`/stationary actions honoured. Re-baseline the Score Calibration Targets against the map model afterward (the current targets were tuned on the non-spatial 3-zone model). Prerequisite already done: STAT-03 Phase 1 multi-cell `speed` movement (`pathfinding.cells_to_move` + `astar_advance`) is wired so each move tick already traverses 5–10·`speed`% cells.
- completed
- note: introduces **Advance** / **only_move** / **Stationary** / **Movement trail** (CONTEXT.md / [ADR-0007](docs/adr/0007-movement-decoupled-from-action.md)) — movement is **decoupled** from the weighted **Action**. On the map path (`movement_ctx is not None` and `player.cell_row is not None`) every non-**Stationary** player **Advances** toward their **Goal cell** every tick (`choose_goal_cell` consulted every tick), regardless of the chosen Action — fixing the zero-`change_zone`-weight Commander/Medic/Ammo who never left spawn. **Stationary** (no Advance) = `is_hiding` True OR chosen action == `capture_base` (anchored to base); every other Action Advances while it acts. The legacy `change_zone` Action is renamed **`only_move`** (same action-array index 1; per-role weight tuning preserved — "Option B"/option (c)); it no longer gates movement and now means a single **2× step** (`cells_to_move(speed) * 2` cells in one `astar_advance`) with no other deliberate effect. Each movement `GameEvent(event_type="movement")` stores a compact **start cell + end cell + timestamp** (not the route), emitted only when the cell actually changed; `BatchSimulator` accumulates a transient `PlayerState.movement_trail` list (no DB column, **no migration**) flushed to the same compact events by `_flush_to_db` only when a round is saved — the exact intermediate route is recomputed on demand at replay via deterministic A* `start→end`. Pure behavioural: all goal/path caching + per-tick A* perf work is explicitly deferred to **MOVE-02**. **3-zone fallback unchanged** (`movement_ctx is None`): the old weighted `_change_zone` still runs on the `only_move` roll (MAP-06 pattern); always-on Advance + 2× apply on the map path only. Advance/A* consume no RNG, so the SIM-07/SIM-08 contract holds in *form* (same seed + Orientation + rosters + map ⇒ identical game, serial == parallel), but games differ from pre-MOVE-01 (expected; ADR-0004, no backfill). **Score Calibration Targets untouched** — re-baselining the map model is deferred to a separate post-MOVE-01 measurement/discussion pass. Rationale and rejected alternatives in [ADR-0007](docs/adr/0007-movement-decoupled-from-action.md); domain terms (Advance, only_move, Stationary, Movement trail, Goal cell) in CONTEXT.md.

### MOVE-02 · Goal-keyed A* path-commitment cache (BatchSim only)

Identified during the SIM-08 `--map` investigation. `astar_advance`/`astar_next_step` run a complete from-scratch A* over the full passable-cell graph (~3,700 cells on San Marcos) **every move tick**, just to take one step's worth of cells — no path memoization. Measured cost: **2,752 ms/round with a map vs 354 ms/round on the 3-zone fallback (~8×)**, the bulk of the "significantly longer with a map" slowdown. Cache the chosen **Goal cell** and its A* route per player; re-step along the cached route each move tick (**Path commitment**, CONTEXT.md) and recompute only when (a) the goal changes, (b) the cache is exhausted, (c) the next route cell is blocked, or (d) the player is knocked off-path (Down/respawn → cache cleared). `choose_goal_cell` still runs **every tick** (it does no A* — only the *route* is cached, not goal selection). An `only_move` tick consumes `2×steps` along the *same* committed route — it is **not** a recompute trigger. **Scope: `BatchSimulator` only** — `ResourceBasedSimulator` is DB-bound (A* is not its bottleneck) and is removed by the immediately-following SIM-09, so it deliberately keeps per-tick `astar_advance`. The cache lives on a transient `PlayerState` field (no DB column, no migration).

**Contract: *internal* determinism only.** A grid has many equal-cost shortest paths; the pre-MOVE-02 per-tick recompute could re-pick among them ("path wobble"), a goal-keyed cache commits to one route — so MOVE-02 **changes which equal-cost route is walked** and therefore produces different seeded games than pre-MOVE-02. Both behaviours are fully deterministic (`astar_path` heap orders on int tuples, PYTHONHASHSEED-independent), so the SIM-07/SIM-08 contract — same seed + Orientation + rosters + map ⇒ identical game, serial == parallel, faithful Replay — holds *under caching*. MOVE-02 is **not** identical to pre-MOVE-02 games; the earlier "no behavioural change / identical games" wording in this entry was contradictory and is **superseded by [ADR-0008](docs/adr/0008-path-commitment-via-goal-keyed-cache.md)**. The seeded-game delta is absorbed by the **already-pending post-MOVE-01 Score Calibration re-baseline** — MOVE-02 creates **no new** re-baseline obligation.

The `hold`/overwatch idea is split out to **MOVE-03**; goal-recompute throttling (a separate *behavioural* perf lever) is parked as **MOVE-04**. Both are explicitly out of MOVE-02 scope.
- completed
- note: introduces **Path commitment** (CONTEXT.md / [ADR-0008](docs/adr/0008-path-commitment-via-goal-keyed-cache.md)) — a goal-keyed A* route cache, **`BatchSimulator` only**. New `pathfinding.astar_advance_cached(player, current, goal, adj, steps, elevation_data=None)` re-steps a committed route; recompute iff cache None/empty, live goal ≠ cached goal, or next cached cell ∉ `adj`. Cache is a transient `PlayerState._path_cache: Optional[Tuple[Tuple[int,int], list]] = None` — `(cached_goal, remaining_cells)` — **no DB column, no migration** (mirrors `movement_trail`); default `None` so it never becomes a ctor arg and never crosses the parallel-worker process boundary; fresh per-round `PlayerState` starts uncached, and every BatchSim Down/respawn/missile/nuke life-loss site clears it to `None` (knocked off-path → recompute). `BatchSimulator._move_player_in_memory` calls `astar_advance_cached` instead of `astar_advance`; `astar_advance`/`astar_next_step` are **unchanged** (RBS + tests still use them). `choose_goal_cell` is still consulted **every tick** (no A* in goal selection — only the route is cached); the `only_move` 2× multiplier consumes `2×steps` from the **same** committed route (not a recompute trigger). Cache re-stepping consumes **no RNG**, so the SIM-07/SIM-08 contract holds *in form* (same seed + Orientation + rosters + map ⇒ identical game, serial == parallel, faithful Replay), but MOVE-02 deliberately changes which equal-cost route is walked ⇒ seeded games differ from pre-MOVE-02 (expected; ADR-0004, no backfill). The contract is **internal determinism, not identity to pre-MOVE-02** — the old "no behavioural change / identical games" wording above was contradictory and is **superseded by ADR-0008**. The route-commitment delta is **folded into the already-pending post-MOVE-01 Score Calibration re-baseline** (no new obligation). `hold`/overwatch split to **MOVE-03**; goal-recompute throttling parked as **MOVE-04**. Rationale and rejected alternatives in [ADR-0008](docs/adr/0008-path-commitment-via-goal-keyed-cache.md); domain term (Path commitment) in CONTEXT.md.

### MOVE-03 · Hold action with overwatch effect

Split out of the original MOVE-02 scope (it required significant action-selection / event-processing changes unrelated to the path cache). Add a 9th **Action** slot, `hold`, with an overwatch effect: a holding player **automatically fires at an enemy entering its LoS** (and is more likely to react to movement through its sight). This is **distinct from the CONTEXT.md "Reaction shot"** (which is a post-**Tag**/post-**Miss** retaliation) — overwatch is a *pre-emptive* auto-fire triggered purely by an enemy entering LoS, with no prior Shot against the holder. Needs **weight redistribution across all 5 roles** (the new slot must take weight from existing actions while keeping every weight ≥ 0 and the role baselines coherent), new `GameEvent` semantics for the overwatch shot, and `hold` likely joins the **Stationary** set (no Advance while holding, like `hide`/`capture_base`). Edge-case test requirement (carried from the original text): a player moving **"through"** a holder's LoS in a single multi-cell Advance must still trigger **≥1** overwatch shot (the traversal crosses LoS even if neither the start nor end cell is visible). Own ADR + a Score Calibration re-baseline (behavioural change).
- completed
- note: introduces **Hold** / **Overwatch** / **Overwatch shot** (CONTEXT.md / [ADR-0009](docs/adr/0009-hold-overwatch.md)) — a 9th **Action** `hold` at array **index 8** (`combat._ACTION_IDX`/`_CHOICES` + the `weights.py` baseline now 9 slots `[70,30,0,0,0,0,0,0,0]`); the movement-relevant Action list everywhere becomes `tag, only_move, hide, capture_base, use_special, resupply_ally, missile_player, request_resupply, hold`. A `hold` roll puts the player in **Overwatch** via a transient `PlayerState.is_holding` (mirrors `is_hiding`) — **no DB column, no migration** (like `_path_cache`/`movement_trail`); it **carries over** (player stays in Overwatch) until a non-`hold` Action is rolled or a Down/respawn (`BatchSimulator._record_down` clears it, so every life-loss site is covered structurally — same hook as the path cache). `hold` joins the **Stationary** set (no **Advance** — added to the `_advance_player` predicate alongside `is_hiding` / `capture_base` in **both** simulators). Per-role `hold` weight (weights.py): Medic **0**; Ammo **+20** (from `tag_player`); Scout **+10**, Heavy **+20**, Commander **+10** (from `only_move`); all weights stay **≥ 0** (`random.choices` rejects negatives). Numbers are tunable — calibration deferred. **Overwatch resolution is `BatchSimulator`-only**: the traversed cells come from `astar_advance_cached` exposing the popped committed-route cells on a transient `PlayerState._last_step_cells` ([ADR-0008](docs/adr/0008-path-commitment-via-goal-keyed-cache.md) **Path commitment**), the tick loop collects (no RNG) one Overwatch `tag_attempt` per holder whose LoS a mover's traversed cells cross (gated by `shot_cooldown` + `last_shot_time` + `final_shots > 0` + holder active; ≤1/holder/tick except rapid-fire Scout) and feeds the **existing** `_resolve_tag_attempts` path so Follow-up / Reaction / RNG are reused; the **Overwatch shot** reuses `event_type="tag"`/`"miss"` + `metadata={"overwatch": true}` so scoring / MVP / accuracy paths are unchanged. RBS treats `hold` as a Stationary **no-op** (dead code, removed by SIM-09 — mirrors the MOVE-02 RBS-scoping precedent; no RBS≡BatchSim identity contract exists). Determinism: the SIM-07/SIM-08 *internal* contract is preserved in form (collection + LoS-cross check + carry-over consume **no RNG**; only the resulting shot does, via the existing deterministic tag path — same seed + Orientation + rosters + map ⇒ identical game, serial == parallel, faithful Replay), but seeded games differ from pre-MOVE-03 (new slot reweights every role; Overwatch adds Tags/Downs — expected, ADR-0004, no backfill). The behavioural delta folds into the **single already-pending post-MOVE-01 Score Calibration re-baseline** (same as MOVE-02 — **no new** obligation; longer-term intent is to tune weights/system to converge map-model scores toward the real San Marcos field targets, which are **not** rewritten). Rationale and rejected alternatives in [ADR-0009](docs/adr/0009-hold-overwatch.md); domain terms (Hold, Overwatch, Overwatch shot, the Reaction-shot contrast) in CONTEXT.md.

### MOVE-04 · Goal-recompute throttling

Recompute the **Goal cell** every *N* ticks instead of every tick (`choose_goal_cell` is currently consulted every tick — MOVE-01 — and MOVE-02 caches only the *route*, not goal selection). This is a **behavioural** perf lever, **not** a free optimisation: staler goals change pursuit/positioning and therefore seeded outcomes, requiring its own Score Calibration re-baseline. **Explicitly out of MOVE-02 scope** (MOVE-02's path cache leaves per-tick goal selection intact). Open this **only if path caching alone proves insufficient** for the map-mode perf target.
- completed
- note: introduces **Goal commitment** (CONTEXT.md / [ADR-0010](docs/adr/0010-goal-commitment-via-tick-cadence-throttling.md)) — tick-cadence throttling of `choose_goal_cell`, **`BatchSimulator` only**. The cascade splits into a *reactive* layer that **still fires every tick** (steps 0/1/1b — MECH-04 nuke-reaction, critical-resource lives/shots ≤ 30% → seek medic/ammo, score-broadcast `seek_medic`) and a *steady-state positioning* layer that is held under Goal commitment between recomputes (steps 2/3/4 — `_goal_from_action`, `_goal_from_role`, enemy-base default), cadence `GOAL_RECOMPUTE_PERIOD_TICKS = 4` ticks (2 s, `time_constants.py`). The committed destination lives on a transient `PlayerState._committed_goal: Optional[tuple[tuple[int,int], bool, int]] = None` (cell, `from_action_driven` flag, `expires_at_tick`) — **no DB column, no migration** (mirrors `_path_cache`/`movement_trail`); default `None` so it never becomes a ctor arg and never crosses the parallel-worker process boundary; fresh per-round `PlayerState` starts uncommitted. **Force-recompute triggers** beyond cadence expiry: {no prior commitment, Goal cell reached, exiting **Stationary** (hide → not-hide, hold → not-hold — stationary players don't Advance, so re-engaging movement re-asks the cascade), a reactive override firing this tick (the committed steady-state goal is dropped and re-derived once the reactive condition clears), **Down**/respawn **iff** the committed goal came from action-driven targeting (tag / missile / resupply / hide) — positioning goals (role-positioning, enemy-base default, `only_move`-driven) survive a Down because the player keeps **Advancing** through the **Respawn cooldown** and the positioning intent is still tactically valid; the `from_action_driven` flag on `_committed_goal` is the source marker}. **Phase is expiry-based** (`expires_at_tick = tick + N` set per-player on each recompute), **not** `tick % N == 0` — load staggers naturally per-player without hashing and the synchronised every-`N`-ticks A* spike is avoided. The route cache (**Path commitment**, MOVE-02 / [ADR-0008](docs/adr/0008-path-commitment-via-goal-keyed-cache.md)) is invalidated **iff a Goal commitment recompute changes the Goal cell** — re-picking the same cell leaves `_path_cache` untouched (the two commitments are separate per-player slots and the route invariant follows the goal, not the recompute event). RBS keeps per-tick goal selection (DB-bound, removed by SIM-09 — same MOVE-02/MOVE-03 RBS-scoping precedent). Determinism: the SIM-07/SIM-08 *internal* contract holds in form (the cadence schedule and the source marker consume **no RNG**; only the existing reactive overrides and the steady-state cascade do, both unchanged — same seed + Orientation + rosters + map ⇒ identical game, serial == parallel, faithful **Replay**), but seeded games differ from pre-MOVE-04 (staler goals deliberately shift pursuit/positioning — expected, [ADR-0004](docs/adr/0004-simulation-data-is-disposable.md), no backfill). The behavioural delta folds into the **single already-pending post-MOVE-01 Score Calibration re-baseline** (same as MOVE-02 / MOVE-03 — **no new** obligation; longer-term intent to tune weights/system toward the real San Marcos field targets, which are **not** rewritten). Rationale and rejected alternatives (per-role N, map-size-scaled N, whole-cascade throttle including reactive overrides, global `tick % N == 0` phase, source-blind Down-clear, reusing `_path_cache[0]` for commitment) in [ADR-0010](docs/adr/0010-goal-commitment-via-tick-cadence-throttling.md); domain term (Goal commitment) and the superseded "Goal cell is recomputed every tick" ambiguity in CONTEXT.md. Map-mode perf measurement (cells/tick recompute ratio, ms/round delta vs MOVE-02 baseline) is in the PR body.

### SIM-09 · Replace RBS with BatchSim in all views + pass map through

Once SIM-06–08 are complete, replace `ResourceBasedSimulator()` with `BatchSimulator()` in `matches/views.py` for both the `create_match` and `create_single_round` views. Each view runs a single round (or two for a full match), captures the seed, and calls `_flush_to_db` / `_flush_match_to_db` immediately. After migration, `ResourceBasedSimulator` is dead code and should be removed.

**Critical:** currently no BatchSim callsite passes a map. Every BatchSim round ever run — batch simulate page, save-games, `score_averages` command — used the 3-zone fallback regardless of what map the user selected. This means BatchSim has been simulating a fundamentally different game than RBS (no A* movement, no LOS targeting, no spawn cells, no elevation). As part of this migration, all BatchSim callsites must accept and forward the `arena_map` argument so map-aware simulation is consistent everywhere.
- completed
- note: consolidates onto a **single engine** (`BatchSimulator`) — resolves [ADR-0002](docs/adr/0002-two-simulation-engines.md) (superseded, 2026-05-20). `BatchSimulator.simulate_match(team_red, team_blue, match_type="friendly", *, arena_map=None) -> Match` and `simulate_single_round_detailed(team_red, team_blue, *, arena_map=None) -> GameRound` are new — both `@transaction.atomic` so a half-saved Match cannot exist (preserves the M-2 invariant). The per-Match colour swap is mirrored **exactly** from the removed RBS: round 2 is run with the team arguments reversed and `match.red_round2_points = round2.blue_points` (because `team_red` physically played blue in round 2; the stored `team_red`/`team_blue` on each `GameRound` is the team that physically played that side). **Distinct from SIM-08 Orientation**, which remains a batch-only (`run` / `save_games`) mechanism — the two never interact. Each round draws its own fresh 63-bit seed via `random.Random().getrandbits(63)` (per-round, independent — the two rounds of one Match have different seeds), persisted to `GameRound.rng_seed`. `BatchSimulator.ROUND_TICKS = TICKS_PER_ROUND` is now a class attribute (patchable to a small value for fast tests; replaces the removed `ResourceBasedSimulator.ROUND_TICKS`). `BatchSimulator._flush_to_db` is extended with `match`, `round_number`, `arena_map`, `zone_size` kwargs — both `arena_map` and `zone_size` now persist onto `GameRound` for **every** path (batch save, single round, full match), closing the pre-SIM-09 gap PLAN named: every `BatchSimulator` callsite (`run` / `save_games` / `simulate_batch` view / `score_averages` command) previously ran the 3-zone fallback regardless of the user's map selection. `matches/sim_helpers/map_loader.py` is **new** — the five former `ResourceBasedSimulator.@staticmethod` helpers (`_load_map_context`, `_resolve_map_data`, `_build_movement_ctx`, `_zone_from_cell`, `_build_spawn_assignments`) are extracted as free functions (`load_map_context`, `resolve_map_data`, `build_movement_ctx`, `zone_from_cell`, `build_spawn_assignments` — drop the underscore prefix); behaviour and signatures are unchanged, every callsite (BatchSim, `score_averages`, tests) is updated. `BatchSimulateForm` gains an optional `arena_map` `ModelChoiceField` (same `_maps_with_confirmed_config` queryset as `MatchSetupForm` / `SingleRoundSetupForm`); the `simulate_batch` view stashes the selected `arena_map_id` in the session alongside the seeds; `save_batch_games` / `_run_save_job` loads the `ArenaMap` and threads it through to `save_games(arena_map=...)`. `create_match` and `create_single_round` views: `ResourceBasedSimulator()` → `BatchSimulator()` (call shape unchanged — `simulate_match` / `simulate_single_round_detailed` accept the same args). **`class ResourceBasedSimulator` is deleted** along with `matches/tests/test_sim_core.py` wholesale (its mechanics are covered by `test_batch_sim.py` + the `sim_helpers` unit tests, per [ADR-0002](docs/adr/0002-two-simulation-engines.md), now superseded); the remaining RBS end-to-end tests in `test_map.py` / `test_time01_tick_native.py` / `views_tests.py` are converted to `BatchSimulator`. **No new DB column, no migration.** Behavioural delta: view-mode rounds shift from RBS mechanics to BatchSim mechanics — **Path commitment** ([ADR-0008](docs/adr/0008-path-commitment-via-goal-keyed-cache.md)), **Hold/Overwatch** ([ADR-0009](docs/adr/0009-hold-overwatch.md)), and **Goal commitment** ([ADR-0010](docs/adr/0010-goal-commitment-via-tick-cadence-throttling.md)) are now active on every `create_match` / `create_single_round` / batch / save flow (previously RBS-only no-ops or missing). The delta **folds into the single already-pending post-MOVE-01 Score Calibration re-baseline** (alongside MOVE-02 / MOVE-03 / MOVE-04) — **no new obligation**, no separate re-baseline. Rationale and the closing supersession note in [ADR-0002](docs/adr/0002-two-simulation-engines.md) (now superseded/completed); domain-language cleanup ("two simulation engines" flagged-ambiguity entry) in CONTEXT.md.

---

---

## Phase 4 — Analytics & Review

Surfaces the data already being collected. No new simulation work required.

### RES-01 · Accuracy % on round detail (quick win)

Add `accuracy_pct` as a `@property` on `PlayerRoundState`: `tags_made / (tags_made + shots_missed) × 100`. 
Display in the currently-blank Accuracy % column on `/matches/game-round/<id>/`. Covered by a unit test.
- completed: already implemented as `PlayerRoundState.get_accuracy` (`matches/models.py:648-654`) — `@property` returning `round(tags_made / (tags_made + shots_missed) * 100)` with `0` fallback when `total == 0`; rendered in the Accuracy column of `game_round_detail.html` for both red (line 115) and blue (line 207) tables (PLAN's "currently-blank" claim was stale); covered by `test_mvp.py:34-52` (0/0, 10/0, 75/25 regimes); also consumed by `sim_helpers/score_calculator.py:29` for MVP. Property name divergence (PLAN spec `accuracy_pct` vs code `get_accuracy`) deferred — rename was offered and declined; `get_accuracy` stays as-is.

### RES-02 · SP timeline chart

Chart SP over time per player on `/matches/game-round/<id>/events/`, sourced from `GameEvent` rows. 
Spending events shown as downward spikes. SP cap (99) shown as a reference line.
- completed
- note: server contract is a new `metadata["sp"]: int ∈ [0, 99]` key — the actor's **post-event** `final_special` — written by every SP-changing emit site: `tag` (3 sites in `matches/simulation.py` — main ~L1849, reaction ~L2010, follow-up ~L2136), `missile` (1 site in `matches/simulation.py` ~L2228), `special` (5 sites in `matches/simulation.py` — `_use_special` commander/scout/medic/ammo activation ~L2265/2284/2303/2328 and `_complete_nuke` detonation ~L2345), and `base_capture` (1 site in `matches/sim_helpers/combat.py` ~L557). **Presence is keyed on event_type, not on whether SP actually changed for that specific actor** — heavy `tag` rows, heavy `missile` rows, and nuke-detonation `special` rows all carry `sp` at the unchanged value (same rule as the existing `attacker.role != "heavy"` SP-increment guards). `base_capture` events' former `metadata["special_points"]` is **renamed to `"sp"`** (no alias retained). **No view, serializer, or model change** — `GameEvent.metadata` is a `JSONField`; `matches/views.py::game_round_events` already passes `{"meta": e.metadata or {}}` through to `events_data` and `GameEventSerializer` serialises `metadata` verbatim, so the new key reaches the client for free. **No DB migration, no backfill** ([ADR-0004](docs/adr/0004-simulation-data-is-disposable.md)). Frontend lives entirely in `laserforce_simulator/templates/matches/game_round_events.html`: a new `chart-sp` `<canvas>` row below the existing Shots / Lives / Points chart row, 10 per-player stepped lines (red players in shades of red, blue players in shades of blue) plus 2 per-team-average overlay datasets (toggle `sp-filter-team-averages`), y-axis fixed `[0, 99]`, dashed reference line at y=99 drawn via the existing `_overlay_plugin` Chart.js plugin pattern (no new plugin). Filter dropdowns (`sp-filter-teams`, `sp-filter-roles`, `sp-filter-players`, `sp-filter-team-averages`) mirror the existing `event-type-filters` / `player-filters` DOM structure exactly for visual consistency. The chart-side `spSeries[playerId] = Array<{sec, sp}>` is built once at chart-init time by walking chronological `events_data` and reading `ev.meta.sp` on SP-changing rows (synthetic `{sec: 0, sp: 0}` prepended per player to start each stepped line at the origin); no global cache outlives chart construction. **SIM-05 playback scoreboard** (`pb-sb-red` / `pb-sb-blue`) gains an `SP` column appended at the end of the existing column set (existing selectors unchanged), driven by `pbPlayers[id].sp` — initialised to `0` in `pbReset` for every player, advanced inside `pbApply` when `ev.type ∈ {"tag", "missile", "special", "base_capture"}` and `typeof ev.meta.sp === "number"` (otherwise left unchanged). **No client-side SP cost reconstruction** — the chart and the playback scoreboard both read `meta.sp` directly; SP cost rules stay server-side. Tests pin the server contract in a new `matches/tests/test_res02_sp_metadata.py` (presence + `isinstance(int)` + `0 <= sp <= 99` across every MUST-carry emit site, absence across `miss` / `resupply_ammo` / `resupply_lives` / `combo_resupply` / `movement` / `elimination`, and `"special_points"` absent on every `base_capture`) and extend `TestM1EventLogWindowing` in `matches/tests/views_tests.py` for the same assertions at the view layer through `events_data`. No JS tests are added — matches the precedent set by the existing three charts. **Pre-existing bugs fixed alongside RES-02 (user-requested during code review):** (1) the existing **Shots / Lives / Points** charts now use the same stepped-line format as the SP chart (`stepped: true`, `pointRadius: 0`, no rolling-average smoothing — the `smooth()` 3-point box filter was dropped) so per-event resource changes read as discrete steps; (2) the elimination / special / nuke vertical-overlay toggles on those three charts now actually render — they were silently broken because the old code assigned the plugin to `chart.options.plugins[id]` (which is the plugin-options map, **not** registration), so the plugin's `afterDraw` was never invoked. Fixed by inline-registering `_overlay_plugin` via the Chart.js v4 constructor `plugins:` array (the only supported chart-local registration path); `drawOverlays` now mutates a closure-captured `overlayEvents = [{sec, kind, label}]` (one entry per overlay event, not just a list of seconds) and calls `chart.update()`. The plugin reads the per-entry `kind` to pick a distinct colour from `OVERLAY_KIND_STYLE` (red for `elimination`, orange for `special`, purple for `nuke_detonated`) and renders the per-entry `label` as a rotated player-name annotation at the top of each vertical line — eliminated player for eliminations, special user for special activations and nuke detonations. The toggle-label colours mirror `OVERLAY_KIND_STYLE` so the legend is readable without hovering the chart. The SP cap reference line had the same registration bug pre-fix and is now also inline-registered. The "Nukes" toggle additionally needed a client-side disambiguator — the simulator emits nuke detonation as `event_type="special"` with description `"… nuke detonates"`, so `drawOverlays` distinguishes detonation from activation by description match. **Still flagged out of scope and tracked separately:** the resource-reconstruction logic at ~L545 / ~L779 (chart-shots / chart-lives / chart-points cumulative arithmetic) and `game_analysis.py:186` still compare `ev.t === 'missile_hit'` / `e.event_type == "missile_hit"` against the simulator's actual `event_type="missile"` (`simulation.py:2228`); the substring `passes()` filter on the timeline still matches, but the chart strict scanners never count missile-driven resource changes. Discovered during the RES-02 grill; not fixed here. Seam contract in `.claude/worktrees/res-02-seam-contract.md`; GameEvent metadata paragraph in [`matches/CLAUDE.md`](laserforce_simulator/matches/CLAUDE.md) extended with a one-line `metadata["sp"]` note. No ADR, no CONTEXT.md change (SP already defined).

### RES-03 · Missile usage log

Filter event log by type `missile`. Each row: timestamp, actor role, target role, result. 
Friendly fire highlighted. Summary: total fired, total hit, efficiency %.
- completed
- note: server contract splits the legacy single `event_type="missile"` row (resolution-only, emitted at `simulation.py:~L2228` pre-RES-03) into **two** event types — `event_type="locking"` at the fire tick (the **Locking event**, CONTEXT.md), carrying `metadata = {"actor_role", "target_role"}`, and `event_type="missiled"` at the resolution tick (the **Missiled event**, CONTEXT.md), carrying `metadata = {"result": "hit"|"miss", "friendly_fire": bool, "actor_role", "target_role"}`; the legacy `event_type="missile"` value is **removed from production** (no alias retained, no backfill — [ADR-0004](docs/adr/0004-simulation-data-is-disposable.md)). All four `missiled` keys are **required** (presence + type asserted by the spec on every emit site); `actor_role` / `target_role` let the missile-log row render both columns without a DB join, `result` distinguishes the hit/miss branch, and **`friendly_fire` is server-emitted** as `bool` (true iff `actor.team_color == target.team_color`) — never derived view-side, mirroring the RES-02 single-source contract for `metadata["sp"]`. The seam helper change is a new `emit_event: Callable | None` kwarg on `start_missile_lock` in `matches/sim_helpers/combat.py`, mirroring the `attempt_resupply` / `capture_base` precedent exactly (a callable the simulator passes in; helpers don't import the simulator); resolution stays on `BatchSimulator._complete_missile`, which now writes `event_type="missiled"` instead of `"missile"` and computes `friendly_fire` from team-colour equality before appending the event. **Down/respawn invariant:** if the locking actor is **Down**ed before resolution, **no `missiled` event fires** (the `locking` event remains in the log) — the missile analogue of the MECH-05 nuke-cancellation rule, structurally enforced by clearing the actor's pending-lock state on every life-loss site via the shared `BatchSimulator._record_down` helper (the same hook that drops `_path_cache` and `is_holding`, so every life-loss site is covered without per-site review). View / URL / template are all new: the URL pattern `/matches/game-round/<int:round_id>/missile-log/` (URL name **`missile_log`**) wires a new view in `matches/views.py` that filters `GameEvent.objects.filter(game_round_id=..., event_type="missiled")` (excludes `locking` and `tag`), passes the queryset to `templates/matches/missile_log.html` which renders one row per Missiled event with mm:ss via the standard `÷2` filter at the HTML boundary (TIME-01 — never seconds internally), and computes the header summary **view-side** (no model property): `fired` = count of `missiled` events, `hit` = count where `result == "hit"`, `efficiency %` = `hits / fired × 100` (friendly-fire hits count toward `hit` — the missile landed; the FF flag carries the qualitative distinction, not the quantitative one). Friendly-fire rows render with a CSS class containing the substring `friendly-fire` so the row is visually distinguishable (locked-in marker; the spec checks the substring, not a specific class name). **Pre-existing bug closed alongside RES-03 (RES-02-deferred):** `game_analysis.py:186` and the `chart-shots` / `chart-lives` / `chart-points` strict scanners in `templates/matches/game_round_events.html` previously compared `ev.t === 'missile_hit'` / `e.event_type == "missile_hit"` — a literal the simulator never emitted (actual `event_type` was `"missile"`) — so missile-driven resource changes were silently missing from those three charts; RES-03 scrubs the `"missile_hit"` literal alongside the `event_type="missile"` rename in the same scope (one bug, one cleanup). The frozen spec at `matches/tests/test_res03_missile_log_spec.py` (15 tests) is the contract; bug-class coverage spans Down/respawn (tests #8 + #9 — locking actor eliminated before resolution emits no `missiled`; resolution clears pending-lock so a subsequent Down doesn't re-fire), tick-vs-seconds (tests #6 + #7 — timestamps are int ticks in `[0, ROUND_TICKS]`; the template renders tick 124 as `01:02`, not `02:04`), CLI/flag wiring (test #10 — the `missile_log` URL renders only `missiled` rows and hides `tag` rows), doc/code consistency (tests #13 + #14 — no `"missile_hit"` and no `event_type="missile"` literal in any production `.py` / `.html` after RES-03; CONTEXT.md defines `Locking event`, `Missiled event`, `Friendly fire`), and seeded-determinism (test #15 — same seed ⇒ identical `locking + missiled` subset across runs; currently `xfail` at spec-freeze time pending empirical `ROUND_TICKS` tuning, re-enable after the first green pass). New ADR: [ADR-0011](docs/adr/0011-missile-event-split.md) records the event-split decision, the rejected alternatives (single `"missile_hit"` rename without split; one event_type with a `metadata["phase"]` discriminator; view-side friendly-fire derivation; excluding FF from the hit count), and the persisted-event delta (zero rows backfilled — [ADR-0004](docs/adr/0004-simulation-data-is-disposable.md); old rounds with the legacy `event_type="missile"` rows remain in dev/test DBs and simply won't show up in the new missile-log view, which filters by `"missiled"`). The two-event split shifts seeded games only insofar as `locking` rows now appear in the log between lock-start and resolution — game *mechanics* are unchanged, so the SIM-07 / SIM-08 internal-determinism contract holds in form and no Score Calibration re-baseline is triggered. GameEvent metadata paragraph in [`matches/CLAUDE.md`](laserforce_simulator/matches/CLAUDE.md) extended with the locking/missiled split note alongside the existing RES-02 SP-snapshot paragraph; URLs block adds the `missile_log` endpoint.

### RES-04 · Zone/cell movement heatmap (post-MAP)

After Phase 1, players have cell coordinates. Aggregate time-in-cell across a round and render as a heatmap overlay
on the map image. Filter by player. Per-zone time-in-zone bar chart as a simpler fallback before full map integration.
- completed
- note: ships **two surfaces** from one persisted per-round snapshot — the per-round overlay at `/matches/game-round/<int:round_id>/heatmap/` (URL name `movement_heatmap`, view `matches/views.py::movement_heatmap`, template `templates/matches/movement_heatmap.html`) and a multi-round aggregate **inside the existing map editor** as a third mode toggle alongside Zones & Bases and Sight Lines (`templates/maps/map_editor.html`, driven by the new JSON endpoint `/maps/<int:map_id>/heatmap-data/`, URL name `map_heatmap_data`, view `core/views.py::map_heatmap_data`). The persisted form is a new `GameRound.cell_occupancy_json` (`JSONField(null=True, blank=True, default=None)`) added by migration `matches/migrations/0026_gameround_cell_occupancy_json.py` (single `AddField`, dependency `0025_alter_gameevent_event_type`); **no backfill** — pre-RES-04 rows stay `NULL`, mirroring the `GameRound.rng_seed` precedent ([ADR-0004](docs/adr/0004-simulation-data-is-disposable.md)). JSON shape is `{str(player_id): {"r,c": int_ticks}}` (both key tiers are JSON-native strings — `str(player_id)` outer, the same `f"{r},{c}"` comma-string used by `sight_data` inner — to avoid the int↔str coercion footgun on the read side); cells whose reconstructed float accumulator rounds (`round()`, banker's) to `0` are **omitted**, so the per-player dict is **sparse** (`{}` is a valid value for a player who never moved off spawn and was eliminated at tick 0). Reconstruction lives in a new pure free function `matches/sim_helpers/cell_occupancy.py::reconstruct_cell_occupancy(movement_trail, spawn_cell, round_ticks, eliminated_at, adj, elevation_data=None) -> dict[tuple[int,int], int]` — **pure Python, no Django imports, no I/O, consumes no RNG**, returns tuple-keyed/int-valued dict (caller stringifies for JSON). Algorithm: walk the **Movement trail** (CONTEXT.md) with a float accumulator `accum`, a `cursor_cell` starting at `spawn_cell`, and a `cursor_tick` starting at `0` capped by `end_tick = min(round_ticks, eliminated_at)`; for each `(start_cell, end_cell, ts)` Advance entry, credit the stationary slice `[cursor_tick, ts)` to `cursor_cell` (which equals `start_cell` by the chain invariant), then credit the **Advance**'s 1 tick split evenly across `1 + len(astar_path(start, end, adj, elevation_data))` cells (the `+1` is the start cell; the route returned by `astar_path` excludes start and includes end), advance the cursor `cursor_cell = end_cell`, `cursor_tick = ts + 1`, and finally credit the trailing stationary slice `[cursor_tick, end_tick)` to `cursor_cell`; cast each accumulator to `int` via `round()` and drop zeros. Sum-over-cells of the integer output may deviate from the float total by at most `len(cells_touched) / 2` ticks (rounding slack); tests assert the inequality, never an exact total. `_flush_to_db` integration sits in `BatchSimulator._flush_to_db` (`matches/simulation.py`) **immediately after** the existing per-player `movement` `GameEvent` flush block and **before** the final `return game_round`, **gated on `movement_ctx is not None`** so map-less rounds leave `cell_occupancy_json` null (no map ⇒ no A* adjacency ⇒ no reconstruction); spawn cell is `p.movement_trail[0][0]` when the trail is non-empty else `(p.cell_row, p.cell_col)`; the snapshot is written via a **second** `game_round.save(update_fields=["cell_occupancy_json"])` (the earlier `save()` in `_flush_to_db` triggers winner calculation, intentional and cheap). `movement_ctx.get_adjacency()` and `movement_ctx.elevation_grid` are read off the **existing** `MapContext` accessors — **no new `MapContext` accessor** is added, and **no new `_flush_to_db` kwarg** (`movement_ctx` is the already-existing SIM-09 kwarg). **Movement `GameEvent` rows are unchanged by RES-04** — RES-04 reads the in-memory `PlayerState.movement_trail` to produce a per-round snapshot; it does **not** change the event log shape (movement events still record the compact start-cell + end-cell + timestamp triples as RES-04 found them — MOVE-01). Filter strategy is **asymmetric by surface**: the single-round view is **client-side** — the full per-player JSON is rendered into the page via `{{ cell_occupancy_json|json_script:"cell-occupancy-data" }}` and a small JS shim sums per-cell across the players the player/role/team dropdowns select then re-paints the canvas (no server round-trip per filter change; keeps the seam narrow); the multi-round editor view filters **server-side** on `team_color` only (`GET /maps/<id>/heatmap-data/?zone_size=<n>&team_color=red|blue`, joining `GameRound.cell_occupancy_json` against `PlayerRoundState.team_color` to drop non-matching players, then summing the remaining `"r,c"` entries — response shape `{"cell_occupancy": {"r,c": int}, "zone_size": int, "rows": int, "cols": int, "round_count": int}` with cells whose final sum is `0` omitted; `team_color` is the **only** server filter — the editor view does not expose per-player or per-role dropdowns). Map-less rounds render a **"No map — heatmap unavailable"** notice (DOM id `heatmap-no-map-notice`) in the single-round template; the PLAN.md "per-zone time-in-zone bar chart fallback" wording is **dropped** because MAP-01..09 are complete (every match path can attach an `ArenaMap`; the 3-zone fallback survives only as a compatibility shim for rounds the user explicitly creates without a map — RES-04 simply doesn't render a heatmap there). **Single-source contract:** per-player JSON is the only persisted form; team-color / role / per-player aggregates are **always derived at view time** via `PlayerRoundState` (the editor view joins `cell_occupancy_json` against `PlayerRoundState.team_color`; the round view's JS sums against `player_roster` rendered alongside the JSON). **Determinism:** reconstruction consumes **no RNG** and reads only the deterministic movement trail + A* route, so same seed + Orientation + rosters + map ⇒ identical `cell_occupancy_json` — the SIM-07/08 contract extends to the new field. **No simulation behaviour change** (the trail itself is unchanged; RES-04 only adds a snapshot derived from it) → **no Score Calibration re-baseline obligation**. **Scope-out (locked):** no backfill management command (regenerable cache + ADR-0004 precedent), no time-window slicing (no `?from=&to=`), no PNG/PDF/CSV export, no JS unit tests (frontend smoke-test only via Chrome-MCP), **no ADR** (decisions are reversible — the column is a `JSONField` add, the cache is regenerable), and no new `MapContext` accessor (re-use `get_adjacency` and `elevation_grid`). Seam contract path: [`.claude/worktrees/res-04-seam-contract.md`](.claude/worktrees/res-04-seam-contract.md). **Locked names** (quick reference, pinned by the seam contract): model field `GameRound.cell_occupancy_json`; migration `matches/migrations/0026_gameround_cell_occupancy_json.py`; pure function `matches/sim_helpers/cell_occupancy.py::reconstruct_cell_occupancy`; per-round view `matches/views.py::movement_heatmap` at URL `/matches/game-round/<int:round_id>/heatmap/` (name `movement_heatmap`) with template `templates/matches/movement_heatmap.html`; map-aggregate view `core/views.py::map_heatmap_data` at URL `/maps/<int:map_id>/heatmap-data/` (name `map_heatmap_data`); JSON outer key `str(player_id)`, inner key `"r,c"`, inner value `int` ticks; DOM ids `heatmap-canvas`, `heatmap-bg`, `heatmap-stage`, `heatmap-filter-player`, `heatmap-filter-role`, `heatmap-filter-team`, `heatmap-filter-row`, `heatmap-no-map-notice`, json_script ids `cell-occupancy-data` and `player-roster-data`, editor mode button `mode-heatmap`, editor controls wrapper `heatmap-controls`, editor team filter `heatmap-editor-filter-team`, editor round-count `heatmap-editor-round-count`; window global `LF_ZONE_SIZE`; test files `matches/tests/test_res04_cell_occupancy.py` (pure unit) and `matches/tests/test_res04_heatmap_view.py` (DB/view), plus one new case `test_flush_to_db_populates_cell_occupancy_json_when_map_active` appended to `matches/tests/test_sim09_consolidation.py`. CONTEXT.md domain terms (**Cell occupancy**, **Movement heatmap**) added in the grilling session.

### SIM-10 · Progressive batch simulation with live progress

A 500-round batch on the San Marcos map (post-SIM-09, map-aware BatchSim) currently blocks the `simulate_batch` view
for minutes with **no feedback** — the browser tab freezes and the user has no way to know whether progress is being
made, how far along the run is, or whether anything has gone wrong. Replace the synchronous one-shot render with a
job-polling pattern that streams aggregate results to the page every 5–10 completed rounds, so the user sees the
win%, average scores, and histogram converge in real time instead of waiting for the entire batch to finish.

**What changes:**
- `BatchSimulator` gains a new generator `run_incremental(team_red, team_blue, n, *, chunk_size, arena_map=None,
  workers=None, master_seed=None)` that yields `{"completed": k, "total": n, "aggregate": <partial dict>}` snapshots
  after each chunk. The seed chain (`random.Random(master_seed)`) is consumed identically to `run()` so the partial
  aggregate at `k == n` is bit-identical to the existing `run()` result for the same `master_seed` — the SIM-07/SIM-08
  contract holds (same seed + Orientation + rosters + map ⇒ same games, serial == parallel). `_aggregate_batch` is
  factored into an incremental variant that accepts a growing `(result, seed, flipped)` list rather than rebuilding
  from scratch each chunk (the list-of-games shape is already what aggregation takes, so this is a small refactor).
- Parallel mode (`workers > 1`) uses **a single long-lived `ProcessPoolExecutor`** with `executor.submit()` per round
  + `concurrent.futures.as_completed()` for progress streaming — **not** one pool per chunk (spawning a fresh pool
  per chunk would dominate the cost on small chunk sizes). Submission order is captured upfront so `side_advantage`
  de-flip uses the future's submission index, not its completion order.
- New async job runner mirroring `_run_save_job` / `save_batch_status` (the existing precedent at
  `matches/views.py:444+`): a background thread drives `run_incremental` and writes the latest snapshot into a shared
  `_BATCH_JOBS` dict (`{status, completed, total, partial, seeds, error}`). Frontend polls a new
  `batch_simulate_status(request, job_id)` view (returns JSON).
- `simulate_batch` POST handler now starts the job and returns `{"job_id": ...}` as JSON (or renders a placeholder
  page with the job id embedded for the polling JS). The existing full-page render path is retired.
- `templates/matches/batch_simulate.html`: form submits via `fetch()`; progress bar (`completed / total`); the
  results panel (win %, avg scores, histogram, side advantage) re-renders incrementally as snapshots arrive. The
  existing save-games UI block stays hidden until the job completes.
- Seed handover for save-games migrates from `request.session["batch_seeds"]` (set inline in the old sync view) to
  the job dict, then copied into the session by the final poll response on `status == "complete"` so
  `save_batch_games` (`views.py:444`) keeps working unchanged.
- Tests:
  - `BatchSimulator.run_incremental(...)` partial-equals-final invariant: with a pinned `master_seed`, summing the
    last yielded snapshot's `(red_wins, blue_wins, ties, avg_red_score, avg_blue_score, side_advantage)` against
    `BatchSimulator().run(...)`'s aggregate produces identical numbers.
  - Serial vs parallel determinism at every chunk boundary, not just the final tally.
  - Job lifecycle: `pending → running → complete`, partial snapshots monotonically grow `completed`, errors surface
    as `status == "error"` with the exception message.

**Out of scope:**
- The save-games flow itself (already async; not changed by SIM-10).
- The `score_averages` CLI path (separate code path, unaffected).
- Cancelling an in-flight job from the UI — deferred; the user can simply close the tab and the worker thread
  finishes on its own (cheap on the small/medium runs that motivated this).
- Per-job persistence across server restarts — `_BATCH_JOBS` is in-process only, same as `_SAVE_JOBS`.

**Risk:** the change is mostly view + template plumbing; the simulator contract change is small and structurally
mirrors the existing `_aggregate_batch` shape. The chief implementation risk is the `as_completed`-based progress
stream interacting cleanly with the SIM-08 side-flip de-aggregation (submission-order indexing, not completion-order
— locked by the test above). No new ADR; no schema change; no Score Calibration re-baseline (mechanics unchanged).
- completed
- note: introduces progressive batch simulation — replaces the synchronous `simulate_batch` render with a job-polling pattern mirroring the existing `_run_save_job` / `save_batch_status` precedent. New generator `BatchSimulator.run_incremental(team_red, team_blue, n, *, arena_map=None, workers=None, master_seed=None) -> Iterator[dict]` is the **sole game-loop and sole `_aggregate_batch` caller**: it yields snapshots `{"completed": int, "total": n, "aggregate": <existing _aggregate_batch dict over games[0..completed)>}` at chunk boundaries, with submission-indexed ordering so **serial == parallel at every chunk boundary, not just at `k == n`** (parallel path submits all `n` futures upfront, records a `future_to_index` map, drains via `as_completed` for liveness, and gates snapshot emission on a `pending_boundary` watermark — the locked test pins `serial_snaps[i] == parallel_snaps[i]` for every `i`). Chunk size is the module-level `_chunk_size_for(n: int) -> int` returning `max(1, min(25, n // 50))` (locked table: `[0,1,49,50,99,100,500,1000,1249,1250,5000,1_000_000] → [1,1,1,1,1,2,10,20,24,25,25,25]`). `run()` is **re-implemented as the consumer of `run_incremental`** (drives the generator to exhaustion and returns the last snapshot's `aggregate`) and `BatchSimulator._run_parallel` is **removed** — its `ProcessPoolExecutor(initializer=worker_django_init)` logic folds into `run_incremental`'s `workers > 1` branch, scoped inside the generator body so the pool cleans up on `GeneratorExit` / completion / fail-fast re-raise. Error policy is **fail-fast**: serial propagates straight out of the generator; parallel best-effort `.cancel()`s pending futures then re-raises the original exception (the `with` block waits for non-cancellables). View surface: `_BATCH_JOBS: dict = {}` next to `_SAVE_JOBS` (shares the existing `_JOBS_LOCK`, no new lock), new `_run_batch_job(job_id, team_red_id, team_blue_id, n, arena_map_id, master_seed)` background thread (mirrors `_run_save_job` — `try` / `with _JOBS_LOCK` writes / `finally: django.db.close_old_connections()`), reshaped `simulate_batch` POST returns `JsonResponse({"job_id", "team_red_id", "team_red_name", "team_blue_id", "team_blue_name", "arena_map_id", "n"})` after writing the initial job dict and starting the daemon thread (GET / form-validation HTML branches unchanged), and new `batch_simulate_status(request, job_id)` view at `/matches/simulate-batch/status/<str:job_id>/` (URL name `batch_simulate_status`, mirrors `save_batch_status`: returns `JsonResponse(job)` or `JsonResponse({"status": "not_found"}, status=404)`). Job-dict shape is locked: `{"status": "running"|"complete"|"error", "completed": int, "total": int, "partial": dict | None, "error": str | None, "team_red_id": int, "team_blue_id": int, "arena_map_id": int | None}` — all keys present from the initial write. **Seed handover via single-write session guard:** the FIRST poll observing `status == "complete"` (where `request.session.get("batch_seeds", {}).get("job_id") != job_id`) copies `avg_seeds` / `outlier_seeds` / team-and-map IDs plus the new guard marker `"job_id"` into `request.session["batch_seeds"]`; subsequent polls observing `complete` skip the write (so user-mutations between polls survive). `save_batch_games` reads the same session shape as today and is **unchanged** — the new `"job_id"` key is benign. Template `templates/matches/batch_simulate.html` is rewritten to the polling shape: JS constants `BIN_SIZE = 5000` and `POLL_INTERVAL_MS = 500` (hard-coded `STATUS_URL_BASE = "/matches/simulate-batch/status/"` matching `save_batch_status`); locked DOM ids `batch-progress-container` / `batch-progress-bar` / `batch-progress-label` / `batch-results` / `batch-red-win-pct` / `batch-blue-win-pct` / `batch-ties` / `batch-red-wins` / `batch-blue-wins` / `batch-red-ties-secondary` / `batch-blue-ties-secondary` / `batch-avg-red-score` / `batch-avg-blue-score` / `batch-avg-red-survivors` / `batch-avg-blue-survivors` / `batch-side-advantage` / `batch-red-side-win-pct` / `batch-red-side-wins` / `batch-blue-side-win-pct` / `batch-blue-side-wins` / `batch-avg-red-side-score` / `batch-avg-blue-side-score` / `batch-side-ties` / `batch-results-n` / `batch-elapsed` / `scoreChart` / `batch-save-games` / `batch-error`; histogram **client-side binning** replicates the prior server logic against `aggregate.red_scores` / `aggregate.blue_scores` (max-floor + `BIN_SIZE`-wide bins, last-bin clamp), Chart.js instance created once on first snapshot and `.update("none")`'d thereafter to avoid polling stutter; team names ride in the POST JSON response (`team_red_name` / `team_blue_name`) — **no GET-context `team_names_json` is added** (earlier proposal dropped). Tests pin every contract: `matches/tests/test_sim10_incremental.py` (NEW — `TestChunkSizeFor` parametrised table, `TestRunIncrementalSnapshotShape`, `TestRunIncrementalFinalEqualsRun`, `TestRunIncrementalSerialEqualsParallelAtEveryBoundary`, `TestRunIncrementalNZero`, `TestRunIncrementalFailFast`, `TestRunIncrementalDriveRun`) and `matches/tests/views_tests.py` (EXTEND — `TestSim10SimulateBatchPostReturnsJson`, `TestSim10BatchSimulateStatusShape`, `TestSim10BatchSimulateStatusLifecycle`, `TestSim10BatchSimulateStatusErrorPath`, `TestSim10BatchSimulateStatusNotFound`, `TestSim10SessionHandoverWritesOnceOnComplete`). **Scope-out (locked):** no new DB column / no migration (`_BATCH_JOBS` is in-process only, mirrors `_SAVE_JOBS`); **no new ADR** (reversible decisions, in-memory store pattern is precedented); **no new CONTEXT.md term** (`snapshot`, `chunk_size`, `_BATCH_JOBS`, `partial` are implementation language, not domain language); `score_averages` CLI is **unchanged** (consumes `run()` which now consumes `run_incremental` internally — transparent); save-games flow (`save_batch_games` / `_run_save_job` / `save_batch_status`) is **unchanged**; no cancel-in-flight UX; no cross-restart persistence; `master_seed` is **not exposed in the form** (`_run_batch_job` plumbs the parameter for tests / future use; production POST passes `None`). **Determinism:** `run_incremental` reuses the SIM-07 / SIM-08 seed chain, **Side alternation**, `_aggregate_batch`, `_side_order`, `_precompute_roster`, `batch_round_worker`, `worker_django_init`, and `load_map_context` unchanged — same `master_seed` + Orientation + rosters + map ⇒ identical games at every chunk boundary (extends the SIM-07/SIM-08 contract to "serial == parallel at every boundary", not just at `k == n`). **No simulation mechanics change** → **no Score Calibration re-baseline obligation** (mirrors RES-04 / RES-02 / RES-03). Seam contract path: [`.claude/worktrees/sim-10-seam-contract.md`](.claude/worktrees/sim-10-seam-contract.md).

### SIM-11 · Wire `workers=` into the UI batch path

Identified during SIM-10 PR review. `simulate_batch` / `_run_batch_job` currently call
`BatchSimulator.run_incremental(...)` with no `workers=` kwarg → strictly serial in the
UI, even though `run_incremental`'s parallel branch is feature-complete and pinned by
the SIM-10 `TestRunIncrementalSerialEqualsParallelAtEveryBoundary` test (same
`master_seed` + Orientation + rosters + map ⇒ identical games at every chunk
boundary, serial == parallel). The seam contract scoped this out deliberately
because SIM-10 was about the live-progress UX, not cross-core throughput;
SIM-11 closes the gap by plumbing `workers` through.

**Smallest viable change:** `_run_batch_job` passes `workers=os.cpu_count()` (or a
`BatchSimulateForm` checkbox / numeric field — to be decided in the grill). No
simulator change required (the parallel branch already exists and is tested). No
new DB column, no migration. Snapshot-emission contract is unchanged (submission-
indexed, gated on the `pending_boundary` watermark) — the progress UI works the
same, just faster per snapshot.

**Risk:** the parallel branch opens a `ProcessPoolExecutor` per job; spawning the
pool on Windows costs ~1–2 s and re-imports Django in every worker. For tiny
batches (n=10) that overhead dominates the gain. Decide in the grill whether the
form exposes a worker count, picks a sensible default by `n`, or always uses all
cores. **Behavioural change is zero** (the serial==parallel contract holds), so
no Score Calibration re-baseline.

**Out of scope for SIM-11:** changing `score_averages` (already opts into
`--workers`); changing the save-games flow (`_run_save_job`); exposing
`master_seed` in the form (still test-only).
- completed
- note: closes the SIM-10 gap where the UI batch path was strictly serial despite `run_incremental`'s parallel branch being feature-complete and pinned by `TestRunIncrementalSerialEqualsParallelAtEveryBoundary`. Introduces a module-level `_workers_for(n: int) -> int` in `matches/views.py` (placed immediately above `_run_batch_job`, mirroring the `_chunk_size_for(n)` precedent in `matches/simulation.py` — pure function of `n`, no surrounding state, no module-level constants), with the threshold and cap pinned in the function body: `n < 50 → 1` (small batches: Windows `ProcessPoolExecutor` spawn cost dominates the parallel gain — the early return makes that branch independent of `os.cpu_count()`) and `n >= 50 → min(os.cpu_count() or 1, 4)` (cap at 4 — CI / test-runner boxes may report far more cores and the workload does not benefit beyond that; `or 1` pins the CPython `os.cpu_count() is None` fallback to serial). The locked parametrised table covers the threshold (49→1, 50→cap), the cap (`cpu_count` of 8 / 16 / 64 all return 4 at `n >= 50`), the `None` fallback, and defensive negative-`n` rows that behave as small-`n`. Single call-site change inside `_run_batch_job`: the existing `BatchSimulator().run_incremental(team_red, team_blue, n, arena_map=arena_map, master_seed=master_seed)` gains one kwarg, `workers=_workers_for(n)`, slotted in the SIM-10-pinned `arena_map`, `workers`, `master_seed` keyword order — no other change to the function (the `try` / `except` / `finally` structure, the `_BATCH_JOBS` writes under `_JOBS_LOCK`, and the `django.db.close_old_connections()` cleanup are preserved verbatim). `import os` is added alongside the existing stdlib imports at the top of `matches/views.py` (alphabetically between `threading` and `uuid`). `BatchSimulateForm` is **not changed** — the decision lives in the view layer; the `score_averages` CLI keeps `--workers` explicit, the UI does not expose it (no `master_seed` exposure either, as in SIM-10). **Determinism:** the SIM-07 / SIM-08 / SIM-10 contracts hold unchanged — same `master_seed` + Orientation + rosters + map ⇒ identical games at every chunk boundary regardless of `workers`. Pre-SIM-11 the UI was strictly serial (no `workers=` kwarg ⇒ `None` ⇒ serial branch); post-SIM-11 the UI picks parallel for `n >= 50` and stays serial for `n < 50`. Byte-identical games either way → **no Score Calibration re-baseline** (mirrors the SIM-10 / RES-04 / RES-03 / RES-02 precedent). **Scope-out (locked):** no `BatchSimulateForm` change; no `simulate_batch` view body change (POST handler, GET handler, form-validation branch, `JsonResponse` shape all unchanged); no `batch_simulate_status` view change; no `_BATCH_JOBS` job-dict shape change; no `_JOBS_LOCK` change; no session handover change (`request.session["batch_seeds"]` shape and the single-write `"job_id"` guard are SIM-10's contract, untouched); no `_run_save_job` / `save_games` / `save_batch_status` change; no `score_averages` change; no `master_seed` form exposure; no `run_incremental` / `_run_incremental_parallel` change (the parallel branch is already feature-complete and pinned by SIM-10); no template touch (`templates/matches/batch_simulate.html` polling JS does not care how many workers the backend uses); no URL change; no new file outside the seam-contract artifact; no new DB column; no migration; no new ADR; no new `CONTEXT.md` term. **Tests:** two new classes appended to `matches/tests/views_tests.py` — `TestSim11WorkersFor` (parametrised, pure unit, imports `_workers_for` directly, patches `os.cpu_count` via `monkeypatch`, one assertion per row of the locked table) and `TestSim11RunBatchJobPassesWorkers` (drives `_run_batch_job` synchronously, patches `"matches.views.BatchSimulator"` so the `run_incremental` attribute returns `iter([])`, asserts `call_args.kwargs["workers"] == 1` for `n=10` and `== _workers_for(50)` for `n=50` so the test passes on any CI box regardless of CPU count). SIM-10's `TestRunIncrementalSerialEqualsParallelAtEveryBoundary` continues to cover the determinism contract; SIM-11 does not re-prove it. Seam contract path: [`.claude/worktrees/sim-11-seam-contract.md`](.claude/worktrees/sim-11-seam-contract.md).

### RV-01 · Compare two rounds side by side

`/matches/compare/?round_a=<id>&round_b=<id>` — per-player stat delta table with green/red colouring. 
Points Over Time overlay chart. Rounds must share at least one team.
- completed
- note: a single read-only view `compare_rounds(request)` (`matches/views.py`) wired at `path("compare/", views.compare_rounds, name="compare_rounds")` — reads `round_a` / `round_b` from `request.GET` (not URL kwargs, so the picker page can be reached with no params). **Four modes, all server-decided** and surfaced via a `mode` context key: **picker** (either param missing → render the two-`<select>` chooser, HTTP 200), **404** (a supplied id doesn't resolve → `get_object_or_404`), **error banner** (`round_a == round_b`, or the two rounds share no team → `mode="error"` + `error_message`, still HTTP 200 so the picker re-renders above the banner), and **full compare** (HTTP 200 with the delta table + overlay chart). **"Shares a team" is Side-agnostic Team-id overlap:** `{a.team_red_id, a.team_blue_id} & {b.team_red_id, b.team_blue_id}` — a team that played red in round A and blue in round B still pairs (this is the SIM-08 **Orientation**-independent comparison the feature needs). The delta table pairs `PlayerRoundState` rows **by `player_id`** (not by Side or slot), so the same human is compared to themselves across the two rounds regardless of which colour they played. **No model change, no migration** — the view is pure read-only/derived ([ADR-0004](docs/adr/0004-simulation-data-is-disposable.md) disposable-data precedent doesn't even apply since nothing is persisted); **consumes no RNG** and runs no simulation, so it is **outside the SIM-07/08 contract and triggers no Score Calibration re-baseline**. Three module-level helpers in `matches/views.py` (pure, no ORM beyond the rounds handed in): `_shared_team_ids(round_a, round_b) -> list[int]` (the set-intersection above, returned as a list); `_player_stat_deltas(round_a, round_b, team_ids) -> list[dict]` — one row per player on a shared team, shape `{player_id, name, role_a, role_b, side_a, side_b, stats: {<stat>: {a, b, delta}}}` where **`delta = b - a`** and the whole `delta` (and the absent side's `a`/`b`) is **`None` when that player has no `PlayerRoundState` on one of the rounds** (joined the roster between rounds); and `_cumulative_team_points(game_round, team_id) -> list[list]` returning `[[tick, cum_points]]` running totals built from that team's `GameEvent` rows, **coalescing the nullable `GameEvent.points_awarded` to 0** so non-scoring events don't break the cumulative sum. The delta table is the **extended** stat set in a fixed key order — `points_scored, mvp, tags_made, times_tagged, accuracy, final_lives, resupplies_given, missiles_landed, specials_used, follow_up_shots, reaction_shots, combo_resupply_count` — exposed as the `stat_keys` context key so the template iterates one source of truth; **`mvp` reuses the existing `PlayerRoundState.get_mvp` property and `accuracy` reuses the existing `get_accuracy` property** (RES-01) — neither is recomputed in the view. The **Points-Over-Time** overlay is `points_series` = one entry **per shared team** `{team_id, team_name, a: [[tick, cum]], b: [[tick, cum]]}` (round A drawn solid, round B dashed) built from `_cumulative_team_points`. Context keys: `round_a, round_b, all_rounds` (`GameRound.objects.select_related("team_red", "team_blue").order_by("-id")` — populates both picker `<select>`s), `mode, error_message, stat_keys, deltas, points_series`. Template `templates/matches/compare_rounds.html`: two `<select>` controls (DOM ids `compare-select-a` / `compare-select-b`), the error banner, the delta table (green = positive delta / red = negative, neutral when `delta is None`), and a Chart.js overlay fed by two `json_script` blocks (DOM ids `compare-points-series` and `compare-deltas`). All timestamps are raw **ticks** through the view/JSON boundary; any mm:ss display applies the standard `÷2` filter at the HTML layer (TIME-01). Tests live in `matches/views_tests.py` (picker / 404 / same-round error / no-shared-team error / full-compare modes, the shared-team Side-agnostic intersection, the `delta = b - a` and `None`-on-absent-side rows, and the `points_awarded`-coalesce). No ADR (reversible, read-only); no CONTEXT.md term (no new domain language — Side / Orientation / tick already defined).

### RV-02 · Auto-flag highlights

Detect: nuke events, first elimination, largest 30-second point swing, team elimination, base destructions. 
Show as a "Highlights" tab on the events page. Store results in `GameRound.highlights_json` (new field) at round completion.
- completed
- note: per-round **Highlight** (CONTEXT.md) auto-flagging persisted to a new `GameRound.highlights_json` (`JSONField`, null/blank, default `None`, placed after `cell_occupancy_json`) by migration `0027_gameround_highlights_json.py` (an `AddField` for the new column plus an `AlterField` on `gameevent.event_type` adding the two new choices; dep `0026`); **no backfill** — pre-RV-02 rounds stay `null` (the [ADR-0004](docs/adr/0004-simulation-data-is-disposable.md) disposable-data precedent, same as `rng_seed` / `cell_occupancy_json`). The detection logic lives in a **pure builder** `matches/sim_helpers/highlights.py::build_highlights(events, result, *, round_ticks, name_by_id, team_by_id) -> list[dict]` — **pure Python, no Django/IO/RNG** — that consumes the **in-memory event buffer** (NOT ORM rows) plus the round result dict, with `round_ticks=TICKS_PER_ROUND` (1800) and the `name_by_id` / `team_by_id` maps passed in (so the function emits NAME strings + a per-event team while staying pure; an absent id resolves to `None`); it returns a flat list of records sorted by tick ascending, each with the fixed 7-key shape `{kind, tick, team, actor, target, points, label}`. **Six kinds:** `nuke_detonation` (discriminated by `event_type=="special"` + `metadata["targets"]` + `points_awarded==500` — the activation row, `points==0` & `metadata["fires_at"]`, is **not** flagged), `nuke_cancelled`, `medic_reset`, `first_elimination` (first elimination by tick → one record), `team_elimination` (read from `result["red_eliminated"]`/`["blue_eliminated"]` + `["eliminated_at"]`, **NOT** the `dead` event — `team_elimination` is never an emitted `GameEvent` type, the `DEAD` event stays the source-of-truth event), and `scoring_burst` (a **Scoring burst**, CONTEXT.md — the forward `[t, t+60)` 60-tick window with the maximum single-team gross points → one record; none emitted when the round had no point events). **Base captures are deliberately *not* a Highlight kind** — they are routine, frequent point-grabs, so they are surfaced in the events-log timeline (a new **"Base Capture"** type-filter checkbox + `🚩` icon were added; `base_capture` events were persisted all along but the timeline filter had no checkbox and its substring match hid them) rather than the highlight reel; their `points_awarded` still count toward the `scoring_burst`. **Two new server-emitted `GameEvent` types** are added at the `BatchSimulator._record_down` chokepoint, which is converted **static → instance** (`self._record_down`, reading `self._event_log` / `self._pending_nukes` stashed in `_simulate_round`; **7 callsites** converted): `nuke_cancelled` (**Nuke cancellation**, CONTEXT.md — emitted at the down/disarm tick for a Commander with a live pending nuke; the nuke is **left in `pending_nukes`** with a new `PendingNuke.cancel_logged: bool=False` de-dup flag set `True` so the existing MECH-05 nuke-reaction/drain path is unchanged — the drain-else branch emits only when `not cancel_logged` → **no re-baseline**, seeded games are byte-identical) and `medic_reset` (**Medic reset chain**, CONTEXT.md — a Medic re-**Down**ed before recovery; a transient `PlayerState.down_chain_count: int=0`, no DB column, increments in `_record_down` **before** stamping `last_downed_time` when `not is_active_at(second)`, fires the event once when the chain reaches 2 for a `medic`, and resets to 0 in the per-tick active-accounting branch). Both emit dicts carry `points_awarded:0`, `target_id:None`, and `metadata=_build_meta(player)`. The builder is invoked in `BatchSimulator._flush_to_db` (~L2762) after the RES-04 `cell_occupancy_json` block and before the final `return`: it builds `name_by_id` / `team_by_id` from the red+blue players, calls `build_highlights(...)`, sets `game_round.highlights_json`, and persists via a **second** `game_round.save(update_fields=["highlights_json"])` (mirrors the RES-04 second-save pattern). View `game_round_events` adds context key `highlights_json` (`game_round.highlights_json or []`); template `game_round_events.html` exposes it via `{{ highlights_json|json_script:"highlights-data" }}` and renders a client-side **Highlights** tab into DOM ids `highlights-section` / `highlights-list` / `highlights-empty` (mm:ss via the standard `÷2` at the HTML layer, TIME-01). **No URL change** — RV-02 reuses the existing `/matches/game-round/<id>/events/` page (the Highlights tab lives there, no new route). **No simulation mechanics change** (the cancelled nuke is left in the pending queue, no RNG consumed) → **no Score Calibration re-baseline obligation**. Tests: `matches/tests/test_rv02_highlights.py` (NEW — pure builder: the 6 kinds, nuke activation-vs-detonation discrimination, `team_elimination`-from-result, base captures **not** flagged as a highlight, the 60-tick scoring-burst window, id→name/team resolution + absent-id `None`, sort order, empty-input edges), `test_sim09_consolidation.py` (EXTENDED — `_flush_to_db` populates `highlights_json` and the second save, `_record_down` static→instance reshape, `nuke_cancelled` / `medic_reset` emit + de-dup), and `views_tests.py` (EXTENDED — the Highlights render path / context key). Domain terms (Highlight, Scoring burst, Medic reset chain, Nuke cancellation) are in [CONTEXT.md](CONTEXT.md); the nuke-cancelled-event decision is recorded in [ADR-0012](docs/adr/0012-nuke-cancelled-event.md).

### RV-03 · Export round report as PDF

`GET /matches/game-round/<id>/export/` — ReportLab (programmatic PDF generation; chosen over WeasyPrint to
avoid template dependency ahead of the Angular migration). Contains round summary, scoreboards, per-player table,
resource summary. "[Simulated]" watermark on simulator-generated rounds.
- completed
- note: single-**Round** PDF export at `GET /matches/game-round/<int:round_id>/export/` (URL name `export_round_report`, view `matches/views.py::export_round_report`), generated server-side with **ReportLab** (programmatic PDF; chosen over WeasyPrint to avoid an HTML-template dependency ahead of the planned Angular migration — added as the unpinned `reportlab>=4.0` line to `laserforce_simulator/requirements.txt`). Provenance is a new **`GameRound.is_simulated`** (`BooleanField(default=True)`; migration `0028_gameround_is_simulated.py`, dep `0027_gameround_highlights_json`, a single `AddField`) — **no backfill** (the [ADR-0004](docs/adr/0004-simulation-data-is-disposable.md) disposable-data precedent, same as `rng_seed` / `cell_occupancy_json` / `highlights_json`); existing rows take the `default=True`, so today *every* persisted Round is a **Simulated round** (CONTEXT.md) and exports with the watermark. The render is a **pure builder** `matches/sim_helpers/pdf_report.py::build_round_report(report_data: dict, *, watermark: bool) -> bytes` — **pure Python, no Django/ORM imports, no settings access, no file I/O beyond an internal `io.BytesIO`, consumes no RNG** — that returns the PDF as `b"%PDF"`-prefixed non-empty bytes and draws a diagonal "[Simulated]" watermark on **every** page (ReportLab `onFirstPage`/`onLaterPages` page callback) gated by the keyword-only `watermark` bool; the view passes `watermark=game_round.is_simulated`. **Watermark testable seam:** ReportLab **compresses page-content streams**, so the literal `[Simulated]` text is **not reliably greppable** in the output bytes — the decision is therefore factored into a tiny pure helper `should_watermark(is_simulated: bool) -> bool` that the page callback consults and tests assert on **directly** (`should_watermark(True) is True`, `False is False`) **without parsing compressed PDF streams** (the byte-level assertions check only the `b"%PDF"` prefix for both branches, never watermark-text presence). The **view is GET-only** (`if request.method != "GET": return HttpResponseNotAllowed(["GET"])` → 405, mirroring the `movement_heatmap` guard), `get_object_or_404(GameRound, pk=round_id)` (→ 404), assembles `report_data` from the ORM, calls the builder, and returns `HttpResponse(pdf_bytes, content_type="application/pdf")` with `Content-Disposition: attachment; filename="round-<id>-<red_slug>-vs-<blue_slug>.pdf"` (the view owns slugification). The seam crosses **one** frozen `report_data` dict: round-summary block (`round_id`, `round_label` = `f"Round {n} of 2"` if `match` else `"Single Round"`, `date_played` pre-formatted by the view + printed verbatim, `map_name` → builder **omits** the map line when `None`, team names/points/eliminated flags, `winner_name` → builder prints `"Tie"` when `None`), plus `red_players` / `blue_players` (ordered `-points_scored, role, player__name` **exactly** mirroring `game_round_detail`). The **per-player table is the RV-01 stat set, single-sourced** — same fixed key order `name, role, points_scored, mvp, tags_made, times_tagged, accuracy, final_lives, resupplies_given, missiles_landed, specials_used, follow_up_shots, reaction_shots, combo_resupply_count` — where **`mvp` reuses the `get_mvp` property** and **`accuracy` reuses the `get_accuracy` property** (NOT the plain `accuracy` property that delegates to `stat_for_simulation`), neither recomputed. A **per-team resource summary** (`red_totals` / `blue_totals`) aggregates `resupplies_given` / `missiles_landed` / `specials_used` / `tags_made` summed over the team's players, `survivors` = count with `final_lives > 0`, and `team_points` from `GameRound.red_points` / `blue_points` (team-level field, **not** summed). Edge cases: empty / early-eliminated round (all-zero stats) renders with zeros and **no crash**; map-less round omits the map line; tie prints "Tie". Entry point: an "Export PDF" link in `templates/matches/game_round_detail.html` (`{% url 'export_round_report' round.id %}`; the Code agent adds the link — no behavioural logic in the template). **Charts/graphs are deliberately deferred to RV-05** (`pdf_charts.py` is a *future* sibling of `pdf_report.py`). The `is_simulated=False` write — the **Actual game log** (CONTEXT.md) `.tdf` import that pairs a Round to a real game — is deferred to **IMPORT-01** (the first writer of `is_simulated=False`; provenance contract locked here at RV-03 planning). **No simulation change, runs no simulation, consumes no RNG → no SIM-07/08 contract interaction and no Score Calibration re-baseline.** Tests: `matches/tests/test_rv03_pdf_report.py` (NEW — pure-unit: `build_round_report` returns `b"%PDF"`-prefixed non-empty bytes for `watermark=True` and `watermark=False`, `should_watermark` truth table, empty/early-eliminated zeroed `report_data` renders without crashing, and a "no Django imports leaked into `pdf_report.py`" defensive check mirroring the RES-04 pattern — hand-built dict literal, **no ORM**) and `matches/tests/views_tests.py` (EXTENDED — GET → 200 + `Content-Type: application/pdf` + `Content-Disposition` shape + `b"%PDF"` body, 404 on missing id, 405 on POST, and both `is_simulated=True` / `False` rounds returning 200 + `b"%PDF"`). Seam contract: [`.claude/worktrees/rv-03-seam-contract.md`](.claude/worktrees/rv-03-seam-contract.md). Domain terms (Round report, Simulated round, Actual game log) are in [CONTEXT.md](CONTEXT.md). **No ADR** — decisions are reversible (a `BooleanField` add + a pure render module).

### SIM-01 · Document and test action weights

Add docstrings to every weight function in `weights.py`. Cover weight sums with unit tests. 
Provide a clearly documented constant dict so weights are adjustable without touching logic code.
- completed
- note: documentation + test hardening only — **no behavioural change, no formula/value change, no migration, no Score Calibration re-baseline, no CONTEXT.md term, no ADR**. (1) **Constant extraction:** the action-weight baseline `[70, 30, 0, 0, 0, 0, 0, 0, 0]` is moved out of `combat.plan_action` (was a stranded magic literal at `combat.py:~293`) into a NEW documented public module constant `BASELINE_ACTION_WEIGHTS` in `matches/sim_helpers/weights.py`; `plan_action` now does `weights = list(BASELINE_ACTION_WEIGHTS)` (copy, never mutate). This is the SIM-01 "adjustable without touching logic code" deliverable — the **constant dict** the story asks for is the existing per-role dicts (`_MEDIC` / `_AMMO` / `_SCOUT` / `_HEAVY` / `_COMMANDER`, which postdate the SIM-01 plan text and already drive every per-role tuning) **plus** this baseline constant, the one remaining tunable that still lived in logic code. (2) **Docstrings** added to all 5 role weight fns (`_get_medic_weights`, `_get_ammo_weights`, `_get_scout_weights`, `_get_heavy_weights`, `_get_commander_weights`) stating baseline totals, the situational-block order, and the non-negative invariant; per-key inline comments added to the 5 const dicts. (3) **Tests** (`matches/tests/test_weights.py`): migrated from a mixed 7-slot/9-slot fixture set to a SINGLE 9-slot fixture sourced from `BASELINE_ACTION_WEIGHTS` (legacy 7-slot `_BASE` / `_ACTION_IDX` deleted); existing sum/vector assertions widened to 9 elements with NO value change (hold redistribution is zero-sum, `request_resupply` = 0 at baseline). New regression test `test_plan_action_never_emits_negative_weight` builds in-memory `PlayerState` objects (no DB) and asserts `plan_action` never hands a negative weight to `random.choices` across 5 roles × ~10 targeted edge states; the medic-`+5`-capture (the known pre-existing failure) and Scout-`xfail` cases kept as-is with sharpened docstrings. Seam contract: [`.claude/worktrees/sim-01-seam-contract.md`](.claude/worktrees/sim-01-seam-contract.md).

### SIM-02 · Batch simulation mode

`POST /matches/simulate-batch/` — accepts `red_team_id`, `blue_team_id`, `n` (10/50/100/500). 
Runs `ResourceBasedSimulator` n times, returns aggregate stats (win%, avg score, avg survivors, 
score distribution histogram). Uses simple in-process threading when the run exceeds ~5 seconds;
results are not stored as permanent Match records.
- completed: fully subsumed by the SIM-09/10/11 chain that logically depended on it but was
  built first (phase-ordered plan, dependent-ordered build). **No code/tests/PR of source** —
  this entry is a docs-only reconciliation. Every SIM-02 acceptance criterion is met or exceeded:
  the `POST /matches/simulate-batch/` endpoint exists (`matches/urls.py` → `views.simulate_batch`,
  URL name `simulate_batch`); `BatchSimulateForm` (`matches/forms.py`) accepts `team_red` / `team_blue`
  / `n` with the **exact** `N_CHOICES = [("10"),("50"),("100"),("500")]` plus an optional `arena_map`;
  aggregate stats (win %, avg score, avg survivors, score-distribution histogram) ship via
  `_aggregate_batch` + client-side binning (DOM ids `batch-red-win-pct`, `batch-avg-red-score`,
  `batch-avg-red-survivors`, `scoreChart`, …). The "in-process threading when slow" requirement is
  **exceeded** — SIM-11 wires `workers=_workers_for(n)` (serial for `n < 50`, `ProcessPoolExecutor`
  capped at 4 for `n >= 50`) into the SIM-10 `run_incremental` progressive path, so large batches scale
  across cores with a live progress UI rather than a one-shot blocking render. Results are **not**
  persisted as Match records — the save-games flow (`save_batch_games`) is a separate opt-in. The
  legacy `ResourceBasedSimulator` named here was removed by SIM-09; the sole engine is `BatchSimulator`.
  Domain term **Batch run** is already in [CONTEXT.md](CONTEXT.md). No ADR, no migration, no re-baseline.

### HX-01 · Per-player career stats page

`/teams/<id>/player/<pid>/stats/` — aggregated `PlayerRoundState` across all rounds: games played, 
avg points, K/D ratio, avg survival time, avg accuracy, avg SP earned. Per-role breakdown. 
Trend chart: avg points per game over time.  Eventually players will be able to switch teams so maybe the URL should be `/players/<pid>/stats/` and the page can show team history as well?
- completed
- note: per-player career page at `GET /players/<int:player_id>/stats/` (URL name `player_career_stats`, view `teams/views.py::player_career_stats`), wired through a NEW URL file `teams/player_urls.py` (`app_name = None` — explicit; reverse stays the bare `'player_career_stats'`, no namespace prefix) included from the project root as `path("players/", include("teams.player_urls"))` placed **above** the `path("", include("teams.urls"))` homepage catch-all so the catch-all does not shadow it (order matters — Django resolves top-to-bottom). The URL shape is deliberately **not** nested under `/teams/<id>/` even though `Player.team` is a single CASCADE FK today — keeping `/players/<pid>/` flat now means a future cross-team-history task does not need to break URLs. The aggregation seam is a NEW pure module `teams/career_stats.py` — **pure Python, no Django imports, no ORM, no RNG, no I/O** (frozen import allowlist: `typing`, `collections`, optional `math`, and `SPECIAL_COST` from `matches.sim_helpers.role_constants`; the "no Django imports leaked" defensive check mirrors the RES-04 / RV-03 precedent) — exposing four functions: `summarize(rounds) -> dict` returning exactly six keys `{games, avg_points, tag_ratio, avg_survival_ticks, avg_accuracy_pct, avg_sp_earned}` (empty input ⇒ `games=0` and every other key `0.0`, no division by zero), `summarize_by_role(rounds) -> list[dict]` one entry per role **actually played** in the locked order Commander/Heavy/Scout/Medic/Ammo (roles not played are omitted; empty input ⇒ `[]`), `points_trend(rounds, window=10) -> list[list]` (`[[round_idx, mean_points], …]` with `round_idx` 1-based, sorted ascending by `(date_played, game_round_id)` tiebreaker, partial trailing window for rounds 1..9 and full 10-window for rounds 10+; `list[list]` not `list[tuple]` so `json_script` serialises trivially), and the exported helper `rolling_mean(values, window=10) -> list[float]` (used internally by `points_trend` but exported so tests can pin it directly). Formulas are sum/sum (NOT mean-of-per-round-ratios) where statistically required and are pinned by the seam: **Tag ratio** = `sum(tags_made) / max(sum(times_tagged), 1)` (sum/sum, with the `max(…, 1)` denominator floor preventing div-by-zero — pinned by `test_tag_ratio_is_sum_over_sum_not_mean_of_ratios` against the deliberately-asymmetric `10/1` vs `0/100` two-round case where mean-of-ratios would yield 5.0 and sum/sum yields ≈0.099); **Avg survival ticks** = `mean(min(was_eliminated_at, 1800))` (the cap is TIME-01's `TICKS_PER_ROUND = 1800`, so the `SURVIVED_SENTINEL = 1801` contributes 1800; the `÷2` tick→second conversion is applied at the **template** layer only via the existing `team_extras.div` filter — TIME-01); **Avg accuracy** = `sum(tags_made) / max(sum(tags_made + shots_missed), 1) × 100`; **Avg SP earned** = `mean(final_special + SPECIAL_COST.get(role, 0) × specials_used)` (the `.get` fallback contributes **0** for Heavy, which has no `SPECIAL_COST` entry — pinned by `test_avg_sp_earned_mixed_roles_includes_heavy_fallback`). The **round-dict** crossing the view ↔ pure-module seam is a frozen 10-key shape `{role, points_scored, tags_made, times_tagged, shots_missed, final_special, specials_used, was_eliminated_at, date_played, game_round_id}` — the pure module never sees a Django object, only plain dicts. The view runs **exactly one** ORM query — `PlayerRoundState.objects.filter(player=player).select_related("game_round").order_by("game_round__date_played", "game_round_id")` — assembles the round-dict list, calls the three pure functions, and ships **six** frozen context keys `player, total_rounds, career, per_role, trend, has_rounds` (`has_rounds = total_rounds > 0`). Template `templates/teams/player_career_stats.html` extends `base.html`, `{% load team_extras %}` for the `div` filter, and renders three surfaces gated on `has_rounds`: a 6-column career-totals row (DOM id `career-totals-table`), the per-role table (DOM id `career-per-role-table`, `|title`-cased role labels), and a Chart.js dashed-line rolling-10 trend chart (canvas DOM id `points-trend-chart`, json_script id `trend-data`, dataset label `"Avg points (rolling 10)"`, x-axis title `"Round number"`, y-axis title `"Avg points (rolling 10)"`, `pointRadius: 2`); the empty branch renders a notice (DOM id `career-no-rounds-notice`) containing the substring `"No rounds played yet"`. Formatting is locked: avg points `|floatformat:1`, tag ratio `|floatformat:2`, survival `|div:2|floatformat:0` + `s` suffix, accuracy `|floatformat:0` + `%`, SP earned `|floatformat:1`. Entry point: a `"Career stats"` anchor in `templates/teams/player_detail.html` reversing `{% url 'player_career_stats' player.id %}`. **Determinism / scope:** **read-only view**, no RNG, no simulation, no `_flush_to_db` touch, no SIM-07 / SIM-08 contract interaction, no Score Calibration re-baseline; **no model change, no migration**, no ADR (reversible: pure read-only view + pure aggregation module), no CONTEXT.md edit (the **Tag ratio** term was added inline during the grilling session that produced this contract). Tests live in a NEW `teams/tests/test_career_stats.py` — pure-unit class for the 4 pure functions (empty inputs, single-round happy path, sum/sum tag-ratio direction, Heavy `SPECIAL_COST` fallback, `was_eliminated_at=1801` capping to 1800, all-misses accuracy, role ordering Commander/Heavy/Scout/Medic/Ammo, role omission, `rolling_mean` partial-then-full window, `points_trend` `(date, game_round_id)` tiebreaker, and the "no Django imports leaked" defensive check) plus a Django `TestCase` class for the view (200 with rounds + all 6 context keys, 200 empty state with `"No rounds played yet"` substring, 404 on missing `player_id`, and the `"Career stats"` link rendered on `/teams/<team_id>/player/<player_id>/`). Seam contract: [`.claude/worktrees/hx-01-seam-contract.md`](.claude/worktrees/hx-01-seam-contract.md).

### HX-01b · Extend per-player career page to the 12-stat benchmark set

Extend the per-role table on the existing HX-01 page (`GET /players/<int:player_id>/stats/`, view `teams/views.py::player_career_stats`, template `templates/teams/player_career_stats.html`) from its current 5 display rows (`avg_points`, `tag_ratio`, `avg_survival_ticks`, `avg_accuracy_pct`, `avg_sp_earned`) to the full 12 `STAT_KEYS` already benchmarked by HX-02 — the real 12-tuple at `teams/role_benchmarks.py:18` is `(points_scored, mvp, tags_made, times_tagged, accuracy, final_lives, resupplies_given, missiles_landed, specials_used, follow_up_shots, reaction_shots, combo_resupply_count)` — minus the two already overlay-mapped via `_HX01_TO_BENCHMARK_STAT` (`points_scored` ↔ `avg_points` and `accuracy` ↔ `avg_accuracy_pct`), plus the 10 net-new rows (`mvp`, `tags_made`, `times_tagged`, `final_lives`, `resupplies_given`, `missiles_landed`, `specials_used`, `follow_up_shots`, `reaction_shots`, `combo_resupply_count`). Each net-new row gets the same `{mean, median, delta, percentile, n}` benchmark cells HX-02 already wires through the view's `per_role_with_benchmarks` context entry — so this task is "add 10 more template rows + 10 more aggregation entries in the view summary"; **no new pure-module work** (`teams/role_benchmarks.py` already exposes `summarize_population` / `percentile_for` for every stat in `STAT_KEYS`), **no new cache work** (the `teams/role_benchmarks_cache.py` single-scan miss already populates all 12 keys per role), **no migration**. The HX-02-shipped `benchmark-na` placeholder cells on the 3 already-rendered HX-01 rows that lack a STAT_KEYS mapping (`tag_ratio`, `avg_survival_ticks`, `avg_sp_earned`) stay as `—` — they remain HX-01-only derived stats not benchmarked by HX-02 today.
- completed
- note: per-role table on the HX-01 career page (`GET /players/<int:player_id>/stats/`) pivoted from one wide row-per-role table to a `<section id="career-per-role-table">` wrapper (preserves the HX-01-locked DOM id) containing one `<table id="career-per-role-table-{role}">` per role actually played, each with **15 rows** (columns `Stat | Player value | Mean | Median | Δ | Percentile | n`). Frozen row order — rows 0-4 are the 5 HX-01 display stats in their current order (`avg_points`, `tag_ratio`, `avg_survival_ticks`, `avg_accuracy_pct`, `avg_sp_earned`), rows 5-14 are the 10 net-new STAT_KEYS in declaration order skipping the two already overlay-mapped (`mvp`, `tags_made`, `times_tagged`, `final_lives`, `resupplies_given`, `missiles_landed`, `specials_used`, `follow_up_shots`, `reaction_shots`, `combo_resupply_count`). Locked human labels: `Avg points / Tag ratio / Avg survival / Avg accuracy / Avg SP earned / MVP score / Tags made / Times tagged / Final lives / Resupplies given / Missiles landed / Specials used / Follow-up shots / Reaction shots / Combo resupplies`. Each `<tr id="career-stat-row-{role}-{key}">` carries the row's `{key}` verbatim — mixed namespace (HX-01 display keys for rows 0-4, STAT_KEYS names for rows 5-14) is intentional. Each `per_role_with_benchmarks[i]` row gains an additive `stat_rows: list[dict]` field (15 entries, ordered; each entry `{key, label, player_value: float, benchmark: dict | None}`); `benchmark=None` discriminates the 3 HX-01-only rows from the 12 benchmark-backed rows. The existing `benchmarks_by_stat` dict is **preserved verbatim** for back-compat with `test_per_role_with_benchmarks_contains_benchmarks_by_stat`. Player-value sources: rows 0-4 pulled directly from the existing `summarize_by_role` row dict (no recomputation); rows 5-14 computed view-side via `compute_career_stat_for_role(player_role_rounds, stat)` from `teams/role_benchmarks.py` — same helper that already feeds the benchmark percentile path, guaranteeing the subject value and the overlay are identical. Empty-state UX mirrors today's `avg_points` / `avg_accuracy_pct` cells exactly: below-threshold subject delta cells render `— (need {min_rounds}+ rounds)`; HX-01-only rows render `<td class="benchmark-na">—</td>` for all 5 benchmark cells; empty `(role, stat)` populations render `n = 0`. Implementation lives entirely in `teams/views.py::_build_per_role_overlay` (additive — signature unchanged; new module-level `_HX01B_STAT_ROW_SPEC` 15-tuple pins the order) and `templates/teams/player_career_stats.html` (per-role table block replaced). **Zero diff** to `teams/career_stats.py` (HX-01 pure module, "no new pure-module work"), `teams/role_benchmarks.py`, `teams/role_benchmarks_cache.py`, `teams/signals.py`, URL files, `matches/`; no model change, no migration, no ADR, no CONTEXT.md edit (no new domain terms — `Tag ratio` / `Role benchmark` / `Percentile rank` already defined and HX-02 STAT_KEYS already documented). Test coverage: 6 new TestCases appended to `teams/tests/test_role_benchmarks_view.py::TestPlayerCareerStatsExtended` (`test_stat_rows_is_15_entry_ordered_list_per_role`, `test_stat_rows_order_is_locked`, `test_hx01_only_rows_carry_none_benchmark`, `test_net_new_rows_subject_value_matches_compute_career_stat_for_role`, `test_below_threshold_subject_renders_need_n_rounds_on_all_12_benchmarked_rows`, `test_per_role_table_dom_ids_present_per_role_played`); existing `test_per_role_with_benchmarks_contains_benchmarks_by_stat` stays green via back-compat dict preservation. Seam contract: [`.claude/worktrees/hx-01b-seam-contract.md`](.claude/worktrees/hx-01b-seam-contract.md).

### HX-02 · Role benchmarks

Global benchmark averages per role computed from all `PlayerRoundState` records. 
Player stat shown with +/− delta and percentile rank vs role average. Recomputed on demand or nightly.
- completed
- note: per-role benchmark surface served as a standalone page at `GET /players/benchmarks/` (URL name `role_benchmarks`, view `teams/views.py::role_benchmarks`) **plus** an additive extension to the HX-01 per-player career page (`GET /players/<int:player_id>/stats/`, view `teams/views.py::player_career_stats`) — the standalone page lists one table per role with mean/median/p25/p75/p90/n cells across all 12 `STAT_KEYS`, and the HX-01 per-role table picks up extra `{mean, median, delta, percentile, n}` cells alongside each player stat. URL routed through the existing `teams/player_urls.py` (the new entry listed FIRST so the `<int:player_id>` pattern does not shadow `benchmarks/`). The aggregation seam is a NEW pure module `teams/role_benchmarks.py` — **pure Python, no Django imports, no ORM, no RNG, no I/O** (frozen import allowlist: `bisect`, `statistics`, `collections.defaultdict`, `typing.Iterable`/`Mapping` — the "no Django imports leaked" defensive check mirrors the HX-01 / RES-04 / RV-03 precedent) — exposing six functions: `build_role_populations(rounds, *, threshold=0) -> dict[(role, stat), list[float]]` (groups career-averages-when-playing-that-Role by `(role, stat)`; `accuracy` aggregates with the Tag-ratio-style `sum(tags_made) / max(sum(tags_made + shots_missed), 1)` shape per player, every other stat — including the pre-computed `mvp` — uses per-round mean within the player's per-role subset), `apply_threshold(populations, *, threshold) -> dict` (drops players with `rounds_in_role < threshold` from each `(role, stat)` population), `summarize_population(values) -> dict` (returns `{mean, median, p25, p75, p90, n}` with `n=0` ⇒ every other key `0.0`, no div-by-zero), `percentile_for(values, x) -> float` (population maximum maps to `100.0`, uses `bisect` against pre-sorted `values`; empty ⇒ `0.0`), `compute_role_benchmarks(rounds, *, threshold=0) -> dict[role, dict[stat, summary]]` (one-shot composition of the four above for the standalone-page view), and `player_position(populations, role, stat, x) -> dict` (returns `{delta, percentile, qualified, n}` for a single player's career-average — `qualified=False` when the role population for that player is below threshold; view renders `— (need N+ rounds)`). Module-level constants `STAT_KEYS` (12-tuple: `points_scored, tags_made, times_tagged, accuracy, shots_missed, final_special, specials_used, mvp, final_lives, resupplies_given, missiles_landed, follow_up_shots, reaction_shots, combo_resupply_count` — actual seam carries the 12 keys in frozen order), `RATIO_STATS = frozenset({"accuracy"})`, `MVP_DERIVED_STATS = frozenset({"mvp"})`, `ROLES = ("commander", "heavy", "scout", "medic", "ammo")`. The round-dict crossing the view ↔ pure-module seam is a strict **18-key SUPERSET** of HX-01's 10-key dict — additive, HX-01 signatures unchanged: the 10 HX-01 keys (`role, points_scored, tags_made, times_tagged, shots_missed, final_special, specials_used, was_eliminated_at, date_played, game_round_id`) plus **6 HX-02 raw counters** (`final_lives, resupplies_given, missiles_landed, follow_up_shots, reaction_shots, combo_resupply_count`, all int) plus **2 view-side pre-computed** (`mvp` float via `calculate_mvp`, `accuracy_pct` float via `get_accuracy`). **Subject-inclusion policy:** mean/median/percentile are computed over the FULL population INCLUDING the subject, so the standalone-page row and the HX-01-overlay cell for the same player are guaranteed identical (no off-by-one between "all players" and "all other players" framings); below-threshold subjects render `qualified=False` and the view emits `— (need N+ rounds)`. **Query params:** `?threshold=<int>` (default `5`, clamped `≥ 0` — negatives and non-int strings fall back to the default) and `?display=mean|median` (default `mean` — any other value falls back to `mean`). **Cache strategy:** Django cache framework (`django.core.cache.cache`) keyed by a global version int — `role_benchmark_version` carries the int; `role_benchmark:v{version}:{role}:{stat}` carries the per-`(role, stat)` samples; the cache helper `teams/role_benchmarks_cache.py` exposes `get_all_benchmark_data(threshold)`, `get_role_benchmark_samples(role, stat)`, `invalidate_role_benchmarks()`. A **single full-table scan on miss** fills every `(role, stat)` key for all 5 roles × 12 stats in one pass, so the second request for any cell after invalidation is a cache hit. Samples-per-`(role, stat)` are cached **threshold-independent** (raw populations); `apply_threshold` runs per request against the cached samples so changing the query param does not bust the cache. **Invalidation triggers:** `post_save` + `post_delete` signals on `PlayerRoundState` (registered in `teams/signals.py::_bump_role_benchmark_version`) **plus** a one-line lazy-import `invalidate_role_benchmarks()` call inside `BatchSimulator._flush_to_db` immediately before the final return — the simulator hook is required because `_flush_to_db` uses `bulk_create` which **does not fire `post_save`**, so the signal alone would miss every batch-simulated round; the lazy import inside the simulator avoids any `teams ↔ matches` circular-import risk. **HX-01 → STAT_KEYS overlay mapping (v1):** only `avg_points → points_scored` and `avg_accuracy_pct → accuracy` map onto a STAT_KEYS entry and so receive benchmark cells; the other three HX-01 display stats (`tag_ratio`, `avg_survival_ticks`, `avg_sp_earned`) render `<td class="benchmark-na">—</td>` until HX-01b extends the per-role table to the full 12-row set. **Empty-population UX:** `n=0` cells render `—` + `n = 0`; below-threshold subject cells render `— (need N+ rounds)` (substituting the active `threshold` value). Template surface: NEW `templates/teams/role_benchmarks.html` (DOM ids `benchmark-filter-form`, `benchmark-threshold-input`, `benchmark-display-toggle`, `benchmark-table-{role}` ×5, `benchmark-row-{role}-{stat}`, `benchmark-no-data-notice`) and EXTENDED `templates/teams/player_career_stats.html` (new cells `benchmark-{role}-{stat_key}-{mean|median|delta|percentile|n}`, class `benchmark-na`, anchor `role-benchmarks-link`). View context — `role_benchmarks(request)`: `{min_rounds, display, roles, benchmarks, stat_keys}`; `player_career_stats` additive context (on top of HX-01's six keys): `{min_rounds, display, stat_keys, per_role_with_benchmarks}`. Tests live in three NEW files: `teams/tests/test_role_benchmarks.py` (pure-unit: 6 functions × empty / single-player / multi-player / threshold / mvp-derived / accuracy-ratio shape / subject-inclusion / percentile-max-is-100 / "no Django imports leaked" defensive check), `teams/tests/test_role_benchmarks_view.py` (Django `TestCase`: standalone page 200 + 5 role tables + threshold/display query-param parsing + malformed-param fallback + empty-population substring + below-threshold substring + HX-01 page extension surfaces the new context keys), `teams/tests/test_role_benchmarks_cache.py` (Django `TestCase`: signal bump invalidates cached version, `_flush_to_db` invalidation, single-scan-fills-all-keys hit-rate check, threshold-independence of cached samples). **Determinism / scope:** **read-only views** — no RNG, no simulation, no `_flush_to_db` simulation change (the one-line `invalidate_role_benchmarks()` cache-bust is not a simulation mechanic), **no SIM-07 / SIM-08 contract interaction, no Score Calibration re-baseline obligation, no DB column / no migration, no ADR** (decisions are reversible: pure module + cache helper + read-only views), **no CONTEXT.md edit** (the `Role benchmark` and `Percentile rank` terms were added inline during the grilling session that produced this contract). Locked names — URL `GET /players/benchmarks/` (URL name `role_benchmarks`); views `role_benchmarks` + `player_career_stats`; pure module `teams/role_benchmarks.py`; cache helper `teams/role_benchmarks_cache.py`; signal handler `teams/signals.py::_bump_role_benchmark_version`; simulator hook `invalidate_role_benchmarks()` call inside `BatchSimulator._flush_to_db`; templates `templates/teams/role_benchmarks.html` (new) + `templates/teams/player_career_stats.html` (extended); DOM ids `benchmark-filter-form` / `benchmark-threshold-input` / `benchmark-display-toggle` / `benchmark-table-{role}` / `benchmark-row-{role}-{stat}` / `benchmark-no-data-notice` / `benchmark-{role}-{stat_key}-{mean|median|delta|percentile|n}` / `role-benchmarks-link`; class `benchmark-na`; test file paths `teams/tests/test_role_benchmarks.py` + `teams/tests/test_role_benchmarks_view.py` + `teams/tests/test_role_benchmarks_cache.py`; context keys `min_rounds, display, roles, benchmarks, stat_keys, per_role_with_benchmarks`; cache keys `role_benchmark:v{version}:{role}:{stat}` + `role_benchmark_version`; query params `?threshold=<int>` (default 5, clamp ≥ 0) + `?display=mean|median` (default `mean`). Seam contract: [`.claude/worktrees/hx-02-seam-contract.md`](.claude/worktrees/hx-02-seam-contract.md).

### HX-03 · Head-to-head record

`/matches/h2h/?team_a=<id>&team_b=<id>` — W/L record, avg score margin, avg survivors, 
most impactful player across all H2H matches.
- completed
- note: read-only head-to-head analytics surface at `GET /matches/h2h/?team_a=<id>&team_b=<id>` (URL name `head_to_head`, view `matches/views.py::head_to_head`, template `templates/matches/head_to_head.html`) — mirrors the RV-01 4-mode read-only view pattern (`picker` when either `team_a` or `team_b` is missing, `404` via `get_object_or_404` when either id does not resolve, `error` with `error_message` + the picker re-rendered above the banner when `team_a_id == team_b_id`, `results` when both ids are valid + distinct, including the empty-history sub-case which still renders `mode="results"` with zeroed aggregates and a `h2h-no-games-notice` block). **Corpus** is the unified basket of every H2H **Match** (`{team_red, team_blue} == {team_a, team_b}`, `is_completed=True`) plus every standalone H2H **Round** (no `Match` parent) under **Side-agnostic Team-id pairing** (orientation-independent — a Team that played red in one game and blue in another still pairs by Team id, matching the RV-01 "shares a team" precedent). **Match record** W/L/T comes from `Match.winner_id` over Matches only (`is_completed=True`); `winner_id NULL → T`, `== team_a_id → W`, `== team_b_id → L`, and a defensive winner id that is neither (legacy/corrupt) counts as a tie. **Round record** W/L/T is per-Round across the unified basket (the 2 Rounds of each H2H Match plus every standalone H2H Round); a Round's winner is the higher-scoring side, equal scores tie. **Score margin** is the mean of `(team_a_score − team_b_score)` per Round across the unified basket, **signed per Round from team_a's perspective** (the view normalises by flipping red/blue when `game_round.team_red_id != team_a_id` so the pure module always sees team_a-perspective scores). **Avg survivors** is two numbers — per-team mean of `count(PlayerRoundState.final_lives > 0)` per Round (team_a's avg, team_b's avg). **Most impactful player** is **cumulative `get_mvp` per team with per-Round attribution**: `PlayerRoundState.team_color` ∈ `{"red","blue"}` is mapped to that Round's `team_red_id` / `team_blue_id` and resolved against `{team_a_id, team_b_id}` *per Round*, so a player who switched teams between H2H games can appear in BOTH per-team pools (with their MVP from games on that team only, since attribution happens once per `PlayerRoundState` row). One winner per team is reported — highest sum on each team's pool; tiebreaker locked as lower `player_id`. **Per-map breakdown** is a one-row-per-`arena_map_id`-observed table (games / W / L / T / mean margin), sorted by games desc with `arena_map_id` asc tiebreaker and `None` last; map-less rounds collapse to a single row labelled `"No map (3-zone)"`. Two **Chart.js** surfaces: a stepped-line **margin over time** chart (canvas `h2h-margin-chart` fed by `json_script` id `h2h-margin-series`, signed margin per Round chronologically with a zero reference) and a stepped-line **cumulative W/L** chart (canvas `h2h-cumulative-wl-chart` fed by `json_script` id `h2h-cumulative-wl-series`, cumulative `team_a_wins − team_b_wins` Round-level; ties don't move the running diff). Three **query params** with **HX-02 forgiving fallback for invalid input** (silently ignored, not 400): `?team_a=&team_b=` (both required for results; either missing → picker), `?provenance=all|real|sim` (default `all`; filters `GameRound.is_simulated` — `real` ⇒ `False`, `sim` ⇒ `True`, `all` ⇒ no filter; **Match-record provenance rule:** when `provenance != "all"`, BOTH Rounds of a Match must match the filter for that Match to count in the Match record — conservative; locked), and `?from=YYYY-MM-DD&to=YYYY-MM-DD` (both optional, default unbounded, invalid silently treated as unbounded on that side; filters `Match.date_played` for the Match record and `GameRound.date_played` for the Round corpus). The aggregation seam is a **NEW pure module** `matches/h2h_stats.py` — **pure Python, no Django imports, no ORM, no RNG, no I/O** (frozen import allowlist: `typing.Iterable`, `typing.Mapping`, `typing.Sequence`, `collections.defaultdict`; the "no Django imports leaked" defensive check `TestNoDjangoImportsLeaked` walks `sys.modules` from a fresh subprocess and asserts no `django*` module loaded — mirrors the HX-01 / HX-02 / RES-04 / RV-03 precedent) exposing **eight** public functions: `compute_match_record`, `compute_round_record`, `compute_score_margin`, `compute_avg_survivors`, `top_impactful_per_team`, `compute_per_map_breakdown`, `margin_series`, `cumulative_wl_series` — every signature returns zeros (or `[]` / `None`) on empty input, never raises. **Seven module-level view helpers** in `matches/views.py` (RV-01 pattern, all flat `_*`-prefixed): `_parse_provenance`, `_parse_date`, `_h2h_matches_qs`, `_h2h_rounds_qs`, `_normalize_round`, `_team_a_or_b`, `_build_player_rounds`, `_build_detail_list`, plus the public `head_to_head(request)` view that assembles everything. The view ↔ pure-module **seam** is **three flat dict lists**: `matches_list` (one entry per H2H Match — `match_id`, `winner_team_id` (`None` = tie), `date_played`, `is_simulated` carried for downstream display), `rounds_list` (one entry per Round in the unified basket, already normalised from team_a perspective — `round_id`, `date_played`, `team_a_score`, `team_b_score`, `team_a_survivors`, `team_b_survivors`, `match_id` (`None` = standalone), `arena_map_id`, `arena_map_name` (`None` when null), `is_simulated`), and `player_rounds_list` (one entry per `PlayerRoundState`, already attributed by the view via per-Round `team_color` resolution — `player_id`, `player_name`, `team_id`, `mvp` (`PlayerRoundState.get_mvp` — **property, no parentheses**), `round_id`). **Locked DOM ids:** picker `h2h-picker-form` / `h2h-select-a` / `h2h-select-b` / `h2h-provenance` / `h2h-from` / `h2h-to` / `h2h-submit`; results headline `h2h-match-record` (wraps "W-L-T") / `h2h-round-record` / `h2h-score-margin` / `h2h-team-a-survivors` / `h2h-team-b-survivors` / `h2h-top-impactful-a` / `h2h-top-impactful-b`; sections `h2h-per-map-table` / `h2h-detail-list` / `h2h-no-games-notice` (only rendered when `match_record.n == 0` AND `round_record.n == 0`); error `h2h-error-banner`; charts canvas `h2h-margin-chart` / `h2h-cumulative-wl-chart` and `json_script` ids `h2h-margin-series` / `h2h-cumulative-wl-series`. **Two entry points** (template-only edits, no view-level changes): a "View Head-to-Head" anchor in `templates/matches/match_list.html` (sibling to the existing "Compare Rounds" button, links to `{% url 'head_to_head' %}` with no params → picker mode), and a per-opponent "vs. {opponent} — H2H" link in `templates/matches/team_history.html` (rendered by view `team_match_history`) that pre-fills both team ids via `{% url 'head_to_head' %}?team_a={{ team.id }}&team_b={{ opponent.id }}`. Time display uses Django's `|date:"Y-m-d H:i"` filter on real wall-clock `date_played` (not the TIME-01 `÷2` tick filter — this surface is not tick-based). **Determinism / scope:** **read-only view** — no RNG, no simulation, no `_flush_to_db` touch, no SIM-07 / SIM-08 contract interaction, no Score Calibration re-baseline; **no model change, no migration, no ADR** (decisions are reversible: pure read-only view + pure aggregation module), **no CONTEXT.md edit** (the **Head-to-head record** term was added during the grilling session that produced this contract and already lives at the bottom of the `### Analytics and review` section). Tests live in **NEW** `matches/tests/test_h2h_stats.py` (pure-unit, no Django imports, no DB — `TestComputeMatchRecord`, `TestComputeRoundRecord`, `TestComputeScoreMargin`, `TestComputeAvgSurvivors`, `TestTopImpactfulPerTeam`, `TestComputePerMapBreakdown`, `TestMarginSeries`, `TestCumulativeWlSeries`, `TestNoDjangoImportsLeaked`) and an EXTENSION to `matches/tests/views_tests.py` (new `TestHx03HeadToHead` Django `TestCase` covering all 20 locked test names — picker / 404 / error / empty-results / full-results headline DOM ids / per-map breakdown / detail list / top-impactful / charts / `provenance=real|sim|invalid` / `from`/`to` date filtering / invalid-date silently-ignored / Side-agnostic pairing / team-switcher per-Round attribution / `is_completed=True` Match filter / Match-record both-Rounds-must-match-provenance rule). Seam contract: [`.claude/worktrees/hx-03-seam-contract.md`](.claude/worktrees/hx-03-seam-contract.md).

### HX-04 · player head-to-head record

`/matches/h2h/player/?player_a=<id>&player_b=<id>` — when on opposite teams: W/L, avg score margin,
average tags vs tagged, filters by role or none
- completed
- note: read-only player head-to-head analytics surface at `GET /matches/h2h/player/?player_a=<id>&player_b=<id>` (URL name `player_head_to_head`, view `matches/views.py::player_head_to_head`, template `templates/matches/player_head_to_head.html`) — mirrors the HX-03 4-mode read-only view pattern (`picker` when either `player_a` or `player_b` is missing, `404` via `get_object_or_404` when either id does not resolve, `error` with `error_message` + the picker re-rendered above the banner when `player_a_id == player_b_id`, `results` when both ids are valid + distinct, including the empty-basket sub-case which still renders `mode="results"` with zeroed aggregates and a `player-h2h-no-games-notice` block). **Corpus** is the per-Round opposite-teams basket — a `GameRound` qualifies iff both Players appeared with **different** `PlayerRoundState.team_color` (same-team Rounds excluded entirely, no fallback display, no "of which N on the same team" footnote). Side-agnostic attribution (a Player who switched teams between Rounds still pairs by per-Round `team_color`, never by `Team` id) — each Round is independently evaluated against the opposite-teams gate. **Round record** W/L/T is per-Round across the opposite-teams basket from player_a's perspective; equal scores tie. **Score margin** is the mean of `(player_a_team_score − player_b_team_score)` per Round, **signed per Round from player_a's perspective** (the view normalises by reading each `PlayerRoundState.team_color` against the Round's `team_red_id` / `team_blue_id` so the pure module always sees player_a-perspective scores). **Tag stats** are two symmetric floats — per-Round mean of `GameEvent(event_type="tag", actor=A, target=B)` counts and the B→A symmetric counter (independent directions — not normalised against each other), plus raw `total_tags_a_to_b` / `total_tags_b_to_a` totals and `n`. Tag-direction grouping uses a **single ORM iterate query** in `_build_player_h2h_tag_counts` (`GameEvent.objects.filter(game_round__in=rounds_qs, event_type="tag", actor_id__in={A,B}, target_id__in={A,B}).values_list("game_round_id", "actor_id", "target_id")` then Python-side group into `{round_id: (tags_a_to_b, tags_b_to_a)}` — **NOT** two `.annotate(Count())` calls, locked). **Per-role breakdown** is a one-row-per-`role_a`-observed table (games / W / L / T / mean margin / avg tags A→B / avg tags B→A) bucketed on player_a's per-Round `PlayerRoundState.role` (the *display* breakdown — regardless of what player_b played in that Round, distinct from the `?role=` filter's both-semantics gate), sorted by games desc with `role` asc tiebreaker. **Per-map breakdown** is a one-row-per-`arena_map_id`-observed table (games / W / L / T / mean margin), sorted by games desc with `arena_map_id` asc tiebreaker and `None` last; map-less rounds collapse to a single row labelled `"No map (3-zone)"`. Two **Chart.js** surfaces: a stepped-line **margin over time** chart (canvas `player-h2h-margin-chart` fed by `json_script` id `player-h2h-margin-series`, signed margin per Round chronologically with a zero reference) and a stepped-line **cumulative W/L** chart (canvas `player-h2h-cumulative-wl-chart` fed by `json_script` id `player-h2h-cumulative-wl-series`, cumulative `player_a_wins − player_b_wins` Round-level; ties don't move the running diff). Four **query params** with **HX-02 forgiving fallback for invalid input** (silently ignored, not 400): `?player_a=&player_b=` (both required for results; either missing → picker), `?role=<role>` (optional, default *any* — when set the basket is restricted to Rounds where **both** Players played that role per `PlayerRoundState.role`; **both-semantics**, locked; invalid role string silently ignored), `?provenance=all|real|sim` (default `all`; filters `GameRound.is_simulated` — `real` ⇒ `False`, `sim` ⇒ `True`, `all` ⇒ no filter), and `?from=YYYY-MM-DD&to=YYYY-MM-DD` (both optional, default unbounded, invalid silently treated as unbounded on that side; filters `GameRound.date_played`). The aggregation seam is a **NEW pure module** `matches/player_h2h_stats.py` — **pure Python, no Django imports, no ORM, no RNG, no I/O** (frozen import allowlist: `typing.Iterable`, `typing.Mapping`, `typing.Sequence`, `collections.defaultdict`; the "no Django imports leaked" defensive check `TestNoDjangoImportsLeaked` walks `sys.modules` from a fresh subprocess and asserts no `django*` module loaded — mirrors the HX-01 / HX-02 / HX-03 / RES-04 / RV-03 precedent) exposing **seven** public functions: `compute_round_record`, `compute_score_margin`, `compute_tag_stats`, `compute_per_role_breakdown`, `compute_per_map_breakdown`, `margin_series`, `cumulative_wl_series` — every signature returns zeros (or `[]`) on empty input, never raises. **Five module-level view helpers** in `matches/views.py` (RV-01 pattern, all flat `_*`-prefixed): `_player_h2h_rounds_qs` (filters to Rounds where **both** Players have a `PlayerRoundState` row + date + provenance; does **NOT** apply the opposite-teams gate or role filter), `_normalize_player_round` (returns the `rounds_list` shape keyed from player_a's perspective; returns **`None`** when `prs_a.team_color == prs_b.team_color` — the same-team gate the caller filters out), `_build_player_h2h_tag_counts` (the single ORM iterate query above), `_filter_by_role_both` (applies the both-semantics `?role=` filter post-normalisation; passthrough on `None` / empty / invalid role), `_build_player_h2h_detail_list` (reverse-chronological list, one row per Round in the basket with display fields), plus the public `player_head_to_head(request)` view that assembles everything. **REUSES** the existing HX-03 `_parse_provenance` / `_parse_date` helpers in-place (no duplication). The view ↔ pure-module **seam** is a **single flat dict list** `rounds_list` (one entry per Round in the opposite-teams basket after all filters, already normalised from player_a's perspective by the view) with **exactly 12 keys**: `round_id, date_played, player_a_team_score, player_b_team_score, tags_a_to_b, tags_b_to_a, role_a, role_b, match_id, arena_map_id, arena_map_name, is_simulated`. **17 frozen context keys:** `mode, error_message, player_a, player_b, all_players, role, provenance, date_from, date_to, round_record, score_margin, tag_stats, per_role_breakdown, per_map_breakdown, detail_list, margin_series, cumulative_wl_series` (picker/error modes still ship `all_players` / `role` / `provenance` / `date_from` / `date_to` so the form re-renders with prior selections). **Locked DOM ids:** picker `player-h2h-picker-form` / `player-h2h-select-a` / `player-h2h-select-b` / `player-h2h-role` / `player-h2h-provenance` / `player-h2h-from` / `player-h2h-to` / `player-h2h-submit`; results headline `player-h2h-round-record` (wraps "W-L-T") / `player-h2h-score-margin` / `player-h2h-tags-a-to-b` / `player-h2h-tags-b-to-a`; sections `player-h2h-per-role-table` / `player-h2h-per-map-table` / `player-h2h-detail-list` / `player-h2h-no-games-notice` (only rendered when `round_record.n == 0`); error `player-h2h-error-banner`; charts canvas `player-h2h-margin-chart` / `player-h2h-cumulative-wl-chart` and `json_script` ids `player-h2h-margin-series` / `player-h2h-cumulative-wl-series`; entry-point anchor `player-h2h-link`. **Single entry point** (template-only edit, no view-level change): a `player-h2h-link` "Head-to-head" outline-button anchor in `templates/teams/player_career_stats.html` header sibling to the existing `role-benchmarks-link` anchor, linking to `{% url 'player_head_to_head' %}?player_a={{ player.id }}` (pre-fills the `player_a` slot only; picker prompts for `player_b`). **No top-nav / match_list / team-history entry point** — career page only, locked. Time display uses Django's `|date:"Y-m-d H:i"` filter on real wall-clock `date_played` (not the TIME-01 `÷2` tick filter — this surface is not tick-based). **Determinism / scope:** **read-only view** — no RNG, no simulation, no `_flush_to_db` touch, no SIM-07 / SIM-08 contract interaction, no Score Calibration re-baseline; **no model change, no migration, no ADR** (decisions are reversible: pure read-only view + pure aggregation module), **no CONTEXT.md edit** (the **Player head-to-head record** term was added during the grilling session that produced this contract and already lives at the bottom of the `### Analytics and review` section adjacent to **Head-to-head record**). Tests live in **NEW** `matches/tests/test_player_h2h_stats.py` (pure-unit, no Django imports, no DB — `TestComputeRoundRecord`, `TestComputeScoreMargin`, `TestComputeTagStats`, `TestComputePerRoleBreakdown`, `TestComputePerMapBreakdown`, `TestMarginSeries`, `TestCumulativeWlSeries`, `TestNoDjangoImportsLeaked`) and an EXTENSION to `matches/tests/views_tests.py` (new `TestHx04PlayerHeadToHead` Django `TestCase` covering all 24 locked test names — picker / 404 / error / empty-basket-results / same-team Rounds excluded / opposite-teams Round included / full-results headline DOM ids / per-role breakdown / per-map breakdown / detail list reverse-chronological / charts / asymmetric tag direction A→B vs B→A / role both-semantics include + exclude + invalid silently ignored / `provenance=real|sim|invalid` / `from`/`to` date filtering / invalid-date silently ignored / Side-agnostic per-Round `team_color` attribution / career-page anchor pre-fills `player_a`). Seam contract: [`.claude/worktrees/hx-04-seam-contract.md`](.claude/worktrees/hx-04-seam-contract.md).

---

---

## Phase 5 — Infrastructure & League System

### API-01 · Migrate to PostgreSQL for production

See DEPLOY-05 in Phase 7 — the two are the same work and should be done together.
- completed: see DEPLOY-05 above.

### API-02 · Read-only REST API

Add Django REST Framework. Endpoints: `GET /api/teams/`, `GET /api/teams/<id>/`,
`GET /api/matches/<id>/`, `GET /api/rounds/<id>/`, `GET /api/rounds/<id>/events/`. Pagination (default 20). 
Token auth for API consumers; session auth for web views.
- completed: `djangorestframework>=3.15` added; `REST_FRAMEWORK` config in settings (SessionAuthentication, AllowAny, PageNumberPagination PAGE_SIZE=20); endpoints `/api/teams/`, `/api/teams/<id>/`, `/api/players/`, `/api/players/<id>/`, `/api/matches/`, `/api/matches/<id>/`, `/api/rounds/`, `/api/rounds/<id>/`, `/api/rounds/<id>/events/` all implemented via DRF DefaultRouter. List/detail serializer split on teams (PlayerInlineSerializer in list) and rounds (GameRoundListSerializer omits player_states). Token auth deferred to Phase 8 — session auth only for now. `rest_framework` added to mypy.ini ignore list.

### API-03 · Async batch simulation endpoint

`POST /api/simulate-batch/` — returns `job_id` immediately. Background worker via **Celery + Redis**
(Fly.io Upstash free Redis add-on) processes the job.
`GET /api/simulate-batch/<job_id>/` polls status and returns results. Frontend progress bar. Jobs expire after 1 hour.
- completed
- note: ships the REST async endpoint pair `POST /api/simulate-batch/` (URL name `api_simulate_batch`, view `SimulateBatchAPIView`) + `GET /api/simulate-batch/<str:job_id>/` (URL name `api_simulate_batch_status`, view `SimulateBatchStatusAPIView`) **and unifies both UI batch flows (`/matches/simulate-batch/`, `/matches/save-games/`) onto the same Celery + Redis execution path** ([ADR-0013](docs/adr/0013-async-batch-execution-via-celery-redis.md)) — the SIM-10 / SIM-11 in-process Job dicts and their machinery are retired in the same PR: `_BATCH_JOBS`, `_SAVE_JOBS`, `_JOBS_LOCK`, `_run_batch_job`, `_run_save_job`, and `_workers_for` are **all deleted** from `matches/views.py` (the `threading` import follows when unused elsewhere). Replaced by two `@shared_task(bind=True)` definitions in a NEW `matches/tasks.py`: `simulate_batch_task(self, team_red_id, team_blue_id, n, arena_map_id=None, master_seed=None) -> dict` (pinned `name="matches.simulate_batch"`) — resolves teams + optional ArenaMap (stale id falls back to `None` via `try/except ArenaMap.DoesNotExist`, preserving the SIM-09/10 stale-id semantics), drives `BatchSimulator().run_incremental(..., workers=1)`, emits each snapshot via `self.update_state(state="PROGRESS", meta=snap)` where `snap == {"completed": int, "total": int, "aggregate": dict}`, returns the final `snap["aggregate"]` on generator exhaustion (matching `BatchSimulator.run()`'s return shape exactly), and ends in `finally: django.db.close_old_connections()`; and `save_games_task(self, team_red_id, team_blue_id, seeds, n, arena_map_id=None) -> dict` (pinned `name="matches.save_games"`) — replays carried `(seed, flipped)` pairs through `BatchSimulator().save_games(...)` and returns `{"round_ids": [gr.id for gr in game_rounds]}` with the same arena_map fallback + `close_old_connections` `finally`. Both task `name=` strings are pinned (not module-dotted paths — the module may move). Three new view-layer helpers replace the deleted dict reads, all flat `_`-prefixed module-level (RV-01 pattern, pure beyond the `AsyncResult` they consume): `_celery_state_to_job_status(state: str) -> str`, `_build_batch_status_response(async_result, *, team_red_id, team_blue_id, arena_map_id) -> dict`, `_build_save_status_response(async_result) -> dict`. **Status mapping (locked at the view boundary — never expose raw Celery states)**: `PENDING`/`STARTED`/`PROGRESS`/`RETRY`/unknown → `"running"`; `SUCCESS` → `"complete"`; `FAILURE`/`REVOKED` → `"error"` (the defensive `"running"` fallback keeps polling alive on unknown states). The polling JSON shape is preserved verbatim from SIM-10 — batch: `{status, completed, total, partial, error, team_red_id, team_blue_id, arena_map_id}`; save: `{status, error, round_ids}` — with `team_red_id` / `team_blue_id` / `arena_map_id` **carried as query params** on every polling GET (POST response includes them; polling JS appends them to the status URL — locked over the `result_extended=True` and `request.session` alternatives because it is stateless and adds no backend cost). The pre-API-03 save-status string `"done"` is renamed to `"complete"` for vocabulary consistency with the batch flow + CONTEXT.md `Job status`; the polling JS in `batch_simulate.html` updates its one save-branch string compare from `data.status === "done"` to `=== "complete"`. The four URL names + the two new REST URL names: `simulate_batch` / `batch_simulate_status` / `save_batch_games` / `save_batch_status` are **preserved** (paths and names unchanged, only view bodies rewritten on Celery); `api_simulate_batch` (`POST /api/simulate-batch/`) and `api_simulate_batch_status` (`GET /api/simulate-batch/<str:job_id>/`) are **new** — mounted in `laserforce_simulator/api_urls.py` after `urlpatterns = router.urls` (DRF `DefaultRouter` only registers ViewSets; `APIView` needs plain `path()` entries appended). REST input validation uses an inline `SimulateBatchRequestSerializer(serializers.Serializer)` with `team_red`/`team_blue`/`n`/`arena_map?`/`master_seed?` (Forms-vs-Serializers is the locked DRF idiom — UI POST still uses `BatchSimulateForm`); same-team rejection + `roster_errors` mirror the UI checks (400 + `{"detail": "<msg>"}`); REST POST response shape is **identical** to the UI POST (`{job_id, team_red_id, team_red_name, team_blue_id, team_blue_name, arena_map_id, n}`); `master_seed` is accepted on the REST POST only (UI form has no field for it) and is plumbed for test pinning / scripted-run convenience — **not** a user-facing knob (locked scope-out). Both REST views inherit `AllowAny` from the API-02 `REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"]` default (deferred-auth precedent, pinned by `TestAPIInheritsAllowAnyPermissions`). The SIM-09 `save_games` flow seam is **unchanged** — only the executor swapped (`save_games_task.delay(...)` replaces `threading.Thread(target=_run_save_job, ...)`); `BatchSimulator.save_games` is untouched, flipped-round actual-side persistence semantics are unchanged, the `request.session["batch_seeds"]` handover is read in the same shape, and the SIM-10 single-write session guard (FIRST poll observing `complete` writes `batch_seeds` with the `"job_id"` marker; subsequent polls skip) is preserved verbatim — only the source of `aggregate` changes from `_BATCH_JOBS[job_id]["partial"]` to `async_result.result`. The SIM-11 in-process `_workers_for(n)` helper is **retired**: the threshold/cap heuristic was tied to a single-process `ProcessPoolExecutor` and does not apply to broker-distributed tasks; horizontal scaling moves to the Celery `--concurrency` knob (one knob, not two stacked). NEW `laserforce_simulator/celery.py`: `celery_app = Celery("laserforce_simulator")` + `config_from_object("django.conf:settings", namespace="CELERY")` + `autodiscover_tasks()`; re-exported from `laserforce_simulator/__init__.py` as `celery_app` so the worker resolves the app. Settings (`laserforce_simulator/settings.py`) gain a `CELERY_*` block appended after `REST_FRAMEWORK`: `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` default `redis://localhost:6379/0` via `decouple.config`, `CELERY_RESULT_EXPIRES = 3600` (1h Job TTL, per PLAN.md), JSON serializer across the board, `CELERY_TASK_ALWAYS_EAGER = config("LF_CELERY_EAGER", default=False, cast=bool)`, and `CELERY_TASK_EAGER_PROPAGATES = True` so task failures surface as exceptions under EAGER rather than silent `FAILURE`. Tests run under EAGER — `conftest.py` does `os.environ.setdefault("LF_CELERY_EAGER", "1")` at module load (no separate `pytest.ini` env block, no `pytest-celery` dependency — explicitly rejected, EAGER suffices). NEW `requirements.txt` line `celery[redis]>=5.3`. **Expiry asymmetry (locked, CONTEXT.md `Job id`)**: polling a Job id whose result has expired (1h TTL) resolves to Celery `PENDING`, indistinguishable from a never-submitted id; the status mapping above maps `PENDING` → `"running"`, so the UI polls forever on an expired id — no special-case fallback path. Template (`templates/matches/batch_simulate.html`) DOM ids are **untouched** (every id the polling JS reads — `batch-form`, `batch-progress-bar`, the per-team / per-side score cells, the `scoreChart` canvas, `batch-save-games`, `avgN`/`outlierN`/`saveStatus` — preserved); JS edits are minimal: `poll(jobId)` appends `?team_red_id=&team_blue_id=&arena_map_id=` using the POST response values, the save-branch string compare flips `"done"` → `"complete"`, and the `'not_found'` branch is dropped (the Celery path returns 200 + running for unknown ids, never 404). **Test surgery in `matches/tests/views_tests.py`**: 8 classes deleted with the `_BATCH_JOBS` / `_SAVE_JOBS` machinery — `TestSim10SimulateBatchPostReturnsJson` (~L761), `TestSim10BatchSimulateStatusShape` (~L840), `TestSim10BatchSimulateStatusLifecycle` (~L897), `TestSim10BatchSimulateStatusErrorPath` (~L970), `TestSim10BatchSimulateStatusNotFound` (~L1014), `TestSim10SessionHandoverWritesOnceOnComplete` (~L1031), `TestSim11WorkersFor` (~L1161), `TestSim11RunBatchJobPassesWorkers` (~L1218) — and the SIM-09 `TestSim09BatchArenaMapPlumbing` class (~L472) is split: its arena_map plumbing methods are rewritten against `save_games_task` / `simulate_batch_task` (e.g. `test_save_batch_games_enqueues_save_games_task_with_arena_map`) and migrate to the two new test files. **20 NEW test classes across two NEW test files.** `matches/tests/test_api03_tasks.py` (8 classes): `TestSimulateBatchTaskHappyPath`, `TestSimulateBatchTaskProgressUpdates` (spies on `Task.update_state` and asserts the `{completed, total, aggregate}` snapshot shape), `TestSimulateBatchTaskWithMap` (real id + stale id fallback), `TestSimulateBatchTaskDeterminism` (same `master_seed` ⇒ identical `.result`), `TestSimulateBatchTaskFailFast` (`pytest.raises(ValueError)` under `EAGER_PROPAGATES`), `TestSaveGamesTaskHappyPath`, `TestSaveGamesTaskWithMap` (real id + `None` + stale id, consolidates the four rewritten arena_map plumbing tests), `TestSaveGamesTaskInvalidTeam`. `matches/tests/test_api03_views.py` (12 classes): `TestSimulateBatchPostUIReturnsJobId`, `TestBatchSimulateStatusEager` (assert `status="complete"`, `partial == final aggregate`, query-param echo), `TestBatchSimulateStatusError`, `TestBatchSimulateStatusUnknownJobId` (PENDING → running with completed=0 / nulls), `TestSaveBatchGamesPost` (patches `save_games_task.delay` to assert session-stashed seeds / arena_map_id thread through; empty-session + missing-seeds 400 branches preserved), `TestSaveBatchStatusEager` (asserts `"complete"` not `"done"`), `TestSimulateBatchAPIPost` (REST POST happy path + same-team 400 + invalid-id serializer 400), `TestSimulateBatchAPIStatusEager` (identical JSON shape to UI endpoint), `TestSimulateBatchAPIStatusUnknownJobId`, `TestCeleryStateMappingHelper` (exhaustive truth table, pure-unit, no DB), `TestSessionHandoverPreservedOnComplete` (single-write `"job_id"` guard preserved verbatim, only `aggregate` source swapped), `TestAPIInheritsAllowAnyPermissions` (unauthenticated POST → 200, documents the API-02 deferred-auth precedent + prevents accidental regression). The seven `TestRunIncremental*` / `TestChunkSizeFor` classes in `matches/tests/test_sim10_incremental.py` are **preserved untouched** — they pin the `BatchSimulator.run_incremental` simulator contract (chunk-size table, partial-equals-final, serial == parallel at every boundary, fail-fast, n=0, run-drives-incremental), not the job machinery. **Determinism preservation (locked, ADR-0013)**: same `master_seed` + Orientation + rosters + map ⇒ identical games at every chunk boundary (SIM-07/08/10 contract holds in form); serial == parallel; faithful Replay; Celery-vs-direct paths produce **identical games** under `CELERY_TASK_ALWAYS_EAGER = True` (the task body is just `BatchSimulator().run_incremental(...)` in the same process) and identical aggregate output when not EAGER (same code path, executor differs). **No simulation mechanics change** → **no Score Calibration re-baseline obligation**, mirroring the SIM-10 / SIM-11 / RES-04 / RES-03 / RES-02 precedent. **Scope-out (locked, ADR-0013)**: no `fly.toml` change (the `processes = ["app", "worker"]` and Upstash secret addition are deployment context deferred to a separate deploy task); no `Dockerfile` change (worker entrypoint variant deferred); no CI Redis provisioning (`ci.yml` workflow needs no broker — EAGER suffices); no token auth on `/api/` (AllowAny inherits from API-02); no `master_seed` UI exposure (REST-only, test convenience); no cancel-in-flight UX (`AsyncResult.revoke` exists but no UI ships); no `Job` persistence past 1h TTL (no DB row, no migration, no cron sweep); no `score_averages` CLI change (the management command stays a foreground `BatchSimulator().run(...)` caller); no `_aggregate_batch` / `run_incremental` / `run` / `save_games` / `BatchSimulateForm` change; no new CONTEXT.md term beyond the three already added by the grill (`Job`, `Job id`, `Job status` — the **Batch run** term already mentions Celery); no new ADR beyond ADR-0013; no migration. Seam contract: [`.claude/worktrees/api-03-seam-contract.md`](.claude/worktrees/api-03-seam-contract.md).

### LG-00 · Player Generation Tools

Generate a full set of randomized players for a league, season, or tournament. The generation UI accepts:
- Number of teams and players per team
- Bell curve mean and variance for stat distribution (configurable per generation run)

Stats are randomized on the configured bell curve. Intended to bootstrap new leagues quickly.
- completed
- note: bulk player/team generation surface at `GET /teams/generate/` (URL name `generate_players`, view `teams/views.py::generate_players`, templates `templates/teams/generate_players.html` form + `templates/teams/generate_players_done.html` confirmation). Two output modes selected by the form's `num_teams` dropdown: `num_teams ≥ 1` (choices `"2".."20"` plus `"random_2_10"`) creates new **Teams** with auto-filled 6-slot rosters + optional bench when `players_per_team > 6`; `num_teams == "0"` creates a flat free-agent pool of 12–100 players on the reserved **Free Agents Team** (CONTEXT.md — magic name `"Free Agents"`, no `is_system` field, no model change, no migration). The generation algorithm lives in a NEW pure module `teams/player_generator.py` — **pure Python, no Django imports, no ORM, no settings access, no I/O, no global RNG** (frozen import allowlist: `random` for `Random` type-hint only with the RNG **injected** by the caller, `typing` for annotations, optional `collections`; the "no Django imports leaked" defensive check mirrors the RES-04 / RV-03 / HX-01 / HX-02 / HX-03 / HX-04 precedent) — exposing **three** public functions: `draw_stats(rng: random.Random, mean: float, std_dev: float) -> dict[str, int]` (returns a dict with exactly 19 keys in the module-level `_STAT_FIELDS` canonical order — 3 awareness + 1 decision + 5 physical + 2 team + 8 role; each value is `max(0, min(100, round(rng.gauss(mean, std_dev))))`, draws made in `_STAT_FIELDS` order to keep RNG consumption deterministic; **NOTE** the field name `Offensive_synergy` is **intentionally capital-O** to match the existing `Player` model field byte-for-byte so the view's `Player(**stats)` splat lands), `draw_preferred_roles(rng) -> list[str]` (returns 1–3 unique role names from the module-local `_ROLE_NAMES` tuple `("commander", "heavy", "scout", "medic", "ammo")` with a `70/20/10` count distribution via `rng.choices([1,2,3], weights=[70,20,10], k=1)[0]` then `rng.sample(_ROLE_NAMES, n)`), and `assign_slots(preferred_roles_per_player) -> dict[str, int | None]` (deterministic greedy bipartite match of the first 6 players to the 6-slot `_SLOT_KEYS` tuple `("commander", "heavy", "scout_1", "scout_2", "medic", "ammo")` — canonical-slot-first iteration, lowest-unassigned-player-index tie-break, both Scout slots bound to the `"scout"` role; unmatched slots receive `None` and the view back-fills them with leftover players in ascending player-index order). The pure module hand-rolls the 5-tuple `_ROLE_NAMES` and 6-tuple `_SLOT_KEYS` locally rather than importing `matches.sim_helpers.role_constants` — keeping `teams/player_generator.py` Django-free is the contract. Form is a NEW `GenerateLeagueForm` in `teams/forms.py` with four fields: `num_teams` (`CharField` + `Select` widget, 22 choices `"0"` + `"2".."20"` + `"random_2_10"` — **`"1"` is deliberately omitted**), `players_per_team` (`CharField` + `Select` widget, 98 choices spanning `"6".."9"` + `"12".."100"` + `"random_team"` + `"random_pool"` — wide superset rendered always; no JS-driven dependent dropdown for v1), `mean` (`IntegerField`, `min_value=0`, `max_value=100`, `initial=50`), `std_dev` (`IntegerField`, `min_value=1`, `max_value=40`, `initial=15`). Cross-field validation in `clean()` raises `forms.ValidationError` with locked wording — `"Players per team must be 12–100 when generating a free-agent pool"` when `num_teams == "0"` paired with anything outside `{"random_pool"}` ∪ `{12..100}`, and `"Players per team must be 6–9 when generating teams"` when `num_teams != "0"` paired with anything outside `{"6","7","8","9","random_team"}` (tests substring-match the locked copy). View is `@transaction.atomic` end-to-end (a mid-loop raise rolls back every Team and Player write — pinned by `test_pure_module_raises_mid_generation_rolls_back`). On GET → render the form (status 200). On valid POST → resolve `random_*` markers via `random.randint(2, 10)` / `random.randint(6, 8)` / `random.randint(12, 100)`, build a fresh `rng = random.Random()` (production uses fresh entropy; no seed input field, deliberately out of scope), pre-shuffle copies of the **`TEAM_NAMES`** and **`PLAYER_NAMES`** pools, and pop from them as Teams/Players are created with **shuffle-and-suffix collision policy** — on `Team.objects.filter(name=candidate).exists()` (or per-Free-Agents-team `Player.objects.filter(team=free_agents_team, name=candidate).exists()`) the candidate gets `" #{k}"` with `k` incrementing from 2 until a free name is found; on pool exhaustion fall back to `f"{POOL[-1]} #{n}"`. Regular-teams branch: for each Team, pop a name, create the Team, pop `players_per_team` player profiles via the existing `_random_player_profile()` helper (NOT refactored — the view discards the helper's `name` field and overrides it with the popped+deduped Player-pool name; signature-stable, out of scope to take a `name` parameter), build stats with `draw_stats(rng, mean, std_dev)` and `preferred_roles` with `draw_preferred_roles(rng)`, create the Player via `Player.objects.create(team=team, **profile_minus_name, **stats, preferred_roles=...)`, then run `assign_slots(...)` on the first 6 players' preferred_roles and set the 6 `slot_*` FKs (back-filling `None` slots with leftover players, ascending player-index order); players 7+ are bench (created on the Team but never assigned to a `slot_*` FK). Free-agents branch (`num_teams == "0"`): resolve `free_agents_team = get_free_agents_team()`, create `players_per_team` Players on that Team with the per-team name-collision check, and do **NOT** touch any `slot_*` FK — the Free Agents Team stays an unfilled roster on purpose (`is_valid_roster` returns False, by design). The POST response **re-renders `generate_players_done.html` directly** (no redirect, no session round-trip — context `{"created_teams": list[Team], "free_agent_count": int}`, status 200) with locked DOM ids `generate-confirm-teams-list` (created-teams `<ul>`, rendered only when non-empty), `generate-confirm-free-agents-notice` + `generate-confirm-free-agent-count` (free-agents notice block, rendered only when `free_agent_count > 0`, copy includes the substring `"Created"` … `"free-agent players"` and the deferred-feature mention `"once it ships (LG-00c)"`). Form DOM ids: `generate-players-form`, `generate-players-num-teams`, `generate-players-per-team`, `generate-players-mean`, `generate-players-std-dev`, `generate-players-submit`. Models layer additions: a NEW `TeamManager(models.Manager)` class with a single public method `regular()` returning `self.exclude(name="Free Agents")` (assigned to `Team.objects` on the class — no migration, managers are not schema; `Team.objects.all()` is **unchanged** and continues to include the Free Agents Team, the distinction is pinned by tests), and a NEW module-level helper `get_free_agents_team() -> Team` adjacent to the `Team` class (mirrors the existing `_random_player_profile` module-level pattern, NOT a classmethod) returning `Team.objects.get_or_create(name="Free Agents")[0]` — idempotent across runs. `teams/views.py::team_list` is migrated from `Team.objects.all()` to `Team.objects.regular()` so the Free Agents Team does not appear on the Teams list — **out of scope** to migrate other `Team.objects.all()` call sites (admin / REST API / `simulate_match`) since the Free Agents Team has no filled roster and any code that iterates rosters or simulates against it already fails the `is_valid_roster` gate. Constants layer addition: a NEW `TEAM_NAMES: tuple[str, ...]` of 30–50 themed laser-tag team names appended to `teams/constants.py` (same shape and casing as the existing `PLAYER_NAMES` — module-scope `tuple[str, ...]` of plain strings; all entries unique, no surrounding whitespace; consumed only by the view, **never** imported by `teams/player_generator.py` per the pure-module allowlist). Two CONTEXT.md domain terms added under `### Teams and players`: **Free Agents Team** (the reserved system Team identified by magic name `"Free Agents"`, filtered out of the Teams list via `Team.objects.regular()`, has no slot FKs filled by design, auto-created via `get_or_create`) and **LG-00 generation** (the bulk player-creation flow at `GET /teams/generate/` with the two output modes — distinct from a roster CSV import (LG-00b) and from the per-player edit form). **Entry point:** a `"Generate Players"` anchor (DOM id `generate-players-link`) in `templates/teams/team_list.html` sibling to the existing "New Team" link, reversing `{% url 'generate_players' %}`. **Determinism / scope:** the pure module never seeds a global `random` and never touches simulator RNG; `@transaction.atomic` covers the entire POST handler so partial generation never persists; **no simulation behaviour change, no `_flush_to_db` touch, no SIM-07 / SIM-08 contract interaction, no Score Calibration re-baseline, no model field change, no migration, no ADR** (decisions are reversible — manager-class swap + module-level helper + pure module + new templates). **Out of scope (deliberate):** no per-stat or per-role bell-curve presets (a single `mean`/`std_dev` covers all 19 stats); no `is_system` Team field (Free Agents identified by magic name only); no `/players/` Players tab — that is **LG-00c**, deferred to after this task lands so the generated free-agent pool is browsable; no preview-before-commit UI (POST writes immediately); no seed input field on the form; no CSV import (LG-00b, separate task); no Season / Tournament linkage (LG-01+); no JS-driven dependent dropdown toggle for v1 (the wide superset is rendered always and `clean()` enforces the cross-field rule); no refactor of `_random_player_profile()` to accept a `name` parameter (the view discards the name the helper returns and overrides it from the popped Player-pool name). Tests live in **three NEW files** under `teams/tests/`: `test_player_generator.py` (pure-unit, no Django imports — `TestDrawStats` × 5 cases including the 19-key canonical order and the clamp-at-0-and-100 extreme-std-dev case, `TestDrawPreferredRoles` × 5 cases including the 70/20/10 count-distribution approximation over N=10_000 draws, `TestAssignSlots` × 6 cases including the deterministic-tiebreak pin and the over-prefer-scout case, and `TestNoDjangoImportsLeaked` mirroring RES-04), `test_generate_view.py` (Django `TestCase` — `TestGenerateGet`, `TestGeneratePostHappyPathTeams`, `TestGeneratePostHappyPathBenchPlayers`, `TestGeneratePostHappyPathFreeAgents`, `TestGeneratePostRandomResolutions`, `TestGeneratePostCrossFieldValidation`, `TestGeneratePostNameCollisions`, `TestGeneratePostTransactionAtomic`, `TestFreeAgentsTeamAutoCreated`), and `test_team_list_filters_free_agents.py` (Django `TestCase` — `TestObjectsRegularManagerMethod` pinning that `Team.objects.regular()` excludes `name="Free Agents"` while `Team.objects.all()` still includes it, plus `TestTeamListExcludesFreeAgents` pinning that `GET /teams/` body does not contain `"Free Agents"` even when the row exists). Seam contract: [`.claude/worktrees/lg-00-seam-contract.md`](.claude/worktrees/lg-00-seam-contract.md).

### LG-00b · Roster Import from CSV

Allow users to import a roster of players from a CSV file. Required columns: player name, role.
All 19 stat columns are optional — unspecified stats default to 50 on import.
- completed
- note: roster-import surface at `GET/POST /teams/import/` (URL name `import_roster`, view `teams/views.py::import_roster`) paired with a canonical CSV-template download at `GET /teams/import/template.csv` (URL name `import_roster_template`, view `teams/views.py::import_roster_template`); both URLs mounted via two new `path(...)` entries appended to `teams/urls.py` **above** the existing `<int:team_id>/` capture-group routes so the literal `import/` segment is unambiguous. Entry point is a single `"Import Roster"` anchor (DOM id `roster-import-link`) appended to `templates/teams/team_list.html` between the LG-00 `generate-players-link` and the existing `Create New Team` button, reversing `{% url 'import_roster' %}`. The parsing algorithm lives in a NEW pure module `teams/roster_importer.py` — **pure Python, no Django imports, no ORM, no settings access, no I/O** (frozen import allowlist: `csv` + `io` + `dataclasses` + `typing` from stdlib only; **explicitly forbidden**: `django.*`, `teams.models`, `teams.forms`, `teams.views`, `teams.constants`, `teams.player_generator`, `matches.*`, and any I/O / `os.path` module) — mirroring the RES-04 / RV-03 / HX-01 / HX-02 / LG-00 pure-module precedent with a defensive `test_no_django_imports_leaked` subprocess check. The pure module hand-rolls its 5-tuple `ROLE_NAMES` (`"commander", "heavy", "scout", "medic", "ammo"`), its 19-tuple `STAT_COLUMNS` (which must equal `teams/player_generator.py::_STAT_FIELDS` verbatim — including the **intentional capital-O** `Offensive_synergy` — pinned by a direct equality test that is the ONE allowed `teams.player_generator` import inside the pure-unit test file), its 8-tuple `REQUIRED_COLUMNS` (`team, name, role, age, started_playing_age, total_games, home_site, height`), its 20-tuple `OPTIONAL_COLUMNS` (`preferred_roles` + the 19 stat columns), its 28-tuple `ALL_COLUMNS` (the declared order that drives the template-CSV header row), its `SLOT_LIMITS = {"commander": 1, "heavy": 1, "scout": 2, "medic": 1, "ammo": 1}`, and its `PROFILE_BOUNDS = {"age": (5, 100), "started_playing_age": (3, 100), "total_games": (0, 100_000)}` rather than importing them — keeping `teams/roster_importer.py` Django-free is the contract. Module constants: `STAT_DEFAULT = 50`, `STAT_MIN = 0`, `STAT_MAX = 100`, `MAX_DATA_ROWS = 1000`, `NAME_MAX_LEN = 100`, `TEAM_NAME_MAX_LEN = 100`, `HOME_SITE_MAX_LEN = 100`, `HEIGHT_MAX_LEN = 20`. Public surface is one function plus four dataclasses/exception: `parse_roster_csv(text: str) -> ParsedRoster` (single string argument — caller owns file reading and UTF-8 decoding; the function defensively strips a single leading `"﻿"` BOM before invoking `csv.DictReader` and never raises on the first error — per-row errors accumulate into a single bundled raise); `ParsedRow(row_num, team, name, role, profile, stats, preferred_roles)` (`frozen=True` dataclass — `profile` is a 5-key dict whose keys MUST match `Player` field names byte-for-byte so the view's `Player.objects.create(**profile, **stats)` splat lands, `stats` always carries all 19 `STAT_COLUMNS` keys defaulted to 50 where blank/absent, `preferred_roles` is 0–5 unique lowercase role names); `ParsedRoster(rows, by_team)` (`frozen=True` dataclass — `rows` is the flat CSV-order list, `by_team` is the view's primary consumption shape, a `dict[str, list[ParsedRow]]` whose insertion order matches first-appearance order of each team in the file per the Python 3.7+ insertion-order guarantee); `RowError(row_num: int, field: str | None, message: str)` (`frozen=True` dataclass — hashable so tests may put them in sets; `row_num` is **1-based DATA row index** with the header being line 1 of the file and file-level errors using `row_num=0` so the template sorts errors top-to-bottom with file-level first; `field` is `None` only for whole-row or whole-file errors); and `RosterImportError(Exception)` with constructor `__init__(self, errors: list[RowError])` exposing `.errors` (the view re-renders this list verbatim and may **extend** it with DB-level slot collisions detected during the pre-flight check). Coercion rules (per-cell): `team` / `name` stripped + non-empty + ≤100 chars (empty → row error); `role` lowercased + stripped + must be in `ROLE_NAMES` (not-in → row error, `"COMMANDER"` lowercases to `"commander"` and succeeds); `age` / `started_playing_age` / `total_games` `int()` + bounds from `PROFILE_BOUNDS` (non-int / out-of-range → row error); `home_site` / `height` stripped strings, empty allowed (becomes `""`), bounded by `HOME_SITE_MAX_LEN` / `HEIGHT_MAX_LEN`; `preferred_roles` split on `","` + each entry stripped + lowercased + each non-empty entry in `ROLE_NAMES` + no duplicates within the cell (empty cell or column absent → `[]`); each of the 19 stat columns: cell omitted / blank / column absent → `STAT_DEFAULT` (`50`), else `int()` bounded `[STAT_MIN, STAT_MAX]` (non-int / out-of-range → row error). Header-level errors (missing required column, unknown column, duplicate column, > `MAX_DATA_ROWS` data rows) raise `RosterImportError` IMMEDIATELY with `row_num=0` and skip per-row parsing; per-row coercion errors accumulate across the whole file and after the loop run an extra in-file collision pass that emits four hard-reject categories: duplicate `(team, name)` pair (`RowError(row_num=<second>, field="name", ...)`), > 1 row of any non-Scout role on the same team (`RowError(row_num=<2nd>, field="role", message="Too many ... commander ... (limit 1)")`), > 2 Scouts on the same team (`RowError(row_num=<3rd>, field="role", ...)`), and (after the parser returns) DB-side slot collisions for pre-existing Teams (any already-filled non-Scout `slot_<role>` FK, or both `slot_scout_1` AND `slot_scout_2` filled when the CSV adds a Scout) detected by the view's pre-flight pass and appended to the same `RosterImportError.errors` list before any write. Form is a NEW `RosterImportForm(forms.Form)` appended to `teams/forms.py` with a single `csv_file: forms.FileField` (widget id `roster-import-file`, `accept=".csv,text/csv"`); the form-class constant `MAX_UPLOAD_BYTES = 2 * 1024 * 1024` (= 2 MiB exactly, comfortably > 1000 rows) is the **only** byte cap (row-cap enforcement is the pure module's job); `clean_csv_file` decodes the upload with `bytes.decode("utf-8-sig")` (BOM-tolerated, belt-and-suspenders with the pure module's defensive `﻿` strip) and stores the **decoded `str`** on `cleaned_data["csv_file"]` so the view consumes text, never an `UploadedFile`. Locked form error wording substrings: `"too large"` (file-too-large) and `"must be UTF-8"` (non-UTF-8) — tests substring-match both. View is `@transaction.atomic` end-to-end with explicit `transaction.set_rollback(True)` inside the `RosterImportError` catch block (the all-or-nothing transactional policy — mandatory because the view returns a 200 response after catching, which without the explicit rollback would NOT trigger `@transaction.atomic`'s automatic rollback on raise; pinned by `test_db_slot_collision_rolls_back_all_writes`). On GET → render `templates/teams/roster_import.html` with `{"form": RosterImportForm(), "errors": [], "row_errors": []}` (status 200). On invalid form POST → re-render the same template with the bound form (status 200, no DB writes). On valid POST, in order: (1) call `parse_roster_csv(form.cleaned_data["csv_file"])`; (2) run the private helper `_check_db_slot_collisions(parsed)` (its name is pinned for test monkey-patching stability) which iterates each `(team_name, rows)` in `parsed.by_team`, skips teams that do not exist, and for each existing team emits one `RowError` per DB-side slot collision before re-raising `RosterImportError`; (3) call the private helper `_apply_roster(parsed) -> (created_teams, appended_teams, player_count)` which per Team in CSV-encounter order (`get_or_create` auto-creates missing Teams, existing Teams are appended to — a team appears in only ONE of `created` / `appended` per call), creates each Player via `Player.objects.create(team=team, name=row.name, preferred_roles=row.preferred_roles, **row.profile, **row.stats)`, assigns the slot FK (`slot_<role>` for non-Scout; first-free of `slot_scout_1` / `slot_scout_2` for Scout) in memory and calls `team.save()` once after all rows for the team are processed; (4) render `templates/teams/roster_import_done.html` with context `{"created_teams": list[Team], "appended_teams": list[Team], "player_count": int, "row_count": int}` (status 200, no redirect, no session round-trip). Any `RosterImportError` caught around steps 1–3 re-renders the form page with `{"form": form, "errors": [str(exc)], "row_errors": exc.errors}` (status 200). Templates: NEW `templates/teams/roster_import.html` extends `base.html` with locked DOM ids `roster-import-form` (the `<form>`), `roster-import-file` (the `<input type="file">`), `roster-import-submit` (submit button), `roster-import-template-link` (the template-download anchor), `roster-import-errors-summary` (top-level errors `<div>`, rendered only when `errors` non-empty), `roster-import-errors` (per-row errors `<ul>`, rendered only when `row_errors` non-empty), and per-error `<li id="roster-import-error-{row_num}-{field|'row'}">` (the suffix is the literal field name when `err.field is not None`, otherwise the literal string `"row"` — e.g. `RowError(row_num=3, field="age", ...)` → `id="roster-import-error-3-age"`; `RowError(row_num=5, field=None, ...)` → `id="roster-import-error-5-row"`). NEW `templates/teams/roster_import_done.html` extends `base.html` with locked DOM ids `roster-import-confirm-summary` (the `Imported <strong>{{ player_count }}</strong> players across <strong>{{ row_count }}</strong> rows` `<div>`, locked copy substrings `"Imported"`, `"players across"`, `"rows"`), `roster-import-confirm-teams-list` (created-teams `<ul>` of `team_detail` links, rendered only when `created_teams` non-empty), and `roster-import-confirm-appended-list` (appended-teams `<ul>`, rendered only when `appended_teams` non-empty); a `"Back to Teams"` link reverses `team_list` and an `"Import another"` link reverses `import_roster`. Template-CSV body (frozen byte-for-byte in §5 of the seam contract): line 1 is the 28-column header in `ALL_COLUMNS` order; lines 2–3 are two example data rows for a single team `"Red Phoenix"` (a Commander + a Scout with the comma-split `preferred_roles="scout,medic"` cell demonstrating the auto-quoting requirement); body terminates with `csv.writer`'s default `"\r\n"` so the test reconstructs the expected body via `csv.writer` + `io.StringIO` rather than embedding a bytestring literal; response carries `Content-Type: text/csv` and `Content-Disposition: attachment; filename="roster_template.csv"`. The `role` column drives **slot assignment ONLY** (the `team.slot_<role>` FK); `preferred_roles` is a SEPARATE optional column (comma-separated within the cell) that populates the `Player.preferred_roles` JSON list — **the two columns do NOT cross-influence each other**. Strict exact-match headers (case-sensitive — including the capital-O `Offensive_synergy`); UTF-8 with BOM tolerated; comma-delimited via stdlib `csv.DictReader`; 1000-row data cap + 2 MiB byte cap; `Team.objects.get_or_create(name=...)` auto-creates missing Teams so a single CSV may seed an entire league while also appending to existing rosters. CONTEXT.md adds the **Roster import** glossary term under `### Teams and players` (added inline during the grilling session — the Docs agent does NOT re-add it). **Determinism / scope:** the CSV import is fully deterministic given the input file — no `random.*` calls in either the pure module or the view; `@transaction.atomic` covers the entire POST handler and `transaction.set_rollback(True)` inside the catch block guarantees all-or-nothing semantics even on a 200 error response; **no simulation behaviour change, no `_flush_to_db` touch, no SIM-07 / SIM-08 contract interaction, no Score Calibration re-baseline, no model field change, no migration, no ADR, no new dependency** (the pure module is stdlib-only). **Out of scope (deliberate):** no preview-before-commit UI (POST writes immediately on success); no per-team `/teams/<id>/import/` entry point (single global entry only); no editing the existing per-player Add Player flow (`player_add` view unchanged); no editing LG-00's `generate_players` view or `player_generator.py` pure module (the only cross-link is the `STAT_COLUMNS == _STAT_FIELDS` equality pin); no Django admin / REST API changes (`teams/api_views.py`, `teams/serializers.py` untouched); no async / Celery / progress bar (the 1000-row cap makes the import sub-second, foreground under `@transaction.atomic`); no JS validation / live preview; no per-row commit (it is all-or-nothing); no CSV dialect detection (comma-delimited only); no multi-file upload; no clobber / overwrite mode for existing players (the import only CREATES new Players — updating an existing Player is not in scope, and `Player.unique_together = ["team", "name"]` enforces this at the DB layer as a hard backstop); no preview-before-commit UI; no per-stat or per-role bell-curve presets (LG-00 territory); no Season / Tournament linkage (LG-01+). Tests live in **two NEW files** under `teams/tests/`: `test_roster_importer.py` (pure-unit, no Django imports — `TestHeaderValidation` × 5 cases including BOM tolerance and the 1001-row cap, `TestCoercion` × 12 cases covering every per-cell rule including empty-stat-defaults-to-50, omitted-stat-column-defaults-to-50, role case-normalisation, and the three profile-bounds rejections, `TestPreferredRoles` × 6 cases covering empty cell, absent column, comma-split parsing, whitespace + lowercasing, invalid-role rejection, within-cell duplicate rejection, `TestInFileCollisions` × 4 cases covering the duplicate `(team, name)` pair, the non-Scout slot overflow on row 2, the Scout overflow on row 3, and the 2-Scout happy path, `TestMultiErrorAccumulation` × 2 cases pinning that per-row errors bundle into a single raise and that header errors short-circuit per-row parsing, `TestRowErrorShape` × 2 cases pinning `frozen=True` + hashability, `TestParsedRosterShape` × 3 cases pinning `by_team` insertion-order grouping + flat `rows` CSV order + the `STAT_COLUMNS == _STAT_FIELDS` equality, and `TestNoDjangoImportsLeaked` mirroring `test_player_generator.py`) and `test_roster_import_view.py` (Django `TestCase` — `TestImportRosterGet` × 2 cases pinning 200 + the four locked DOM ids, `TestImportRosterPostHappyPath` × 4 cases covering the 2-teams-12-players multipart POST, slot-FK correctness, append-to-existing-team, and auto-create-missing-team, `TestImportRosterPostDbSlotCollision` × 2 cases covering existing-`slot_commander`-filled rejection and both-Scout-slots-filled rejection, `TestImportRosterPostFormErrors` × 2 cases covering the `MAX_UPLOAD_BYTES` substring and the `must be UTF-8` substring, `TestImportRosterPostParseErrors` × 3 cases covering unknown-column header error, the per-row DOM id `roster-import-error-1-accuracy`, and the multi-row error rendering, `TestImportRosterPostAtomic` × 2 cases pinning DB-collision rollback via `transaction.set_rollback(True)` and the parser-raise-writes-nothing case, `TestImportRosterTemplate` × 3 cases pinning the `Content-Disposition` header + the byte-for-byte body equality (reconstructed via `csv.writer` + `io.StringIO`) + the header-equals-`ALL_COLUMNS` assertion, and `TestEntryPointLink` × 1 case pinning the `roster-import-link` DOM id and the `"Import Roster"` substring on `GET /teams/`). Seam contract: [`.claude/worktrees/lg-00b-seam-contract.md`](.claude/worktrees/lg-00b-seam-contract.md).

### LG-00c · Sortable Players tab

A new `/players/` index page listing every Player (including the Free Agents pool), sortable by any of the 19 stats + `overall_rating` + `team` + `preferred_roles`. Server-side sort via `?sort=&dir=asc|desc` query params with HX-02 forgiving-fallback validation. Adds a 'Players' nav link in `base.html`. Visible immediately after LG-00 lands so the generated free-agent pool is browsable.
- completed
- note: sortable-players surface at `GET /players/` (URL name `player_list`, view `teams/views.py::player_list`, template `templates/teams/player_list.html`) — a read-only, server-side-sort, paginated index of every Player including the Free Agents pool (no special-case exclusion — the Free Agents team cell links to its own `/teams/<free_agents.id>/` detail page like any other team). One new URL `path("", views.player_list, name="player_list")` appended to `teams/player_urls.py` **after** the two existing entries (`benchmarks/` → `<int:player_id>/stats/` → `""`) so the trailing empty-path route cannot be shadowed by the `<int:player_id>` capture-group; the outer `path("players/", include("teams.player_urls"))` mount in `laserforce_simulator/urls.py` is reused unchanged. Reverse via the bare `reverse("player_list")` (no `app_name:` prefix — consistent with the existing HX-01 / HX-02 routes in that file). Three query params: `?sort=<key>` (default `"team"`, 23 accepted keys total), `?dir=<asc|desc>` (default `"asc"`, case-sensitive — `"ASC"` falls back to `"asc"`), `?page=<int>` (default 1, `EmptyPage` / `PageNotAnInteger` → page 1); no other params are read; the view never 404s on bad query params (`get_object_or_404` is not used) — every invalid input is coerced to a default by the two forgiving-fallback helpers `_coerce_sort(raw, default="team") -> str` and `_coerce_dir(raw, default="asc") -> str` defined **inline** at module scope in `teams/views.py` beside the existing HX-02 `_coerce_threshold` / `_coerce_display` (no new pure-aggregation module — LG-00c **deliberately has no `teams/*.py` aggregation seam**, contrasting with HX-01 / HX-02 / LG-00 / LG-00b which all do; the helpers and constants are stdlib-only and imported directly from `teams.views` by the test file). Four new module-level constants in `teams/views.py`: `_SORT_KEYS: dict[str, str]` (22 entries, URL key → ORM target — the 19 stat fields plus `name` / `team` / `overall_rating`; insertion order is declared order for clarity, tests treat membership not ordering), `_SORT_KEYS_DISPLAY: tuple[tuple[str, str], ...]` (23 entries — the 22 ORM keys PLUS the `preferred_roles` sentinel, in the locked display column order driving both the `<th>` headers and the per-row `<td>` cells in the template), `_VALID_DIRS = ("asc", "desc")`, and `_PAGE_SIZE = 50`. **Capital-O casing quirk** — URL key `offensive_synergy` (lowercase alias) maps to ORM target `Offensive_synergy` (capital-O, matching the existing `Player.Offensive_synergy` field byte-for-byte and the `_STAT_FIELDS` / `STAT_COLUMNS` precedent set by LG-00 / LG-00b); pinned by `test_sort_by_offensive_synergy_url_alias_maps_to_capital_O_field`. The 23rd accepted sort key, `"preferred_roles"`, is the **Python-side sentinel** — it is NOT in `_SORT_KEYS` (the field is a JSON list with no ORM target) and is handled in a separate branch of the view that materialises the queryset via `list(qs)` and sorts in memory with key `(",".join(p.preferred_roles or []), p.name)` and `reverse=(direction == "desc")` (empty `preferred_roles` joins to `""` which sorts to the TOP of asc; the secondary `player.name` component is the tiebreak — pinned by `test_sort_by_preferred_roles_python_branch`). The ORM branch annotates the queryset with `overall_rating_db = (sum of 19 F-fields) / 19.0` (using `F("Offensive_synergy")` with the capital-O to match the field) so `?sort=overall_rating` is a single SQL `ORDER BY` rather than a Python sort; the `.order_by()` call always appends `"name"` as a deterministic secondary tiebreak (even when sorting by name itself — harmless, keeps the ORM branch uniform). `Paginator(qs_or_rows, _PAGE_SIZE)` provides standard Django pagination; sort + dir are carried in page links via `querystring_without_page` (the view assembles two URL-encoded helper strings via `request.GET.copy()` + `.pop()`); clicking a column header drops `page=` (resets to page 1) via `querystring_without_sort_dir_page`. The view ships **exactly seven** frozen context keys: `page_obj, paginator, sort, dir, sort_keys, querystring_without_page, querystring_without_sort_dir_page`. New template `templates/teams/player_list.html` extends `base.html` and renders a `<div class="table-responsive">` wrapper around a single `<table id="player-list-table">` whose `<thead>` iterates `sort_keys` (the 23-entry `_SORT_KEYS_DISPLAY`) to emit one `<th id="player-list-th-{url_key}">` per column with an `<a href>` that flips direction on the active column (`asc` ↔ `desc`) and starts new columns at `asc`; the active-column arrow glyphs are the locked Unicode `↑` (U+2191, asc) and `↓` (U+2193, desc), appended to the human label with a single space (`{{ label }} ↑` / `{{ label }} ↓`) and pinned by `test_active_column_renders_arrow_glyph`. The `<tbody>` renders one row per Player on the page: the `name` cell is a `<a href="{% url 'player_career_stats' player.id %}">` linking to the HX-01 career page (pinned by `test_name_cell_links_to_career_stats`), the `team` cell is a `<a href="{% url 'team_detail' player.team.id %}">` (pinned by `test_team_cell_links_to_team_detail` — Free Agents Team players link to their own team detail page; no special-case), the `preferred_roles` cell is `{{ player.preferred_roles|join:", " }}` (empty list renders an empty cell), the `overall_rating` cell uses the `@property` (NOT the annotation) via `{{ player.overall_rating|floatformat:1 }}`, and the 19 stat cells render plain integers including the capital-O `{{ player.Offensive_synergy }}` attribute. The pagination block is a `<nav id="player-list-pagination">` rendered only when `page_obj.has_other_pages` is true, with Previous / Page X of Y / Next links whose hrefs use `querystring_without_page` so sort + dir are preserved across page navigation (pinned by `test_pagination_carries_sort_and_dir_in_links`). One new line added to `templates/base.html`: a `<a class="nav-link" id="player-list-nav-link" href="{% url 'player_list' %}">Players</a>` anchor placed in the existing `<div class="navbar-nav ms-auto">` block immediately AFTER the existing `Teams` `<a>` and BEFORE the existing `Matches` `<a>` (no CSS class change, no reordering of other nav links — exactly one new `<a>` line added, pinned by `test_nav_link_present_in_base_html`). Default sort + tiebreak — `?sort=team&dir=asc` is the default; secondary tiebreak is always `name asc` (appended to every ORM `.order_by()` and used in the Python-sort tuple); the test creating two Teams `("Alpha", "Bravo")` × two Players `("Zed", "Aaron")` pins the rendered row order `Alpha/Aaron, Alpha/Zed, Bravo/Aaron, Bravo/Zed`. Forgiving-fallback for invalid params — `?sort=bogus` → context `sort == "team"`, `?dir=BOGUS` → context `dir == "asc"` (case-sensitive — `"ASC"` falls back; mirrors HX-02's `_coerce_display` casing discipline). **Determinism / scope** — read-only view, no RNG, no simulation, no `_flush_to_db` touch, **no SIM-07 / SIM-08 contract interaction, no Score Calibration re-baseline obligation**; **no model field change, no migration, no ADR** (decisions are reversible — read-only view + inline helpers + new template + one-line nav link addition); **no new pure-aggregation module** (the helpers live inline in `teams/views.py`, contrasting with HX-01 / HX-02 / LG-00 / LG-00b which all ship a pure module); **no new dependency** (Django stdlib `Paginator` + `F` are already imported / available); **no JS** (sort headers are plain anchor tags with query-string flips); the view is fully deterministic given (Player, Team) DB state and the query string — same DB + same URL → identical rendered HTML. CONTEXT.md change is minimal: the existing **Free Agents Team** glossary entry's trailing `(deferred)` qualifier inside the `(LG-00c, deferred)` parenthetical is dropped — that is the ONLY CONTEXT.md change LG-00c is allowed to make; **no new domain term**. **Out of scope (deliberate):** no filter UI (no role filter, no team filter, no search box); no client-side / JS sort, no DataTables, no AJAX; no CSV export from this page; no per-player Edit / Delete buttons in rows (the existing `/teams/<team_id>/player/<player_id>/edit/` flow is unchanged); no bulk-actions checkbox column; ~~no alternative page sizes (`?per_page=` is not read);~~ **superseded — `?per_page=` ADDED in-PR** (default `10`, whitelist `(10, 25, 50, 100)`; see "Follow-ups" paragraph below); no `is_simulated` toggle / filter; no API / JSON endpoint at `/players/` (the existing read-only DRF `/api/players/` already covers programmatic access; this is purely the HTML index); no CSV import / LG-00b coupling; no batch-sim / Celery touch. Tests live in **one NEW file** `teams/tests/test_player_list_view.py` with two classes and 27 tests total: `TestCoerceSortAndDir` (10 helper unit tests — `test_coerce_sort_accepts_every_orm_key`, `test_coerce_sort_accepts_preferred_roles_sentinel`, `test_coerce_sort_falls_back_on_unknown_value`, `test_coerce_sort_falls_back_on_none`, `test_coerce_sort_falls_back_on_empty_string`, `test_coerce_dir_accepts_asc`, `test_coerce_dir_accepts_desc`, `test_coerce_dir_falls_back_on_unknown`, `test_coerce_dir_falls_back_on_none`, `test_coerce_dir_falls_back_on_uppercase` — these tests import `_coerce_sort` / `_coerce_dir` / `_SORT_KEYS` / `_VALID_DIRS` directly from `teams.views` and the file runs under Django's `TestCase` runner because of the `teams.views` import) and `TestPlayerListView` (17 Django `TestCase` view tests — `test_get_returns_200_with_default_sort`, `test_default_sort_is_team_asc_with_name_secondary`, `test_sort_by_name_asc`, `test_sort_by_overall_rating_desc`, `test_sort_by_offensive_synergy_url_alias_maps_to_capital_O_field`, `test_sort_by_preferred_roles_python_branch`, `test_sort_by_every_stat_key_returns_200`, `test_invalid_sort_falls_back_to_team`, `test_invalid_dir_falls_back_to_asc`, `test_pagination_renders_50_per_page`, `test_pagination_carries_sort_and_dir_in_links`, `test_sort_change_resets_to_page_1`, `test_free_agents_players_appear_in_listing`, `test_name_cell_links_to_career_stats`, `test_team_cell_links_to_team_detail`, `test_active_column_renders_arrow_glyph`, `test_nav_link_present_in_base_html`). Locked names — URL `path("", views.player_list, name="player_list")` (full URL `/players/`); view `teams.views.player_list(request)`; helpers `teams.views._coerce_sort(raw, default="team") -> str` + `teams.views._coerce_dir(raw, default="asc") -> str`; constants `teams.views._SORT_KEYS: dict[str, str]` (22 ORM-backed entries) + `teams.views._SORT_KEYS_DISPLAY: tuple[tuple[str, str], ...]` (23 column-display entries) + `teams.views._VALID_DIRS = ("asc", "desc")` + `teams.views._PAGE_SIZE = 50`; template `templates/teams/player_list.html`; nav-link line in `templates/base.html`; DOM ids `player-list-table` / `player-list-th-{url_key}` (e.g. `player-list-th-offensive_synergy`, `player-list-th-preferred_roles`) / `player-list-pagination` / `player-list-nav-link`; arrow glyphs `↑` (U+2191) + `↓` (U+2193); context keys (7) `page_obj, paginator, sort, dir, sort_keys, querystring_without_page, querystring_without_sort_dir_page`; test file `teams/tests/test_player_list_view.py` with classes `TestCoerceSortAndDir` + `TestPlayerListView`; CONTEXT.md change drops the trailing `(deferred)` on the existing **Free Agents Team** entry. **Follow-ups landed in the same PR (extensions to the seam contract):** (1) **Per-page selector** — `?per_page=10|25|50|100` query param (default `10`, whitelist `_VALID_PAGE_SIZES = (10, 25, 50, 100)`, helper `_coerce_per_page(raw, default=_DEFAULT_PAGE_SIZE) -> int` mirroring HX-02's truthy-int coerce discipline; non-int / out-of-whitelist / missing → default); rendered as a `<select id="player-list-per-page-select">` inside a `<form id="player-list-per-page-form" method="get">` above the table with hidden inputs carrying current `sort` + `dir` (so changing page size does NOT reset the user's column ordering) and `onchange="this.form.submit()"` for auto-submit + a `<noscript>` Apply button fallback; `per_page` survives across page navigation (in `querystring_without_page`) AND across column-header re-sorts (in `querystring_without_sort_dir_page`); new context keys `per_page` + `page_size_options` bring the total to 9 (was 7); the previous `_PAGE_SIZE = 50` constant is removed. (2) **LG00c-7 inline fix** — pagination Previous/Next hrefs were carrying uncoerced invalid `?sort=&dir=` values verbatim because `querystring_without_page` was built from raw `request.GET.copy()`; fixed by assigning the COERCED values back into the QueryDict (`qs_no_page["sort"] = sort` etc.) before `.urlencode()`; regression test `test_pagination_links_drop_invalid_sort_and_dir`. (3) **LG00c-8 inline fix (responsive layout)** — at viewports ≥ 1320px the 23-column table was clamped to Bootstrap's `.container.mt-4` max-width (1320px xxl) PLUS a redundant inner `<div class="container mt-4">` in the template, wasting up to ~600px of viewport on a 1920px screen and forcing a needless horizontal scroll; fixed by dropping the redundant inner `.container` (the outer one from `base.html` is preserved for the h1/paragraph/pagination) and applying `style="margin-left: calc(-50vw + 50%); margin-right: calc(-50vw + 50%); padding: 0 1rem;"` to the `.table-responsive` so it breaks out of the outer container at wide viewports (the calc no-ops at small viewports, resolving to ~0 when container width ≈ viewport width); verified at 720 / 800 / 1280 / 1920 / 2560px — zero wasted space at every size, table fits 2560px without scroll, scrolls horizontally only when actually needed at narrower widths. (4) **LG00c-9 inline fix (template-comment leak)** — a multi-line `{# ... #}` block was rendering literally as visible text under the player count (Django `{# #}` is single-line only — multi-line needs `{% comment %}...{% endcomment %}`); collapsed to a one-line `{# #}` comment. Net tests: 27 → 37 (+10 — 5 new pure-unit cases on `_coerce_per_page`, 4 new view tests on `?per_page=` semantics, 1 new view test for the LG00c-7 regression); 2 existing view tests updated to pass `?per_page=50` explicitly (preserves their original 51-player fixture intent now that the default is 10, not 50); 1 existing test renamed (`test_default_pagination_is_10_per_page` was `test_pagination_renders_50_per_page`). New module-level constants in `teams/views.py`: `_VALID_PAGE_SIZES: tuple[int, ...] = (10, 25, 50, 100)` and `_DEFAULT_PAGE_SIZE: int = 10`; new helper `_coerce_per_page`; new DOM ids `player-list-per-page-form` + `player-list-per-page-select`; new locked option-value-and-selected substrings in tests. Seam contract: [`.claude/worktrees/lg-00c-seam-contract.md`](.claude/worktrees/lg-00c-seam-contract.md).

### LG-01 · League / Season foundation

Reframed during the LG-01 grilling session (2026-05-26) — the original
PLAN.md scope ("new `Season` model + standings page") is the *tip* of a
much larger feature. LG-01 is the foundation for **single-player league
mode**: a user creates a League, plays many Seasons within it
indefinitely, with the per-Match colour swap split across the Season
calendar so rosters can shift between the two Rounds of one Match.
LG-01 ships only the **model + algorithm** layer; the user-facing surfaces
(landing, create flow, dashboard, Play Next, history, team game log) are
split into LG-01a..g and grilled separately when each is picked up.

**Three-layer model** ([ADR-0014](docs/adr/0014-league-season-foundation.md)):

- `League(name, mode, state, created_at)` — persistent container; `mode` ∈
  {`sandbox`, `league`, `multiplayer`}; `state` ∈ {`active`, `archived`};
  no User/Team owner FK yet (deferred to UX-01 + CAR-01).
- `Season(league_fk, name, start_date, teams_m2m, state, schedule_format,
  starting_team_ids_json, champion_team_fk, created_at)` — one cycle in a
  League; `state` ∈ {`draft`, `active`, `completed`}; `schedule_format`
  enum (v1 only ships `single_round_robin`, extensible);
  `starting_team_ids_json` snapshots the M2M at `draft → active` so the
  pure schedule algorithm is deterministic against a frozen team list.
- `Match.season` `ForeignKey(Season, null=True, blank=True,
  on_delete=models.SET_NULL)` — sandbox Matches stay `season=NULL`
  ([ADR-0004](docs/adr/0004-simulation-data-is-disposable.md) no-backfill
  precedent).

**Round-keyed scheduling** ([ADR-0015](docs/adr/0015-schedule-on-demand-no-fixture-rows.md)):
the two **Rounds** of one **Match** are scheduled separately in time
(round 1 in first half of Season, round 2 in second half — strict
mirror, no interleaving). Schedule is **computed on demand** from a pure
module `matches/schedule_generator.py::generate_schedule(team_ids,
schedule_format) -> list[ScheduleFixture]` (frozen dataclass:
`matchday`, `round_number`, `team_a_id`, `team_b_id`). **No
`ScheduleEntry` model, no pre-created `Match` rows** — Match rows are
find-or-created Side-agnostically by `(season_id, frozenset({
team_red_id, team_blue_id}))` at the moment a Round is simulated.

**Partial-completable Match**: `is_completed=True` is set only after the
*second* Round persists; round 1 alone leaves the Match with
`*_round1_*` populated and `is_completed=False`. **No new `Match`
columns** — the existing fields and `is_completed` semantics carry the
state. `Match.calculate_winner` (already gated on `is_completed=True`
via `save()`) runs unchanged.

**New simulator entry point** (the sole writer for Season Matches):
`BatchSimulator.simulate_scheduled_round(season, team_a, team_b,
round_number, *, arena_map=None) -> GameRound` (`@transaction.atomic`).
Round 1 call find-or-creates the Match and persists `GameRound(
round_number=1)`; round 2 call finds the Match Side-agnostically and
runs simulation with args reversed (preserving the per-Match colour
swap from `simulate_match` verbatim), persists `GameRound(round_number=
2)`, sets `match.is_completed=True`, saves (triggers
`calculate_winner`). The existing `simulate_match` (both Rounds atomic)
is **kept** as the sandbox-Match entry point — sandbox Matches with
`season=NULL` use it; Season Matches must use the new method.

**Standings — Match-keyed, 3W/1T/0L**: each completed Match contributes
one outcome per team (W = `Match.winner_id == team.id`, T = `NULL`, L =
otherwise). Tiebreak ladder: (1) **round wins** (sum of `Match
.red_rounds_won` / `blue_rounds_won` over the team's Season Matches);
(2) **total score** (the team's side of `Match.red_total_points` /
`blue_total_points`, includes the team-elim bonus); (3) **alphabetical
by Team name**. Aggregation lives in a pure module
`matches/standings.py::compute_standings(...) -> list[StandingsRow]`,
mirroring the HX-03 / HX-04 pure-module precedent (no Django imports,
`TestNoDjangoImportsLeaked` subprocess check).

**State machine**:
- `draft → active` — explicit "Start Season" action; validates ≥ 2
  enrolled Teams; snapshots M2M into `starting_team_ids_json`; locks
  M2M.
- `active → completed` — **auto-transition** the moment the last
  unplayed fixture's `GameRound` persists; computes Standings;
  stamps `champion_team_fk` to the top row.
- `completed → ???` — no further transitions; read-only.
- **M2M frozen at `active`** — to change roster, finish/abandon and
  start the next Season; the LG-01e "Start Next Season" chain
  inherits the previous Season's M2M.

**Surfaces in LG-01 itself** (foundation only):
- `/seasons/<int:season_id>/standings/` — read-only Standings page,
  template `templates/seasons/standings.html` (Django admin
  registration for `League` + `Season` ships alongside so a Season can
  be created manually for testing without LG-01b).
- `/seasons/<int:season_id>/schedule/` — read-only fixture list
  rendered from the pure module + overlay of played `GameRound`s.

**Apps**:
- `League` + `Season` models live in **`matches/`** (they own
  `Match.season` FK and the simulator surface).
- Pure modules live alongside the existing
  `matches/h2h_stats.py` / `matches/player_h2h_stats.py` precedent.

**Out of scope for LG-01** (deferred to sub-tasks, each grilled
separately when picked up):
- LG-01a — `/` mode picker landing + `/leagues/` list
- LG-01b — `/leagues/create/` create-League flow (LG-00 integration,
  default `single_round_robin`, etc.)
- LG-01c — `/leagues/<id>/` and `/seasons/<id>/` dashboards
- LG-01d — `POST /seasons/<id>/play-next/` (Play Next Round) +
  Play Week / Play To End variants
- LG-01e — "Start Next Season" chain + League archive button
- LG-01f — `/leagues/<id>/history/` cross-Season history
- LG-01g — `/seasons/<id>/teams/<tid>/games/` per-team game log

**Out of scope entirely** (separate top-level PLAN tasks): tournament
formats / playoffs (LG-02), awards (LG-03), season-end stat updates
(LG-04), player potential (LG-05), manager identity + team selection +
firing (CAR-01..03), finances / contracts / free agents / trades /
prospects (no PLAN entry yet — propose Phase 5.8 "Player economy" if
later wanted), power rankings / stat leaders / watch list / hall of
fame (separate analytics tasks).

Two ADRs land alongside the foundation:
[ADR-0014 League/Season foundation](docs/adr/0014-league-season-foundation.md)
and [ADR-0015 Schedule on-demand](docs/adr/0015-schedule-on-demand-no-fixture-rows.md).
Domain terms **League**, **Season**, **Standings** are added to
CONTEXT.md under a new `### League and seasons` subsection.
- completed
- note: foundation layer for single-player **League mode** — ships **two new models** (`League`, `Season`), **one new `Match.season` FK**, **two new pure modules** (`matches/schedule_generator.py`, `matches/standings.py`), **one new simulator entry point** (`BatchSimulator.simulate_scheduled_round`), **two new read-only views** (`/seasons/<id>/standings/`, `/seasons/<id>/schedule/`), and **admin registrations** for `League` + `Season`; behind admin + two GET-only pages — the user-facing surfaces (mode picker, create flow, dashboard, Play Next, history, team game log) are deferred to **LG-01a..g** and each gets its own task / grilling session. **Models** live in `matches/models.py`: `League(name, mode, state, created_at)` with `mode ∈ {sandbox, league, multiplayer}` (default `"league"`) and `state ∈ {active, archived}` (default `"active"`) plus an `active_season` `@property` returning the single non-`completed` Season per League (`seasons.exclude(state="completed").order_by("-id").first()`); `Season(league, name, start_date, teams, state, schedule_format, starting_team_ids_json, champion_team, created_at)` with `league FK(League, on_delete=CASCADE, related_name="seasons")`, **required `start_date: DateField()`** (no default, no `null=True`), `teams M2M(Team, related_name="enrolled_seasons")`, `state ∈ {draft, active, completed}` (default `"draft"`), `schedule_format ∈ {single_round_robin}` (default `"single_round_robin"`, max_length=32 leaves headroom for future formats), `starting_team_ids_json JSONField(null=True, blank=True, default=None)` snapshotted at activation, `champion_team FK(Team, on_delete=SET_NULL, related_name="seasons_won")` (NULL until auto-completion stamps it; SET_NULL so deleting a Team does NOT delete its Seasons-won history), and `__str__ = f"{self.league.name} — {self.name}"` (em-dash U+2014, pinned by `TestSeasonModel.test_str_returns_league_name_em_dash_season_name`). `Match.season` adds a `FK(Season, null=True, blank=True, on_delete=SET_NULL, related_name="matches")` — sandbox Matches stay `season=NULL` forever, no backfill (mirrors [ADR-0004](docs/adr/0004-simulation-data-is-disposable.md) precedent), and deleting a Season SET_NULLs its Matches rather than cascading them out of history. **Three Season methods**: `clean() -> None` enforces the **Active-Season invariant** (≤ 1 non-`completed` Season per League, `Season.objects.filter(league=self.league).exclude(state="completed").exclude(pk=self.pk).exists()` raises `django.core.exceptions.ValidationError`); `start_season() -> None` (`@transaction.atomic`) flips `draft → active`, raises `ValidationError` on `self.teams.count() < 2`, and snapshots `starting_team_ids_json = sorted([t.id for t in self.teams.all()])` (ascending — defence-in-depth alongside the pure module's own input sort); `complete_if_finished() -> None` (`@transaction.atomic`, **idempotent**) is a no-op on non-`active` Seasons, otherwise calls `generate_schedule(self.starting_team_ids_json, self.schedule_format)`, compares each fixture against persisted `GameRound`s via the **Side-agnostic frozenset key** (`frozenset({game_round.team_red_id, game_round.team_blue_id}) == frozenset({fixture.team_a_id, fixture.team_b_id})` AND matching `round_number`), and when every fixture has a played Round flips `state="completed"` + computes Standings via `compute_standings(completed_matches, enrolled_teams)` + stamps `champion_team = Team.objects.get(pk=rows[0].team_id)` (the rank-1 row). **State machine** is `draft → active` (explicit `start_season()` action — locks the M2M; to change roster, finish/abandon and start the next Season) and `active → completed` (auto-transition the moment the last fixture's `GameRound` persists; idempotent re-calls are safe). **Pure module `matches/schedule_generator.py`** ships `SCHEDULE_FORMATS: tuple[str, ...] = ("single_round_robin",)` (view-side validation surface — adding a format is appending an entry + a branch in `generate_schedule`), `ScheduleFixture(matchday: int, round_number: int, team_a_id: int, team_b_id: int)` (frozen dataclass — pinned field order), and `generate_schedule(team_ids: list[int], schedule_format: str = "single_round_robin") -> list[ScheduleFixture]` — runs the **circle method** with the fixed-team-at-index-0 rotation, normalises each fixture so `team_a_id = min(pair)` and `team_b_id = max(pair)` before output sort, mirrors round-1 matchdays `1..N-1` into round-2 matchdays `N..2*(N-1)` (strict mirror, no interleaving — see [ADR-0015](docs/adr/0015-schedule-on-demand-no-fixture-rows.md)), drops fixtures involving the **bye sentinel `-1`** (odd N appends `-1` to the rotating slots; the sentinel value is internal — pinned by `test_bye_sentinel_minus_one_never_appears_in_output`), and returns the list sorted by `(matchday, team_a_id)`. Output is a function of the *set*, not of input order (`generate_schedule([5,1,3,7]) == generate_schedule([1,3,5,7])` — input-sort defence-in-depth). **Pinned consequences**: N=4 ⇒ 12 fixtures total (6 per round, 3 matchdays per round × 2 fixtures per matchday); N=8 ⇒ 56 fixtures (7 matchdays × 4 fixtures × 2 rounds); N=5 (odd) ⇒ 5+5 matchdays × 2 played fixtures each = 20 total. **Pure module `matches/standings.py`** ships `StandingsRow(team_id, matches_played, wins, losses, ties, league_points, round_wins, total_score, rank)` (frozen dataclass — **9 fields, pinned order**) and `compute_standings(completed_matches: list[dict], enrolled_teams: list[tuple[int, str]]) -> list[StandingsRow]`. The **input dict shape — 8 frozen keys**: `match_id`, `team_red_id`, `team_blue_id`, `winner_team_id` (`None` ⇒ tie), `red_rounds_won`, `blue_rounds_won`, `red_total_points` (already includes team-elim bonus via the existing `Match.red_total_points` `@property`), `blue_total_points`. **Aggregation rules**: per match, both teams `matches_played += 1`; W/L attribution from `winner_team_id` with the defensive HX-03 precedent (`winner_team_id` neither team's id counts as a tie — corrupt-data defence, mirrors `compute_match_record`); `league_points = 3 * wins + 1 * ties + 0 * losses`; `round_wins` accumulates `red_rounds_won` / `blue_rounds_won` per side; `total_score` accumulates `red_total_points` / `blue_total_points` per side; teams in `enrolled_teams` with no matches get a fully-zeroed row. **Sort ladder (in order)**: `league_points` desc, `round_wins` desc, `total_score` desc, `team_name` asc (the **alphabetical tiebreak lives INSIDE the pure module** — `enrolled_teams: list[tuple[int, str]]` carries `(team_id, team_name)` so the full sort is a function of `(completed_matches, enrolled_teams)` and is unit-testable with zero DB; decision (a) over view-side tiebreaking, locked in §0 of the seam contract). `rank` is populated 1-based and dense in iteration order (the alphabetical tiebreak is the final disambiguator — equal-ranked rows still get distinct ranks). Both pure modules carry a **frozen import allowlist** (`dataclasses`, `typing`, optionally `collections` — **NO** Django, NO `random`, NO `datetime`, NO I/O, NO logging) defended by a `TestNoDjangoImportsLeaked` subprocess fresh-import + `sys.modules` walk that mirrors the HX-01 / HX-02 / HX-03 / HX-04 / RES-04 / RV-03 / LG-00 / LG-00b precedent. **Simulator entry point** is the sole writer for Season Matches: `BatchSimulator.simulate_scheduled_round(self, season, team_a, team_b, round_number, *, arena_map=None) -> GameRound` (`@transaction.atomic`) in `matches/simulation.py`. Guard sequence (in order): `ValueError` when `season.state != "active"` (substring `"active"` in the message), `ValueError` when `round_number not in (1, 2)`, `ValueError` when `round_number == 2` and no existing Match (substring `"round 1"` in the message). **Side-agnostic Match lookup** is inlined (no helper method) via two ORM queries — `(season=…, team_red=team_a, team_blue=team_b).first()` ELSE `(season=…, team_red=team_b, team_blue=team_a).first()` — so a round-1 call with `(team_a, team_b)` and a round-2 call with `(team_b, team_a)` resolve to the same Match row. Round 1: find-or-create the Match with `Match.objects.create(season=season, team_red=team_a, team_blue=team_b, is_completed=False)`, delegate to the existing per-Round simulation entry point used by `simulate_match` (byte-for-byte same `arena_map` resolution, same seed-handling, same `_flush_to_db` parameters — **no new RNG draws**, no behavioural change), persist `GameRound(round_number=1, …)`, write `match.red_round1_*` / `blue_round1_*` / `round1_eliminated_at`, leave `is_completed=False`, `match.save()`. Round 2: same lookup, raise on missing, delegate to the per-Round entry point with **args reversed** (`team_red=team_b, team_blue=team_a` — mirrors the existing per-Match colour swap in `simulate_match` byte-for-byte; the `GameRound` for round 2 has team_b as physical red), persist `GameRound(round_number=2, …)`, write `match.red_round2_*` / `blue_round2_*` / `round2_eliminated_at`, **set `match.is_completed=True`** + `match.save()` (the existing `save()` override triggers `calculate_winner` — populates `match.winner`). After persistence on either round, the method calls `season.complete_if_finished()` (idempotent, no-op except on the final fixture — auto-transitions `active → completed` + stamps `champion_team`). The existing `simulate_match` (both-Rounds atomic, sandbox-Match entry point) is **kept verbatim** — sandbox Matches with `season=NULL` use it; Season Matches must use the new method. **No new model fields beyond the three listed**, **no new RNG draws**, **no `_flush_to_db` change beyond shared per-Round refactor inside `simulation.py`**, **no simulation mechanics change** → **no SIM-07 / SIM-08 contract interaction, no Score Calibration re-baseline**. **Views** in `matches/views.py`: `season_standings(request, season_id)` resolves `get_object_or_404(Season, pk=season_id)` and branches on `season.state` — `"draft"` ⇒ **draft preview** mode: list `season.teams.all()` sorted by `(-team_overall, name)` where `team_overall = mean(p.overall_rating for p in team.active_players) if team.active_players else 0.0` using the **existing `Team.active_players` `@property`** (the 6 starting-lineup players via `slot_*` FKs) and **existing `Player.overall_rating` `@property`**; **explicitly NO `Player.is_bench` field added** (bench is derived from "not in any `slot_*` FK" via the existing `Team.bench_players` `@property` — pinned scope-out, locked in §4a of the seam contract); rows are zero-filled dict shapes with the 9 `StandingsRow` keys and `rank = i+1`; `is_draft_preview = True` flips the **"Preview — Season not started"** banner. `"active"` / `"completed"` ⇒ live mode: `qs = Match.objects.filter(season=season, is_completed=True)`, materialise the 8-key dicts (`red_rounds_won` / `blue_rounds_won` read from the existing `Match` fields/methods; `red_total_points` / `blue_total_points` read via the existing `@property` — no parentheses), determine `team_ids` from `season.starting_team_ids_json` (defensive fallback to `sorted([t.id for t in season.teams.all()])` when NULL), build `enrolled_teams = list(Team.objects.filter(id__in=team_ids).values_list("id", "name"))`, call `compute_standings(...)`, `is_draft_preview = False`. The view assembles `rows_with_teams = [(row, teams_by_id[team_id]) for row in rows]` so the template iterates `(row, team)` tuples directly (no custom template-tag needed); context keys (frozen): `season`, `rows`, `rows_with_teams`, `is_draft_preview`. `season_schedule(request, season_id)` resolves the Season, determines `team_ids` (draft ⇒ `sorted([t.id for t in season.teams.all()])`, else `season.starting_team_ids_json`), calls `generate_schedule(team_ids, season.schedule_format)` (skipped with `fixtures=[]` when `len(team_ids) < 2` — the page still renders 200 with an empty-state notice), indexes persisted `GameRound`s by `(frozenset({match.team_red_id, match.team_blue_id}), round_number) → game_round` for **Side-agnostic played-overlay lookup**, builds per-fixture dicts (`matchday`, `round_number`, `team_a_id`, `team_b_id`, `team_a`, `team_b`, `played`, `game_round_id`, `red_score`, `blue_score`, `date = season.start_date + (matchday - 1) * 7 days`), groups by matchday into `matchdays = list[{"matchday": int, "date": date, "fixtures": list[per-fixture]}]`. Context keys (frozen): `season`, `matchdays`. **URLs** ship a NEW file `matches/season_urls.py` (no `app_name` — bare URL namespace, mirrors `teams/player_urls.py`) with two `path` entries reverse-named `season_standings` and `season_schedule`; mounted in `laserforce_simulator/urls.py` as `path("seasons/", include("matches.season_urls"))` immediately after the existing `path("matches/", include("matches.urls"))` line. Resulting URLs: `GET /seasons/<int:season_id>/standings/` and `GET /seasons/<int:season_id>/schedule/` — both **GET-only** (no POST routes in LG-01 — Play Next is LG-01d). **Templates** under `templates/seasons/`: `standings.html` extends `base.html` with **locked DOM ids** `season-standings-table` (outer `<table>`), `season-standings-empty` (rendered when `is_draft_preview AND len(rows) == 0`), `season-draft-preview-banner` (rendered when `is_draft_preview` truthy), `season-state-badge` (renders `season.state`); frozen header row order **left to right** `Rank | Team | MP | W | L | T | Pts | RW | TS` (MP=matches_played, Pts=league_points, RW=round_wins, TS=total_score); team cells link to `{% url 'team_detail' team.id %}`. `schedule.html` extends `base.html` with locked DOM ids `season-schedule-table` (outer table/container), `season-schedule-empty` (when `len(matchdays) == 0`), and per-matchday `season-schedule-matchday-{n}` (where `{n}` is the 1-based matchday number); each matchday section shows `Matchday {n} — {date|date:"Y-m-d"}` and a sub-table of fixtures (`team_a.name` vs `team_b.name`, `round_number`, either the played score `red_score`–`blue_score` with optional `GameRound` detail link or literal `Unplayed`). **Admin** registrations in `matches/admin.py` (inserted AFTER existing registrations — the Code agent does NOT modify any existing registration): `@admin.register(League) class LeagueAdmin(admin.ModelAdmin)` with `list_display = ("name", "mode", "state", "created_at")`; `@admin.register(Season) class SeasonAdmin(admin.ModelAdmin)` with `list_display = ("name", "league", "state", "schedule_format", "start_date")` and **`filter_horizontal = ("teams",)`** (the M2M dual-select widget). **Migration** is a single file `matches/migrations/0029_league_season_match_fk.py` depending on `("matches", "0028_gameround_is_simulated")` + the latest `teams` migration at branch-cut time, with operations in pinned order `CreateModel(League)` → `CreateModel(Season)` → `AddField(Match, season)`; **no `RunPython`, no `RunSQL`, no backfill** ([ADR-0004](docs/adr/0004-simulation-data-is-disposable.md) precedent). **Imports added** to `matches/models.py`: `from django.core.exceptions import ValidationError` + `from django.db import transaction`. Imports added to `matches/views.py`: `from .schedule_generator import generate_schedule` + `from .standings import compute_standings` + `from teams.models import Team` + `from datetime import timedelta` (alongside the existing `get_object_or_404`). **Tests live in four NEW files** under `matches/tests/`: `test_schedule_generator.py` (pure-unit `SimpleTestCase` — `TestGenerateScheduleHappyPath` × 4 covering N=4/N=8 fixture counts + every-pair-once per round, `TestGenerateScheduleOrder` × 4 covering matchday spans + output sort + `team_a_id < team_b_id` per fixture, `TestGenerateScheduleOddN` × 4 covering bye-drop / no-twice-per-matchday / N=5 total 20 played / `-1` never appears, `TestGenerateScheduleDeterminism` × 2, `TestGenerateScheduleErrors` × 3, `TestScheduleFormatsConstant` × 1, `TestNoDjangoImportsLeaked` × 1 subprocess), `test_standings.py` (pure-unit — `TestComputeStandingsEmptyInput` × 2, `TestComputeStandingsBasicWinLoss` × 3, `TestComputeStandingsTie` × 2 incl. defensive unknown-winner-id, `TestComputeStandingsTiebreakLadder` × 3 (round_wins → total_score → alphabetical), `TestComputeStandingsRankPopulated` × 1, `TestComputeStandingsTeamElimBonusFlowsIn` × 1, `TestNoDjangoImportsLeaked` × 1), `test_lg01_models.py` (Django `TestCase` — `TestLeagueModel` × 7 incl. `active_season` property edge cases, `TestSeasonModel` × 4 incl. the em-dash `__str__`, `TestSeasonCleanInvariant` × 4 (second non-completed in same/different League, OK when first completed, self-exclusion), `TestSeasonStartSeason` × 4 (flip / sort-snapshot / `< 2` raise / M2M unchanged), `TestSeasonCompleteIfFinished` × 5 (no-op on non-active / no-op on incomplete fixtures / flip on all-played / champion stamp / idempotent re-call), `TestMatchSeasonFK` × 4 (default NULL / assignable / Season-delete SET_NULLs not cascades / reverse accessor)), and `test_lg01_simulator.py` (Django `TestCase` — `TestSimulateScheduledRoundGuards` × 5, `TestSimulateScheduledRoundRound1` × 5, `TestSimulateScheduledRoundRound2` × 6 incl. args-reversed `team_red == team_b` in round-2 GameRound + `is_completed=True` flip + `calculate_winner` trigger, `TestSimulateScheduledRoundSideAgnosticLookup` × 2, `TestSimulateScheduledRoundAutoCompletion` × 3 — simulator tests use small-N seeded simulations (N=2 / N=3) to keep runtime down and assert on schema-level outcomes, NOT exact score totals). **Determinism / scope.** Both views are pure read-derivations (no writes, no RNG, no simulation kicked off); `simulate_scheduled_round` is pure orchestration over the existing per-Round simulator — **per-Round RNG consumption is byte-for-byte identical to `simulate_match` at round-1 and round-2 time separately**, the per-Match colour swap is verbatim, no new RNG draws are introduced, and the SIM-07 / SIM-08 contract is untouched. **No Score Calibration re-baseline.** The **Active-Season invariant** is the only data-integrity rule enforced at the model layer (via `Season.clean()`); schedule determinism is enforced by the `starting_team_ids_json` snapshot at activation plus the pure module's input sort (defence-in-depth). Pure modules carry zero state — every call is a pure function of its inputs. **CONTEXT.md is NOT edited** — the `League` / `Season` / `Standings` glossary entries under `### League and seasons` were added at grilling time. **No ADR write** — [ADR-0014](docs/adr/0014-league-season-foundation.md) (model + state machine) and [ADR-0015](docs/adr/0015-schedule-on-demand-no-fixture-rows.md) (algorithm surface, no `ScheduleEntry` table) were both written at grilling time. **Out of scope (deferred to LG-01a..g)**: `/` mode picker landing + `/leagues/` list (LG-01a), `/leagues/create/` create-League flow (LG-01b), `/leagues/<id>/` + `/seasons/<id>/` dashboards (LG-01c), `POST /seasons/<id>/play-next/` + Play Week / Play To End (LG-01d), "Start Next Season" chain + League archive button (LG-01e), `/leagues/<id>/history/` cross-Season history (LG-01f), `/seasons/<id>/teams/<tid>/games/` per-team game log (LG-01g). **Out of scope entirely** (separate top-level PLAN tasks): tournament formats / playoffs (LG-02), awards (LG-03), season-end stat updates (LG-04), player potential (LG-05), manager identity (CAR-01..03), finances / contracts / free agents / trades / prospects, power rankings / stat leaders / watch list / hall of fame. **Out of scope (deliberate within LG-01)**: no `simulate_match` behavioural change (sandbox stays byte-for-byte identical); no sandbox URL / view change; no `Player.is_bench` field (bench derived via slot FKs); no `Season.matchday_cadence_days` (deferred); no `League.owner_user` (deferred to UX-01); no `Match.state` enum (rejected in ADR-0014); no API / DRF endpoint for `League` / `Season` (HTML views only); no batch-sim / Celery touch; no backfill (pre-LG-01 Matches stay `season=NULL` forever); no "Start Next Season" wiring (LG-01e); no `Player` / `Team` app touch beyond the auto-generated M2M reverse accessor `Team.enrolled_seasons` and FK reverse accessor `Team.seasons_won`. **Locked names** — model classes `matches.models.League` + `matches.models.Season` + the `Match.season` FK addition; `League` choices `("sandbox","Sandbox"), ("league","League"), ("multiplayer","Multiplayer")` + `("active","Active"), ("archived","Archived")` (defaults `"league"` / `"active"`); `Season` choices `("draft","Draft"), ("active","Active"), ("completed","Completed")` + `("single_round_robin","Single round-robin")` (defaults `"draft"` / `"single_round_robin"`); related names `League.seasons` / `Season.teams.enrolled_seasons` / `Season.champion_team.seasons_won` / `Match.season.matches`; methods `League.active_season` (`@property`) + `Season.clean()` + `Season.start_season()` (`@transaction.atomic`) + `Season.complete_if_finished()` (`@transaction.atomic`); pure modules `matches/schedule_generator.py` + `matches/standings.py`; dataclasses `ScheduleFixture(matchday, round_number, team_a_id, team_b_id)` + `StandingsRow(team_id, matches_played, wins, losses, ties, league_points, round_wins, total_score, rank)`; functions `generate_schedule(team_ids, schedule_format="single_round_robin") -> list[ScheduleFixture]` + `compute_standings(completed_matches, enrolled_teams) -> list[StandingsRow]`; constant `SCHEDULE_FORMATS = ("single_round_robin",)`; bye sentinel `-1` (internal, not exported); match dict 8 keys `match_id, team_red_id, team_blue_id, winner_team_id, red_rounds_won, blue_rounds_won, red_total_points, blue_total_points`; simulator method `BatchSimulator.simulate_scheduled_round(self, season, team_a, team_b, round_number, *, arena_map=None) -> GameRound`; URL file `matches/season_urls.py`; mount `path("seasons/", include("matches.season_urls"))`; URL patterns `path("<int:season_id>/standings/", views.season_standings, name="season_standings")` + `path("<int:season_id>/schedule/", views.season_schedule, name="season_schedule")`; URL names `season_standings` + `season_schedule`; views `matches.views.season_standings` + `matches.views.season_schedule`; standings context keys (4) `season, rows, rows_with_teams, is_draft_preview`; schedule context keys (2) `season, matchdays`; templates `templates/seasons/standings.html` + `templates/seasons/schedule.html`; DOM ids `season-standings-table` + `season-standings-empty` + `season-draft-preview-banner` + `season-state-badge` + `season-schedule-table` + `season-schedule-empty` + `season-schedule-matchday-{n}`; admin classes `matches.admin.LeagueAdmin` (list_display `("name", "mode", "state", "created_at")`) + `matches.admin.SeasonAdmin` (list_display `("name", "league", "state", "schedule_format", "start_date")` + `filter_horizontal = ("teams",)`); migration `matches/migrations/0029_league_season_match_fk.py` (deps `("matches", "0028_gameround_is_simulated")` + latest `teams`, ops in order `CreateModel(League)` → `CreateModel(Season)` → `AddField(Match, season)`); test files `matches/tests/test_schedule_generator.py` + `matches/tests/test_standings.py` + `matches/tests/test_lg01_models.py` + `matches/tests/test_lg01_simulator.py`. Seam contract: [`.claude/worktrees/lg-01-seam-contract.md`](.claude/worktrees/lg-01-seam-contract.md).

### LG-01a · Mode picker landing + `/leagues/` list

Replaces the existing `/` homepage redirect (today `path("", include(
"teams.urls"))`) with a card-based mode-picker landing — **Sandbox**
card → links to `/teams/` (existing URL unchanged), **Single-player
League** card → links to `/leagues/`, **Multiplayer** card → greyed
"Coming soon". In-progress Leagues are listed directly on the landing
as clickable cards (zengm `main_dashboard_example.png` pattern). The
mode-picker view lives in `core/` (new `landing_view`); the
`/leagues/` index lives in `matches/`. No model change.
- completed
- note: thin user-facing surface layer over the LG-01 foundation — **two new read-only views** (`core.views.landing` at `/` reverse-named `landing`, `matches.views.league_list` at `/leagues/` reverse-named `league_list`), **two new templates** (`templates/core/landing.html`, `templates/leagues/list.html`), **one new URL include file** `matches/league_urls.py` (mirrors `matches/season_urls.py` — no `app_name`, bare URL name `league_list`), a **2-line `urls.py` diff** (replaced `path("", include("teams.urls"))` with `path("", core_views.landing, name="landing")` after adding `from core import views as core_views`; inserted `path("leagues/", include("matches.league_urls"))` immediately after the existing `path("seasons/", ...)` line; the `path("teams/", include("teams.urls"))` mount is unchanged so `{% url 'team_list' %}` keeps reversing to `/teams/`; HX-01 ordering comment for `path("players/", ...)` stays accurate since the `""` line still exists), and a **2-line `base.html` navbar diff** (`navbar-brand` href flipped from `{% url 'team_list' %}` to `{% url 'landing' %}` with the visible `⚡ Laserforce Manager` text + unicode `⚡` unchanged; new `<a class="nav-link" id="leagues-nav-link" href="{% url 'league_list' %}">Leagues</a>` inserted as the FIRST child of `<div class="navbar-nav ms-auto">` above the existing `Teams` link, no other nav-link line touched). `landing(request) -> HttpResponse` is undecorated, runs **one ORM query** `League.objects.filter(state="active").order_by("-id")`, and **lazy-imports `from matches.models import League` INSIDE the function body** to mirror the `core/views.py::map_heatmap_data` lazy-import precedent (avoids the apps-loading cycle); context (frozen) is `{in_progress_leagues}`. `league_list(request) -> HttpResponse` is undecorated, runs two ORM queries (`state="active"` then `state="archived"`, both `order_by("-id")`); context (frozen) is `{active_leagues, archived_leagues}`. **11 locked DOM ids** — `mode-picker`, `mode-card-sandbox`, `mode-card-league`, `mode-card-multiplayer` (non-anchor `<div>` with `aria-disabled="true"` + `<span class="badge bg-secondary">Coming soon</span>`, must NOT be wrapped in `<a>`), `in-progress-leagues` (rendered only when at least one active League exists — empty branch emits no notice), `in-progress-league-card-{id}` (one per active League), `leagues-nav-link`, `league-list-active-table` (rendered only when non-empty), `league-list-archived-table` (rendered only when non-empty), `league-list-empty-notice` (rendered only when both lists empty, substring `No Leagues yet`), `league-create-link` (always rendered). **Deferred broken-link decision (locked):** per-League name links and in-progress cards use the raw href string `/leagues/<id>/` — NOT `{% url 'league_detail' ... %}` — because the `league_detail` URL name lands in LG-01c; similarly `league-create-link` uses raw `/leagues/create/` deferred to LG-01b. Both 404 at LG-01a merge time; the web-smoke triage acknowledges these. **21 view tests + 1 navbar regression** across `core/tests.py` (extended) and `matches/tests/test_league_list.py` (new). **No model change, no migration, no ADR, no CONTEXT.md edit, no new domain term, no JS, no new dependency, no `/teams/` route change, no `team_list` view / URL name change, no `select_related` on the `League.active_season` `@property` (per-card extra query is acceptable for a user-bounded landing list — non-breaking to optimise later)**. Seam contract: [`.claude/worktrees/lg-01a-seam-contract.md`](.claude/worktrees/lg-01a-seam-contract.md).

### LG-01b · Create-League flow

`GET /leagues/create/` form: League name + initial Season name + start
date + N teams (4/8/12/16) + players-per-team + stat distribution
(mean/std-dev) + `schedule_format` (v1 dropdown shows only
`single_round_robin`, extensible). On POST: creates `League(state=
active)` + initial `Season(state=draft)` + auto-generates Teams via
the existing LG-00 `teams/player_generator.py` pure module + enrolls
those Teams into the Season's M2M. User is redirected to the Season
draft page where they can adjust before clicking "Start Season"
(which is the `draft → active` action defined in LG-01).
`@transaction.atomic`. No model change.
- completed
- note: thin CRUD surface layer over the LG-01 foundation and the LG-00 `_generate_teams` helper — **one new URL** `path("create/", views.league_create, name="league_create")` inserted **BEFORE** the existing `path("", views.league_list, name="league_list")` entry in `matches/league_urls.py` (Django first-match resolution would otherwise have `""` capture every `/leagues/<x>` request), reverse-named `league_create`, GET-or-POST (no 405 guard); **one new form class** `matches.forms.CreateLeagueForm(forms.Form)` appended to the existing `matches/forms.py` with **7 fields in pinned order** — `league_name = forms.CharField(max_length=100)` (required, no uniqueness validation — duplicate League names allowed), `season_name = forms.CharField(max_length=100, initial="Season 1")`, `start_date = forms.DateField(initial=django.utils.timezone.localdate)` (callable initial, evaluated per-bind), `num_teams = forms.TypedChoiceField(choices=[(4, "4"), (8, "8"), (12, "12"), (16, "16")], coerce=int, empty_value=None, initial=4)` (so `cleaned_data["num_teams"]` is an `int`, not a `str`), `schedule_format = forms.ChoiceField(choices=[("single_round_robin", "Single round-robin")], disabled=True, initial="single_round_robin")` (single-option `disabled=True` — Django serves the initial value regardless of POST content so a tampered POST cannot inject a different format, no extra `clean_schedule_format` guard), `mean = forms.IntegerField(min_value=0, max_value=100, initial=50)`, and `std_dev = forms.IntegerField(min_value=1, max_value=40, initial=15)`; **`players_per_team` is NOT a form field** — fixed at the **literal `6`** server-side inline in the view body (locked, not configurable per create). **One new view** `matches.views.league_create(request: HttpRequest) -> HttpResponse` appended to `matches/views.py`, decorated `@transaction.atomic` (single decorator — the entire body is one atomic block so a `_generate_teams` or `Season.objects.create` raise rolls back the League + Season + Teams + Players + slot FKs + M2M rows atomically), with a **6-step body in pinned order**: (1) GET branch ⇒ instantiate `form = CreateLeagueForm()` and render `templates/leagues/create.html` with context `{"form": form}` and return; (2) POST branch ⇒ `form = CreateLeagueForm(request.POST)` and on `not form.is_valid()` re-render the same template with the bound form (errors auto-attached, no `messages.*` flash) and return; (3) build a fresh `rng = random.Random()` (default-seeded — LG-01b does NOT pin a deterministic seed, team / player generation is intentionally random per create) plus defensive copies `team_names_pool = list(TEAM_NAMES)` and `player_names_pool = list(PLAYER_NAMES)` (the `list(...)` mirror of the LG-00b roster-import precedent so `_generate_teams` may mutate pools internally without leaking back into the `teams/constants.py` module-level constants); (4) call `created_teams = _generate_teams(cleaned["num_teams"], 6, rng=rng, mean=cleaned["mean"], std_dev=cleaned["std_dev"], team_names_pool=team_names_pool, player_names_pool=player_names_pool)` with the locked **literal `6`** for `players_per_team`; (5) `league = League.objects.create(name=cleaned["league_name"], mode="league", state="active")` + `season = Season.objects.create(league=league, name=cleaned["season_name"], start_date=cleaned["start_date"], state="draft", schedule_format=cleaned["schedule_format"])` (the `mode="league"` / `state="active"` / `state="draft"` / `schedule_format="single_round_robin"` literals are all field-level defaults, kept explicit for clarity); (6) `season.teams.add(*created_teams)` (M2M bulk-add, single SQL INSERT per row, no per-team `.save()`) then `return redirect("season_standings", season_id=season.id)` (the LG-01 GET URL at `/seasons/<int:season_id>/standings/`, reverse kwarg `season_id` pinned by `matches/season_urls.py`). **Cross-app import** is the single new line `from teams.views import _generate_teams` at the top of `matches/views.py` — the **only** cross-app import LG-01b introduces (name pools come from `teams.constants` so no `teams.views`-side state crosses); the leading underscore on `_generate_teams` reflects its intra-`teams/` private status, LG-01b promotes it to a cross-app seam read-only (no rename, no signature change, no relocation). **One new template** `laserforce_simulator/templates/leagues/create.html` extending `base.html` with `{% block title %}Create League{% endblock %}` (locked exact string) and a single `<form method="post">` containing `{% csrf_token %}`, the 7 form fields rendered field-by-field (NOT `{{ form.as_p }}` / `{{ form.as_table }}` — DOM ids must be deterministic) with per-field `{{ form.<field>.errors }}` blocks adjacent to each input, plus a submit button; **9 locked DOM ids** — `league-create-form` (outer `<form>`), `league-create-league-name` (the `<input type="text">` for `league_name`), `league-create-season-name` (the `<input>` for `season_name`), `league-create-start-date` (the `<input type="date">` for `start_date`), `league-create-num-teams` (the `<select>` for `num_teams`), `league-create-schedule-format` (the `<select disabled>` for `schedule_format` — `disabled` is the only client-side affordance, a pure HTML attribute not JS), `league-create-mean` (the `<input type="number">` for `mean`), `league-create-std-dev` (the `<input type="number">` for `std_dev`), and `league-create-submit` (the submit `<button>` / `<input type="submit">`). The **LG-01a deferred broken-link** `league-create-link` `href="/leagues/create/"` (the `Create League` button on `templates/leagues/list.html`, which 404'd at LG-01a merge time per the LG-01a triage) now resolves to the new GET endpoint without any `templates/leagues/list.html` edit — the raw href string the LG-01a template hardcoded stays verbatim. **Tests** live in the NEW file `matches/tests/test_league_create.py` with **4 `TestCase` subclasses** (pinned names): `TestLeagueCreateGet` (GET → 200, `assertTemplateUsed("leagues/create.html")`, all 9 locked DOM ids present, `schedule_format` `<select>` carries `disabled`, `reverse("league_create")` resolves to `/leagues/create/`), `TestLeagueCreatePost` (POST valid payload → 302 redirect to `reverse("season_standings", args=[season.id])`, exactly 1 new `League` row with `name=…`/`mode="league"`/`state="active"`, exactly 1 new `Season` row with `state="draft"`/`schedule_format="single_round_robin"`/`start_date`/`league_id`/`champion_team is None`/`starting_team_ids_json is None`, exactly `num_teams` new `Team` rows enrolled into `season.teams`, each Team has 6 active-slot Players, smoke-asserts that the redirect-target `/seasons/<id>/standings/` returns 200 — exercising the LG-01 standings view's `is_draft_preview` branch with a real freshly-created Season — plus an `N=16` boundary case creating 16 Teams + 96 Players), `TestLeagueCreateFormValidation` (missing `league_name` / `num_teams=5` / `mean=-1` / `mean=101` / `std_dev=0` / `std_dev=41` / empty `start_date` each re-render at 200 with the appropriate form error and zero rows created, plus a `schedule_format="double_round_robin"` tamper-POST still persists the Season with `schedule_format="single_round_robin"` because `disabled=True` serves the initial value), and `TestSeamWithGenerateTeams` (**locked: NO `mock.patch` on `_generate_teams`** — the real function is exercised end-to-end so signature drift between LG-01b's call site and `teams/views.py` surfaces as a test failure rather than a silent mock pass; real-call assertions that the 4 created Teams each have 6 Players distributed across the 6 slot FKs and that stats fall within `[0, 100]` clipping; plus a **transaction rollback test** that patches `Season.objects.create` — NOT `_generate_teams` — to raise mid-flow and asserts post-raise `League.objects.filter(name=…).count() == 0` AND `Team.objects.filter(name__in=…).count() == 0`, pinning the `@transaction.atomic` boundary against future refactors that might move the decorator or call `_generate_teams` outside the atomic block); tests must NOT touch `simulate_scheduled_round` or any simulator code path (LG-01b does not run a simulation, the Season is created in `draft` state and `_generate_teams` is the only heavy operation, accidentally entering the simulator would be a scope leak and is locked out). **Out of scope (locked):** no model change (`matches/models.py` read-only at LG-01b), no migration (LG-01 shipped `0029_league_season_match_fk.py` and that is the final migration in the LG-01x stack until LG-01c), no ADR write ([ADR-0014](docs/adr/0014-league-season-foundation.md) + [ADR-0015](docs/adr/0015-schedule-on-demand-no-fixture-rows.md) cover the foundation, LG-01b is a CRUD surface and needs no design record), no CONTEXT.md edit (`League` / `Season` / `Standings` glossary entries exist from LG-01), no "Start Season" UI / POST endpoint (the `draft → active` transition via `Season.start_season()` is deferred to LG-01d or later — LG-01b leaves the Season in `draft` indefinitely and the standings page renders the draft preview), no JS (server-rendered HTML only — no Chart.js, no htmx, no inline `<script>` blocks), no API / DRF endpoint (no `/api/leagues/`, no `/api/seasons/` create surface), no `messages.success(...)` flash / `django.contrib.messages` usage (the redirect itself is the user feedback), no new dependency (no `pip install`, no `requirements.txt` edit), no edit to `teams/views.py::_generate_teams` (the function signature is the seam and changing it would break LG-01b's contract with `teams/`), no edit to `teams/forms.py` or `teams/constants.py`, no edit to `templates/leagues/list.html` (the LG-01a `league-create-link` continues to point at the now-resolving URL without template-side changes), no edit to `LeagueAdmin` / `SeasonAdmin` (LG-01 shipped both and LG-01b does not extend the admin surface), no Free Agents Team touch (LG-00 / LG-00b territory — `_generate_teams` creates fresh Teams + Players from the constants pools and LG-01b neither pulls from nor pushes to the Free Agents Team), no deterministic RNG seeding (LG-01b is not under the SIM-07 / SIM-08 contract — it runs no simulator), no simulation mechanics change → no Score Calibration re-baseline obligation. **Locked names** — URL path `/leagues/create/` (inserted before `path("", …)` in `matches/league_urls.py`); URL name `league_create` (bare, no `app_name`); view `matches.views.league_create`; form class `matches.forms.CreateLeagueForm`; form fields `league_name, season_name, start_date, num_teams, schedule_format, mean, std_dev` (7 fields in pinned order, `players_per_team` is NOT a field); template `templates/leagues/create.html` (block title `Create League`); cross-app import `from teams.views import _generate_teams`; redirect URL name `season_standings` (reverse kwarg `season_id`); DOM ids `league-create-form, league-create-league-name, league-create-season-name, league-create-start-date, league-create-num-teams, league-create-schedule-format, league-create-mean, league-create-std-dev, league-create-submit`; test file `matches/tests/test_league_create.py` with classes `TestLeagueCreateGet` / `TestLeagueCreatePost` / `TestLeagueCreateFormValidation` / `TestSeamWithGenerateTeams`; locked literals `players_per_team = 6` (server-side inline), `mode = "league"`, `state = "active"` (League), `state = "draft"` (Season), `schedule_format = "single_round_robin"`. Seam contract: [`.claude/worktrees/lg-01b-seam-contract.md`](.claude/worktrees/lg-01b-seam-contract.md).

### LG-01c · League / Season dashboard

`/leagues/<int:league_id>/` shows the League's current Season summary
(top-3 Standings snippet, next upcoming Round, completed/total Round
count, leaders snippet, "Start Season" / "Play Next" / "Start Next
Season" action button keyed off `state`). `/seasons/<int:season_id>/`
shows the Season overview with a sidebar nav to Standings / Schedule
/ Teams / History (zengm-style layout per
`league_dashboard_view.png`). Read-only views; no model change.
- completed
- note: read-only dashboard view layer over the LG-01 foundation — **two new view functions** (`matches.views.league_dashboard` at `GET /leagues/<int:league_id>/` reverse-named `league_dashboard`, `matches.views.season_dashboard` at `GET /seasons/<int:season_id>/` reverse-named `season_dashboard`, both bare names with no `app_name`), **one new shared private helper** `matches.views._build_dashboard_context(displayed_season: Season | None, season_mode: str) -> dict` (module-level flat, RV-01 / HX-03 `_`-prefixed precedent, returns the **11-key body context** `displayed_season, season_mode, standings_snippet, next_fixture, round_count_completed, round_count_total, leaders_points, leaders_tags, leaders_ratio, action_button_label, action_button_state`), **one new pure module** `matches/season_dashboard.py` (frozen import allowlist `dataclasses` / `typing` / optional `collections.defaultdict` — **NO** Django, NO ORM, NO `random`, NO `datetime`, NO I/O, NO `matches.schedule_generator`, defended by `TestNoDjangoImportsLeaked`), **two new templates** (`templates/leagues/dashboard.html` block title `{{ league.name }} — League` em-dash U+2014 and `templates/seasons/dashboard.html` block title `{{ season.league.name }} — {{ season.name }}`), and **two single-line URL inserts** (`matches/league_urls.py` gets `path("<int:league_id>/", views.league_dashboard, name="league_dashboard")` inserted AFTER the LG-01b `path("create/", …)` line and BEFORE the LG-01a `path("", views.league_list, …)` line so the typed `<int:league_id>/` pattern matches only digit-only paths and leaves `/leagues/` + `/leagues/create/` untouched; `matches/season_urls.py` gets `path("<int:season_id>/", views.season_dashboard, name="season_dashboard")` inserted at the TOP of `urlpatterns` so it does not get shadowed by the LG-01 `<int:season_id>/standings/` / `<int:season_id>/schedule/` patterns). Both views are undecorated (no `@transaction.atomic` — read-only; no `@require_GET` — the explicit `HttpResponseNotAllowed(["GET"])` guard is the locked first-line pattern mirroring `movement_heatmap` / `export_round_report`), each `get_object_or_404` for the 404 branch. The league view's **season-pick logic** (locked, in order): call `league.active_season` (the LG-01 `@property` — implementation MUST call the property, not re-implement the query); non-`None` ⇒ `displayed_season = active` + `season_mode = "draft"` if `active.state == "draft"` else `"active"`; else fall back to `completed_recent = league.seasons.filter(state="completed").order_by("-id").first()` and either `displayed_season = completed_recent` + `season_mode = "completed"`, else `displayed_season = None` + `season_mode = "none"`. The season view's pick is trivial — `displayed_season = season` and `season_mode = season.state` (one of `"draft" | "active" | "completed"`, **never `"none"`** since the Season exists by virtue of URL resolution). Body assembly delegates to `_build_dashboard_context`; the league view's final context is the body context plus the `league` key (12 keys total) and the season view's is the body context plus `season, sidebar_active="overview", sidebar_links` (15 keys total, `displayed_season` kept `== season` for template-include parity). **Branch-specific population**: `"none"` (league-only) ⇒ `standings_snippet = []` / `next_fixture = None` / `round_count_* = 0` / `leaders_* = []` / `action_button_label = "No Season"` / `action_button_state = "none"`; `"draft"` ⇒ standings snippet is the zero-filled top-3 from `displayed_season.teams.all()` sorted by name asc with the 9 LG-01 standings keys all zeroed (`team_id, matches_played=0, …, rank=i+1`) paired with its `team`, `next_fixture = None`, `round_count_* = 0`, `leaders_* = []`, `action_button_label = "Start Season"`, `action_button_state = "start_season"`; `"active"` ⇒ standings via `compute_standings(...)` (LG-01) over `Match.objects.filter(season=displayed_season, is_completed=True)` paired with their Teams via `Team.objects.in_bulk(...)`, `fixtures = generate_schedule(displayed_season.starting_team_ids_json, displayed_season.schedule_format)`, `played_keys = {(frozenset({gr.match.team_red_id, gr.match.team_blue_id}), gr.round_number) for gr in GameRound.objects.filter(match__season=displayed_season).select_related("match")}`, `fixture = find_next_fixture(fixtures, played_keys)`, `round_count_completed, round_count_total = round_progress(fixtures, played_keys)`, `leaders_* = compute_leaders(player_rounds, stat, limit=3)` per stat, `action_button_label = "Play Next"`, `action_button_state = "play_next"`; `"completed"` ⇒ standings + leaders same as `"active"`, `find_next_fixture` returns `None` on an all-played Season (LG-01 `complete_if_finished` invariant) so `next_fixture = None`, `round_progress` returns `(len(fixtures), len(fixtures))`, `action_button_label = "Start Next Season"`, `action_button_state = "start_next_season"`. **Pure module `matches/season_dashboard.py`** surfaces the **frozen dataclass `LeaderRow(player_id, player_name, role, team_id, team_name, value, games_played, rank)`** (`@dataclass(frozen=True)`, **8 fields in pinned order**) plus three functions: `compute_leaders(player_rounds: list[dict], stat: str, limit: int = 3) -> list[LeaderRow]` (locked stat vocabulary `"points_per_game"` ⇒ `mean(points_scored)`, `"tags_per_game"` ⇒ `mean(tags_made)`, `"tag_ratio"` ⇒ `sum(tags_made) / max(sum(times_tagged), 1)` — canonical CONTEXT.md sum/sum form NOT mean of per-row ratios, the `max(..., 1)` clamp avoids div-by-zero and matches the existing `Player.career_stats` rule; `value` is `float` even when both sums are 0; deterministic sort ladder `value` desc → `games_played` desc → `player_id` asc; `rank` 1-based dense in iteration order; **empty input ⇒ `[]`** immediately; **unknown stat ⇒ `ValueError(f"Unknown stat {stat!r}; expected one of points_per_game, tags_per_game, tag_ratio")`**; defensive "last row wins" for inconsistent role / team across a player's group — view passes rows in `id` asc so "last" == most-recent PRS), `find_next_fixture(fixtures, played_keys) -> Optional[ScheduleFixture]` (first `ScheduleFixture` whose `(frozenset({team_a_id, team_b_id}), round_number)` is NOT in `played_keys` — side-agnostic `frozenset` match; empty / all-played ⇒ `None`), and `round_progress(fixtures, played_keys) -> tuple[int, int]` (`completed` = count of fixtures matched against `played_keys`, NOT `len(played_keys)` — extra `GameRound` rows that don't correspond to a fixture are not double-counted, defensive HX-03 precedent; empty ⇒ `(0, 0)`). **Player-round seam dict** (only thing crossing view ↔ pure-module for leader aggregation, frozen 7 keys, every key required): `player_id, player_name, role, team_id, team_name, tags_made, times_tagged, points_scored` — materialised by the locked queryset `PlayerRoundState.objects.filter(game_round__match__season=displayed_season).select_related("player", "game_round", "game_round__match").order_by("id")` (single `select_related`-flattened query, `order_by("id")` makes "last row wins" deterministic); `team_id` / `team_name` resolve from `prs.game_round.team_red` / `team_blue` keyed off `prs.team_color`; in the `"none"` branch `displayed_season is None` and the queryset is NOT issued — `leaders_* = []` directly. **`next_fixture` 7-key dict** (built view-side from a `ScheduleFixture` + the two Teams via a single `Team.objects.in_bulk(...)` per view call): `matchday, round_number, team_a_id, team_a_name, team_b_id, team_b_name, date` — the `date` derived as `season.start_date + timedelta(days=(matchday - 1) * 7)` mirroring the LG-01 `season_schedule` per-matchday date derivation byte-for-byte. **Templates** `templates/leagues/dashboard.html` carries the **10 locked DOM ids** with branch-presence rules: `league-dashboard-header` (always), `league-dashboard-state-badge` (always; `"none"` renders literal `"No Season"`), `league-dashboard-action-button` (always, `<button disabled data-action-state="{{ action_button_state }}">` with text `== action_button_label`; HTML `disabled` MUST be present), `league-dashboard-standings-snippet` (draft / active / completed only), `league-dashboard-next-round` (active / completed only; renders `"All fixtures played"` stub when `next_fixture is None` in `"completed"`; omitted entirely in `"draft"` / `"none"`), `league-dashboard-round-count` (active / completed only, `{{ round_count_completed }} / {{ round_count_total }}`), `league-dashboard-leaders-points` / `league-dashboard-leaders-tags` / `league-dashboard-leaders-ratio` (active / completed only), `league-dashboard-no-season-notice` (only when `season_mode == "none"`, contains substring `"No Season"`). `templates/seasons/dashboard.html` carries **15 locked DOM ids**: `season-dashboard-header` (always), `season-dashboard-state-badge` (always), `season-dashboard-action-button` (always, same `<button disabled data-action-state="…">` shape), `season-dashboard-sidebar` (always, outer `<nav>` / `<ul>`), `season-dashboard-sidebar-standings` (always, live `<a href>` reversed via `season_standings`), `season-dashboard-sidebar-schedule` (always, live `<a href>` reversed via `season_schedule`), `season-dashboard-sidebar-teams` (always, disabled `<span class="…disabled…">` — **NO `<a href>`**), `season-dashboard-sidebar-history` (always, disabled `<span>`), `season-dashboard-standings-snippet` (always — container present even in `"draft"` where it iterates zero rows), `season-dashboard-next-round` (active / completed only), `season-dashboard-round-count` (active / completed only), `season-dashboard-leaders-points` / `season-dashboard-leaders-tags` / `season-dashboard-leaders-ratio` (active / completed only). **`sidebar_links` shape** (frozen 5 entries in pinned order): `overview` (`url=None`, `disabled=False`, `active=True` — renders `<span class="sidebar-link active">Overview</span>`), `standings` (live link to `season_standings`), `schedule` (live link to `season_schedule`), `teams` (disabled `<span>`), `history` (disabled `<span>`); `sidebar_active = "overview"` always at LG-01c. **Raw-href patterns** (LG-01a deferred broken-link precedent, locked): per-leader anchors render the raw string `/players/{{ row.player_id }}/career-stats/` (NOT `{% url 'player_career_stats' ... %}`) with anchor text `{{ row.player_name }}` and `{{ row.value|floatformat:2 }}` rendered adjacent; the "View all leaders" anchors render raw `/leagues/{{ league.id }}/leaders/` and `/seasons/{{ season.id }}/leaders/` — both 404 at LG-01c merge time, tests assert the literal href substring. **Tests** live in **three NEW files** under `matches/tests/`: `test_season_dashboard.py` (pure-unit `SimpleTestCase` — `TestComputeLeadersEmpty`, `TestComputeLeadersSinglePlayer`, `TestComputeLeadersTiebreak`, `TestComputeLeadersDeterministic`, `TestComputeLeadersRoleMix`, `TestComputeLeadersStatVocabulary`, `TestComputeLeadersLimit`, `TestComputeLeadersDefensiveLastWins`, `TestFindNextFixture`, `TestRoundProgress`, `TestNoDjangoImportsLeaked` subprocess fresh-import); `test_league_dashboard.py` (Django `TestCase` — `TestLeagueDashboardRouting`, `TestLeagueDashboardSeasonPick`, `TestLeagueDashboardDraftBranch`, `TestLeagueDashboardActiveBranch`, `TestLeagueDashboardCompletedBranch`, `TestLeagueDashboardNoneBranch`); `test_season_dashboard_view.py` (Django `TestCase` — `TestSeasonDashboardRouting`, `TestSeasonDashboardStateMatrix`, `TestSeasonDashboardSidebar`, `TestSeasonDashboardBody`); tests that exercise an `"active"` or `"completed"` Season hand-construct the persisted `Match` + `GameRound` + `PlayerRoundState` rows (mirroring the LG-01 simulator-test setup pattern) — tests must NOT touch `simulate_scheduled_round` / `simulate_match` / `save_games` or any simulator entry point (LG-01c runs no simulation, a test that accidentally enters the simulator is a scope leak and locked out). **Determinism / scope.** Read-only views — no writes, no RNG, no simulation, no `_flush_to_db` touch, no SIM-07 / SIM-08 contract interaction, no Score Calibration re-baseline obligation. The pure module consumes no RNG, no Django, no ORM, no `datetime` — unit-testable with zero DB. The 4 `season_mode` literals (`"draft" / "active" / "completed" / "none"`), 4 `action_button_state` literals (`"start_season" / "play_next" / "start_next_season" / "none"`), and 5 sidebar key literals (`"overview" / "standings" / "schedule" / "teams" / "history"`) are implementation enums NOT domain language and NOT added to CONTEXT.md. **Out of scope (locked).** No model change (`matches/models.py` read-only at LG-01c); no migration (LG-01's `0029_league_season_match_fk.py` remains the final LG-01x migration); no ADR write (LG-01c is a read-only view layer over already-decided foundations — nothing surprising-without-context, nothing hard-to-reverse, no real trade-off, the seam contract is the only artifact); no CONTEXT.md edit (the `League` / `Season` / `Standings` glossary entries exist from LG-01); no POST endpoint (both views GET-only with explicit `HttpResponseNotAllowed(["GET"])`; the placeholder `<button disabled>`s are HTML-attribute disabling only — no `<form>` wrapper, no `csrf_token`, no `request.method == "POST"` branch); no `Season.start_season()` UI wire-up (LG-01d); no `simulate_scheduled_round` touch (LG-01c imports no simulator, runs no simulation); no LG-01d / LG-01e / LG-01f / LG-01g logic (Play Next, Start Next Season chain, Teams tab, History tab deferred); no Teams view, no History view (sidebar entries are disabled `<span>` placeholders); no `/leagues/<id>/leaders/` or `/seasons/<id>/leaders/` URL mount (raw-href deferred-broken-link); no `/players/<id>/career-stats/` URL mount (raw-href, route may exist independently in `teams/`); no JS, no Chart.js, no htmx, no inline `<script>` blocks (server-rendered HTML only); no API / DRF endpoint; no new dependency; no edit to `matches/models.py` / `matches/simulation.py` / `matches/standings.py` / `matches/schedule_generator.py` (LG-01 pure modules consumed verbatim); no edit to `templates/seasons/standings.html` / `templates/seasons/schedule.html` / `templates/leagues/list.html` / `templates/leagues/create.html` (LG-01 / LG-01a / LG-01b templates unchanged); no edit to `LeagueAdmin` / `SeasonAdmin`; no `messages.success(...)` / `django.contrib.messages` usage; no simulation mechanics change → **no Score Calibration re-baseline**. **Locked names** — URL paths `GET /leagues/<int:league_id>/` + `GET /seasons/<int:season_id>/`; URL names `league_dashboard` + `season_dashboard` (bare, no `app_name`); views `matches.views.league_dashboard` + `matches.views.season_dashboard`; shared body-context helper `matches.views._build_dashboard_context`; pure module `matches/season_dashboard.py`; dataclass `season_dashboard.LeaderRow(player_id, player_name, role, team_id, team_name, value, games_played, rank)` (8 fields in pinned order); functions `season_dashboard.compute_leaders` + `season_dashboard.find_next_fixture` + `season_dashboard.round_progress`; stat vocabulary literals `"points_per_game"` + `"tags_per_game"` + `"tag_ratio"`; season-mode literals `"draft"` + `"active"` + `"completed"` + `"none"` (`"none"` is league-only); action-button-state literals `"start_season"` + `"play_next"` + `"start_next_season"` + `"none"`; sidebar key literals `"overview"` + `"standings"` + `"schedule"` + `"teams"` + `"history"`; player-round seam dict 7 keys `player_id, player_name, role, team_id, team_name, tags_made, times_tagged, points_scored`; `next_fixture` seam dict 7 keys `matchday, round_number, team_a_id, team_a_name, team_b_id, team_b_name, date`; templates `templates/leagues/dashboard.html` + `templates/seasons/dashboard.html`; league DOM ids `league-dashboard-header` / `league-dashboard-state-badge` / `league-dashboard-action-button` / `league-dashboard-standings-snippet` / `league-dashboard-next-round` / `league-dashboard-round-count` / `league-dashboard-leaders-points` / `league-dashboard-leaders-tags` / `league-dashboard-leaders-ratio` / `league-dashboard-no-season-notice`; season DOM ids `season-dashboard-header` / `season-dashboard-state-badge` / `season-dashboard-action-button` / `season-dashboard-sidebar` / `season-dashboard-sidebar-standings` / `season-dashboard-sidebar-schedule` / `season-dashboard-sidebar-teams` / `season-dashboard-sidebar-history` / `season-dashboard-standings-snippet` / `season-dashboard-next-round` / `season-dashboard-round-count` / `season-dashboard-leaders-points` / `season-dashboard-leaders-tags` / `season-dashboard-leaders-ratio`; raw href patterns `/players/{{ row.player_id }}/career-stats/` + `/leagues/{{ league.id }}/leaders/` + `/seasons/{{ season.id }}/leaders/`; test files `matches/tests/test_season_dashboard.py` + `matches/tests/test_league_dashboard.py` + `matches/tests/test_season_dashboard_view.py` with classes `TestComputeLeadersEmpty` / `TestComputeLeadersSinglePlayer` / `TestComputeLeadersTiebreak` / `TestComputeLeadersDeterministic` / `TestComputeLeadersRoleMix` / `TestComputeLeadersStatVocabulary` / `TestComputeLeadersLimit` / `TestComputeLeadersDefensiveLastWins` / `TestFindNextFixture` / `TestRoundProgress` / `TestNoDjangoImportsLeaked` (pure-unit), `TestLeagueDashboardRouting` / `TestLeagueDashboardSeasonPick` / `TestLeagueDashboardDraftBranch` / `TestLeagueDashboardActiveBranch` / `TestLeagueDashboardCompletedBranch` / `TestLeagueDashboardNoneBranch` (league view), `TestSeasonDashboardRouting` / `TestSeasonDashboardStateMatrix` / `TestSeasonDashboardSidebar` / `TestSeasonDashboardBody` (season view). Seam contract: [`.claude/worktrees/lg-01c-seam-contract.md`](.claude/worktrees/lg-01c-seam-contract.md).

### LG-01d · Play Season (Start Season + Play One Week + Play Two Months + Play Until End)

- completed
- note: write-surface layer over the LG-01c dashboards — turns the previously-disabled `action_button_state="play_next"` / `"start_season"` placeholders into a Play dropdown driving five new endpoints. **Five new view functions** appended to `matches/views.py` (`start_season`, `play_week`, `play_two_months`, `play_until_end`, `play_status`) plus **one new flat `_`-prefixed helper** `_build_play_status_response(async_result, *, season_id) -> dict` (reuses the API-03 `_celery_state_to_job_status` verbatim). **Two new pure functions** appended to the LG-01c module `matches/season_dashboard.py` on the same frozen import allowlist (`find_next_matchday`, `select_play_fixtures`) — `TestNoDjangoImportsLeaked` continues to pass. **One new Celery task** `matches/tasks.py::play_season_task` (`@shared_task(bind=True, name="matches.play_season")`, `(self, season_id, max_matchdays: int | None = None) -> dict`) with per-Round atomic commits — **no outer `@transaction.atomic`** (load-bearing, recorded in [ADR-0016](docs/adr/0016-play-season-job-execution-model.md)); each Round's atomic commit is the existing `simulate_scheduled_round` decorator, so mid-loop failure leaves prior Rounds persisted and the user can re-click to resume. **Five new URL routes** in `matches/season_urls.py` (`start_season` → `/seasons/<id>/start-season/`, `play_week` → `/seasons/<id>/play-week/`, `play_two_months` → `/seasons/<id>/play-two-months/`, `play_until_end` → `/seasons/<id>/play-until-end/`, `play_status` → `/seasons/<id>/play-status/<job_id>/`) — all 5 inserted BEFORE the LG-01 standings/schedule entries to pin first-match resolution. **One polling endpoint** (`play_status`) shared between both async tasks returning the locked 5-key JSON `{status, completed, total, error, season_id}` (Round-level counts; `status` mapped via the API-03 `_celery_state_to_job_status` helper; `season_id` echoed from the URL kwarg, authoritative over the `?season_id=` query param). **POST guards (locked literals)**: `max_matchdays=1` (Play One Week, inline `with transaction.atomic():` block — whole-matchday atomic), `max_matchdays=8` (Play Two Months, async), `max_matchdays=None` (Play Until End, async); Celery broker name `"matches.play_season"`; `start_season` swallows the LG-01 `Season.clean()` "already active" race via the idempotent substring `"non-completed"` match on `ValidationError` messages. **14 new locked DOM ids** — 7 per dashboard, symmetric across `templates/seasons/dashboard.html` and `templates/leagues/dashboard.html`: `{season,league}-dashboard-play-dropdown` (always when action button renders), `{season,league}-dashboard-play-start-season` (only in `start_season` state), `{season,league}-dashboard-play-one-week` / `-play-two-months` / `-play-until-end` (only in `play_next` state), `{season,league}-dashboard-play-error` (only when `play_error` truthy), `{season,league}-dashboard-play-progress` (always, hidden by default). Inline polling JS per template (no external JS file, duplication locked); on `data.status === "complete"` ⇒ `window.location.reload()`. **Context keys** — both dashboards gain `play_error: str | None` (populated on sync POST failure re-render) and `play_job_id: str | None` (always `None` at LG-01d, reserved). **Two CONTEXT.md edits** — extend the **Job** entry from "Two kinds today" to "Three kinds today" (adds the **Play Season job** with `N=8` / `N=None` parameterisation), and add a new **Matchday** term under `### League and seasons`. **One new ADR** [`docs/adr/0016-play-season-job-execution-model.md`](docs/adr/0016-play-season-job-execution-model.md) recording the per-Round atomic commits decision + the 4 rejected alternatives (outer-atomic task body, two separate task functions, `ScheduleEntry`-row-locking, mid-job cancel UI, server-side `Season.state` lock). **Scope-out (locked)**: no model change, no migration, no `django.contrib.messages`, no `master_seed` UI, no mid-job cancel UI, no top-nav refactor / sidebar / URL nesting (deferred to LG-01h), no per-Season arena map options — `simulate_scheduled_round` called with `arena_map=None`, deferred to LG-01j; no "One Week (Live)" replay surface (deferred to LG-01i, depends on CAR-01); no API / DRF endpoint; no edit to `simulate_match` / `simulate_scheduled_round` / `matches/models.py` / `matches/standings.py` / `matches/schedule_generator.py` / `matches/simulation.py`; no edit to `LeagueAdmin` / `SeasonAdmin`; no JS file added to `static/`; no new dependency; **no simulation mechanics change → no Score Calibration re-baseline**. **Tests** live in **3 files** (2 NEW + 1 EXTENDED) with **11 new test classes**: `matches/tests/test_play_orchestrator.py` (NEW, `SimpleTestCase` pure-unit, classes `TestFindNextMatchday` + `TestSelectPlayFixtures`), `matches/tests/test_lg01d_tasks.py` (NEW, Django `TestCase` under `CELERY_TASK_ALWAYS_EAGER=True`, classes `TestPlaySeasonTaskHappyPath` + `TestPlaySeasonTaskMaxMatchdays` + `TestPlaySeasonTaskPerRoundCommit` + `TestPlaySeasonTaskTeamLookup`), and `matches/tests/views_tests.py` (EXTENDED, classes `TestLg01dStartSeason` + `TestLg01dPlayWeek` + `TestLg01dPlayTwoMonths` + `TestLg01dPlayUntilEnd` + `TestLg01dPlayStatus`). Seam contract: [`.claude/worktrees/lg-01d-seam-contract.md`](.claude/worktrees/lg-01d-seam-contract.md).

### LG-01e · "Start Next Season" chain + League archive

A completed Season's dashboard shows a "Start Next Season" button:
`POST /leagues/<int:league_id>/next-season/` creates a fresh
`Season(state=draft)` inside the same League with `name="Season
N+1"`, `start_date=previous.start_date + 7 * 2 * (N-1) days`, and
M2M copied verbatim from the previous Season. User can edit before
activating. League dashboard also gains an "Archive League"
toggle (sets `League.state=archived`; reversible). No model change.
- completed
- note: write-surface POST endpoint that fills the previously-disabled LG-01c-locked `action_button_state="start_next_season"` placeholder slot on both dashboards — **one new view function** `matches.views.next_season(request: HttpRequest, league_id: int) -> HttpResponse` appended to `matches/views.py`, decorated `@transaction.atomic` (single decorator, no other middleware — mirrors LG-01b `league_create`); **one new URL route** `path("<int:league_id>/next-season/", views.next_season, name="next_season")` inserted into `matches/league_urls.py` AFTER the LG-01c `path("<int:league_id>/", views.league_dashboard, …)` line and BEFORE the LG-01a `path("", views.league_list, …)` line (Django first-match resolution; final `urlpatterns` order `[create/, <int:league_id>/, <int:league_id>/next-season/, ""]`); URL name `next_season` (bare, no `app_name`, mirrors LG-01a / LG-01b / LG-01c precedent); reverse via `reverse("next_season", kwargs={"league_id": league.id})`; POST-only — `if request.method != "POST": return HttpResponseNotAllowed(["POST"])` as the **first** line of the view body (LG-01d `start_season` / `play_week` precedent; no `@require_POST` decorator). View body runs **4 guards in pinned order**: 405 on non-POST (before any ORM hit); 404 via `get_object_or_404(League, pk=league_id)`; **302 redirect to `season_dashboard` of `league.active_season`** when a non-completed Season already exists (active-Season guard — idempotent on the double-submit race; reads the LG-01 `League.active_season` `@property` directly, NOT a re-implemented query); **400 `HttpResponseBadRequest("No completed Season in this League.")`** when `latest_completed = league.seasons.filter(state="completed").order_by("-id").first()` returns `None` (defensive — should never fire from the LG-01c UI, but pins clean-400 behaviour against a direct curl / replay POST). Then in pinned body order: `name = f"Season {league.seasons.count() + 1}"` (`.count()` evaluated BEFORE the create so the new Season takes the next sequential index), `start_date = date(latest_completed.start_date.year + 1, 1, 1)` (calendar-year jump, Jan 1 of next year — **grilling-locked formula supersedes the PLAN.md original `7 * 2 * (N-1) days` which was ambiguous**), `schedule_format = latest_completed.schedule_format` (carry over verbatim), `state = "draft"` explicit on `Season.objects.create(...)`, **NOT set**: `starting_team_ids_json` (snapshotted by `start_season()` at activation, NOT at create — LG-01 precedent) and `champion_team` (only stamped by `complete_if_finished`); then `new_season = Season.objects.create(league=league, name=name, start_date=start_date, schedule_format=schedule_format, state="draft")`; then **copy teams from the snapshot (NOT the live M2M)** — `team_ids = latest_completed.starting_team_ids_json or []` (defensive `or []`, LG-01 schedule generator precedent), `teams_qs = Team.objects.filter(id__in=team_ids)`, `new_season.teams.add(*teams_qs)` (M2M bulk-add, LG-01b precedent); finally `return redirect("season_dashboard", season_id=new_season.id)` → HTTP 302 to the LG-01c new Season's dashboard (renders in `season_mode == "draft"`). The **snapshot-as-source-of-truth rule** is load-bearing — copying from `starting_team_ids_json` rather than `latest_completed.teams.all()` is defence-in-depth that mirrors the LG-01 schedule generator's frozen-snapshot precedent; missing-team ids are silently dropped by the `IN` clause. **PLAN.md original scope narrowed at grilling time**: the "League dashboard also gains an 'Archive League' toggle" is **dropped** from the LG-01x public surface — `LeagueAdmin` already supports the `state="archived"` flip and the admin-only path is sufficient for LG-01e merge time; the public-facing Archive button earns its own task later. Editing a `draft` Season's roster / name / start_date pre-activation is similarly admin-only at LG-01e (`SeasonAdmin` already exposes `filter_horizontal=("teams",)` plus default ModelAdmin scalar fields). **Two MODIFIED templates** `templates/leagues/dashboard.html` + `templates/seasons/dashboard.html` — the LG-01c `{% else %}` branch (currently renders the `<button disabled data-action-state="start_next_season">` placeholder for both the `"start_next_season"` and `"none"` states via fall-through) is split into `{% elif action_button_state == "start_next_season" %}` (real `<form method="post" action="{% url 'next_season' league_id=… %}">` with `{% csrf_token %}` + a single `<button type="submit" data-action-state="{{ action_button_state }}">{{ action_button_label }}</button>`) plus `{% else %}` (keeps the `<button disabled>` for the `"none"` state on the league dashboard; season dashboard never reaches `"none"`). **`league_id` derivation**: league dashboard uses `league.id`, season dashboard uses `season.league_id` (the `_id` accessor avoids the JOIN that `season.league.id` would trigger; values identical). The LG-01c-locked `{league,season}-dashboard-action-button` outer-wrapper `<span>` ids continue to wrap the new form in all 4 branches (LG-01c-test backwards compatibility, mirrors LG-01d's stacking pattern); the LG-01c-locked `data-action-state="{{ action_button_state }}"` attribute is carried on the submit `<button type="submit">` inside the form (NOT on the outer wrapper) so existing LG-01c tests that scan for `data-action-state="start_next_season"` continue to pass. **2 NEW locked DOM ids**: `league-dashboard-next-season-form` and `season-dashboard-next-season-form` (the `<form method="post">` elements' `id` attributes, only when `action_button_state == "start_next_season"`). The submit text is literally `"Start Next Season"` (rendered via the LG-01c-locked `action_button_label` context key which `_build_dashboard_context` already sets to `"Start Next Season"` in the `season_mode == "completed"` branch). **No inline JS, no `<script>` block, no `fetch()` interception** — the form submits synchronously (server-side 302; LG-01e is sync, unlike LG-01d's async Play Two Months / Until End forms). **Cross-app imports** — LG-01e introduces **zero truly new** top-of-file imports: `from teams.models import Team` (LG-01c), `from datetime import date` (LG-01b), `from django.db import transaction` (LG-01b), `from django.shortcuts import redirect, get_object_or_404` (LG-01b), `from django.http import HttpResponseNotAllowed, HttpResponseBadRequest` (`HttpResponseNotAllowed` already imported per LG-01c / LG-01d; `HttpResponseBadRequest` may need adding to the existing `from django.http import …` line if not present — the Code agent defensively checks existing imports and adds only the names actually missing), `from .models import League, Season` (LG-01) — every name needed is already at the top of `matches/views.py`, defensive check + no-duplicate rule. **Context keys** — no new keys: LG-01c provides `action_button_label = "Start Next Season"` / `action_button_state = "start_next_season"` / `league` / `season` (all consumed verbatim); LG-01d provides `play_error: str | None` / `play_job_id: str | None` (LG-01e **reads neither and populates neither** — error paths redirect or 400, never re-render; LG-01e is sync). The LG-01c `_build_dashboard_context` helper is NOT edited (11-key body context consumed verbatim); the `matches/season_dashboard.py` pure module gains **zero new functions**. **Tests** live in **3 files** (1 NEW + 2 EXTENDED) with **9 + 1 + 1 new test classes**: `matches/tests/test_lg01e_next_season.py` (NEW, Django `TestCase`, classes `TestNextSeasonRouting` + `TestNextSeasonHappyPath` + `TestNextSeasonNameFormat` + `TestNextSeasonStartDate` + `TestNextSeasonScheduleFormatCarry` + `TestNextSeasonTeamsCopiedFromSnapshot` + `TestNextSeasonActiveSeasonGuard` + `TestNextSeasonNoCompletedGuard` + `TestNextSeasonAtomicity`); `matches/tests/test_league_dashboard.py` (EXTENDED, append `TestLg01eDashboardWiring` only — asserts the league dashboard's completed branch renders `<form id="league-dashboard-next-season-form">` with the correct action URL, csrf token, submit text, and `data-action-state="start_next_season"`; draft / active / none branches DO NOT render the form id); `matches/tests/test_season_dashboard_view.py` (EXTENDED, append `TestLg01eDashboardWiring` only — symmetric on the season dashboard with action URL derived from `season.league_id`). Tests must NOT touch `simulate_scheduled_round` / `simulate_match` / `save_games` or any simulator entry point (LG-01e runs no simulation, accidentally entering the simulator is a scope leak and locked out); tests must NOT `mock.patch` the ORM beyond the single `Season.objects.create` patch in `TestNextSeasonAtomicity` (forces the rollback path — LG-01b transaction-rollback precedent). **Scope-out (locked)**: no model change (`matches/models.py` read-only at LG-01e), no migration (LG-01's `0029_league_season_match_fk.py` remains the final LG-01x migration), no ADR write ([ADR-0014](docs/adr/0014-league-season-foundation.md) + [ADR-0015](docs/adr/0015-schedule-on-demand-no-fixture-rows.md) + [ADR-0016](docs/adr/0016-play-season-job-execution-model.md) cover the foundation, schedule surface, and play job-execution model; LG-01e is a thin CRUD POST endpoint with no real trade-off requiring a record), no CONTEXT.md edit (`League` / `Season` / `Standings` / `Matchday` / `Job` glossary entries exist from LG-01 / LG-01d; "Start Next Season" is a UI label, not a domain term), no new pure module (LG-01e is pure CRUD; no aggregation worth factoring out), no "Archive League" toggle UI (deferred to admin-only `LeagueAdmin`; **narrows the PLAN.md original LG-01e scope**), no edit-draft UI (admin-only via `SeasonAdmin`), no `Season.state="archived"` value (completed Seasons already effectively read-only per LG-01 invariants), no edit to `matches/models.py` / `matches/simulation.py` / `matches/standings.py` / `matches/schedule_generator.py` / `matches/season_dashboard.py` / `matches/tasks.py` / `LeagueAdmin` / `SeasonAdmin`, no edit to `matches/forms.py` (LG-01e takes no form input — the POST carries only `csrfmiddlewaretoken`, every new Season field is derived server-side from `latest_completed`), no simulator touch / no RNG / **no SIM-07 / SIM-08 contract interaction / no Score Calibration re-baseline**, no JS / no inline `<script>` / no htmx / no Alpine, no new dependency, no API / DRF endpoint (`/api/leagues/<id>/next-season/` deferred — LG-01e is UI-only), no `django.contrib.messages` flash (the 302 redirect IS the user feedback), no backfill (pre-LG-01e completed Seasons untouched), no top-nav refactor / sidebar / URL nesting (deferred to LG-01h alongside LG-01d), no re-baseline of LG-01c / LG-01d tests (the LG-01c `data-action-state` scan continues to pass post-LG-01e because the new `<form>` carries the same attribute on its submit button). **Locked names** — URL path `POST /leagues/<int:league_id>/next-season/`; URL name `next_season` (bare, no `app_name`); URL file edit `matches/league_urls.py` (single-line insert, final order `[create/, <int:league_id>/, <int:league_id>/next-season/, ""]`); view `matches.views.next_season`; decorator `@transaction.atomic`; redirect target on success URL name `season_dashboard` (`reverse("season_dashboard", args=[new_season.id])` → HTTP 302); redirect target for the active-Season guard URL name `season_dashboard` (`reverse("season_dashboard", args=[league.active_season.id])` → HTTP 302); 400 response `HttpResponseBadRequest("No completed Season in this League.")` (exact body literal); 405 response `HttpResponseNotAllowed(["POST"])` (first line of view body); 404 response `get_object_or_404(League, pk=league_id)`; active-Season check `league.active_season` (LG-01 `@property`); latest-completed query `league.seasons.filter(state="completed").order_by("-id").first()`; templates MODIFIED `templates/leagues/dashboard.html` + `templates/seasons/dashboard.html`; NEW DOM ids `league-dashboard-next-season-form` + `season-dashboard-next-season-form`; preserved LG-01c DOM ids `league-dashboard-action-button` + `season-dashboard-action-button` (outer-wrapper `<span>`); preserved LG-01c attribute `data-action-state="{{ action_button_state }}"` (on the submit `<button>` inside the form); locked literals `name = f"Season {league.seasons.count() + 1}"` (`.count()` evaluated BEFORE create), `state = "draft"`, `start_date = date(latest_completed.start_date.year + 1, 1, 1)` (calendar-year jump, supersedes the PLAN.md original `7 * 2 * (N-1) days`), `schedule_format = latest_completed.schedule_format`, submit label `"Start Next Season"`; locked 400-body literal `"No completed Season in this League."`; snapshot read `latest_completed.starting_team_ids_json or []`; team resolution `Team.objects.filter(id__in=team_ids)`; M2M populate `new_season.teams.add(*teams_qs)`; cross-app imports `from teams.models import Team` + `from datetime import date` + `from django.db import transaction` + `from django.shortcuts import redirect, get_object_or_404` + `from django.http import HttpResponseNotAllowed, HttpResponseBadRequest` + `from .models import League, Season` (all defensive check + no-duplicate); context keys READ `league` / `season` / `action_button_state` / `action_button_label`; context keys NOT used `play_error` / `play_job_id`; pure module touched (none); new pure functions (none); test files `matches/tests/test_lg01e_next_season.py` (NEW) + `matches/tests/test_league_dashboard.py` (EXTENDED) + `matches/tests/test_season_dashboard_view.py` (EXTENDED); test classes `TestNextSeasonRouting` / `TestNextSeasonHappyPath` / `TestNextSeasonNameFormat` / `TestNextSeasonStartDate` / `TestNextSeasonScheduleFormatCarry` / `TestNextSeasonTeamsCopiedFromSnapshot` / `TestNextSeasonActiveSeasonGuard` / `TestNextSeasonNoCompletedGuard` / `TestNextSeasonAtomicity` (NEW file) + `TestLg01eDashboardWiring` (league) + `TestLg01eDashboardWiring` (season). Seam contract: [`.claude/worktrees/lg-01e-seam-contract.md`](.claude/worktrees/lg-01e-seam-contract.md).

### LG-01f · League history

`/leagues/<int:league_id>/history/` — cross-Season history page (zengm
`league_history_view.png` pattern): one row per completed Season with
champion (denormalised on `Season.champion_team_fk` per LG-01),
runner-up (computed view-side from Standings 2nd place), Standings
top 3, total Matches played. Pure read-only view; one ORM query. No
model change.
- completed
- note: read-only paginated **League History** page plus a project-wide nav refactor — ships **one new view** `matches.views.league_history(request, league_id) -> HttpResponse` at `GET /leagues/<int:league_id>/history/` reverse-named `league_history` (bare name, no `app_name`; URL inserted **AFTER** LG-01e `<int:league_id>/next-season/` and **BEFORE** LG-01a `""` in `matches/league_urls.py`; final order `[create/, <int:league_id>/, <int:league_id>/next-season/, <int:league_id>/history/, ""]`); undecorated (read-only, no `@transaction.atomic`, no `@require_GET`); GET-only via `HttpResponseNotAllowed(["GET"])` as the **first** line of the view body (LG-01c / LG-01d / LG-01e precedent); 404 via `get_object_or_404(League, pk=league_id)`. **Four new module-level `_`-prefixed flat helpers** in `matches/views.py`: `_build_history_row(season, teams_by_id, *, is_in_progress) -> dict` (11-key frozen row dict — `season_id, season_name, season_url, start_date, teams_enrolled, matches_played, champion, runner_up, tournament_champion, top_three, is_in_progress`; consumes the prefetch cache + `teams_by_id` lookup, zero DB hits; `top_three` exactly length 3 with `None` padding; `tournament_champion` always `None` at LG-01f as an LG-02 reservation; `champion` falls back to `standings[0]` when `champion_team is None` defensively), `_build_league_sidebar_links(league, displayed_season, sidebar_active) -> list[dict]` (returns exactly **14 dicts** in pinned section order `[top, league, team, players]` with 6-key shape `key, label, section, url, disabled, active` — `disabled = (url is None)`, `active = (entry["key"] == sidebar_active)`), `_coerce_per_page(raw, default=10) -> int` (whitelist `(10, 25, 50, 100)`, invalid ⇒ default), and `_coerce_page(raw, default=1) -> int` (positive-int string semantics for Django `Paginator.get_page`). **The LG-01c `_season_sidebar_links` 5-entry helper is DELETED wholesale** (replaced by `_build_league_sidebar_links`). View body issues **3 SQL queries** for data — `get_object_or_404(League)`, `league.seasons.select_related("champion_team").prefetch_related("matches", "teams").filter(state__in=["active", "draft", "completed"]).order_by("-id")`, and `Team.objects.in_bulk(team_ids)` — then identifies the **in-progress Season** via `next((s for s in seasons if s.state in {"active", "draft"}), None)` (LG-01 invariant: ≤ 1 non-completed Season per League), paginates **completed** Seasons only via standard Django `Paginator` (the in-progress row is **NOT** counted toward `per_page` — appears on every page; with `per_page=10` page 1 = 1 in-progress + 10 completed = 11 `<tr>`), and renders the **9 context keys** `league, in_progress_row, completed_rows, page_obj, paginator, per_page, per_page_options=(10,25,50,100), sidebar_links, sidebar_active="history"`. **`displayed_season` resolution** for the sidebar's Standings + Schedule URLs uses the locked chain `league.active_season or league.seasons.filter(state="completed").order_by("-id").first()` — LIVE when non-`None`, disabled fallback when `None`. **Per-row 10-column order** (left to right): Season name (live link via `reverse("season_dashboard", args=[season.id])`), Start date (`{{ season.start_date|date:"Y-m-d" }}`), # teams enrolled (`len(season.starting_team_ids_json or [])` for completed; `season.teams.count()` via prefetch when `starting_team_ids_json is None` on a draft Season), Total Matches played (`len([m for m in season.matches.all() if m.is_completed])` over the prefetch — NOT a `.filter(is_completed=True).count()` query), Champion (`season.champion_team` with `standings[0]` fallback for completed rows; literal **`"In progress"`** badge for the in-progress row inside an element whose CSS class contains the substring `"in-progress"`), Runner-Up (`teams_by_id.get(standings[1]["team_id"])` else `"—"`), Tournament Champion (literal `"—"` em-dash U+2014 placeholder, LG-02 fills later), 1st/2nd/3rd place (standings ranks 1–3, `"—"` fallback when fewer than 3 teams have played); the in-progress row's cells 6/8/9/10 populate live standings from `compute_standings(matches_list, enrolled_teams)` over completed Matches so far (may be `[]`). **`standings` consumption** consumes the LG-01 pure module `matches.standings.compute_standings` byte-for-byte (no new pure module, no edit) — the helper reads `standings[i].team_id` (`StandingsRow` dataclass attribute) with a `getattr` adapter for forward-compat. **One NEW partial** `templates/_partials/league_sidebar.html` — outer `<nav id="league-sidebar">`, iterates `sidebar_links` grouping by `entry["section"]` in pinned order `[top, league, team, players]` with `<h6>` section-header labels `"LEAGUE" / "TEAM" / "PLAYERS"` (the `"top"` section has no header — Dashboard sits above the LEAGUE header), disabled entries render `<span class="...disabled...">` (NO `<a href>`), live entries render `<a id="sidebar-{section}-{key}" href="{{ entry.url }}">`, active entry's class contains the substring `"active"`. **One NEW page template** `templates/leagues/history.html` extending `base.html`, `{% block title %}{{ league.name }} — History{% endblock %}` (em-dash U+2014, locked exact format), structure `<div class="d-flex">{% include "_partials/league_sidebar.html" %}<main>...</main></div>`; the only inline JS is the optional per-page-selector `onchange="this.form.submit()"` (LG-00c precedent). **Five MODIFIED templates** — `templates/base.html` (LG-01a `<a id="leagues-nav-link" href="...">Leagues</a>` is replaced by a Bootstrap `<li class="nav-item dropdown">` carrying the **toggle text `"League ▾"`** with caret U+25BE and preserving the LG-01a-locked DOM id `leagues-nav-link` on the toggle `<a class="nav-link dropdown-toggle">` — clicking still navigates to `/leagues/` via the `href` AND opens the dropdown via `data-bs-toggle`; the dropdown menu carries **5 items in locked order**: Standings (disabled `<span class="dropdown-item disabled">`), Playoffs (disabled), Finances (disabled), History (LIVE `<a class="dropdown-item" id="league-history-topbar-link" href="{{ top_bar_history_url }}">History</a>`), Power Rankings (disabled); no inline JS — Bootstrap 5's built-in dropdown component is the only dependency), `templates/leagues/dashboard.html` (`sidebar_active="dashboard"` added to context, sidebar partial included), `templates/seasons/dashboard.html` (`sidebar_active=None` added to context, **the LG-01c-locked 5-entry `season-dashboard-sidebar*` markup is REMOVED** wholesale — DOM ids `season-dashboard-sidebar` / `season-dashboard-sidebar-standings` / `season-dashboard-sidebar-schedule` / `season-dashboard-sidebar-teams` / `season-dashboard-sidebar-history` are GONE; replaced by the new 14-entry partial), `templates/seasons/standings.html` (`sidebar_active="standings"` added, sidebar partial included), `templates/seasons/schedule.html` (`sidebar_active="schedule"` added; the Season Schedule page's `sidebar_active` literal matches the **LEAGUE > Schedule** sidebar entry — Schedule lives in LEAGUE as a 6th entry per the 2026-05-27 resolution in [ADR-0017](docs/adr/0017-league-context-nav-shape.md), diverging from zengm's TEAM-section Schedule because in this project the schedule is league-level; the TEAM section retains a disabled `"schedule_team"` placeholder with the `_team` suffix on the `key` to disambiguate). **14-entry sidebar list** (locked pinned order, single `top` entry then 6 LEAGUE then 4 TEAM then 3 PLAYERS): `(top, dashboard, "Dashboard", LIVE)` / `(league, standings, "Standings", LIVE conditional)` / `(league, schedule, "Schedule", LIVE conditional)` / `(league, playoffs, "Playoffs", disabled)` / `(league, finances, "Finances", disabled)` / `(league, history, "History", LIVE)` / `(league, power_rankings, "Power Rankings", disabled)` / `(team, roster, "Roster", disabled)` / `(team, schedule_team, "Schedule", disabled)` / `(team, finances_team, "Finances", disabled)` / `(team, history_team, "History", disabled)` / `(players, free_agents, "Free Agents", disabled)` / `(players, trade, "Trade", disabled)` / `(players, trading_block, "Trading Block", disabled)` — 4 LIVE entries (Dashboard, Standings conditional, Schedule conditional, History) + 10 disabled; the LEAGUE Standings / Schedule entries fall back to `disabled=True` when `displayed_season is None`. **Active-key mapping**: League dashboard ⇒ `"dashboard"`, League history ⇒ `"history"`, Season dashboard ⇒ `None` (the LG-01c "Overview" slot is gone — no entry matches the season dashboard at LG-01f, so the sidebar renders with zero active entries), Season standings ⇒ `"standings"`, Season schedule ⇒ `"schedule"`. **One NEW context processor** `core.context_processors.league_nav(request) -> dict[str, str]` returning the single-key `{"top_bar_history_url": <resolved URL>}` via the locked 3-step resolution chain — (1) `request.session["last_league_id"]` if present AND the League still exists ⇒ `reverse("league_history", kwargs={"league_id": lid})`; (2) else if **exactly one** League exists (probed via `League.objects.values_list("id", flat=True)[:2]` so the count query is bounded) ⇒ `reverse("league_history", kwargs={"league_id": <that id>})`; (3) else ⇒ `reverse("league_list")`. Stale session ids are dropped via `League.objects.filter(pk=lid).exists()` so admin deletion never crashes the reverse. Registered in `settings.TEMPLATES[0]["OPTIONS"]["context_processors"]`. At most 2 lightweight queries per request, no caching (deferred). **Session write site** — every League-context view writes `request.session["last_league_id"] = <league_id>` (as `int`, not string) AFTER the 405 / 404 guards and BEFORE the final template render / redirect: `league_dashboard` (LG-01c, `league.id`), `league_history` (LG-01f, `league.id`), `season_dashboard` (LG-01c, `season.league_id`), `season_standings` (LG-01, `season.league_id`), `season_schedule` (LG-01, `season.league_id`), `next_season` (LG-01e, `league.id`, **BEFORE the redirect return statement** so the session cookie is set before the response is built), `start_season` / `play_week` / `play_two_months` / `play_until_end` / `play_status` (LG-01d, each `season.league_id`; `play_status` keeps `last_league_id` fresh on every poll). **In-progress row variants** — `<tr id="league-history-in-progress-row" class="in-progress-row ...">` (locked CSS-class substring `"in-progress-row"`); Champion cell renders literal text `"In progress"` inside an element whose CSS class contains the substring `"in-progress"`; cells 6/8/9/10 render live standings; pinned at the top of the table, repeated on every paginated page. **Empty state** when `len(seasons) == 0` ⇒ `<div id="league-history-empty-notice">` containing substring `"No Seasons yet"` (locked exact substring); the table is omitted; the sidebar partial + top-bar dropdown still render. **Pagination** uses standard Django `Paginator` over `completed_seasons` with `per_page` from `_coerce_per_page` and `page_obj = paginator.get_page(_coerce_page(...))`; per-page selector at `<form id="league-history-per-page-form" method="get">` wrapping `<select id="league-history-per-page-select" name="per_page">` with the 4 options; pagination `<nav id="league-history-pagination">` rendered ONLY when `paginator.num_pages > 1`; per-page persists across page navigation. **Locked DOM ids** (history-page): `league-sidebar` / `sidebar-top-dashboard` / `sidebar-league-standings` / `sidebar-league-schedule` / `sidebar-league-playoffs` / `sidebar-league-finances` / `sidebar-league-history` / `sidebar-league-power_rankings` / `sidebar-team-roster` / `sidebar-team-schedule_team` / `sidebar-team-finances_team` / `sidebar-team-history_team` / `sidebar-players-free_agents` / `sidebar-players-trade` / `sidebar-players-trading_block` / `league-history-table` / `league-history-empty-notice` / `league-history-in-progress-row` / `league-history-row-{season_id}` / `league-history-pagination` / `league-history-per-page-form` / `league-history-per-page-select`. **Locked top-bar dropdown DOM ids**: `leagues-nav-link` (preserved from LG-01a, now on the toggle `<a class="nav-link dropdown-toggle">`) + `league-history-topbar-link` (the History dropdown item). **Locked CSS-class substrings**: `"active"` (active sidebar entry), `"disabled"` (disabled entries), `"in-progress"` (in-progress badge), `"in-progress-row"` (in-progress `<tr>`). **Locked literals**: `"In progress"` (Champion cell for in-progress row), `"—"` (em-dash U+2014, Tournament Champion + empty top-3 ranks), `"No Seasons yet"` (empty notice), `"LEAGUE" / "TEAM" / "PLAYERS"` (section headers), `"League ▾"` (toggle text, U+25BE caret), per-page whitelist `(10, 25, 50, 100)`, query params `?per_page=` + `?page=`. **Tests** live in **3 NEW files + 5+ EXTENDED files** under `matches/tests/`: `test_league_history.py` (NEW, Django `TestCase`, 8 classes `TestLeagueHistoryRouting` / `TestLeagueHistoryEmptyState` / `TestLeagueHistoryCompletedRows` / `TestLeagueHistoryInProgressRow` / `TestLeagueHistoryChampionFallback` / `TestLeagueHistoryPagination` / `TestLeagueHistorySidebar` / `TestLeagueHistorySessionWrite`); `test_league_sidebar.py` (NEW, Django `TestCase` — the helper reads `League.seasons.filter(state="completed")` so DB-touching; classes `TestBuildLeagueSidebarLinks` + `TestSidebarLinkShape`); `test_league_nav_context_processor.py` (NEW, Django `TestCase`, class `TestLeagueNavContextProcessor`); `test_league_dashboard.py` (EXTENDED — append `TestLg01fSidebarRendered` + `TestLg01fSessionWrite`); `test_season_dashboard_view.py` (EXTENDED — **DELETE the LG-01c `TestSeasonDashboardSidebar` class wholesale** since its 5-entry assertions are obsolete under the 14-entry shape; append `TestLg01fSidebarRendered` + `TestLg01fSessionWrite`); `views_tests.py` (EXTENDED — append sidebar + session-write assertions to LG-01 `season_standings` + `season_schedule` tests, `id="sidebar-league-standings"` / `id="sidebar-league-schedule"` carries `"active"`); the 5 LG-01d view test files (EXTENDED — one `test_lg01f_session_writes_last_league_id` per `start_season` / `play_week` / `play_two_months` / `play_until_end` / `play_status` view-test class); `test_lg01e_next_season.py` (EXTENDED — `test_lg01f_session_writes_last_league_id_before_redirect` asserting 302 + `client.session["last_league_id"] == league.id`). Tests must NOT touch `simulate_scheduled_round` / `simulate_match` / `save_games` or any simulator entry point (LG-01f runs no simulation, accidentally entering the simulator is a scope leak and locked out); tests must NOT `mock.patch` the ORM beyond `@override_settings` / `TestCase` machinery. **ADR** [ADR-0017](docs/adr/0017-league-context-nav-shape.md) records the 14-entry sidebar shape, the session-driven top-bar History resolution chain, the rationale for replacing LG-01c's 5-entry sidebar wholesale, and the rationale for putting Schedule in LEAGUE rather than TEAM. **Scope-out (locked)**: no model change (`matches/models.py` / `teams/models.py` / `core/models.py` read-only at LG-01f); no migration (LG-01e's `0029_*` remains the final LG-01x migration); no new pure module (LG-01f is thin view-glue plus one context processor — `matches/season_dashboard.py` gains zero new functions AND is NOT consumed; `matches/standings.py` consumed verbatim; `matches/schedule_generator.py` NOT consumed); no simulator touch / no RNG / no `BatchSimulator` call / **no SIM-07 / SIM-08 contract interaction / no Score Calibration re-baseline obligation**; no JS framework / htmx / Alpine / Stimulus — only Bootstrap 5's built-in dropdown JS (already a project dep) plus the optional `onchange="this.form.submit()"` per-page selector inline JS; no API / DRF endpoint (`/api/leagues/<id>/history/` deferred); no `django.contrib.messages` flash; no new dependency (no `pip install`, no `requirements.txt` edit); no admin change (`LeagueAdmin` / `SeasonAdmin` / `TeamAdmin` unchanged); no CONTEXT.md edit (sidebar / topnav / dropdown / session-pin terminology is implementation language, not domain — `League` / `Season` / `Standings` / `Matchday` glossary entries already exist); no edit to `matches/standings.py` / `matches/schedule_generator.py` / `matches/season_dashboard.py` / `matches/tasks.py`; no edit to `templates/leagues/list.html` / `templates/leagues/create.html`; no edit to LG-01d play-related templates beyond what the modified-template list covers; no "Archive League" toggle UI / "Edit Draft Season" UI (admin-only, deferred); no `Season.state="archived"` value (completed Seasons already effectively read-only per LG-01); no expansion of the `sidebar_active` enum beyond the 14 locked literals + `None` (LG-02+ flips disabled entries to live as features ship); no backfill for legacy completed Seasons without `champion_team` beyond the defensive `standings[0]` fallback in the Champion cell; no top-nav refactor beyond the `Leagues` → `League ▾` dropdown swap — the **mode-based base.html restructure** (different top-bar per LEAGUE / TEAM / PLAYERS mode) is **LG-01h's** scope; LG-01f partially skeletons it via the sidebar's 4-section grouping but does NOT implement mode-switching; no new URL routes beyond `/leagues/<id>/history/` (the disabled sidebar entries + disabled top-bar items do NOT mount routes — they render as `<span class="disabled">` with no `<a href>`); no re-baseline of LG-01c / LG-01d / LG-01e tests beyond the single deletion of `TestSeasonDashboardSidebar` (every other LG-01c / LG-01d / LG-01e test continues to pass without modification — the LG-01c dashboard action-button DOM ids, `data-action-state` attributes, leaders / standings snippet DOM ids, etc. are all preserved by the flex-container restructure); no edit to PLAN.md by the Code agent or the Tests agent (this PLAN.md note is the Docs agent's responsibility). **Locked names** — URL path `/leagues/<int:league_id>/history/`; URL name `league_history` (bare, no `app_name`); view `matches.views.league_history`; helpers `matches.views._build_history_row` / `matches.views._build_league_sidebar_links` / `matches.views._coerce_per_page` / `matches.views._coerce_page`; deleted helper `matches.views._season_sidebar_links` (LG-01c 5-entry); context processor `core.context_processors.league_nav` in NEW file `core/context_processors.py`; sidebar partial `templates/_partials/league_sidebar.html` (NEW); page template `templates/leagues/history.html` (NEW); modified templates `templates/base.html` + `templates/leagues/dashboard.html` + `templates/seasons/dashboard.html` + `templates/seasons/standings.html` + `templates/seasons/schedule.html`; `sidebar_active` literals `"dashboard" / "standings" / "schedule" / "playoffs" / "finances" / "history" / "power_rankings" / "roster" / "schedule_team" / "finances_team" / "history_team" / "free_agents" / "trade" / "trading_block" / None`; sidebar section literals `"top" / "league" / "team" / "players"`; session key `request.session["last_league_id"]` (int); context keys (history view) `league` / `in_progress_row` / `completed_rows` / `page_obj` / `paginator` / `per_page` / `per_page_options=(10,25,50,100)` / `sidebar_links` / `sidebar_active="history"`; context key (top-bar, from processor) `top_bar_history_url`; row-dict 11 keys `season_id, season_name, season_url, start_date, teams_enrolled, matches_played, champion, runner_up, tournament_champion, top_three, is_in_progress`; sidebar-entry-dict 6 keys `key, label, section, url, disabled, active`; per-page whitelist `(10, 25, 50, 100)`; pinned literals `"In progress"` / `"—"` / `"No Seasons yet"` / `"LEAGUE" / "TEAM" / "PLAYERS"` / `"League ▾"`; preserved LG-01a DOM id `leagues-nav-link` (now on dropdown toggle); deleted LG-01c DOM ids `season-dashboard-sidebar` / `season-dashboard-sidebar-standings` / `season-dashboard-sidebar-schedule` / `season-dashboard-sidebar-teams` / `season-dashboard-sidebar-history`; deleted LG-01c test class `TestSeasonDashboardSidebar`; test files `matches/tests/test_league_history.py` (NEW) + `matches/tests/test_league_sidebar.py` (NEW) + `matches/tests/test_league_nav_context_processor.py` (NEW) + EXTENDED `matches/tests/test_league_dashboard.py` + `matches/tests/test_season_dashboard_view.py` + `matches/tests/views_tests.py` + the LG-01d view test files + `matches/tests/test_lg01e_next_season.py`; test classes `TestLeagueHistoryRouting` / `TestLeagueHistoryEmptyState` / `TestLeagueHistoryCompletedRows` / `TestLeagueHistoryInProgressRow` / `TestLeagueHistoryChampionFallback` / `TestLeagueHistoryPagination` / `TestLeagueHistorySidebar` / `TestLeagueHistorySessionWrite` / `TestBuildLeagueSidebarLinks` / `TestSidebarLinkShape` / `TestLeagueNavContextProcessor` / `TestLg01fSidebarRendered` / `TestLg01fSessionWrite`. Seam contract: [`.claude/worktrees/lg-01f-seam-contract.md`](.claude/worktrees/lg-01f-seam-contract.md).

### LG-01g · Per-Team Schedule view

`/leagues/<int:league_id>/team_schedule/<int:team_id>/` (rescoped from
the original PLAN literal `/seasons/<int:season_id>/teams/<int:team_id>/games/`
at grilling time — the URL is now League-scoped with the Season
resolved implicitly, mirroring the LG-01c `displayed_season` chain).
Two-column read-only view of one Team's per-Round schedule inside the
displayed Season — Upcoming Games (unplayed `(fixture, round_number)`
pairs from `generate_schedule(...)` filtered to this Team) and
Completed Games (one row per persisted `GameRound` for a Match where
the Team is `team_red` or `team_blue`). Per-Round granularity preserves
the per-Match colour swap; W/L/T column reflects the picked Team's
per-Round outcome. Adds a single nullable `League.current_team` FK
populated at LG-01b create time and consumed by the LG-01f sidebar's
TEAM > Schedule entry, which flips from disabled placeholder to LIVE
link.
- completed
- note: read-only **per-Team Schedule** page plus a small wiring change across the LG-01b create flow and the LG-01f sidebar partial — ships **one new view** `matches.views.team_schedule(request, league_id, team_id) -> HttpResponse` at `GET /leagues/<int:league_id>/team_schedule/<int:team_id>/` reverse-named `team_schedule` (bare name, no `app_name`; URL inserted **AFTER** LG-01f `<int:league_id>/history/` and **BEFORE** LG-01a `""` in `matches/league_urls.py`; final order `[create/, <int:league_id>/, <int:league_id>/next-season/, <int:league_id>/history/, <int:league_id>/team_schedule/<int:team_id>/, ""]`); undecorated (read-only, no `@transaction.atomic`, no `@require_GET`); GET-only via `HttpResponseNotAllowed(["GET"])` as the **first** line of the view body (LG-01c / LG-01d / LG-01e / LG-01f precedent). The PLAN literal `/games/` URL is **rescoped** to `/team_schedule/<team_id>/` and re-keyed off `league_id` instead of `season_id` — the page title is **`{team_name} — Schedule`** (em-dash U+2014, locked exact format) and the page is a **Schedule**, NOT a "game log"; the rescope is final, recorded here in lieu of an ADR ([ADR-0014](docs/adr/0014-league-season-foundation.md) + [ADR-0015](docs/adr/0015-schedule-on-demand-no-fixture-rows.md) + [ADR-0017](docs/adr/0017-league-context-nav-shape.md) cover the foundation + on-demand schedule + nav shape — LG-01g introduces no new design decision, only one nullable FK + a thin view + a sidebar flip). **One new model field** `matches.models.League.current_team = models.ForeignKey("teams.Team", null=True, blank=True, on_delete=models.SET_NULL, related_name="managed_in_leagues")` — the Team within a League the user manages (the one whose players they can edit, in CAR-01 terms). String-reference `"teams.Team"` avoids a circular import (LG-01 / LG-01a precedent for cross-app FKs in `matches/models.py`). `null=True, blank=True` — pre-LG-01g Leagues stay `current_team=None` with **no backfill** ([ADR-0004](docs/adr/0004-simulation-data-is-disposable.md) precedent); `on_delete=SET_NULL` so Team deletion nulls the FK on every League pointing at it without cascading the League out of history; `related_name="managed_in_leagues"` (plural — a Team may be `current_team` of multiple Leagues, no uniqueness constraint; CAR-01 may tighten this later). **One single-`AddField` migration** `matches/migrations/0030_league_current_team.py` depending on `("matches", "0029_league_season_match_fk")` + the latest `teams` migration at branch-cut time (Code agent resolves the literal `teams` migration name via `python manage.py makemigrations --check --dry-run`; if `0030_*` was taken by another worktree mid-grilling, renumber to the next integer — the contract pins field shape + single-op shape, not the integer). **Three new module-level `_`-prefixed flat helpers** in `matches/views.py`: `_resolve_current_team_for_sidebar(league, displayed_season) -> Team | None` (the **order-(a)-(b)-(c) fallback chain** — (a) `league.current_team` IF that Team is enrolled in `displayed_season.teams.all()` (defensive against admin removing the Team from the Season's M2M between auto-set and render), (b) `displayed_season.teams.order_by("name").first()` alphabetical, (c) `None` when the Season has no teams or `displayed_season is None`; reads `league.current_team_id` first so the FK SELECT is avoided when the chain falls through), `_render_fixture_sides(fixture, teams_by_id) -> tuple[Team, Team]` (pure no-DB resolver for the per-Match colour swap — Round 1 returns `(teams_by_id[team_a_id], teams_by_id[team_b_id])`, Round 2 returns `(teams_by_id[team_b_id], teams_by_id[team_a_id])` mirroring the arg-reversal `simulate_scheduled_round` performs when persisting the second Round of a Match), and `_build_team_schedule_rows(displayed_season, team, fixtures, played_game_rounds, teams_by_id) -> dict[str, list[dict]]` (no-DB row-builder consuming the view's pre-fetched `played_game_rounds` queryset; builds Upcoming via the `(frozenset({team_a_id, team_b_id}), round_number) NOT IN played_keys` filter Side-agnostically — same idiom as `find_next_fixture` / LG-01c — and Completed by walking the persisted `GameRound`s with `matchday` recovered via a `fixture_by_key` lookup, falling back to `matchday=0` defensively for sandbox-Match conversion edges; returns `{"upcoming": list[dict], "completed": list[dict]}`). **One MODIFIED helper** — `matches.views._build_league_sidebar_links(league, displayed_season, sidebar_active) -> list[dict]` keeps its LG-01f signature **byte-for-byte unchanged**; only the body of the `schedule_team` entry flips from the LG-01f-locked always-disabled shape `{"url": None, "disabled": True, "active": False}` to a LIVE-when-resolvable computation that calls `_resolve_current_team_for_sidebar(...)`, sets `url = reverse("team_schedule", kwargs={"league_id": league.id, "team_id": picked.id})` when the chain returns non-`None`, and falls back to `url=None`/`disabled=True` otherwise; `active=True` is set only when `sidebar_active == "schedule_team"` (i.e. on the Team Schedule page itself). The 14-entry sidebar count is preserved, all 13 other entries are byte-for-byte unchanged from LG-01f, and the LG-01f-shipped 5 modified templates (`base.html`, `leagues/dashboard.html`, `seasons/dashboard.html`, `seasons/standings.html`, `seasons/schedule.html`) are **untouched** — the partial reads the new `url`/`disabled`/`active` fields off the helper output the same way it already does. **LG-01b auto-set hook** — inside the existing `@transaction.atomic` body of `matches.views.league_create`, between the LG-01b step-5a `League.objects.create(...)` and step-5b `Season.objects.create(...)` calls, two new lines insert `league.current_team = sorted(created_teams, key=lambda t: t.name)[0]` followed by `league.save(update_fields=["current_team"])`. The sort is necessary for deterministic auto-set (`_generate_teams` returns Teams in RNG-driven order); `update_fields=[…]` is a single-column UPDATE that won't touch `mode` / `state` / `name`; the write lives inside the atomic block so a later `Season.objects.create` raise rolls back the FK write atomically with the League create. The LG-01e `next_season` view is **NOT touched** — `League.current_team` carries forward by reference across Seasons, and the LG-01g sidebar fallback chain handles the rare case where the carried Team is no longer enrolled in the new Season's M2M. **One NEW page template** `templates/leagues/team_schedule.html` extending `base.html` with `{% block title %}{{ team.name }} — Schedule{% endblock %}` (em-dash U+2014, locked) and structure `<div class="d-flex">{% include "_partials/league_sidebar.html" %}<main>...</main></div>`; the LG-01f sidebar partial is consumed unchanged. **Two-column page shape** — Upcoming Games column (one `<tr>` per unplayed `(fixture, round_number)` pair from `generate_schedule(displayed_season.starting_team_ids_json or sorted([t.id for t in displayed_season.teams.all()]), displayed_season.schedule_format)` filtered to fixtures where `team.id in {fixture.team_a_id, fixture.team_b_id}`) and Completed Games column (one `<tr>` per persisted `GameRound` in `GameRound.objects.filter(match__season=displayed_season).filter(Q(match__team_red=team) | Q(match__team_blue=team)).select_related("match", "match__team_red", "match__team_blue").order_by("id")`). **Per-Round granularity is the contract** — the two Rounds of one Match appear as two separate rows on their respective matchdays (the schedule unit is the Round, per [ADR-0015](docs/adr/0015-schedule-on-demand-no-fixture-rows.md) and the **Team schedule** CONTEXT.md entry), so a partial Match (Round 1 played, Round 2 not) naturally splits — its Round 1 lands in Completed, its Round 2 in Upcoming. **Per-Match colour swap on Round 2** — for Upcoming Round-2 rows the view-side flip in `_render_fixture_sides` reverses `(team_a, team_b)` to `(team_b, team_a)` so the displayed Sides match what `simulate_scheduled_round` will actually persist (the round-2 simulator call reverses team args byte-for-byte from `simulate_match`); for Completed rows the Side read comes directly off `game_round.match.team_red` / `team_blue` because the persisted `GameRound` already records the physical Sides for that Round. Each row renders as `(R) {red_team_name} VS (B) {blue_team_name}` with the per-Round Side annotation (locked literal glyphs `"(R)"` / `"(B)"`, concatenation pattern locked verbatim, tests substring-match). **Per-Round outcome `"W"/"L"/"T"`** on Completed rows is computed from the picked Team's per-Round perspective off `match.red_round1_points` / `blue_round1_points` (when `round_number==1`) or `red_round2_points` / `blue_round2_points` (when `round_number==2`) — **NOT** from the rolled-up `Match.winner` / `Match.red_total_points` / `Match.red_rounds_won` (which include the team-elim bonus + both Rounds + bonus points and can disagree with the per-Round result — e.g. a Match that rolled up as a Win for the picked Team can have an individual Round the picked Team *lost*); the test `test_outcome_is_per_round_not_per_match_winner` pins this distinction. **`date` derivation** is byte-for-byte the LG-01 `season_schedule` precedent — `displayed_season.start_date + timedelta(days=(matchday - 1) * 7)`, imports of `timedelta` defensive against existing LG-01b / LG-01e top-of-file imports. **Team-picker dropdown** — `<select id="team-schedule-team-picker">` scoped to `displayed_season.teams.order_by("name")` alphabetical, with `onchange="window.location.href = '...'.replace('/0/', '/' + this.value + '/')"` inline-JS navigation to `/leagues/<league_id>/team_schedule/<new_team_id>/` (LG-00c per-page-selector inline-JS precedent); `<noscript>` Apply button at `id="team-schedule-team-picker-apply"` is the accessibility fallback (it re-submits as a GET, which under the current path-component scheme re-renders the same page — pinned acceptable degradation, CAR-01 may revisit). **Season resolution chain** — `displayed_season = league.active_season or league.seasons.filter(state="completed").order_by("-id").first()`; the active branch uses the LG-01 `@property`, not a re-implemented query; **404 with `"No Season in this League."`** when both branches return `None` (rare — LG-01b auto-creates a Season at create time, but a manually-deleted Season can produce this state). **9 frozen context keys** (`league`, `displayed_season`, `team`, `upcoming_rows`, `completed_rows`, `team_picker_options`, `sidebar_links`, `sidebar_active="schedule_team"`, `current_team`); **7-key Upcoming row dict** (`matchday`, `round_number`, `date`, `red_team_id`, `red_team_name`, `blue_team_id`, `blue_team_name`); **11-key Completed row dict** (Upcoming keys plus `game_round_id`, `red_score`, `blue_score`, `outcome`). **Session write** — `request.session["last_league_id"] = league.id` (int, not string) AFTER the 405 / 404 guards and BEFORE the final template render, extending the LG-01f session-write site list to include `team_schedule`. **LG-01f sidebar wiring flip — TEAM > Schedule entry behaviour matrix**: on the Team Schedule page itself the entry is LIVE + `active=True`; on the League dashboard / League history / Season dashboard / Season standings / Season schedule pages the entry is LIVE-when-`displayed_season` has teams AND `_resolve_current_team_for_sidebar` returns non-`None` (with `active=False` since `sidebar_active` will be a different literal on those pages), disabled otherwise (no Season in League, or Season has no teams). The defensive degradation cases — `league.current_team` set but Team not enrolled (admin removed), `league.current_team=None` with Season teams present, `league.current_team=None` with Season empty, `displayed_season=None` — are all handled by the order-(a)-(b)-(c) fallback chain in `_resolve_current_team_for_sidebar`. **Locked DOM ids** (page-level, 12 total): `team-schedule-header` / `team-schedule-team-picker-form` / `team-schedule-team-picker` / `team-schedule-team-picker-apply` (inside `<noscript>`) / `team-schedule-upcoming-section` / `team-schedule-upcoming-list` (conditional on `upcoming_rows`) / `team-schedule-upcoming-empty` (substring `"No upcoming games"`) / `team-schedule-completed-section` / `team-schedule-completed-list` (conditional on `completed_rows`) / `team-schedule-completed-empty` (substring `"No completed games"`) / `team-schedule-upcoming-row-{matchday}-{round_number}` (per-row, `(matchday, round_number)` is the unique Upcoming fixture key) / `team-schedule-completed-row-{game_round_id}` (per-row, `game_round_id` is the persisted unique key); preserved LG-01f DOM ids `league-sidebar` (outer sidebar wrapper, untouched) + `sidebar-team-schedule_team` (the sidebar entry id — preserved; inner element flips from `<span class="…disabled…">` to `<a href="…">` when LIVE). The Round detail link on each Completed row uses the literal path `/matches/game-round/{game_round_id}/` (Code agent may swap for a `{% url ... %}` call once the existing URL name is verified, but the literal substring is what tests assert). **Tests** live in **1 NEW file + 3 EXTENDED files** under `matches/tests/`: `test_lg01g_team_schedule.py` (NEW, Django `TestCase`, **12 classes** — `TestTeamScheduleRouting` (405/404 happy/sad paths), `TestTeamScheduleSeasonResolution` (active vs latest-completed branches + the rule-3 404), `TestTeamScheduleRowGranularity` (per-Round split incl. `test_partial_match_round1_in_completed_round2_in_upcoming`), `TestTeamScheduleSideAnnotation` (`test_round2_upcoming_renders_team_b_red_team_a_blue_per_match_colour_swap` + the Completed-row-reads-persisted-Side test), `TestTeamScheduleOutcome` (W/L/T plus `test_outcome_is_per_round_not_per_match_winner`), `TestTeamScheduleSorting` (Upcoming by `(matchday, round_number)`, Completed by `id` asc), `TestTeamScheduleDropdown` (alphabetical scope, locked DOM id, onchange URL shape), `TestTeamScheduleEmptyStates` (`"No upcoming games"` + `"No completed games"` substrings), `TestTeamScheduleContextKeys` (`test_view_ships_nine_frozen_context_keys` + sidebar-active equals literal), `TestTeamScheduleSidebarWiring` (LIVE on the Team Schedule page, disabled when no Season), `TestTeamScheduleSessionWrite` (last_league_id written after guards), `TestTeamScheduleDomIds` (one test per locked DOM id from the inventory above)); `test_lg01_models.py` (EXTENDED — `TestLeagueCurrentTeamField` covers nullable / default-None / SET_NULL on Team delete / `related_name="managed_in_leagues"` reverse accessor / field-meta sanity); `test_league_create.py` (EXTENDED — `TestLg01gCurrentTeamAutoSet` POSTs the form and asserts `league.current_team.name` equals the alphabetically-first generated Team name + `current_team` is in the Season's M2M); `test_league_sidebar.py` (EXTENDED — `TestLg01gScheduleTeamEntryLive` covers all 5 fallback-chain branches end-to-end against `_build_league_sidebar_links`). **Determinism / scope.** Read-only view, no RNG, no simulation, no `_flush_to_db` touch — **no SIM-07 / SIM-08 contract interaction, no Score Calibration re-baseline obligation, no simulator entry-point call**. Tests must NOT touch `simulate_scheduled_round` / `simulate_match` / `save_games`; per-Round point fields are set directly via `Match.objects.create(...)` or `match.<field>=N; match.save()`. **Out of scope (locked):** no new pure module (inline `_`-prefixed helpers only — `matches/team_schedule.py` is NOT created; no new file in `matches/sim_helpers/` or anywhere else under `matches/`); no edit to `matches/standings.py` / `matches/schedule_generator.py` / `matches/season_dashboard.py` / `matches/tasks.py` / `matches/simulation.py` / `matches/season_urls.py` / `matches/admin.py` / `matches/forms.py`; no edit to `teams/models.py` / `teams/views.py` / `teams/forms.py` / `teams/admin.py` / `teams/constants.py` / `teams/player_generator.py`; no edit to the 5 LG-01f-modified templates (`base.html` / `leagues/dashboard.html` / `seasons/dashboard.html` / `seasons/standings.html` / `seasons/schedule.html`); no new ADR (decisions are reversible — one nullable FK + a read-only view + a sidebar flip — and re-summarised in this PLAN note); no JS framework / htmx / Alpine / Stimulus (only the dropdown's inline `onchange` and the `<noscript>` Apply fallback button); no API / DRF endpoint (`/api/leagues/<id>/team_schedule/<tid>/` deferred); no `django.contrib.messages` flash; no new dependency (no `requirements.txt` edit); no backfill for pre-LG-01g Leagues (`current_team` defaults to `None`; existing test fixtures stay valid); no CAR-01 manager-role plumbing beyond the `current_team` FK (CAR-01 may rename / repoint later); no top-nav refactor (LG-01h scope); no `League.archived` toggle UI (admin-only, deferred); no simulator touch / no RNG / no Score Calibration re-baseline; no `_flush_to_db` touch / no SIM-07 / SIM-08 contract interaction; no admin change (`LeagueAdmin` / `SeasonAdmin` unchanged at LG-01g — the new `current_team` field is admin-visible by default but no custom `ModelAdmin` tweak is added). **Locked names** — URL path `/leagues/<int:league_id>/team_schedule/<int:team_id>/`; URL name `team_schedule` (bare, no `app_name`); view `matches.views.team_schedule`; helpers `matches.views._resolve_current_team_for_sidebar` / `matches.views._render_fixture_sides` / `matches.views._build_team_schedule_rows`; modified helper `matches.views._build_league_sidebar_links` (signature unchanged, body flips `schedule_team` entry); model field `matches.models.League.current_team` (FK to `teams.Team`, `null=True, blank=True, on_delete=SET_NULL, related_name="managed_in_leagues"`); reverse accessor `team.managed_in_leagues`; migration `matches/migrations/0030_league_current_team.py` (single `AddField`, deps `("matches", "0029_league_season_match_fk")` + latest `teams`); template `templates/leagues/team_schedule.html` (block title `{{ team.name }} — Schedule`, em-dash U+2014); `sidebar_active` literal `"schedule_team"` (extends LG-01f enum); session key `request.session["last_league_id"]` (int); context keys (9) `league` / `displayed_season` / `team` / `upcoming_rows` / `completed_rows` / `team_picker_options` / `sidebar_links` / `sidebar_active` / `current_team`; DOM ids (12) `team-schedule-header` / `team-schedule-team-picker-form` / `team-schedule-team-picker` / `team-schedule-team-picker-apply` / `team-schedule-upcoming-section` / `team-schedule-upcoming-list` / `team-schedule-upcoming-empty` / `team-schedule-completed-section` / `team-schedule-completed-list` / `team-schedule-completed-empty` / `team-schedule-upcoming-row-{matchday}-{round_number}` / `team-schedule-completed-row-{game_round_id}`; preserved LG-01f DOM ids `league-sidebar` + `sidebar-team-schedule_team`; locked literals `"(R)"` + `"(B)"` (Side-prefix glyphs) + `"W"` / `"L"` / `"T"` (per-Round outcome) + `"No upcoming games"` + `"No completed games"` (empty-state notices) + `"No Season in this League."` (404 body) + `{team_name} — Schedule` (page-title format, em-dash U+2014); test file `matches/tests/test_lg01g_team_schedule.py` (NEW) with classes `TestTeamScheduleRouting` / `TestTeamScheduleSeasonResolution` / `TestTeamScheduleRowGranularity` / `TestTeamScheduleSideAnnotation` / `TestTeamScheduleOutcome` / `TestTeamScheduleSorting` / `TestTeamScheduleDropdown` / `TestTeamScheduleEmptyStates` / `TestTeamScheduleContextKeys` / `TestTeamScheduleSidebarWiring` / `TestTeamScheduleSessionWrite` / `TestTeamScheduleDomIds`; extended test files `matches/tests/test_lg01_models.py` (append `TestLeagueCurrentTeamField`) + `matches/tests/test_league_create.py` (append `TestLg01gCurrentTeamAutoSet`) + `matches/tests/test_league_sidebar.py` (append `TestLg01gScheduleTeamEntryLive`). Seam contract: [`.claude/worktrees/lg-01g-seam-contract.md`](.claude/worktrees/lg-01g-seam-contract.md).

### LG-01h · Global nav restructure

Move League / Season navigation into a sidebar inside the League: when
the user is inside a League, the top nav collapses to a single
League-context surface and the per-app navigation (Standings /
Schedule / Teams / History / Play) lives in a sidebar. URL nesting
follows — `/leagues/<id>/<app>/` becomes the canonical shape, with the
flat `/seasons/<id>/...` and `/leagues/<id>/...` routes preserved as
redirects. Sandbox features (single-match create, batch sim, save
games) are hidden when the user is inside a League. Deferred from
LG-01d's scope-narrow decision (no top-nav refactor at LG-01d).

**LG-01f shipped a partial skeleton** of this restructure ([ADR-0017](docs/adr/0017-league-context-nav-shape.md)): the 14-entry zengm-shaped sidebar partial `templates/_partials/league_sidebar.html` (1 top + 6 LEAGUE + 4 TEAM + 3 PLAYERS) is now wired on 5 pages (League dashboard, League history, Season dashboard, Season standings, Season schedule) with **only 4 entries live** (Dashboard, LEAGUE > Standings, LEAGUE > Schedule, LEAGUE > History — the latter two LIVE conditional on `displayed_season` being non-`None`); and a new top-bar Bootstrap dropdown `League ▾` replaces the LG-01a `Leagues` link with **5 items, only History live** (Standings / Playoffs / Finances / Power Rankings disabled). LG-01h's remaining scope: (a) the **mode-based base.html restructure** — at the top level only a start-page link plus global Help / Tools dropdowns, with league-related entries living inside the `League ▾` dropdown, sandbox-related entries inside a Sandbox dropdown / section, and multiplayer-related entries inside a Multiplayer dropdown / section once that mode exists; (b) flip the **10 disabled sidebar entries** (LEAGUE > Playoffs, LEAGUE > Finances, LEAGUE > Power Rankings, TEAM > Roster, TEAM > Schedule, TEAM > Finances, TEAM > History, PLAYERS > Free Agents, PLAYERS > Trade, PLAYERS > Trading Block) plus the **4 disabled top-bar items** (Standings, Playoffs, Finances, Power Rankings) to LIVE as their underlying features ship — LG-02 fills Playoffs; future tasks fill the rest; (c) absorb additional nav entries beyond the 14 LG-01f shipped, captured in screenshots the user will add to the repo before LG-01h is picked up. The `LG-01h` id is stable; the partial skeleton shipped by LG-01f does not split this into LG-01h.1 / LG-01h.2 — the remaining mode-based base.html refactor + the disabled-entry activation continue under the same task id.
- completed
- note: **mode-based base.html restructure** + **19 placeholder pages behind a single shared view** + **sidebar shape expansion from 14 to 23 entries** — ships one NEW context processor `core.context_processors.app_mode(request: HttpRequest) -> dict[str, str]` (appended to the LG-01f-created `core/context_processors.py`, NOT a new file) returning `{"app_mode": "league" | "sandbox"}` via the locked path-prefix rule `request.path.startswith("/leagues/") or request.path.startswith("/seasons/")` ⇒ `"league"`, everything else (including `/`, `/teams/`, `/players/`, `/matches/`, `/maps/`, `/help/*`, `/tools/*`) ⇒ `"sandbox"` (uses `getattr(request, "path", "/")` so `RequestFactory()`-built requests without `.path` don't crash), registered in `settings.TEMPLATES[0]["OPTIONS"]["context_processors"]` **immediately after** `core.context_processors.league_nav` (locked order — `league_nav` first, `app_mode` second); `templates/base.html` is MODIFIED — the existing `<div class="navbar-nav ms-auto">` block is restructured around `{% if app_mode == "league" %}` / `{% else %}` with the LG-01a-locked outer wrapper `<div class="container">` + `<button class="navbar-toggler">` + `<div class="collapse navbar-collapse" id="mainNav">` preserved verbatim around both branches; **league-mode** renders brand → `League ▾` (LG-01f dropdown, 5 items now ALL LIVE) → `Help ▾` (NEW) → `Tools ▾` (NEW); **sandbox-mode** renders brand → the 6 LG-01a flat sandbox links (Teams / Players / Matches / Batch Sim / Create Team / Maps verbatim from LG-01a `base.html` lines 30–35) → `League ▾` → `Help ▾` → `Tools ▾`; LG-01a-locked DOM id `leagues-nav-link` on the dropdown toggle + LG-01f-locked DOM id `league-history-topbar-link` preserved verbatim in both branches; ships one NEW view `matches.views.coming_soon(request: HttpRequest, feature_key: str, league_id: int | None = None, team_id: int | None = None) -> HttpResponse` (undecorated, GET-only via `HttpResponseNotAllowed(["GET"])` as **first** line — LG-01c/d/e/f/g locked pattern) as the single shared `<h1>Coming soon</h1>` view for every placeholder URL, body in pinned order — 405 guard, `get_object_or_404(League, pk=league_id)` when `league_id is not None` (else `league = None`), `feature = _FEATURE_REGISTRY.get(feature_key)` against module-level hard-coded dict ⇒ `Http404(f"Unknown placeholder feature {feature_key!r}.")` when `None`, `request.session["last_league_id"] = league.id` when `league is not None` (extends LG-01f session-write site list), `displayed_season = league.active_season or league.seasons.filter(state="completed").order_by("-id").first()` when `league is not None` else `None`, `sidebar_links = _build_league_sidebar_links(league, displayed_season, sidebar_active)` when `league is not None` else `sidebar_links = []` (Help / Tools placeholders render with empty sidebar — sandbox-mode pages), `return render(request, "_placeholder.html", context)` with the locked **7 context keys** `league` / `displayed_season` / `feature_key` / `feature_label` (= `feature["label"]`) / `feature_section` (= `feature["section"]`) / `sidebar_links` / `sidebar_active` (= `feature["sidebar_active"]`); ships module-level constant `matches.views._FEATURE_REGISTRY: dict[str, dict[str, str | None]]` keyed on `feature_key` with value dicts `{label, section, sidebar_active}` where `section ∈ {"league", "team", "players", "stats", "help", "tools"}` — **35 entries** total (3 League-scoped `league_playoffs` / `league_finances` / `league_power_rankings` + 3 Team-scoped `team_roster` / `team_finances` / `team_history` + 6 Players-scoped `players_free_agents` / `players_trade` / `players_trading_block` / `players_prospects` / `players_watch_list` / `players_hall_of_fame` + 6 Stats-scoped `stats_game_log` / `stats_league_leaders` / `stats_player_ratings` / `stats_player_stats` / `stats_team_stats` / `stats_statistical_feats` + 6 Help `help_overview` / `help_changes` / `help_custom_rosters` / `help_debugging` / `help_lol_gm_forums` / `help_zen_gm_forums` + 4 Tools `tools_achievements` / `tools_screenshot` / `tools_debug_mode` / `tools_reset_db` + 7 already-LIVE registry entries reserved for future flips) — every Help + Tools entry has `sidebar_active=None`; ships one NEW template `templates/_placeholder.html` extending `base.html`, `{% block title %}{{ feature_label }} — Coming Soon{% endblock %}` (em-dash U+2014, locked exact format), structure when `league` is non-`None` wraps `<div class="d-flex">{% include "_partials/league_sidebar.html" %}<main>...</main></div>` else renders `<main>...</main>` directly, locked `<main>` DOM ids `coming-soon-header` (wraps `<h1>{{ feature_label }}</h1>`) / `coming-soon-section-badge` (badge rendering `{{ feature_section }}`) / `coming-soon-message` (`<p>` containing locked substring `"Coming soon"`) / `coming-soon-feature-key` (`<small>` rendering `{{ feature_key }}`), no inline JS / no `<script>` / no form / no `<button>`; ships URL routes — 2 NEW include files `core/help_urls.py` (6 paths `overview/` / `changes/` / `custom-rosters/` / `debugging/` / `lol-gm-forums/` / `zen-gm-forums/` reverse-named `coming_soon_help_overview` / `coming_soon_help_changes` / `coming_soon_help_custom_rosters` / `coming_soon_help_debugging` / `coming_soon_help_lol_gm_forums` / `coming_soon_help_zen_gm_forums`, `from matches import views` cross-app import) + `core/tools_urls.py` (4 paths `achievements/` / `screenshot/` / `debug-mode/` / `reset-db/` reverse-named `coming_soon_tools_achievements` / `coming_soon_tools_screenshot` / `coming_soon_tools_debug_mode` / `coming_soon_tools_reset_db`), both with no `app_name` (bare namespace, LG-01a–g precedent); `matches/league_urls.py` EXTENDED with **15 new path entries** inserted **AFTER** the LG-01g `<int:league_id>/team_schedule/<int:team_id>/` line and **BEFORE** the LG-01a `""` line — 3 League-scoped `<int:league_id>/playoffs/` (`coming_soon_playoffs`) / `<int:league_id>/finances/` (`coming_soon_finances`) / `<int:league_id>/power-rankings/` (`coming_soon_power_rankings`); 3 Team-scoped `<int:league_id>/team/roster/` (`coming_soon_team_roster`) / `<int:league_id>/team/finances/` (`coming_soon_team_finances`) / `<int:league_id>/team/history/` (`coming_soon_team_history`) (kwarg `team_id` reserved for LG-02+ swap to `<int:team_id>/` paths); 6 Players-scoped `<int:league_id>/players/free-agents/` (`coming_soon_free_agents`) / `<int:league_id>/players/trade/` (`coming_soon_trade`) / `<int:league_id>/players/trading-block/` (`coming_soon_trading_block`) / `<int:league_id>/players/prospects/` (`coming_soon_prospects`) / `<int:league_id>/players/watch-list/` (`coming_soon_watch_list`) / `<int:league_id>/players/hall-of-fame/` (`coming_soon_hall_of_fame`); 6 Stats-scoped `<int:league_id>/stats/game-log/` (`coming_soon_game_log`) / `<int:league_id>/stats/league-leaders/` (`coming_soon_league_leaders`) / `<int:league_id>/stats/player-ratings/` (`coming_soon_player_ratings`) / `<int:league_id>/stats/player-stats/` (`coming_soon_player_stats`) / `<int:league_id>/stats/team-stats/` (`coming_soon_team_stats`) / `<int:league_id>/stats/statistical-feats/` (`coming_soon_statistical_feats`); **LEAGUE > Standings stays LIVE via LG-01f's `season_standings` — no new `<int:league_id>/standings/` route mounted**; final `matches/league_urls.py` order is the LG-01g 5 entries followed by the 18 new League-scoped placeholders then `""`; `laserforce_simulator/urls.py` MODIFIED with 2 single-line inserts `path("help/", include("core.help_urls"))` + `path("tools/", include("core.tools_urls"))` (alphabetical placement, Code agent discretion); top-bar dropdown items — `League ▾` 5 items now ALL LIVE in locked top-to-bottom order Standings / Playoffs / Finances / History / Power Rankings with DOM ids `league-standings-topbar-link` / `league-playoffs-topbar-link` / `league-finances-topbar-link` / `league-history-topbar-link` (LG-01f preserved) / `league-power-rankings-topbar-link`, hrefs resolved via 4 NEW keys on the LG-01f `core.context_processors.league_nav` processor — `top_bar_standings_url` / `top_bar_playoffs_url` / `top_bar_finances_url` / `top_bar_power_rankings_url` all using the same 3-step session-pin → single-League → list-page chain (when no League exists, every key resolves to `reverse("league_list")`); `Help ▾` (NEW, 6 items in pinned order Overview / Changes / Custom Rosters / Debugging / LOL GM Forums / Zen GM Forums, toggle text `"Help ▾"` U+25BE, toggle DOM id `help-nav-link`, per-item DOM ids `help-overview-topbar-link` / `help-changes-topbar-link` / `help-custom-rosters-topbar-link` / `help-debugging-topbar-link` / `help-lol-gm-forums-topbar-link` / `help-zen-gm-forums-topbar-link`); `Tools ▾` (NEW, 4 items in pinned order Achievements / Screenshot / Enable Debug Mode / Reset DB, toggle text `"Tools ▾"` U+25BE, toggle DOM id `tools-nav-link`, per-item DOM ids `tools-achievements-topbar-link` / `tools-screenshot-topbar-link` / `tools-debug-mode-topbar-link` / `tools-reset-db-topbar-link`); sidebar partial `templates/_partials/league_sidebar.html` MODIFIED with 1 edit — add `{% elif section.grouper == "stats" %}<h6 class="text-muted text-uppercase small mt-3 mb-1">STATS</h6>{% endif %}` adjacent to the existing `{% elif section.grouper == "players" %}` branch (locked exact `<h6>` markup mirroring the existing 3 section headers); section iteration order from `regroup` locked `top` → `league` → `team` → `players` → `stats`; sidebar-entry DOM-id pattern `sidebar-{section}-{key}` extends automatically to 9 new entries `sidebar-players-prospects` / `sidebar-players-watch_list` / `sidebar-players-hall_of_fame` / `sidebar-stats-game_log` / `sidebar-stats-league_leaders` / `sidebar-stats-player_ratings` / `sidebar-stats-player_stats` / `sidebar-stats-team_stats` / `sidebar-stats-statistical_feats`; helper `matches.views._build_league_sidebar_links(league, displayed_season, sidebar_active) -> list[dict]` EXTENDED in-place (LG-01g precedent — same signature, body extended, **NOT** renamed to `_v2`) returning **23 dicts** in pinned order (was 14 at LG-01f/g) — index 0 top + indexes 1–6 LEAGUE (6 entries Standings / Schedule / Playoffs / Finances / History / Power Rankings, all LIVE — Standings via `season_standings`, Schedule via `season_schedule` per ADR-0017 §2 divergence-from-zengm, Playoffs / Finances / Power Rankings via their `coming_soon_*` URLs, History via `league_history`) + indexes 7–10 TEAM (4 entries Roster / Schedule (LG-01g `schedule_team` LIVE via `team_schedule`) / Finances / History, the latter 3 LIVE via `coming_soon_team_roster` / `coming_soon_team_finances` / `coming_soon_team_history`) + indexes 11–16 PLAYERS (6 entries Free Agents / Trade / Trading Block (LG-01f preserved keys, now LIVE) plus 3 NEW Prospects / Watch List / Hall of Fame) + indexes 17–22 STATS (6 entries, entire section NEW — Game Log / League Leaders / Player Ratings / Player Stats / Team Stats / Statistical Feats, all LIVE via `coming_soon_*`), each entry preserves the LG-01f 6-key dict shape `{key, label, section, url, disabled, active}`; `sidebar_active` enum extends from LG-01f's 14-value + `None` to a **23-value + `None`** enum with full locked list `"dashboard"` / `"standings"` / `"schedule"` / `"playoffs"` / `"finances"` / `"history"` / `"power_rankings"` / `"roster"` / `"schedule_team"` / `"finances_team"` / `"history_team"` / `"free_agents"` / `"trade"` / `"trading_block"` / `"prospects"` / `"watch_list"` / `"hall_of_fame"` / `"game_log"` / `"league_leaders"` / `"player_ratings"` / `"player_stats"` / `"team_stats"` / `"statistical_feats"` / `None`, key-collision rule from LG-01g `_team` suffix precedent preserved (LEAGUE > Schedule keeps `"schedule"`, TEAM > Schedule uses `"schedule_team"`, TEAM > Finances uses `"finances_team"`, TEAM > History uses `"history_team"`); **page wiring is zero-edit** — every page that already renders the LG-01f sidebar partial (League dashboard, League history, Season dashboard, Season standings, Season schedule, Team Schedule from LG-01g) automatically picks up the 23-entry shape via the modified helper; **confirmed zero-edit templates**: `templates/leagues/dashboard.html`, `templates/leagues/history.html`, `templates/leagues/team_schedule.html` (LG-01g), `templates/seasons/dashboard.html`, `templates/seasons/standings.html`, `templates/seasons/schedule.html`; **confirmed edited templates**: `templates/base.html` (mode branching + Help / Tools dropdowns) + `templates/_partials/league_sidebar.html` (STATS section header) + `templates/_placeholder.html` (NEW); tests live in **3 NEW files + 6 EXTENDED files** under `matches/tests/` — `test_lg01h_app_mode_processor.py` (NEW, Django `TestCase`, class `TestAppModeContextProcessor` covering `/` / `/teams/` / `/players/` / `/matches/` / `/maps/` / `/help/overview/` / `/tools/achievements/` ⇒ sandbox, `/leagues/` / `/leagues/1/` / `/leagues/1/history/` / `/seasons/1/` / `/seasons/1/standings/` ⇒ league, empty `request.path` + missing `.path` edge cases) + `test_lg01h_coming_soon.py` (NEW, classes `TestComingSoonRouting` covering 200 happy / 405 POST / 404 stale league_id / 404 unknown feature_key / sidebar rendered in league branch / sidebar empty in Help-Tools branch / `feature_label` in `<h1>` / `coming-soon-message` substring `"Coming soon"` / 7 context keys / `app_mode` matches URL-prefix rule + `TestComingSoonFeatureRegistry` asserting all 35 entries + 3 value-dict keys + every `sidebar_active` in the 23+`None` enum + Help / Tools entries `sidebar_active=None` + `TestComingSoonSessionWrite` writes `last_league_id` on league-scoped placeholders but NOT on Help / Tools) + `test_lg01h_base_html_branching.py` (NEW, `client.get("/")` ⇒ sandbox markup + Help / Tools dropdowns, `client.get("/teams/")` ⇒ sandbox links + Help / Tools dropdowns, `client.get(f"/leagues/{league.id}/")` ⇒ flat sandbox links ABSENT + Help / Tools dropdowns rendered + `League ▾` with all 5 items LIVE); EXTENDED — `test_league_sidebar.py` (length 14 → 23, section counts `top=1, league=6, team=4, players=6, stats=6`, NEW `TestLg01hStatsSection` / `TestLg01hPlayersExpansion` / `TestLg01hDisabledFlipsLive`) + `test_league_nav_context_processor.py` (4 new keys `top_bar_standings_url` / `top_bar_playoffs_url` / `top_bar_finances_url` / `top_bar_power_rankings_url` resolving via the 3-step chain) + `test_league_history.py` / `test_league_dashboard.py` / `test_season_dashboard_view.py` / `views_tests.py` (every `len(sidebar_links) == 14` updated to `== 23`, 14-entry enumerations updated to 23-entry list, section counts 4 → 5); tests must NOT touch `simulate_scheduled_round` / `simulate_match` / `save_games`; **scope-out (locked)**: no model change, no migration (LG-01g `0030_league_current_team.py` remains the latest LG-01x migration), no simulator touch, no RNG, no `_flush_to_db` touch, no SIM-07 / SIM-08 contract interaction, no Score Calibration re-baseline obligation, no new ADR (ADR-0017 gets a Consequences-extension note here at code time — `docs/adr/0017-league-context-nav-shape.md` Consequences section appended), no CONTEXT.md edit (every term — sandbox / league / placeholder / mode / dropdown / sidebar / app_mode — is implementation language, not domain; existing `League` / `Season` / `Standings` / `Matchday` / `Job` / `Team schedule` / `Current team` glossary entries cover the domain), no real implementation of the 19 placeholder pages (each renders `<h1>Coming soon</h1>` via the shared template), no JS framework / htmx / Alpine / Stimulus / new inline `<script>` blocks (Bootstrap 5 dropdown JS is the only existing dep), no new dependency, no API / DRF endpoint, no `django.contrib.messages` flash, no admin change, no backfill, no `<int:team_id>/` placeholder routes at LG-01h (Team-scoped placeholders are `<int:league_id>/`-keyed; LG-02+ may convert), no mode-toggle UI (mode is path-driven only), no multiplayer mode (deferred per ADR-0017 §1), no edit to `matches/standings.py` / `matches/schedule_generator.py` / `matches/season_dashboard.py` / `matches/tasks.py` / `matches/simulation/` / `matches/season_urls.py` / `matches/forms.py` / `teams/models.py` / `teams/views.py` / `teams/forms.py` / `teams/admin.py` / `teams/constants.py` / `teams/player_generator.py`, no edit to existing dashboard / standings / schedule / history / team_schedule templates (consume the modified partial + helper unchanged). Seam contract: [`.claude/worktrees/lg-01h-seam-contract.md`](.claude/worktrees/lg-01h-seam-contract.md).

### LG-01j · Per-Season arena map options

Per-Season arena map configuration: **two modes** — (a) a single
map used for every Round of the Season, or (b) a random map drawn
per-Round from an enrolled pool (the tournament-style randomiser).
At LG-01d the Play actions pass `arena_map=None` to
`simulate_scheduled_round` so every Round runs on the 3-zone
fallback. LG-01j adds the per-Season map configuration UI on the
Season dashboard, threads the resolved `arena_map` through the
`play_season_task` Celery signature, and resolves the per-fixture
map inside the task body. **Mode (c) — per-sub-league rotating
map pools — is removed from LG-01j's scope** and re-sequenced to
[SUB-01](#sub-01--sub-leagues--per-sub-league-rotating-map-pools)
(2026-05-28) because no `SubLeague` model exists yet; that task
introduces sub-leagues as a real domain concept and the pool
rotation builds on top.
- completed
- note: **per-Season arena map configuration** — adds two locked-at-create-League-time ship modes `single` (one fixed `ArenaMap` for every Round) + `random_per_round` (deterministic per-Round draw from a pool, seeded by fixture identity) alongside the preserved `none` default (3-zone fallback — the LG-01d behaviour); the third "per-sub-league rotation" mode stays deferred to **SUB-01 post-CAR-03** (no third enum value reserved at LG-01j); ships ONE migration `laserforce_simulator/matches/migrations/0031_season_map_mode_pool.py` adding 3 fields to `Season` — `map_mode: CharField(max_length=32, choices=[("none", "3-zone fallback"), ("single", "Single map"), ("random_per_round", "Random per Round")], default="none")` + `map_pool: ManyToManyField("core.ArenaMap", blank=True, related_name="seasons_using_pool")` + `starting_map_pool_ids_json: JSONField(null=True, blank=True, default=None)` (mirrors the LG-01 `starting_team_ids_json` snapshot precedent — sorted ascending by `ArenaMap.id` for determinism; `None` pre-activation, `[]` after activation with no maps, `[id1, id2, …]` after activation with maps); `Season.clean()` extends with a defensive `map_mode ∈ {none, single, random_per_round}` enum check raising `ValidationError({"map_mode": "Unknown map mode."})`, M2M pool-count rules live form-side only (M2M rows aren't visible to `Model.clean()` pre-`save`); `Season.start_season()` extension PRESERVES the existing `@transaction.atomic` decorator + draft→active flip + `<2 teams` guard + `starting_team_ids_json` snapshot + return shape and appends one line `self.starting_map_pool_ids_json = sorted([m.id for m in self.map_pool.all()])` before the existing `self.save()` (snapshot inside the atomic block, sorted-ascending for re-activation determinism, empty pool ⇒ `[]` NOT `None`); `CreateLeagueForm` (LG-01b) gains 2 NEW fields at the END of the field block in pinned order — `map_mode = forms.ChoiceField(choices=Season._meta.get_field("map_mode").choices, initial="none", required=True, label="Map mode")` (single-source-of-truth choices from the model field) + `map_pool = forms.ModelMultipleChoiceField(queryset=_maps_with_confirmed_config(), required=False, label="Map pool")` (REUSES the existing `matches.forms._maps_with_confirmed_config()` helper verbatim — only `ArenaMap` rows with at least one confirmed `MapZoneConfig` are pickable; half-built maps excluded), final form field order `league_name → season_name → start_date → num_teams → schedule_format → mean → std_dev → map_mode → map_pool` (9 fields total); form `clean()` extension PRESERVES the existing body and appends 3 mode-vs-pool rules raising `ValidationError({"map_pool": "…"})` byte-equal to the seam contract — `mode=="none" and len(pool)>0` ⇒ `"Map pool must be empty when Map mode is '3-zone fallback'."`, `mode=="single" and len(pool)!=1` ⇒ `"Map pool must contain exactly 1 map when Map mode is 'Single map'."`, `mode=="random_per_round" and len(pool)<1` ⇒ `"Map pool must contain at least 1 map when Map mode is 'Random per Round'."` (errors attach to `map_pool` for co-located help text); `league_create` view EXTENDED in-place — `Season.objects.create(...)` also passes `map_mode=cleaned["map_mode"]` (or assigns post-create) and after the existing `season.teams.add(*created_teams)` line appends `season.map_pool.set(cleaned["map_pool"])` inside the same `@transaction.atomic` block; LG-01e `next_season` view EXTENDED in-place — inside the existing `@transaction.atomic` body after the existing `new_season.teams.add(*teams_qs)` line, carries forward `new_season.map_mode = latest_completed.map_mode` and rehydrates the pool from the FROZEN snapshot `pool_ids = latest_completed.starting_map_pool_ids_json or []` then `new_season.map_pool.set(ArenaMap.objects.filter(id__in=pool_ids))` (deleted maps drop silently via `filter`; the live `latest_completed.map_pool` is deliberately ignored — the snapshot is what the Season ACTUALLY played with); NEW module-level helper `matches.tasks._resolve_fixture_map(season, fixture, pool_by_id) -> ArenaMap | None` (no class, pure — consumes only `season.id` / `season.map_mode` / `season.starting_map_pool_ids_json` + a `ScheduleFixture`-shaped object + a `dict[int, ArenaMap]`, NO ORM access inside the helper) with a 4-branch body — `mode=="none"` ⇒ `None`; `mode=="single"` ⇒ `pool_by_id.get(starting_map_pool_ids_json[0])` (defensive `None` on empty snapshot or admin-deleted row); `mode=="random_per_round"` ⇒ `rng = random.Random(f"{season.id}|{fixture.matchday}|{fixture.round_number}|{fixture.team_a_id}|{fixture.team_b_id}")` then `chosen_id = rng.choice(pool_ids)` then `pool_by_id.get(chosen_id)` (fresh `Random` per fixture so map choice doesn't share state with the simulator's RNG and is replay-faithful per fixture identity); unknown `mode` ⇒ `raise ValueError(f"Unknown map_mode: {mode!r}")`; seed-string format is byte-locked `f"{season.id}|{fixture.matchday}|{fixture.round_number}|{fixture.team_a_id}|{fixture.team_b_id}"` (5 components, pipe `|`-separated, no spaces, exact order); helper lives in `matches/tasks.py` NOT `matches/season_dashboard.py` (the latter's frozen no-Django import allowlist from LG-01h is preserved); `play_season_task` (async Celery) + `play_week` (synchronous in-request) BOTH extended in-place to add a deferred `from core.models import ArenaMap` import, resolve the pool ONCE outside the per-fixture loop via `pool_ids = season.starting_map_pool_ids_json or []` + `pool_by_id: dict[int, ArenaMap] = ArenaMap.objects.in_bulk(pool_ids)` (single ORM query regardless of fixture count), then inside the loop call `arena_map = _resolve_fixture_map(season, fixture, pool_by_id)` and pass through to `BatchSimulator().simulate_scheduled_round(..., arena_map=arena_map)` — the simulator's `arena_map=None` default per SIM-09 means NO simulator edit is needed and the existing `MatchJob` progress writes / exception handling / `@shared_task` decorator are UNCHANGED; LG-01c `_build_dashboard_context` grows from 11 to 12 context keys via ONE new key `map_config_label: str` rendered via a 4-branch `if/elif/else` ladder producing 5 locked label strings — `"Map: 3-zone fallback (no map)"` (when `displayed_season is None` OR `season_mode == "none"` OR `displayed_season.map_mode == "none"`), `f"Map: Single — {map_obj.name}"` (em-dash U+2014, single SPACE on both sides), `"Map: Single — (map deleted)"` (admin-deleted snapshot row), `f"Map: Random per Round ({len(names)} maps: {', '.join(names)})"` (alphabetical by `ArenaMap.name`), `"Map: Random per Round (no maps)"`; templates EDIT-ONLY (no new template) — `templates/leagues/dashboard.html` + `templates/seasons/dashboard.html` render `{{ map_config_label }}` inside NEW DOM ids `league-dashboard-map-config` / `season-dashboard-map-config` placed IMMEDIATELY UNDER the respective `*-dashboard-action-button` and IMMEDIATELY ABOVE the respective `*-dashboard-standings-snippet`; `templates/leagues/create.html` renders 2 new field rows for `{{ form.map_mode }}` + `{{ form.map_pool }}` with NEW DOM ids `league-create-map-mode` / `league-create-map-pool` AFTER the existing `team_stat_std_dev` row and BEFORE the submit button; `SeasonAdmin.filter_horizontal` extends from `("teams",)` to `("teams", "map_pool")` (the ONLY admin change — `map_mode` surfaces via the default model-form render with no `SeasonAdmin` edit); CONTEXT.md `### League and seasons` section gains 3 NEW glossary entries appended after the existing `Team schedule` entry — `Map mode` / `Map pool` / `Per-fixture map resolution` (no new section); pre-LG-01j Seasons take the `map_mode="none"` default + empty M2M + `None` snapshot at migration time which yields 3-zone fallback at play time — the LG-01d behaviour preserved with no data migration; mid-League map-config edits are admin-only via Django admin (no edit URL / edit view / edit form / edit template ships); the change folds into the existing pending post-MOVE-01 Score Calibration re-baseline alongside MOVE-02 / MOVE-03 / MOVE-04 / SIM-09 (NO new Score Calibration re-baseline obligation triggered by LG-01j alone — no simulator mechanics changed, the `arena_map=` kwarg flow already exists per SIM-09); **no new ADR** ships at LG-01j (the decisions are reversible — model fields + a deterministic helper, with the existing CONTEXT.md domain language extension covering the vocabulary). Seam contract: [`.claude/worktrees/lg-01j-seam-contract.md`](.claude/worktrees/lg-01j-seam-contract.md).

### LG-01k · top nav bar behaviour fix

when inside of a league the top nav bar should match the side nav bar, otherwise it should be nearly blank if not in sandbox
(from start screen it should be only) help and tools, once they go to sandbox it should be:
teams, players, matches, batch sime, create team, maps, help, and tools
- completed
- note: **three-mode topnav restructure** — extends the LG-01h `core.context_processors.app_mode(request: HttpRequest) -> dict[str, str]` enum from 2 values (`"league"` / `"sandbox"`) to **3 values** (`"start"` / `"league"` / `"sandbox"`) via the locked 3-way path-prefix rule applied in this exact order so `/` does NOT fall into sandbox — (1) `path == "/"` (exact match) ⇒ `"start"`, (2) `path.startswith("/leagues/") or path.startswith("/seasons/")` ⇒ `"league"`, (3) everything else (including empty string `""`, missing `.path` attribute, `/teams/`, `/players/`, `/matches/`, `/maps/`, `/help/*`, `/tools/*`, any unknown path) ⇒ `"sandbox"`; the defensive read distinguishes "missing attribute" from "explicit `/`" via `path = getattr(request, "path", None)` — `None` and `""` both fall through to the sandbox return, only an explicit `path == "/"` string trips the start return; signature unchanged, return-key unchanged, return-value-type still `dict[str, str]` (all 3 enum values are strings); rewrites `core.context_processors.league_nav(request: HttpRequest) -> dict[str, Any]` (return-type annotation widens from `dict[str, str]` (LG-01h) to `dict[str, Any]` because `top_bar_links` is a `list[dict]`, not a `str`) — the **5 LG-01h URL keys** `top_bar_history_url` / `top_bar_standings_url` / `top_bar_playoffs_url` / `top_bar_finances_url` / `top_bar_power_rankings_url` are DELETED from the return dict (zero callers remain after the `base.html` rewrite), and the processor now returns exactly **2 keys** — `top_bar_links: list[dict]` (the 23-entry output of `matches.views._build_league_sidebar_links(league, displayed_season, sidebar_active=None)` for the resolved League + displayed Season, or `[]` when no League can be resolved; `sidebar_active=None` is locked since active-styling belongs to the sidebar partial not the topnav) + `top_bar_dashboard_url: str` (`reverse("league_dashboard", kwargs={"league_id": league.id})` for the resolved League, or `reverse("league_list")` when no League can be resolved); the **3-step League resolution chain** (session-pin `last_league_id` → single-League via bounded `[:2]` probe → fallback) + the **displayed-Season resolution** (`league.active_season` → most-recent completed via `seasons.filter(state="completed").order_by("-id").first()` → `None`) + the **defensive DB-error handling** (every ORM call wrapped `try: ... except DatabaseError:` logging at DEBUG, broken-transaction renders falling through to the empty-list + list-page fallback) are all identical to LG-01h; a new lazy local-import `from matches.views import _build_league_sidebar_links` joins the existing `from matches.models import League` inside the function body (not at module scope, to preserve the LG-01f apps-loading-cycle guard); rewrites `templates/base.html`'s `<div class="navbar-nav ms-auto">` block around a 3-way `{% if app_mode == "league" %}` / `{% elif app_mode == "sandbox" %}` / `{% else %}` branch where the `{% else %}` arm is the start-mode (path == `/`) minimum-viable layout — placing it as `{% else %}` minimises visual complexity of the most-frequently-loaded path; the brand link `<a class="navbar-brand" href="{% url 'landing' %}">⚡ Laserforce Manager</a>` + the LG-01a-locked outer wrapper `<div class="container">` + `<button class="navbar-toggler">` + `<div class="collapse navbar-collapse" id="mainNav">` are preserved verbatim around all 3 branches; **league-mode block (7 elements)** in pinned left-to-right order `[⌂ home icon] | League ▾ | Team ▾ | Players ▾ | Stats ▾ | Tools ▾ | Help ▾` — (1) Dashboard home-icon link `<a class="nav-link" id="dashboard-nav-link" href="{{ top_bar_dashboard_url }}" aria-label="League dashboard">⌂</a>` where the home-icon text content is the literal character `⌂` (U+2302 HOUSE — no Bootstrap Icons CDN, no `<i>` element, no SVG, no 🏠 emoji), (2) `League ▾` dropdown toggle id `league-nav-link` (the `s` is dropped from the LG-01h `leagues-nav-link` id — LG-01k uses singular `league-nav-link` to match the `section="league"` vocabulary of `top_bar_links`) containing the LEAGUE section (6 entries: Standings / Schedule / Playoffs / Finances / History / Power Rankings), (3) `Team ▾` id `team-nav-link` containing the TEAM section (4 entries: Roster / Schedule / Finances / History), (4) `Players ▾` id `players-nav-link` containing the PLAYERS section (6 entries: Free Agents / Trade / Trading Block / Prospects / Watch List / Hall of Fame), (5) `Stats ▾` id `stats-nav-link` containing the STATS section (6 entries: Game Log / League Leaders / Player Ratings / Player Stats / Team Stats / Statistical Feats), (6) `Tools ▾` preserved verbatim from LG-01h (4 items, ids `tools-nav-link` + `tools-{achievements,screenshot,debug-mode,reset-db}-topbar-link`), (7) `Help ▾` preserved verbatim from LG-01h (6 items, ids `help-nav-link` + `help-{overview,changes,custom-rosters,debugging,lol-gm-forums,zen-gm-forums}-topbar-link`); **sandbox-mode block (8 elements)** in pinned order `Teams | Players | Matches | Batch Sim | Create Team | Maps | Tools ▾ | Help ▾` — the 6 LG-01a flat anchors preserved verbatim (Teams `team_list` / Players `player_list` with LG-01a-locked DOM id `player-list-nav-link` preserved / Matches `match_list` / Batch Sim `simulate_batch` / Create Team `team_create` / Maps `map_list`, anchors 1, 3, 4, 5, 6 carry no DOM id matching LG-01a) followed by the universal Tools ▾ + Help ▾ dropdowns — **delta from LG-01h: the LG-01h sandbox-branch `League ▾` dropdown is REMOVED from sandbox mode entirely** (a user in sandbox mode is not browsing a League, so the League menu surface is irrelevant); **start-mode block (2 elements)** in pinned order `Tools ▾ | Help ▾` and nothing else — no `League ▾`, no Dashboard icon, no flat sandbox links, no `player-list-nav-link` — the start page (`/`) presents the minimum-viable topnav and the user picks a mode card (per LG-01a `mode-card-sandbox` / `mode-card-league` / `mode-card-multiplayer`) to path-flip into a populated mode; **order delta from LG-01h applies in all 3 modes: Tools is now BEFORE Help** (LG-01h had Help-then-Tools; LG-01k swaps to Tools-then-Help); the Tools ▾ + Help ▾ markup is identical across all 3 modes — the Code agent MAY (locked optional) factor the ~14 lines of duplication into a small `{% include "_partials/topnav_tools_help.html" %}` partial (path locked) included at the end of each branch, OR inline the markup 3× (test plan asserts on DOM ids, not on inclusion structure); **section-dropdown iteration pattern** (applies to all 4 league-mode section dropdowns) uses `{% regroup top_bar_links by section as sections %}` at the start of the league branch followed by per-section rendering via `section.grouper` filtering, with per-entry branching on `entry.disabled` — `{% if entry.disabled %}<li><span class="dropdown-item disabled">{{ entry.label }}</span></li>{% else %}<li><a class="dropdown-item" id="topbar-{{ entry.section }}-{{ entry.key }}" href="{{ entry.url }}">{{ entry.label }}</a></li>{% endif %}` — and the locked **`topbar-{section}-{key}` DOM-id pattern** (mirrors LG-01f `sidebar-{section}-{key}`) produces the 22 league-mode dropdown-entry ids `topbar-league-standings` / `topbar-league-schedule` / `topbar-league-playoffs` / `topbar-league-finances` / `topbar-league-history` / `topbar-league-power_rankings` / `topbar-team-roster` / `topbar-team-schedule_team` / `topbar-team-finances_team` / `topbar-team-history_team` / `topbar-players-free_agents` / `topbar-players-trade` / `topbar-players-trading_block` / `topbar-players-prospects` / `topbar-players-watch_list` / `topbar-players-hall_of_fame` / `topbar-stats-game_log` / `topbar-stats-league_leaders` / `topbar-stats-player_ratings` / `topbar-stats-player_stats` / `topbar-stats-team_stats` / `topbar-stats-statistical_feats` (note the `power_rankings` / `schedule_team` / `finances_team` / `history_team` / `free_agents` / `trading_block` / `watch_list` / `hall_of_fame` / `game_log` / `league_leaders` / `player_ratings` / `player_stats` / `team_stats` / `statistical_feats` underscore-not-hyphen forms — they match the helper's `key=` values); **the top Dashboard entry (`section="top", key="dashboard"`) of `top_bar_links` is filtered OUT of the regrouped iteration** — it surfaces only via the leading `dashboard-nav-link` icon, not in any dropdown, so no `topbar-top-dashboard` DOM id is emitted; disabled entries render as `<span class="dropdown-item disabled">` with NO DOM id (the `topbar-{section}-{key}` id is only emitted on LIVE `<a>` elements — tests must not assert on disabled-entry ids); **single-source-of-truth observation**: `matches.views._build_league_sidebar_links` becomes the sole producer of the per-section entry list consumed by BOTH the LG-01f sidebar partial AND the LG-01k league-mode topbar — flipping a disabled→LIVE in the helper (e.g. LG-02 lighting up Playoffs) updates both surfaces at once with zero per-surface edit; LG-01k does NOT edit the helper itself, it is read-only consumed; **6 retired LG-01h DOM ids** — `leagues-nav-link` (replaced by `league-nav-link`) / `league-standings-topbar-link` (replaced by `topbar-league-standings`) / `league-playoffs-topbar-link` (replaced by `topbar-league-playoffs`) / `league-finances-topbar-link` (replaced by `topbar-league-finances`) / `league-history-topbar-link` (replaced by `topbar-league-history`) / `league-power-rankings-topbar-link` (replaced by `topbar-league-power_rankings` — underscore not hyphen); **5 retired LG-01h context keys** — `top_bar_history_url` / `top_bar_standings_url` / `top_bar_playoffs_url` / `top_bar_finances_url` / `top_bar_power_rankings_url`; tests live in **1 NEW file + 3 EXTENDED files** under `matches/tests/` — `test_lg01k_base_html_branching.py` (NEW, Django `TestCase`, classes `TestLg01kStartModeTopbar` / `TestLg01kSandboxModeTopbar` / `TestLg01kLeagueModeTopbar` covering per-mode DOM-id presence/absence, the `⌂` U+2302 character inside the `dashboard-nav-link` anchor body, the Tools-before-Help source-order check, the `topbar-top-dashboard` ABSENT assertion, the 6 retired-id ABSENT assertions, and at least one `topbar-{section}-{key}` id per section); EXTENDED `test_lg01h_app_mode_processor.py` (existing `TestAppModeContextProcessor` gains 3 new methods for `"/"` ⇒ `"start"`, `""` ⇒ `"sandbox"`, missing-`.path` ⇒ `"sandbox"`); EXTENDED `test_league_nav_context_processor.py` (the 5 LG-01h test methods on retired URL keys are DELETED and replaced with new methods covering the 2-key return shape — `top_bar_links` length 23 with League / `[]` on fallback, `top_bar_dashboard_url` resolves to `league_dashboard` / `league_list` fallback, retired keys ABSENT, helper called with `sidebar_active=None` via monkeypatched recording, displayed-Season chain works, `displayed_season is None` keeps 23 entries but Standings/Schedule are `url=None, disabled=True`, top entry `[0]` has `section="top", key="dashboard"` present in processor output — the TEMPLATE filters it from the regrouped iteration, not the processor); MINIMAL EDIT `test_lg01h_base_html_branching.py` (assertions referencing retired ids and retired URL context keys are deleted or updated; Tools / Help DOM-id assertions stay verbatim; LG-01h file NOT replaced wholesale — `test_lg01k_base_html_branching.py` is the new authority for topbar DOM assertions); tests must NOT touch `simulate_scheduled_round` / `simulate_match` / `save_games`; **scope-out (locked)**: no model change, no migration, no simulator touch, no RNG, no `_flush_to_db` touch, no SIM-07 / SIM-08 contract interaction, no Score Calibration re-baseline (LG-01k is a UI restructure — no simulation mechanics change), no new ADR (ADR-0017 is unchanged; LG-01k modification is at the implementation layer, the LG-01h architectural decision still stands), no CONTEXT.md edit (`start` / `sandbox` / `league` are implementation enum values for topnav rendering, not domain language), no new dependency, no API / DRF endpoint, no `django.contrib.messages` flash, no admin change, no JS framework / htmx / Alpine / Stimulus / inline `<script>` blocks (Bootstrap 5 dropdown JS already in `base.html` is the only existing dep), no new template tag library, no new Django context processor beyond the existing 2, no edit to `templates/_partials/league_sidebar.html`, no edit to `matches.views._build_league_sidebar_links` (read-only consumed by both sidebar and topbar), no edit to any view function / URL include file / `core/views.py` / `matches/views.py` / `settings.py` (the `TEMPLATES` context-processor registration list is unchanged — only the existing 2 entries are reused), no edit to the LG-01h `coming_soon` view / `_FEATURE_REGISTRY` / `templates/_placeholder.html`, no edit to the LG-01a `landing` view / `templates/core/landing.html`, no mode-toggle UI (mode is path-driven only, per LG-01h precedent), no multiplayer mode (deferred per ADR-0017 §1), no new placeholder views or `coming_soon_*` URL names (LG-01k strictly reuses the LG-01h URL names), no backfill; locked names — context processor function names `core.context_processors.app_mode` (signature unchanged, body extended 2→3 branches) + `core.context_processors.league_nav` (signature unchanged, body rewritten, return-type annotation widens to `dict[str, Any]`); NEW context keys `top_bar_links` + `top_bar_dashboard_url`; RETIRED context keys (5 listed above); `app_mode` 3-value enum `"start"` / `"league"` / `"sandbox"`; helper consumed read-only `matches.views._build_league_sidebar_links(league, displayed_season, sidebar_active=None)`; files modified `laserforce_simulator/core/context_processors.py` + `laserforce_simulator/templates/base.html`; files new (test) `laserforce_simulator/matches/tests/test_lg01k_base_html_branching.py`; files extended (test) `laserforce_simulator/matches/tests/test_lg01h_app_mode_processor.py` + `test_league_nav_context_processor.py` + `test_lg01h_base_html_branching.py` (minimal-edit); file new (optional partial, Code agent's discretion) `laserforce_simulator/templates/_partials/topnav_tools_help.html`; DOM ids NEW (league mode only) `dashboard-nav-link` + `league-nav-link` (replaces retired `leagues-nav-link`) + `team-nav-link` + `players-nav-link` + `stats-nav-link` + the 22 `topbar-{section}-{key}` ids; DOM ids PRESERVED from LG-01h `tools-nav-link` + 4 Tools child ids + `help-nav-link` + 6 Help child ids; DOM id PRESERVED from LG-01a `player-list-nav-link` (sandbox mode only); DOM ids RETIRED (6 listed above); toggle text literals `League ▾` / `Team ▾` / `Players ▾` / `Stats ▾` / `Tools ▾` / `Help ▾` (all trailing U+25BE) plus home-icon text content `⌂` (U+2302 HOUSE); DOM-id pattern locked `topbar-{section}-{key}` (mirrors LG-01f `sidebar-{section}-{key}`); test classes `TestLg01kStartModeTopbar` / `TestLg01kSandboxModeTopbar` / `TestLg01kLeagueModeTopbar` (new file) plus extensions to `TestAppModeContextProcessor` and `TestLeagueNavContextProcessor`. Seam contract: [`.claude/worktrees/lg-01k-seam-contract.md`](.claude/worktrees/lg-01k-seam-contract.md).

### LG-01z · Sidebar placeholder backlog (sub-plan)

After LG-01h landed, the 23-entry league sidebar
(`templates/_partials/league_sidebar.html` /
`_build_league_sidebar_links` in `matches/views.py`) carries 19 disabled
"coming soon" placeholders. **Playoffs** is covered by LG-02 below; the
remaining 17 do not yet have an LG-XX feature in this document
(`PLAN.md` lines 660-666 explicitly acknowledged this gap during the
LG-01 grilling session).

The full per-placeholder backlog — one entry per missing surface (LEAGUE
> Finances / Power Rankings; TEAM > Roster / Finances / History; PLAYERS
> Free Agents / Trade / Trading Block / Prospects / Watch List / Hall of
Fame; STATS > Game Log / League Leaders / Player Ratings / Player Stats
/ Team Stats / Statistical Feats) — lives in
[`sub-plan.md`](sub-plan.md) under IDs **LG-01z-a..q**. Each sub-entry is
a scope sketch (placeholder URL it replaces, minimum-viable
implementation, dependency on other PLAN tasks). Each one will go
through its own grilling session before implementation.
- completed: 11 of the 17 placeholders shipped as real read-only screens in
  one parallel batch — LG-01z-b Power Rankings (sortable), -c Team Roster,
  -e Team History (3-tab), -f Free Agents, -j Watch List (session-scoped),
  -l Game Log, -m League Leaders, -n Player Ratings, -o Player Stats,
  -p Team Stats, -q Statistical Feats. Each owns an isolated view in the new
  `matches/league_screens/` package + a pure-logic module where aggregation
  is non-trivial (`power_rankings_logic`, `league_leaders_logic`,
  `season_player_stats`, `team_stats_logic`, `team_history_logic`,
  `stat_feats`) + a `templates/leagues/<screen>.html` template + a
  `test_lg01z_<screen>.py` test file. Central wiring repoints each entry in
  `_build_league_sidebar_links` (single source of truth for both the LG-01f
  sidebar and the LG-01k topbar) from its `coming_soon_*` placeholder to the
  live URL, adds the route to `matches/league_urls.py`, and trims
  `_FEATURE_REGISTRY` to the 7 still-blocked placeholders. The remaining 6
  (LG-01z-a Finances, -d Team Finances, -g Trade, -h Trading Block,
  -i Prospects, -k Hall of Fame) stay on `coming_soon` but now render an
  explainer page with a `blocker` note naming the unbuilt dependency (salary/
  contract model, LG-05 potential, LG-03/04 awards). Read-only throughout —
  no model change, no migration, no simulator touch. Per-screen status +
  blockers tracked in [`sub-plan.md`](sub-plan.md); seam contract at
  `.claude/worktrees/lg-01z-seam-contract.md`.

### LG-02 · Tournament formats — Part 1 (sandbox standalone tournaments)

**Status: DONE — all Part-1 sandbox formats shipped.** Single-elimination, bulk
intake + async play-all, best-of-N Series, per-round Series escalation,
double-elimination / round-robin / RR→DE / Swiss, and the Random Draw player
pool. The deferred LG-02x-2 (Duos / Trios) slice stays in [`PLAN.md`](PLAN.md).
See [ADR-0019](docs/adr/0019-tournament-bracket-model.md) for the persisted
standalone-sandbox model decision; the LG-02 grill (2026-06-02) split the work
into Part 1 (sandbox) and Part 2 (in-League composer).

#### Part 1 · Sandbox standalone tournaments

- **LG-02a · [DONE] Sandbox single-elimination Tournament.** A standalone,
  persisted single-elimination bracket built and played entirely in the sandbox,
  decoupled from League/Season. Single-elimination only; arbitrary **N ≥ 4** with
  byes; a bracket node is exactly one 2-round `Match`; winners auto-advance; the
  bracket renders as a visual tree on the detail page.
  - completed: shipped the **sandbox single-elimination Tournament** at the new
    `/tournaments/` mount (cite [ADR-0019](docs/adr/0019-tournament-bracket-model.md);
    seam [`.claude/worktrees/lg-02a-seam-contract.md`](.claude/worktrees/lg-02a-seam-contract.md);
    CONTEXT.md `### Tournaments` carries the 8 locked terms). **Standalone &
    persisted** — three new models in `matches/models.py` (`Tournament` /
    `TournamentParticipant` / `BracketNode`, migration
    `matches/migrations/0033_tournament.py`, new models only — no `RunPython` /
    backfill, ADR-0004 precedent), `season`-less and never touching
    `generate_schedule`. **Single-elimination only** (`format` enum present but
    single-valued `"single_elimination"`, extensible). `Tournament` runs a 3-state
    machine `setup` → `active` → `completed`: `setup` is the **Seeding-editable**
    window, the `BracketNode` tree is built + persisted + locked only on the
    `setup` → `active` transition (`lock_and_build()`, `@transaction.atomic`,
    `ValidationError` on N < 4), the final node resolving stamps `champion` +
    `completed` (mirrors `Season.start_season`'s draft→active M2M lock).
    **Node = one Match** (a `BracketNode` holds two team slots + an optional played
    2-round `Match`; no series). **Tie-break** when `Match.winner is None`
    (rounds + total points tied): best single-`GameRound` score advances, else the
    **higher Bracket seed (lower seed int)** — pure integer compare, no re-sim.
    **Arbitrary N ≥ 4 with byes**: bracket size = next power of two ≥ N, the top
    `(size − N)` seeds get round-1 byes. **Seeding** = mean active-player
    `overall_rating` **DESC** default (the LG-01c draft-preview talent order) +
    manual reorder (`tournament_reseed`, rejected once locked). **Team source** =
    select existing `Team.objects.regular()` **and/or** generate new via the LG-01b
    cross-app `teams.views._generate_teams` seam (signature unchanged). Play is
    **synchronous game-by-game** — one `tournament_play_next` POST sims exactly one
    node's Match via `BatchSimulator().simulate_match(..., match_type="tournament")`
    and Advances the winner. Pure bracket math lives in `matches/bracket.py`
    (frozen allowlist `dataclasses`/`typing`/`math`/`collections`, no Django —
    `TestNoDjangoImportsLeaked`); the view↔pure seam crosses **ints/dicts only**
    (`_node_to_dict` flattener). Six views/URLs (`tournament_list` / `_create` /
    `_detail` / `_reseed` / `_lock` / `_play_next`) under
    `path("tournaments/", include("matches.tournament_urls"))`; a **bracket-tree
    viz** on `tournament_detail` (DOM ids `tournament-bracket` /
    `tournament-bracket-round-{n}` / `tournament-node-{round}-{position}`); a
    sandbox-nav entry `tournaments-nav-link` in the `app_mode == "sandbox"` topnav
    branch; admin for all three models. Tests: `matches/tests/test_bracket.py`
    (pure-unit), `test_tournament_models.py`, `test_tournament_views.py`.
- **LG-02a-2 · [DONE] Bulk team intake + async play-all.** Two ergonomics follow-ups
  deferred from LG-02a so it could ship the minimal create + synchronous play loop
  first. (1) **CSV participant import** — let a Tournament's participant list be
  populated from a CSV roster via the **LG-00b roster importer** (reuse the
  existing import path rather than a bespoke parser), on top of the LG-02a
  select-existing + generate sources. (2) **Async "play-all"** — a one-click
  "play every remaining node to a champion" that runs **off-request** as a Celery
  task on the **ADR-0016 `play_season_task` precedent** (same task plumbing the
  League play loop already uses), instead of the per-node synchronous
  `tournament_play_next` POST. *Why deferred:* both are additive surfaces over the
  shipped model — the sync single-step loop proves the bracket/advancement engine
  end-to-end without the Celery/CSV surface area, and the async path wants the
  proven engine underneath it.
  - completed: shipped **CSV participant import + async play-all** as additive
    surfaces over the shipped LG-02a model (seam
    [`.claude/worktrees/lg-02a-2-seam-contract.md`](.claude/worktrees/lg-02a-2-seam-contract.md);
    **no model, no migration, no ADR** — per-node-atomic follows ADR-0016, CSV
    reuse follows LG-00b, both reversible). **CSV import reuses LG-00b verbatim**
    cross-app read-only — `teams.forms.RosterImportForm`,
    `teams.roster_importer.parse_roster_csv` / `RosterImportError`, and
    `teams.views._check_db_slot_collisions` / `_apply_roster` (signatures
    unchanged, no `teams/` edit) — plus the **Celery** plumbing reuse
    (`matches.views._celery_state_to_job_status` verbatim, the `play_season_task`
    body precedent). One new **pure** bracket fn `matches/bracket.py::stage_progress(nodes:
    list[dict]) -> tuple[int, int]` (STAGE-based progress = completed/total Bracket
    rounds; reads `bracket_round` / `is_bye` / `winner_id` off `_node_to_dict`
    output; respects the frozen `dataclasses`/`typing`/`math`/`collections`-only
    allowlist — `TestNoDjangoImportsLeaked` still passes, no new import). New module
    `matches/tournament_engine.py::play_next_node(tournament) -> BracketNode | None`
    (`@transaction.atomic`) **extracts** the per-node resolve/advance body out of the
    inline `tournament_play_next`; the sync view is **refactored** to call it (keeps
    its POST-only / `state != "active"` HTTP shell, inline sim/resolve/advance block
    deleted). New Celery task `matches/tasks.py::play_tournament_task(self,
    tournament_id) -> dict` (`@shared_task(name="matches.play_tournament")`) loops
    `play_next_node` to a champion — **per-node-atomic, NO outer
    `@transaction.atomic`** (ADR-0016 precedent: a mid-loop FAILURE leaves every
    already-resolved node committed; resumable), inactive-state early-return no-op,
    `close_old_connections()` in `finally`, **stage-based** `update_state` meta +
    return `{"completed": int, "total": int}` (stage counts, NOT node counts). Three
    new views/URLs in `tournament_views.py` / `tournament_urls.py`:
    `tournament_play_all` (POST → `play_tournament_task.delay`, HTTP **202**
    `{job_id, tournament_id}`, **409** when not active),
    `tournament_play_status` (GET, 5-key polling JSON `{status, completed, total,
    error, tournament_id}` via the new `_build_tournament_play_status_response`
    mirroring `_build_play_status_response`), and `tournament_import_participants`
    (POST `@transaction.atomic`). **CSV import = created-teams-only** (only brand-new
    `_apply_roster` `created_teams` become `TournamentParticipant`s — no
    `uniq_tournament_team` collision; appended teams are NOT auto-added), then
    **re-seed the whole field by talent** (`_team_mean_rating` →
    `default_seed_order`, two-phase offset write dodging `uniq_tournament_seed`,
    reusing the `tournament_reseed` idiom), **setup-only** (`is_locked` ⇒ flash +
    redirect, no writes), error path **re-renders** `tournament_detail.html` HTTP 200
    with `transaction.set_rollback(True)` + per-row errors (RosterImportError or bound
    form-invalid). A new private `_detail_context(tournament)` helper shares the detail
    context between `tournament_detail` and the import-error re-render (the 6 frozen
    LG-02a keys + `import_form` / `import_row_errors`). New DOM ids on
    `tournament_detail.html`: setup `tournament-import-{form,file,submit,template-link,errors}`
    + per-row `tournament-import-error-{row_num}-{field|row}`; active
    `tournament-play-all-{form,submit,progress}` (inline 1000 ms poll JS mirroring the
    LG-01d seasons dashboard, reveal/update progress, reload on complete, surface error
    on FAILURE) — the single-step `tournament-play-next-form` is unchanged. The
    CONTEXT.md **Job** term is extended to a **4th kind** (**Play Tournament job**) +
    the `/tournaments/<id>/play-all/` URL; **no new term** (the **Roster import** term
    is reused unedited). **Non-deterministic** — `simulate_match` draws fresh per-round
    seeds, so Play Tournament games are NOT master-seed-replayable: **no SIM-07 / SIM-08
    interaction, NO Score Calibration re-baseline**. Tests:
    `matches/tests/test_bracket.py` (extend — `TestStageProgress`),
    `test_tournament_engine.py` (NEW), `test_tournament_tasks.py` (NEW, under
    `CELERY_TASK_ALWAYS_EAGER`), `test_tournament_views.py` (extend).
- **LG-02b · [DONE] Best-of-N series nodes.** Generalise a bracket node from **one**
  2-round `Match` to a **best-of-3 / best-of-5 series**: the node resolves when one
  side clinches the majority, then Advances. *Why deferred:* LG-02a locked
  "node = exactly one Match" so the advancement + tie-break engine could be built
  against a single deterministic result; a series re-opens node-resolution
  semantics (per-game records, clinch detection, the tie-break's role) and is a
  clean increment once single-game advancement is proven.
  - completed: generalised a **Bracket node** from holding **one** 2-round `Match`
    to a best-of-N **Series**, the node Advancing only once a Team clinches the
    Match-win majority (seam
    [`.claude/worktrees/lg-02b-seam-contract.md`](.claude/worktrees/lg-02b-seam-contract.md)).
    New `Tournament.series_length` (`PositiveSmallIntegerField`, choices `1`/`3`/`5`
    "Best of 1/3/5", `default=1`; create-time only, frozen on the setup→active
    `lock_and_build` transition) — **Bo1 (`series_length == 1`) is byte-equivalent
    to LG-02a** (one Match, clinch threshold 1, identical Advancement). New
    `SeriesMatch` through-model (`node` FK CASCADE `related_name="series_matches"`,
    `match` FK SET_NULL, 1-based `game_number`, `winner` FK to `teams.Team`
    SET_NULL; `UniqueConstraint` `uniq_seriesmatch_node_game`,
    `Meta.ordering=["game_number"]`) — **one row per played Series Match**; the
    win tally is **derived** by counting `winner` rows per team-slot, **never
    stored** as counters. The LG-02a `BracketNode.match` FK is **dropped wholesale**
    (the per-Match link now lives on `SeriesMatch.match`). Migration
    `matches/migrations/0034_*` in pinned order `AddField(Tournament.series_length)`
    → `CreateModel(SeriesMatch)` → `RemoveField(BracketNode.match)` — **no
    `RunPython`, no backfill** (ADR-0004 disposable-sandbox precedent). Pure
    `matches/bracket.py` gains `clinch_threshold(series_length) -> int`
    (`(series_length // 2) + 1`: Bo1→1, Bo3→2, Bo5→3) and `series_winner_slot(wins_a,
    wins_b, series_length) -> Optional[str]` (`"a"`/`"b"`/`None`, total + never-raises,
    `wins_a` checked first); the `find_next_node` playable predicate swaps the old
    `winner_id IS NULL AND match_id IS NULL` checks for **`series_winner_slot(...)
    is None`**; `_node_to_dict` gains `wins_a`/`wins_b`/`series_length` and **drops
    `match_id`** (frozen `dataclasses`/`typing`/`math`/`collections`-only allowlist
    unchanged — `TestNoDjangoImportsLeaked` still passes). `tournament_engine.py::
    play_next_node` is rewritten to resolve **one Match per step**,
    **per-Match-atomic** (extends ADR-0016 from per-node to per-Match): sim ONE Match
    (sides fixed `team_a`/`team_b`) → per-Match `break_tie` on `match.winner is None`
    → create the next `SeriesMatch` row → recompute the derived tally → clinch at
    `(N//2)+1` ⇒ stamp `node.winner` + `advance_winner` + `champion`/`completed` on
    the final, **else return the node with no Advancement**; dead-rubber Matches are
    never simulated. The `stage_progress` / play-all / play-status routes are
    **unchanged** (`stage_progress` still reads `winner_id`, which is stamped only on
    clinch; the Celery loop simply iterates once per Match, so a Bo3/Bo5 drains over
    more steps). Surface: a create-form **`series_length` `<select>`** (DOM id
    `tournament-create-series-length`, options Bo1/Bo3/Bo5, default Bo1) + a per-node
    **Series-score** element (DOM id `tournament-node-series-score-{br}-{pos}`
    rendering `wins_a–wins_b`); the champion still surfaces via the unchanged
    `tournament-champion-banner`. **Non-deterministic** (`simulate_match` draws fresh
    per-round seeds) ⇒ **no SIM-07/08 interaction, NO Score Calibration re-baseline**.
    New **ADR-0020** "Best-of-N series bracket nodes" (cross-ref ADR-0019 + ADR-0016).
    Tests: `matches/tests/test_bracket.py` (extend — `clinch_threshold` /
    `series_winner_slot` / Series `find_next_node` cases + purity),
    `test_tournament_models.py` (extend — `SeriesMatch` + `series_length` +
    `_node_to_dict`), `test_tournament_engine.py` (extend — per-Match step +
    clinch-advance + Bo1-equivalence + tie-break), `test_tournament_views.py` (extend
    — create-field + detail Series-score), `test_tournament_tasks.py` (extend —
    play-all over a Bo3 Series). See seam
    [`.claude/worktrees/lg-02b-seam-contract.md`](.claude/worktrees/lg-02b-seam-contract.md).
- **LG-02b-2 · [DONE] Per-Bracket-round series escalation.** Let the
  **Series length vary by Bracket round** — Bo1 early rounds → Bo3 semis → Bo5
  final — instead of the single per-Tournament `series_length` LG-02b applies to
  every node. The node reads its round's N (a per-round config persisted at
  lock time) rather than `tournament.series_length`, building directly on the
  LG-02b Series engine (clinch / `SeriesMatch` / per-Match-atomic play stay
  verbatim — this task adds only the per-round N lookup + the create-time UI to
  set it). *Why deferred:* a clean increment over LG-02b — the Series engine is
  built once against a single per-Tournament length; escalation is purely a
  config-resolution + UI layer on top, not a re-open of node-resolution semantics.
  - completed: generalised the LG-02b best-of-N **Series length** from a **single
    flat per-Tournament value applied to every node** into a **per-Bracket-round**
    value anchored to **depth from the final** (seam
    [`.claude/worktrees/lg-02b-2-seam-contract.md`](.claude/worktrees/lg-02b-2-seam-contract.md)).
    A node's Series length resolves from `depth = total_rounds - bracket_round`:
    **depth 0** final, **1** semifinal, **2** quarterfinal, **≥ 3** every earlier
    round (all collapse to one `earlier` slot — no fifth tier). The pure clinch
    engine (`clinch_threshold`, `series_winner_slot`, `count_series_wins`,
    `SeriesMatch`, the per-Match-atomic `play_next_node` body) is **UNCHANGED** —
    **only the *source* of the `series_length` argument moves from tournament-level
    to node-level**. Model: the LG-02b flat `Tournament.series_length` is **DROPPED
    wholesale** (no alias/shim) and replaced by **four** `PositiveSmallIntegerField`s
    — `final_series_length` / `semifinal_series_length` / `quarterfinal_series_length`
    / `earlier_series_length` (each choices `1`/`3`/`5` "Best of 1/3/5", `default=1`,
    create-time only, frozen on the setup→active transition); plus new
    `BracketNode.series_length` (`PositiveSmallIntegerField`, `default=1`, **no
    choices**, the resolved int). **Bo1-everywhere (all four `1`, the migration
    default) is byte-equivalent to LG-02b/LG-02a.** `lock_and_build` computes
    `total_rounds = max(spec.bracket_round …)` and **stamps `node.series_length`**
    on **every** persisted node (incl. byes — inert) in the existing
    `BracketNode.objects.create(...)` loop via the new pure resolver. New pure
    `matches/bracket.py::series_length_for_round(bracket_round, total_rounds, *,
    final, semifinal, quarterfinal, earlier) -> int` (`depth = total_rounds -
    bracket_round`; 0→final, 1→semifinal, 2→quarterfinal, `else`/≥3→earlier; four
    slot args **keyword-only**; pure/total/never-raises; frozen `dataclasses`/
    `typing`/`math`/`collections`-only allowlist unchanged — no new import,
    `TestNoDjangoImportsLeaked` still passes). **Seam read swap**: `_node_to_dict`'s
    `"series_length"` and `play_next_node`'s clinch check now read `node.series_length`
    (was `node.tournament.series_length`); `select_related("tournament")` is
    droppable from `play_next_node` + `find_next_playable_node` (no residual
    `node.tournament` reader; nicety, not pinned). **No monotonicity** — the four
    slots are independent `{1,3,5}` in any order. Migration `matches/migrations/
    0035_*` in pinned order `RemoveField(Tournament.series_length)` → 4×
    `AddField(Tournament.*_series_length)` → `AddField(BracketNode.series_length)`
    — **no `RunPython`, no backfill** (ADR-0004 disposable-sandbox precedent; dep
    `0034_tournament_series`). Surface: **four create-form `<select>`s** (DOM ids
    `tournament-create-{final,semifinal,quarterfinal,earlier}-series-length`,
    options Bo1/Bo3/Bo5, Bo1 default; POST fields `final_series_length` /
    `semifinal_series_length` / `quarterfinal_series_length` / `earlier_series_length`,
    each int-coerced + forced into `{1,3,5}` with independent forgiving fallback to
    `1`; old single `tournament-create-series-length` id **removed**); detail page
    `_build_rounds` node dict gains `series_length`, and each **non-bye** node
    renders a Bo-N label (DOM id `tournament-node-series-length-{br}-{pos}`, text
    `Bo{n}`) beside the unchanged `tournament-node-series-score-{br}-{pos}`; the
    frozen `_detail_context` keys and `tournament-champion-banner` are unchanged.
    The four `Tournament` fields + `BracketNode.series_length` auto-surface in the
    default admin change forms (no `list_display` change). **ADR-0020 extended**
    (not re-written) for the per-round escalation; CONTEXT.md `### Tournaments`
    **Series length** (revised) + **Series escalation** (added) already written.
    **Non-deterministic** (`simulate_match` draws fresh per-round seeds) ⇒ **no
    SIM-07/SIM-08 interaction, NO Score Calibration re-baseline**. Tests:
    `matches/tests/test_bracket.py` (extend — `series_length_for_round` depth
    boundaries + N=4/8/16 worked cases + purity), `test_tournament_models.py`
    (extend/migrate — `lock_and_build` stamps per depth incl. byes, four new fields
    + node field exist/default/choices, old field gone, `_node_to_dict` reads node),
    `test_tournament_views.py` (extend/migrate — four selects + POST persist +
    fallback + detail Bo-N label), `test_tournament_engine.py` (extend/migrate —
    node reads its own `series_length`, Bo3 clinch at 2, Bo1 unchanged),
    `test_tournament_tasks.py` (migrate the `_active_series_tournament` helper to
    the four-field shape).
- **LG-02c+ · [DONE — double-elim + round-robin + RR→DE + Swiss all DONE; LG-02x-1
  Random Draw player pool also DONE, LG-02x-2 Duos / Trios is the next LG-02 Part-1 work] Additional bracket formats.** **Double elimination** (losers get a
  second chance via a losers bracket), **round robin** (all teams play each other,
  used for seeding), **round robin → double elimination** (RR seeding phase feeds a
  DE finals), and **Swiss** (pairings from current standings; rounds
  auto-calculated ⌈log₂(N)⌉, admin-overridable). *Why deferred:* the `format` enum
  shipped extensible-but-single (`"single_elimination"`) precisely so these slot in
  as new enum values + new pure `matches/bracket.py` builders without a model
  migration; each format is its own grill (losers-bracket wiring, RR scheduling +
  seeding handoff, Swiss pairing) rather than a variant of single-elim.
  - **Double elimination · [DONE].** Extended the single-elim `BracketNode` tree
    into **two coupled brackets** — a **Winners bracket** and a **Losers bracket**
    joined by a **Grand final with Bracket reset** — as a second
    `Tournament.format` enum value (`("double_elimination", "Double elimination")`)
    driven by a new pure builder, hosting **both** sub-brackets in the *existing*
    `BracketNode` table (one table + a sub-bracket tag, **not** a second
    `LoserBracketNode` model). Single-elim is **byte-unchanged** end to end
    ([ADR-0021](docs/adr/0021-double-elimination-bracket.md); seam
    [`.claude/worktrees/lg-02c-seam-contract.md`](.claude/worktrees/lg-02c-seam-contract.md)).
    Builds on LG-02b (the `SeriesMatch` clinch engine) + LG-02b-2 (depth-from-final
    escalation, re-anchored to depth-from-Grand-final). Model: `BracketNode` gains
    **`bracket_type`** (`CharField`, choices `winners`/`losers`/`grand_final`,
    `default="winners"`), **`loser_advances_to`** (self-FK, SET_NULL, related_name
    `"loser_feeders"` — where THIS node's loser **Drops**), and
    **`loser_advances_to_slot`** (`a`/`b`, nullable); LB nodes + single-elim WB
    nodes leave the loser pointer NULL (loser eliminated). The
    `uniq_tournament_round_position` constraint is **renamed**
    `uniq_tournament_bracket_round_position` with `bracket_type` added to the field
    tuple (a WB and LB node may share `(round, position)`). Migration
    `matches/migrations/0036_*` (dep `0035_tournament_series_escalation`; ops
    `AlterField(Tournament.format)` choices-widen → 3× `AddField(BracketNode.*)` →
    `RemoveConstraint` → `AddConstraint` — **no `RunPython`, no backfill**,
    ADR-0004). Pure `matches/bracket.py` (frozen allowlist unchanged, no new
    import, `TestNoDjangoImportsLeaked` green): new
    **`build_double_elim_bracket(participants)`** emits the full two-tree spec list
    for arbitrary **N ≥ 4 with byes** (WB = the existing single-elim tree; LB
    consumes WB losers via a **naive same-position drop** — **NO anti-rematch
    folding**, a known limitation deferred; GF1 + GF2 built at lock); new
    **`series_length_for_depth(depth, *, final, semifinal, quarterfinal,
    earlier)`** (depth→slot dispatch — DE depth = distance-to-GF1, GF1/GF2 depth 0;
    `series_length_for_round` **delegates** to it, byte-identical); new **SEPARATE
    `advance_loser`** (parallel to a **byte-unchanged** `advance_winner` — the
    engine makes two explicit calls on a WB/GF1 clinch); `resolve_bye_chain`
    generalized to collapse **Drop byes** (a WB Bye produces no loser ⇒ its LB slot
    collapses); `find_next_node` sort key → `(bracket_type rank
    winners<losers<grand_final, round, position)` (single-elim collapses to
    `(round, position)`); `stage_progress` counts distinct `(bracket_type,
    bracket_round)` groups. `_node_to_dict` gains `bracket_type` + a **3-tuple**
    `loser_advances_to` `(bracket_type, round, position)` + `loser_advances_to_slot`
    (the deliberate 2-tuple-`advances_to` / 3-tuple-`loser_advances_to` asymmetry —
    the Drop crosses brackets, the winner-advance does not). Engine
    `play_next_node` stays **ONE** per-Match-atomic loop for both formats; on a
    WB/GF1 clinch it Advances the winner **AND** Drops the loser into
    `loser_advances_to`, then resolves the **Grand-final Bracket reset**: if the GF1
    winner is the WB champ, stamp **`GF2.winner` inert** (bye-style auto-resolve, so
    `find_next_node` never returns GF2) + champion + `completed` immediately; if the
    GF1 winner is the LB champ, both Advance into GF2 (playable) and GF2's winner is
    champion. View/template: create-form `<select>` DOM id
    **`tournament-create-format`** (POST field `format`, forgiving fallback to
    `single_elimination`); detail `_build_rounds` returns a **3-key dict**
    `{"winners", "losers", "grand_final"}` (single-elim: only `"winners"` non-empty)
    and renders three sections — the template **branches on `tournament.format`** so
    **single-elim keeps the legacy** `tournament-node-{round}-{position}` ids while
    DE uses **`tournament-node-{bracket_type}-{round}-{position}`** (containers
    `tournament-bracket-winners` / `-losers` / `-grand-final`). The three new
    `BracketNode` fields + the widened `format` choices auto-surface in admin (no
    `list_display` change). **Non-deterministic** (`simulate_match` draws fresh
    per-round seeds) ⇒ **no SIM-07/SIM-08 interaction, NO Score Calibration
    re-baseline**. ADR-0021 + CONTEXT.md `### Tournaments` (Winners bracket / Losers
    bracket / Drop / Grand final / Bracket reset) already written, not re-touched.
    Tests: `test_bracket.py` (`series_length_for_depth`, `build_double_elim_bracket`,
    `advance_loser`, Drop-bye cascade, bracket-order `find_next_node`, DE
    `stage_progress`; single-elim cases green), `test_tournament_models.py` (DE
    fields/renamed constraint, DE `lock_and_build` incl. depth-stamped byes,
    single-elim regression, `_node_to_dict` DE keys), `test_tournament_views.py`
    (format select + persist + fallback, DE three-section render, single-elim id
    regression), `test_tournament_engine.py` (WB loser Drop, Grand-final Bracket
    reset both branches, single-elim regression), `test_tournament_tasks.py`
    (`play_tournament_task` drains a DE bracket, stage counts over both brackets).
  - **Round robin · [DONE].** Added a third `Tournament.format` enum value
    (`("round_robin", "Round robin")`): a **flat double round-robin** where every
    enrolled team plays every other **twice** (one fixture per leg), with **NO
    advancement** — the champion is the **Standings** leader once every node is
    resolved. Unlike the elim formats it has no bracket tree: a **flat set of
    `BracketNode` rows with no `advances_to` / `loser_advances_to` edges**. It reuses
    three existing pure seams verbatim — `generate_schedule` (the fixture list, whose
    full output **is** a double RR — each pair twice, once per leg), `compute_standings`
    (the LG-06g ranked table), and the LG-02b `SeriesMatch` clinch engine (Bo1 per
    fixture) — so it shipped as a **choices widen + two `Tournament` methods + one
    engine guard + a `_BRACKET_RANK` entry**, with **no new pure builder** and **no
    `matches/bracket.py` import-allowlist change** (only `_BRACKET_RANK["round_robin"]
    = 3`, a literal edit; `TestNoDjangoImportsLeaked` green). Single- and double-elim
    are **byte-unchanged**. `BracketNode.bracket_type` choices gain `"round_robin"`
    (fits the existing `max_length=12`). Migration
    `matches/migrations/0037_tournament_round_robin.py` (dep
    `0036_bracketnode_double_elimination`; two `AlterField`s — `Tournament.format` +
    `BracketNode.bracket_type` choices widen — **no `RunPython`, no backfill**,
    ADR-0004). Build: a **third `format` branch in `Tournament.lock_and_build()`**
    (model layer, **not** `bracket.py`) deferred-imports `generate_schedule` and
    creates one node per fixture — `bracket_type="round_robin"`,
    `bracket_round=matchday`, `position`=0-based index within the matchday,
    `team_a`/`team_b` + `seed_a`/`seed_b` fixed at lock, `is_bye=False`,
    `series_length=1` (Bo1), `advances_to`/`loser_advances_to` left `None` — and runs
    **no wiring pass and no `resolve_bye_chain`** (RR nodes never advance; N=4 → 12
    nodes, N=6 → 30). Two new `Tournament` methods (both deferred-import
    `compute_standings`): **`round_robin_standings()`** assembles the 9-key
    `completed_matches` / `(id, name)` `enrolled_teams` / 6-key `season_rounds` seam
    inputs from the RR nodes and returns ranked `StandingsRow`s for every enrolled team
    (zero-filled pre-play; `winner_team_id = node.winner_id`, never `None` for a
    resolved node; no seed-aware tiebreak override — `compute_standings`' `team_name
    asc` final tiebreak stands); **`complete_round_robin_if_finished()`** (idempotent,
    parallel to `Season.complete_if_finished`) stamps `champion =
    round_robin_standings()[0].team_id` + `state="completed"` once **every** RR node
    has `winner_id is not None`. Engine `play_next_node` stays **ONE**
    per-Match-atomic loop for all three formats; on an RR clinch a **guard `if
    tournament.format == "round_robin":`** (after the `node.winner` stamp, before the
    elim advance/crown block) calls `complete_round_robin_if_finished()` then `return
    node` — **required** to skip the elim "crown when `advances_to` is `None`" rule,
    which would otherwise wrongly crown on the first resolved node.
    `find_next_node` is **UNCHANGED** (an unplayed RR node is playable, a resolved one
    skipped; sort `(3, matchday, position)`). View/template: the create-form
    `<select>` (`tournament-create-format`) gains a **`"Round robin"`** option
    (forgiving fallback to `single_elimination`; the four series-length selects hidden
    client-side for RR, their inert values harmless); the detail page keeps
    `_build_rounds`' 3-key elim dict (all empty for RR) and rides on two NEW
    `_detail_context` keys **`rr_crosstable`** + **`rr_standings`** (empty for elim),
    rendering an N×N crosstable (DOM id **`tournament-rr-crosstable`** — leg
    `round_number==1` → `cell[team_a][team_b]`, leg `2` → `cell[team_b][team_a]`,
    diagonal blank; the view re-derives `round_number` via `generate_schedule` since
    the node only stores `matchday`/`position`) + a live standings table (DOM id
    **`tournament-rr-standings`**), reusing the lock / play-next / play-all controls +
    `tournament-champion-banner` verbatim; no per-node series-score / Bo-N labels (RR
    is Bo1). **Non-deterministic** (`simulate_match` draws fresh per-round seeds) ⇒
    **no SIM-07/SIM-08 interaction, NO Score Calibration re-baseline**; **no new ADR**
    (reversible — choices widen + two methods + a deferred import) and **no new
    CONTEXT.md term** (reuses Tournament / Bracket node / Standings) — ADR-0021 +
    CONTEXT.md `### Tournaments` extended at grilling time, not re-touched. Seam
    [`.claude/worktrees/lg-02c-round-robin-seam-contract.md`](.claude/worktrees/lg-02c-round-robin-seam-contract.md).
    Tests: `test_bracket.py` (`TestBracketRankRoundRobin`, `TestNoDjangoImportsLeaked`
    green), `test_tournament_models.py` (`TestTournamentRoundRobinFormat`,
    `TestLockAndBuildRoundRobin`, `TestRoundRobinStandings`,
    `TestCompleteRoundRobinIfFinished`), `test_tournament_engine.py`
    (`TestPlayNextNodeRoundRobinNoEarlyCrown`, `TestPlayNextNodeRoundRobinCompletes`),
    `test_tournament_views.py` (`TestCreateFormRoundRobin`,
    `TestDetailRoundRobinCrosstable`), `test_tournament_tasks.py`
    (`TestPlayTournamentTaskRoundRobin`) — assert on pure functions, persisted
    node/row shapes, `node.winner` / `champion` / `state`, standings ORDER, and DOM
    ids, **never** exact simulated point totals.
  - **Round robin → double elimination · [DONE].** Added a fourth
    `Tournament.format` enum value
    (`("round_robin_double_elim", "Round robin → Double elimination")`, em-dash arrow
    `→` U+2192): a **two-stage** format composing the two shipped LG-02c formats — a
    round-robin **Seeding stage** (the SHIPPED double round-robin, verbatim) whose
    final **Standings rank** seeds a double-elimination **Finals stage** (the SHIPPED
    ADR-0021 WB+LB+Grand-final tree) built **lazily** when the last Seeding node
    resolves. Builds on the round-robin (seeding) + double-elimination (finals) slices,
    reusing their pure + persist machinery verbatim. Single/double-elim and plain
    round-robin are **byte-unchanged**
    ([ADR-0021](docs/adr/0021-double-elimination-bracket.md) extended; seam
    [`.claude/worktrees/lg-02c-rr-de-seam-contract.md`](.claude/worktrees/lg-02c-rr-de-seam-contract.md)).
    - completed: Two NEW `Tournament` `PositiveSmallIntegerField(default=0)` fields —
      **`wb_advancers`** (top-ranked teams into the Winners bracket) +
      **`lb_advancers`** (next-ranked teams pre-seeding the Losers bracket), declared
      after the four `*_series_length` fields, **no `choices`** (the create form's
      `rrde_combo` select is the single source of valid shapes; the model holds
      resolved ints, mirroring `BracketNode.series_length`), **create-time only, frozen
      at lock**, `0` for non-RRDE formats. Locked shape: **`wb ∈ {4,8,16}`**,
      **`lb ∈ {0, wb//2}`** — six combos (`4/0, 4/2, 8/0, 8/4, 16/0, 16/8`); the SHAPE
      is enforced in the create form, the COUNT fit (`wb <= n`, `wb + lb <= n`)
      validated at `lock_and_build` → `ValidationError` (surfaced via `messages.error`,
      LG-02a precedent). The RRDE lock widens the RR guard to
      `if self.format in ("round_robin", "round_robin_double_elim"):` and builds
      **ONLY the RR Seeding nodes** (byte-identical to the round-robin build) — **the
      DE Finals are NOT built at lock**. **Deferred Finals build:** new
      **`build_de_finals_if_rr_finished(self)`** (`@transaction.atomic`) fires when the
      last Seeding node resolves (guarded: RRDE + `active` + every RR node resolved +
      idempotent), reads `round_robin_standings()`, splits by rank (top `wb` →
      `ParticipantSpec` WB seeds `1..wb`; next `lb` → LB pre-seeds `wb+1..wb+lb`; rest
      eliminated), calls the new pure builder, resolves each spec's `series_length` via
      `series_length_for_depth(spec.depth, ...)`, and persists via the shared helper —
      the Tournament **STAYS `active`**, the champion is crowned later by the DE Grand
      final. **NEW pure builder `build_rr_de_finals_bracket(upper_specs, lower_specs)`**
      (`matches/bracket.py`, **no new import**, `TestNoDjangoImportsLeaked` green):
      `lower_specs == []` **delegates directly to `build_double_elim_bracket(upper_specs)`**
      (provably identical, a plain top-`wb` DE); non-empty pre-fills LB-R1 slot "a" with
      the `lb` pre-seeds in seed order and points each WB-R1 `loser_advances_to` at the
      matching LB-R1 slot "b" (naive same-position drop — **NO anti-rematch folding**,
      inherited limitation), WB re-tagged `winners` via the DE re-tag pass, GF1/GF2 as
      a plain DE, **no byes anywhere**. **Extracted shared helper
      `_persist_elim_specs(self, specs, ...)`** — the DE persist loop + the two wiring
      passes (`advances_to` 2-tuple / `loser_advances_to` 3-tuple) + the
      `resolve_bye_chain` cascade, pulled out of `lock_and_build` so BOTH the
      single/double-elim lock path AND the deferred Finals build reuse it verbatim
      (`series_length` stamping stays in the caller; single/double-elim `lock_and_build`
      stays byte-identical, pinned by `TestLockAndBuildSingleElimUnchanged` /
      `...DoubleElimUnchanged`). **Engine:** the `play_next_node` RR guard rekeys from
      `tournament.format == "round_robin"` to **`node.bracket_type == "round_robin"`**
      and dispatches on format (`round_robin` → `complete_round_robin_if_finished()`;
      `round_robin_double_elim` → `build_de_finals_if_rr_finished()`) then
      `return node` — a Seeding node never falls through; DE-stage nodes fall through to
      the **byte-unchanged** elim advance/drop/GF-reset/crown block. **Migration
      `matches/migrations/0038_tournament_rr_de.py`** (dep `0037_tournament_round_robin`;
      `AlterField(Tournament.format)` choices-widen + `AddField(wb_advancers)` +
      `AddField(lb_advancers)` — **no `RunPython`, no backfill**, ADR-0004).
      **View/template:** create-form `<select name="format">`
      (`tournament-create-format`) gains the `round_robin_double_elim` option + a NEW
      `<select name="rrde_combo">` (`tournament-create-rrde-combo`) enumerating the six
      combos (shown client-side only for RRDE; forgiving fallback to `(4, 0)`); detail
      page gains DERIVED `tournament_stage` (`setup`/`seeding`/`finals`/`completed`) +
      `cut_labels` (`team_id -> "wb"|"lb"|"out"` from standings rank) context keys, a
      **`tournament-stage-badge`** DOM id, and per-standings-row cut-marker substrings
      **`tournament-standings-cut-{wb|lb|out}`** in the seeding stage — reusing the RR
      crosstable/standings and the DE three-section tree verbatim. **Stage is DERIVED,
      not stored** (`nodes.exclude(bracket_type="round_robin").exists()`). **Non-
      deterministic** (`simulate_match` fresh per-round seeds) ⇒ **no SIM-07/SIM-08
      interaction, NO Score Calibration re-baseline**; **no new ADR** (ADR-0021 extended
      for the deferred build) and **no new CONTEXT.md term** (Round robin → double
      elimination finalised at grilling). Tests: `test_bracket.py`
      (`TestBuildRrDeFinalsBracket` — `lb=0` equals `build_double_elim_bracket`, fused
      `lb=wb/2` LB-R1 pre-fill + WB-R1 drop wiring + GF/`depth` + no byes + LB rounds
      `2W-1`; `TestNoDjangoImportsLeaked`), `test_tournament_models.py` (fields/defaults/
      no-choices, RRDE `lock_and_build` builds only RR nodes + count-fit
      `ValidationError`, `build_de_finals_if_rr_finished` guards/idempotency/seeding,
      `_persist_elim_specs` byte-identity regressions), `test_tournament_engine.py`
      (last-RR-node triggers Finals build, drain crowns via GF, Seeding node never
      advances), `test_tournament_views.py` (combo select + parse/persist + fallback,
      stage badge + cut markers, reused RR/DE ids), `test_tournament_tasks.py`
      (`play_tournament_task` drains both stages to a champion, `stage_progress` spans
      RR then WB/LB/GF groups) — assert on pure functions, persisted node/row/edge
      shapes, `node.winner`/`champion`/`state`, standings ORDER, DOM ids, **never** exact
      simulated point totals.
  - **Swiss · [DONE].** Added the fifth `Tournament.format` enum value
    **`("swiss", "Swiss")`** (+ a fifth `BracketNode.bracket_type` `("swiss", "Swiss")`
    and `_BRACKET_RANK["swiss"] = 4`) as a **flat, edge-less** Swiss-system format:
    every Swiss node is a Bo1 pairing with `advances_to`/`loser_advances_to` `None`,
    `is_bye=False`, `series_length=1` — **no advancement tree, no final node**; the
    champion is the **Standings leader (Buchholz re-ranked)** once the last Swiss round
    resolves. **EVEN-N only, no byes:** an odd participant count raises
    `ValidationError("Swiss requires an even number of participants.")` at
    `lock_and_build` (surfaced via `messages.error`, LG-02a precedent). New field
    **`Tournament.swiss_rounds`** (`PositiveSmallIntegerField(default=0)`, after
    `lb_advancers`, **no `choices`**, create-time): `0` = auto, resolved at lock to
    `swiss_rounds or math.ceil(math.log2(N))` **clamped to `[1, N-1]`** and **written
    back** (frozen). **R1 build at lock = seed "fold"** (sort by Bracket seed asc,
    split in half, interleave `(seed[i], seed[i+N/2])`) in a **dedicated `swiss`
    branch** of `lock_and_build` (its own branch — not folded into RR — for the even-N
    guard + round-count freeze + fold pairing + `bracket_type="swiss"`); the R1 build
    emits **only** the `N/2` round-1 nodes. **Later rounds DEFERRED per round:** new
    **`advance_swiss_if_round_finished(self)`** (`@transaction.atomic`) fires when the
    current (highest) Swiss round's last node resolves (no-op unless `swiss` + `active`
    + every node in that round resolved); if `current < swiss_rounds` it builds the
    next round via a **greedy ranked sweep** from `swiss_standings()` + `played_pairs`
    (pair each unpaired team with the next not-yet-played team, **allow-rematch
    fallback** for the trailing teams, no backtracking) and stays `active`; if
    `current == swiss_rounds` it crowns `swiss_standings()[0]` and flips
    `state="completed"`. **No draws** (`break_tie` forces a per-Match winner) ⇒
    `league_points = 3 * wins`. **Buchholz tiebreak is ORDERING-ONLY:** ladder
    `league_points desc → Buchholz desc → round_wins desc → total_score desc →
    team_name asc`, Buchholz = sum of opponents' final `league_points` per played
    pairing (rematch counts twice); `compute_standings` is **FROZEN/unmodified** —
    Buchholz is a **separate pure re-rank layer** over its rows + the opponent graph
    (stable sort, so `team_name asc` survives without a name lookup crossing the pure
    seam). **NEW pure functions** (`matches/bracket.py`, **no new import**,
    `TestNoDjangoImportsLeaked` green): **`build_swiss_round(ranked_team_ids,
    seed_by_team, played_pairs, bracket_round)`** — ONE function for BOTH the R1 fold
    (empty `played_pairs` ⇒ the not-yet-played check never fires) and the later greedy
    sweep (rank order + filled `played_pairs`); and **`swiss_buchholz_rerank(rows,
    opponents_by_team)`** — re-sorts + renumbers `rank` 1-based dense, ORDERING-ONLY.
    **Model helpers:** **`_standings_over_nodes(self, node_qs)`** extracted from
    `round_robin_standings()` (which stays byte-identical, pinned by a regression
    test), reused by **`swiss_standings(self)`**; plus **`_swiss_opponent_graph(self)`**
    and **`_swiss_played_pairs(self)`**. **Engine:** `play_next_node` gains a Swiss
    guard alongside the RR/RR→DE guard — `if node.bracket_type == "swiss":
    advance_swiss_if_round_finished(); return node` — so a resolved Swiss node never
    falls through to the elim advance/crown block (which would wrongly crown on the
    first resolved node, since `advances_to is None`); callers unchanged.
    **Migration `matches/migrations/0039_tournament_swiss.py`** (dep
    `0038_tournament_rr_de`; `AlterField(Tournament.format)` + `AlterField(BracketNode.
    bracket_type)` choices-widen + `AddField(Tournament.swiss_rounds)` — **no
    `RunPython`, no backfill**, ADR-0004). **View/template:** create-form `<select
    name="format">` (`tournament-create-format`) gains the `swiss` option + a NEW
    numeric `swiss_rounds` input (`tournament-create-swiss-rounds`, shown client-side
    only for swiss; forgiving `_parse_swiss_rounds` ⇒ `0` on absent/blank/invalid/
    negative; series-length + rrde-combo controls hidden for swiss); detail page gains
    DERIVED context keys **`swiss_rounds_view`** (`[{round_number, pairings}]`, Swiss
    nodes grouped by round, reusing the node-card include via `_build_swiss_rounds`) +
    **`swiss_standings`** (`[(StandingsRow, Team)]`), the DOM ids
    **`tournament-swiss-rounds`** / **`tournament-swiss-round-{n}`** /
    **`tournament-swiss-standings`** / **`tournament-node-swiss-{br}-{pos}`**, and
    `_tournament_stage` returns `"swiss"` (badge widened) — reusing the champion/lock/
    play-next/play-all ids verbatim. **Non-deterministic** (`simulate_match` fresh
    per-round seeds) ⇒ **no SIM-07/SIM-08 interaction, NO Score Calibration
    re-baseline**; **no new ADR** (ADR-0021 extended for the per-round deferred build +
    Swiss-only Buchholz re-rank) and **no new CONTEXT.md term** (Swiss + Buchholz
    finalised at grilling). Tests: `test_bracket.py` (`TestBuildSwissRound` fold/greedy/
    allow-rematch, `TestSwissBuchholzRerank` ladder + ORDERING-ONLY + empty,
    `TestBracketRankSwiss`, `TestNoDjangoImportsLeaked`), `test_tournament_models.py`
    (`swiss_rounds` field/default/no-choices, `lock_and_build` even-N + resolve/clamp/
    freeze + odd-N `ValidationError`, `_standings_over_nodes` byte-identity regression,
    `swiss_standings` Buchholz ORDER, `advance_swiss_if_round_finished` deferred build /
    crown / no-op / rematch-fallback), `test_tournament_engine.py` (Swiss node never
    advances, last-node-of-round triggers next build, final round crowns
    `swiss_standings()[0]`), `test_tournament_views.py` (create form offers swiss + the
    `tournament-create-swiss-rounds` input + forgiving parse/fallback; detail renders the
    four new Swiss DOM ids, hides series/rrde controls, reuses champion/lock/play ids,
    elim+RR ids absent), `test_tournament_tasks.py` (`play_tournament_task` drains a full
    Swiss tournament to a champion, `stage_progress` per-round counts) — assert on pure
    functions, persisted node/row shapes, `node.winner`/`champion`/`state`, standings
    ORDER, DOM ids, **never** exact simulated point totals. See
    [ADR-0021](docs/adr/0021-double-elimination-bracket.md) Consequences for the "new
    format = new enum value + reused/new pure seam" precedent this slice extends again.
- **LG-02x-1 · [DONE] Random Draw player pool.** A format with **no pre-set teams**:
  a pool of individual players registers, the system runs a **deterministic
  tier-balanced draw** into teams, and roles are assigned dynamically each game Round;
  the drawn teams then play the shipped **Round Robin → Double Elimination** bracket.
  *Why deferred (own grill):* it breaks the LG-02a assumption that participants **are**
  existing `Team`s — it needs a player-pool registration surface, a draw/assignment
  step with admin review, and dynamic per-Round role assignment, none of which the
  LG-02a Tournament/Participant/BracketNode model covers. The **LG-02x-1 grill
  (2026-06-04)** superseded the original one-line "randomize team assignments once the
  pool is full" sketch with the **tier-balanced draw + per-Round dynamic roles** design
  recorded below, and finalised the CONTEXT.md terms **Player pool / Drawn-team
  membership / Random Draw / Tier / Role assignment mode**.
  - completed: shipped the **Random Draw player-pool mode** as a NEW **orthogonal**
    `Tournament.team_assembly == "random_draw"` axis (vs the default `"preset"`), **NOT
    a new `format` value** (cite
    [ADR-0022](docs/adr/0022-random-draw-player-pool-tournament.md); seam
    [`.claude/worktrees/lg-02x-1-seam-contract.md`](.claude/worktrees/lg-02x-1-seam-contract.md);
    CONTEXT.md carries the 5 locked terms — not edited). A `random_draw` Tournament
    keeps `format="round_robin_double_elim"` and runs the **shipped LG-02c RR→DE
    bracket byte-unchanged** (`lock_and_build`, `_persist_elim_specs`,
    `round_robin_standings`, `build_de_finals_if_rr_finished`, `play_next_node`,
    `stage_progress`, the detail crosstable / cut-labels / DE-finals surfaces all
    untouched); pool intake, the draw, the relaxed roster rule, and per-Round dynamic
    roles **all key off `team_assembly == "random_draw"`**. **Model:** two new
    create-time `Tournament` fields — `team_assembly`
    (`"preset"`/`"random_draw"`, default `"preset"`) and `role_assignment_mode`
    (`"random"`/`"per_tier"`, default `"random"`, meaningful only for `random_draw`) —
    plus a NEW **`TournamentPlayerEntry`** model (the durable **pool registration AND
    draw result**: `tournament` CASCADE / `player` CASCADE / `tier` (1..6 post-draw,
    null pre-draw) / `drawn_team` SET_NULL, `Meta.ordering = [tournament_id, tier,
    player_id]`, `unique(tournament, player)` — a Player can be on draw teams across
    **different** Tournaments but **never two in the same** one), and a new
    `Team.is_draw_team` boolean (migrations `matches/0040_tournament_random_draw` +
    `teams/00XX_team_is_draw_team`, cross-app dep, **no `RunPython`/backfill**,
    ADR-0004 precedent). **Draw** = NEW pure module `matches/draw.py`
    (`dataclasses`/`typing`/`random`/`collections`-only, `TestNoDjangoImportsLeaked`):
    `compute_draw(pool)` is **STRAIGHT TIERS + GREEDY BALANCE, deterministic, no RNG**
    (sort by `overall_rating` DESC / player-id ASC; 6 contiguous Tiers of `T = N/6`,
    Tier 1 = strongest; strongest-remaining Tier player → currently-weakest team;
    `ValueError` unless `N % 6 == 0 and N >= 24`), idempotent re-roll, **admin
    hand-edit is the variation mechanism**; plus `build_random_role_assignment` /
    `build_per_tier_role_assignment` (injected `random.Random`) over the fixed
    `ROLE_SLOTS = (commander, heavy, scout_1, scout_2, medic, ammo)`. **Per-Round
    dynamic roles** via an additive keyword-only `before_round_hook` on
    `BatchSimulator.simulate_match` (default `None` ⇒ byte-unchanged for every existing
    caller; fires once per Round, round 2 receiving swapped `(team_blue, team_red)`,
    rewriting the drawn Teams' `slot_*` FKs **in memory only**) driven by a
    `team_assembly`-keyed branch in `tournament_engine.play_next_node` +
    `_build_role_hook(tournament)` (`else` branch byte-identical to today; `random` =
    each team shuffles independently, `per_tier` = one Tier→slot bijection both sides;
    fresh `random.Random()` per Round). **Roster relaxation:** `Team.roster_errors`
    skips the belongs-to-team ownership check for `is_draw_team` Teams (drawn Teams
    **reference borrowed Players** — `Player.team` is **never reassigned**, so career
    stats stay unified); the duplicate-player + all-6-slots + role-distribution checks
    **still fire**. **Views/URLs:** `tournament_create` reads `team_assembly` /
    `role_assignment_mode` (forgiving fallbacks); 6 new player-pool views/URLs
    (`tournament_pool_add_existing` / `_generate` / `_import` / `_remove` /
    `tournament_draw` / `tournament_draw_edit`, all setup-only, `@transaction.atomic`)
    mirroring LG-02a/a-2 intake at **Player** granularity — existing-select, LG-00
    generate, and LG-00b CSV (`parse_roster_csv` reused, `by_team` grouping **ignored**,
    each row = one pool Player on the Free Agents Team) — plus `_detail_context`
    additions (`pool_entries` / `pool_size` / `is_drawn` / `pool_import_form` / …); the
    existing `tournament_lock` reaches `active` over the drawn Teams **unchanged**. New
    detail-page pool/draw surface (DOM ids `tournament-pool-*` / `tournament-draw-*`),
    rendered only for `random_draw`. **Non-deterministic** (the per-Round role draw +
    the per-Match sims use fresh RNG) ⇒ **no SIM-07/SIM-08 interaction, NO Score
    Calibration re-baseline** (no simulation mechanics change — only which Player
    occupies each role slot). Tests: NEW `matches/tests/test_draw.py` (pure) + extensions
    to `test_tournament_models.py` / `test_tournament_views.py` / `test_tournament_engine.py`
    / `test_tournament_tasks.py` / `test_simulation_view_paths.py` / `teams/tests/test_models.py`
    (assert pure functions, persisted row/constraint shapes, the hook contract, the
    relaxed-roster rule, DOM ids — **never** exact simulated point totals).

#### Part 2 · In-League composable season structure

- **LG-02-Part2a · [DONE] `SeasonPhase` foundation slice.** Ships the persisted
  **`SeasonPhase`** model (FK → **Season** with `related_name="phases"`, a
  1-based `ordinal`, a `phase_type` enum whose `PHASE_TYPE_CHOICES` declares all
  three of `round_robin` / `tournament` / `member_night` now though only
  `round_robin` has behaviour, `uniq_season_phase_ordinal` on `(season,
  ordinal)`), migration `0041_season_phase` (`CreateModel`-only, dep
  `0040_tournament_random_draw`, **no `RunPython` / no backfill** — the
  [ADR-0004](docs/adr/0004-simulation-data-is-disposable.md) disposable-data
  precedent), and a **single chokepoint on `Season`**
  (`ordered_phases() -> list[SeasonPhase]` / `scheduled_fixtures() ->
  list[ScheduleFixture]`) that the whole Season read-path now routes through
  instead of inline `generate_schedule(...)` calls (`_is_finished`,
  `play_season_task`, `season_schedule`, `_build_dashboard_context`,
  `league_history` Play-Week preview, `team_schedule`). A Season with **zero**
  persisted phases falls back to an **implicit single `round_robin` phase** (a
  real but unsaved `SeasonPhase`, `pk is None`) — byte-identical to today; a new
  Season gets one explicit `round_robin` phase created inside the atomic block
  at `league_create` / `next_season`. `Season.schedule_format` stays as-is
  (legacy; the RR phase reads it). **Zero user-visible change**, **no simulator
  change / no RNG / no Score Calibration re-baseline**. Admin: `SeasonPhaseAdmin`.
  Seam contract:
  [`.claude/worktrees/lg-02-part2a-seam-contract.md`](.claude/worktrees/lg-02-part2a-seam-contract.md);
  app guide: `matches/CLAUDE.md` "LG-02-Part2a season phase foundation". Tests:
  `matches/tests/test_season_phase.py` (NEW) + extensions to
  `test_league_create.py` / `test_league_next_season.py` / `views_tests.py` /
  `test_league_play.py`.

- **LG-02-Part2b · [DONE] League-create "+" composer UI + per-phase format.**
  The create-League surface gained a vanilla-JS "**+** Add block" composer
  (LG-01d inline-`<script>` precedent) that writes **multiple ordered
  `SeasonPhase` rows** — the admin picks/orders `round_robin` / `tournament`
  blocks (e.g. RR → Tournament) instead of the single auto-created `round_robin`
  phase Part2a wrote. Landed **two dormant `SeasonPhase` columns**: a per-phase
  **`schedule_format`** (`CharField(32, null=True, blank=True)` — an RR phase
  copies `Season.schedule_format`, a tournament phase is `NULL`) so alternative
  regular-season formats can later land on the phase rather than the Season, and
  the forward **`SeasonPhase → Tournament` FK** (`SET_NULL`,
  `related_name="season_phases"`) — the column only, **ALWAYS NULL this slice**;
  the build / hand-off is Part2c. A **NEW pure module**
  `matches/phase_composer.py` (frozen `dataclasses` / `typing` allowlist,
  `TestNoDjangoImportsLeaked`-defended) parses the composer's comma-separated
  phase-type wire format into ordered `PhaseSpec(ordinal, phase_type,
  schedule_format)` via `parse_phase_composition(raw, *,
  season_schedule_format)` — empty input ⇒ a single RR default, ≥ 1 RR required,
  `member_night` not selectable, three exact `ValueError` strings. The
  `CreateLeagueForm` gained a hidden `phases` field whose `clean()` calls the
  parser and stashes `cleaned_data["phase_specs"]`; both creation sites loop over
  the specs — `league_create` (~553) from the composer, `next_season` (~1942)
  by **carrying the previous Season's composition forward** verbatim (with
  `tournament=None`). Migration `0042_seasonphase_format_tournament` (dep
  `0041_season_phase`, two `AddField`, no `RunPython` —
  [ADR-0004](docs/adr/0004-simulation-data-is-disposable.md)).
  `SeasonPhaseAdmin.list_display` extended with the two new columns. **Read-path
  UNCHANGED** — the Part2a chokepoint still plays the **first `round_robin`
  phase** via `Season.schedule_format` and ignores the rest; **no simulator
  change / no RNG / no Score Calibration re-baseline**. Seam contract:
  [`.claude/worktrees/lg-02-part2b-seam-contract.md`](.claude/worktrees/lg-02-part2b-seam-contract.md);
  app guide: `matches/CLAUDE.md` "LG-02-Part2b create-league phase composer";
  [ADR-0023](docs/adr/0023-season-phase-composable-structure.md). Tests:
  `matches/tests/test_phase_composer.py` (NEW) + extensions to
  `test_season_phase.py` / `test_league_create.py` / `test_league_next_season.py`.

- **LG-02-Part2c-1 · [DONE] RR → single-elimination playoff embed.** The first
  slice of Part2c — a thin orchestration layer that takes a Season composed of an
  ordered `round_robin` phase then a `tournament` phase, plays the regular season,
  **auto-builds** a standings-seeded single-elimination playoff bracket the moment
  the RR phase completes (matchups visible **before** any playoff click), then
  drains the bracket to crown the **Season champion**. Replaces Part2b's
  "play the first `round_robin` phase only" read-path with a **phase cursor** +
  two **lifecycle hooks** on `Season`. **Cursor / completion:**
  `Season.current_phase() -> SeasonPhase | None` returns the first INCOMPLETE
  phase by ordinal (`None` when all complete); completion is **derived**, not
  stored (no `SeasonPhase.state`) via the private `Season._phase_complete(phase)`
  — RR ⇔ the existing `_is_finished()` all-fixtures-played check, tournament ⇔
  `phase.tournament_id is not None AND phase.tournament.state == "completed"`.
  **Auto-build:** `Season.activate_pending_tournament_phase()`
  (`@transaction.atomic`, idempotent) fires when the cursor reaches an unbuilt
  `tournament` phase whose preceding RR phase is complete — it creates a
  `Tournament(format="single_elimination", team_assembly="preset", state="setup",
  name=f"{season.name} Playoffs")`, seeds **one `TournamentParticipant` per season
  team from the preceding phase's Standings** (`seed = StandingsRow.rank`, rank 1 →
  seed 1), wires `phase.tournament`, and calls `tournament.lock_and_build()`
  (setup → active; bracket built). **Completion rewrite:**
  `Season.complete_if_finished()` (REWRITTEN, `@transaction.atomic`) now gates on
  the **FINAL phase** (last ordinal) being complete and stamps the champion from
  that phase's type — `phase.tournament.champion` for a tournament final,
  `compute_standings(...)[0]` for an RR final (via
  `_stamp_champion_for_final_phase`, which supersedes the removed `_stamp_champion`);
  a single-RR-phase Season (and the implicit phase-less fallback) stays
  **byte-identical** to today. **Post-round hook:**
  `simulate_scheduled_round` calls `season.activate_pending_tournament_phase()`
  **then** `season.complete_if_finished()` after persistence in both the Round-1
  and Round-2 branches (ordering load-bearing — build before complete-check so the
  Season doesn't prematurely complete the instant the last RR fixture lands).
  **Play actions:** RR-scoped play (`play_week` / `play_two_months` /
  `play_until_end`) is behaviourally **UNCHANGED**; only the terminal play-dropdown
  label flips **"Until End of Season" → "Until Playoffs"** when a tournament phase
  follows (`has_following_tournament_phase`, label text only). Two NEW views drain
  the bracket: `play_single_round` (sync POST, one bracket node/Match via
  `play_next_node`, 302 redirect) and `play_playoffs` (async POST → 202 `{job_id,
  season_id}`, 409 / 405) backed by Celery task `play_playoffs_task`
  (`@shared_task(bind=True, name="matches.play_playoffs")`, returns
  `{"completed", "total"}` STAGE counts from `matches.bracket.stage_progress`,
  drains via `while play_next_node(...) is not None`); polling **reuses** the
  LG-01d `play_status` view / `_build_play_status_response` verbatim. **Compose
  guard:** `parse_phase_composition` gains one rule — a `tournament` phase requires
  a **preceding** `round_robin` phase (`ValueError("a tournament phase requires a
  preceding round-robin phase")`, fired after the zero-RR check). **Dashboard /
  template:** `_build_dashboard_context` gains four keys (`playoff_phase_active` /
  `playoff_tournament_id` / `playoff_completed` / `has_following_tournament_phase`)
  computed from `current_phase()`; both the season and league dashboards render a
  playoff button group (Play Single Round + Play Playoffs, only when
  `playoff_phase_active`) and a **"View bracket"** link to the existing
  `/tournaments/<id>/` page (when `playoff_tournament_id is not None`, do NOT embed
  the bracket). **Tournament Matches stay `season=NULL`** (the tournament engine is
  consumed verbatim — decision #3): **NO `Match.season_phase` FK, NO Match
  migration, no re-baseline, no simulator/engine change** this slice. Seam
  contract:
  [`.claude/worktrees/lg-02-part2c-1-seam-contract.md`](.claude/worktrees/lg-02-part2c-1-seam-contract.md);
  app guide: `matches/CLAUDE.md` "LG-02-Part2c-1 RR → single-elimination playoff
  embed"; [ADR-0023](docs/adr/0023-season-phase-composable-structure.md) (extended
  with a "Part2c-1 consequences" addendum). Tests: `matches/tests/test_season_playoff.py`
  (NEW) + extensions to `test_phase_composer.py` / `test_season_phase.py`.

- **LG-02-Part2c-2 · [DONE] Multi-RR play loop + `Match.season_phase` FK +
  cross-phase matchday offsetting (the Part2c SPINE).** Generalises the Part2c-1
  single-RR-then-single-elim path into a **multi-round-robin** Season: the
  supported + tested composition is **one-or-more `round_robin` phases then an
  OPTIONAL trailing `tournament`** (RR1→RR2, RR1→RR2→playoff). A thin orchestration
  slice — no simulator mechanics change, no tournament-engine change, no
  composer/form/template change, **no Score Calibration re-baseline**; legacy
  phase-less and single-RR Seasons stay **byte-identical**. **`Match.season_phase`
  FK + migration `0043`:** a new optional FK on `Match`
  (`models.ForeignKey("matches.SeasonPhase", null=True, blank=True,
  on_delete=models.SET_NULL, related_name="matches")`) mirroring `Match.season`;
  RR Matches now carry **both** `season=<season>` **and** `season_phase=<rr phase>`
  while tournament/playoff Matches (and legacy phase-less Seasons) stay
  `season_phase=NULL`. Migration `0043_match_season_phase` (dep
  `0042_seasonphase_format_tournament`) is a **single `AddField`, NO `RunPython` /
  NO backfill** ([ADR-0004](docs/adr/0004-simulation-data-is-disposable.md)
  posture). **By-phase fixture seam + global-continuous matchday offset:** NEW
  `Season.scheduled_fixtures_by_phase() -> list[tuple[SeasonPhase,
  list[ScheduleFixture]]]` offsets phase k's fixtures by the sum of all prior RR
  phases' matchday spans (one monotonic 1..N calendar);
  `Season.scheduled_fixtures()` is REWRITTEN as the flat concatenation of those
  offset fixtures (byte-identical for single-RR / phase-less). **Per-phase RR
  completion:** `Season._phase_complete` routes a *persisted* RR phase through a NEW
  `_rr_phase_complete` (scoped `match__season_phase=phase`) while the *implicit*
  `pk is None` fallback keeps the whole-season `_is_finished()` path — so the cursor
  finishes RR1 before RR2 opens; `_final_standings_for_phase` stays whole-season so
  **Standings are cumulative across RR phases** (a trailing playoff seeds from the
  cumulative leader). **Phase-aware find-or-create:** `simulate_scheduled_round`
  gains keyword-only `season_phase=None`; the Side-agnostic key becomes
  `(season, season_phase, frozenset({team ids}))` so identical pairings in different
  RR phases are distinct Matches (post-round hooks UNCHANGED). **Phase-aware
  Django-free helpers:** `select_play_fixtures` / `find_next_matchday` carry
  `(phase_id, fixture)` pairs + 3-tuple `(phase_id, frozenset, round_number)` keys
  via PLAIN INT phase-ids (`TestNoDjangoImportsLeaked` still passes);
  `find_next_fixture` / `round_progress` stay on the flat 2-tuple dashboard shape.
  **Play-loop wiring:** `play_season_task` (`matches/tasks.py`) and `play_week`
  (`matches/league_views.py`) iterate by-phase, build phase-aware `played_keys`, and
  pass `season_phase=phase_by_id.get(phase_id)`; `play_two_months` /
  `play_until_end` UNCHANGED. **Composer UNCHANGED** (`parse_phase_composition`
  already permits multiple `round_robin` tokens; the Part2c-1
  tournament-must-follow-RR guard stays). Seam contract:
  [`.claude/worktrees/lg-02-part2c-2-seam-contract.md`](.claude/worktrees/lg-02-part2c-2-seam-contract.md);
  app guide: `matches/CLAUDE.md` "LG-02-Part2c-2 multi-round-robin season";
  [ADR-0023](docs/adr/0023-season-phase-composable-structure.md) (extended with a
  "Part2c-2 consequences" addendum). CONTEXT.md **Matchday** / **Season phase**
  entries carry the behavioural touch-ups (no new domain term).

- **LG-02-Part2c-3a · [DONE] First alternative regular-season format —
  `double_round_robin` + `Match.leg` (Part2b `schedule_format` column wired
  end-to-end).** The first sub-slice of the re-sliced Part2c-3. Lands the **first
  alternative regular-season `schedule_format`** — **`double_round_robin`** — as a
  single `SeasonPhase` format, wiring the Part2b dormant per-phase `schedule_format`
  column **end-to-end** for the first time. A `double_round_robin` phase has every
  enrolled pair meet **twice within one phase** as **two distinct Matches**,
  discriminated by a NEW **`Match.leg`** field; `single_round_robin`, legacy
  phase-less Seasons, and all tournament Matches stay **`leg=1` ⇒ byte-identical**.
  A **thin orchestration slice** — no simulator mechanics change, no RNG change, no
  tournament-engine change, **no Score Calibration re-baseline**. **`Match.leg`
  field + migration `0044`:** `leg = models.PositiveSmallIntegerField(default=1)` on
  `Match` (after `season_phase`); migration `0044_match_leg` (dep
  `0043_match_season_phase`) is a **single `AddField`, NO `RunPython` / NO backfill**
  ([ADR-0004](docs/adr/0004-simulation-data-is-disposable.md) posture — existing rows
  take `default=1`). **Schedule generation:** `ScheduleFixture` gains a trailing
  `leg: int = 1` (appended LAST, keyword-constructed everywhere ⇒ equality-identical
  to existing constructions when defaulted); `SCHEDULE_FORMATS = ("single_round_robin",
  "double_round_robin")`; `generate_schedule(team_ids, "double_round_robin")` returns
  the single-RR fixture list (`leg=1`, matchdays `1..2*(n-1)`) **CONCATENATED** with
  the same fixtures re-emitted at **`leg=2`** with matchday **offset by `2*(n-1)`**
  (one monotonic `1..4*(n-1)` calendar, leg 2 sequentially after leg 1), final-sorted
  by `(matchday, team_a_id)`; the module stays Django-free, `single_round_robin`
  output byte-identical. **Phase-aware find-or-create:** `simulate_scheduled_round`
  gains keyword-only **`leg: int = 1`** (appended LAST) and the key becomes
  **`(season, season_phase, frozenset({team ids}), leg)`** so the two legs of a
  pairing are distinct Matches (post-round hooks UNCHANGED; `leg=1` collapses to
  today's key plus a constant). **Leg threading:** `_is_finished` /
  `_rr_phase_complete` played-keys + fixture-compare keys gain `leg` (a double-RR
  phase now requires **both** legs of every pairing before completing);
  `_final_standings_for_phase` UNCHANGED (cumulative — both legs are distinct Matches
  in the whole-season corpus); the Django-free pure helpers gain `leg`
  (`select_play_fixtures` / `find_next_matchday` → 4-tuple
  `(phase_id, frozenset, round_number, leg)`; FLAT `find_next_fixture` /
  `round_progress` → 3-tuple `(frozenset, round_number, leg)`, REQUIRED because a
  double-RR phase holds the same `(pair, round_number)` twice); the play-loop wiring
  (`play_season_task` / `play_week`) and the three FLAT overlay sites
  (`_build_dashboard_context` / `season_schedule` / `team_schedule`) build
  leg-bearing `played_keys` from `gr.match.leg` and pass `leg=fixture.leg`;
  `scheduled_fixtures_by_phase`'s offset re-construction carries `leg=f.leg` through.
  **Composer:** the per-token wire format extends from phase-**TYPE** tokens to
  **`type[:format]`** tokens (`"round_robin:double_round_robin,tournament"`); a bare
  `round_robin` defaults to `single_round_robin` (Part2b serialized values parse
  unchanged); `tournament` carries no format (`PhaseSpec.schedule_format=None`);
  `parse_phase_composition` reads the per-token format into `PhaseSpec.schedule_format`
  and raises a NEW `ValueError(f"unknown schedule_format: {fmt!r}")` for an
  unsupported format (existing `ValueError` strings preserved verbatim; `PhaseSpec`
  shape unchanged). The composer template gains a `double_round_robin` `<select>`
  option and serializes each RR row as `round_robin:<format>`; **all Part2b DOM ids
  unchanged**. **`next_season` is a NO-OP** (its Part2b carry-forward already copies
  each phase's `schedule_format` verbatim). **Backward-compat:** `single_round_robin`
  / legacy / tournament / playoff all stay `leg=1`, byte-identical; bare
  `round_robin` token ⇒ `single_round_robin`. **No re-baseline** — extend
  [ADR-0023](docs/adr/0023-season-phase-composable-structure.md) (Part2c-3a
  consequences addendum, no new ADR). **Scope-out (the c-3b…c-3f remainder below):**
  per-phase seeding-mode field; mid-season tournaments; per-tournament-block config;
  non-single-elim finals embeds; season-linked playoff Match history; weekly playoff
  pacing. Seam contract:
  [`.claude/worktrees/lg-02-part2c-3a-seam-contract.md`](.claude/worktrees/lg-02-part2c-3a-seam-contract.md);
  app guide: `matches/CLAUDE.md` "LG-02-Part2c-3a double round-robin regular-season
  format". Tests: extensions to `test_schedule_generator.py` / `test_phase_composer.py`
  / `test_league_play.py` / `test_season_multi_rr.py` / `test_league_create.py` /
  `test_season_dashboard_logic.py`.

- **LG-02-Part2c-3b · [DONE] Per-phase `tournament_mode` field on `SeasonPhase`
  (dormant).** Carried over from the LG-02-Part2b grill (2026-06-05). Part2b
  captures ordered phase *types* only; Part2c-1/Part2c-2/Part2c-3a hardcode
  standings-rank-seeded, season-ending. A `tournament` phase has **two flavours by
  Season role**: a **season-ending tournament** (playoff / closer) is **seeded from
  the preceding phase's Standings** and *requires* a preceding fixture-producing
  phase (the only flavour built so far); a **mid-season tournament** needs **no
  preceding Standings** — seeded by *expected team strength*, by a *random seed* of
  the preset teams, or drawn from a *player pool* — and may sit anywhere, including
  first. This slice lands the field that captures the distinction as a **fully
  dormant** addition (the `member_night` declared-but-inert precedent): a NEW
  **`SeasonPhase.tournament_mode`** `CharField(max_length=16, default="standings")`
  whose `TOURNAMENT_MODE_CHOICES` declares all four values now —
  **`standings`** (season-ending: from Standings), **`strength`** (mid-season: by
  team strength), **`unseeded`** (mid-season: random seed of the preset teams), and
  **`random_draw`** (mid-season: drawn pool → RR→DE, reusing the LG-02x-1
  `team_assembly="random_draw"` machinery). **`unseeded` ≠ `random_draw`** —
  unseeded randomly seeds the season's *existing preset teams*, random_draw builds
  *fresh balanced teams from a pool*. Migration `0045_seasonphase_tournament_mode`
  (dep `0044_match_leg`, single `AddField`, **no `RunPython` / no backfill** —
  [ADR-0004](docs/adr/0004-simulation-data-is-disposable.md); existing
  standings-playoff phases inherit `default="standings"`). The field is **threaded
  through the seam** but **always `"standings"` this slice**: the pure
  `PhaseSpec` (matches/phase_composer.py) gains a trailing
  **`tournament_mode: str = "standings"`** (appended LAST with a default ⇒ existing
  keyword constructions stay equality-identical, the c-3a `ScheduleFixture.leg`
  precedent) — but the **wire format is UNCHANGED** (the mode is **not** parsed
  from the wire; a `tournament:<mode>` token still raises `"malformed phase
  composition"`, reserving the `:` syntax for the c-3c picker); both
  `SeasonPhase`-creation sites (`league_create` / `next_season`) stamp
  `tournament_mode=spec.tournament_mode` / `=src.tournament_mode` so the
  carry-forward is **forward-compatible for c-3c** (a non-default mode set on a
  source phase reproduces across seasons). **Compose-time validity rule
  UNCHANGED** — the `standings`-requires-a-preceding-RR rule is already enforced
  for every `tournament` block by the existing blanket `parse_phase_composition`
  preceding-RR guard. **`activate_pending_tournament_phase` UNCHANGED** (still
  hardcodes standings-seeding; the default already matches, so byte-identical);
  read-path / simulator / RNG UNCHANGED, **no Score Calibration re-baseline**.
  `SeasonPhaseAdmin.list_display` gains `tournament_mode`. Extends
  [ADR-0023](docs/adr/0023-season-phase-composable-structure.md) (Part2c-3b
  consequences addendum, no new ADR); CONTEXT.md **Season phase** entry carries the
  `tournament_mode` vocabulary (+ the stale Part2c-2 → Part2c-3b fix). **Scope-out
  (→ c-3c):** the composer picker / `tournament:<mode>` wire token, the guard
  relaxation that lets a mid-season tournament sit anywhere, and the differential
  strength/unseeded/random_draw build. Seam contract:
  [`.claude/worktrees/lg-02-part2c-3b-seam-contract.md`](.claude/worktrees/lg-02-part2c-3b-seam-contract.md);
  app guide: `matches/CLAUDE.md` "LG-02-Part2c-3b per-phase tournament_mode field".
  Tests: extensions to `test_season_phase.py` / `test_phase_composer.py` /
  `test_league_create.py` / `test_league_next_season.py`.

- **LG-02-Part2c-3c · [DONE] Mid-season tournaments.** A `tournament` phase that
  sits **between two `round_robin` phases** (or first), not as the season closer —
  the mid-season flavour the c-3b seeding-mode field unlocks (no preceding
  Standings; may sit anywhere). Ships the **`strength`** + **`unseeded`** mid-season
  build (**`random_draw` still DEFERRED** — see the follow-up below); turns the c-3b
  dormant `tournament_mode` field live for those two modes. **Wire token:** the
  `tournament` composer token becomes **`tournament[:mode]`** (`parse_phase_composition`
  splits each token on the first `:`; for a `tournament` token the format-part is the
  **mode**, defaulting to `standings`) with a NEW locked
  `ValueError(f"unknown tournament_mode: {mode!r}")` for `random_draw` or any unknown
  string (every pre-existing `ValueError` string preserved verbatim). **Guard
  relaxation:** the **≥1-round-robin** rule is kept verbatim; the
  `"a tournament phase requires a preceding round-robin phase"` string is preserved
  but now fires **ONLY** for a `standings`-mode tournament — a `strength` / `unseeded`
  phase may sit **anywhere, including first**, and a mid-season `standings`
  tournament is allowed (there is no "standings-must-be-final" rule). **Build
  differential** (`Season.activate_pending_tournament_phase` generalises the gate; a
  NEW private `Season._seed_order_for_phase(phase) -> list[int]` branches on
  `tournament_mode`): `standings` → preceding-phase Standings rank order (byte-identical
  to today); `strength` → `bracket.default_seed_order([(tid, mean_overall_rating)])`
  over the season's starting teams (DESC mean, ASC id tiebreak); `unseeded` → a fresh
  `random.Random()` shuffle of the starting team ids (non-deterministic, NOT the
  SIM-07 seed chain). The **shared build tail is mode-independent** — `seed = position
  + 1` (byte-identical to today's `seed=row.rank` for `standings`), name
  `f"{self.name} Playoffs"` for `standings` else `f"{self.name} Tournament"`, then
  `lock_and_build()`. **Build trigger:** `Season.start_season` gains an
  `activate_pending_tournament_phase()` call **inside** its existing
  `@transaction.atomic` block (after the snapshot writes + `state="active"`), so a
  FIRST-phase mid-season tournament builds the instant the Season activates; the
  existing post-round hook still covers the mid-season-after-RR case (the method is
  idempotent). **Play-loop barrier:** a NEW
  `Season.playable_fixtures_by_phase()` (filters `scheduled_fixtures_by_phase()` to RR
  phases whose ordinal is strictly **less** than the first incomplete `tournament`
  phase's ordinal, via a NEW private `_tournament_barrier_ordinal()`) halts the RR
  loop at an incomplete mid-season tournament phase so the bracket — built by the hook
  — drains through the EXISTING `play_single_round` / `play_playoffs` views before
  later RR phases play; once the tournament completes the barrier advances and the
  later RR phases become playable. Two one-line play-loop swaps
  (`tasks.py::play_season_task`, `league_views.py::play_week`:
  `scheduled_fixtures_by_phase()` → `playable_fixtures_by_phase()`); `play_two_months`
  / `play_until_end` enqueue `play_season_task` UNCHANGED. **Dashboard label split:**
  the terminal play button reads **"Until Playoffs"** when the following tournament is
  the FINAL phase, **"Until Tournament"** when it is mid-season (a new
  `following_tournament_is_final` context bool computed alongside `_playoff_cursor_keys`,
  touching both `templates/seasons/dashboard.html` and `templates/leagues/dashboard.html`);
  the playoff button-group DOM ids + `play_until_end` action are UNCHANGED (visible
  label text only). **Composer:** a tournament composer row gains a mode `<select>`
  with locked DOM id **`league-create-phase-mode-{i}`** (options `standings` /
  `strength` / `unseeded`; `random_draw` a DISABLED "coming soon" — the `member_night`
  precedent), shown for `tournament` rows only, with `serialize()` emitting
  `tournament:<mode>`. **NO migration** (`tournament_mode` exists from c-3b);
  read-path purity preserved (`matches/season_dashboard.py` untouched —
  `TestNoDjangoImportsLeaked` stays green); simulator / RNG / tournament engine
  consumed verbatim, **no Score Calibration re-baseline**. Extends
  [ADR-0023](docs/adr/0023-season-phase-composable-structure.md) (Part2c-3c
  consequences addendum, no new ADR); the CONTEXT.md **Season phase** + **Matchday**
  entries carry the build-now / barrier-drain domain language. **Follow-up
  (deferred):** the mid-season **`random_draw`** build (drawn pool → RR→DE, reusing
  the LG-02x-1 `team_assembly="random_draw"` machinery) — the parser rejects it with a
  ValueError and the composer offers it as a disabled "coming soon" option only. Seam
  contract:
  [`.claude/worktrees/lg-02-part2c-3c-seam-contract.md`](.claude/worktrees/lg-02-part2c-3c-seam-contract.md);
  app guide: `matches/CLAUDE.md` "LG-02-Part2c-3c mid-season tournaments". Tests:
  extensions to `test_phase_composer.py` / `test_season_phase.py` /
  `test_season_playoff.py` / `test_league_create.py` / `test_season_dashboard_logic.py`.

- **LG-02-Part2c-3d · [DONE] Per-tournament-block configuration.** Surfaces
  per-`tournament`-block config (format + top-N cut) on `SeasonPhase`, building the
  **cut** but holding the **format** dormant (flipped live in c-3e). A **pure
  orchestration/config slice** — no simulator mechanics change, no RNG change, no
  tournament-engine change (`play_next_node` / `lock_and_build` untouched), **no
  Score Calibration re-baseline**; `tournament_cut=0` (the default) is
  **byte-identical to today** (full participant set). **Two new `SeasonPhase`
  columns + migration `0046`:** **`tournament_format`** —
  `CharField(max_length=32, choices=TOURNAMENT_FORMAT_CHOICES, default="single_elimination")`,
  **DORMANT** (written-but-unread; the build still hardcodes
  `format="single_elimination"` — an admin-set `tournament_format="swiss"` still builds
  single-elim, a known ACCEPTABLE foot-gun until c-3e) — and **`tournament_cut`** —
  `PositiveSmallIntegerField(default=0)`, **LIVE** (`0` = no cut = all enrolled teams).
  `TOURNAMENT_FORMAT_CHOICES` is INLINED on `SeasonPhase` (5 tuples mirroring
  `Tournament.FORMAT_CHOICES` byte-for-byte — `single_elimination` / `double_elimination`
  / `round_robin` / `round_robin_double_elim` / `swiss`; the `→` U+2192 label) because
  `Tournament` is defined later in the file. Migration `0046_seasonphase_format_cut`
  (dep `0045_seasonphase_tournament_mode`) is **2× `AddField`** (`tournament_format` then
  `tournament_cut`), **NO `RunPython` / NO backfill**
  ([ADR-0004](docs/adr/0004-simulation-data-is-disposable.md) posture — existing
  tournament phases inherit `single_elimination` + cut `0`). **Build cut slice
  (`Season.activate_pending_tournament_phase`):** ONE inserted guard —
  `if phase.tournament_cut: order = order[:phase.tournament_cut]` — sits AFTER
  `order = self._seed_order_for_phase(phase)` and BEFORE the existing
  `if not order: return`, keeping the top `cut` seeds of the already-ordered (any-mode)
  seed vector with dense seeds `1..cut`; `cut > len(order)` is a Python no-op slice (all
  teams). `_seed_order_for_phase` is **BYTE-IDENTICAL** — the cut applies to its OUTPUT at
  the caller, never inside it; the build's `format="single_elimination"` /
  `team_assembly="preset"` / `seed = position + 1` tail STAYS hardcoded. **Wire grammar
  `tournament[:mode[:cut]]`:** the tournament branch of `parse_phase_composition` switches
  from `partition(":")` to `split(":")` (RR branch UNCHANGED, still
  `round_robin[:schedule_format]` via `partition`) — `parts[0]` type, `parts[1]` mode
  (default `standings`), `parts[2]` cut string (default `"0"`); `len(parts) > 3` / empty
  cut / non-int cut ⇒ the existing `"malformed phase composition"`; a NEW locked
  `ValueError(f"tournament cut must be 0 or at least 4: {cut}")` fires when
  `cut != 0 and cut < 4` (floor `{0} ∪ {≥4}`); every pre-existing `ValueError` string
  (incl. `f"unknown tournament_mode: {mode!r}"`) is preserved VERBATIM and the module
  stays Django-free. `PhaseSpec` gains a trailing **`tournament_cut: int = 0`** (appended
  LAST with a default ⇒ existing keyword constructions stay equality-identical, the c-3a
  `leg` / c-3b `tournament_mode` precedent). **Validation is PARSER-ONLY** — no
  `Season.clean()` / `SeasonPhase.clean()` guard; a `cut` leaving `< 4` participants at
  runtime is caught defence-in-depth by the EXISTING `lock_and_build` ≥4-participant
  `ValidationError`. **Creation / carry-forward (`matches/league_views.py`):**
  `league_create` adds `tournament_cut=spec.tournament_cut` (does NOT set
  `tournament_format` — there is no `PhaseSpec.tournament_format`; the column default
  applies); `next_season` carries forward **BOTH** `tournament_cut=src.tournament_cut`
  AND `tournament_format=src.tournament_format` verbatim (the persisted source row has
  both real columns). **Composer (`templates/leagues/create.html`):** a tournament row
  gains a cut `<input type="number" min="0">` (DOM id **`league-create-phase-cut-{i}`**,
  class `phase-cut-input`, default value `0`, tournament-rows-only) wired to
  `serialize()`, plus a DISABLED tournament-format `<select>` (DOM id
  **`league-create-phase-tournament-format-{i}`**, DISTINCT from the RR
  `league-create-phase-format-{i}`, single option "Single elimination (more formats
  coming soon)") that serializes NOTHING — the `phase-tournament-pending` /
  disabled-`random_draw` placeholder precedent. `serialize()` emits
  `tournament:<mode>:<cut>` for a tournament row (RR row `round_robin:<format>`
  unchanged); all Part2b / c-3a / c-3c DOM ids unchanged. **Admin
  (`matches/admin.py`):** `SeasonPhaseAdmin.list_display` appends `"tournament_format"`,
  `"tournament_cut"`. **Backward-compat:** bare `tournament` / `tournament:strength` wire
  tokens parse identically to c-3c (mode resolved, cut `0`); every Part2b / c-3a / c-3c
  serialized value parses unchanged. Extends
  [ADR-0023](docs/adr/0023-season-phase-composable-structure.md) (Part2c-3d consequences
  addendum, no new ADR); the CONTEXT.md **Season phase** entry carries the
  config-split vocabulary. **Scope-out (→ c-3e):** the LIVE format picker + per-format
  sub-config + the non-single-elim build that READS `tournament_format`; `team_assembly`
  is subsumed by the deferred `tournament_mode="random_draw"`. Seam contract:
  [`.claude/worktrees/lg-02-part2c-3d-seam-contract.md`](.claude/worktrees/lg-02-part2c-3d-seam-contract.md);
  app guide: `matches/CLAUDE.md` "LG-02-Part2c-3d per-tournament-block configuration".
  Tests: extensions to `test_phase_composer.py` / `test_season_phase.py` /
  `test_season_playoffs.py` / `test_league_create.py` / `test_league_next_season.py`.

- **LG-02-Part2c-3e · [DONE] Non-single-elim finals embeds.** Flips the dormant
  c-3d `SeasonPhase.tournament_format` column **dormant→live** so a Season
  `tournament` phase builds via ANY of the **five** formats
  (`single_elimination` / `double_elimination` / `round_robin` /
  `round_robin_double_elim` / `swiss`) with **full per-format sub-config parity**
  with the standalone `tournament_create` form. A **thin orchestration/config
  slice** — the standalone Tournament engine already builds + drains all five
  formats, consumed VERBATIM — no simulator mechanics change, no RNG change, no
  tournament-engine change (`lock_and_build` / `play_next_node` untouched); the
  tournament sims are **non-deterministic** so there is **no Score Calibration
  re-baseline** (the `unseeded`-shuffle precedent). The SE-default (series `1`,
  advancers `0`) build stays **byte-identical to Part2c-1**. **7 new `SeasonPhase`
  columns + migration `0047`:** appended after `tournament_cut` —
  `final_series_length` / `semifinal_series_length` / `quarterfinal_series_length`
  / `earlier_series_length` (`PositiveSmallIntegerField`, choices
  `((1,"Best of 1"),(3,"Best of 3"),(5,"Best of 5"))`, default `1`) +
  `wb_advancers` / `lb_advancers` / `swiss_rounds`
  (`PositiveSmallIntegerField`, no choices, default `0`) — each **mirroring
  `Tournament`'s same-named field byte-for-byte**, the series choices **INLINED on
  `SeasonPhase`** (NOT referencing `Tournament.*`; `Tournament` is declared later
  in the file — the c-3b/c-3d inlined-choices precedent). `tournament_format` (the
  c-3d column) flips **dormant→live** with **no schema change to it** (only its
  *consumption* in the build changes). Migration `0047_seasonphase_tournament_subconfig`
  (dep `0046_seasonphase_format_cut`) is **7× `AddField`**, **NO `RunPython` / NO
  backfill** ([ADR-0004](docs/adr/0004-simulation-data-is-disposable.md) posture —
  existing tournament phases inherit `single_elimination` + series `1` + advancers
  `0`); `tournament_format` was already migrated by c-3d's `0046` (no `AlterField`).
  **Build (`Season.activate_pending_tournament_phase`):** **ONE changed**
  `Tournament.objects.create(...)` — `format=phase.tournament_format` plus the 7
  sub-config kwargs from the phase (`final_series_length`,
  `semifinal_series_length`, `quarterfinal_series_length`, `earlier_series_length`,
  `wb_advancers`, `lb_advancers`, `swiss_rounds`); everything else — the
  idempotency/gate guards, the c-3d cut slice (`order = order[:phase.tournament_cut]`),
  `_seed_order_for_phase` (**BYTE-IDENTICAL**, NOT edited), `seed = position + 1`,
  the `"{name} Playoffs"` (standings) / `"{name} Tournament"` (mid-season) name,
  `team_assembly="preset"`, `state="setup"`, `lock_and_build()` — is **UNCHANGED**.
  `lock_and_build` already dispatches on `self.format` for all five formats and
  consumes the 7 sub-config fields (series tiers via
  `series_length_for_round`/`series_length_for_depth` → `_persist_elim_specs`; wb/lb
  for RR→DE via `build_de_finals_if_rr_finished`; `swiss_rounds` for Swiss) — no
  engine edit. **PhaseSpec gains 8 trailing defaulted fields**
  (`tournament_format="single_elimination"`, the 4 series tiers `=1`,
  `wb_advancers=0`, `lb_advancers=0`, `swiss_rounds=0`) appended LAST so every
  existing keyword construction stays equality-identical (the c-3a `leg` / c-3b
  `tournament_mode` / c-3d `tournament_cut` append-with-default precedent); the
  frozen import allowlist (`dataclasses` / `typing` ONLY) is UNCHANGED — no new
  import, `TestNoDjangoImportsLeaked` stays green. **Wire grammar extends from
  c-3d's `tournament[:mode[:cut]]` to a positional trailing-optional 11-field
  layout** `tournament:mode:cut:format:fsl:ssl:qsl:esl:wb:lb:swiss` (the tournament
  branch's `split(":")` widens the c-3d `> 3` malformed check to `> 11`; the RR
  branch is UNCHANGED); each new field is trailing-optional with its default
  (empty after strip ⇒ existing `"malformed phase composition"`). Three **NEW
  LOCKED** `ValueError`s: `f"unknown tournament_format: {fmt!r}"` (format ∉ the
  5-format embeddable set), `f"series length must be 1, 3, or 5: {n}"` (any tier ∉
  `{1,3,5}`), and `f"invalid wb/lb combo for round_robin_double_elim: {wb}/{lb}"`
  (the (wb,lb) combo validated **ONLY** when `format == "round_robin_double_elim"`,
  against the six locked combos `{(4,0),(4,2),(8,0),(8,4),(16,0),(16,8)}`; for any
  non-RR→DE format wb/lb are parsed-and-stored inert, mirroring how `Tournament`
  carries `0/0` there). Validation ORDER (locked): split → `> 11` malformed → mode
  membership → cut parse + floor → format membership → series tiers parse +
  `{1,3,5}` → wb/lb parse + RR→DE-only combo → swiss parse. Every pre-existing
  `ValueError` string is preserved VERBATIM; bare `tournament` and every c-3d/c-3c
  serialized token parse identically (the missing trailing fields take their
  defaults); the module stays **Django-free** (allowlist UNCHANGED, no `json`).
  **Validation posture mirrors c-3d — SHAPE at the parser** (format in set, series
  in `{1,3,5}`, wb/lb in the six combos), **COUNT/parity at `lock_and_build`**
  (defence-in-depth): the existing `ValidationError`s — `< 4` participants,
  `wb_advancers > n`, `wb_advancers + lb_advancers > n`, Swiss odd-N — catch a
  degenerate config at runtime, so **no new `Season.clean()` / `SeasonPhase.clean()`
  / form cross-field guard** is added. **Composer
  (`templates/leagues/create.html`):** the c-3d **disabled** tournament-format
  placeholder goes **LIVE** (5 options matching `SeasonPhase.TOURNAMENT_FORMAT_CHOICES`,
  DOM id `league-create-phase-tournament-format-{i}` now ENABLED), plus new
  per-format sub-config controls — 4 series-length selects (DOM ids
  `league-create-phase-{final,semifinal,quarterfinal,earlier}-sl-{i}`, Bo1/Bo3/Bo5,
  shown for SE/DE/RR→DE), a wb/lb combo select (`league-create-phase-rrde-combo-{i}`,
  six combo value-strings `4/0`…`16/8`, shown for RR→DE only, `serialize()` splits
  on `/` into wb+lb), and a swiss-rounds input (`league-create-phase-swiss-rounds-{i}`,
  `type="number" min="0" value="0"`, shown for Swiss only) — show/hidden by a
  type+format toggle mirroring the standalone `tournamentCreateToggle`.
  `serialize()` emits the full 11-field token for a tournament row (RR rows still
  emit `round_robin:<format>` unchanged); all prior Part2b / c-3a / c-3c / c-3d DOM
  ids preserved (the mode select, the cut input, `phase-tournament-pending`).
  **Creation / carry-forward (`matches/league_views.py`):** `league_create` sets
  all 8 new fields from `spec` (`tournament_format` now comes from `spec` — there
  IS a `PhaseSpec.tournament_format` this slice, so the c-3d "left to column
  default" note no longer applies); `next_season` carries all 8 forward from `src`
  verbatim; both inside their existing `@transaction.atomic` blocks. **Admin
  (`matches/admin.py`):** `SeasonPhaseAdmin.list_display` appends the 7 sub-config
  columns after `tournament_cut` (`tournament_format` already present from c-3d).
  **Backward-compat:** bare `tournament` / every c-3d/c-3c serialized token parse
  identically (trailing fields default); existing tournament phases inherit
  `single_elimination` + series `1` + advancers `0`; the SE-default build is
  byte-identical to Part2c-1. **UNCHANGED:** completion `_phase_complete`, champion
  `_stamp_champion_for_final_phase`, the drain views `play_single_round` /
  `play_playoffs` / `play_playoffs_task`, the full tournament engine
  (`lock_and_build` / `build_*_bracket` / `find_next_node` / `advance_winner` /
  `series_length_for_round` / `build_de_finals_if_rr_finished` / `advance_swiss_if_round_finished`),
  the simulator / RNG / `Match` model, the standalone `tournament_create.html`. **No
  re-baseline.** Extends
  [ADR-0023](docs/adr/0023-season-phase-composable-structure.md) (Part2c-3e
  consequences addendum, no new ADR); the CONTEXT.md **Season phase** entry carries
  the five-format + sub-config vocabulary. **Scope-out (→ c-3f):** the
  season-linked playoff Match-history surface + weekly playoff pacing; the
  mid-season `random_draw` build stays DEFERRED. Seam contract:
  [`.claude/worktrees/lg-02-part2c-3e-seam-contract.md`](.claude/worktrees/lg-02-part2c-3e-seam-contract.md);
  app guide: `matches/CLAUDE.md` "LG-02-Part2c-3e non-single-elim finals embeds".
  Tests: extensions to `test_phase_composer.py` / `test_season_phase.py` /
  `test_season_playoffs.py` / `test_league_create.py` / `test_league_next_season.py`.

- **LG-02-Part2c-3f · [DONE] Season-linked playoff Match history + weekly
  playoff pacing.** The final Part2c-3 slice — a **thin view/engine-layer**
  orchestration with **NO model change, NO migration, NO simulator/engine
  mechanics change, NO Score Calibration re-baseline**. (A) **Season-linked
  playoff history:** widened the Team History **Overall tab** corpus
  (`matches/league_screens/team_history.py::_build_overall_context`) from
  regular-season-only to a `.distinct()` UNION of regular-season rounds +
  **season-embedded playoff rounds** via the FK chain
  `match__series_match__node__tournament__season_phases__isnull=False` (the
  `season_phases` reverse-set guard separates a season playoff from a standalone
  *sandbox* Tournament — both carry `season=NULL` per Part2c-1 decision #3), and
  filled `playoff_appearances = Tournament.objects.filter(season_phases__isnull=
  False, participants__team=team).distinct().count()` into the existing
  `compute_overall_record(..., playoff_appearances=…)` kwarg (the pure
  `team_history_logic.py` + the `team_history.html` render slot already supported
  it — **assert-only, unchanged**). Players tab already counts playoff
  `PlayerRoundState` rows (one accepted limit: their `season_year` is `None`);
  Seasons-tab rank stays regular-season-only. (B) **Weekly playoff pacing:** new
  `matches/tournament_engine.py::play_next_bracket_round(tournament) -> int`
  (drain the lowest incomplete `(bracket_type, bracket_round)` STAGE to clinch via
  the VERBATIM per-Match-atomic `play_next_node`; return the nodes-clinched count)
  + phase-aware `play_week` (tournament cursor → one bracket STAGE +
  `complete_if_finished()` + 302; else the RR matchday path byte-identical) +
  phase-aware `play_season_task` tail (drain bracket STAGES on the shared
  `max_matchdays − rr_weeks_played` budget, `None` = drain to champion; PROGRESS
  switches to `stage_progress` STAGE-counts at the boundary). The **Playoffs
  League screen + registry/sidebar/url/re-export stay byte-unchanged** (embedded
  bracket, no game-log reshape); `play_single_round` / `play_playoffs` /
  `play_playoffs_task` untouched. The `member_night` phase type stays inert (see
  its own PLAN item). Seam contract:
  [`.claude/worktrees/lg-02-part2c-3f-seam-contract.md`](.claude/worktrees/lg-02-part2c-3f-seam-contract.md);
  impl notes in [`matches/CLAUDE.md`](laserforce_simulator/matches/CLAUDE.md).

### LG-06 · ZenGM league-screen parity polish

**Status: DONE — LG-06a through LG-06h all shipped.** The full ZenGM
league-screen parity polish set is complete; per-step implementation notes follow.

Follow-ups to the shipped LG-01z read-only screens, from the per-page comparison
against the reference product (LOL GM) in
[`docs/zengm-comparison/`](docs/zengm-comparison/) (see that folder's `README.md`
for methodology + the C1–C10 cross-cutting table; each step links its per-page
doc). All UI-only, read-only — no model change, no simulator touch. Lower
priority than LG-02..LG-05; sequence after the screens have real multi-season
data to justify the controls. Each step should go through its own grilling
session before implementation.

- **LG-06a · [DONE] Page-size selector + Team History pagination.** Add the standard
  10/25/50/100 page-size `<select>` (LG-01f `league_history` precedent) to
  **Free Agents**, **Player Ratings**, **Player Stats**; add pagination to
  **Team History** (currently unbounded — one row per player ever, no paging).
  Cross-cutting **C4**. Docs:
  [`free-agents.md`](docs/zengm-comparison/free-agents.md),
  [`player-ratings.md`](docs/zengm-comparison/player-ratings.md),
  [`player-stats.md`](docs/zengm-comparison/player-stats.md),
  [`team-history.md`](docs/zengm-comparison/team-history.md).
  - completed: the three rating/stats screens (Free Agents, Player Ratings,
    Player Stats) already paginated view-side — each already imported
    `_coerce_per_page` / `_coerce_page` and set `per_page` / `page_obj` /
    `paginator` — so LG-06a added only the page-size `<select>` UI (the LG-01f
    `history.html` precedent) to their templates plus a `per_page_options`
    context key fed from the shared `matches.league_views._LG01F_PER_PAGE_OPTIONS
    = (10, 25, 50, 100)` tuple (the single source; not hardcoded in any
    template). Team History, which had **no** pagination before, gained
    `Paginator` wiring on the **Players section only** (view + template) —
    `page_obj` / `paginator` / `players_querystring_without_page` (the latter
    carries `team_id` and omits `page`); the Overall and Seasons sections were
    left untouched. On every screen the per-page `<form>` preserves the other
    params via hidden inputs (`sort` + `dir` on the rating/stats screens,
    `team_id` on Team History) and omits `page` so a page-size change resets to
    page 1; the Team History team-picker form additionally gained a hidden
    `per_page` so switching team preserves the chosen page size. New DOM ids
    `<screen>-per-page-form` / `<screen>-per-page-select` plus
    `team-history-players-pagination`; `_coerce_per_page` / `_coerce_page` were
    reused verbatim (no new helpers). UI-only — no model, migration,
    CONTEXT.md, ADR, simulator, or score re-baseline. Seam contract at
    `.claude/worktrees/lg-06a-seam-contract.md`.
- **LG-06b · [DONE] Team filter.** Add an "All Teams" + per-enrolled-team filter
  `<select>` to **Player Ratings**, **Player Stats**, **Statistical Feats** (the
  team list is already enrolled-season-scoped on those views). Cross-cutting
  **C5**. Docs:
  [`player-ratings.md`](docs/zengm-comparison/player-ratings.md),
  [`player-stats.md`](docs/zengm-comparison/player-stats.md),
  [`statistical-feats.md`](docs/zengm-comparison/statistical-feats.md).
  - completed: all three screens gained an "All Teams" + per-enrolled-team
    `<select>` driven by `?team_id=<id>`, with a shared validator
    `matches.league_views._coerce_team_id(raw, enrolled_ids)` (mirrors
    `_coerce_per_page`; the single source imported by all three modules) that
    returns the int id iff it parses **and** is enrolled, else `None` (= All
    Teams, forgiving fallback). Each view sets `enrolled_teams`
    (`displayed_season.teams.order_by("name")`) + `selected_team_id`. Filter
    points differ per screen: Player Ratings filters the queryset
    (`qs.filter(team_id=selected)` after `_enrolled_player_queryset`); Player
    Stats filters the materialized rows post-`aggregate_player_stats` on
    `PlayerStatRow.team_id`; Statistical Feats filters the seam **inputs**
    before `stat_feats.scan_feats` (keep `player_rounds` where
    `team_id == selected`, keep `matches` where `selected in {red_team_id,
    blue_team_id}`) — `stat_feats.py` itself untouched. `team_id` is carried in
    both querystring helpers + a hidden per-page-form input on the two
    paginated screens (the picker form omits `page` so a team change resets to
    page 1); Statistical Feats has no pagination/sort. New DOM ids
    `{player-ratings,player-stats,statistical-feats}-team-filter-{form,select}`.
    UI-only, read-only — no model, migration, URL, simulator, CONTEXT.md, ADR,
    or score re-baseline. Cross-cutting **C5**. Seam contract at
    `.claude/worktrees/lg-06b-seam-contract.md`.
- **LG-06c · [DONE] Sortable columns on the remaining tables.** Bring the LG-00c
  `_coerce_sort` / `_coerce_dir` sort-header pattern (already used on Power
  Rankings / Free Agents / Player Ratings / Player Stats / Team Stats) to the
  five tables that lack it: **Team History**, **Game Log**, **League Leaders**,
  **Watch List**, **Statistical Feats**. Cross-cutting **C6**. Docs:
  [`team-history.md`](docs/zengm-comparison/team-history.md),
  [`game-log.md`](docs/zengm-comparison/game-log.md),
  [`league-leaders.md`](docs/zengm-comparison/league-leaders.md),
  [`watch-list.md`](docs/zengm-comparison/watch-list.md),
  [`statistical-feats.md`](docs/zengm-comparison/statistical-feats.md).
  - completed: the five screens (Team History, Game Log, League Leaders, Watch
    List, Statistical Feats) gained the LG-00c sortable-column-header pattern,
    sorting **view-side** with in-memory `sorted(key=…, reverse=(dir=="desc"))`
    on the already-materialized rows — the pure modules `stat_feats.py`,
    `team_history_logic.py`, and `league_leaders_logic.py` (incl. `LeaderRow`,
    whose `rank` stays the frozen metric standing) are UNTOUCHED, sorted on
    their OUTPUT. Sort-key coercion is the single new shared helper
    `matches.league_views._coerce_sort_key(raw, allowed, default)` (returns
    `raw` iff in the `allowed` frozenset, else `default`; mirrors
    `_coerce_per_page` / `_coerce_team_id`), with `teams.views._coerce_dir`
    imported and reused verbatim (no duplicate). Multi-table screens use
    NAMESPACED params so sorting one table never resets a sibling: Team History
    (`players_sort`/`players_dir`, `seasons_sort`/`seasons_dir`) and League
    Leaders (per-board `<board>_sort`/`<board>_dir` across all four boards
    `avg_tags`/`avg_score`/`fewest_tagged`/`tag_ratio`); single-table screens
    (Game Log, Watch List, Statistical Feats) use a single `?sort=&dir=`. On the
    LG-06a-paginated Team History Players table the sort runs BEFORE
    `Paginator` (so the global, not per-page, top row leads), with the extended
    `players_querystring_without_page` carrying `players_sort`/`players_dir` on
    pagination links and a sibling `players_querystring_without_sort_page`
    backing the headers so a sort change resets to page 1. Sort coexists with
    the existing `?team_id=` filters on Game Log and Statistical Feats (header
    hrefs carry `team_id`; team-picker forms carry `sort`/`dir` via hidden
    inputs). Team History's Overall tab (a single W-L-T `dl`) stays unsorted.
    Key tuples are `None`-safe (`(value is None, value)` so `None` sorts last
    in asc) with a per-screen deterministic secondary tiebreak. New DOM ids
    `<screen>[-<table>]-th-<key>` with the active header appending ` ↑`/` ↓`
    glyphs. UI-only, read-only — no model, migration, URL, simulator, RNG,
    CONTEXT.md, ADR, or score re-baseline. Cross-cutting **C6**. Seam contract
    at `.claude/worktrees/lg-06c-seam-contract.md`.
- **LG-06d · [DONE] Season selector + rate/career toggles.** Add a `?season=` selector
  (and, where it maps, ZenGM's Per Game / Per 36 / Totals + Career-Totals
  toggles) across the stats screens once leagues routinely span multiple
  Seasons — currently every screen renders only `displayed_season`. Cross-cutting
  **C1 / C2 / C7**. Lowest priority of the set. Doc:
  [`README.md`](docs/zengm-comparison/README.md) (cross-cutting table).
  - completed: a `?season=` selector landed on **6 screens** — Player Stats,
    Team Stats, League Leaders, Statistical Feats, Game Log, Power Rankings —
    listing each of this League's Seasons newest-first plus a **Career** entry
    (aggregate across all of THIS League's Seasons); no `?season=` param keeps
    the current `displayed_season` (backward-compatible). **Team History is
    excluded** — it is natively all-time and its own Seasons tab already is the
    per-season view, so a season selector would be redundant there. Two new
    shared coercers in `matches.league_views` mirror the `_coerce_per_page` /
    `_coerce_team_id` forgiving precedent: `_coerce_season(raw, valid_season_ids,
    default)` (returns the literal `"career"` sentinel iff `raw == "career"`,
    else the int id iff it parses **and** is in the valid set, else the caller's
    `default` = the `displayed_season` id or `None`) and `_coerce_rate(raw,
    default="total")` (one of the locked literals `"total"` / `"per_game"` /
    `"per_10"`, else default). Career is a **view-side queryset switch** — each
    screen swaps its round/match filter from `...match__season=<season>` to
    `...match__season__league=league` and reuses its existing pure aggregation
    module **verbatim** (`aggregate_player_stats`, `team_stats_logic`,
    `league_leaders_logic`, `stat_feats`, the Game Log in-view round-row build,
    `power_rankings_logic` are all indifferent to one-season vs. all-seasons).
    Player Stats additionally gained a `?rate=` toggle — Totals / Per Game /
    **Per 10 min** (the laser-tag analogue of ZenGM's Per-36) — via a new pure fn
    `matches.season_player_stats.apply_rate(rows, rate)` that transforms the
    summed count columns **only** (`SUMMED_KEYS`); MVP / Acc% / Tag Ratio /
    Survival pass through untouched. Per-10 denominator = the player's total
    uptime, `stats["survival"] * games` (survival is the per-Round mean
    survival-seconds, so ×games rebuilds the summed uptime), i.e.
    `count * 600 / (survival_mean * games)` with a `<= 0` → `0.0` guard; per-game
    = `value / games`. The Player Stats pipeline is `aggregate_player_stats` →
    `apply_rate` → `team_id` filter → `sort_player_stats` → `Paginator`, so the
    sort runs on the **rate-adjusted** displayed value. `season` (and `rate` on
    Player Stats) carries through every querystring helper, hidden per-page /
    team-filter form input, and sort-header href; changing `season` or `rate`
    omits `page` to reset to page 1 (LG-06a/b/c precedent). New DOM ids
    `<screen>-season-filter-{form,select}` (prefixes `player-stats`, `team-stats`,
    `league-leaders`, `statistical-feats`, `game-log`, `power-rankings`) plus
    `player-stats-rate-{form,select}`. UI-only, read-only — no model, migration,
    simulator, RNG, or Score Calibration re-baseline; CONTEXT.md was edited (the
    **Per-10-minute rate** + **Career view (league-scoped)** terms); no ADR.
    Cross-cutting **C1 / C2 / C7**. Seam contract at
    `.claude/worktrees/lg-06d-seam-contract.md`.
- **LG-06e · [DONE] Statistical Feats as a per-game feed.** Reshape the feats screen
  from the current ~9 fixed category-best entries into ZenGM's model: one
  sortable row per notable single-game performance with its box-score line +
  Opp / Result / Season, deep-linking to the Round. Larger than the other LG-06
  steps (changes `stat_feats.py` output shape + template). Doc:
  [`statistical-feats.md`](docs/zengm-comparison/statistical-feats.md).
  - completed: the pure module `matches/stat_feats.py` had its OUTPUT SHAPE
    rewritten from the 9-finder/single-`FeatRecord` design into a per-game feed —
    `scan_feats(player_rounds, matches) -> tuple[list[FeatRow], list[TeamFeatRecord]]`
    now emits **one `FeatRow` per (player, round)** that qualifies, each carrying
    that round's full box-score line (the new pinned `BOX_SCORE_KEYS` tuple of 13:
    the 12 `season_player_stats.STAT_KEYS` per-round PLUS `nuke_detonations`) as a
    `stats` mapping plus view-computed Opp / per-Round Result / Season descriptors,
    and a stacked non-empty `feats` tuple of `FeatBadge(kind, label, is_season_best)`
    badges. **Hybrid qualification** — a row is included iff it crosses ANY
    per-game threshold OR is a season-best leader: threshold constants ship at
    conservative starting values (`TRIPLE_NUKE_THRESHOLD=3`, `HIGH_TAGS_THRESHOLD=20`,
    `HIGH_POINTS_THRESHOLD=12000`, `HIGH_MVP_THRESHOLD=15`,
    `HIGH_RESUPPLIES_THRESHOLD=20`, `HIGH_MISSILES_THRESHOLD=8`, plus the boolean
    `medic_shutout` = medic & `times_tagged==0` and `perfect_heavy` = heavy &
    `shots_missed==0` & `tags_made>0`), calibration explicitly deferred; the 5
    `SEASON_BEST_STATS` (`mvp`/`points_scored`/`tags_made`/`resupplies_given`/
    `missiles_landed`) each yield exactly one guaranteed leader row (tiebreak:
    highest value -> highest `round_id` -> lowest `player_id`, all-zero-max stat
    skipped) tagged `is_season_best=True`. A row both crossing a threshold AND
    leading its kind collapses to ONE badge with `is_season_best=True` winning.
    Feat kinds are pinned in `FEAT_KINDS` (8 `(kind, label)` pairs). `comeback_win`
    moved OUT of the per-player feed into a separate **Team feats** section —
    `find_comeback_win(matches) -> list[TeamFeatRecord]` (return type changed from
    `Optional[FeatRecord]`; detection logic unchanged). `scan_feats` guarantees a
    deterministic default order (`round_id` DESC, then `player_id` ASC); the module
    stays Django-free (`TestNoDjangoImportsLeaked` retained). The view
    `matches.league_screens.statistical_feats.statistical_feats` materialises the
    extended per-(player,round) seam dicts (Opp / Result / Season computed
    **view-side** from `GameRound.red_points`/`blue_points` per-ROUND — NOT the
    Match outcome — and `Match.season`; `mvp = float(prs.get_mvp)` property,
    `accuracy = float(prs.get_accuracy())` **method**, `nuke_detonations` from the
    existing `event_type="special"`/`points_awarded=500` detonation pass), then
    adds **LG-06a pagination** (`_coerce_per_page`/`_coerce_page`,
    `_LG01F_PER_PAGE_OPTIONS`, `Paginator` AFTER sort) and **expanded LG-06c sort**
    over the full box-score column set (`_FEATS_SORT_KEYS` frozenset of every
    descriptor + 13 box-score keys, `_FEATS_SORT_KEYS_DISPLAY`, the
    `_feat_row_sort_value` extractor, `teams.views._coerce_dir` reused) with
    **default sort = most recent first** (`("round", "desc")`) and a deterministic
    `(round_id desc, player_id asc)` secondary tiebreak; the Team-feats list is not
    paginated. Coexists with the LG-06b `?team_id=` filter (applied to the seam
    inputs) + the LG-06d `?season=` selector (incl. Career); changing season/team/
    sort/per-page omits `page` to reset to page 1. The template
    `templates/leagues/statistical_feats.html` was rewritten from a `<ul>` of
    categories into the sortable `statistical-feats-table` (DOM ids
    `statistical-feats-th-<key>` per column with ` ↑`/` ↓` glyphs,
    `stat-feat-badge-<kind>` badges with a `(season best)`/`season-best` suffix,
    `statistical-feats-per-page-{form,select}` / `-pagination`) plus the separate
    `statistical-feats-team-feats` section (`stat-team-feat-<kind>`), preserving the
    LG-06b/d filter ids and the `stat-feats-empty-notice`. Read-only — **no model,
    migration, URL, simulator, RNG, or Score Calibration re-baseline; no CONTEXT.md
    edit** (the **Statistical feat** term was already finalized) and no ADR. Tests
    reshaped in `matches/tests/test_league_statistical_feats.py` (pure-unit +
    view). Seam contract at
    [`.claude/worktrees/lg-06e-seam-contract.md`](.claude/worktrees/lg-06e-seam-contract.md).
- **LG-06f · [DONE] Watch List as a full stats view (+ per-League watch flag).** Replace the 3-column bookmark
  table with the Player-Stats column set filtered to watched players (ZenGM
  parity). Per-user (vs. current browser-session) persistence is **deferred to
  UX-01** (the watch list moves from `request.session` to a per-user model when
  accounts land). Doc: [`watch-list.md`](docs/zengm-comparison/watch-list.md).
  - completed: watch lists became **per-League** in the browser session —
    `request.session["watch_lists"]: dict[str, list[int]]` keyed by
    `str(league_id)` (e.g. `{"3": [12, 47], "8": [12]}`); the pre-LG-06f global
    singular `request.session["watch_list"]` key is **ABANDONED** with no
    migration, no read-compat, and no fallback (session data is disposable,
    ADR-0004 precedent). A single source-of-truth reader
    `matches.league_views._watched_player_ids(request, league_id) -> set[int]`
    (alongside `_coerce_per_page` / `_coerce_team_id` / `_coerce_season`) coerces
    each stored entry to int (silently dropping non-ints), never raises, and is
    consumed by BOTH the new context processor AND the screen view. A new context
    processor `core.context_processors.watch_list(request) -> {"watched_player_ids":
    set[int]}` (alongside `league_nav` / `app_mode`, lazy-importing
    `_watched_player_ids` to dodge the apps cycle) resolves `league_id` from
    `request.resolver_match.kwargs` defensively (off-League / no match ⇒ empty
    set) and is **registered immediately AFTER `core.context_processors.app_mode`**
    in `settings.TEMPLATES[0]["OPTIONS"]["context_processors"]`. A POST-only
    CSRF-protected toggle endpoint
    `matches.league_screens.watch_list.watch_list_toggle(request, league_id) ->
    JsonResponse` (URL name `watch_list_toggle`, route
    `/leagues/<int:league_id>/players/watch-list/toggle/` inserted right after the
    `players_watch_list` route) flips a player's membership in **this League's**
    list and returns `{"watched": bool, "player_id": int}` (200), `{"error":
    "invalid player_id"}` / `{"error": "unknown player_id"}` (both 400),
    `HttpResponseNotAllowed(["POST"])` (405), or 404 on missing League — per-League
    isolation guaranteed by the `str(league_id)` key. The Watch List screen view
    was **rewritten** into the Player-Stats column set filtered to watched players:
    a new **pure** helper `season_player_stats.zero_fill_watched(rows, watched_ids,
    identity_by_id) -> list[PlayerStatRow]` (alongside `aggregate_player_stats` /
    `apply_rate` / `sort_player_stats`, **no new imports** — the module's frozen
    no-Django allowlist is preserved) keeps only watched aggregated rows then
    appends a zero row (`games=0`, every `STAT_KEYS + DERIVED_KEYS` key at `0.0`)
    for each watched id with no Round in scope, in **ascending-id order**
    (aggregated-rows-first / zero-rows-second deterministic output; a watched id
    absent from `identity_by_id` is silently skipped). The locked view pipeline is
    `_build_round_dicts` (imported from `player_stats.py`) → `aggregate_player_stats`
    → `zero_fill_watched` → `apply_rate` → `sort_player_stats` → `Paginator`. The
    reshaped screen carries the full Player-Stats kit **minus the team filter**
    (the Watch List is a personal cross-team set) — season selector (+ Career) via
    `_resolve_season_scope`, rate toggle via `_coerce_rate`, per-page via
    `_coerce_per_page` / `_coerce_page`, sortable columns via `coerce_sort` /
    `coerce_dir` / `sort_player_stats` — with new DOM ids
    `watch-list-{per-page,season-filter,rate}-{form,select}` /
    `watch-list-th-{key}` / `watch-list-pagination` mirroring `player-stats-*`,
    preserving `watch-list-table` / `watch-list-empty-notice` (the `"No Season"`
    substring branch retained) and `sidebar_active="watch_list"`. The **add-form is
    DROPPED** (`watch-list-add` / `-select` and the old `watch-list-row-{id}` rows
    removed); **Remove All / `?action=clear`** is retained (now clears
    `watch_lists[str(league_id)]` then redirects to the bare URL); a per-row
    **watch flag replaces the per-row Remove control**. Two new partials —
    `templates/_partials/watch_flag.html` (a `<button class="watch-flag">` with
    `.watch-flag-on` when watched, `data-player-id` + `data-toggle-url`, NO unique
    `id` so duplicate-player rows don't collide) and
    `templates/_partials/watch_flag_script.html` (one delegated-click `<script>`,
    included exactly once per page, fetch-POSTs with the `X-CSRFToken` cookie and
    toggles `.watch-flag-on` on **all** buttons sharing a `data-player-id`) — wire
    the ZenGM-style flag onto the player-name cell of **8 league screens**
    (`player_stats`, `player_ratings`, `free_agents`, `league_leaders` ×4 boards,
    `statistical_feats`, `team_roster` ×2 sections, `team_history`, and the
    rewritten `watch_list`). UI-only — **no model, no migration, no simulator, no
    RNG, no Score Calibration re-baseline**; CONTEXT.md gained the **Watch list** /
    **Watch flag** terms; no ADR. Tests in
    `matches/tests/test_watch_flag.py`, `matches/tests/test_watch_toggle.py`, and
    `matches/tests/test_league_watch_list.py` (the latter also hosts the pure
    `zero_fill_watched` unit tests). The league-pinned **career-page** flag — the
    one player surface this reshape could not cover (the global
    `/players/<id>/stats/` page is league-agnostic, so its flag has no League to
    toggle against) — was **split off to LG-06h** on 2026-06-02. Seam contract:
    [`.claude/worktrees/lg-06f-seam-contract.md`](.claude/worktrees/lg-06f-seam-contract.md).
- **LG-06g · [DONE] Standings form/side detail.** Surface Streak, Last-5 (L5), and a
  home-away (Red/Blue side) split on the Standings table — we already persist
  per-Round side data; this is presentation only. Doc:
  [`standings.md`](docs/zengm-comparison/standings.md).
  - completed: the LG-01 Standings table gained **8 new columns in two grains**
    plus made **all 17 columns sortable** (LG-06c pattern). The pure module
    `matches/standings.py` was extended in place — `StandingsRow` grew from 9 to
    **17 fields** (appended after `rank`, pinned order: `match_streak`,
    `match_l5`, `round_streak`, `round_l5`, `red_wlt`, `blue_wlt`,
    `red_points_for`, `blue_points_for`) and `compute_standings` gained a 3rd
    positional param `season_rounds`. **Two corpora by design:** the Match-grain
    columns (existing W/L/T/Pts/RW/TS + `match_streak` + `match_l5`) read the
    completed-Match corpus (`completed_matches`, now a 9-key dict with the added
    `date_played`); the Round-grain columns (`round_streak`, `round_l5`) and all
    four side-split columns (`red_wlt`/`blue_wlt`/`red_points_for`/
    `blue_points_for`) read **every persisted Season Round** including Rounds of
    in-progress (`is_completed=False`) Matches (`season_rounds`, a 6-key dict
    `round_id, team_red_id, team_blue_id, red_points, blue_points, date_played`).
    The **side split is per PHYSICAL side** — read straight off `GameRound`'s
    stored `team_red`/`team_blue` + `red_points`/`blue_points` (SIM-08: stored
    sides are the actual physical sides), NEVER the Match-level `red_*`/`blue_*`
    fields (those are team-position-keyed — `Match.red_round2_points` is
    team_red's points while it physically played BLUE in R2). A Round result:
    red wins iff `red_points > blue_points`, blue iff `blue_points > red_points`,
    tie iff equal; `red_wlt`/`red_points_for` aggregate the Rounds the team
    physically held red, `blue_*` symmetric, and a team aggregates into BOTH
    across the Season. `round_streak`/`round_l5` are the team's own side-agnostic
    W/L/T. **Streak** is stored as a `(kind, length)` tuple (`("W",3)` →
    `"W3"`, `("L",2)` → `"L2"`, `("T",1)` → `"T1"`, `("",0)` → `"—"`) — the
    `(kind, length)` shape avoids the T-vs-no-streak collision a signed int
    would carry; **L5** and the side records are `(W,L,T)` int-tuples displayed
    `"3-1-1"`. Both grains order chronologically by `(date_played, id)` asc,
    most-recent = tail. The dataclass holds **structured numerics only**; the
    template formats display strings and the view derives sort keys. **All 17
    columns sortable** via the LG-06c pattern — `matches.league_views`
    `_coerce_sort_key` (new frozenset `_STANDINGS_SORT_KEYS` of 17 keys, default
    `("rank","asc")` so a no-`?sort` request renders today's order unchanged) +
    `teams.views._coerce_dir` (newly imported into `league_views`), sorting
    **view-side** on the materialized rows after `compute_standings` with new
    helpers `_standings_sort_value` / `_streak_sort_value` / `_standings_row_attr`
    (the last an attr-or-key adapter so the draft-preview dict rows sort through
    the same path); record/L5 columns sort `(wins desc, losses asc)`, streaks by
    signed run length, and **`rank` stays frozen** (never renumbered, the LG-06c
    League-Leaders precedent) so sorting by another column reorders display while
    the Rank cell shows the true standing. The view (`season_standings`) builds
    `season_rounds` from `GameRound.objects.filter(match__season=season).values(
    …)`, adds `date_played` to the Match dicts, and exposes new context keys
    `sort` / `dir` / `sort_keys` (= `_STANDINGS_SORT_KEYS_DISPLAY`) /
    `querystring_without_sort_dir`; the **draft-preview** branch emits the 8 new
    fields zeroed (`("",0)` streaks → `"—"`, `(0,0,0)` → `"0-0-0"`, points `0`)
    and still sorts. The template `templates/seasons/standings.html` swapped its
    9 hardcoded `<th>` for the LG-06c sort-header loop (DOM ids
    `season-standings-th-<key>` for all 17, ` ↑`/` ↓` glyph on the active header)
    and renders 17 `<td>`, preserving `season-standings-table` / `-empty` /
    `-draft-preview-banner` / `season-state-badge`. UI-only, read-only — **no
    model, migration, URL, simulator, RNG, or Score Calibration re-baseline**;
    CONTEXT.md gained the **Standings form** + **Side split** terms (no ADR).
    Tests: `matches/tests/test_standings.py` (pure-unit — every callsite migrated
    to the 3-arg signature, new classes for both grains + side split + ordering;
    `TestNoDjangoImportsLeaked` retained) and `matches/tests/test_season_views.py`
    (view/DOM — 17 header ids, two-corpora difference, physical-side split, sort
    reorders with frozen rank, draft zeroed + sortable). Seam contract:
    [`.claude/worktrees/lg-06g-seam-contract.md`](.claude/worktrees/lg-06g-seam-contract.md).
- **LG-06h · [DONE] League-scoped player page (+ watch flag).** Introduce a
  **league-pinned** player detail route (`/leagues/<league_id>/players/<player_id>/…`)
  so a Player viewed from inside a League carries that League's context — and put the
  ZenGM **watch flag** on it. This is the one player surface LG-06f could **not** cover:
  the existing `player_career_stats` page at `/players/<id>/stats/` is league-agnostic, so
  its flag has no League to toggle the (per-League) watch list against. Carved out of
  **LG-06f** on 2026-06-02 because pinning the global HX-01 career page to a League is a
  new route + view + template, not a watch-list reshape. Repoint the 8 LG-06f league
  screens' player-name links at the new route. **Open questions for its own grill:** does
  the page show **league-scoped** stats (only this League's Seasons) or the same global
  HX-01 career aggregates; how a Player with games in two Leagues is handled (name overlap
  is intentional — separate Player rows, separate per-League watch lists); whether to
  reuse the HX-01 aggregation or a Season-scoped one; sidebar chrome + flag placement.
  **Depends on LG-06f** — reuses the per-League watch-list storage, toggle endpoint,
  context processor, and flag partial it ships, verbatim.
  - completed: shipped the read-only **League player page** at the league-pinned
    route `/leagues/<int:league_id>/players/<int:player_id>/` (URL name
    `league_player_detail`, GET-only). The view
    `matches/league_screens/player_detail.py::player_detail(request, league_id,
    player_id)` is re-exported from `matches/league_screens/__init__` and lives
    among the existing `players/*` routes in `matches/league_urls.py` (after
    `players_free_agents` / `players_watch_list` / `watch_list_toggle`, before the
    `league_list` catch-all — the digit-only `<int:player_id>` converter does not
    shadow the literal `players/free-agents/` etc.). The page mirrors the ZenGM
    player profile: a header (player bio + the LG-06f **watch flag** + an EXTERNAL
    link out to the global HX-01 `player_career_stats` page at
    `/players/<id>/stats/`), an **Overall** summary, grouped **current ratings**
    read off the `Player` fields, and a **Potential** block rendering the literal
    `—` placeholder (LG-05 owns the real Potential field — none exists yet). The
    league-scoped **Regular-Season stats table** (one per-Season row plus a
    Career-in-league row) is built **VIEW-SIDE** by reusing
    `matches.league_screens.player_stats._build_round_dicts` +
    `matches.season_player_stats.aggregate_player_stats` — **no new pure module**:
    one aggregation pass per this-League Season the player has Rounds in (scope
    `game_round__match__season=season, player_id=player.id`) plus one league-wide
    Career pass (`game_round__match__season__league=league, player_id=player.id`).
    Each per-Season row's **Team is derived from the player's actual Rounds that
    Season** (the aggregated row's last-seen `team_name`/`team_id`, NOT the current
    `Player.team`), so a dropped/transferred player shows the team they played for.
    Rendering is **LENIENT**: any valid `(League, Player)` pair renders 200 (404
    only on a missing League or missing Player); the league-scoped sections render a
    blank empty-state when the player has no Rounds in the League (e.g. a free agent
    or a player whose only Rounds are in another League) — the header, Potential,
    and all stubs still render. Five inline **"coming soon" stub** sections
    (Playoffs, Ratings-history, Awards, Salaries, Transactions) hold space for the
    model-less ZenGM sections. The **8 LG-06f league screens'** player-name links
    were repointed from the global `player_career_stats` to the in-League
    `league_player_detail` route (Statistical Feats, previously plain text, gained a
    link; the sandbox `teams/` surfaces stay league-agnostic on
    `player_career_stats`). Read-only — **no model, migration, simulator, RNG, or
    Score Calibration re-baseline; no ADR**. CONTEXT.md already carries the **League
    player page** term. Template `templates/leagues/player_detail.html`; tests
    `matches/tests/test_league_player_detail.py`. Seam contract:
    [`.claude/worktrees/lg-06h-seam-contract.md`](.claude/worktrees/lg-06h-seam-contract.md).

Structural divergences surfaced by the playthrough that map to **existing**
tasks rather than LG-06 (see
[`season-lifecycle.md`](docs/zengm-comparison/season-lifecycle.md)): the
playoffs stage + phase-aware Play menu → **LG-02**; season-MVP / Finals-MVP
awards (and surfacing them on League History) → **LG-03**; MMR / Rank / Potential
columns → **STAT-PROXY-01**.

### LG-03 · [DONE] Season-end awards

Computed from `PlayerRoundState` aggregates: Most Points, Highest K/D by role, Best Medic, 
Most Efficient Nuke, Best Accuracy. Awards page at `/seasons/<id>/awards/`. Award badge on player profile.

Also surface the headline **season MVP** (and, once LG-02 playoffs land, a
**Finals MVP**) on the **League History** table (LG-01f) — the reference product
puts both in its history row next to Champion / Runner-up, and ours currently has
no awards column. See
[`docs/zengm-comparison/season-lifecycle.md`](docs/zengm-comparison/season-lifecycle.md).

**Status: DONE.** A **read-only / derived** league screen — every award recomputed
**on render (transient)** from frozen `PlayerRoundState` rows, with **NO model field,
NO migration, NO simulator change, NO Score Calibration re-baseline, NO persisted award
rows**. A new Django-free pure module `matches/season_awards.py` (allowlist
`dataclasses` / `typing` / `collections`, guarded by `TestNoDjangoImportsLeaked`) exposes
`compute_season_awards(player_rounds, *, min_games)` and `pick_finals_mvp(final_round_dicts)`
over frozen `AwardWinner` / `AwardSet` dataclasses; the view does ALL ORM work and feeds the
pure fn a flat `list[dict]`. **Corpus split:** the regular-season awards read
`PlayerRoundState.objects.filter(game_round__match__season=season)` — season-embedded
**playoff** Matches carry `season=NULL` (Part2c-1 #3) and are naturally excluded — while
**Finals MVP** is computed separately over the championship bracket node's rounds and is set
**only on a bracket-format playoff** (`single/double_elimination`, `round_robin_double_elim`;
`None` for `round_robin`/`swiss`/no playoff). The award set is the **6 regular-season awards**
— **Most Points**, **Best Accuracy**, **K/D by role** (5 winners, one per role), **Best
Medic**, **Most Efficient Nuke**, **Season MVP** (mean of `get_mvp`) — **plus the separate
Finals MVP**. **Qualifier:** the rate/mean awards (Season MVP, Best Accuracy, Most Efficient
Nuke) require `games(player) >= ceil(max_games_any_player / 2)`; the total/count awards (Most
Points, Best Medic, K/D) are ungated; ties break by metric → games desc → `player_id` asc.
**Three surfaces:** the new **awards page** (`season_awards` view / `/seasons/<id>/awards/`,
league-sidebar shell, GET-only); two new **League History** columns (Season MVP / Finals MVP —
`_build_history_row` grows 11 → 13 keys, reusing the same shared regular-season-dicts +
finals-corpus helper); and the **player profile** awards badge (the
`league-player-awards-stub` placeholder becomes the live `league-player-awards` block fed by a
new `player_awards` context list). Seam contract:
[`.claude/worktrees/lg-03-season-awards-seam-contract.md`](.claude/worktrees/lg-03-season-awards-seam-contract.md);
impl notes in [`matches/CLAUDE.md`](laserforce_simulator/matches/CLAUDE.md).

### LG-04 · [DONE] Season-end stat updates

At the end of each season, all players (on active teams or otherwise) receive a stat update.
The original framing factored in **new experience** (games played this season), **player age**,
and **prior experience** (historical games), with default weights fixed in code but overridable
per season — but the LG-04 grill (2026-06-10) confirmed the system is modeled on **ZenGM**,
whose `developSeason` is driven **purely by an age curve** (in-game production never moves
ratings). That framing is therefore **superseded**: LG-04 follows ZenGM — **age-driven**;
**games-played is cosmetic** (it ticks a counter but is never a develop input), per
[ADR-0024](docs/adr/0024-zengm-player-development-ratings-history.md).

**Status: DONE.** Development is a **ZenGM-faithful age curve** (young trend up, peak
mid-to-late 20s, older decline increasingly fast; per-stat age modifiers + change limits +
random noise, coaching fixed at 0), run **league-scoped at each `next_season` rollover** (the
preseason analogue) over the rolling League's **developing set** — its snapshot Teams' players
(active slots + bench) plus the `free_agent_pool` players: each Player is aged `+1`, its 19 live
`Player` stat fields are **mutated in place** (the first persisted `Player`-stat mutation in the
league flow), its `total_games` is **cosmetically ticked** (active player by their exact
regular-season appearance count in the just-completed Season — playoff rounds carry
`season=NULL` and are excluded; free-agent by a smaller random amount), and one immutable
**`PlayerSeasonRating`** snapshot row (19 stats + age + `overall_rating` + a reserved nullable
`potential`) is written for the new Season. A **baseline** `PlayerSeasonRating` row (as-generated
stats, no development) is written for every founding Player at `league_create`; the live `Player`
fields stay the Simulator's source of truth and the rating rows are a read-only audit trail. The
develop math lives in a **Django-free pure module `matches/development.py`** (allowlist
`dataclasses`/`typing`/`random`/`collections`, RNG **injected**, guarded by
`TestNoDjangoImportsLeaked`); production builds a **fresh `random.Random()` per rollover and
stores no seed** (the row is the audit trail). A migration ships
(`0048_playerseasonrating.py`, one `CreateModel`, no backfill). The LG-06h
`league-player-ratings-history-stub` becomes the live `league-player-ratings-history` block — a
Chart.js overall-rating-over-time trend + per-Season stat table (Potential renders `—`).
**NO Score Calibration re-baseline** (Stat *inputs* change, no simulation *mechanic*).
**Deferred:** the per-team **coaching/scouting budget** knob (no per-(team, season) state yet —
coaching effect fixed at 0, deferred to a slice designed with LG-05's scouting budget),
**retirement / replacement intake**, and **`potential`** (reserved nullable column, computed in
**LG-05**). See [ADR-0024](docs/adr/0024-zengm-player-development-ratings-history.md), seam
contract
[`.claude/worktrees/lg-04-player-development-seam-contract.md`](.claude/worktrees/lg-04-player-development-seam-contract.md),
and impl notes in [`matches/CLAUDE.md`](laserforce_simulator/matches/CLAUDE.md).

### LG-05 · [DONE] Player potential

Each player carries a `potential` attribute: a dynamically computed estimate of their likely stat ceiling.

The original framing tied the scouting-noise band to a **per-season scouting budget allocation on
the team**. That framing is **superseded** the same way LG-04's "experience" framing was: LG-05
ships the noise band off a **FIXED `DEFAULT_SCOUTING_BUDGET = 50` constant** (no per-(team, season)
state exists yet), and **CAR-01** later promotes the budget to a per-team field — exactly the
deferral ADR-0024 recorded for the coaching/scouting knob.

**Status: DONE.** `potential` is a per-`Player` **projected peak overall** (a `FloatField`),
computed at each **season-end** — the `league_create` baseline AND every per-League `next_season`
rollover — alongside LG-04 development, never on demand. The compute is a **noise-free
forward-projection** of the LG-04 age curve (`matches/development.py::_project_peak_overall`):
the LG-04 per-stat curve is rolled forward from the player's current age to age 40 with **zero
noise** (a `0.9` midpoint multiplier in place of LG-04's `rng.uniform(0.4, 1.4)`), tracking the
**running-max overall** across the path — that running max is the ceiling, **floored at the
player's current overall** (it can never predict regression below the present average) and
**capped at 100**. `compute_potential(stats, age, rng, *, scouting_budget=DEFAULT_SCOUTING_BUDGET)`
then lays a **scouting-noise band** over that ceiling: `sd = POTENTIAL_MAX_SD * (1 - budget/100)`
(budget 0 → max sd, 100 → 0), exactly **one `rng.gauss(0, sd)` draw**, re-clamped to
`[current_overall, 100]`. Both functions are **pure** (Django-free, no new import —
`TestNoDjangoImportsLeaked` stays green), and LG-04's `develop_stat` / `develop_player_stats` are
left **byte-unchanged**.

The value lands in a **new live `Player.potential` FloatField** (nullable, default `None`;
migration `teams/migrations/0012_player_potential.py`, single `AddField`, dep
`0011_team_is_draw_team`, no backfill) AND fills the **`PlayerSeasonRating.potential`** column LG-04
reserved-but-always-`None`. Two write sites in `matches/league_views.py` set it:
`_write_baseline_ratings` (founding baseline) and `_develop_league_for_new_season` (rollover, on
the POST-development stats + already-incremented age). Each rollover builds a **SEPARATE fresh
`random.Random()`** for the gauss draw, INDEPENDENT of LG-04's develop RNG — so LG-04's pinned
1-gauss-then-19-uniform sequence and its seeded develop output stay **byte-identical**. Players
outside any league flow keep `potential = None`.

**UI:** `potential` becomes a **sortable `Pot` column** on `player_ratings` + `free_agents`
(nulls-last in both directions via `F("potential").desc/asc(nulls_last=True)`), a **render-only**
cell on `team_roster`, a **live card** on the LG-06h player page (`#league-player-potential`,
replacing the "Arrives with LG-05" stub), and the LG-04 ratings-history `Pot` column now lights up
for rows written after LG-05. **NO Score Calibration re-baseline** — `potential` is **read-only to
the simulator** (never a sim input), so no simulation mechanic changes and **no new ADR** (the
column is a reversible nullable add, recomputed every rollover). MMR / Rank stay non-sortable `—`
placeholders (STAT-PROXY-01); the global HX-01 career page and the LG-00c `/players/` list are
untouched. The **Potential** CONTEXT.md term is already written. See
[ADR-0024](docs/adr/0024-zengm-player-development-ratings-history.md) (the LG-05 consequences
addendum), seam contract
[`.claude/worktrees/lg-05-player-potential-seam-contract.md`](.claude/worktrees/lg-05-player-potential-seam-contract.md),
and impl notes in [`matches/CLAUDE.md`](laserforce_simulator/matches/CLAUDE.md) `## LG-05 player
potential`.

### INFRA-01 · PostgreSQL/SQLite Parity Hardening

**Status: DONE.** **Reframed on contact:** Postgres was **already canonical** —
the Docker/CI/Fly deploy work had landed it (`psycopg2-binary` in
`requirements.txt`, a `postgres:16` service in `docker-compose.yml`, CI
(`.github/workflows/ci.yml`) running both the full pytest suite **and** a docker
smoke job against `postgres:16`, Fly.io deploying off that image, and
`settings.py` reading `DATABASE_URL` via `dj_database_url`). INFRA-01 therefore
became a **HARDEN + VERIFY + DOCUMENT-parity** slice, **not** a migration: the
original "switch to Postgres" framing was obsolete before the task started.

**SQLite stays the guarded dev-only default** when `DATABASE_URL` is unset. The
SQLite write-contention hardening **stays in place, guarded**: the `OPTIONS`
block in `settings.py` (`timeout` / `transaction_mode`) and the
`core/db_pragmas.py` WAL `connection_created` hook both early-return / no-op on
Postgres (`connection.vendor != "sqlite"`).

**Production-code surface: NONE.** `settings.py` and `core/db_pragmas.py` are
**byte-unchanged**; **no model field change → no migration**; **no Score
Calibration re-baseline** (nothing touches a simulation input). The only
artifacts that land are **two pure guards in `core/tests.py`**: **(A)**
`set_sqlite_pragmas` early-returns on a non-sqlite (`vendor="postgresql"`)
connection so the WAL PRAGMAs **never run on Postgres** (asserts `cursor` not
called, no DB hit, backend-agnostic); **(B)** a `MapZoneConfig.zone_data`
nested-payload round-trip (2D int `zones` + `wall_meta` dict + 2D float
`elevation`) that deep-equals after `refresh_from_db()`, covering **SQLite-text
vs Postgres-jsonb** `JSONField` serialization parity (passes on SQLite locally,
Postgres in CI).

**SQLite-assumption audit came back clean:** no raw SQL / `.extra()` / `.raw()`
except the guarded PRAGMA; **zero `icontains` / `iexact`** case-insensitive
lookups (so no Postgres case-sensitivity break); the only residual delta is an
`order_by("name")` collation difference, which is **cosmetic**.

**Acceptance:** lock-freedom is the documented **Postgres MVCC** property (no
single-writer lock — the `database is locked` class of error cannot arise);
**CI proves the full suite green on Postgres**; the "Play Until End of Season on
compose-Postgres, no lock errors" end-to-end smoke is a **DEFERRED manual
check** (documented as manual — **not** claimed as run).

See [ADR-0025](docs/adr/0025-postgresql-canonical-sqlite-dev-only.md) (full
rationale) and the seam contract
[`.claude/worktrees/infra-01-postgres-sqlite-parity-seam-contract.md`](.claude/worktrees/infra-01-postgres-sqlite-parity-seam-contract.md);
this PLAN note is the impl note (no app-level `CLAUDE.md` change — the task is
tests + docs only).

---

---

## Phase 5.5 — Single-Player Career Mode

A single-user play mode where the user acts as a team manager navigating a league season. This phase
sits between the League system (Phase 5) and full multiplayer (Phase 6).

### CAR-01 · [DONE] Manager role and team assignment

In single-player career mode, the user is a team manager (not a player in the simulation).
The user is assigned to a team at the start of a career league. Each season the user manages their
team through the league schedule.

**Status: DONE.** The manager **names their own team at create-League time**, and that named team
becomes one of the N generated teams **and** the League's `current_team`. **Locked grill decisions:**
single-player `league` mode **IS** career mode — **NO new mode value**, **NO `Manager` / `User`
model** (both deferred to **UX-01**; the manager is the implicit local user); `current_team` **is**
the manager's career team, reusing the **existing `League.current_team` FK** (no new model field).

**Surface = the create-League form field ONLY.** A new optional field
`matches/forms.py::CreateLeagueForm.manager_team_name` (`forms.CharField(max_length=100,
required=False, label="Your team name")`, DOM id `league-create-manager-team-name`) is inserted
**after `league_name` / before `season_name`**, with a matching field row in
`templates/leagues/create.html` between the league-name and season-name rows. `league_create`
(inside its existing `@transaction.atomic`) renames the **alphabetical-first generated team** to the
stripped `manager_team_name` and sets it as `league.current_team`; **blank name → today's LG-01g
verbatim `sorted(created_teams, key=name)[0]` alphabetical auto-pick** (byte-identical, backward
-compatible). The named team **stays one of the N** (league size unchanged = `num_teams`); the wiring
runs at the current `current_team` position, **before** `Season.objects.create` / `season.teams.add`
/ the phase loop / `_write_baseline_ratings`. No `clean()` change, no uniqueness validation (team
names are not globally unique). The existing LG-01g 'your team' framing already surfaces
`current_team`.

**Scope-out (locked).** The **per-team scouting budget is DEFERRED** (out of CAR-01 scope — LG-05's
FIXED `DEFAULT_SCOUTING_BUDGET = 50` stands until a later slice promotes it). **No migration, no new
model field** (reuses `League.current_team`), **no new mode value**, **no `Manager` / `User` model**,
**no simulator change**, **no Score Calibration re-baseline**, **no ADR**. Tests:
`matches/tests/test_league_create.py::TestCar01ManagerTeamName`. Seam contract:
[`.claude/worktrees/car-01-manager-team-assignment-seam-contract.md`](.claude/worktrees/car-01-manager-team-assignment-seam-contract.md);
impl note in [`matches/CLAUDE.md`](laserforce_simulator/matches/CLAUDE.md) `## CAR-01 manager team
assignment`.

### LG-01i · [DONE] Season "One Week (Live)" replay UI

Live replay surface invoked from the Play dropdown — a
"One Week (Live)" entry that lets the manager watch their team's next
game replay in the browser (play/pause/scrub) and then commit or
discard the run. Deferred from LG-01d; re-sequenced from Phase 5 to
Phase 5.5 (post-CAR-01) on 2026-05-28.

**Status: DONE.** Shipped as **preview-then-commit live replay, NOT server-side
tick streaming** (the PLAN's original "plays the next matchday tick-by-tick" /
"tick-stream engine" framing was superseded at grilling — there is no WebSocket /
SSE / Channels surface; the client SIM-05 playback engine plays pre-baked JSON).
**Mode B is locked: only `League.current_team`'s game is previewed; the rest of
the matchday / bracket stage is simmed FRESH at commit, never previewed.** A
preview draws a seed (or a pair), `random.seed()`s it, runs the in-memory tick
loop with **NO DB flush**, serializes the events to the SIM-05 `events_data` /
`players_data` JSON shape, and **pins the captured seed(s) in
`request.session["live_preview_pin"][str(season_id)]`** keyed to the cursor
identity ("locked once previewed" — the cursor-identity equality check is the
auto-invalidation; Discard or a successful commit clears the pin). Commit re-runs
**only the watched game with the injected captured seed(s)** (SIM-07 ⇒
byte-identical to what was watched) then sims the rest of the matchday (RR) /
bracket stage (playoff) fresh, sync, atomically. Two watchable cursors: the **RR
cursor** = `current_team`'s single next-matchday Round (1 seed; bye ⇒ degrade to
plain commit), and the **playoff cursor** = its next undecided 2-round Match
(2 seeds), offered only when `current_team` is **alive** (eliminated ⇒ no live
entry). The injected-seed seam is additive + keyword-only with `None` ⇒
verbatim-today (`_simulate_and_flush_round` / `simulate_scheduled_round`
`rng_seed=`, `simulate_match` `rng_seeds=`, and the new
`tournament_engine.play_specific_node` extracted from `play_next_node`) ⇒
byte-identical to every existing caller ⇒ **NO Score Calibration re-baseline**.

**Dependency correction.** The PLAN's original "depends on **CAR-01** + the new
Season-replay tick-stream engine CAR-01 owns" was **inaccurate** — **CAR-01
shipped only the manager-team-name create-League form field** (no tick-stream
engine ever existed). LG-01i builds the preview / replay surface **itself** from
the **SIM-05 client playback engine** + the **in-memory `_simulate_round` / flush
split** (the new `preview_scheduled_round` / `preview_tournament_match` no-flush
bundle reusing the SIM-05 `events_data` / `players_data` shapes). CAR-01 is only
the `League.current_team` **consumer** — LG-01i watches whatever team CAR-01
assigned.

**Scope-out (locked).** No migration, no model change (the seed lives in
`request.session`; the committed round persists it via the existing
`GameRound.rng_seed`), no re-baseline, no server-side tick streaming /
WebSocket / SSE, no watching non-manager games, no re-roll within a pin
(Discard then re-open to draw fresh), no playoff-live when eliminated, no
SIM-05 partial extraction (the playback JS is duplicated into the new
`templates/seasons/play_week_live.html`, not factored out), no ADR, no
CONTEXT.md term (the **One Week (Live)** glossary term was finalised at
grilling). Seam contract:
[`.claude/worktrees/lg-01i-one-week-live-seam-contract.md`](.claude/worktrees/lg-01i-one-week-live-seam-contract.md);
impl notes in [`matches/CLAUDE.md`](laserforce_simulator/matches/CLAUDE.md)
`## LG-01i Season "One Week (Live)" play-now-and-watch replay`.

**Redesign (play-now-and-watch — supersedes the preview-then-commit model
above).** A follow-up reframed the feature: **"One Week (Live)" now PLAYS the
manager's game immediately** (commits it), kicks off the **rest** of the
matchday / bracket stage as a **background** `play_season_task(max_matchdays=1)`
run, and opens a **read-only replay** of the just-played game (the SIM-05 engine
fed from the *persisted* event log via the extracted
`matches.views.round_playback_payload`). **Results are final the moment it runs —
no preview, no commit, no discard, no retry.** This **removed** the preview
methods (`preview_scheduled_round` / `preview_tournament_match` /
`_serialize_events_for_preview`), the injected-seed seam (`rng_seed=` /
`rng_seeds=` on the sim methods + `play_specific_node`), the session pin, and the
`play_week_live_commit` / `play_week_live_discard` views. The surface is now a
POST `play_week_live` (commit + enqueue rest + redirect) and a GET
`play_week_live_watch` (replay the committed game). `_resolve_live_cursor` /
`_alive_playoff_node` / `play_specific_node` (now seed-less) survive; the dropdown
entry became a POST `<form>`. Still **no model change, no migration, no
re-baseline**. The seam contract above documents the superseded design.

### MECH-15 · [DONE] Gate base capture on active state (no capturing while down)

**Status: DONE.** A **one-guard** fix in the planning layer: the `capture_base`
branch of `matches/sim_helpers/combat.py::plan_action` is wrapped in
`if player.is_active_at(second):`, so a player inside the **Respawn cooldown**
(Downed, `last_downed_time` within `RESPAWN_TICKS = 16`) **never plans** a base
capture — both the map path and the 3-zone fallback. This mirrors the existing
`use_special` gate one branch down (and the missile gate in `start_missile_lock`
/ the tag gate in `_resolve_tag_attempts`); the **blast-radius audit confirmed
base capture was the only deliberate action leaking while down**. `combat.capture_base`
itself is **left unchanged** (the guard lives upstream in `plan_action`, the
chosen single layer), and **`award_bases` is deliberately left unchanged** —
round-end awards to survivors are treated as end-of-round possession, not active
interaction. **No model change, no migration, no CONTEXT.md edit** (the
**Base capture** / **Respawn cooldown** / **Not-targetable** / **Reset window**
terms already cover it), **no ADR**. **Re-baseline:** this is a simulator-mechanics
change that shifts seeded outcomes (a downed player no longer captures), folded
into the **single pending post-MOVE-01 Score Calibration re-baseline** — no new
obligation. The seed-sensitive `test_strong_team_winpct_not_diluted_by_alternation`
(SIM-08) was re-pinned `master_seed=9001 → 7777` to land on a representative
sample after the RNG-sequence shift (62.5% team-position win vs 49.2% balanced
physical side); both the 55% floor and the de-flip contrast guard stay intact.
**Regression test:** `matches/tests/test_map.py::TestMap04BaseInteraction`
(`test_downed_player_does_not_plan_capture` + an active-control at the
`RESPAWN_TICKS` boundary) — pure-unit, RNG pinned so `plan_action` rolls
`capture_base`, asserts the downed player yields no capture plan while the active
control does.

**Bug.** A player who has been **Downed** and is still inside the **Respawn
cooldown** (the not-targetable + reset windows, `RESPAWN_TICKS = 16`) can still
**Capture a base**. Surfaced while watching the LG-01i live replay — a greyed-out
(downed) player emitted a `base_capture` event and scored the 1001-point capture
mid-cooldown. A downed player is not active and must not be able to interact with
a base.

**Root cause.** The per-tick action loop in
`matches/simulation/entrypoints.py::_simulate_round` builds `plans` by calling
`_plan_action` for **every** player in `all_alive` — including players currently
in the respawn cooldown (alive, `final_lives > 0`, but `not is_active_at(second)`).
The `capture_base` dispatch (`entrypoints.py` ~L1480 → `_capture_base` →
`matches/sim_helpers/combat.py::capture_base`) range-checks the base, checks
`final_shots >= 3` (or Ammo), and awards the capture — but has **no
`is_active_at(second)` guard**. By contrast the shot path gates on active/taggable
state (`resolve_shot` validity gate; the `_resolve_tag_attempts` `is_active_at` /
`is_taggable_at` checks at ~L1926) and missiles gate inside `start_missile_lock`,
so capture is the one deliberate action that leaks through while down.

**Fix.** Add an active-state guard so a player who is not `is_active_at(second)`
cannot capture a base (nor be awarded one at round end via `award_bases` if that
path can fire for a downed player — confirm during the grill). **Open question —
which layer:** (a) inside `combat.capture_base` (single chokepoint, covers the
live-capture and any award path, returns `False` early when the actor is inactive
— but the function currently takes no tick-active signal beyond `second`, which it
already has via `player.is_active_at(second)`); (b) at the dispatch site in
`_simulate_round`; or (c) in `plan_action` so a downed player never *plans* a
capture in the first place (most consistent with how the weighted action vector
already zeroes deliberate actions for inactive players — audit whether
`capture_base` is even supposed to be a reachable plan for a downed player). Prefer
the layer that also makes the fix regression-obvious.

**Blast-radius audit (do in the grill):** confirm whether any **other** deliberate
action (tag/missile/resupply/special) can also leak while in the respawn cooldown,
or whether base capture is genuinely the only gap. If others leak, widen the fix to
a single shared "is this player allowed a deliberate action this tick?" gate rather
than patching capture alone.

**Scope.** Simulator-mechanics change → **this re-baselines seeded outcomes** (a
downed player no longer captures, so the event log / scores / standings shift on
affected ticks). Fold it into the single pending post-MOVE-01 Score Calibration
re-baseline — do **not** open a separate re-baseline obligation. **No model change,
no migration.** **Regression test (TDD):** a player Downed at tick *T* emits **no**
`base_capture` event while `T <= tick < T + RESPAWN_TICKS` even when standing in
base range with ≥ 3 shots; once active again, capture works as before. Pin it in
`matches/tests/simulation_tests.py` (or `test_map.py::TestMap04BaseInteraction`)
with a deterministic hand-built `PlayerState` in the cooldown window — assert on the
absence/presence of the `base_capture` event, not on point totals.

### CAR-02 · [DONE] Performance-based firing

The system tracks manager performance metrics (win rate, standings position, point differential).
When a manager's performance falls below a configurable threshold, the system fires them automatically.
After being fired, the manager can apply for or be assigned to another team in the league.

**Status: DONE.** Shipped as a **ZenGM-faithful owner-mood model**, NOT a single configurable
threshold (the PLAN's literal "configurable threshold" wording was superseded at grilling — a flat
knob can't express ZenGM's "over-perform to bank goodwill / rebuilding teams forgiven" fuzziness).
The team **Owner** judges the **Manager** (the implicit local user = `League.current_team`, CAR-01 —
**no `Manager`/`User` model**) once per completed Season across **three cumulative Mood factors**:
*wins* (regular-season Match record vs a .500 baseline,
`WINS_FACTOR * WINS_BASELINE_SCALE * (won - games/2) / (games/2)`, `games == 0` ⇒ neutral `0.0`),
*playoffs* (read off the Season's embedded `tournament` Season-phase bracket — `champion` ⇒ `+0.2`,
`seeded`-no-title ⇒ `(0.16/num_rounds) * rounds_won`, `missed` ⇒ `-0.2`, **`none`** = no tournament
phase ⇒ neutral `0.0`), and *money* — **DORMANT = 0.0 this slice** (the column exists so a future
finance subsystem lights it up without a migration; see **FIN-01** below). Each factor's cumulative
total is **capped at `+1` on the upside ONLY** (`MOOD_FACTOR_CAP = 1.0`, no negative floor — you
cannot bank goodwill past `+1` but can sink arbitrarily low; "can't win by maxing one factor, can
lose by neglecting one"). The Manager is **Fired** when, **strictly past a 2-Season Grace period**
(`GRACE_PERIOD_SEASONS = 2`, flat — ZenGM's +3-if-joined-at-playoffs nuance dropped), the summed mood
`wins_total + playoffs_total + money_total <= FIRE_THRESHOLD (-1.0)`; a past-grace **Hot seat**
warning fires at `total + delta < -1` (level 1, "another season…") or `total + 2*delta < -1` (level
2, "a couple more…"). A fired Manager **must Reassign** via a **New Team** picker (the **worst-5** by
the just-completed Season's final Standings, old team excluded) — which sets `current_team`, starting
a **fresh tenure + grace** — before the pre-season rollover can run.

**Model + migration.** One immutable per-`(League, completed Season)` snapshot
`matches.models.OwnerEvaluation` (FKs `League`/`Season` CASCADE + `teams.Team` SET_NULL `team_managed`;
the 3 factor deltas + 3 cumulative-capped totals, `verdict` ∈ `{retained, hot_seat, fired}`,
`hot_seat_level` 0/1/2; `uniq_league_season_owner_evaluation` constraint), migration
`0049_ownerevaluation` (**CreateModel-only — NO `RunPython`/backfill**, ADR-0004 disposable-data
posture; existing Leagues get no historical rows, the lazy writer fills them in Season order on first
reach). **Tenure boundaries + grace derive from the snapshot chain** (a `team_managed` change between
consecutive rows by Season order = a new tenure, cumulative + grace reset) — **no `tenure_id` field** —
because firings mutate `League.current_team` and a past Season's managed team is otherwise
unrecoverable.

**Pure module + orchestration.** A Django-free `matches/owner_mood.py` (frozen import allowlist
`dataclasses`/`typing`/`collections`, defended by `TestNoDjangoImportsLeaked`) holds the constants, the
3 frozen dataclasses (`MoodDeltas`/`MoodTotals`/`Verdict`), and 4 pure fns (`compute_wins_delta`,
`compute_playoffs_delta`, `cap_cumulative`, `decide_verdict` — the **one** decider returns BOTH the
outcome string AND the hot-seat level). The view assembles flat inputs (ints/strings) from the reused
`Season._final_standings_for_phase` standings path + the `tournament` bracket and calls them — the
module never sees a Django object or RNG. `matches.league_views._ensure_owner_evaluations(league,
up_to_season)` is the **lazy + idempotent writer** (`get_or_create`-keyed on `(league, season)`, walks
completed Seasons **oldest→newest** threading the per-factor caps + cumulatives + tenure marker). The
existing `@transaction.atomic next_season` rollover body is extracted **verbatim** into a plain
`_run_season_rollover(league, latest_completed) -> Season` shared by both `next_season` and the new
reassign path; `next_season` becomes the **verdict gate** (ensure → read the just-completed eval → a
`fired`-and-unreassigned Manager is redirected to the New Team picker and **cannot roll**; everyone
else rolls byte-equivalently to today). New views `owner_evaluation` (GET eval screen, browsable for
past Seasons) / `new_team_picker` (GET worst-5 list) / `reassign_team` (POST, sets `current_team` +
runs the shared rollover); URL names `owner_evaluation` / `new_team_picker` / `reassign_team`;
templates `seasons/owner_evaluation.html` + `leagues/new_team.html`; the dashboard "Start Next Season"
control is **rerouted** to a GET link to the eval screen (which exposes Start Next Season if retained/
hot_seat, Choose New Team if fired) — the `data-action-state="start_next_season"` attribute survives on
the link for LG-01c/e back-compat. The **hot-seat warning IS shipped**.

**Scope-out (locked).** **Money factor DORMANT** (always `0.0` — no finance subsystem; see **FIN-01**).
Challenge-mode firings (miss-playoffs / luxury-tax) and voluntary rival-offer switching **DEFERRED**
(both default-off in ZenGM; luxury-tax needs FIN-01). **No `Manager`/`User` model** (the Manager is
`League.current_team`, CAR-01). **No simulator change → no Score Calibration re-baseline.** **No
backfill / `RunPython`.** **No new CONTEXT.md term** (all 10 finalised at the grill). CAR-03 will later
gate firing to single-player `league` mode (this slice already operates only there — the only mode with
`current_team`). Decision: [ADR-0026](docs/adr/0026-manager-firing-owner-mood.md). Seam contract:
[`.claude/worktrees/car-02-performance-based-firing-seam-contract.md`](.claude/worktrees/car-02-performance-based-firing-seam-contract.md);
impl note in [`matches/CLAUDE.md`](laserforce_simulator/matches/CLAUDE.md) `## CAR-02 manager firing
(owner mood)`. Tests: `matches/tests/test_owner_mood.py` (pure-unit) +
`test_owner_evaluation_model.py` + `test_owner_evaluations_writer.py` + `test_owner_evaluation_view.py`
+ `test_reassign_team.py` (NEW) + extended `test_league_next_season.py` / `test_league_dashboard.py` /
`test_season_dashboard_view.py`.

### CAR-03 · [DONE] Career isolation from multiplayer

The firing mechanic and team-switching only apply in single-user career mode. In multiplayer leagues,
each user is locked to their team for the full duration of the league — no transfers, no firing.

**Status: DONE.** A **defensive gate only** — the CAR-02 owner-evaluation / firing / reassignment
lifecycle is made **inert unless `League.mode == "league"`**. The gate predicate is a **positive
allowlist** `mode == "league"` (so `"sandbox"`, `"multiplayer"`, and any future mode are inert by
default), expressed by the single shared helper `matches/league_views.py::_is_career_league(league) ->
bool` (`return league.mode == "league"`) — the **one source of truth** consumed by the writer, the two
reassign views, and the dashboard context. The **chokepoint** is the lazy writer
`_ensure_owner_evaluations`, which now **early-returns** when `not _is_career_league(league)`: no
`OwnerEvaluation` rows are ever written for a non-career League. The two reassign-path views
`new_team_picker` + `reassign_team` add a `not _is_career_league(league)` guard **after** the
`get_object_or_404(League, ...)` and **before** any `current_team` write ⇒ **HTTP 400** (writes
nothing). `_build_dashboard_context` gains an `is_career_mode: bool` key; both
`templates/seasons/dashboard.html` and `templates/leagues/dashboard.html` split the "Start Next Season"
control on it — career ⇒ the CAR-02 `…-owner-evaluation-link`, non-career ⇒ a direct `next_season` POST
`…-next-season-form` (the pre-CAR-02 LG-01e shape) — with the outer `…-action-button` wrapper +
`data-action-state="start_next_season"` preserved in BOTH arms (LG-01c/e back-compat).

**Deliberately NOT guarded.** `next_season` is **UNCHANGED** — the writer no-op means its verdict gate
reads `evaluation is None` and rolls the Season normally (no firing, no New-Team redirect).
`owner_evaluation` is **UNCHANGED** — it naturally raises its existing `Http404` on the missing row for
a non-career completed Season; no explicit guard added (the natural 404 suffices).

**Scope-out (locked).** **No multiplayer creation flow / form field / new mode value**; no
`Manager`/`User` model (the Manager is `League.current_team`, CAR-01); **no model field change, no
migration, no simulator or RNG change → no Score Calibration re-baseline**; **no new ADR**; **no new
CONTEXT.md term** (the **Owner evaluation** glossary entry already carries the league-mode-only clause,
added this session). Seam contract:
[`.claude/worktrees/car-03-career-isolation-seam-contract.md`](.claude/worktrees/car-03-career-isolation-seam-contract.md);
impl note in [`matches/CLAUDE.md`](laserforce_simulator/matches/CLAUDE.md) `## CAR-03 career isolation
from multiplayer`. Tests extend the existing CAR-02 files with a `mode="multiplayer"` fixture:
`test_owner_evaluations_writer.py` (0 rows written) + `test_league_next_season.py` (302 + new draft
Season, no eval row, `current_team` unchanged) + `test_reassign_team.py` (400 on both views,
`current_team` unchanged) + `test_league_dashboard.py` / `test_season_dashboard_view.py` (multiplayer
renders `…-next-season-form`, not `…-owner-evaluation-link`; `league` mode unchanged).

### FIN-01 · [DONE] Team finance subsystem (lights up the dormant *money* mood factor)

The finance epic CAR-02 deferred: introduce **player salary**, a per-team **budget** (allocations
across *house* / *coaches* / *analysts*), and **season profit** accounting (revenue vs. expenses over
a Season), so the **dormant `OwnerEvaluation.money_delta` / `money_total` mood factor** CAR-02 shipped
as a permanent `0.0` column comes **alive**. The activation seam is the ZenGM formula already pinned in
[`Screenshots_and_video_examples/firing_rules/firing_rules.md`](Screenshots_and_video_examples/firing_rules/firing_rules.md):
`money = (profit - expectedProfit) / scale` (expected profit / scale tracking ZenGM's
`15 * salaryCapFactor` / `100 * salaryCapFactor`), capped per-factor at `+1` exactly like *wins* /
*playoffs*. CAR-02 left the *money* column dormant **by design** so this slice lights it up **without a
migration to `OwnerEvaluation`** — the writer (`_ensure_owner_evaluations`) simply starts feeding a
non-zero `money_delta` once the budget feature is enabled, and the eval screen's
`owner-evaluation-factor-money` row stops rendering `0.0`. This also **unblocks ZenGM's luxury-tax
challenge-mode firing** (CAR-02 deferred it precisely because it needs a budget / expenses model). Adds
a **CONTEXT.md** finance vocabulary (Salary / Budget / Profit / Luxury tax) and an **ADR** for the
finance model + the season-profit accounting interaction with the rollover; depends on **CAR-02**
(the owner-mood model + the dormant *money* seam) and sits in career mode alongside the CAR slices.

**FIN-01 scope decisions (grill 2026-06-16, ZenGM finance docs in
[`Screenshots_and_video_examples/finance_system/`](Screenshots_and_video_examples/finance_system/)
are the base model).** Budgets are **three** levels — **scouting / coaching / facilities** (health
deferred, see FIN-04) — each a 1–100 ZenGM `level` (`DEFAULT_LEVEL = 34` neutral) plus a ticket price.
**Cost-only this slice:** all three budgets are pure expense line-items feeding `profit`; **facilities
additionally feeds the revenue/attendance side**; scouting & coaching buy **no gameplay edge yet**
(wiring deferred to FIN-02 / FIN-03). **Player salary** is **derived from `overall_rating`** (cap-scaled,
no contracts / free agency); **payroll** = sum of the active roster's salaries. **Revenue** is
**season-level** (we batch-sim, no per-game `writeTeamStats` stream) computed at the `next_season`
rollover, keeping ZenGM's **hype** loop (`winp`-driven) but a **fixed single market** (no `pop` / popRank
variance). **Luxury tax + min-payroll penalty** ship as expense lines; **tax redistribution is skipped**;
the **luxury-tax challenge-mode firing stays deferred** (write the term, no toggle). A per-`(team, Season)`
**finance snapshot** row (the `PlayerSeasonRating` / `OwnerEvaluation` precedent) is written at rollover
and read by `_ensure_owner_evaluations` to feed `money_delta = (profit − expectedProfit) / (100 *
salaryCapFactor)`. **Per-League finance toggle at create time** (the ZenGM `budget` master switch): OFF ⇒
the whole subsystem is **inert** — no salaries, no budget spending, every sim team on defaults, the money
axis stays `0.0` — so a finance-OFF league is **byte-identical to today** (wins + playoffs sentiment
only), and LG-04 / LG-05 are untouched. The LG-01h **Finances** placeholder (sidebar + topbar) becomes
the live **Team Finances** page + a **League Finances** table.

**Status: DONE.** Shipped per the grill decisions above. The **money axis is live**: when a League has
`finance_enabled`, `_ensure_owner_evaluations` reads the managed Team's per-Season finance profit and
feeds `money_delta = (profit − 15*scf) / (100*scf)` (`scf = salary_cap / BASELINE_SALARY_CAP`,
`== 1.0` this slice) into the cap-chain CAR-02 left dormant — **no `OwnerEvaluation` migration** (CAR-02
designed the seam for exactly this) and **no `owner_mood` change** (the verdict already summed `money`).
A per-League **`finance_enabled` toggle** (set at create time) gates the whole subsystem ON TOP of
CAR-03's `_is_career_league` mode gate; **OFF ⇒ inert and byte-identical to today** (zero finance rows,
`Player.salary` stays `None`, every sim team on neutral budget defaults, the money axis `0.0`, LG-04/
LG-05 develop output unperturbed) — the load-bearing inertness guarantee.

**Model + migrations.** `teams.Player.salary` (nullable `FloatField`, derived from `overall_rating`
cap-scaled, recomputed in place at the LG-05 write sites) + five `teams.Team` finance fields
(`budget_scouting`/`budget_coaching`/`budget_facilities` neutral `34` ZenGM levels, `ticket_price`,
`cash`) in `teams/migrations/0013_player_salary_team_finance.py`; `matches.League.finance_enabled`
(`BooleanField(default=False)`) + an **immutable** per-`(Team, Season)` `matches.TeamSeasonFinance`
snapshot (5 revenue lines + 6 expense lines + derived `revenue`/`expenses`/`profit` + carried `hype`;
`team` SET_NULL / `season` CASCADE; `uniq_team_season_finance`) in
`matches/migrations/0050_league_finance_teamseasonfinance.py` (dep `0049_ownerevaluation` + cross-app
`teams 0013`). Both **AddField/CreateModel-only — NO `RunPython`/backfill** (ADR-0004 disposable-data
posture; the lazy writer fills rows in Season order on first reach).

**Pure module + orchestration.** A Django-free `matches/finance.py` (frozen import allowlist
`dataclasses`/`typing`/`math`/`collections` — **no `random`**, defended by `TestNoDjangoImportsLeaked`)
holds the **locked-but-tunable** magic constants (`DEFAULT_LEVEL = 34`, `EXPECTED_PROFIT_BASE = 15`,
`SALARY_CAP = BASELINE_SALARY_CAP = 90000`, luxury/min-payroll thresholds, revenue/expense coefficients
— the LG-04 age-curve precedent, sized so a typical Season's profit lands near the `15` anchor), three
frozen dataclasses (`RevenueLines` / `ExpenseLines` / `TeamFinanceResult`), and nine pure fns
(`level_to_amount`, `salary_for_overall`, `compute_hype` — the `winp`-driven `0.55`-anchor loop,
`season_revenue`, `season_expenses`, `luxury_tax`, `min_payroll_penalty`, `season_profit`,
`money_delta`) behind the single aggregator `compute_team_finance(...)`. The flat inputs crossing the
view↔pure seam are ints/floats/levels ONLY — the module **never sees a Django object or RNG**.
`matches.league_views._ensure_team_finances(league, up_to_season)` is the **lazy + idempotent writer**
(twin of `_ensure_owner_evaluations`; first-line early-return when not career or not `finance_enabled`;
`get_or_create`-keyed on `(team, season)`, walks completed Seasons **oldest→newest** so hype carries;
first-season seed `prev_hype=0.0`/`winp_old=0.5`; `team.cash += profit`). Salary is recomputed in
`_write_baseline_ratings` + `_develop_league_for_new_season` (the LG-05 `potential` precedent, gated on
`finance_enabled`). **Rollover order (LOCKED) in `next_season`:** `_ensure_team_finances` **before**
`_ensure_owner_evaluations` (finance rows feed the money axis), then the verdict gate.

**UI.** `CreateLeagueForm` gains a `finance_enabled` checkbox (DOM id `league-create-finance-enabled`).
The LG-01h **Finances** placeholders flip live (the LG-01z pattern): two new league-screen views
`team_finances` (GET + budget-edit POST, keyed on `current_team`, history + budget levels + ticket
price + cash + live luxury/min-payroll figures) and `league_finances` (GET-only league-wide table); URL
names `team_finances` / `league_finances` replace the `coming_soon_*` placeholders; `_FEATURE_REGISTRY`
drops the two finance blockers; `_build_league_sidebar_links` repoints both sidebar + topbar Finances
entries at once; templates `leagues/team_finances.html` + `leagues/league_finances.html`. A
finance-disabled League shows a `*-disabled-notice` in place of the body. (`players_trade` /
`players_trading_block` stay blocked — FIN-01 adds salary but NOT contracts/cap-space.)

**Scope-out (locked).** **Cost-only this slice** — scouting & coaching buy NO gameplay edge yet (wiring
= **FIN-02** / **FIN-03**). **Health budget + injuries = FIN-04** (only three of ZenGM's four budgets
ship). **Tax redistribution SKIPPED**; the **luxury-tax challenge-mode firing stays DEFERRED to FIN-05**
(term written, no toggle). **No contracts / free agency / cap space** (salary derived from
`overall_rating`). **Fixed single market** (no pop / popRank / per-game gauss). **Finances consume no
RNG, are outside SIM-07/08, change no sim mechanic → NO Score Calibration re-baseline**; LG-04/LG-05
develop output is byte-identical toggle ON or OFF. **No new CONTEXT.md term** (the `### Finance`
glossary + the two "money dormant" caveat edits were finalised at the grill). Decision:
[ADR-0027](docs/adr/0027-team-finance-subsystem.md). Seam contract:
[`.claude/worktrees/fin-01-seam-contract.md`](.claude/worktrees/fin-01-seam-contract.md); impl note in
[`matches/CLAUDE.md`](laserforce_simulator/matches/CLAUDE.md) `## FIN-01 team finance subsystem`.
Tests: `matches/tests/test_finance.py` (pure-unit) + `test_team_finance_model.py` +
`test_team_finances_writer.py` + `test_finance_money_axis.py` + `test_finance_toggle.py`
(byte-identical-OFF invariant) + `test_create_form_finance.py` + `test_finance_screens.py`.

### FIN-02 · [DONE] Wire the *coaching* budget into player development (LG-04)

**Status: DONE.** Lights up the **coaching** budget FIN-01 shipped cost-only: a Team's effective
coaching level now **directionally scales** its players' LG-04 age-curve development at each
`next_season` rollover — better coaching speeds development, neglected coaching slows it — while
a **finance-OFF (or `coaching_effect=0.0`) league stays byte-identical to LG-04** and **Potential
(LG-05) is left untouched** (coaching never touches Potential; FIN-03 owns scouting→potential).
**Mechanism (no new RNG draw).** `matches/development.py::develop_player_stats(stats, age, rng, *,
coaching_effect=0.0)` gains a **keyword-only** `coaching_effect` float; **between the single
`base_change_noise` gauss draw and the 19 `develop_stat` `uniform(0.4, 1.4)` draws**, the
per-player **effective base change** is scaled **once** —
`effective *= 1 + _sign(effective) * coaching_effect` (a tiny module-private `_sign(x)` helper
returning `-1.0`/`0.0`/`+1.0`, **no `math` import** — the frozen `dataclasses`/`typing`/`random`/
`collections` allowlist holds). The `_sign` factor makes the scale **directional**: a positive
coaching effect pushes a gaining player **up** and a declining player **toward 0** (slower decline),
a negative effect does the reverse — coaching is a multiplier on the magnitude-with-sign, not a flat
add. The scale happens **after** the gauss and **before** the 19 uniform draws, so the pinned
**1-gauss-then-19-uniform** RNG sequence is **unperturbed** and a seeded develop is reproducible;
`coaching_effect=0.0` (the default) ⇒ multiplier **exactly 1.0** ⇒ **byte-identical to LG-04**.
`develop_stat` is **unchanged** (the scaled `effective_base_change` still flows through it as before).
**Mapping (lives in `finance.py`, not `development.py`).** New `matches/finance.py::coaching_effect(level)
-> float` + constant **`MAX_COACHING_EFFECT = 0.09`**, reusing FIN-01's `_bound` + `DEFAULT_LEVEL`
(34) / `MAX_LEVEL` (100): the 1–100 ZenGM level maps to **`0.0` at level 34** (neutral), **`+0.09`
at 100**, **`-0.045` at 1** (linear each side of the neutral pivot). The level→effect mapping is
**deliberately homed in `finance.py`** — `development.py` keeps its frozen no-finance import allowlist
and only ever sees the resolved **float**; the view threads it across the seam.
**Model + migration.** `matches.TeamSeasonFinance` gains `budget_scouting` / `budget_coaching` /
`budget_facilities` (`PositiveSmallIntegerField(default=34)`) + `games_played` (`PSI(default=0)`),
all inserted **between `hype` and `created_at`**, in
**`matches/migrations/0051_teamseasonfinance_budget_levels.py`** (dep
`0050_league_finance_teamseasonfinance`, **4× `AddField`, NO `RunPython`/backfill** — the ADR-0004
disposable-data posture). The snapshot now carries the budget levels **and** the games count so the
multi-Season games-weighted average has its inputs on the row.
**Writer + wiring.** `_ensure_team_finances` now snapshots the four new fields, with `games_played`
= that Team's **regular-season `matches_played`** for the Season (via
`Season._final_standings_for_phase(...)`). New `matches/league_views.py::_coaching_effect_by_team(league,
latest_completed) -> dict[int, float]` (**gated on `league.finance_enabled`** — OFF ⇒ `{}`) computes,
per Team, a **games-weighted mean of `budget_coaching` over the last ≤3 completed-Season
`TeamSeasonFinance` rows** (`Sum(level*games)/Sum(games)`, current-level fallback when there is no
history), then maps it through `finance.coaching_effect(...)`. The dict is threaded into the rollover
develop call as `coaching_effect=coaching_by_team.get(player.team_id, 0.0)` —
**active + bench players get their Team's effect; free-agent-pool players get `0.0`** (no Team). **Call
order:** `_ensure_team_finances` runs **before** the rollover/develop so `latest_completed`'s snapshot
is already inside the ≤3-Season window. This games-weighted smoothing honours the CONTEXT.md
**Budget level** multi-Season-average contract (ZenGM `getLevelLastThree`).
**Scope / decisions (LOCKED).** Potential (LG-05 `_project_stat_noise_free` / `compute_potential`)
**untouched** (FIN-03 owns scouting→potential). **Backend-only** — no UI / template change. **No
Score Calibration re-baseline** (coaching mutates Stat *inputs* for finance-ON leagues only, changes
no simulation *mechanic*; finance-OFF stays byte-identical). The decision is recorded as the **FIN-02
Consequences addendum on [ADR-0024](docs/adr/0024-zengm-player-development-ratings-history.md)** — the
ADR that recorded the coaching-knob deferral — so **no new ADR**. CONTEXT.md gained the **Coaching
effect** term + Budget / Player-development edits (finalised this session). Seam contract:
[`.claude/worktrees/fin-02-coaching-development-seam-contract.md`](.claude/worktrees/fin-02-coaching-development-seam-contract.md);
impl note in [`matches/CLAUDE.md`](laserforce_simulator/matches/CLAUDE.md) `## FIN-02 coaching budget
into player development`. Depends on **FIN-01**. Tests: `matches/tests/test_finance.py`
(`coaching_effect` mapping + `MAX_COACHING_EFFECT`) + `test_development.py` (the directional scale,
`coaching_effect=0.0` byte-identical, the no-new-RNG-draw invariant) + `test_team_finances_writer.py`
(the four new snapshot fields + `games_played`) + `test_league_next_season.py`
(`_coaching_effect_by_team` games-weighted mean, OFF ⇒ `{}`, free-agents get `0.0`).

Follow-up to FIN-01 (which ships the coaching budget as a cost-only line-item). Wire the team's effective
**coaching** level (`getLevelLastThree("coaching")` → ZenGM `coachingEffect`, `±0.10` around
`DEFAULT_LEVEL = 34`) into the **LG-04** age-curve development at the `next_season` rollover — better
coaching speeds development, neglected coaching slows it. Requires a level→effect mapping onto
`matches/development.py` (the LG-04 coaching knob was shipped **fixed at 0** precisely as this deferral;
see [ADR-0024](docs/adr/0024-zengm-player-development-ratings-history.md)). **Finance-OFF leagues keep
coaching effect at 0** (byte-identical to LG-04 today). Changes seeded develop output for finance-ON
leagues (Stat inputs only, **no Score Calibration re-baseline**). Depends on **FIN-01**.

### FIN-03 · [DONE] Wire the *scouting* budget into player potential (LG-05)

Follow-up to FIN-01 (which ships the scouting budget as a cost-only line-item). Wire the team's effective
**scouting** level into **LG-05** `compute_potential`'s scouting-noise band — better scouting tightens the
potential estimate, neglected scouting widens it. Requires mapping the 1–100 ZenGM level onto LG-05's
`scouting_budget` `[0,100]` seam, which **FIN-01 leaves fixed at `DEFAULT_SCOUTING_BUDGET = 50`** (the
LG-05 / CAR-01 deferral). **Finance-OFF leagues keep `scouting_budget = 50`** (byte-identical to LG-05
today). Changes the seeded potential estimate for finance-ON leagues (read-only to the simulator, **no
Score Calibration re-baseline**). Depends on **FIN-01**.

**Status: DONE.** Lights up the **scouting** budget FIN-01 shipped cost-only — the **structural mirror of
FIN-02** (coaching→development): a Team's effective scouting level now sets the **width of its players'
LG-05 Potential-estimate band** at each `next_season` rollover (and at founding) — better scouting
**tightens** the estimate around the noise-free projection, neglected scouting **widens** it — while a
**finance-OFF (or `scouting_budget=50`) league stays byte-identical to LG-05** and **development (LG-04) is
left untouched** (scouting never touches realised Stats; FIN-02 owns coaching→development). **Mapping
(`finance.py`, NOT `development.py`).** NEW pure fn `matches/finance.py::scouting_budget(level: int) ->
float` + constants `NEUTRAL_SCOUTING_BUDGET = 50.0` / `MAX_SCOUTING_BUDGET = 100.0`, reusing FIN-01's
`_bound` + `DEFAULT_LEVEL` (34) / `MAX_LEVEL` (100). Unlike FIN-02's dual-slope neutral-pivot
`coaching_effect`, this is **single-slope** anchored at `DEFAULT_LEVEL`→neutral / `MAX_LEVEL`→max — yields
**level 1 → 25.0, 34 → 50.0, 100 → 100.0**; `NEUTRAL_SCOUTING_BUDGET` just **equals** LG-05's
`DEFAULT_SCOUTING_BUDGET = 50` by value (**`finance.py` MUST NOT import `development`**; the frozen
no-Django allowlist holds). `compute_potential` / `DEFAULT_SCOUTING_BUDGET` / `POTENTIAL_MAX_SD = 8.0` are
consumed **verbatim** — the band `sd = POTENTIAL_MAX_SD * (1 − scouting_budget/100)` and its
**one-`rng.gauss`-draw-always** guarantee are LG-05's, unperturbed; FIN-03 only threads a different float
into the existing keyword arg. **Wiring (`league_views.py`).** NEW `_scouting_budget_by_team(league,
latest_completed) -> dict[int, float]` (the twin of FIN-02's `_coaching_effect_by_team`; **gated on
`finance_enabled`, OFF ⇒ `{}`**; per developing Team the games-weighted mean of `budget_scouting` over the
last ≤3 completed-Season `TeamSeasonFinance` rows with a current-`Team.budget_scouting` fallback, mapped
via `finance.scouting_budget`), threaded into CHANGED `_develop_league_for_new_season` as
`scouting_budget=scouting_by_team.get(player.team_id, development.DEFAULT_SCOUTING_BUDGET)` (pool players →
default 50); CHANGED `_write_baseline_ratings` builds a per-team **current-level** band map (founding pass,
no completed Season) and threads the same kwarg (finance-ON only). Finance OFF ⇒ `{}` / no band map ⇒ every
`.get` yields 50 ⇒ **byte-identical to LG-05** (regression-pinned).

**Scope addition (user-decided): strength-seed AI + manager budgets at create.** NEW
`_seed_team_budgets_by_strength(teams) -> None` (`league_views.py`) at `league_create`, **finance-ON only,
BEFORE `_write_baseline_ratings`** (so the baseline reads seeded levels): ranks enrolled teams by **mean
active-roster `overall_rating` desc** (tie-break `team_id` asc), assigns a **rank-linear** level across the
band `[SEED_BUDGET_MIN, SEED_BUDGET_MAX] = [20, 90]` (strongest → 90, weakest → 20; **single team →
`SEED_BUDGET_SINGLE = 55`**; int), and sets the **SAME** level on **all three** budget fields
(`budget_scouting` / `budget_coaching` / `budget_facilities`) for **every** team **including
`League.current_team`** (the manager edits theirs later), persisted via `bulk_update`. Seeded **ONCE at
create, frozen forever** — `next_season` carries Team rows forward untouched (NO re-seed). CPU teams never
adjust their budgets this slice; a future "CPU teams adjust budgets" feature is explicitly **deferred**, the
manager's own Team stays editable on the Team Finances screen.

**Scope-out (locked).** **Development untouched** (LG-04 `develop_player_stats` / `develop_stat`
byte-unchanged; coaching→development = FIN-02). **Estimate-precision only** — scouting changes the *seeded
Potential estimate*, never a realised Stat, read-only to the simulator. **The level→float map is in
`finance.py`, never `development.py`** (`NEUTRAL_SCOUTING_BUDGET` only *equals* LG-05's
`DEFAULT_SCOUTING_BUDGET` by value). **NO migration** (`TeamSeasonFinance.budget_scouting` / `.games_played`
+ the `Team.budget_*` fields already exist), **NO simulator change**, **NO Score Calibration re-baseline**
(estimate-only, finance-ON only; finance-OFF byte-identical). **No new ADR** (FIN-03 Consequences addendum
on [ADR-0027](docs/adr/0027-team-finance-subsystem.md)). **No new CONTEXT.md term beyond the already-written
Scouting estimate edge** entry (+ the Potential pointer CAR-01→FIN-03 / Budget avoid-line edits). Seam
contract: [`.claude/worktrees/fin-03-seam-contract.md`](.claude/worktrees/fin-03-seam-contract.md); impl
note in [`matches/CLAUDE.md`](laserforce_simulator/matches/CLAUDE.md) `## FIN-03 scouting budget into player
potential`. Tests (all **extended**, not new files): `matches/tests/test_finance.py` (`scouting_budget`
mapping 1→25.0 / 34→50.0 / 100→100.0 + clamps) + `test_league_create.py`
(`_seed_team_budgets_by_strength` — rank-linear `[20, 90]`, single→55, all three fields equal,
`current_team` included, finance-ON-before-baseline) + `test_league_next_season.py`
(`_scouting_budget_by_team` games-weighted ≤3-Season mean + fallback, **OFF ⇒ `{}`**, byte-identical-OFF
Potential rows, AI-budget-frozen-across-rollover).

### FIN-04 · [DONE] Health budget + injury/availability system

The **health** budget category FIN-01 deferred (ZenGM: health spending shortens injuries). Introduces a
minimal **injury / availability** model so the fourth budget category buys a real edge (fewer / shorter
unavailabilities), then wires the **health** level into it. This is the only one of the four ZenGM
categories with **no existing seam** in our domain (we have no injuries today), so it carries the most new
surface — its own grill. Depends on **FIN-01** (the budget-level + finance-toggle infrastructure) and is
sequenced after FIN-02 / FIN-03.

**Status: DONE.** Lands the **fourth ZenGM budget** — a per-Team **health budget** (a cost line plus a
ratings edge expressed as injury *duration*) backed by a minimal **injury / availability** model.
Architecture (LOCKED): injuries roll **OUTSIDE the tick loop**, are resolved **in-memory at fixture time**
(auto-substitute or play-hurt), and a per-Player availability counter is decremented once per fixture the
Team plays. **Finance-gated** on `_is_career_league(season.league) AND season.league.finance_enabled` —
OFF (or sandbox / multiplayer) ⇒ no rolls, no roster mutation, no `games_unavailable` change ⇒
**byte-identical to today**; the simulator is **byte-untouched** (no signature change, no
`before_round_hook`) ⇒ **NO Score Calibration re-baseline**. **Per-fixture, regular-season round-robin
fixtures only** (tournaments/playoffs untouched). **Subjects = the 6 active-roster STARTERS only** (bench /
free-agent fill-ins never roll, never tracked — no orphan injuries); a healthy fielding starter rolls a new
injury, an already-unavailable starter decrements without re-rolling, and each now-unavailable starter is
resolved by the Team's `injury_policy` — **`auto_sub`** (rewrite the in-memory `slot_*` FK to a substitute,
priority bench → League free-agent pool) or **`play_hurt`** (subtract the per-stat penalty from the injured
Player's 19 stats), with **`play_hurt` as the universal no-sub fallback** so the roster always resolves to a
valid 6. The health edge reads `finance.health_effect(Team.budget_health)` from the **LIVE current
`budget_health`** at fixture time (NOT the games-weighted ≤3-Season smoothing FIN-02/03 use) and scales the
drawn injury **DURATION** down only — frequency is a fixed base rate × age. **No re-baseline.**

**Model + modules.** NEW fields `teams.Player.games_unavailable` (`PositiveSmallIntegerField(default=0)`,
the availability counter — reset to 0 at `next_season` rollover), `teams.Team.budget_health`
(`PositiveSmallIntegerField(default=34)`, the fourth budget level), `teams.Team.injury_policy`
(`CharField(choices=INJURY_POLICY_CHOICES, default="auto_sub")` — `auto_sub` / `play_hurt`), and
`matches.TeamSeasonFinance.health_cost` (`FloatField(default=0.0)`, after `min_payroll_penalty`). Migrations
`teams/migrations/0014_player_team_health_injury.py` (dep `0013`; 3× `AddField`) +
`matches/migrations/0052_teamseasonfinance_health_cost.py` (dep `0051`; 1× `AddField`) — both AddField-only,
**NO `RunPython`/backfill** (ADR-0004 disposable-data posture). NEW pure module `matches/injury.py`
(Django-free, frozen import allowlist `dataclasses`/`typing`/`random`/`collections`, RNG **injected**
per-fixture never the SIM seed chain): fns `age_factor` / `injury_probability` (flat base × age, **no Stat
input**) / `roll_injury` / `draw_duration` (health-edge-scaled duration) / `play_hurt_penalty`, with the
RNG-consumption order pinned (`roll_injury` then, if hit, `draw_duration`). NEW `finance.py::health_effect`
+ `MAX_HEALTH_EFFECT = 0.5`, a trailing `ExpenseLines.health` field, and a keyword-only `health_level`
threaded through `season_expenses` / `compute_team_finance` (the seventh expense line). NEW
`matches.league_views.resolve_injuries_for_fixture(season, team_red, team_blue) -> dict` /
`restore_after_fixture(token) -> None` (in-memory mutate-then-restore — **never `.save()`** the temporary
roster; only `games_unavailable` is persisted), wrapped around the `simulate_scheduled_round(...)` call in
`play_season_task` / `play_week` / `play_week_live` (the RR branch only — the playoff branch is untouched),
plus the `games_unavailable = 0` reset in `_develop_league_for_new_season` and the `health_level=` /
`"health_cost"` thread inside the gated `_ensure_team_finances`. UI: the Team Finances screen gains DOM ids
`team-finances-budget-health` / `team-finances-injury-policy` / `team-finances-availability` (+
`-availability-empty`) for the manager-editable level, policy toggle, and unavailable-players display.

**Scope-out (locked).** tournaments/playoffs untouched, no injury-type taxonomy, no in-sim injuries, no
frequency-from-health, no Stat-driven probability, FIN-05 deferred, **NO Score Calibration re-baseline**.
Decision: [ADR-0028](docs/adr/0028-health-budget-injury-availability.md). Seam contract:
[`.claude/worktrees/fin-04-health-injury-seam-contract.md`](.claude/worktrees/fin-04-health-injury-seam-contract.md);
impl note in [`matches/CLAUDE.md`](laserforce_simulator/matches/CLAUDE.md) `## FIN-04 health budget +
injury/availability`. Tests: `matches/tests/test_injury.py` (NEW, pure-unit incl. `TestNoDjangoImportsLeaked`)
+ `test_finance.py` (EXTEND — `health_effect` mapping, the seventh expense) + `test_injury_resolution.py`
(NEW, DB — starter-only subjects, roll/decrement, auto_sub + play_hurt + no-sub fallback, never-`.save()`
restore, rollover reset, byte-identical-OFF, `health_cost`→`profit`) + `test_finance_screens.py` (EXTEND —
the `budget_health` / `injury_policy` POST + the new DOM ids + the availability display).

### FIN-05 · [DONE] Luxury-tax challenge-mode firing

Re-opens the ZenGM **luxury-tax challenge-mode firing** CAR-02 and FIN-01 both deferred: an optional
per-League rule that fires the Manager outright whenever they pay the luxury tax in a completed Season
(independent of cumulative owner mood). FIN-01 ships the luxury-tax **expense line** that makes the trigger
computable but **not** the firing rule itself. Mirrors ZenGM's `challengeFiredLuxuryTax` game attribute
(default off). Depends on **FIN-01** (the payroll + luxury-tax model) and on the CAR-02 firing lifecycle.

**Status: DONE.** Lands the deferred **luxury-tax challenge fire** — an **optional per-League rule**
(`League.challenge_fired_luxury_tax`, default off, **set at create only, no mid-League edit surface**) that
fires the Manager **outright** any completed Season their Current team pays the luxury tax, **independent of
cumulative owner mood** — the faithful analogue of ZenGM's `challengeFiredLuxuryTax`. It **respects the same
Grace period** as mood firing (no luxury fire during grace) and is **checked FIRST inside the past-grace
gate** so it takes precedence over the mood verdict. The firing **reason** is persisted immutably on the
`OwnerEvaluation` row and surfaced as a distinct flavour line on the owner-evaluation screen. **No simulator
change → NO Score Calibration re-baseline.** **Silently inert** when team finances are OFF (no
`TeamSeasonFinance` row ⇒ nothing to fire on) or outside career mode (the writer early-returns, the CAR-03
posture) — **no cross-field form validation** (a toggle on a non-finance League is harmless).

**Locked seam.** The decision stays in the single pure decider: `matches/owner_mood.py::decide_verdict` gains
**two keyword-only bools, both `default False`** — `luxury_tax_paid` and `challenge_fired_luxury_tax` — and a
**new branch placed FIRST inside the existing `past_grace` block, BEFORE the mood `total <= FIRE_THRESHOLD`
check**: `if past_grace and challenge_fired_luxury_tax and luxury_tax_paid: return Verdict("fired", 0)`. The
mood / hot-seat / retained branches are **byte-unchanged**, only shifted down; the default-`False` bools mean
**zero blast radius** on every existing caller. The module stays **Django-free** (the two new params are plain
bools, no new import — the frozen `dataclasses`/`typing`/`collections` allowlist holds, `TestNoDjangoImportsLeaked`
stays green) and the frozen `Verdict` dataclass is **UNCHANGED** (no `fired_reason` on the pure seam — the
reason is persisted by the writer, never carried through the decider). The firing reason lands on a NEW
**`OwnerEvaluation.fired_reason`** (`CharField(max_length=16, choices=FIRED_REASON_CHOICES, default="")`;
choices `""` / `"owner_mood"` / `"luxury_tax"`; legacy pre-FIN-05 fired rows default `""` and render as the
mood message) plus the NEW **`League.challenge_fired_luxury_tax`** (`BooleanField(default=False)`), both
shipped in **`matches/migrations/0053_fin05_luxury_tax_firing.py`** (2× `AddField`, dep
`0052_teamseasonfinance_health_cost`, **no `RunPython`/backfill** — the ADR-0004 disposable-data posture).
The writer **`_ensure_owner_evaluations`** **reuses the existing per-Season `tsf` lookup** FIN-01 already
fetches for the money axis (`luxury_tax_paid = tsf is not None and tsf.luxury_tax > 0`), passes both bools
into `decide_verdict`, reconstructs `fired_reason` from the same inputs (`"luxury_tax"` on a challenge fire,
else `"owner_mood"` on any other fire, else `""`), and stamps it into the existing
`OwnerEvaluation.objects.get_or_create(... defaults={...})` block — and on a challenge fire **still records the
`wins`/`playoffs`/`money` deltas and cap-chains the cumulative totals exactly as today** (the challenge only
flips `verdict.outcome` → `"fired"` and `fired_reason` → `"luxury_tax"`; nothing about the factor math, the
running totals, or the tenure derivation changes). **`next_season` is UNCHANGED** — a challenge fire produces
`verdict == "fired"` and routes to `new_team_picker` by the existing reason-independent gate. The create form
gains a **`CreateLeagueForm.challenge_fired_luxury_tax`** toggle (DOM id `league-create-challenge-luxury-tax`,
no `clean()` change) threaded through `league_create`'s `League.objects.create(...)`, and the eval screen
gains a distinct **fired-reason flavour element** (DOM id `owner-evaluation-fired-reason`) keyed on
`evaluation.fired_reason` read straight off the row (no new context key — CONTEXT.md forbids recomputing a past
evaluation from current state).

**Scope-out (locked).** **No mid-League edit surface** for the toggle (create-time only). **No cross-field form
validation** (a toggle on a non-finance League is silently inert). **No `Verdict.fired_reason`** on the pure
seam (the reason is persisted on the row, never carried through `decide_verdict`). **No re-derivation of
`fired_reason`** from current state (stamped at write time, read back verbatim). **No `next_season` code
change.** **No simulator change → NO Score Calibration re-baseline.** **No backfill / `RunPython`.** **No new
ADR** (FIN-05 Consequences addendum on [ADR-0026](docs/adr/0026-manager-firing-owner-mood.md)). **No new
CONTEXT.md term.** Seam contract:
[`.claude/worktrees/fin-05-luxury-tax-firing-seam-contract.md`](.claude/worktrees/fin-05-luxury-tax-firing-seam-contract.md);
impl note in [`matches/CLAUDE.md`](laserforce_simulator/matches/CLAUDE.md) `## FIN-05 luxury-tax challenge-mode
firing`. Tests (all **extended**, not new files): `matches/tests/test_owner_mood.py` (challenge precedence
over mood, grace suppression, both-bools-required, default-off byte-identical, `TestNoDjangoImportsLeaked`) +
`test_owner_evaluation_model.py` (`fired_reason` default `""` + the three choices; `League.challenge_fired_luxury_tax`
default `False`) + `test_owner_evaluations_writer.py` (`fired_reason` values across challenge / mood / retained;
mood-recorded-normally on a challenge fire; **finance-OFF inert** byte-identical; **non-career inert**) +
`test_league_next_season.py` (challenge-fired → picker, no new Season) + `test_league_create.py` (the toggle
persists + the DOM id renders) + `test_owner_evaluation_view.py` (the `owner-evaluation-fired-reason` flavour
for `luxury_tax` vs `owner_mood`/legacy `""` vs no element when not fired).

### SUB-01 · Sub-leagues + per-sub-league rotating map pools — piece 1

**[DONE] Season `rotate_by_matchday` arena-map mode** (SUB-01 piece 1 of three). A 4th
`Season.map_mode` value `rotate_by_matchday` extending the shipped LG-01j
per-Season map config with a Season-level **author-ordered** ArenaMap rotation
keyed on **matchday alone**: a Round's map = `ordered_ids[fixture.matchday %
len(ordered_ids)]`, so every fixture on matchday N shares one arena and a
Match's two Rounds (different matchdays) rotate. This **satisfies LG-01j's
deferred "mode (c)" at the Season level** (the per-*sub-league* rotation remains
the third slice). **Fully deterministic, consumes NO RNG** (simpler than
`random_per_round`); the `map_pool` M2M stays **EMPTY** for this mode (the
ordered JSON is the sole source); **no simulator change, NO Score Calibration
re-baseline**; tournament/playoff fixtures unaffected (they never call
`_resolve_fixture_map`). Surface: `Season.MAP_MODE_CHOICES` +1 tuple + TWO NEW
`JSONField`s — `map_rotation_ids_json` (live, author-ordered) +
`starting_map_rotation_ids_json` (activation snapshot, author order PRESERVED,
the order-preserving twin of the id-sorted `starting_map_pool_ids_json`);
migration `matches/migrations/0054_season_map_rotation.py` (dep
`0053_fin05_luxury_tax_firing`; 1× `AlterField(map_mode)` + 2× `AddField`, **no
`RunPython`/backfill** — ADR-0004); a NEW `_resolve_fixture_map`
`rotate_by_matchday` branch reading `starting_map_rotation_ids_json` (no RNG,
defensive `None` on empty/deleted), with the THREE play-loop call sites
(`tasks.play_season_task`, `league_views.play_week`,
`league_views.play_week_live` RR branch) unioning **both** snapshots into one
`in_bulk`; `Season.start_season()` snapshots `list(self.map_rotation_ids_json or
[])` (author order); `CreateLeagueForm` gains a hidden `map_rotation` field +
inline order-preserving parse to `cleaned_data["map_rotation_ids"]` + the FULL
4×2 cross-guard validation matrix; `league_create` writes / `next_season`
carries the rotation forward verbatim; `_build_map_config_label` 5th branch
`"Map: Rotating (N maps: a, b, c)"` in AUTHOR order; a vanilla-JS "+ Add map"
composer in `templates/leagues/create.html` (LG-02b pattern) — dashboards
already render the label, no `SeasonAdmin` change. **No new ADR** (reversible
model fields + a deterministic branch on the existing LG-01j helper; the
CONTEXT.md `Map mode` / `Map pool` / `Per-fixture map resolution` terms already
cover the vocabulary). Seam contract:
[`.claude/worktrees/sub-01-rotate-by-matchday-seam-contract.md`](.claude/worktrees/sub-01-rotate-by-matchday-seam-contract.md);
impl note in [`matches/CLAUDE.md`](laserforce_simulator/matches/CLAUDE.md)
`## SUB-01 rotate_by_matchday arena-map mode`. (Pieces 2 (LG-07 member nights)
and 3 (first-class `SubLeague` + per-sub-league rotating pools) remain in
[`PLAN.md`](PLAN.md).)

---

## Phase 7 — Docker & Production Deployment

The app currently runs only on a local dev machine. This phase makes it deployable as a Docker container.

**Deployment target:** Fly.io (free tier — persistent storage, native Docker support, does not spin down).
**Media storage:** Cloudflare R2 (free tier — 10 GB, no egress fees, S3-compatible API).
**Deploy trigger:** auto-deploy to Fly.io on every push to `main` via CI.
**Domain:** fly.dev default subdomain for now; custom domain deferred until the project grows.
  (Custom domains on Fly.io are free — only the domain registration itself costs money.)

### DEPLOY-01 · Environment variable configuration

`settings.py` currently has `SECRET_KEY`, `DEBUG = True`, and `ALLOWED_HOSTS` hardcoded. In production
these must come from environment variables so secrets are never in the repository.

- Add `python-decouple` to `requirements.txt`
- Rewrite the relevant `settings.py` values to read from env vars with safe defaults:
  `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `DATABASE_URL`
- R2 credentials (`R2_BUCKET_NAME`, `R2_ACCESS_KEY`, `R2_SECRET_KEY`, `R2_ENDPOINT_URL`) also go here
- Add a `.env` file for local development (contains real values, never committed)
- Add a `.env.example` file (placeholder values, committed as documentation)
- Add `.env` to `.gitignore`
- completed: `python-decouple` and `dj-database-url` added to requirements.txt; settings.py reads SECRET_KEY, DEBUG, ALLOWED_HOSTS via decouple and DATABASES via dj_database_url.config(); R2 placeholders added; .env/.env.example created; .gitignore and ci.yml updated; mypy.ini added for import-untyped suppression.

### DEPLOY-02 · Production WSGI server (gunicorn)

Django's built-in `runserver` is a dev-only server — it is single-threaded and not safe for production.
`gunicorn` is the standard production server for Django.


- Add `gunicorn` to `requirements.txt`
- Confirm the app starts with: `gunicorn laserforce_simulator.wsgi:application --bind 0.0.0.0:8000`
- completed: `gunicorn>=20.1.0` added to requirements.txt; `Procfile` added at repo root for Fly.io/Heroku (`web: gunicorn ... --chdir laserforce_simulator`); `gunicorn.conf.py` added at repo root with workers=3, sync worker class, 30s timeout, stdout logging. Docker verification deferred to DEPLOY-06.

### DEPLOY-03 · Static file serving (WhiteNoise)

In production, Django does not serve its own CSS/JS/images — a separate web server normally does that.
WhiteNoise lets Django serve them directly from the container without needing a separate nginx process.

- Add `whitenoise` to `requirements.txt`
- Add `WhiteNoiseMiddleware` to `MIDDLEWARE` in `settings.py` (must come directly after `SecurityMiddleware`)
- Set `STATIC_ROOT = BASE_DIR / "staticfiles"` so `collectstatic` knows where to write files
- `collectstatic` will be run during the Docker image build step (DEPLOY-06)
- completed: `whitenoise>=6.0.0` added to requirements.txt; `WhiteNoiseMiddleware` inserted after `SecurityMiddleware` in MIDDLEWARE; `STATIC_ROOT = BASE_DIR / "staticfiles"` added to settings.py. collectstatic wiring deferred to DEPLOY-06.

### DEPLOY-04 · Media file storage (Cloudflare R2)

Uploaded map images are "media files" stored on disk by default. In a Docker container the disk is
ephemeral — files written during one deploy disappear when the container restarts. They must be stored
in Cloudflare R2 instead.

- Add `django-storages[s3]` and `boto3` to `requirements.txt`
- Configure `DEFAULT_FILE_STORAGE = "storages.backends.s3boto3.S3Boto3Storage"` in production settings
- R2 credentials added as environment variables (see DEPLOY-01) — never hardcoded
- Test: upload a map image in the editor and verify the file URL points to R2, not local disk
- completed: `django-storages[s3]>=1.14` and `boto3>=1.34` added to requirements.txt; `settings.py` uses Django 5.2 `STORAGES` dict (not deprecated `DEFAULT_FILE_STORAGE`); activates `S3Boto3Storage` when all four R2 env vars are set, falls back to local `FileSystemStorage` otherwise. Added `_get_image_local_path()` helper in `core/views.py` so OpenCV/PIL processing works with both local and remote storage (remote images are downloaded to a local cache). Added `R2_PUBLIC_URL` env var for custom domain or R2 public bucket URL. `_seed_defaults()` guarded to skip when remote storage is active. `upload_map` reads dimensions via storage API rather than `.path`; handles corrupt uploads. Real-R2 end-to-end test deferred until a bucket is provisioned.

### DEPLOY-05 · PostgreSQL database (see also API-01 in Phase 5)

SQLite writes to a single file on disk. Like media files, this disappears when a container restarts and
doesn't support multiple concurrent connections well. PostgreSQL is the production-grade replacement.

- Add `psycopg2-binary` and `dj-database-url` to `requirements.txt`
- Replace the `DATABASES` block in `settings.py` with `dj_database_url.config(default="sqlite:///db.sqlite3")`
  so SQLite is still used locally and PostgreSQL is used in production via `DATABASE_URL` env var
- Run all migrations against PostgreSQL and confirm they pass
- Update GitHub Actions to spin up a `postgres` service container for CI
- Note: this is the same work as API-01 in Phase 5 — the two can be merged/done together
- completed: `psycopg2-binary>=2.9` added to requirements.txt; `dj-database-url` already present from DEPLOY-01; CI `test` job now spins up `postgres:16` service with health checks and sets `DATABASE_URL` env var; CI `pull_request` trigger widened to fire on all PRs (not just to main/master). All 212 tests pass against PostgreSQL in CI.

### DEPLOY-06 · Dockerfile

- completed: Multi-stage build (`python:3.11-slim` builder + runtime). Builder installs system deps (`libglib2.0-0`, `libgomp1` for opencv-python-headless) and Python packages. Runtime copies site-packages from builder, runs `collectstatic` at build time with a dummy SECRET_KEY. `entrypoint.sh` runs `manage.py migrate` at container start before gunicorn. `fly.toml` added for Fly.io (app=laserforce-simulator, region=ord). `.dockerignore` excludes .git, .env, __pycache__, media/, staticfiles/. `staticfiles/` added to .gitignore.

### DEPLOY-07 · docker-compose.yml for local development

- completed: `docker-compose.yml` added at repo root. `db` service is `postgres:16` with a health check and a persistent named volume. `app` service builds from Dockerfile, mounts source code for live edits, loads `laserforce_simulator/.env`, and overrides `DATABASE_URL` to point to the compose `db` service. Run with `docker compose up`; run migrations with `docker compose run app python laserforce_simulator/manage.py migrate`.

### DEPLOY-08 · CI pipeline update

- completed: New `docker` job added to `ci.yml` (runs after `test`). Spins up a postgres:16 service, builds the Docker image, then runs a smoke test (container started with `--network host`; polls `GET /` up to 30 times; expects HTTP 200). Deploy steps (`setup-flyctl` + `flyctl deploy --remote-only`) are gated on `github.ref == refs/heads/main && secrets.FLY_API_TOKEN != ''` — silently skipped until the secret is added to GitHub Actions secrets.

---
