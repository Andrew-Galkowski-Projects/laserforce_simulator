# Development Plan

Organized by phase. Phases 0–2 are prerequisites for later phases; don't skip ahead.
Story IDs from `sm5_user_stories_v2.html` are referenced where applicable.

---

## Phase 0 — Immediate Fixes (blockers)

These are bugs and technical debt that corrupt simulation results or mislead future development. Fix before building anything new.

### FIX-01 · Enforce Scout-only role doubling
`teams/models.py` — `Player.clean()` currently allows 2 Commanders, 2 Medics, etc. Only Scout may appear twice in an SM5 roster. Fix the validation, show a clear error on the team detail page for any existing bad rosters, and add unit tests covering all valid and invalid compositions.

### FIX-01b · Block match creation on invalid rosters
`matches/views.py` — The match and single-round creation views must check `is_valid_roster` on both teams before calling the simulator. Return a form error with the specific composition problem; never pass a broken roster to `ResourceBasedSimulator`.

### FIX-02 · Derive shot_power and shield from role
`teams/models.py` — `shot_power` and `shield` are stored as DB columns but should be computed from the player's role. Convert to `@property` on `Player`, delete the DB columns, and update any simulator code that reads them directly.

### FIX-03 · Remove SingleRound legacy model and route
`matches/models.py` — `SingleRound` is superseded by `GameRound`. Remove the model, its migration, the `/matches/round/<id>/` route, and its view. Update any templates that still link to it.

### FIX-04 · Clean up stale TODO comments in get_mvp
Minor — remove or update the two stale TODO comments in `PlayerRoundState.get_mvp`. Add a docstring explaining the weighting formula. No functional change.

---

## Phase 1 — Map–Simulation Integration

The map editor produces a rich cell grid with precomputed sight lines. Currently the simulator ignores it, using only 3 abstract zones. This phase replaces the 3-zone model with full map awareness.

### MAP-01 · Player position on the cell grid
Replace `PlayerRoundState.current_zone` (0/1/2) with a `(row, col)` cell coordinate. On round start, place players on or near their team's base cell. Persist the active zone_size and map for the round on `GameRound` so all queries are keyed consistently.

**Data changes:** `GameRound` gets `arena_map` FK and `zone_size` field. `PlayerRoundState` gets `cell_row` and `cell_col` integers. Keep `current_zone` as a derived property (red/neutral/blue based on cell's zone type) for backwards compatibility with existing views.

### MAP-02 · Cell-aware zone movement
Replace `_change_zone()` with a pathfinding step that moves a player to an adjacent passable cell each tick. Use the `SightLineConfig` adjacency data (cells that can see each other share an edge or corridor) as a proxy for connectivity, or derive an explicit adjacency list from `MapZoneConfig.zones`. Players navigate toward a goal cell (enemy base, ally position, nearest resupply) using a simple weighted heuristic.

**Acceptance:** A player starting at their home base will reach the enemy base in a realistic number of ticks proportional to map size. Players never move into wall cells.

### MAP-03 · Line-of-sight targeting
Replace the current "same zone = can tag" rule with LOS-based targeting. A player can tag any enemy whose cell appears in the `SightLineConfig` adjacency list for the actor's current cell. Pull sight data from the precomputed `SightLineConfig` at round start and hold in memory for the duration.

**Acceptance:** Two players separated by a wall cannot tag each other. Players across a corridor can. Hit-chance formula remains the same; only target eligibility changes.

### MAP-04 · Base interaction via BaseSightLineConfig
Replace the abstract base-capture zone check with `BaseSightLineConfig` lookups. A player can interact with a base (capture, resupply trigger) only if their current cell appears in `visible_cells` for that base. Load `BaseSightLineConfig` at round start alongside `SightLineConfig`.

### MAP-05 · Role-aware goal selection
Update the weight functions in `weights.py` to express goals in terms of target cells rather than abstract zones. Each role picks a goal cell (enemy base, ally player position, nearest cover) and the movement action moves one step toward it. Scouts prioritize high-LOS cells; Heavies hold near chokepoints; Medics/Ammos follow the ally they intend to resupply.

### MAP-06 · Fallback for rounds without a map
When `GameRound.arena_map` is null (map not assigned), fall back to the existing 3-zone logic so that existing tests and simulations without maps continue to work. This is a compatibility shim — new matches should always have a map.

---

## Phase 2 — Player Stats Integration

Most of the 19 player stats exist on the model but are not used in simulation. This phase connects them.

### STAT-01 · Expose all 19 stats in the add/edit player UI
`teams/` — Both the add and edit player forms must render all 19 stat fields grouped by category (Awareness, Decision-making, Physical, Team, Role). New players default to 50 for all stats. Show `overall_rating` as a live-updating summary. Add a convenience "Set to Average / Elite" bulk preset.

### STAT-02 · Role-preference stat multipliers
Define `ROLE_STAT_WEIGHTS` mapping role → {stat_name: multiplier} for all 19 stats. Example: Scout `accuracy` weight = 1.5, Medic `accuracy` weight = 0.4, Medic `resupply_efficiency` weight = 2.0. Add `Player.stat_for_simulation(stat_name)` which returns `stat_value × role_weight`. Update `ResourceBasedSimulator` and `weights.py` to use this method. Keep `overall_rating` as the unweighted display average.

### STAT-03 · Wire stats into action weight functions
Map each relevant stat to a weight modifier in `weights.py`:
- `accuracy` / `survival` — already used in hit-chance formula; confirm they feed in correctly
- `decision_making` — scales the spread between actions (high decision-making = weights more concentrated on optimal action)
- `positioning` — biases movement toward high-value cells (map integration; pairs with MAP-05)
- `stamina` — degrades action quality / effective hit-chance in second half of round
- `speed` — allows more cells traversed per tick (map integration)
- `special_usage` — scales special activation weight directly
- `resupply_efficiency` / `resupply_synergy` — scale resupply weight for Medic/Ammo
- `teamwork` / `communication` — scale ally-following behavior weight
- `game_awareness` / `player_awareness` — scale reaction to enemy nuke (see Phase 3)

### STAT-04 · Seed stats from match history
`teams/` — "Update stats from history" button on player edit page. Derives `accuracy` from `tags_made/(tags_made+shots_missed)` ratio, `survival` from avg `was_eliminated_at`, `special_usage` from SP spend rate, across the player's `PlayerRoundState` history. Show a diff before applying. Only available after minimum 5 games.

---

## Phase 3 — Simulation Mechanics

New and corrected mechanics that make the simulator more faithful to SM5 rules and more interesting strategically.

### MECH-01 · Medic/Ammo follow-up combo tag
A Medic or Ammo can perform a "follow-up" action: tag an ally immediately after a resupply, granting that ally both a life restore (Medic) and a shot resupply (Ammo) in the same interaction. The ally and support player both receive the benefit. In game terms this represents a quick hand-off. Implement as a new action type `combo_resupply` with its own weight (high when both Medic and Ammo are in the same cell as a low-resource ally). Create a `GameEvent` of type `combo_resupply` with both resource grants logged in `metadata`.

### MECH-02 · Base tag to reset same-target restriction
After tagging an enemy and depleting their shields (scoring a life), the attacker normally must wait 8 seconds before tagging the same target again. Implement a rule: tagging a neutral or opposing base resets this per-target cooldown for the attacker. Similarly, tagging any other enemy or ally in the same cell/radius also resets it. Track the "last tagged player" per attacker and clear it on base interaction or zone-wide tag of any other valid target.

### MECH-03 · Commander nuke stacking behavior
Currently Commanders almost never queue a second nuke during the first nuke's fuse window. Add a `nuke_aggression` derived value from `special_usage` and `game_awareness` stats. High `nuke_aggression` players will attempt to nuke stack (fire a second nuke before the first resolves). The weight for `use_special` during an active nuke fuse window should scale with this value rather than being near-zero for all Commanders.

### MECH-04 · Player reaction to incoming nukes
When a pending nuke is in flight (fuse window active), players should react based on stats. Add a nuke-awareness check each tick for all active players on the target team:
- High `game_awareness` + `player_awareness`: player attempts to tag the Commander to cancel the nuke (raises `tag_player` weight toward the Commander specifically, regardless of zone)
- High `survival`: player hides or moves to a different cell to reduce the nuke's impact (hide weight increases)
- Low awareness stats: player ignores the nuke and continues their normal action

### MECH-05 · Nuke cancellation fuse window fix (SIM-03)
Verify and correct the nuke cancellation logic: a nuke must be cancelled if the firing Commander is eliminated during the fuse window (not just at exact timestamps). Write a regression test: Commander fires nuke at T=100, gets tagged at T=103 (within fuse), nuke must not detonate.

---

## Phase 4 — Analytics & Review

Surfaces the data already being collected. No new simulation work required.

### RES-01 · Accuracy % on round detail (quick win)
Add `accuracy_pct` as a `@property` on `PlayerRoundState`: `tags_made / (tags_made + shots_missed) × 100`. Display in the currently-blank Accuracy % column on `/matches/game-round/<id>/`. Covered by a unit test.

### RES-02 · SP timeline chart
Chart SP over time per player on `/matches/game-round/<id>/events/`, sourced from `GameEvent` rows. Spending events shown as downward spikes. SP cap (99) shown as a reference line.

### RES-03 · Missile usage log
Filter event log by type `missile`. Each row: timestamp, actor role, target role, result. Friendly fire highlighted. Summary: total fired, total hit, efficiency %.

### RES-04 · Zone/cell movement heatmap (post-MAP)
After Phase 1, players have cell coordinates. Aggregate time-in-cell across a round and render as a heatmap overlay on the map image. Filter by player. Per-zone time-in-zone bar chart as a simpler fallback before full map integration.

### RV-01 · Compare two rounds side by side
`/matches/compare/?round_a=<id>&round_b=<id>` — per-player stat delta table with green/red colouring. Points Over Time overlay chart. Rounds must share at least one team.

### RV-02 · Auto-flag highlights
Detect: nuke events, first elimination, largest 30-second point swing, team elimination, base destructions. Show as a "Highlights" tab on the events page. Store results in `GameRound.highlights_json` (new field) at round completion.

### RV-03 · Export round report as PDF
`GET /matches/game-round/<id>/export/` — WeasyPrint or ReportLab. Contains round summary, scoreboards, per-player table, resource summary. "[Simulated]" watermark on simulator-generated rounds.

### SIM-01 · Document and test action weights
Add docstrings to every weight function in `weights.py`. Cover weight sums with unit tests. Provide a clearly documented constant dict so weights are adjustable without touching logic code.

### SIM-02 · Batch simulation mode
`POST /matches/simulate-batch/` — accepts `red_team_id`, `blue_team_id`, `n` (10/50/100/500). Runs `ResourceBasedSimulator` n times, returns aggregate stats (win%, avg score, avg survivors, score distribution histogram). Background task if sync >5 seconds. Results not stored as permanent Match records.

### SIM-04 · Simulation confidence display
Per-player data source label ("40 real games" vs "Role defaults — no history") on simulation summary. Team-level confidence badge: Low (<5 games), Medium (5–20), High (>20). Link to edit stats from confidence panel.

### SIM-05 · Simulation replay playback
Play/Pause/Step/Speed controls on the events page. Event timeline highlights current event; live resource counters update per event. State managed client-side; no additional backend endpoint needed.

### HX-01 · Per-player career stats page
`/teams/<id>/player/<pid>/stats/` — aggregated `PlayerRoundState` across all rounds: games played, avg points, K/D ratio, avg survival time, avg accuracy, avg SP earned. Per-role breakdown. Trend chart: avg points per game over time.

### HX-02 · Role benchmarks
Global benchmark averages per role computed from all `PlayerRoundState` records. Player stat shown with +/− delta and percentile rank vs role average. Recomputed on demand or nightly.

### HX-03 · Head-to-head record
`/matches/h2h/?team_a=<id>&team_b=<id>` — W/L record, avg score margin, avg survivors, most impactful player across all H2H matches.

### PR-01 · Pre-match win probability forecast
`/matches/forecast/?red=<id>&blue=<id>` — triggers 100-sim batch (requires SIM-02 and STAT-02). Shows win% per team, projected score range (10th–90th percentile), projected avg survivors, per-player risk flags.

### PR-02 · Roster composition comparison
Two side-by-side roster selectors vs same opponent, each running 100 sims. Side-by-side win%, avg score, avg survivors. Recommended scenario highlighted with rationale.

### PR-03 · What-if scenario editor
Fork a real `GameRound`, change one variable (swap role, adjust stat, change player), re-simulate, show diff vs original. Forked scenario is temporary, not a permanent Match record.

### STAT-03-UI · Stat derivation from history (pairs with STAT-04)
See Phase 2 entry. Surfaces in Analytics phase as a user-facing button.

---

## Phase 5 — Infrastructure & League System

### API-01 · Migrate to PostgreSQL for production
`settings.py` — read `DATABASE_URL` via `dj-database-url`. SQLite for local dev/CI; PostgreSQL in production. Verify all migrations against PostgreSQL. Update GitHub Actions to spin up a Postgres service container.

### API-02 · Read-only REST API
Add Django REST Framework (or django-ninja). Endpoints: `GET /api/teams/`, `GET /api/teams/<id>/`, `GET /api/matches/<id>/`, `GET /api/rounds/<id>/`, `GET /api/rounds/<id>/events/`. Pagination (default 20). Token auth for API consumers; session auth for web views.

### API-03 · Async batch simulation endpoint
`POST /api/simulate-batch/` — returns `job_id` immediately. Background worker (Celery + Redis or Django-Q) processes. `GET /api/simulate-batch/<job_id>/` polls status and returns results. Frontend progress bar. Jobs expire after 1 hour.

### LG-01 · Seasons and standings
New `Season` model: name, start/end dates, enrolled teams (M2M). Standings: W/L/T, points (3W/1T/0L), round wins, total score. Matches linked to season via FK. Active vs completed states.

### LG-02 · Tournament bracket
Single-elimination bracket for 4/8/16 teams; seeded by standings or manual order. Results auto-advance winners. Bracket rendered as a visual tree.

### LG-03 · Season-end awards
Computed from `PlayerRoundState` aggregates: Most Points, Highest K/D by role, Best Medic, Most Efficient Nuke, Best Accuracy. Awards page at `/seasons/<id>/awards/`. Award badge on player profile.

---

## Sequencing Summary

```
Phase 0 (Fixes)
  → Phase 1 (Map Integration)  ← required for meaningful positional mechanics
    → Phase 2 (Stats Integration)  ← required before Phase 3 stat-driven behaviors
      → Phase 3 (Simulation Mechanics)
        → Phase 4 (Analytics — most of this can run in parallel with Phase 3)
          → Phase 5 (Infrastructure & League)
```

Phase 4 items RES-01 (accuracy %), RES-02 (SP chart), RES-03 (missile log), and SIM-01 (document weights) are quick wins that can be done any time after Phase 0.