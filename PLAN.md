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

### STAT-02 · Role-preference stat multiplier
`Player` has a multi-valued `preferred_roles` field. Add `Player.stat_for_simulation(stat_name)` which returns
`stat_value × 1.2` if the player's current game role is in their `preferred_roles` set, otherwise returns
`stat_value` unmodified. This flat 20% boost across all stats is the first pass.

Per-stat-per-role weight tuning (e.g. Scout `accuracy` weight = 1.5, Medic `resupply_efficiency` weight = 2.0)
is **deferred** — see Deferred Items section. Keep `overall_rating` as the unweighted display average.

### STAT-03 · Wire stats into action weight functions
Map each relevant stat to a weight modifier in `weights.py`. Stats are wired in their respective phases:

**Phase 1 (map-dependent):**
- `positioning` — biases movement toward high-value cells (pairs with MAP-05)
- `speed` — allows more cells traversed per tick (pairs with MAP-02)

**Phase 2 (this phase):**
- `accuracy` / `survival` — already used in hit-chance formula; confirm they feed in correctly
- `decision_making` — scales the spread between actions (high = weights more concentrated on optimal action)
- `stamina` — degrades action quality / effective hit-chance in second half of round
- `special_usage` — scales special activation weight directly
- `resupply_efficiency` / `resupply_synergy` — scale resupply weight for Medic/Ammo
- `teamwork` / `communication` — scale ally-following behavior weight

**Phase 3 (nuke-mechanic-dependent):**
- `game_awareness` / `player_awareness` — scale reaction to enemy nuke (see MECH-04)

---

## Phase 3 — Simulation Mechanics

New and corrected mechanics that make the simulator more faithful to SM5 rules and more interesting strategically.

### MECH-01 · Medic/Ammo follow-up combo resupply
When an ally requests resupply and both a Medic and an Ammo are within LOS of that ally simultaneously,
the ally receives both a life restore (Medic) and a shot resupply (Ammo) in one interaction.
This is ally-initiated — the ally moves toward support players and requests resupply; if both are in LOS,
both resupply simultaneously.

The weight for seeking resupply is higher when the ally is below threshold on lives OR ammo.

Implement as a new action type `combo_resupply`. No bonus points beyond the sum of a standard medic
resupply + ammo resupply. Track `combo_resupply_count` on `PlayerRoundState`. Create a `GameEvent` of
type `combo_resupply` with both resource grants logged in `metadata`.

### MECH-02 · Tag of any entity resets same-target restriction
After scoring a life against an enemy, the attacker must wait 8 seconds before tagging the same target again.
This per-target cooldown resets whenever the attacker tags any other entity — any player (ally or enemy),
any base, or any other valid target. Track the "last tagged player" per attacker and clear it on any
successful tag of a different entity.

### MECH-03 · Commander nuke stacking behavior
Currently Commanders almost never queue a second nuke during the first nuke's fuse window. 
Add a `nuke_aggression` derived value from `special_usage` and `game_awareness` stats. 
High `nuke_aggression` players will attempt to nuke stack (fire a second nuke before the first resolves). 
The weight for `use_special` during an active nuke fuse window should scale with this value rather
than being near-zero for all Commanders.

### MECH-04 · Player reaction to incoming nukes
When a pending nuke is in flight (fuse window active), players should react based on stats. Add a nuke-awareness 
check each tick for all active players on the target team:
- High `game_awareness` + `player_awareness`: player attempts to tag the Commander to cancel the nuke 
  (raises `tag_player` weight toward the Commander specifically, overriding normal role behavior)
- High `survival`: player moves to a different cell to reduce the nuke's impact (hide weight increases)
- Low awareness stats: player ignores the nuke and continues their normal action

### MECH-05 · Nuke cancellation fuse window fix (SIM-03)
Verify and correct the nuke cancellation logic: a nuke must be cancelled if the firing Commander is eliminated 
during the fuse window (not just at exact timestamps). Write a regression test: Commander fires nuke at T=100, 
gets tagged at T=103 (within fuse), nuke must not detonate.

---

## Phase 4 — Analytics & Review

Surfaces the data already being collected. No new simulation work required.

### RES-01 · Accuracy % on round detail (quick win)
Add `accuracy_pct` as a `@property` on `PlayerRoundState`: `tags_made / (tags_made + shots_missed) × 100`. 
Display in the currently-blank Accuracy % column on `/matches/game-round/<id>/`. Covered by a unit test.

### RES-02 · SP timeline chart
Chart SP over time per player on `/matches/game-round/<id>/events/`, sourced from `GameEvent` rows. 
Spending events shown as downward spikes. SP cap (99) shown as a reference line.

### RES-03 · Missile usage log
Filter event log by type `missile`. Each row: timestamp, actor role, target role, result. 
Friendly fire highlighted. Summary: total fired, total hit, efficiency %.

### RES-04 · Zone/cell movement heatmap (post-MAP)
After Phase 1, players have cell coordinates. Aggregate time-in-cell across a round and render as a heatmap overlay
on the map image. Filter by player. Per-zone time-in-zone bar chart as a simpler fallback before full map integration.

### RV-01 · Compare two rounds side by side
`/matches/compare/?round_a=<id>&round_b=<id>` — per-player stat delta table with green/red colouring. 
Points Over Time overlay chart. Rounds must share at least one team.

### RV-02 · Auto-flag highlights
Detect: nuke events, first elimination, largest 30-second point swing, team elimination, base destructions. 
Show as a "Highlights" tab on the events page. Store results in `GameRound.highlights_json` (new field) at round completion.

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