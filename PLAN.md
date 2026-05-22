# Development Plan

Organized by phase. Phases 0–2 are prerequisites for later phases; don't skip ahead.
Story IDs from `sm5_user_stories_v2.html` are referenced where applicable.

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

### SIM-01 · Document and test action weights
Add docstrings to every weight function in `weights.py`. Cover weight sums with unit tests. 
Provide a clearly documented constant dict so weights are adjustable without touching logic code.

### SIM-02 · Batch simulation mode
`POST /matches/simulate-batch/` — accepts `red_team_id`, `blue_team_id`, `n` (10/50/100/500). 
Runs `ResourceBasedSimulator` n times, returns aggregate stats (win%, avg score, avg survivors, 
score distribution histogram). Uses simple in-process threading when the run exceeds ~5 seconds;
results are not stored as permanent Match records.

### SIM-04 · Simulation confidence display
Per-player data source label ("40 real games" vs "Role defaults — no history") on simulation summary. 
Team-level confidence badge: Low (<5 games), Medium (5–20), High (>20). Link to edit stats from confidence panel.

### SIM-05 · Simulation replay playback
Play/Pause/Step/Speed controls on the events page. Event timeline highlights current event; 
live resource counters update per event. State managed client-side; no additional backend endpoint needed.

### HX-01 · Per-player career stats page
`/teams/<id>/player/<pid>/stats/` — aggregated `PlayerRoundState` across all rounds: games played, 
avg points, K/D ratio, avg survival time, avg accuracy, avg SP earned. Per-role breakdown. 
Trend chart: avg points per game over time.

### HX-02 · Role benchmarks
Global benchmark averages per role computed from all `PlayerRoundState` records. 
Player stat shown with +/− delta and percentile rank vs role average. Recomputed on demand or nightly.

### HX-03 · Head-to-head record
`/matches/h2h/?team_a=<id>&team_b=<id>` — W/L record, avg score margin, avg survivors, 
most impactful player across all H2H matches.

### PR-01 · Pre-match win probability forecast
`/matches/forecast/?red=<id>&blue=<id>` — triggers 100-sim batch (requires SIM-02 and STAT-02). 
Shows win% per team, projected score range (10th–90th percentile), projected avg survivors, per-player risk flags.

### PR-02 · Roster composition comparison
Two side-by-side roster selectors vs same opponent, each running 100 sims. Side-by-side win%, avg score, avg survivors.
Recommended scenario highlighted with rationale.

### PR-03 · What-if scenario editor
Fork a real `GameRound`, change one variable (swap role, adjust stat, change player), 
re-simulate, show diff vs original. Forked scenario is temporary, not a permanent Match record.

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

### LG-00 · Player Generation Tools
Generate a full set of randomized players for a league, season, or tournament. The generation UI accepts:
- Number of teams and players per team
- Bell curve mean and variance for stat distribution (configurable per generation run)

Stats are randomized on the configured bell curve. Intended to bootstrap new leagues quickly.

### LG-00b · Roster Import from CSV
Allow users to import a roster of players from a CSV file. Required columns: player name, role.
All 19 stat columns are optional — unspecified stats default to 50 on import.

### LG-01 · Seasons and standings
New `Season` model: name, start/end dates, enrolled teams (M2M). Standings: W/L/T, points (3W/1T/0L),
round wins, total score. Matches linked to season via FK. Active vs completed states.
This should look at the screenshots existing within the /Screenshots_and_video_examples/ directory.

### LG-02 · Tournament formats
Support the following tournament types:

- **Single elimination** — 4/8/16 teams; standard knockout bracket.
- **Double elimination** — losers get a second chance via the losers bracket.
- **Round robin** — all teams play each other; used for seeding.
- **Round robin → Double elimination** — round robin seeding phase feeds into a double elimination finals.
- **Swiss** — pairings based on current standings; rounds auto-calculated from participant count
  (typically ⌈log₂(N)⌉), overridable by tournament admin.
- **Random Draw** — a pool of individual players with no pre-set teams. When all participants
  are registered, the system randomizes team assignments. Tournament admin reviews and edits
  the assignments, then confirms to lock them in. Format runs as Round Robin → Double Elimination.
- **Duos** — players register as pairs. Pairs are placed on 6v6 teams alongside other pairs.
  Pair performance is tracked independently across games. Requires `TournamentSubGroup` model.
- **Trios** — same as Duos but groups of three.

**TournamentSubGroup model:** links players as partners within a specific tournament. Used by Duos and Trios
to track sub-group performance independently of the full team result.

Bracket rendered as a visual tree. Results auto-advance winners.
This should look at the screenshots existing within the /Screenshots_and_video_examples/ directory.

### LG-03 · Season-end awards
Computed from `PlayerRoundState` aggregates: Most Points, Highest K/D by role, Best Medic, 
Most Efficient Nuke, Best Accuracy. Awards page at `/seasons/<id>/awards/`. Award badge on player profile.

### LG-04 · Season-end stat updates
At the end of each season, all players (on active teams or otherwise) receive a stat update.
The update factors in:
- **New experience** — games played this season
- **Player age** — older players improve more slowly
- **Prior experience** — players with more historical games have a smaller update magnitude

Default weights for these three factors are fixed in code but overridable per season by the league admin.

### LG-05 · Player potential
Each player carries a `potential` attribute: a dynamically computed estimate of their likely stat ceiling.

- Computed at each season-end stat update, not on demand.
- Derived from current player stats + the team's seasonal scouting budget allocation.
- **Scouting budget** is a per-season allocation on the team. Higher budget = more accurate `potential`
  estimate. Lower budget = noisier estimate with added randomness.
- `potential` has a floor of `overall_rating` — it can never predict a player will regress below
  their current average.
- `potential` is not exposed in the UI until this phase is complete.

---

## Phase 5.5 — Single-Player Career Mode

A single-user play mode where the user acts as a team manager navigating a league season. This phase
sits between the League system (Phase 5) and full multiplayer (Phase 6).

### CAR-01 · Manager role and team assignment
In single-player career mode, the user is a team manager (not a player in the simulation).
The user is assigned to a team at the start of a career league. Each season the user manages their
team through the league schedule.

### CAR-02 · Performance-based firing
The system tracks manager performance metrics (win rate, standings position, point differential).
When a manager's performance falls below a configurable threshold, the system fires them automatically.
After being fired, the manager can apply for or be assigned to another team in the league.

### CAR-03 · Career isolation from multiplayer
The firing mechanic and team-switching only apply in single-user career mode. In multiplayer leagues,
each user is locked to their team for the full duration of the league — no transfers, no firing.

---

## Phase 6 — Users and Multiplayer

### UX-01 · User accounts and team ownership
Django auth system (email + password). Open self-registration — anyone can create an account.
Admins can remove user accounts via Django Admin.

Permissions: only team owners can edit their teams/players; read-only access to others.
Users can see the teams, players, leagues, seasons, and tournaments they have created.

League access is **closed by default** (invite-only). League creators can set a league to open
(anyone can join) or send invitations to specific users.

Google/OAuth social login is deferred — see Deferred Items section.

### UX-02 · User–player link
Each user account may be linked to exactly one `Player` record (one-to-one). This link represents
a self-insert — the user's personal profile of what they believe their own stats are or aspire to be.
The linked player is a vanity record and does not automatically appear on any simulated team.

This should look at the screenshots existing within the /Screenshots_and_video_examples/ directory.

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

## Phase 8 — Angular Frontend Migration

Replaces Django's server-rendered HTML templates with an Angular single-page application (SPA).
Django becomes a pure API backend; Angular handles all UI in the browser. This phase requires Phase 5's
API-02 (REST API) to be complete and deployed (Phase 7) before starting.

**Approach:** migrate one feature area at a time. Django templates remain live until the Angular
equivalent is complete and verified. Django Admin is a permanent exception and is never migrated.

### ANG-01 · Harden and complete the REST API (prerequisite)
Before building Angular against it, ensure:

- All endpoints needed by the UI exist: teams, players, matches, rounds, events, maps
- Consistent JSON envelope (data, pagination, errors)
- Filtering and pagination on list endpoints
- Proper HTTP error codes (400 for validation, 404 for missing records, etc.)

### ANG-02 · CORS configuration
During development Angular runs on `http://localhost:4200` and Django runs on `http://localhost:8000`.

- Add `django-cors-headers` to `requirements.txt`
- Add `CorsMiddleware` to `MIDDLEWARE` (before `CommonMiddleware`)
- Set `CORS_ALLOWED_ORIGINS = ["http://localhost:4200"]` for dev; production domain added when known

### ANG-03 · JWT authentication
- Add `djangorestframework-simplejwt` to `requirements.txt`
- Add `/api/token/` (login) and `/api/token/refresh/` endpoints
- **Access token** stored in memory (not localStorage — avoids XSS token theft)
- **Refresh token** stored in an httpOnly cookie (survives page refresh without re-login)
- Angular `HttpInterceptor` attaches `Authorization: Bearer <token>` to every API request automatically

### ANG-04 · Angular project scaffold
One-time setup in a `/frontend/` directory at the repo root.

```bash
npm install -g @angular/cli
ng new frontend --routing --style=scss --strict
cd frontend
ng add @angular/material
```

### ANG-05 · Angular API services
One Angular service per Django API resource. Components never call `HttpClient` directly.

```
TeamsService     → GET/POST/PATCH /api/teams/
PlayersService   → GET/POST/PATCH /api/players/
MatchesService   → GET/POST       /api/matches/
RoundsService    → GET            /api/rounds/<id>/
EventsService    → GET            /api/rounds/<id>/events/
MapsService      → GET/POST       /api/maps/
```

### ANG-06 · Migrate views by feature area
Migrate one area at a time in order of complexity. Each item: build the Angular route/component,
verify feature parity with the existing Django template, then remove the Django template + view.

1. **Teams list & detail** — simple CRUD table + form; good first Angular component to build
2. **Player add/edit** — stat form with live `overall_rating` preview
3. **Match list & create** — team picker, match creation, results list
4. **Round detail** — per-player stat table, MVP scores
5. **Event timeline** — filtered event log, color-coded by type (SIM-05 replay controls slot in here)
6. **Map editor** — most complex: canvas overlay, zone painting, sight-line drag-select (migrate last)

### ANG-07 · Serve Angular from Docker (nginx sidecar)
Once Angular is built (`ng build --configuration production`), serve it via an nginx sidecar service.
nginx serves the Angular static files on port 80 and proxies `/api/` requests to the Django container
on port 8000. Add `nginx.conf` and update `docker-compose.yml` with the `nginx` service.

### ANG-08 · Remove Django template views
Once each Angular view is verified, delete the corresponding Django template file and its
HTML-serving view function. Keep the API endpoint. Update URL routing to remove the old path.
The app should have zero `.html` template files by the end of this phase, except Django Admin
(which is a permanent exception and stays indefinitely).

---

## Sequencing Summary

```
Phase 0 (Fixes) ← complete
  → Phase 7 (Docker & Deployment) ← do this first; ship the Django template UI to prod early
  → Phase 1 (Map Integration)
    → Phase 2 (Stats Integration)
      → Phase 3 (Simulation Mechanics)
        → Phase 4 (Analytics — most items can run in parallel with Phase 3)
          → Phase 5 (Infrastructure & League)
            → Phase 5.5 (Single-Player Career Mode)
              → Phase 6 (Users and Multiplayer)
                → Phase 8 (Angular Frontend Migration)
                  (requires Phase 5 API-02 REST API)
```

Phase 4 items RES-01 (accuracy %), RES-02 (SP chart), RES-03 (missile log), and SIM-01 (document weights)
are quick wins that can be done any time after Phase 0.

Phase 7 (Deployment) can be done in parallel with any feature phase — re-deploy as features land.

---

## Deferred Items

The following were explicitly scoped out and should not be implemented until re-evaluated:

- **Mirrored/reflective walls** (MAP-07) — shot-bouncing mechanic; deferred from Phase 1
- **Per-stat-per-role weight tuning** (STAT-02 follow-up) — granular multipliers per stat per role;
  deferred until baseline simulation data exists to inform the values
- **Google/OAuth social login** (UX-01) — deferred from Phase 6; email/password only for now
- **Custom domain** — deferred until the project grows; fly.dev subdomain is sufficient for now
- **Goal-recompute throttling** (MOVE-04) — behavioural perf lever (staler goals every *N* ticks);
  out of MOVE-02 scope, opened only if the MOVE-02 path cache alone is insufficient for the
  map-mode perf target

---

## Phase 4 — Highlight Surfacing & Chart Overlays (added 2026-05-21, post-RV-02)

Frontend-only follow-ons that reuse data already persisted/logged by earlier work — no new
simulation, no migration. Both build on the existing `game_round_events.html` infrastructure
(M-1 JSON windowing, the SIM-05 playback engine, and the RES-02 `_overlay_plugin` Chart.js v4
vertical-overlay pattern).

### RV-04 · Highlight overlay on the playback timeline + chart toggle
Surface the RV-02 **Highlight** list (`GameRound.highlights_json`) in two more places beyond the
Highlights tab:

- **Playback timeline (SIM-05):** mark each Highlight at its tick on the playback scrubber / event
  timeline (a coloured pip per `kind`, reusing the `OVERLAY_KIND_STYLE` palette extended for the
  RV-02 kinds — `nuke_detonation`, `nuke_cancelled`, `medic_reset`, `first_elimination`,
  `team_elimination`, `scoring_burst`). Clicking a pip jumps playback to that tick;
  the currently-playing Highlight is indicated. No new backend — `highlights_json` is passed to the
  page via `json_script` alongside `events_data`.
- **Chart toggle:** an optional overlay on the four event-page charts (`chart-shots`, `chart-lives`,
  `chart-points`, `chart-sp`) drawing one vertical line per Highlight, coloured by `kind`, label =
  kind + player/team — using the **existing** RES-02 `_overlay_plugin` registration path (inline
  `plugins:` array, `drawOverlays` mutating the closure-captured overlay list). A "Highlights" toggle
  in the chart filter UI mirrors the existing elimination/special/nuke overlay toggles exactly.

**Scope:** read-only/derived; no model change, no migration, no simulator change. Depends on RV-02
(`highlights_json`). **Acceptance:** every Highlight in `highlights_json` appears as a timeline pip
and (when toggled) a chart overlay line at the correct tick; toggling Highlights off restores the
prior chart appearance; clicking a timeline pip scrubs playback to that Highlight.

### RES-05 · Medic-hits overlay on the event-page charts
Add **medic hits** as a toggleable overlay on the four event-page charts, reusing the RES-02
`_overlay_plugin` pattern. The exact definition of "medic hit" is to be pinned during the grill
(candidates: every `tag` row whose **target** is a **Medic**; the **medic-under-fire alert** moments
— a Medic tagged 2× within `MEDIC_ALERT_WINDOW_TICKS`; or hits *landed by* a Medic) — the data is
already in the event log (`tag` rows carry actor/target roles in `metadata`), so this is a
client-side scan + overlay with no backend change. A "Medic hits" toggle joins the existing chart
filter toggles.

**Scope:** frontend-only; no model change, no migration, no simulator change. Depends on RES-02
(chart + overlay-plugin infrastructure). **Acceptance:** toggling "Medic hits" marks each qualifying
event on the charts at the correct tick and toggling it off restores the prior appearance; the
definition chosen in the grill is documented in CONTEXT.md if it introduces new domain language.

## Phase 3 — Simulation Mechanics Backlog (added 2026-05-21)

Mechanics and decision-making items captured from working notes. These extend the MECH / MOVE
families and the role-aware goal selection work (MAP-05). None are scheduled yet — each carries an
open question or design dependency that must be resolved before implementation. Items are ordered
roughly by readiness; MECH-07 (goal-selection rework) is intentionally last because its shape is
still undecided.

### MECH-08 · Reset-timing miss penalty
Players currently have no notion of *when* a downed enemy will turn back on, so they cannot mistime a
shot. Add behaviour where a player attempting to tag a reset target can fire **too early** — before
the target reactivates — and waste the shot. The miss should fall out of imperfect timing rather than
the existing hit-chance roll.

**Open question:** which stats drive the timing estimate? Candidates already on the model —
`game_awareness` (already gates the MECH-02 reset filter), `nuke_awareness`/reaction-style stats, and
possibly a new dedicated stat. Resolve which stat(s) feed the early-fire probability before wiring.

### MECH-09 · Reset re-tag action/goal
For reset handling, lean on the existing LOS infrastructure (MAP-03) and the per-tick candidate
filters rather than the abstract zone check. Add an action/goal so a player actively **looks for a
reset opportunity to re-tag a downed enemy** once it reactivates, using `SightLineConfig` for
eligibility and the appropriate target filters. Pairs with MECH-08 (timing) and builds on the MECH-02
`last_tagged_id` reset-target machinery.

### MECH-10 · Follow rule — cap pursuit of downed players
Medics are dying within ~4 minutes because players follow a downed target indefinitely. Add a
**follow rule**: a player cannot follow a downed player more than **10 squares along the downed
player's path**. The path is modelled as a hallway (corridor spread) that starts at the square where
the player was downed and extends until the player turns back on. Pursuit beyond the 10-square limit
is disallowed, which should give Medics survivable breathing room.

**Open question:** corridor width / spread of the "hallway" and how it interacts with LOS and walls
(MAP-07) still needs pinning.

### MECH-11 · Crouch mechanic + stamina cost
Add a **crouching** mechanic that makes a player un-hittable over a **half wall** (the low-wall type
from MAP-07). To prevent continuous abuse, crouching **drains stamina** — either disallowing
sustained crouch outright, or applying a **movement penalty** when stamina is depleted. Touches the
hit-eligibility path (low walls currently block movement but not sight) and the stamina schedule.

**Open question:** which lever — hard stamina gate vs. movement-penalty-on-empty — and whether
stamina here reuses the existing proportional stamina schedule or needs a separate pool.

### MECH-12 · High-ground / half-wall sight-line falloff formula
Rework the high-ground LOS formula (MAP-09) so elevation does **not** grant a clean look at everything
directly below a half wall. Behaviour: a player on elevation should **not** see the cells directly
below a half wall unless **close to the wall**. The farther the elevated player stands from the half
wall, the more of the near sight lines below the wall are removed; farther still removes more. The
falloff should follow a **triangle-type formula** (sight removed grows with distance from the wall).

**Status:** this is a formula rework of the MAP-09 shoot-over / `SightLineConfig` computation, not a
new subsystem. Lands in `compute_sight_lines` / `_has_los` (the `can_shoot_over_wall` path).

### MECH-13 · Per-player information table (imperfect information)
Players currently act on **perfect information**, which is incorrect — each player should decide using
only what they personally know. Add (or fully wire) a **per-player information table** that informs
decision-making, so choices are made against believed/last-known state rather than ground truth.

**Status:** a per-player view already exists via the MECH-06 `player_memory` dict (transient, staleness
thresholds per role). Unclear how much of decision-making actually consults it today — audit current
usage in goal/target selection, then route remaining perfect-information reads through the table.

### MECH-14 · Memory/comms-driven adaptive role behaviour
Now that memory (MECH-06) and communication are implemented, players should **change what they do**
based on new information they receive, rather than following static role scripts. Concrete behaviours
to encode:

- **Scouts** push in past the Heavy when the Heavy is down.
- **Commander** takes space when the Heavy is down.
- **Ammo** can resupply the Heavy for free when the Commander is down.

These are conditional goal/action overrides keyed off teammate-status memory; they extend the MECH-06
broadcast/memory hooks and feed into the role goal selection (MAP-05 / MECH-07).

### MOVE-05 · Simulation engine de-duplication (refactor)
`simulation.py` is heavily bloated and contains duplicated logic. Continue the consolidation already
begun.

**Status:** partially done — `ResourceBasedSimulator` was removed (SIM-09). Several areas still
**duplicate the tagging-and-related-checks code** (a player tag plus all the associated checks appears
in more than one place). Extract the shared tag/check path into a single helper so there is one
implementation. No behavioural change intended; fold any incidental delta into the existing pending
Score Calibration re-baseline.

### MECH-07 · Role-aware goal-selection rework (MAP-05 follow-up)
Make changes to role-aware goal selection (MAP-05). Shape is **still being worked out** — scope and
acceptance criteria are deliberately deferred.

**Status:** TBD — intentionally sequenced **last** in this batch until the design is settled.