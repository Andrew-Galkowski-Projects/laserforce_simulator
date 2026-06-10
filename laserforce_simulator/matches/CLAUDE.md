# matches/

Handles match creation, game round simulation, event logging, and result views.

## Models (`matches/models.py`)

**`Match`**: Two `GameRound`s; teams swap colors between rounds. Winner is determined by rounds won, then total cumulative points. A 10,000-point bonus is awarded for eliminating the opposing team entirely.

**`GameRound`**: One of the two rounds in a match; represents a 15-minute simulation. Has an optional `arena_map` FK (`core.ArenaMap`, null/blank, SET_NULL) and `zone_size` IntegerField (null/blank). Both are set by the simulator when a map is provided; null means the round ran with the 3-zone fallback. **SIM-09:** `_flush_to_db` now persists `arena_map` and `zone_size` for **every** path (full match via `simulate_match`, single round via `simulate_single_round_detailed`, batch save via `save_games`), closing the pre-SIM-09 gap where saved batch games had no map info ([ADR-0002](../../docs/adr/0002-two-simulation-engines.md) superseded). `rng_seed` (`BigIntegerField`, null/blank, SIM-07) holds the 63-bit integer seed `random.seed()`'d before this round was simulated, making the round replayable via `BatchSimulator.replay_round`; null means the round predates SIM-07 or is otherwise not replayable (no backfill, ADR-0004). Post-SIM-09 every persisted round has a seed — `simulate_match` and `simulate_single_round_detailed` each draw a **fresh** 63-bit seed per round via `random.Random().getrandbits(63)` (the two rounds of one Match have **different** seeds — independent draws, never derived from a master). Replay is faithful **only** while the round's rosters, map config, **and Orientation** are unchanged — the seed captures randomness, not world state (roster/map snapshotting is deferred, not SIM-07). **SIM-08:** there is **no new column for Orientation** — a flipped batch round persists the *actual* sides (`team_red` is the team that physically played red, with `PlayerRoundState.team_color` consistent), so the stored sides implicitly encode the Orientation needed for faithful replay; the per-Match colour swap done by `simulate_match` (round 2 args reversed) similarly stores the actual sides each round and is a **distinct** mechanism from SIM-08 Orientation. Rationale: storing a seed rather than `random.getstate()` is recorded in [ADR-0005](../../docs/adr/0005-rng-seed-not-state-for-replay.md); the seed↔Orientation pairing and no-new-column decision in [ADR-0006](../../docs/adr/0006-batch-side-alternation.md); the consolidation of view-path persistence onto `BatchSimulator` in [ADR-0002](../../docs/adr/0002-two-simulation-engines.md) (superseded by SIM-09). **RES-04 `cell_occupancy_json`** (`JSONField`, null/blank, default `None`; migration `0026_gameround_cell_occupancy_json.py`) stores a per-round **Cell occupancy** snapshot (CONTEXT.md) as `{str(player_id): {"r,c": int_ticks}}` — outer keys stringified player IDs, inner keys the same `"r,c"` comma-strings used by `sight_data`, values integer tick counts. Populated by `_flush_to_db` only when `movement_ctx is not None` (i.e. the round ran with a map); map-less rounds leave the field `null`. Cells whose float accumulator rounds to `0` are **omitted**, so each per-player dict is sparse (`{}` is valid for a player who never moved off spawn and was eliminated at tick 0). **No backfill** — pre-RES-04 rounds stay `null` (the [ADR-0004](../../docs/adr/0004-simulation-data-is-disposable.md) precedent, same as `rng_seed`). Drives the two RES-04 surfaces — the per-round overlay at `/matches/game-round/<id>/heatmap/` and the multi-round map-editor "Heatmap" mode via the `/maps/<id>/heatmap-data/` endpoint. See the **RES-04 movement heatmap** subsection below for the pure-function entry point and the seam contract. **RV-02 `highlights_json`** (`JSONField`, null/blank, default `None`; migration `0027_gameround_highlights_json.py`) stores the per-round **Highlight** list (CONTEXT.md) as `list[{kind, tick, team, actor, target, points, label}]` (flat, tick-sorted), populated by `_flush_to_db` on **every** save path via the pure `build_highlights` builder; pre-RV-02 rounds stay `null` (**no backfill**, the [ADR-0004](../../docs/adr/0004-simulation-data-is-disposable.md) precedent as for `rng_seed` / `cell_occupancy_json`). Drives the Highlights tab on the events page — see the **RV-02 highlights** subsection below. **RV-03 `is_simulated`** (`BooleanField(default=True)`; migration `0028_gameround_is_simulated.py`, dep `0027`, a single `AddField`) is the provenance flag for the RV-03 **Round report** PDF — a **Simulated round** (`is_simulated=True`) exports with the diagonal "[Simulated]" watermark; an imported real-game Round (paired with an **Actual game log**, IMPORT-01) is stored `is_simulated=False` and exports without one. **No backfill** — existing rows take the `default=True` (the [ADR-0004](../../docs/adr/0004-simulation-data-is-disposable.md) precedent, as for `rng_seed` / `cell_occupancy_json` / `highlights_json`); the simulator paths inherit the default and never set it explicitly, so today *every* persisted Round is simulated. IMPORT-01 is the first writer of `is_simulated=False`. See the **RV-03 round report PDF** subsection below.

**`PlayerRoundState`**: Starting resources are role-dependent (lives, shots, special, missiles). Tracks final resource counts, tags, misses, zone visits, MVP score. `was_eliminated_at` stores the **tick** of final elimination (`1801` = survived the full round, the `SURVIVED_SENTINEL`; was `901` before TIME-01). The MVP formula is role-specific and weighted heavily toward that role's primary contribution. Also tracks `follow_up_shots` (total follow-up shots fired after non-downing hits) and `reaction_shots` (shots fired in response to being tagged or missed). Uptime breakdown fields (TIME-01 rename from `seconds_*`): `ticks_active` (ticks the player was fully active and targetable), `ticks_not_targetable` (ticks spent in the post-tag deactivation window), and `ticks_reset_window` (ticks spent in the taggable portion of the reset window between deactivation and full return); these reconcile with derived dead-time (`1800 - was_eliminated_at`) to exactly 1800 ticks per player. Scoring breakdown: `missile_points` (total points earned from missile hits and base destructions, separate from tag points). Cell position: `cell_row` and `cell_col` (IntegerFields, null/blank) store the player's current cell when a map is used — updated each tick after movement. `zone_fallback` (was `current_zone` DB column) stores the zone index (0=red, 1=neutral, 2=blue); `current_zone` is a `@property` that reads `zone_fallback`. The simulator updates `zone_fallback` after each cell move via `player.save(update_fields=["cell_row", "cell_col", "zone_fallback"])`. `combo_resupply_count` (IntegerField, default=0) tracks the number of times this player received both a lives and a shots resupply in the same tick (combo resupply); incremented by `resolve_resupply_requests` in `sim_helpers/resupply_queue.py`.

Forwarding properties added to satisfy the duck-type interface required by `sim_helpers/combat.py` and `mechanics.py` (so shared functions work with both `PlayerRoundState` and `PlayerState`): `accuracy`, `survival`, `name`, `player_awareness` (all delegating to `self.player`), `last_shot_time` (backed by a transient `_last_shot_time` attribute, default −99.0), and `tag_id_key` (returns `self.get_tag_id()`). No DB fields — no migration required. The `accuracy`, `survival`, and `player_awareness` properties call `self.player.stat_for_simulation(stat_name, self.role)` to apply the preferred-role 20% boost (capped at 100) rather than reading the raw field directly.

`get_mvp` is now a thin delegating property that calls `calculate_mvp(self)` from `matches.sim_helpers.score_calculator`. `max_lives` and `max_shots` now look up `MAX_LIVES`/`MAX_SHOTS` from `matches.sim_helpers.role_constants` rather than inline dicts.

**`GameEvent`**: Every action (tag, missile, special, miss, resupply, base capture, elimination, movement) is logged here with an actor, optional target, `timestamp` in **ticks** (TIME-01 — was "seconds into the round"; the REST API returns this raw, the `÷2`-to-seconds conversion happens only at HTML/CLI), points, and a JSON `metadata` field. **MOVE-01:** a movement event (`event_type="movement"`) records one **Advance** as a **compact start-cell + end-cell + timestamp** entry in `metadata` (the exact intermediate route is *not* stored — it is recomputed on demand at replay via deterministic A* `start → end`), emitted **only when the cell actually changed**. These per-Advance events reconstruct the player's **Movement trail** (CONTEXT.md). `actor_role`/`new_zone` are still carried for replay/zone display. Combo resupply events use `event_type="combo_resupply"` and carry `{"medic_tag": <str>, "ammo_tag": <str>}` in `metadata`; these fire when a player receives both lives and shots in the same tick. Single resupply events continue to use `event_type="resupply_lives"` or `event_type="resupply_ammo"`. **MOVE-03:** an **Overwatch shot** (the pre-emptive shot a **Hold**ing player fires when an enemy crosses its LoS) is **not** a new event type — it reuses the normal `event_type="tag"` / `"miss"` rows with `metadata={"overwatch": true}` added, so scoring / MVP / accuracy paths are unchanged (the flag is purely an analytics marker). See [ADR-0009](../../docs/adr/0009-hold-overwatch.md) and CONTEXT.md (**Overwatch shot**, distinct from a **Reaction shot**). **RES-02 SP snapshot:** every SP-changing event — `tag`, `missile`, `special`, `base_capture` — carries `metadata["sp"]: int` (the actor's post-event `final_special`, range `[0, 99]`); presence is keyed on event_type, not on whether SP actually changed, so heavy `tag` / heavy `missile` rows and nuke-detonation `special` rows carry `sp` at the actor's unchanged value (same rule as the existing heavy-attack SP-increment guards). `base_capture` events' former `metadata["special_points"]` is **renamed to `"sp"`** by RES-02 with no alias retained. The key drives the per-player SP timeline chart and the SIM-05 playback `SP` column in `templates/matches/game_round_events.html`; see PLAN.md RES-02 and the seam contract `.claude/worktrees/res-02-seam-contract.md` for the full emit-site list. **RES-03 missile event split:** the legacy single `event_type="missile"` row (the resolution-only row emitted at `simulation.py:~L2228` pre-RES-03) is **removed from production** and replaced by two event types — `event_type="locking"` at the fire tick (the **Locking event**, CONTEXT.md), carrying `metadata = {"actor_role", "target_role"}`, and `event_type="missiled"` at the resolution tick (the **Missiled event**, CONTEXT.md), carrying `metadata = {"result": "hit"|"miss", "friendly_fire": bool, "actor_role", "target_role"}`. All four `missiled` keys are required (the spec pins presence + type on every emit site); `actor_role` / `target_role` let the missile-log row render both columns without a DB join, and `result` distinguishes the hit/miss branch. **Friendly fire** is **server-emitted** as `metadata["friendly_fire"]: bool` (true iff `actor.team_color == target.team_color`) — never derived view-side, mirroring the RES-02 single-source contract for `metadata["sp"]`. **Down/respawn invariant:** if the locking actor is **Down**ed before resolution, **no `missiled` event fires** (the `locking` event remains in the log); this is the missile analogue of the MECH-05 nuke-cancellation rule and is structurally enforced by clearing the actor's pending-lock state on every life-loss site via the shared `sim_helpers.down.record_down` chokepoint (the same hook that drops `_path_cache` and `is_holding`, so every life-loss site is covered without per-site review). The split closes the RES-02-flagged pre-existing-bug surface: `game_analysis.py:186` and the `chart-shots` / `chart-lives` / `chart-points` strict scanners in `templates/matches/game_round_events.html` previously compared against the literal `"missile_hit"` — a string the simulator never emitted (the actual `event_type` was `"missile"`) — so missile-driven resource changes were silently missing from those three charts; RES-03 scrubs the `"missile_hit"` literal alongside the `event_type="missile"` rename in the same scope (one bug, one cleanup). The new missile-log surface lives at `/matches/game-round/<int:round_id>/missile-log/` (URL name `missile_log`, template `templates/matches/missile_log.html`), renders one row per `missiled` event (filters out `locking` and `tag`), shows mm:ss via the standard `÷2` template filter at the HTML boundary (TIME-01), and surfaces a header summary with **fired** = count of `missiled` events, **hit** = count where `result == "hit"`, **efficiency %** = `hits / fired × 100` (view-side; no model property; friendly-fire hits count toward `hit`). Friendly-fire rows render with a CSS class containing the substring `friendly-fire`. The seam helper change is a new `emit_event` kwarg on `start_missile_lock` in `sim_helpers/combat.py`, mirroring the `attempt_resupply` / `capture_base` precedent (a callable the simulator passes in; helpers don't import the simulator). See PLAN.md RES-03 and [ADR-0011](../../docs/adr/0011-missile-event-split.md). **RES-04 leaves `event_type="movement"` rows untouched** — the new movement heatmap reads the in-memory `PlayerState.movement_trail` to compute a per-round `cell_occupancy_json` snapshot during `_flush_to_db`; it does **not** rewrite, add to, or otherwise modify the per-Advance `movement` `GameEvent` shape (still the compact start-cell + end-cell + timestamp triple in `metadata`, MOVE-01). **RV-02** appends two server-emitted event types to `EVENT_TYPES` (migration `0027` `AlterField`): `nuke_cancelled` (**Nuke cancellation**, CONTEXT.md — emitted at the down/disarm tick for a Commander with a live pending nuke; **single-source** from the `record_down` chokepoint, carries `points_awarded:0` + `metadata=_actor_meta(player)`, and the cancelled nuke is **left in `pending_nukes`** with a `PendingNuke.cancel_logged` de-dup flag so the MECH-05 reaction/drain path is unchanged — the drain-else branch only emits when `not cancel_logged`) and `medic_reset` (**Medic reset chain**, CONTEXT.md — fired once when a Medic's transient `down_chain_count` reaches 2, i.e. re-**Down**ed before recovery; same `record_down` chokepoint, `points_awarded:0` + `metadata=_actor_meta(player)`). The `team_elimination` highlight kind is **never** a `GameEvent` type — it is derived view-side from the round result, and the `DEAD` event stays the source-of-truth elimination event.

## Simulation Engine (`matches/simulation/`)

### Simulation package layout

`matches/simulation/` is a three-module package (split from the pre-existing 2473-line `matches/simulation.py` for navigability). The package `__init__.py` re-exports every name callers previously imported from `matches.simulation`, so `from matches.simulation import …` lines need no changes:

- `matches/simulation/round_loop.py` — per-tick mechanics (`_str_tag_id`, `_observe_lives`, `_update_player_memory`, `_broadcast_communication`, `_apply_score_broadcast`, `_apply_nuke_activation_broadcast`, `_check_medic_under_fire`, `_apply_nuke_reaction_flags`). Pure tick mechanics — sim_helpers imports only, **no Django ORM imports**.
- `matches/simulation/entrypoints.py` — the `BatchSimulator` class plus the `_PlayerData` / `_SIMULATION_STATS` / `_precompute_roster` / `_chunk_size_for` batch-execution glue. Houses every `simulate_*` / `run` / `run_incremental` / `save_games` / `replay_round` method, the per-tick loop (`_simulate_round`), and the action-resolution helpers (`_resolve_tag_attempts`, `_complete_missile`, `_use_special`, `_complete_nuke`, `_advance_player`, `_move_player_in_memory`, `_collect_overwatch_attempts`, etc.). The class's `_flush_to_db` is a thin delegator onto `persistence.flush_to_db`.
- `matches/simulation/persistence.py` — `flush_to_db` (the ORM serialisation function, with `@transaction.atomic` preserved). The only module in the package that imports `GameRound` / `PlayerRoundState` / `GameEvent`. Owns the second `GameRound.save(update_fields=…)` writes for `cell_occupancy_json` (RES-04) and `highlights_json` (RV-02), and the HX-02 `invalidate_role_benchmarks()` cache bump.

`BatchSimulator.ROUND_TICKS` (class attribute) remains patchable by tests. The module-level `random` import lives in `entrypoints.py` and is re-exported via `__init__.py`, so `patch("matches.simulation.random.randint")` continues to work.

One simulator lives in `matches/simulation/`: **`BatchSimulator`** — pure in-memory (no DB writes during the tick loop), using `PlayerState` dataclasses (see [`sim_helpers/player_state.py`](sim_helpers/CLAUDE.md)). Used by every view path (`create_match`, `create_single_round`, `simulate_batch`) and by the `score_averages` management command. **History:** the project previously ran a second DB-backed engine, `ResourceBasedSimulator`; it was consolidated onto `BatchSimulator` by SIM-09 (May 2026; [ADR-0002](../../docs/adr/0002-two-simulation-engines.md) superseded). `BatchSimulator` absorbed RBS's view-path responsibilities — see `simulate_match` / `simulate_single_round_detailed` / the extended `_flush_to_db` in the BatchSimulator subsection below.

Map-loading helpers live in [`matches/sim_helpers/map_loader.py`](sim_helpers/CLAUDE.md) — free functions extracted from the former `ResourceBasedSimulator.@staticmethod`s by SIM-09 (drop the underscore prefix; behaviour and signatures unchanged):
- `load_map_context(arena_map) -> tuple[MapContext | None, int | None]` — **primary map-loading entry point**. Merges the former two-step `resolve_map_data` → `build_movement_ctx` pipeline into one call: runs all ORM queries (zone config, base positions, sight lines, base sight lines, cell ranking, strong spots, spawn pools, elevation) and immediately constructs a `MapContext` object. Returns `(None, None)` when `arena_map` is `None` (3-zone fallback). Raises `ValueError` for missing zone config / bases / sight lines. Used by `BatchSimulator.simulate_match` / `simulate_single_round_detailed` / `run` / `save_games` and the `score_averages` command.
- `resolve_map_data(arena_map) -> MapData` — **legacy shim**, returns the `MapData` dataclass. New code should use `load_map_context` instead.
- `build_movement_ctx(zone_data, spawn_cells, ...)` — **legacy shim**. Returns a `MapContext`. New callers should prefer `load_map_context`.
- `build_spawn_assignments(roster_roles, team_color, spawn_cells, team_spawn_pools) -> dict[int, tuple | None]` — delegation shim that calls `assign_spawn_cells` from [`sim_helpers/spawn_assigner.py`](sim_helpers/CLAUDE.md). Retained as a shim for legacy callsites.
- `zone_from_cell(row, col, spawn_cells: dict | None) -> int` — returns zone index (0=red, 1=neutral, 2=blue) by Manhattan-distance proximity to base cells. Nearest base type wins; neutral bases take precedence when closer than or equidistant to both team bases. Returns 1 (neutral) when `spawn_cells` is None/empty or red/blue base is absent.

Module-level class:
- `MapData` — dataclass still returned by `resolve_map_data` (the legacy shim): `zone_size`, `spawn_cells`, `zone_data`, `sight_data`, `base_sight_data`, `cell_ranking`, `strong_spots`, `wall_meta`, `spawn_pools`, `elevation_grid`. New code uses `MapContext` (see `sim_helpers/map_context.py`) which replaces the old 11-key `movement_ctx` dict.

Visibility, elevation, and base-interaction helpers have moved to `matches/sim_helpers/combat.py` — see [`sim_helpers/CLAUDE.md`](sim_helpers/CLAUDE.md) for their full descriptions. `simulation/entrypoints.py` now imports them from `combat` rather than defining them inline.

Cell-aware movement (MAP-02/05; **MOVE-01: decoupled from the weighted Action** — active when `movement_ctx is not None` and `player.cell_row is not None`). On the map path **every non-Stationary player Advances toward their Goal cell every tick**, regardless of which weighted Action was rolled; `choose_goal_cell` is consulted **every tick** (not only on the movement roll). **Stationary** = no Advance this tick = `is_hiding` True OR `is_holding` True (MOVE-03 — in **Overwatch**) OR chosen action == `capture_base` (anchored to the **Base** being captured); all other Actions Advance while they act. The `change_zone` Action is renamed **`only_move`** (same action-array index 1, per-role weight tuning preserved) and no longer gates movement — it now devotes the tick entirely to repositioning by doubling that tick's Advance (a single `cells_to_move(speed) * 2` step). See [ADR-0007](../../docs/adr/0007-movement-decoupled-from-action.md) and CONTEXT.md (**Advance**, **only_move**, **Stationary**, **Goal cell**, **Movement trail**).
- `BatchSimulator._move_player_in_memory(player, second, goal_cell, movement_ctx)` — calls `astar_advance_cached` (MOVE-02 path-commitment cache; [ADR-0008](../../docs/adr/0008-path-commitment-via-goal-keyed-cache.md)) for `cells_to_move(player.speed, zone_data)` cells (STAT-03 Phase 1 multi-cell step; **doubled to `* 2` on an `only_move` tick** — MOVE-01); updates `cell_row` / `cell_col` / `current_zone` directly on the `PlayerState` (no ORM); appends the step to the transient `PlayerState.movement_trail` (flushed to compact `GameEvent(event_type="movement")` rows — start cell + end cell + timestamp — by `_flush_to_db` only when a round is saved; no DB column, no migration). `speed` is a baked field on `PlayerState`.

When no map is assigned (`movement_ctx is None`), the old weighted `_change_zone` 3-zone fallback is used on the `only_move` roll (MAP-06 compatibility); the always-on Advance and the `only_move` 2× apply on the map path only.

**MOVE-04 — Goal commitment ([ADR-0010](../../docs/adr/0010-goal-commitment-via-tick-cadence-throttling.md), CONTEXT.md).** With MOVE-01 / MOVE-02 in place, `choose_goal_cell` itself runs every tick for every non-**Stationary** player and the residual cost is the goal-selection cascade (action-driven / role-positioning / enemy-base default plus teamwork-bias + memory + LOS-count scans), not A*. MOVE-04 throttles only the **steady-state positioning** layer (cascade steps 2/3/4) — the **reactive** layer (step 0 MECH-04 nuke-reaction, step 1 critical-resource lives/shots ≤ 30%, step 1b score-broadcast `seek_medic`) **still fires every tick** so time-sensitive overrides are never delayed. The committed steady-state Goal cell is held on a transient `PlayerState._committed_goal` (`Optional[tuple[(int,int), bool, int]]` — `(cell, from_action_driven, expires_at_tick)`; **no DB column / no migration**) for `GOAL_RECOMPUTE_PERIOD_TICKS = 4` ticks (2 s, `sim_helpers/time_constants.py`). **Force-recompute triggers** beyond cadence expiry: no prior commitment, Goal cell reached, exiting **Stationary** (hide → not, hold → not), a reactive override firing, **Down**/respawn **iff** the committed goal came from action-driven targeting (the `from_action_driven` flag — positioning goals survive a Down because the player keeps **Advancing** through the **Respawn cooldown**). Phase is **expiry-based** per-player (`expires_at_tick = tick + N`), **not** a global `tick % N == 0` — load staggers naturally without hashing and avoids the synchronised every-N-ticks cascade spike. The route cache (**Path commitment**, MOVE-02 / [ADR-0008](../../docs/adr/0008-path-commitment-via-goal-keyed-cache.md)) invalidates **iff** a Goal commitment recompute changes the Goal cell — re-picking the same cell leaves `_path_cache` untouched (two commitments, two slots, two invalidation policies). The cadence schedule and source marker consume **no RNG**, so the SIM-07/SIM-08 *internal* contract holds in form (same seed + Orientation + rosters + map ⇒ identical game, serial == parallel, faithful **Replay**), but seeded games **differ from pre-MOVE-04** — staler goals deliberately shift pursuit/positioning. The delta folds into the **single already-pending post-MOVE-01 Score Calibration re-baseline** (same as MOVE-02 / MOVE-03 / SIM-09 — **no new** obligation). **MOVE-04 perf measurement:** cells/tick recompute ratio and ms/round delta vs the MOVE-02 baseline are reported in the PR body, not in this doc — the decision does not depend on the exact ratio. See [ADR-0010](../../docs/adr/0010-goal-commitment-via-tick-cadence-throttling.md) and CONTEXT.md (**Goal commitment**, and the superseded "Goal cell is recomputed every tick" ambiguity).

**MOVE-02 — Path commitment ([ADR-0008](../../docs/adr/0008-path-commitment-via-goal-keyed-cache.md), CONTEXT.md).** The per-tick A* cost of MOVE-01 (all roles now moving, ~8× slowdown with a map) is addressed by a **goal-keyed route cache**: a player follows the single A* route computed when its **Goal cell** was set, re-stepping that committed route each move tick rather than recomputing full A* from the current cell every tick. `choose_goal_cell` is **still consulted every tick** — only the *route* is cached, not goal selection (goal selection runs no A*). The cache lives on a transient `PlayerState._path_cache` field (no DB column, **no migration** — mirrors `movement_trail`); it recomputes only on {goal changed, cache exhausted, next route cell blocked, Down/respawn → cache cleared}. An `only_move` tick consumes `2×steps` along the *same* committed route (not a recompute trigger). Re-stepping consumes **no RNG**, so the SIM-07/SIM-08 contract holds *in form* (same seed + Orientation + rosters + map ⇒ identical game, serial == parallel, faithful Replay), but MOVE-02 deliberately changes which equal-cost route is walked, so seeded games **differ from pre-MOVE-02** — the contract is *internal* determinism, **not** identity to pre-MOVE-02 games (the PLAN.md "no behavioural change" wording was contradictory and is superseded by ADR-0008). See `astar_advance_cached` in [`sim_helpers/CLAUDE.md`](sim_helpers/CLAUDE.md) and CONTEXT.md (**Path commitment**).

**`BatchSimulator`** — pure in-memory, no DB writes during the tick loop. Uses `PlayerState` dataclasses (see `matches/sim_helpers/player_state.py`). The **sole simulator** post-SIM-09 — used by `score_averages`, batch win-rate analysis, **and every view path** (`create_match`, `create_single_round`, `simulate_batch`, batch save). A round runs in **~200 ms** on the no-map 3-zone path with the current mechanics (BS-1, measured May 2026). The legacy "~25 ms" figure predated the MOVE-01..03 / MECH-01..06 per-tick logic and is no longer accurate — the cost is genuine CPU, **not** ORM: a 10-game run issues ~1 query/game (roster load only), confirming nothing in the tick loop touches the DB. Large runs scale across cores via the `workers=` argument (serial by default; serial and parallel are a guaranteed-identical SIM-07/08 contract). **TIME-01:** BatchSim is **fully tick-native** — its loop counter, all `ticks_*` accumulators, `was_eliminated_at`, and every game-logic edge are in ticks (1800-tick round, no seconds anywhere internally). **`BatchSimulator.ROUND_TICKS = TICKS_PER_ROUND`** is a class attribute (patchable to a small value for fast tests; replaces the removed `ResourceBasedSimulator.ROUND_TICKS`).

**SIM-09 view-path methods (both `@transaction.atomic`)** — `BatchSimulator` absorbed RBS's view-path responsibilities ([ADR-0002](../../docs/adr/0002-two-simulation-engines.md) superseded):
- `simulate_match(team_red, team_blue, match_type="friendly", *, arena_map=None) -> Match` — runs two rounds (the second with the team arguments **reversed**, mirroring the per-Match colour swap exactly as RBS did) and persists a complete `Match` + two `GameRound`s + per-round `PlayerRoundState` + `GameEvent` rows. `match.red_round2_points = round2.blue_points` because `team_red` physically played blue in round 2; the stored `team_red`/`team_blue` on each `GameRound` is the team that physically played that side. **Distinct from SIM-08 Orientation**, which remains a batch-only (`run` / `save_games`) mechanism — the two never interact. `@transaction.atomic` preserves the M-2 invariant: no orphan half-Match can exist on error.
- `simulate_single_round_detailed(team_red, team_blue, *, arena_map=None) -> GameRound` — runs one round and persists it (no `Match` parent). `@transaction.atomic`.
- Each round draws its own fresh 63-bit seed via `random.Random().getrandbits(63)` — the **two rounds of one Match have different seeds**, independent draws never derived from a master — persisted to `GameRound.rng_seed` for faithful Replay (SIM-07).
- `_flush_to_db` is extended with `match`, `round_number`, `arena_map`, `zone_size` kwargs — both `arena_map` and `zone_size` now persist onto `GameRound` for **every** path, closing the pre-SIM-09 gap where saved batch games had no map info.

`run(team_red, team_blue, n=100, *, arena_map=None, master_seed=None)` — accepts an optional `arena_map` keyword argument; when provided, resolves map data via `load_map_context` (`sim_helpers/map_loader.py`), and passes the `MapContext` to `_simulate_round` so players navigate by A* rather than the 3-zone fallback. `_make_players` accepts `spawn_cells` and `zone_data` kwargs and initialises `cell_row`/`cell_col` from the team's spawn cell; it also bakes boosted stat values (via `stat_for_simulation`) into each `PlayerState` at construction so the in-memory simulation never calls back to the ORM for per-tick stat reads. `_move_player_in_memory` updates `player.current_zone` directly without any DB writes. **MOVE-02:** it calls `astar_advance_cached` (not `astar_advance`) so the player re-steps a committed goal-keyed route (**Path commitment**, [ADR-0008](../../docs/adr/0008-path-commitment-via-goal-keyed-cache.md)); the transient `PlayerState._path_cache` is cleared to `None` at every tag / follow-up / reaction / missile / nuke life-loss site via the shared `sim_helpers.down.record_down(player, tick, ctx)` chokepoint (stamps `last_downed_time` + drops the route in one place, so "every life-loss site clears the cache" is structural; knocked off-path → recompute next move).

**SIM-07 seeding:** `master_seed` defaults to `None`, in which case each batch run draws a fresh random master from an independent OS-entropy generator; tests pin it to watch aggregate results move after weight/logic changes. Per-round int seeds are derived from a deterministic `random.Random(master_seed)` ("seed chain") — same master seed ⇒ same chain ⇒ identical games. `_run_parallel` and `batch_round_worker` take an int seed and `random.seed(it)`, so **serial and parallel runs produce identical games for a given master seed** (a guaranteed, tested property). Per SIM-08 the reproducible unit is the pair **(RNG seed, Orientation)**, so `avg_seeds` / `outlier_seeds` are `list[[int, bool]]` (the `bool` is `flipped`) rather than `list[int]`; `replay_round(red_roster, blue_roster, seed, flipped, movement_ctx=None)` does `random.seed(seed)` then `_simulate_round` with the rosters in the orientation given by `flipped`; `save_games` takes a `list[tuple[int, bool]]` and persists each via `_flush_to_db(..., rng_seed=...)` onto `GameRound.rng_seed`. Replay is faithful only while rosters, map config, **and Orientation** are unchanged (the seed captures randomness, not world state). `score_round_worker` (the `score_averages` path) remains out of SIM-07/SIM-08 scope (no int seed, no Side flip; seeding stays `random.getstate()`-based), but now threads a parent-built `MapContext` (or `None`) as a 4th args element so `score_averages --map` works under `--workers > 1`. See [`management/commands/CLAUDE.md`](management/commands/CLAUDE.md) for the `--map` flag and the `_SIMULATION_STATS` parallel-path fix (`game_awareness`/`resource_awareness` were missing, breaking all `--workers > 1` runs).

**SIM-10 Progressive batch simulation:** `BatchSimulator.run_incremental(team_red, team_blue, n, *, arena_map=None, workers=None, master_seed=None) -> Iterator[dict]` is the **sole game-loop and sole `_aggregate_batch` caller** post-SIM-10 — a generator twin of `run()` that yields `{"completed": int, "total": n, "aggregate": <existing _aggregate_batch dict over games[0..completed)>}` snapshots at chunk boundaries. Snapshot keys are submission-indexed so **serial == parallel at every chunk boundary, not just at `k == n`**: the parallel path submits all `n` futures upfront, records a `future_to_index` map, drains via `as_completed` for liveness, and gates snapshot emission on a `pending_boundary` watermark (the catch-up loop emits ready boundaries in order when multiple complete in one `as_completed` step). Chunk size is the module-level `_chunk_size_for(n: int) -> int` returning `max(1, min(25, n // 50))` (≈50 snapshots per run regardless of `n`). `run()` is **re-implemented as the consumer of `run_incremental`** — it drives the generator to exhaustion and returns the last snapshot's `aggregate`; the public `run()` signature and return value are unchanged from callers' perspective. `_run_parallel` is **removed** — its `ProcessPoolExecutor(initializer=worker_django_init)` logic folds into `run_incremental`'s `workers > 1` branch, scoped inside the generator body so the pool cleans up on `GeneratorExit` / completion / fail-fast re-raise. Error policy is **fail-fast**: serial propagates straight out of the generator (no `try`/`except`); parallel best-effort `.cancel()`s pending futures then re-raises the original exception. With `n == 0` the generator yields exactly one terminal snapshot `{"completed": 0, "total": 0, "aggregate": _aggregate_batch([], 0)}`. The seed chain, **Side alternation** (`_is_flipped`), `_aggregate_batch`, `_side_order`, `_precompute_roster`, `batch_round_worker`, `worker_django_init`, and `load_map_context` are **unchanged** — same `master_seed` + Orientation + rosters + map ⇒ identical games at every chunk boundary (extends the SIM-07/SIM-08 contract from "serial == parallel at `k == n`" to "serial == parallel at every boundary `k`"). **No simulation mechanics change** → **no Score Calibration re-baseline obligation**. The `score_averages` CLI is unaffected (it consumes `run()` which now consumes `run_incremental` internally — transparent). **SIM-11 (May 2026):** the UI batch path (`_run_batch_job` in `matches/views.py`) now passes `workers=_workers_for(n)` into `run_incremental(...)` — `_workers_for(n)` is a module-level helper returning `1` for `n < 50` and `min(os.cpu_count() or 1, 4)` otherwise (the 50-game threshold avoids Windows `ProcessPoolExecutor` spawn-cost dominating small batches; the 4-worker cap bounds CI / test-runner boxes that report far more cores); `BatchSimulateForm` is unchanged (the decision lives in the view layer) and the `score_averages` CLI keeps `--workers` explicit. Determinism is unchanged — the SIM-10 serial==parallel contract holds at every chunk boundary regardless of `workers`, so **no Score Calibration re-baseline**. See PLAN.md SIM-10 and the seam contract at [`.claude/worktrees/sim-10-seam-contract.md`](../../.claude/worktrees/sim-10-seam-contract.md). **API-03 (May 2026) retires `_workers_for(n)` and `_run_batch_job`** alongside the SIM-10 `_BATCH_JOBS` dict; the UI batch path is now a Celery `simulate_batch_task.delay(...)` enqueue, each task body runs `workers=1`, and horizontal scaling moves to the Celery `--concurrency` knob — see the **Async execution (Celery)** subsection below for the full seam.

**SIM-08 Side alternation:** `run()` / `_run_parallel` alternate which **Team** plays the red **Side** by game index — game `k` is **flipped** iff `k` is odd (`k=0` canonical). The choice is a deterministic function of the index and **never consumes the RNG** (so it does not perturb the SIM-07 seed chain). `round_seeds` entries carry `flipped` alongside the seed. Result-dict keys `red_*` / `blue_*` are **unchanged in name but redefined as team-position keyed**: they mean the team passed as the `team_red` / `team_blue` argument, regardless of the Side it actually played — each game's raw result is de-flipped before bucketing, so the existing per-team win%/score view and template keep working unchanged. A new `side_advantage` sub-dict exposes the raw physical-side signal: `red_side_wins`, `blue_side_wins`, `side_ties`, `red_side_win_pct`, `blue_side_win_pct`, `avg_red_side_score`, `avg_blue_side_score`, `n`. `_flush_to_db` persists the **actual** sides for flipped games: a flipped round's `GameRound.team_red` is the team that physically played red, and `PlayerRoundState.team_color` stays consistent with that — **no new `GameRound` column and no migration** (storing the actual sides implicitly records the Orientation for SIM-05 replay). Even alternation is guaranteed at the `run()` level over the full ordered game sequence (even n ⇒ exact 50/50; odd ⇒ ±1); `save_games` does **not** re-alternate — it replays each carried `(seed, flipped)` pair faithfully, so the avg/outlier subset may be slightly side-skewed, which does **not** bias team/league stats because every saved round records its true sides and all aggregates are team-position keyed. Serial and parallel runs produce identical team-position aggregates **and identical `side_advantage`** for a given master seed (a guaranteed, tested property) — `batch_round_worker` swaps the precomputed red/blue rosters when `flipped`. The SIM-07 replay contract extends from "same seed + rosters + map" to **"same seed + Orientation + rosters + map ⇒ identical game"**. Scope is `BatchSimulator` `run` / `_run_parallel` / `save_games` plus the batch view/template only: the per-Match colour swap done by `BatchSimulator.simulate_match` (round 2 args reversed; SIM-09) is a **separate** mechanism and is untouched by Side alternation — the two never interact. `score_round_worker` stays out of scope by the SIM-07 precedent. Rationale and rejected alternatives in [ADR-0006](../../docs/adr/0006-batch-side-alternation.md); domain terms (Side, Side alternation, Orientation, team-position keyed) in [CONTEXT.md](../../CONTEXT.md).

Per-tick loop:

1. Process pending missiles/nukes that have completed their delay
2. Process pending deferred follow-up and reaction shots (shots scheduled by shot-cooldown logic)
3. Each active player picks an **Action** (weighted random by role, zone, remaining resources) — `tag`, `only_move`, `hide`, `capture_base`, `use_special`, `resupply_ally`, `missile`, `request_resupply`, or **`hold`** (MOVE-03 — the 9th slot; a `hold` roll puts the player in **Overwatch** and **carries over** like `hide` until a non-`hold` Action is rolled or a Down/respawn force-clears it)
4. Resolve the Action — update state and optionally write a `GameEvent`
5. **MOVE-01 (map path only):** unless the player is **Stationary** (`is_hiding`, `is_holding` (MOVE-03 — in **Overwatch**), or the chosen Action is `capture_base`), **Advance** toward the **Goal cell** this tick — independent of the Action chosen in step 3 (`choose_goal_cell` consulted every tick). An `only_move` Action doubles this tick's Advance distance. On the 3-zone fallback (`movement_ctx is None`) movement instead runs the legacy weighted `_change_zone` on the `only_move` roll.
6. **MOVE-03 Overwatch resolution:** collect (no RNG) one **Overwatch shot** `tag_attempt` per **Hold**ing player whose **Line of sight** a mover's traversed cells crossed this tick — the traversed cells are the committed-route cells `astar_advance_cached` popped this tick, exposed on the transient `PlayerState._last_step_cells` ([ADR-0008](../../docs/adr/0008-path-commitment-via-goal-keyed-cache.md) **Path commitment**). Each attempt is gated by the holder's `_shot_cooldown` + `last_shot_time` + `final_shots > 0` + holder-active checks (≤ 1 per holder per tick except a rapid-fire Scout), then fed into the **existing** `_resolve_tag_attempts` path — which dispatches per-attempt to `sim_helpers.shot.resolve_shot` with `kind=SHOT_KIND_OVERWATCH` (shot-resolver consolidation; see the **Shot resolver consolidation** section below) — so Follow-up / Reaction / hit-roll RNG are reused unchanged; the resulting shot writes a normal `tag`/`miss` `GameEvent` with `metadata={"overwatch": true}`. (Historical: the legacy RBS treated `hold` as a Stationary no-op since it had no path-commitment cache to read; that path was removed by SIM-09.) See [ADR-0009](../../docs/adr/0009-hold-overwatch.md) and CONTEXT.md (**Hold**, **Overwatch**, **Overwatch shot**).
7. Check for team eliminations

Action weights are in `matches/sim_helpers/weights.py` (the action-array slot at index 1 is now **`only_move`**, formerly `change_zone`). **MOVE-03** added a 9th slot at **index 8**, **`hold`** (baseline `[70,30,0,0,0,0,0,0,0]`) — see the `hold` column in the Role baselines below and [ADR-0009](../../docs/adr/0009-hold-overwatch.md). Movement is no longer gated by the weighted Action (MOVE-01 / [ADR-0007](../../docs/adr/0007-movement-decoupled-from-action.md)). See [`sim_helpers/CLAUDE.md`](sim_helpers/CLAUDE.md) for details.

**STAT-03 stat wiring** (weights.py / combat.py): `decision_making` applies a post-role spread multiplier (`factor = 1 + dm/100`) — best-weight action × factor, all others ÷ factor (clamped ≥ 0). `stamina` is checked every 10% of round elapsed; when `stamina < elapsed_%`, `stamina_penalty_count` increments, stacking −10% on the `only_move` weight (action index 1, formerly `change_zone`) and −5% on hit-chance (`stamina_hit_modifier = max(0.5, 1 − 0.05 × count)`). The schedule is proportional (`elapsed / round_duration`) so it is **unit-agnostic and unchanged by TIME-01** — only the tick-valued round duration feeds it. `special_usage` multiplies the `use_special` weight delta by `special_usage / 50` across all roles. `accuracy` / `survival` feed hit-chance as `70 + accuracy − survival` (confirmed, no change). `resupply_efficiency` scales the `request_resupply` action weight (index 7) for all roles; `resupply_synergy` scales the `resupply_ally` weight for Medic/Ammo players — both wired in MECH-01 (former TODO blocks removed). `teamwork` and `communication` are fully wired as of MECH-06 (former skeleton TODO blocks removed) — see MECH-06 player memory section above for behavioral details. `request_resupply` (action index 7) is available to all roles; at end of each tick `resolve_resupply_requests` from `sim_helpers/resupply_queue.py` is called to fulfill pending requests — see [`sim_helpers/CLAUDE.md`](sim_helpers/CLAUDE.md) for full resolution logic.

**MECH-04 nuke reaction**: each tick, `_apply_nuke_reaction_flags` resets then sets `reacting_to_nuke` for every active player on the nuke-targeted team. Each player rolls `reaction_probability = (game_awareness + player_awareness) / 200`. When reacting: Medic/Ammo rush toward the neediest ally (by lives ratio for Medic, shots ratio for Ammo) to maximise resupply output before the nuke lands; their `tag_player` weight also transfers to `resupply_ally + 20` (in `weights.py`). Non-support players with lives ≤ 30% of max override their movement goal to the allied Medic cell (survival mode); lives > 30% — the MECH-06 TODO hook is now filled: player seeks the enemy Commander's last-known cell from `player_memory` (if fresh) to attempt a tag-cancel. `reacting_to_nuke` is a transient bool on `PlayerState` (no DB column); set by `_apply_nuke_reaction_flags` each tick, read by `pathfinding.choose_goal_cell`.

**MECH-06 player memory**: replaces perfect-knowledge enemy lookups in goal selection with a per-player memory dict. Key points:
- `player_memory` on `PlayerState`: `{tag_id: {"cell": (r,c), "timestamp": s, "role": role}}` — transient dict, no DB column, cleared at round start.
- Staleness thresholds by role of the remembered player: Heavy/Medic/Ammo = 60 s; Scout/Commander = 15 s. Stale slow-role entries → last-known cell still used; stale fast-role entries → fall through to role default.
- `communication` stat: per-tick probability (0–100%) that a player broadcasts their current LOS snapshot to all alive allies within `sqrt(rows² + cols²) / 2` Euclidean cells.
- `teamwork` stat (>50): on non-nuke-fuse ticks, biases goal selection toward high-LOS cells that are also within LOS of ≥1 alive ally (overlapping coverage).
- Score broadcast every 180 s: losing team → aggression weight +10; winning + low lives + medic dead → hide weight +20; winning + low lives + medic alive + ≥360 s remaining → movement override to allied medic cell. Implemented in `_apply_score_broadcast_weights` in `weights.py`.
- Nuke activation broadcast: when a Commander fires a nuke, all alive enemy players receive the Commander's current cell in their `player_memory`.
- Medic-under-fire alert: when a Medic is tagged 2× within 12 s, all alive teammates receive the Medic's current cell in their `player_memory`.
- Perfect knowledge retained for resupply resolution only (LOS/same-zone checks in `resupply_queue.py` are unchanged).

**MECH-05 nuke cancellation fix**: nuke resolution checks `n.player.special_active_until >= n.complete_time` instead of the former `is_active_at`-only check. Nuke resolution is ordered after reaction/followup/tag processing so same-tick cancellations are applied before detonation. (Pre-SIM-09 the rule was harmonised across the two engines; post-SIM-09 only the BatchSim path remains.)

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

## Time model (TIME-01)

Tick is the canonical persisted/internal/API unit: 1 round = **1800 ticks** (1 tick = 0.5 s). Seconds are a **display-only** `÷2` applied at exactly two boundaries — HTML templates and the `score_averages` / `game_analysis` CLI. The REST API returns raw ticks (no serializer `÷2`).

All absolute time constants live in one zero-dependency module, [`sim_helpers/time_constants.py`](sim_helpers/CLAUDE.md) (`TICKS_PER_ROUND`, `SURVIVED_SENTINEL=1801`, `RESPAWN_TICKS=16`, `NOT_TARGETABLE_TICKS=8`, `ENDGAME_RUSH_TICKS=1680`, `SCORE_BROADCAST_PERIOD_TICKS=360`, staleness `120`/`30`, …). `weights.py` (endgame rush, score broadcast) and the simulators consume these tick-valued constants; the seconds-stated figures in the MECH-06 notes below ("every 180 s", "≥360 s remaining", "12 s", staleness "60 s/15 s") are the human-readable seconds view of those tick constants. Rationale and the two hard-to-reverse decisions (API returns ticks; tick-precision changes seeded outcomes) are in [ADR-0001](../../docs/adr/0001-time-unit-seconds-now-tick-native-later.md); domain terms in [CONTEXT.md](../../CONTEXT.md).

## Role Mechanics

| Role | Shields / Shot Power | Has Missiles | Can Resupply |
|------|---------------------|--------------|--------------|
| Commander | 3 / 2 | Yes | No |
| Heavy | 3 / 3 | Yes | No |
| Scout | 1 / 1 | No | No |
| Medic | 1 / 1 | No | Yes (lives) |
| Ammo | 1 / 1 | No | Yes (shots) |

Shields absorb damage; a hit that reduces shields to 0 costs the defender one life and resets shields to max. Respawn after a life loss requires a respawn cooldown of `RESPAWN_TICKS = 16` ticks: the first `NOT_TARGETABLE_TICKS = 8` ticks (4 s) are not-targetable, then the derived reset window (`[NOT_TARGETABLE_TICKS, RESPAWN_TICKS)`, 8 ticks / 4 s) is taggable-but-not-active before fully active. These are 16/8 ticks internally; the "8 s / 4 s" framing is the human-facing seconds view (`÷2`). Zone values: 0 = red_zone, 1 = neutral_zone, 2 = blue_zone.

**Heavy nerf**: heavies have 1 shot/second (vs 2/s for other roles) and always down their target in one hit, so they never generate follow-up shots.

**Scout rapid fire**: when the scout's special is active (`special_active_until > second`), `_shot_cooldown` returns 0.0, giving unlimited fire rate.

**MOVE-03 Hold / Overwatch** ([ADR-0009](../../docs/adr/0009-hold-overwatch.md), CONTEXT.md **Hold** / **Overwatch** / **Overwatch shot**): the 9th **Action** `hold` (array index 8) puts the player in **Overwatch** — it anchors to its current **Cell** (so `hold` is **Stationary**: it joins `is_hiding` and chosen-Action-`capture_base` in the `_advance_player` predicate, no **Advance** while holding) and watches its **Sight lines**. Like `hide`, Hold **carries over** via a transient `PlayerState.is_holding` (no DB column / no migration) until a non-`hold` Action is rolled or a Down/respawn force-clears it (cleared by `sim_helpers.down.record_down`, the same chokepoint that drops the path cache, so every life-loss site is covered structurally). An enemy entering — or **Advancing** through — the holder's **Line of sight** draws a pre-emptive **Overwatch shot** (a full **Shot**: consumes a Shot, normal hit roll, can **Tag**/**Down**, chains a **Follow-up shot**, provokes a victim **Reaction shot**); it is **not** a **Reaction shot** (which requires a prior enemy Shot at the defender — an Overwatch shot fires *first*). Per-role `hold` weight (`weights.py`): **Medic 0** (no source — Medic stays support-focused); **Ammo +20** taken from `tag_player`; **Scout +10**, **Heavy +20**, **Commander +10** all taken from `only_move`. All weights stay **≥ 0** (the `random.choices` non-negative invariant); the numbers are tunable and calibration is deferred. Overwatch resolution reads the path-commitment route cache (`PlayerState._last_step_cells`) and lives on **`BatchSimulator`** (the sole engine post-SIM-09; the legacy RBS treated `hold` as a Stationary no-op, removed).

## Shot resolver consolidation

A single wide **Shot** resolver replaces the five inline copies of the Shot → Hit → Tag → Down → Elimination ladder that previously lived in `BatchSimulator` (May 2026). The deepening surfaces a domain concept that was scattered across ~700 lines of duplicated combat logic.

**Why it existed.** The simulator had **five** call sites that each open-coded the same shot resolution: `_resolve_tag_attempts` (initial-tag branch + immediate-reaction branch + immediate-follow-up branch) and `_simulate_round` (queued `due_rx` reaction drain + queued `due_fu` follow-up drain). Each duplicate computed `hit_chance = clamp((70 + accuracy − survival) * elev_mod * stamina_modifier, 10, 95)`, decremented `final_shots` (with subtle Ammo-asymmetries between sites), stamped `last_shot_time`, ran the Tag/Miss/Down cascade, emitted events, and re-scheduled follow-ups / reactions — each in a slightly different way. RES-02's universal SP-snapshot, RES-03's missile event split, and MOVE-03's Overwatch flag all had to be applied site-by-site.

**New module layout.**
- `sim_helpers/round_context.py` — `RoundContext` dataclass bundles the per-round mutable state (`event_log`, `pending_nukes`, `pending_followups`, `pending_reactions`, `all_alive`, `movement_ctx`) into one struct threaded through the chokepoint and the resolver. Replaces the RV-02 static→instance self-stash on `BatchSimulator`.
- `sim_helpers/down.py` — `record_down(player, tick, ctx)` is the pure-function chokepoint. Single life-loss bookkeeping site: stamps `last_downed_time`, clears `_path_cache` (MOVE-02), clears `is_holding` (MOVE-03), clears `_committed_goal` iff `from_action_driven=True` (MOVE-04), increments `down_chain_count`, emits `medic_reset` and `nuke_cancelled` events via `ctx.event_log`. Called by `resolve_shot` and by `_complete_missile` / `_complete_nuke`. Does NOT touch `final_lives` or `shields` — those mutations happen at the caller.
- `sim_helpers/shot.py` — `resolve_shot(attacker, defender, tick, *, kind, ctx, chain_depth=0) → ShotOutcome` is the wide resolver. Four `SHOT_KIND_*` string constants (`INITIAL`, `FOLLOW_UP`, `REACTION`, `OVERWATCH`). 10 phases: validity gate → hide-50%-miss roll → hit roll → kind-specific counter → `final_shots` decrement → `last_shot_time` stamp → on hit (counters, shields, Down → `record_down` → maybe `elimination` event, `tag` event with kind metadata, MECH-06 medic-under-fire + memory broadcast) → on miss (`shots_missed`, `miss` event) → reaction scheduling (skipped on REACTION/FOLLOW_UP) → follow-up scheduling (chain cap 2; skipped on REACTION). Reactions and follow-ups at `cd_ticks=0` (rapid-fire scout) recurse into `resolve_shot` immediately; otherwise deferred to `ctx.pending_followups` / `ctx.pending_reactions`.

**Call-site map.** Each of the five (six with Overwatch) sites becomes a one-line dispatch:
- `_resolve_tag_attempts` (in `simulation/entrypoints.py`): per-attempt `resolve_shot(..., kind=SHOT_KIND_OVERWATCH if a.get("overwatch") else SHOT_KIND_INITIAL, ctx=ctx)`. The method body collapsed from ~457 lines to ~27 (a backward-compat ctx-synthesis shim accommodates legacy test callers).
- `_simulate_round` queued reactions: `for rx in due_rx: resolve_shot(rx.attacker, rx.defender, second, kind=SHOT_KIND_REACTION, ctx=ctx)`.
- `_simulate_round` queued follow-ups: `for fu in due_fu: resolve_shot(fu.attacker, fu.defender, second, kind=SHOT_KIND_FOLLOW_UP, ctx=ctx, chain_depth=fu.chain_depth)`.

**Behaviour changes (deliberate, folded into the pending re-baseline).** Two pre-existing per-site asymmetries are repaired:
- **Uniform hide-50%-miss roll.** Pre-consolidation only `SHOT_KIND_INITIAL` rolled `defender.is_hiding and random.random() > 0.5`; the four other sites skipped the check. Post-consolidation every kind rolls.
- **Uniform Ammo non-decrement of `final_shots`.** Pre-consolidation the initial-tag hit/miss branches decremented `final_shots` unconditionally (including for Ammo); the four other sites + the `miss_hid` branch skipped the decrement for Ammo. Post-consolidation Ammo never decrements regardless of kind.

The one-pass-per-shot interleaving (hit roll → mutate → reaction roll → follow-up roll, per attempt) also reorders RNG draws relative to the pre-consolidation two-pass structure (collect all hit rolls, then all reaction rolls, then all follow-up rolls). Seeded games **differ from pre-refactor**; the **internal SIM-07 / SIM-08 contract** (same seed + Orientation + rosters + map ⇒ identical game, serial == parallel, faithful Replay) **holds in form**. The drift folds into the **already-pending post-MOVE-01 Score Calibration re-baseline** — **no new re-baseline obligation**. One calibration-sensitive test (`test_strong_team_winpct_not_diluted_by_alternation`) had its strong-team-win% threshold dropped from 58% to 55% to absorb the drift; the load-bearing invariant (strong team's team-position win% clearly above 50%, not diluted by side alternation) is unchanged.

**File-line reductions.** `simulation.py` shrank from 3253 to 2594 lines (−659, ~20%). Deletions: 102-line `due_rx` block, 126-line `due_fu` block, 457-line `_resolve_tag_attempts` body, 86-line `BatchSimulator._record_down` method, 7-line orphaned `_cooldown_ticks` helper, 1-line orphaned `TICK_SECONDS` import.

**Seam contract.** [`.claude/worktrees/shot-resolver-seam-contract.md`](../../.claude/worktrees/shot-resolver-seam-contract.md) pins the locked names (`RoundContext`, `record_down`, `resolve_shot`, the four `SHOT_KIND_*` constants, `ShotOutcome`), the 10-phase spec, the call-site map, and the two behaviour changes.

**Tests.**
- `matches/tests/test_record_down.py` — 28 pure-unit tests pinning all six behaviours of `record_down` across 4 classes (state mutations, committed-goal conditional clear, medic-reset chain, nuke cancellation, plus an `event_log=None` batch-path class). No DB, hand-built `PlayerState`.
- `matches/tests/test_shot_resolver.py` — 49 pure-unit tests across 8 classes covering all 10 phases × 4 kinds (`TestResolveShotInitial`, `TestResolveShotAmmoUniformity`, `TestResolveShotHideUniformity`, `TestResolveShotFollowUp`, `TestResolveShotReaction`, `TestResolveShotOverwatch`, `TestResolveShotDownChain`, `TestResolveShotSpecialPoints`). RNG patched via `matches.sim_helpers.shot.random.randint` / `.random` for deterministic hit/miss/hide control.
- Pre-existing tests in `test_hold_overwatch.py`, `test_goal_commitment.py`, `test_simulation_view_paths.py`, and `test_missile_log.py` migrated to call `sim_helpers.down.record_down(player, tick, ctx)` instead of the now-deleted `BatchSimulator._record_down` instance method.

## EventLog consolidation

A single `EventLog` class owns the `GameEvent`-dict shape — replaces the **23+ inline `event_log.append({...})` sites** that were previously scattered across `simulation.py`, `sim_helpers/shot.py`, `sim_helpers/down.py`, `sim_helpers/combat.py`, and `sim_helpers/resupply_queue.py` (each duplicating the 7-key dict shape and the 5-key actor / 4-key target metadata blocks). Lands as deepening candidate #2 (May 2026), completing the picture the shot-resolver consolidation started.

**Why it existed.** The pre-refactor code had **three** distinct emit patterns: (1) inline 7-key dict literals in `simulation.py` (18 sites) and `shot.py`/`down.py` private `_emit_*` helpers, (2) an `emit_event=None` callable kwarg seam in `combat.py` / `resupply_queue.py` (one adapter wired to `event_log.append`), and (3) a `_resupply_event_dict` adapter in `simulation.py` that converted the kwargs-style emit calls from `resupply_queue.py` back into the dict shape. Three copies of `_actor_meta` / `_target_meta` / `_build_meta` (each module's "local" version) sat behind it all. Per LANGUAGE.md: *one adapter = hypothetical seam; two adapters = real seam* — the callable seam was one adapter; EventLog promotes it to a real one.

**New module.**
- `sim_helpers/event_log.py` — `EventLog` class with **13 per-event-type verbs** (`tag`, `miss`, `elimination`, `nuke_cancelled`, `medic_reset`, `special`, `locking`, `missiled`, `missile_dodge`, `resupply_lives`, `resupply_ammo`, `combo_resupply`, `base_capture`) + `entries: list[dict]` read API + `__iter__` + `__len__` + `__repr__`. The **null-object pattern**: `EventLog(persist=True)` records every emit into `self._entries`; `EventLog(persist=False)` is the silent no-op variant. Every emit site is one unguarded line (`ctx.events.tag(...)`) — the legacy `if event_log is not None:` guards delete from all 23 sites. EventLog OWNS metadata construction — callers never see `_actor_meta` / `_target_meta` / the 7-key dict shape.

**Field rename on `RoundContext`.** `event_log: Optional[list]` → `events: EventLog` (always non-None). Callers read `ctx.events` (the EventLog) and `ctx.events.entries` (the underlying list, for `_flush_to_db` consumption and test inspection). A transitional `EventLog(persist=True, buffer=<list>)` shim lets the simulator's local `event_log: list` parameter and `ctx.events.entries` share the same list — fully removable as a follow-up.

**Collapsed callable seam in `combat.py` / `resupply_queue.py`.** The four `combat.py` helpers (`attempt_resupply`, `capture_base`, `award_bases`, `start_missile_lock`) and `resupply_queue.resolve_resupply_requests` drop their `emit_event=None` kwarg and take a required `ctx: RoundContext`. They call `ctx.events.*` verbs directly. The `_resupply_event_dict` adapter, the `_batch_emit` lambda inside `_simulate_round`, and the `_do_resupply` dict-vs-kwargs bridge inside `resupply_queue.py` all delete.

**Movement events stay off the EventLog.** `event_type="movement"` rows are written directly to `GameEvent` at `_flush_to_db` time from `PlayerState.movement_trail` (MOVE-01 / RES-04 — the trail is the in-memory source). No `movement` verb on EventLog.

**Wire-format normalization.** One pre-existing inconsistency repaired in passing: the `simulation.py::_attempt_resupply` route produced descriptions like `"X heals Y"` / `"X resupplies Y"` (combat.py wording); the `resupply_queue.py` route went through the `_resupply_event_dict` adapter and produced `"resupply request: resupply_lives"`. EventLog standardizes on the combat.py wording for all paths — a clear improvement; no test asserted on the old adapter wording.

**Behaviour-neutral.** Zero RNG consumed by EventLog verbs; the dict shape is byte-identical to the pre-refactor 7-key literals; metadata schemas (5-key actor block, 4-key target block, kind-specific extras) preserved. Seeded games are **byte-identical** — `_flush_to_db`, `build_highlights`, `game_round_events.html`, the missile-log view, and every existing analytics reader keep working unchanged. **No new Score Calibration re-baseline obligation** (the shot-resolver consolidation's pending re-baseline carries through). The strong-team win% calibration test's ≥ 55% threshold from the shot-resolver consolidation needs no further adjustment.

**File-line reductions.** Net **+430 / −806 lines** across 11 modified files (despite adding a 580-line new module). Significant production-code shrinkage: `combat.py` (-100), `simulation.py` (-200), `resupply_queue.py` (-60), `shot.py` (-90), `down.py` (-40). The 3 metadata-helper triples (in `simulation.py`, `shot.py`, `down.py`, `combat.py`, `resupply_queue.py`) collapse to one private set inside EventLog.

**Seam contract.** [`.claude/worktrees/event-log-seam-contract.md`](../../.claude/worktrees/event-log-seam-contract.md) pins the locked names (`EventLog`, `events`, the 13 verb signatures), the call-site map, the wire-format normalization, and the behavior-neutrality claim.

**Tests.**
- `matches/tests/test_event_log.py` — 59 pure-unit tests across 17 classes pinning each verb's output shape, the null-log behaviour, iteration, the `entries` live-list contract, the RES-03 four-key missile metadata contract, the medic_tag/ammo_tag combo_resupply metadata, and the `base_capture` three-description variants. No DB required.
- Pre-existing tests in `test_record_down.py`, `test_shot_resolver.py`, `test_simulation_view_paths.py`, `test_resupply.py`, `test_missile_log.py` migrated from raw `event_log: list` access (and the `emit_event=` callable kwarg) to `RoundContext(events=EventLog(persist=True))` + `ctx.events.entries` (or for resupply tests, the new `ctx=` kwarg + `target_id`-based event filtering).

## Score Calibration Targets

Used by `score_averages` to measure simulation accuracy against real-world averages:

| Role | Target score |
|------|-------------|
| Commander | 9,952 |
| Heavy | 6,482 |
| Scout | 5,102 |
| Ammo | 3,242 |
| Medic | 2,282 |

> Note: these are **real San Marcos field ground-truth** averages and are **not** rewritten by any MOVE/SIM task. They were tuned against the non-spatial **3-zone fallback** model. MOVE-01 (movement decoupled from the Action), MOVE-02 (**Path commitment** — route-commitment changes which equal-cost path is walked, [ADR-0008](../../docs/adr/0008-path-commitment-via-goal-keyed-cache.md), CONTEXT.md), MOVE-03 (**Hold**/**Overwatch** — a new Action slot reweights every role and Overwatch shots add Tags/Downs, [ADR-0009](../../docs/adr/0009-hold-overwatch.md), CONTEXT.md), MOVE-04 (**Goal commitment** — steady-state goal-selection cascade throttled to a 4-tick cadence with reactive overrides still per-tick, shifts pursuit/positioning, [ADR-0010](../../docs/adr/0010-goal-commitment-via-tick-cadence-throttling.md), CONTEXT.md), and SIM-09 (view-mode rounds now run BatchSim mechanics — Path commitment, Hold/Overwatch, Goal commitment — that were previously RBS-only no-ops or missing, [ADR-0002](../../docs/adr/0002-two-simulation-engines.md) superseded) all leave these targets unchanged for now; the **single pending post-MOVE-01 re-baseline** against the map model absorbs the MOVE-02 route-commitment delta, the MOVE-03 behavioural delta, the MOVE-04 goal-staleness delta, **and** the SIM-09 view-mode mechanics-shift delta. None creates a separate re-baseline obligation. The longer-term intent is to tune the weights/system so map-model scores converge toward these ground-truth targets. Still pending separate measurement/discussion.

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
/matches/game-round/<id>/missile-log/ → RES-03 missile usage log (URL name "missile_log")
/matches/game-round/<id>/heatmap/    → RES-04 per-round movement heatmap (URL name "movement_heatmap")
/matches/game-round/<id>/export/     → RV-03 round report PDF download (URL name "export_round_report")
/matches/compare/?round_a=&round_b= → RV-01 side-by-side round comparison (URL name "compare_rounds")
/matches/h2h/?team_a=&team_b=         → HX-03 head-to-head record (URL name "head_to_head")
/matches/h2h/player/?player_a=&player_b= → HX-04 player head-to-head record (URL name "player_head_to_head")
/matches/team/<id>/history/          → team win/loss history
/matches/simulate-batch/             → run N in-memory simulations (enqueues simulate_batch_task; URL name "simulate_batch")
/matches/simulate-batch/status/<job_id>/ → batch-simulate job status JSON (URL name "batch_simulate_status")
/matches/save-batch-games/           → enqueue save_games_task for avg/outlier seeds (URL name "save_batch_games")
/matches/save-batch-status/<job_id>/ → save-job status JSON (URL name "save_batch_status")

/api/matches/                        → MatchViewSet (list, detail)
/api/rounds/                         → GameRoundViewSet (list, detail, events action)
/api/rounds/<id>/events/             → paginated GameEvent list for that round
/api/simulate-batch/                 → API-03 REST POST: enqueue simulate_batch_task (URL name "api_simulate_batch")
/api/simulate-batch/<job_id>/        → API-03 REST GET: poll job status (URL name "api_simulate_batch_status")
```

## Async execution (Celery)

**API-03 (May 2026)** retires the SIM-10 / SIM-11 in-process Job dicts (`_BATCH_JOBS`, `_SAVE_JOBS`, `_JOBS_LOCK`, `_run_batch_job`, `_run_save_job`, `_workers_for`) and unifies both UI batch flows (`/matches/simulate-batch/`, `/matches/save-games/`) plus the new REST endpoint pair onto **two Celery `@shared_task`s** backed by **Redis**. This is an **executor swap, not a mechanics change** — `BatchSimulator.run_incremental` / `_aggregate_batch` / `save_games` are untouched, the SIM-07/08/10 internal-determinism contract holds in form (same `master_seed` + Orientation + rosters + map ⇒ identical games at every chunk boundary; serial == parallel; faithful Replay; Celery-vs-direct paths produce identical games under `CELERY_TASK_ALWAYS_EAGER`), and **no Score Calibration re-baseline obligation**. See [ADR-0013](../../docs/adr/0013-async-batch-execution-via-celery-redis.md) and CONTEXT.md (**Job**, **Job id**, **Job status**, `### Async execution`).

**Tasks (`matches/tasks.py`).** Both are `@shared_task(bind=True)`:
- `simulate_batch_task(self, team_red_id, team_blue_id, n, arena_map_id=None, master_seed=None) -> dict` (pinned `name="matches.simulate_batch"`) — resolves teams + optional `ArenaMap` (stale `arena_map_id` falls back to `None` via `try/except ArenaMap.DoesNotExist`, preserving the SIM-09/10 stale-id semantics), drives `BatchSimulator().run_incremental(..., workers=1)`, emits each snapshot via `self.update_state(state="PROGRESS", meta=snap)` where `snap == {"completed": int, "total": int, "aggregate": dict}`, and on generator exhaustion returns the final `snap["aggregate"]` (matching `BatchSimulator.run()`'s return shape exactly). Ends in a `finally: django.db.close_old_connections()` block.
- `save_games_task(self, team_red_id, team_blue_id, seeds, n, arena_map_id=None) -> dict` (pinned `name="matches.save_games"`) — replays the carried `(seed, flipped)` pairs via `BatchSimulator().save_games(...)` and returns `{"round_ids": [gr.id for gr in game_rounds]}`. Same arena_map fallback + `close_old_connections` `finally` as `simulate_batch_task`.

**Worker concurrency model.** Each task is **serial inside its body** (`workers=1` baked in). Horizontal throughput comes from running multiple Celery workers — `celery -A laserforce_simulator worker --concurrency 4` — one concurrency knob (`--concurrency`), not two stacked. The SIM-11 `_workers_for(n)` in-process helper is **retired** as part of this swap; the threshold/cap heuristic was tied to a single-process `ProcessPoolExecutor` and does not apply to broker-distributed tasks.

**View-layer helpers (`matches/views.py`).** Three flat `_`-prefixed module-level helpers (RV-01 pattern) replace the deleted dict reads, all built on `celery.result.AsyncResult`:
- `_celery_state_to_job_status(state: str) -> str` — pure status-mapping helper (truth table below).
- `_build_batch_status_response(async_result, *, team_red_id, team_blue_id, arena_map_id) -> dict` — assembles the SIM-10-shaped polling JSON for a batch job.
- `_build_save_status_response(async_result) -> dict` — same for a save job.

**Status mapping (locked at the view boundary — never expose raw Celery states).**

| Celery state | Mapped `status` |
|---|---|
| `PENDING` / `STARTED` / `PROGRESS` / `RETRY` / unknown | `"running"` |
| `SUCCESS` | `"complete"` |
| `FAILURE` / `REVOKED` | `"error"` |

The defensive `"running"` fallback for unknown states keeps the polling UI from breaking on a Celery state it does not recognise.

**Polling JSON — batch flow (`GET /matches/simulate-batch/status/<str:job_id>/?team_red_id=&team_blue_id=&arena_map_id=`, URL name `batch_simulate_status`).** Shape preserved verbatim from SIM-10: `{"status", "completed", "total", "partial", "error", "team_red_id", "team_blue_id", "arena_map_id"}`. Per-key sourcing:
- `status` — `_celery_state_to_job_status(async_result.state)`.
- `completed` / `total` — `async_result.info["completed"]` / `["total"]` on `PROGRESS`; `async_result.result["n"]` on `SUCCESS`; `0` / `0` everywhere else.
- `partial` — `async_result.info["aggregate"]` on `PROGRESS`; `async_result.result` on `SUCCESS` (the final aggregate dict, same shape as `snap["aggregate"]`); `None` elsewhere.
- `error` — `str(async_result.info)` on `FAILURE`/`REVOKED` (Celery exposes the exception instance via `.info`; `str(exc)` matches the pre-API-03 `_BATCH_JOBS[…]["error"]` contract); `None` otherwise.
- `team_red_id` / `team_blue_id` / `arena_map_id` — **carried via query params** on every polling GET (the POST response includes them; the polling JS appends them to every status URL). Celery does not persist task kwargs on the result backend by default and result-backend expiry (1h) would lose them anyway; query-param carry keeps the view stateless and avoids `result_extended=True` backend cost.

**Polling JSON — save flow (`GET /matches/save-batch-status/<str:job_id>/`, URL name `save_batch_status`).** Shape `{"status", "error", "round_ids"}`. `round_ids` is `async_result.result["round_ids"]` on `SUCCESS` and `[]` otherwise. **Vocabulary rename:** the pre-API-03 `_SAVE_JOBS` success status was the string `"done"`; API-03 renames it to `"complete"` for consistency with the batch flow and with the CONTEXT.md `Job status` term (the polling JS in `batch_simulate.html` for the save branch updates its one string compare from `data.status === "done"` to `=== "complete"`).

**POST response shapes (preserved + new).**
- `POST /matches/simulate-batch/` (UI) → `{"job_id", "team_red_id", "team_red_name", "team_blue_id", "team_blue_name", "arena_map_id", "n"}` — unchanged from SIM-10.
- `POST /matches/save-batch-games/` (UI) → `{"job_id"}` — unchanged.
- `POST /api/simulate-batch/` (REST, **new**) → identical shape to the UI batch POST (same client code can read either).

**Session handover preserved verbatim.** The SIM-10 single-write session guard for `request.session["batch_seeds"]` is unchanged: the FIRST poll observing `status == "complete"` (where `request.session.get("batch_seeds", {}).get("job_id") != job_id`) copies `avg_seeds` / `outlier_seeds` / `team_red_id` / `team_blue_id` / `arena_map_id` plus the `"job_id"` guard marker into the session and sets `request.session.modified = True`; subsequent polls observing `complete` check the guard and skip the write so user-mutations between polls survive. Only the source of `aggregate` changes — it is now read from `async_result.result` instead of `_BATCH_JOBS[job_id]["partial"]`. `save_batch_games` reads the same session shape and is **unchanged** beyond swapping its executor from `threading.Thread(target=_run_save_job)` to `save_games_task.delay(...)`.

**Expiry asymmetry (`CELERY_RESULT_EXPIRES = 3600`, 1 hour).** Polling a Job id whose result has expired resolves to Celery `PENDING` — indistinguishable from a never-submitted id. The status mapping above maps `PENDING` → `"running"`, so the UI polls forever on an expired id. Documented on the CONTEXT.md `Job id` term; no special-case fallback path in the view layer.

**REST surface (API-03).** Two new APIView subclasses in `matches/api_views.py` mounted off `laserforce_simulator/api_urls.py` after the DRF `DefaultRouter` urls (router only handles ViewSets — `APIView` needs plain `path()` entries):
- `POST /api/simulate-batch/` (URL name `api_simulate_batch`, `SimulateBatchAPIView`) — accepts `{team_red, team_blue, n, arena_map?, master_seed?}` validated by an inline `SimulateBatchRequestSerializer(serializers.Serializer)` (Forms-vs-Serializers is the locked DRF idiom — the UI POST still uses `BatchSimulateForm`). Same-team rejection and `roster_errors` mirror the UI view, returning 400 with `{"detail": "<msg>"}`. Validation errors return DRF's default per-field shape. `master_seed` is REST-only (UI form has no field for it) and is plumbed for test pinning / scripted-run convenience, **not** a user-facing knob.
- `GET /api/simulate-batch/<str:job_id>/` (URL name `api_simulate_batch_status`, `SimulateBatchStatusAPIView`) — returns the identical polling JSON shape as the UI `batch_simulate_status` endpoint. Same query-param carry pattern.

Both REST views **inherit `AllowAny` from the API-02 `REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"]` default** — no override of `permission_classes` / `authentication_classes` (deferred-auth precedent locked by API-02 and pinned by `TestAPIInheritsAllowAnyPermissions`).

**URL names — preserved + new (full table).**

| URL name | Path | HTTP | Source |
|---|---|---|---|
| `simulate_batch` | `/matches/simulate-batch/` | GET / POST | preserved |
| `batch_simulate_status` | `/matches/simulate-batch/status/<str:job_id>/` | GET | preserved |
| `save_batch_games` | `/matches/save-batch-games/` | POST | preserved |
| `save_batch_status` | `/matches/save-batch-status/<str:job_id>/` | GET | preserved |
| `api_simulate_batch` | `/api/simulate-batch/` | POST | **new (API-03)** |
| `api_simulate_batch_status` | `/api/simulate-batch/<str:job_id>/` | GET | **new (API-03)** |

**Template DOM ids unchanged.** `templates/matches/batch_simulate.html` retains every DOM id the polling JS reads (`batch-form`, `batch-progress-container`, `batch-progress-bar`, `batch-progress-label`, `batch-error`, `batch-results`, `batch-results-n`, `batch-elapsed`, the per-team `batch-red-*` / `batch-blue-*` / `batch-side-*` / `batch-avg-*` cells, the `scoreChart` canvas, the `batch-save-games` button, the `avgN` / `outlierN` / `saveStatus` cells, and the `batch-team-red-name` / `batch-team-blue-name` class hooks). JS changes are minimal: the `poll(jobId)` URL appends `?team_red_id=&team_blue_id=&arena_map_id=` (the three values from the POST response), the save-status branch's string compare flips `"done"` → `"complete"`, and the `'not_found'` branch is dropped (the Celery path returns 200 + running for unknown ids, never 404).

**SIM-09 save_games flow seam unchanged.** Only the executor swapped (`save_games_task.delay(...)` replaces `threading.Thread(target=_run_save_job, ...)`); `BatchSimulator.save_games` itself is untouched, persisted side semantics (flipped rounds keep their actual sides on `GameRound.team_red` / `team_blue`) are unchanged, and the `request.session["batch_seeds"]` handover is read in the same shape.

**Settings (`laserforce_simulator/settings.py`).** A `CELERY_*` block appended after `REST_FRAMEWORK`: `CELERY_BROKER_URL` and `CELERY_RESULT_BACKEND` default to `redis://localhost:6379/0` (override via env), `CELERY_RESULT_EXPIRES = 3600`, JSON serialization across the board, `CELERY_TASK_ALWAYS_EAGER = config("LF_CELERY_EAGER", default=False, cast=bool)`, and `CELERY_TASK_EAGER_PROPAGATES = True` so task failures surface as exceptions under EAGER rather than silent `FAILURE` state. Celery app lives in `laserforce_simulator/celery.py` (`celery_app = Celery("laserforce_simulator")` + `config_from_object("django.conf:settings", namespace="CELERY")` + `autodiscover_tasks()`), re-exported from `laserforce_simulator/__init__.py` as `celery_app` so the worker resolves the app.

**Tests.** Run under `CELERY_TASK_ALWAYS_EAGER = True` — no Redis required for tests or CI. `conftest.py` does `os.environ.setdefault("LF_CELERY_EAGER", "1")` at module load (no separate `pytest.ini` env block, no `pytest-celery` dependency). Test files: `matches/tests/test_batch_tasks.py` (direct task-level tests under EAGER) and `matches/tests/test_batch_views.py` (view-level tests for the rewritten UI views + the new REST views). The seven `TestRunIncremental*` / `TestChunkSizeFor` classes in `matches/tests/test_simulation_incremental.py` are **unchanged** — they pin the `BatchSimulator.run_incremental` simulator contract, not the job machinery.

**Local development.** Production requires a running Redis broker and at least one worker — `celery -A laserforce_simulator worker -l info` next to the Django dev server. For devs without Redis: opt-in eager mode via `LF_CELERY_EAGER=1` (sets `CELERY_TASK_ALWAYS_EAGER=True`), which runs every `.delay()` synchronously in the request thread.

**Scope-out (locked, ADR-0013 §scope-out).** No `fly.toml` / `Dockerfile` edit (deployment wiring deferred to a separate deploy task); no CI Redis provisioning (EAGER suffices); no token auth on `/api/` (`AllowAny` inherits from API-02); no `master_seed` UI exposure (REST-only, test convenience); no cancel-in-flight UX (`AsyncResult.revoke` exists but no UI ships); no `Job` persistence past the 1h TTL (no DB row, no cron sweep); no `score_averages` CLI change (the management command stays foreground `BatchSimulator().run(...)`); **no simulation mechanics change**; **no Score Calibration re-baseline**; no new CONTEXT.md term beyond the three already added (`Job`, `Job id`, `Job status`); no new ADR beyond ADR-0013; no `_aggregate_batch` / `run_incremental` / `run` / `save_games` / `BatchSimulateForm` change.

Seam contract: [`.claude/worktrees/api-03-seam-contract.md`](../../.claude/worktrees/api-03-seam-contract.md).

## Forms (`matches/forms.py`)

**`MatchSetupForm`** and **`SingleRoundSetupForm`** both include an optional `arena_map` `ModelChoiceField` (empty_label="No map (3-zone fallback)"). The queryset is populated in `__init__` via `_maps_with_confirmed_config()` which returns only `ArenaMap` objects with at least one confirmed `MapZoneConfig`. Rounds without a map fall back to the existing 3-zone logic.

The corresponding views (`create_match`, `create_single_round`) extract `arena_map = form.cleaned_data.get("arena_map")`, pass it keyword-only to the simulator, and catch `ValueError` (missing config, missing base, or missing sight lines) to display a form error without crashing.

## Templates

All templates live in `laserforce_simulator/templates/`. The `game_round_events.html` template has event filtering and color-coded display; `game_round_detail.html` shows per-player stats and MVP scores. Both `enhanced_match_setup.html` and `enhanced_single_round_setup.html` include the optional `arena_map` picker field.

**M-1 — event-log windowing.** `game_round_events` (view) emits every event **once** as a compact JSON list (`{{ events_data|json_script:"events-data" }}`) plus a `players_data` block, instead of one server-rendered DOM row per event (the old design produced ~20k DOM nodes for a single round). `game_round_events.html` renders only a bounded **window** of the timeline client-side (`WIN = 250` rows, Newer/Older pager) and feeds the *same* JSON array to the kill feed (recency-capped at 250), the three Chart.js charts, and the SIM-05 playback engine — nothing reads the DOM for event data anymore. The playback engine auto-pages the window onto the current event (and click-on-row still jumps playback). The per-event JSON key set (`type/ts/tf/icon/desc/pts/aid/an/at/tid/tn/tt/meta`, `tid == -1` when targetless) and the `players_data` shape are pinned by `TestM1EventLogWindowing` in `views_tests.py`; changing a key requires updating both the view and the template. `GameRound.get_kill_feed` is now unused by the view (kill feed is derived client-side from `events_data`) but retained as a public model helper.

## RES-04 movement heatmap

Per-round **Cell occupancy** snapshots ship via two surfaces driven from a single persisted form on `GameRound.cell_occupancy_json` (see the **`GameRound`** paragraph above for the field, migration, and null-on-map-less semantics).

**Pure reconstruction.** `matches/sim_helpers/cell_occupancy.py::reconstruct_cell_occupancy(movement_trail, spawn_cell, round_ticks, eliminated_at, adj, elevation_data=None) -> dict[tuple[int,int], int]` is the algorithmic seam — **pure Python, no Django imports, no I/O, consumes no RNG**. It walks the player's **Movement trail** (CONTEXT.md) crediting the stationary slice `[cursor_tick, ts)` before each Advance to `cursor_cell`, the Advance itself splits 1 tick evenly across `1 + len(astar_path(start, end, adj, elevation_data))` cells (the `+1` is the start cell — `astar_path` returns the route excluding start, including end), then the trailing stationary slice `[cursor_tick, end_tick)` after the final Advance, with `end_tick = min(round_ticks, eliminated_at)`. The float accumulator is cast to `int` via banker's-rounded `round()` and cells whose final value rounds to `0` are **omitted** — the output is sparse, tuple-keyed, int-valued. The caller stringifies (`(r,c) → "r,c"`, `player_id → str(player_id)`) for JSON; the function stays in the pure tuple/int domain so it is testable without JSON round-trip.

**`_flush_to_db` integration.** Sits in `flush_to_db` (`matches/simulation/persistence.py`; called by `BatchSimulator._flush_to_db` in `matches/simulation/entrypoints.py`) **immediately after** the per-player `movement` `GameEvent` flush block and **before** the final `return game_round`, **gated on `movement_ctx is not None`** (no map ⇒ no A* adjacency ⇒ the column stays `null`). Spawn cell sources from `p.movement_trail[0][0]` when the trail is non-empty, else `(p.cell_row, p.cell_col)`; players with neither (defensive — e.g. `p.player_id is None`) are skipped. The snapshot is persisted by a **second** `game_round.save(update_fields=["cell_occupancy_json"])` — the earlier `_flush_to_db` save triggers winner calculation; the second save is intentional and cheap. `movement_ctx.get_adjacency()` and `movement_ctx.elevation_grid` are read off the **existing** `MapContext` accessors — **no new `MapContext` accessor** and **no new `_flush_to_db` kwarg** (`movement_ctx` is the already-existing SIM-09 kwarg). RES-04 does **not** add a per-tick cost — the reconstruction runs once per player at round end.

**Per-round view** (`matches/views.py::movement_heatmap`) at `/matches/game-round/<int:round_id>/heatmap/` (URL name `movement_heatmap`, template `templates/matches/movement_heatmap.html`). GET only (`HttpResponseNotAllowed` on non-GET, 405); `get_object_or_404(GameRound, pk=round_id)`. Context: `game_round`, `cell_occupancy_json` (raw — passed through `json_script` as DOM id `cell-occupancy-data`), `player_roster` (list of `{id, name, role, team_color}` dicts ordered red-then-blue, role-then-name; rendered via `json_script` as DOM id `player-roster-data`), `has_map`, `arena_map`, `zone_size` (exposed to JS as `window.LF_ZONE_SIZE`), `processed_image_url`. Filter strategy is **client-side** — three dropdowns (`heatmap-filter-player`, `heatmap-filter-role`, `heatmap-filter-team`) sum the per-player JSON in-browser and re-paint the canvas; no server round-trip per filter change. Map-less rounds render a **"No map — heatmap unavailable"** notice (DOM id `heatmap-no-map-notice`); the original PLAN.md "per-zone bar chart fallback" is **dropped** because MAP-01..09 are complete (the 3-zone fallback survives only as a compatibility shim for rounds the user explicitly creates without a map — RES-04 simply does not render a heatmap there).

**Multi-round aggregate** lives **inside the existing map editor** as a third mode toggle alongside Zones & Bases and Sight Lines (see [`core/CLAUDE.md`](../../laserforce_simulator/core/CLAUDE.md)). Driven by `core/views.py::map_heatmap_data` at `/maps/<int:map_id>/heatmap-data/` (URL name `map_heatmap_data`); GET only (405 on non-GET), required `zone_size` int query param (400 `"zone_size required"` when missing), optional `team_color` ∈ {`"red"`, `"blue"`} (400 `"invalid team_color"` on any other non-empty value). Filtering is **server-side on `team_color` only** — the view joins `GameRound.cell_occupancy_json` against `PlayerRoundState.team_color` to drop non-matching players, then sums the remaining `"r,c"` → ticks entries. Response shape `{"cell_occupancy": {"r,c": int}, "zone_size": int, "rows": int, "cols": int, "round_count": int}` with cells whose final sum is `0` omitted (matches the per-round file format). The editor view exposes **no** per-player or per-role dropdowns — those are single-round only.

**Single-source contract.** Per-player JSON is the only persisted form; team-color / role / per-player aggregates are **always derived at view time** via `PlayerRoundState` (the editor view server-side, the round view client-side via `player_roster`). Same seed + Orientation + rosters + map ⇒ identical `cell_occupancy_json` — reconstruction consumes no RNG and reads only the deterministic movement trail + A* route, so the SIM-07/08 contract extends to the new field. **No simulation behaviour change** → **no Score Calibration re-baseline obligation**. Locked names (model field, migration, pure function, view names + URLs, JSON ids, DOM ids, test files) are pinned by the seam contract at [`.claude/worktrees/res-04-seam-contract.md`](../../.claude/worktrees/res-04-seam-contract.md). **No ADR** — decisions are reversible (the column is a `JSONField` add; the cache is regenerable). **Scope-out:** no backfill, no time-window slicing, no PNG/PDF/CSV export, no JS unit tests, no new `MapContext` accessor.

## RV-01 round comparison

A single read-only view `compare_rounds(request)` (`matches/views.py`) at `path("compare/", views.compare_rounds, name="compare_rounds")` compares two `GameRound`s that share at least one team. It reads `round_a` / `round_b` from `request.GET` (query params, **not** URL kwargs — so the picker is reachable with no params). **No model change, no migration** — pure read-only/derived; **consumes no RNG**, runs no simulation, so it is outside the SIM-07/08 contract and triggers no Score Calibration re-baseline. Tests live in `matches/views_tests.py`.

**Four modes**, surfaced via the `mode` context key:
- **picker** — either param missing → render the two-`<select>` chooser (HTTP 200).
- **404** — a supplied id doesn't resolve (`get_object_or_404`).
- **error** — `round_a == round_b`, or the two rounds share no team → `mode="error"` + `error_message` (HTTP 200; the picker re-renders above the banner).
- **full compare** — delta table + overlay chart (HTTP 200).

**"Shares a team" is Side-agnostic Team-id overlap:** `{a.team_red_id, a.team_blue_id} & {b.team_red_id, b.team_blue_id}` — a team that played red in round A and blue in round B still pairs (Orientation-independent). The delta-table rows pair `PlayerRoundState` **by `player_id`**, so the same human is compared to themselves across both rounds regardless of Side.

**Module-level helpers** in `matches/views.py` (pure beyond the rounds handed in):
- `_shared_team_ids(round_a, round_b) -> list[int]` — the set-intersection above as a list.
- `_player_stat_deltas(round_a, round_b, team_ids) -> list[dict]` — one row per player on a shared team, shape `{player_id, name, role_a, role_b, side_a, side_b, stats: {<stat>: {a, b, delta}}}` where **`delta = b - a`**; the whole `delta` (and the absent side's `a`/`b`) is **`None` when that player has no `PlayerRoundState` on one of the rounds** (joined the roster between rounds).
- `_cumulative_team_points(game_round, team_id) -> list[list]` — `[[tick, cum_points]]` running totals from that team's `GameEvent` rows, **coalescing the nullable `GameEvent.points_awarded` to 0** so non-scoring events don't break the cumulative sum.

The delta table is the **extended** stat set in a fixed key order, exposed as the `stat_keys` context key (single source of truth for the template loop): `points_scored, mvp, tags_made, times_tagged, accuracy, final_lives, resupplies_given, missiles_landed, specials_used, follow_up_shots, reaction_shots, combo_resupply_count`. **`mvp` reuses the existing `PlayerRoundState.get_mvp` property** and **`accuracy` reuses the existing `get_accuracy` property** (RES-01) — neither is recomputed in the view.

The **Points-Over-Time** overlay is `points_series` = one entry **per shared team** `{team_id, team_name, a: [[tick, cum]], b: [[tick, cum]]}` (round A solid, round B dashed), built from `_cumulative_team_points`. Timestamps are raw **ticks** through the view/JSON boundary; any mm:ss display applies the standard `÷2` filter at the HTML layer (TIME-01).

**Context keys:** `round_a, round_b, all_rounds` (`GameRound.objects.select_related("team_red", "team_blue").order_by("-id")` — populates both picker `<select>`s), `mode, error_message, stat_keys, deltas, points_series`.

**Template** `templates/matches/compare_rounds.html`: two `<select>` controls (DOM ids `compare-select-a` / `compare-select-b`), the error banner, the delta table (green = positive delta / red = negative, neutral when `delta is None`), and a Chart.js overlay fed by two `json_script` blocks (DOM ids `compare-points-series` and `compare-deltas`).

**Entry point:** a "Compare Rounds" button in the `match_list.html` header (`/matches/`) links to the picker; deep links (`?round_a=&round_b=`) also work directly.

## RV-02 highlights

Per-round **Highlight** auto-flagging (CONTEXT.md) persisted to `GameRound.highlights_json` (see the **`GameRound`** paragraph above for the field, migration `0027`, and null-no-backfill semantics) and surfaced as a **Highlights** tab on the existing events page. **No URL change** — RV-02 reuses `/matches/game-round/<id>/events/`; the tab lives on that page, no new route.

**Pure builder.** `matches/sim_helpers/highlights.py::build_highlights(events, result, *, round_ticks, name_by_id, team_by_id) -> list[dict]` is the detection seam — **pure Python, no Django imports, no I/O, consumes no RNG**. `events` is the **in-memory event buffer** (NOT ORM rows); `result` is the round result dict; `round_ticks` is `TICKS_PER_ROUND` (1800); `name_by_id` / `team_by_id` are id→name / id→team maps passed in so the builder emits NAME strings + a per-event team while staying pure (an absent id resolves to `None`). Returns a flat list sorted by tick ascending, each record the fixed 7-key shape `{kind, tick, team, actor, target, points, label}`. **Six kinds:** `nuke_detonation` (discriminated by `event_type=="special"` + `metadata["targets"]` + `points_awarded==500`; the activation row — `points==0` & `metadata["fires_at"]` — is **not** flagged), `nuke_cancelled`, `medic_reset`, `first_elimination` (first elimination by tick → one record), `team_elimination` (from `result["red_eliminated"]`/`["blue_eliminated"]` + `["eliminated_at"]`, **NOT** the `dead` event), `scoring_burst` (the **Scoring burst** — forward `[t, t+60)` 60-tick window with maximum single-team gross points → one record; none when the round had no point events). **Base captures are deliberately not a highlight kind** (routine point-grabs — left to the events-log timeline, see below); their `points_awarded` still feed the `scoring_burst` sum.

**`record_down` chokepoint.** The shared life-loss chokepoint is `sim_helpers.down.record_down(player, tick, ctx)` — see the **Shot resolver consolidation** section below for the lift. It emits `nuke_cancelled` at the down/disarm tick for a Commander with a live pending nuke and sets `PendingNuke.cancel_logged=True` while **leaving the nuke in `pending_nukes`** (MECH-05 reaction/drain unchanged; drain-else emits only when `not cancel_logged`), and increments the transient `PlayerState.down_chain_count` **before** stamping `last_downed_time` when `not is_active_at(second)`, emitting `medic_reset` once when the chain reaches 2 for a `medic`. (Pre-consolidation this was `BatchSimulator._record_down`, a static→instance method reading `self._event_log` / `self._pending_nukes` stashed in `_simulate_round`; both the method and the self-stash are gone.)

**Event-log "Base Capture" filter.** `base_capture` events were always persisted but hidden from the events-log timeline — the event-type filter (`game_round_events.html`) had no checkbox for them and the substring match (`ev.type.includes(t)`) never matched, so they silently never rendered. RV-02 adds a **"Base Capture"** type checkbox (value `base_capture`, checked by default) and a `🚩` icon (`GameEvent.get_event_icon`) so captures are visible in the log. A **"Missile Lock"** checkbox (value `locking`, checked) was added for the same reason — `locking` (Locking event, RES-03) rows were hidden by the same filter gap; its `🔒` icon already existed.

**`_flush_to_db` integration.** `build_highlights` is invoked in `flush_to_db` (`matches/simulation/persistence.py`; called by `BatchSimulator._flush_to_db` in `matches/simulation/entrypoints.py`) **after** the RES-04 `cell_occupancy_json` block and **before** the final `return`: it builds `name_by_id` / `team_by_id` from the red+blue players, calls the builder, sets `game_round.highlights_json`, and persists via a **second** `game_round.save(update_fields=["highlights_json"])` (mirrors the RES-04 second-save pattern). Populated on **every** save path; no per-tick cost.

**View / template surface.** `game_round_events` (view) adds context key `highlights_json` (`game_round.highlights_json or []`); `game_round_events.html` exposes it via `{{ highlights_json|json_script:"highlights-data" }}` and renders a client-side Highlights tab into DOM ids `highlights-section` / `highlights-list` / `highlights-empty` (mm:ss via the standard `÷2` at the HTML layer, TIME-01).

**No re-baseline.** The builder consumes no RNG and the cancelled nuke is left in the pending queue, so seeded games are byte-identical — **no Score Calibration re-baseline obligation**. Domain terms (Highlight, Scoring burst, Medic reset chain, Nuke cancellation) are in [CONTEXT.md](../../CONTEXT.md); the nuke-cancelled-event decision is in [ADR-0012](../../docs/adr/0012-nuke-cancelled-event.md).

## RV-03 round report PDF

A single-**Round** PDF export — the **Round report** (CONTEXT.md) — at `GET /matches/game-round/<int:round_id>/export/` (URL name `export_round_report`, view `matches/views.py::export_round_report`), generated server-side with **ReportLab** (programmatic PDF; chosen over WeasyPrint to avoid an HTML-template dependency ahead of the planned Angular migration — added as the unpinned `reportlab>=4.0` line to `laserforce_simulator/requirements.txt`). Provenance is driven by the new `GameRound.is_simulated` flag (see the **`GameRound`** paragraph above for the field, migration `0028`, and no-backfill semantics).

**Pure builder.** `matches/sim_helpers/pdf_report.py::build_round_report(report_data: dict, *, watermark: bool) -> bytes` is the render seam — **pure Python, no Django/ORM imports, no settings access, no file I/O beyond an internal `io.BytesIO` buffer, consumes no RNG**. It returns `b"%PDF"`-prefixed non-empty bytes and draws a diagonal "[Simulated]" watermark on **every** page via a ReportLab `onFirstPage`/`onLaterPages` page callback, gated by the keyword-only `watermark` bool (`watermark=False` ⇒ no watermark drawn). The builder **never** touches the ORM — the view assembles `report_data` and hands it over; that dict is the only thing crossing the seam.

**Watermark testable seam.** ReportLab **compresses page-content streams**, so the literal `[Simulated]` text is **not reliably greppable** in the output bytes. The watermark decision is therefore factored into a tiny pure helper `should_watermark(is_simulated: bool) -> bool` that the page callback consults and tests assert on **directly** — `should_watermark(True) is True`, `should_watermark(False) is False` — **without parsing compressed PDF streams**. The byte-level tests assert only the `b"%PDF"` prefix on both branches; they never check for watermark-text presence.

**View.** `export_round_report` is **GET-only** (`if request.method != "GET": return HttpResponseNotAllowed(["GET"])` → 405, mirroring the `movement_heatmap` guard), resolves `get_object_or_404(GameRound, pk=round_id)` (→ 404 on a missing id), assembles `report_data` from the ORM, calls `build_round_report(report_data, watermark=game_round.is_simulated)`, and returns `HttpResponse(pdf_bytes, content_type="application/pdf")` with `Content-Disposition: attachment; filename="round-<id>-<red_slug>-vs-<blue_slug>.pdf"` (the view owns slugification; only the filename shape is pinned). Scoreboard ordering mirrors `game_round_detail` **exactly** (`-points_scored, role, player__name`).

**`report_data` content (frozen seam).** Round-summary block: `round_id`, `round_label` (`f"Round {n} of 2"` if the round has a `match` else `"Single Round"`), `date_played` (the view pre-formats; the builder prints verbatim), `map_name` (`None` ⇒ builder **omits** the map line), team names / points / `*_eliminated` flags, and `winner_name` (`None` ⇒ builder prints `"Tie"`). The **per-player table is the RV-01 stat set, single-sourced** — same fixed key order `points_scored, mvp, tags_made, times_tagged, accuracy, final_lives, resupplies_given, missiles_landed, specials_used, follow_up_shots, reaction_shots, combo_resupply_count` — where **`mvp` reuses the `PlayerRoundState.get_mvp` property** and **`accuracy` reuses the `get_accuracy` property** (NOT the plain `accuracy` property that delegates to `stat_for_simulation`), neither recomputed. A **per-team resource summary** (`red_totals` / `blue_totals`) sums `resupplies_given` / `missiles_landed` / `specials_used` / `tags_made` over the team's players, derives `survivors` (count with `final_lives > 0`), and reads `team_points` from `GameRound.red_points` / `blue_points` (the team-level field, **not** summed from players).

**Edge cases.** An empty / early-eliminated round (all-zero stats) renders with zeros and **no crash**; a map-less round (`map_name=None`) omits the map line; a tie (`winner_name=None`) prints "Tie".

**Entry point.** An "Export PDF" link in `templates/matches/game_round_detail.html` (`{% url 'export_round_report' round.id %}`) — added by the Code agent; no behavioural logic lives in the template.

**Scope-out.** Charts/graphs are deliberately **deferred to RV-05** (`pdf_charts.py` is a *future* sibling of `pdf_report.py`). The `is_simulated=False` write — the **Actual game log** `.tdf` import that pairs a Round to a real game — is deferred to **IMPORT-01** (the first writer of `is_simulated=False`; the provenance contract was locked at RV-03 planning). **RV-03 runs no simulation and consumes no RNG** → no SIM-07/08 contract interaction and **no Score Calibration re-baseline**. Locked names are pinned by the seam contract at [`.claude/worktrees/rv-03-seam-contract.md`](../../.claude/worktrees/rv-03-seam-contract.md). **No ADR** — decisions are reversible (a `BooleanField` add + a pure render module).

## HX-03 head-to-head

A single read-only view `head_to_head(request)` (`matches/views.py`) at `path("h2h/", views.head_to_head, name="head_to_head")` aggregates the **Head-to-head record** (CONTEXT.md, `### Analytics and review`) between two **Teams** — W/L Match record, W/L Round record, mean signed score margin from team_a's perspective, per-team mean survivors, the most impactful player per team, a per-map breakdown table, two Chart.js charts (margin over time, cumulative W/L), and a unified reverse-chronological detail list of Matches + standalone Rounds. Reads three query params from `request.GET` (`team_a`, `team_b`, plus optional `provenance` and `from`/`to` date range) — picker reachable with no params. Template `templates/matches/head_to_head.html`. Pure aggregation module `matches/h2h_stats.py`. **No model change, no migration** — read-only/derived; **consumes no RNG**, runs no simulation, so it is outside the SIM-07/08 contract and triggers no Score Calibration re-baseline. Mirrors the RV-01 four-mode pattern (`picker` / `404` / `error` / `results`); the `results` mode includes the empty-history sub-case where all aggregates render as zeros and a single `h2h-no-games-notice` block renders.

**Corpus.** Every H2H **Match** (`{team_red, team_blue} == {team_a, team_b}`, `is_completed=True`) PLUS every standalone H2H **Round** (no `Match` parent), under **Side-agnostic Team-id pairing** (Orientation-independent — a Team that played red in one game and blue in another still pairs by Team id, matching the RV-01 "shares a team" Side-agnostic intersection precedent). The Match record applies only to the Matches subset; the Round record + score margin + avg survivors + most-impactful-player + per-map breakdown + both charts apply across the unified basket (the 2 Rounds of each H2H Match + every standalone H2H Round). Scores are normalised by the view to team_a's perspective before crossing the seam (flipping red/blue when `game_round.team_red_id != team_a_id`).

**Pure module** `matches/h2h_stats.py` — **pure Python, no Django imports, no ORM, no RNG, no I/O** (frozen import allowlist: `typing.Iterable`, `typing.Mapping`, `typing.Sequence`, `collections.defaultdict`). Exposes **eight** public functions: `compute_match_record` (W/L/T over H2H Matches; `winner_team_id` mapped to W/L/T; unknown winner id defensively counts as tie), `compute_round_record` (W/L/T per Round; team_a vs team_b score comparison, equal = tie), `compute_score_margin` (mean signed `team_a_score − team_b_score` per Round; empty ⇒ `0.0`, no div-by-zero), `compute_avg_survivors` (per-team mean `count(final_lives > 0)` per Round; two numbers — team_a and team_b), `top_impactful_per_team` (top cumulative-MVP player per team; tiebreaker lower `player_id`; team with no rows returns `None` for that side), `compute_per_map_breakdown` (one row per `arena_map_id` observed including the single `None` row labelled `"No map (3-zone)"`; sorted by games desc with `arena_map_id` asc tiebreaker and `None` last), `margin_series` (chart data — signed margin per Round chronologically sorted by `(date_played, round_id)`, `list[list]` not `list[tuple]` for `json_script` serialisation), `cumulative_wl_series` (chart data — cumulative `team_a_wins − team_b_wins` Round-level chronologically; ties don't move the running diff). Every signature returns zeros (or `[]` / `None`) on empty input, never raises. The "no Django imports leaked" defensive check `TestNoDjangoImportsLeaked` mirrors the HX-01 / HX-02 / RES-04 / RV-03 precedent (walks `sys.modules` from a fresh subprocess after `import matches.h2h_stats`).

**Seam.** Three flat dict lists cross view ↔ pure-module: `matches_list` (one entry per H2H Match — `match_id`, `winner_team_id` (`None` = tie), `date_played`, `is_simulated`), `rounds_list` (one entry per Round in the unified basket, **already normalised from team_a perspective by the view** — `round_id`, `date_played`, `team_a_score`, `team_b_score`, `team_a_survivors`, `team_b_survivors`, `match_id` (`None` = standalone), `arena_map_id`, `arena_map_name` (`None` when null), `is_simulated`), and `player_rounds_list` (one entry per `PlayerRoundState` in the rounds basket, **already attributed to team_a or team_b by the view** via per-Round `team_color` resolution — `player_id`, `player_name`, `team_id`, `mvp` (`PlayerRoundState.get_mvp` — **property, no parentheses**), `round_id`). The pure module never sees a Django object, only plain dicts.

**View.** `matches/views.py::head_to_head(request)` plus **seven** flat module-level helpers prefixed `_` (RV-01 pattern — no class): `_parse_provenance(raw)` (returns `"all"` / `"real"` / `"sim"`; anything else falls back to `"all"` — HX-02 forgiving-fallback precedent), `_parse_date(raw)` (`None` / `""` / `ValueError` → `None`), `_h2h_matches_qs(team_a, team_b, provenance, date_from, date_to)` (filters by Team-id pair on either red or blue, `is_completed=True`, date range, and when `provenance != "all"` requires **BOTH** `game_rounds` of the Match to match `is_simulated` — the locked conservative rule), `_h2h_rounds_qs(team_a, team_b, provenance, date_from, date_to)` (filters by Team-id pair on either red or blue, date range, provenance; includes Rounds with `match=None` and Rounds whose Match is in the H2H Match set), `_normalize_round(game_round, team_a_id)` (returns the `rounds_list` shape, flipping red/blue when `game_round.team_red_id != team_a_id` so team_a's perspective is canonical), `_team_a_or_b(round_, team_color, team_a_id)` (resolves `"red"`/`"blue"` against the actual Round's sides + `team_a_id` → `team_a_id` or `team_b_id`; this is the per-Round attribution that lets a player who switched teams appear in BOTH per-team pools), `_build_player_rounds(rounds_qs, team_a_id, team_b_id)` (single ORM query `PlayerRoundState.objects.filter(game_round__in=rounds_qs).select_related("player", "game_round")`, maps each row to the `player_rounds_list` shape using `_team_a_or_b`, reads `mvp = state.get_mvp` — **property, no parentheses**), and `_build_detail_list(matches_list, rounds_list)` (unified reverse-chronological list, one row per Match with 2-round totals + winner AND one row per standalone Round with that Round's score; each row carries `kind` ∈ `{"match", "round"}` + display fields).

**Template surface.** `templates/matches/head_to_head.html` extends `base.html`. **Locked DOM ids** — picker form: `h2h-picker-form`, `h2h-select-a`, `h2h-select-b`, `h2h-provenance`, `h2h-from`, `h2h-to`, `h2h-submit`. Results headline: `h2h-match-record` (wraps "W-L-T"), `h2h-round-record`, `h2h-score-margin`, `h2h-team-a-survivors`, `h2h-team-b-survivors`, `h2h-top-impactful-a`, `h2h-top-impactful-b`. Sections: `h2h-per-map-table`, `h2h-detail-list`, `h2h-no-games-notice` (only rendered when `match_record.n == 0` AND `round_record.n == 0`). Error banner: `h2h-error-banner` containing `{{ error_message }}`. Charts: canvas ids `h2h-margin-chart` (stepped line with zero reference) and `h2h-cumulative-wl-chart` (stepped line, no reference); `json_script` ids `h2h-margin-series` (renders `margin_series`) and `h2h-cumulative-wl-series` (renders `cumulative_wl_series`) — mirrors the RV-01 `compare_rounds.html` overlay pattern. Time display uses Django's `|date:"Y-m-d H:i"` filter on real wall-clock `date_played` (not the TIME-01 `÷2` tick filter — this is wall-clock, not tick-based).

**Entry points.** Two template-only edits (no view-level changes): a "View Head-to-Head" anchor in `templates/matches/match_list.html` header sibling to the existing "Compare Rounds" button (links to `{% url 'head_to_head' %}` with no params → picker mode), and a per-opponent "vs. {opponent} — H2H" link in `templates/matches/team_history.html` (rendered by the existing `team_match_history` view at `team/<int:team_id>/history/`) that pre-fills both team ids via `{% url 'head_to_head' %}?team_a={{ team.id }}&team_b={{ opponent.id }}`.

**Determinism / scope.** **Read-only view** — no RNG, no simulation, no `_flush_to_db` touch, no SIM-07 / SIM-08 contract interaction, no Score Calibration re-baseline obligation. **No model change, no migration, no ADR** (decisions are reversible — pure read-only view + pure aggregation module). **No CONTEXT.md edit** (the **Head-to-head record** term was added inline during the grilling session that produced this contract and already lives at the bottom of the `### Analytics and review` section).

**Locked names.** URL `GET /matches/h2h/` (URL name `head_to_head`); view `matches/views.py::head_to_head`; pure module `matches/h2h_stats.py` (8 functions: `compute_match_record`, `compute_round_record`, `compute_score_margin`, `compute_avg_survivors`, `top_impactful_per_team`, `compute_per_map_breakdown`, `margin_series`, `cumulative_wl_series`); template `templates/matches/head_to_head.html`; DOM ids `h2h-picker-form` / `h2h-select-a` / `h2h-select-b` / `h2h-provenance` / `h2h-from` / `h2h-to` / `h2h-submit` / `h2h-match-record` / `h2h-round-record` / `h2h-score-margin` / `h2h-team-a-survivors` / `h2h-team-b-survivors` / `h2h-top-impactful-a` / `h2h-top-impactful-b` / `h2h-per-map-table` / `h2h-detail-list` / `h2h-no-games-notice` / `h2h-error-banner`; canvas ids `h2h-margin-chart` / `h2h-cumulative-wl-chart`; `json_script` ids `h2h-margin-series` / `h2h-cumulative-wl-series`; test files `matches/tests/test_h2h_stats.py` (NEW) + `matches/tests/views_tests.py::TestHx03HeadToHead` (EXTENDED). Seam contract: [`.claude/worktrees/hx-03-seam-contract.md`](../../.claude/worktrees/hx-03-seam-contract.md).

## HX-04 player head-to-head

A single read-only view `player_head_to_head(request)` (`matches/views.py`) at `path("h2h/player/", views.player_head_to_head, name="player_head_to_head")` aggregates the **Player head-to-head record** (CONTEXT.md, `### Analytics and review` — the Player analogue of the **Head-to-head record** term, sitting adjacent to it) between two **Players** — W/L Round record, mean signed score margin from player_a's perspective, per-direction tag means + raw totals (A→B and B→A, independent), a per-role breakdown table (bucketed on player_a's per-Round role), a per-map breakdown table, two Chart.js charts (margin over time, cumulative W/L), and a unified reverse-chronological detail list. Reads four query params from `request.GET` (`player_a`, `player_b`, plus optional `role`, `provenance`, `from`/`to`) — picker reachable with no params. Template `templates/matches/player_head_to_head.html`. Pure aggregation module `matches/player_h2h_stats.py`. **No model change, no migration** — read-only/derived; **consumes no RNG**, runs no simulation, so it is outside the SIM-07/08 contract and triggers no Score Calibration re-baseline. Mirrors the HX-03 four-mode pattern (`picker` / `404` / `error` / `results`); the `results` mode includes the empty-basket sub-case where all aggregates render as zeros and a single `player-h2h-no-games-notice` block renders.

**Corpus.** Every `GameRound` where both Players appeared with **different `PlayerRoundState.team_color`** (i.e. on opposite teams). Same-team Rounds are **excluded entirely** — no fallback display, no "of which N on the same team" footnote. Side-agnostic per-Round attribution (a Player who switched teams between Rounds still pairs by per-Round `team_color`, never by `Team` id) — each Round is independently evaluated against the opposite-teams gate. Scores are normalised by the view to player_a's perspective before crossing the seam (reading each `PlayerRoundState.team_color` against the Round's `team_red_id` / `team_blue_id`).

**Pure module** `matches/player_h2h_stats.py` — **pure Python, no Django imports, no ORM, no RNG, no I/O** (frozen import allowlist: `typing.Iterable`, `typing.Mapping`, `typing.Sequence`, `collections.defaultdict`). Exposes **seven** public functions: `compute_round_record` (W/L/T per Round from player_a's perspective; equal scores tie), `compute_score_margin` (mean signed `player_a_team_score − player_b_team_score` per Round; empty ⇒ `{"mean_margin": 0.0, "n": 0}`, no div-by-zero), `compute_tag_stats` (per-Round means of A→B and B→A `GameEvent(event_type="tag")` counts plus raw totals + `n`; tag direction is independent — A→B and B→A are separate counters), `compute_per_role_breakdown` (one row per `role_a` observed — bucketed on player_a's per-Round role regardless of what player_b played, the *display* breakdown distinct from the both-semantics `?role=` filter; sorted by games desc with `role` asc tiebreaker), `compute_per_map_breakdown` (one row per `arena_map_id` observed including the single `None` row labelled `"No map (3-zone)"`; sorted by games desc with `arena_map_id` asc tiebreaker and `None` last), `margin_series` (chart data — signed margin per Round chronologically sorted by `(date_played, round_id)`, `list[list]` not `list[tuple]` for `json_script` serialisation), `cumulative_wl_series` (chart data — cumulative `player_a_wins − player_b_wins` Round-level chronologically; ties don't move the running diff). Every signature returns zeros (or `[]`) on empty input, never raises. The "no Django imports leaked" defensive check `TestNoDjangoImportsLeaked` mirrors the HX-01 / HX-02 / HX-03 / RES-04 / RV-03 precedent.

**Seam.** A single flat dict list crosses view ↔ pure-module: `rounds_list` (one entry per Round in the opposite-teams basket after all filters applied, **already normalised from player_a's perspective by the view** with **exactly 12 keys**: `round_id`, `date_played`, `player_a_team_score`, `player_b_team_score`, `tags_a_to_b` (`GameEvent(actor=A, target=B, event_type="tag")` count for this Round), `tags_b_to_a`, `role_a` (player_a's `PlayerRoundState.role` for this Round), `role_b`, `match_id` (`None` = standalone), `arena_map_id`, `arena_map_name` (`None` when null), `is_simulated`). The pure module never sees a Django object, only plain dicts.

**View.** `matches/views.py::player_head_to_head(request)` plus **five** flat module-level helpers prefixed `_` (RV-01 pattern — no class): `_player_h2h_rounds_qs(player_a, player_b, provenance, date_from, date_to)` (filters to Rounds where **both** Players have a `PlayerRoundState` row + date + provenance; **does NOT apply the opposite-teams gate or role filter** — those happen view-side after per-Round `team_color` / `role` resolution), `_normalize_player_round(game_round, prs_a, prs_b)` (returns the `rounds_list` 12-key shape keyed from player_a's perspective by reading the two `PlayerRoundState` rows; returns **`None`** when `prs_a.team_color == prs_b.team_color` — the same-team gate the caller filters out — and flips `player_a_team_score` / `player_b_team_score` based on each PRS's `team_color` against the Round's `team_red_id` / `team_blue_id`), `_build_player_h2h_tag_counts(rounds_qs, player_a_id, player_b_id)` (**single ORM iterate query**, locked — `GameEvent.objects.filter(game_round__in=rounds_qs, event_type="tag", actor_id__in={A,B}, target_id__in={A,B}).values_list("game_round_id", "actor_id", "target_id")` then Python-side group into `{round_id: (tags_a_to_b, tags_b_to_a)}`; **NOT** two `.annotate(Count())` calls), `_filter_by_role_both(rounds_list, role)` (applies the both-semantics `?role=` filter on already-normalised dicts: returns rows where `row["role_a"] == role AND row["role_b"] == role`; passthrough when `role` is `None` / empty / invalid — HX-02 forgiving-fallback), and `_build_player_h2h_detail_list(rounds_list)` (reverse-chronological list with display fields). **REUSES** the existing HX-03 `_parse_provenance` / `_parse_date` helpers in-place (no duplication).

**Template surface.** `templates/matches/player_head_to_head.html` extends `base.html`. **Locked DOM ids** — picker form: `player-h2h-picker-form`, `player-h2h-select-a`, `player-h2h-select-b`, `player-h2h-role`, `player-h2h-provenance`, `player-h2h-from`, `player-h2h-to`, `player-h2h-submit`. Results headline: `player-h2h-round-record` (wraps "W-L-T"), `player-h2h-score-margin`, `player-h2h-tags-a-to-b`, `player-h2h-tags-b-to-a`. Sections: `player-h2h-per-role-table`, `player-h2h-per-map-table`, `player-h2h-detail-list`, `player-h2h-no-games-notice` (only rendered when `round_record.n == 0`). Error banner: `player-h2h-error-banner` containing `{{ error_message }}`. Charts: canvas ids `player-h2h-margin-chart` (stepped line with zero reference) and `player-h2h-cumulative-wl-chart` (stepped line, no reference); `json_script` ids `player-h2h-margin-series` (renders `margin_series`) and `player-h2h-cumulative-wl-series` (renders `cumulative_wl_series`) — mirrors the HX-03 `head_to_head.html` overlay pattern. Time display uses Django's `|date:"Y-m-d H:i"` filter on real wall-clock `date_played` (not the TIME-01 `÷2` tick filter — this is wall-clock, not tick-based).

**Entry point.** Single template-only edit (no view-level change): a `player-h2h-link` "Head-to-head" outline-button anchor in `templates/teams/player_career_stats.html` header sibling to the existing `role-benchmarks-link` anchor (see [`teams/CLAUDE.md`](../teams/CLAUDE.md) HX-01 section), linking to `{% url 'player_head_to_head' %}?player_a={{ player.id }}` (pre-fills the `player_a` slot only; picker prompts for `player_b`). **No top-nav / match_list / team-history entry point** — career page only, locked.

**Query params.** `?player_a=&player_b=` (both required for results; either missing → picker); `?role=<role>` (optional, default *any* — when set, basket is restricted to Rounds where **both** Players played that role per `PlayerRoundState.role`, **both-semantics**, locked; invalid role string silently ignored — HX-02 forgiving-fallback); `?provenance=all|real|sim` (default `all`; `real` ⇒ `is_simulated=False`, `sim` ⇒ `is_simulated=True`, `all` ⇒ no filter; invalid silently falls back to `all`); `?from=YYYY-MM-DD&to=YYYY-MM-DD` (both optional, default unbounded, invalid silently ignored on that side; filters `GameRound.date_played`).

**Determinism / scope.** **Read-only view** — no RNG, no simulation, no `_flush_to_db` touch, no SIM-07 / SIM-08 contract interaction, no Score Calibration re-baseline obligation. **No model change, no migration, no ADR** (decisions are reversible — pure read-only view + pure aggregation module). **No CONTEXT.md edit** (the **Player head-to-head record** term was added inline during the grilling session that produced this contract and already lives at the bottom of the `### Analytics and review` section adjacent to **Head-to-head record**). **Scope-out (locked):** same-team Rounds excluded entirely; no MVP / most-impactful surface (HX-03 only — pairwise comparison doesn't transfer); no per-player-per-role asymmetric matchup view (deferred — `?role=` is symmetric 'both' only); no seasonal/tournament/month filter; no model change / migration / ADR / CONTEXT.md edit; no simulation-mechanics change; no new ORM column / serializer / REST API surface; no backfill; no Score Calibration re-baseline; no top-nav / match_list / team-history entry point.

**Locked names.** URL `GET /matches/h2h/player/` (URL name `player_head_to_head`); view `matches/views.py::player_head_to_head`; pure module `matches/player_h2h_stats.py` (7 functions: `compute_round_record`, `compute_score_margin`, `compute_tag_stats`, `compute_per_role_breakdown`, `compute_per_map_breakdown`, `margin_series`, `cumulative_wl_series`); template `templates/matches/player_head_to_head.html`; DOM ids `player-h2h-picker-form` / `player-h2h-select-a` / `player-h2h-select-b` / `player-h2h-role` / `player-h2h-provenance` / `player-h2h-from` / `player-h2h-to` / `player-h2h-submit` / `player-h2h-round-record` / `player-h2h-score-margin` / `player-h2h-tags-a-to-b` / `player-h2h-tags-b-to-a` / `player-h2h-per-role-table` / `player-h2h-per-map-table` / `player-h2h-detail-list` / `player-h2h-no-games-notice` / `player-h2h-error-banner` / `player-h2h-link`; canvas ids `player-h2h-margin-chart` / `player-h2h-cumulative-wl-chart`; `json_script` ids `player-h2h-margin-series` / `player-h2h-cumulative-wl-series`; test files `matches/tests/test_player_h2h_stats.py` (NEW) + `matches/tests/views_tests.py::TestHx04PlayerHeadToHead` (EXTENDED). Seam contract: [`.claude/worktrees/hx-04-seam-contract.md`](../../.claude/worktrees/hx-04-seam-contract.md).

## LG-01 league / season foundation

Foundation layer for single-player **League mode** (CONTEXT.md `### League and seasons` — domain terms **League**, **Season**, **Standings**). Ships two new models, a `Match.season` FK, two pure modules, one new simulator entry point, two read-only views, and admin registrations — behind admin + two GET pages. User-facing surfaces (mode picker, create flow, dashboards, Play Next, history, team game log) are deferred to **LG-01a..g** and grilled separately. Decisions are locked in [ADR-0014](../../docs/adr/0014-league-season-foundation.md) (model + state machine) and [ADR-0015](../../docs/adr/0015-schedule-on-demand-no-fixture-rows.md) (algorithm surface, no `ScheduleEntry` table).

**Models** (`matches/models.py`). `League(name, mode, state, created_at)` with `mode ∈ {sandbox, league, multiplayer}` (default `"league"`), `state ∈ {active, archived}` (default `"active"`), and an `active_season` `@property` returning the single non-`completed` Season per League. `Season(league, name, start_date, teams, state, schedule_format, starting_team_ids_json, champion_team, created_at)` — `league FK(League, on_delete=CASCADE, related_name="seasons")`, **required `start_date: DateField()`** (no default), `teams M2M(Team, related_name="enrolled_seasons")`, `state ∈ {draft, active, completed}` (default `"draft"`), `schedule_format ∈ {single_round_robin}` (default `"single_round_robin"`, `max_length=32` headroom), `starting_team_ids_json: JSONField(null=True, blank=True, default=None)` (snapshotted at activation, sorted ascending), `champion_team FK(Team, on_delete=SET_NULL, related_name="seasons_won")`, `__str__ = f"{league.name} — {name}"` (em-dash U+2014). `Match.season` adds `FK(Season, null=True, blank=True, on_delete=SET_NULL, related_name="matches")` — sandbox Matches stay `season=NULL` forever, no backfill (mirrors [ADR-0004](../../docs/adr/0004-simulation-data-is-disposable.md)); deleting a Season SET_NULLs its Matches rather than cascading them out of history. Three Season methods (signatures pinned): `clean() -> None` enforces the **Active-Season invariant** (≤ 1 non-`completed` Season per League — raises `django.core.exceptions.ValidationError`); `start_season() -> None` (`@transaction.atomic`) flips `draft → active`, raises `ValidationError` on `< 2` teams, and snapshots `starting_team_ids_json = sorted([t.id for t in self.teams.all()])`; `complete_if_finished() -> None` (`@transaction.atomic`, **idempotent**) — no-op on non-`active`, otherwise compares persisted `GameRound`s against `generate_schedule(...)` fixtures via the **Side-agnostic frozenset key** (`frozenset({team_red_id, team_blue_id}) == frozenset({fixture.team_a_id, fixture.team_b_id})` AND matching `round_number`) and on all-played flips `state="completed"` + computes Standings + stamps `champion_team = Team.objects.get(pk=rows[0].team_id)`. State machine: `draft → active` (explicit, M2M locked at activation — to change roster, start the next Season); `active → completed` (auto-transition the moment the last fixture's `GameRound` persists). Imports added: `from django.core.exceptions import ValidationError`, `from django.db import transaction`.

**Pure module `matches/schedule_generator.py`** ships `SCHEDULE_FORMATS: tuple[str, ...] = ("single_round_robin",)`, the frozen dataclass `ScheduleFixture(matchday: int, round_number: int, team_a_id: int, team_b_id: int)`, and `generate_schedule(team_ids: list[int], schedule_format: str = "single_round_robin") -> list[ScheduleFixture]`. Algorithm: sort ascending → reject `< 2` with `ValueError` → odd N appends bye sentinel `-1` (internal, never appears in output) → **circle method** with team-at-index-0 fixed and the remaining slots rotating → each fixture normalised so `team_a_id = min(pair)` / `team_b_id = max(pair)` → round-1 matchdays `1..N-1` mirrored into round-2 matchdays `N..2*(N-1)` (strict mirror, no interleaving — see [ADR-0015](../../docs/adr/0015-schedule-on-demand-no-fixture-rows.md)) → bye-involving fixtures dropped → output sorted by `(matchday, team_a_id)`. Output is a function of the *set* (input order independent — `generate_schedule([5,1,3,7]) == generate_schedule([1,3,5,7])`). N=4 ⇒ 12 fixtures (6 per round); N=8 ⇒ 56 fixtures; N=5 (odd) ⇒ 10 matchdays × 2 played fixtures = 20.

**Pure module `matches/standings.py`** ships the frozen dataclass `StandingsRow(team_id, matches_played, wins, losses, ties, league_points, round_wins, total_score, rank)` (**9 fields, pinned order**) and `compute_standings(completed_matches: list[dict], enrolled_teams: list[tuple[int, str]]) -> list[StandingsRow]`. The **input dict shape** is **8 frozen keys**: `match_id, team_red_id, team_blue_id, winner_team_id` (`None` ⇒ tie), `red_rounds_won, blue_rounds_won, red_total_points` (includes team-elim bonus via the existing `@property`), `blue_total_points`. Aggregation: `league_points = 3*W + 1*T + 0*L`; defensive HX-03 precedent — `winner_team_id` neither team's id counts as a tie; teams in `enrolled_teams` with no matches get a fully-zeroed row. Sort ladder (in order): `league_points` desc, `round_wins` desc, `total_score` desc, `team_name` asc — the **alphabetical tiebreak lives INSIDE the pure module** (the `(team_id, team_name)` tuple shape carries the name across the seam) so the full ordering is unit-testable with zero DB. `rank` is 1-based, dense, in iteration order.

Both pure modules carry a **frozen import allowlist** (`dataclasses`, `typing`, optionally `collections` — **NO** Django, NO `random`, NO `datetime`, NO I/O, NO logging) defended by a `TestNoDjangoImportsLeaked` subprocess fresh-import + `sys.modules` walk that mirrors the HX-01 / HX-02 / HX-03 / HX-04 / RES-04 / RV-03 / LG-00 / LG-00b precedent.

**Simulator entry point** (`matches/simulation/entrypoints.py`) — the sole writer for Season Matches: `BatchSimulator.simulate_scheduled_round(self, season, team_a, team_b, round_number, *, arena_map=None) -> GameRound` (`@transaction.atomic`). Guards (in order): `ValueError` when `season.state != "active"`, `ValueError` when `round_number not in (1, 2)`, `ValueError` when `round_number == 2` and no existing Match. **Side-agnostic Match lookup** is inlined (no helper method): two ORM queries `(season=…, team_red=team_a, team_blue=team_b).first()` ELSE `(season=…, team_red=team_b, team_blue=team_a).first()` — round-1 `(team_a, team_b)` and round-2 `(team_b, team_a)` resolve to the same row. Round 1 find-or-creates the Match (`is_completed=False`), delegates to the per-Round entry point used by `simulate_match` byte-for-byte (same `arena_map` resolution, same seed-handling, same `_flush_to_db` parameters — **no new RNG draws**), persists `GameRound(round_number=1, …)`, writes `match.red_round1_*` / `blue_round1_*` / `round1_eliminated_at`, leaves `is_completed=False`. Round 2 looks up Side-agnostically (raise on missing), delegates with **args reversed** (`team_red=team_b, team_blue=team_a` — mirrors the per-Match colour swap in `simulate_match` verbatim; the round-2 `GameRound` persists with team_b as physical red), persists `GameRound(round_number=2, …)`, writes the `*_round2_*` fields, **sets `is_completed=True`** + `match.save()` (triggers the existing `calculate_winner` via the `save()` override). After persistence (either round), calls `season.complete_if_finished()` (idempotent — no-op except on the final fixture). The existing `simulate_match` (both-Rounds atomic, sandbox-Match entry point) is **kept verbatim** — sandbox Matches with `season=NULL` use it; Season Matches must use the new method. No simulation mechanics change → **no SIM-07 / SIM-08 contract interaction, no Score Calibration re-baseline**.

**Views** (`matches/views.py`). `season_standings(request, season_id)` branches on `season.state`: `"draft"` ⇒ **draft preview** mode listing `season.teams.all()` sorted by `(-team_overall, name)` where `team_overall = mean(p.overall_rating for p in team.active_players) if team.active_players else 0.0` (uses the existing `Team.active_players` `@property` over the 6 `slot_*` FKs + the existing `Player.overall_rating` `@property` — **explicitly NO `Player.is_bench` field added**, bench is derived from "not in any `slot_*` FK"), rows rendered as zero-filled `StandingsRow`-shaped dicts with `is_draft_preview=True` flipping the "Preview — Season not started" banner. `"active"` / `"completed"` ⇒ live mode: `Match.objects.filter(season=season, is_completed=True)` materialised into the 8-key dicts, `team_ids` taken from `starting_team_ids_json` (defensive fallback to live M2M), `enrolled_teams = list(Team.objects.filter(id__in=team_ids).values_list("id", "name"))`, `rows = compute_standings(...)`. View assembles `rows_with_teams = [(row, teams_by_id[team_id]) for row in rows]` so the template iterates `(row, team)` tuples directly. Context keys (frozen): `season, rows, rows_with_teams, is_draft_preview`. `season_schedule(request, season_id)` calls `generate_schedule(team_ids, season.schedule_format)` (skipped with `fixtures=[]` when `len(team_ids) < 2`), indexes persisted `GameRound`s by `(frozenset({match.team_red_id, match.team_blue_id}), round_number)` for played-overlay lookup, groups per-fixture dicts (`matchday, round_number, team_a_id, team_b_id, team_a, team_b, played, game_round_id, red_score, blue_score, date = season.start_date + (matchday - 1) * 7 days`) into `matchdays = list[{"matchday": int, "date": date, "fixtures": list[per-fixture]}]`. Context keys (frozen): `season, matchdays`. Imports added to `views.py`: `from .schedule_generator import generate_schedule`, `from .standings import compute_standings`, `from teams.models import Team`, `from datetime import timedelta`.

**URLs**. NEW file `matches/season_urls.py` (no `app_name` — bare URL namespace, mirrors `teams/player_urls.py`) with `path("<int:season_id>/standings/", views.season_standings, name="season_standings")` and `path("<int:season_id>/schedule/", views.season_schedule, name="season_schedule")`. Mounted in `laserforce_simulator/urls.py` as `path("seasons/", include("matches.season_urls"))` immediately after the existing `path("matches/", include("matches.urls"))` line. Both URLs are **GET-only** (no POST in LG-01 — Play Next is LG-01d). Reverse via bare names (`reverse("season_standings", args=[season.id])`).

**Templates** (`templates/seasons/`). `standings.html` extends `base.html` with locked DOM ids `season-standings-table` (outer `<table>`), `season-standings-empty` (rendered when `is_draft_preview AND len(rows) == 0`), `season-draft-preview-banner`, `season-state-badge` (`{{ season.state }}`); header row order **left to right**: `Rank | Team | MP | W | L | T | Pts | RW | TS` (MP=matches_played, Pts=league_points, RW=round_wins, TS=total_score); team cells link to `{% url 'team_detail' team.id %}`. `schedule.html` extends `base.html` with locked DOM ids `season-schedule-table`, `season-schedule-empty` (when `len(matchdays) == 0`), and per-matchday `season-schedule-matchday-{n}` (1-based); each matchday section shows `Matchday {n} — {date|date:"Y-m-d"}` and per-fixture rows (team-a vs team-b, `round_number`, either `red_score`–`blue_score` with optional `GameRound` detail link or literal `Unplayed`).

**Admin** (`matches/admin.py` — inserted AFTER existing registrations, no existing registration modified). `LeagueAdmin.list_display = ("name", "mode", "state", "created_at")`; `SeasonAdmin.list_display = ("name", "league", "state", "schedule_format", "start_date")` + **`filter_horizontal = ("teams",)`** (the M2M dual-select widget).

**Migration**. Single file `matches/migrations/0029_league_season_match_fk.py` depending on `("matches", "0028_gameround_is_simulated")` + the latest `teams` migration at branch-cut time. Operations in pinned order: `CreateModel(League)` → `CreateModel(Season)` → `AddField(Match, season)`. **No `RunPython`, no `RunSQL`, no backfill**.

**Tests** live in four NEW files under `matches/tests/` (full listing in the `## Tests` section below): `test_schedule_generator.py` (pure-unit), `test_standings.py` (pure-unit), `test_league_models.py` (Django `TestCase`), `test_league_simulator.py` (Django `TestCase`). Simulator tests use small-N seeded simulations (N=2 / N=3) and assert on schema-level outcomes, not exact score totals.

**Determinism / scope.** Read-only views (no writes, no RNG); `simulate_scheduled_round` is pure orchestration over the existing per-Round simulator — per-Round RNG consumption is byte-for-byte identical to `simulate_match` at round-1 and round-2 time separately. **No Score Calibration re-baseline.** The Active-Season invariant is the only data-integrity rule enforced at the model layer; schedule determinism is enforced by the `starting_team_ids_json` snapshot at activation plus the pure module's input sort (defence in depth). **No CONTEXT.md edit** (the `League` / `Season` / `Standings` glossary entries were added at grilling time). **No ADR write** ([ADR-0014](../../docs/adr/0014-league-season-foundation.md) + [ADR-0015](../../docs/adr/0015-schedule-on-demand-no-fixture-rows.md) were both written at grilling time). **Scope-out (deferred to LG-01a..g)**: mode picker landing, `/leagues/` list, create-League flow, dashboards, Play Next, "Start Next Season" chain, League history, per-Team game log. **Scope-out (within LG-01)**: no `simulate_match` change, no sandbox URL / view change, no `Player.is_bench` field, no `Season.matchday_cadence_days`, no `League.owner_user`, no `Match.state` enum, no API / DRF endpoint, no batch-sim / Celery touch, no backfill, no `Player` / `Team` app touch beyond the auto-generated reverse accessors `Team.enrolled_seasons` + `Team.seasons_won`.

**Locked names.** Models `matches.models.League` + `matches.models.Season` + the `Match.season` FK; related names `seasons` / `enrolled_seasons` / `seasons_won` / `matches`; pure modules `matches/schedule_generator.py` + `matches/standings.py`; dataclasses `ScheduleFixture(matchday, round_number, team_a_id, team_b_id)` + `StandingsRow(team_id, matches_played, wins, losses, ties, league_points, round_wins, total_score, rank)`; functions `generate_schedule` + `compute_standings`; constant `SCHEDULE_FORMATS = ("single_round_robin",)`; bye sentinel `-1` (internal); match dict 8 keys `match_id, team_red_id, team_blue_id, winner_team_id, red_rounds_won, blue_rounds_won, red_total_points, blue_total_points`; simulator method `BatchSimulator.simulate_scheduled_round(self, season, team_a, team_b, round_number, *, arena_map=None) -> GameRound`; URL file `matches/season_urls.py`; URL names `season_standings` + `season_schedule`; views `matches.views.season_standings` + `matches.views.season_schedule`; templates `templates/seasons/standings.html` + `templates/seasons/schedule.html`; DOM ids `season-standings-table` / `season-standings-empty` / `season-draft-preview-banner` / `season-state-badge` / `season-schedule-table` / `season-schedule-empty` / `season-schedule-matchday-{n}`; admin classes `matches.admin.LeagueAdmin` + `matches.admin.SeasonAdmin`; migration `matches/migrations/0029_league_season_match_fk.py`; test files `matches/tests/test_schedule_generator.py` + `matches/tests/test_standings.py` + `matches/tests/test_league_models.py` + `matches/tests/test_league_simulator.py`. Seam contract: [`.claude/worktrees/lg-01-seam-contract.md`](../../.claude/worktrees/lg-01-seam-contract.md).

## LG-01a leagues list

Flat list of every **League** (active + archived sections) at `GET /leagues/`, plus a `Create League` button — the matches-side half of the LG-01a user-facing surface layer over the LG-01 foundation (the landing page itself lives in [`core/CLAUDE.md`](../core/CLAUDE.md) **LG-01a landing view**).

**URL.** Mounted in `laserforce_simulator/urls.py` as `path("leagues/", include("matches.league_urls"))` (inserted immediately after the existing `path("seasons/", include("matches.season_urls"))` line, before `path("maps/", ...)`). NEW file `matches/league_urls.py` mirrors `matches/season_urls.py` (no `app_name` — bare URL name) with a single `path("", views.league_list, name="league_list")`; `reverse("league_list")` resolves to `/leagues/`.

**View.** `matches.views.league_list(request) -> HttpResponse` — undecorated, GET-driven (no explicit method allowlist). Body: two ORM queries — `active_leagues = list(League.objects.filter(state="active").order_by("-id"))` and `archived_leagues = list(League.objects.filter(state="archived").order_by("-id"))` — then `render("leagues/list.html", {"active_leagues": active_leagues, "archived_leagues": archived_leagues})`. Context (frozen): `{active_leagues, archived_leagues}`. The view does NOT touch `Season`, `active_season`, or any per-League aggregation — `/leagues/` is the flat index; per-League dashboards are deferred to LG-01c.

**Template.** NEW `templates/leagues/list.html` extends `base.html`, `{% block title %}Leagues{% endblock %}`. Locked DOM ids: `league-list-active-table` (outer `<table>` — rendered ONLY when `active_leagues` is non-empty, omitted otherwise), `league-list-archived-table` (rendered ONLY when `archived_leagues` is non-empty), `league-list-empty-notice` (rendered ONLY when both lists are empty, containing the substring `No Leagues yet`), `league-create-link` (always rendered — the `Create League` button). Per-row League-name `<td>` contains an `<a>` whose `href` is the **raw string `/leagues/{{ league.id }}/`** (NOT `{% url 'league_detail' ... %}`); the row's state cell renders `league.state` inside an element whose `class` attribute contains the substring `state-badge`. **Deferred broken-link decision (locked):** the per-row `/leagues/<id>/` links resolve at LG-01c and the `league-create-link` `href="/leagues/create/"` resolves at LG-01b — both 404 at LG-01a merge time and the web-smoke triage acknowledges these known-broken links.

**Determinism / scope.** Read-only view — no writes, no RNG, no simulation, no `_flush_to_db` touch, no SIM-07 / SIM-08 contract interaction, no Score Calibration re-baseline. **No model change, no migration, no ADR, no CONTEXT.md edit, no new aggregation module** (read-only view with inline `.filter().order_by()` is sufficient — LG-00c precedent; these queries are even simpler), no JS, no new dependency, no API / DRF endpoint. **Tests** live in NEW `matches/tests/test_league_list.py` — 9 view tests covering 200 status + context keys, URL reverse, empty-state notice + always-rendered create button, active table sort + DOM id, archived table sort + DOM id, active-only omission, archived-only omission, per-row deferred `/leagues/<id>/` href, and the deferred `/leagues/create/` href on the create button.

**Locked names.** URL `GET /leagues/` (URL name `league_list`, no `app_name`); URL include file `matches/league_urls.py`; view `matches.views.league_list`; template `templates/leagues/list.html`; DOM ids `league-list-active-table` / `league-list-archived-table` / `league-list-empty-notice` / `league-create-link`; context keys `active_leagues, archived_leagues`; test file `matches/tests/test_league_list.py`. Seam contract: [`.claude/worktrees/lg-01a-seam-contract.md`](../../.claude/worktrees/lg-01a-seam-contract.md).

## LG-01b create-league flow

`GET/POST /leagues/create/` form that creates a `League(state=active)` + initial `Season(state=draft)` + auto-generated `Team`s (via the existing LG-00 `teams.views._generate_teams` helper) enrolled into the Season's M2M, then redirects to `/seasons/<season_id>/standings/`. This is the thin CRUD surface that fills the LG-01a deferred broken-link gap — the `league-create-link` `href="/leagues/create/"` rendered on `templates/leagues/list.html` (and 404'd at LG-01a merge time per the LG-01a triage) now resolves to the new GET endpoint without any `templates/leagues/list.html` edit.

**URL.** A new `path("create/", views.league_create, name="league_create")` is inserted into `matches/league_urls.py` **BEFORE** the existing `path("", views.league_list, name="league_list")` entry — Django URL resolution is first-match, so the empty-string `league_list` pattern would otherwise capture every request mounted at `/leagues/`. Final `urlpatterns` order is `[create/, ""]`. `reverse("league_create")` resolves to `/leagues/create/`. Both GET and POST hit the same view; no `HttpResponseNotAllowed` guard on other methods.

**Form.** `matches.forms.CreateLeagueForm(forms.Form)` is appended to the existing `matches/forms.py` (alongside `MatchSetupForm` / `SingleRoundSetupForm` / `BatchSimulateForm`) with **7 fields in pinned order**: `league_name = forms.CharField(max_length=100)` (required, no uniqueness validation — duplicate League names allowed and differ only by `id`), `season_name = forms.CharField(max_length=100, initial="Season 1")`, `start_date = forms.DateField(initial=django.utils.timezone.localdate)` (callable initial, evaluated per-bind), `num_teams = forms.TypedChoiceField(choices=[(4, "4"), (8, "8"), (12, "12"), (16, "16")], coerce=int, empty_value=None, initial=4)` (so `cleaned_data["num_teams"]` is an `int`, not a `str`), `schedule_format = forms.ChoiceField(choices=[("single_round_robin", "Single round-robin")], disabled=True, initial="single_round_robin")` (single-option `disabled=True` — Django serves the initial value regardless of POST content so a tampered POST cannot inject a different format, no extra `clean_schedule_format` guard), `mean = forms.IntegerField(min_value=0, max_value=100, initial=50)`, and `std_dev = forms.IntegerField(min_value=1, max_value=40, initial=15)`. **`players_per_team` is NOT a form field** — it is fixed at the **literal `6`** server-side inline in the view body (locked, not configurable per create). No `Meta`, no `__init__` override, no custom widgets pinned.

**View.** `matches.views.league_create(request: HttpRequest) -> HttpResponse` is appended to `matches/views.py`, decorated `@transaction.atomic` (single decorator — the entire body is one atomic block so a `_generate_teams` raise or any subsequent `Season.objects.create` failure rolls back the League + Season + Teams + Players + slot FKs + M2M rows atomically; no half-created League can exist on error). Body in **6 pinned steps**: (1) GET branch ⇒ `form = CreateLeagueForm()` + render `templates/leagues/create.html` with `{"form": form}` + return; (2) POST branch ⇒ `form = CreateLeagueForm(request.POST)` + on `not form.is_valid()` re-render the same template with the bound form (errors auto-attached, no `messages.*` flash) + return; (3) build `rng = random.Random()` (default-seeded — LG-01b does NOT pin a deterministic seed) plus defensive `team_names_pool = list(TEAM_NAMES)` and `player_names_pool = list(PLAYER_NAMES)` copies (mirrors the LG-00b roster-import precedent so `_generate_teams` may mutate pools internally without leaking back into the `teams/constants.py` module-level constants); (4) `created_teams = _generate_teams(cleaned["num_teams"], 6, rng=rng, mean=cleaned["mean"], std_dev=cleaned["std_dev"], team_names_pool=team_names_pool, player_names_pool=player_names_pool)` — the locked **literal `6`** is the `players_per_team` arg; (5) `league = League.objects.create(name=cleaned["league_name"], mode="league", state="active")` + `season = Season.objects.create(league=league, name=cleaned["season_name"], start_date=cleaned["start_date"], state="draft", schedule_format=cleaned["schedule_format"])` (the `"league"` / `"active"` / `"draft"` / `"single_round_robin"` literals are all field-level defaults but kept explicit for clarity); (6) `season.teams.add(*created_teams)` (M2M bulk-add, no per-team `.save()`) + `return redirect("season_standings", season_id=season.id)`. The redirect URL name `season_standings` and its reverse kwarg `season_id` are pinned by `matches/season_urls.py` (LG-01).

**Cross-app import.** The single new line `from teams.views import _generate_teams` at the top of `matches/views.py` is the **only** cross-app import LG-01b introduces (name pools come from `teams.constants` via `from teams.constants import TEAM_NAMES, PLAYER_NAMES`, no `teams.views`-side state crosses). The leading underscore on `_generate_teams` reflects its intra-`teams/` private status; LG-01b promotes it to a cross-app seam read-only — **no rename, no signature change, no relocation, no edit to `teams/views.py`**. The function signature `_generate_teams(num_teams: int, players_per_team: int, *, rng: random.Random, mean: int, std_dev: int, team_names_pool: list[str], player_names_pool: list[str]) -> list[Team]` IS the seam contract and must not change.

**Template.** NEW `templates/leagues/create.html` extends `base.html`, `{% block title %}Create League{% endblock %}` (locked exact string), with a single `<form method="post">` containing `{% csrf_token %}`, the 7 form fields rendered field-by-field (NOT `{{ form.as_p }}` / `{{ form.as_table }}` — DOM ids must be deterministic) with per-field `{{ form.<field>.errors }}` blocks adjacent to each input, plus a submit button. **9 locked DOM ids**: `league-create-form` (outer `<form>`), `league-create-league-name` (the `<input type="text">` for `league_name`), `league-create-season-name` (the `<input>` for `season_name`), `league-create-start-date` (the `<input type="date">` for `start_date`), `league-create-num-teams` (the `<select>` for `num_teams`), `league-create-schedule-format` (the `<select disabled>` for `schedule_format` — `disabled` is the only client-side affordance, a pure HTML attribute not JS), `league-create-mean` (the `<input type="number">` for `mean`), `league-create-std-dev` (the `<input type="number">` for `std_dev`), and `league-create-submit` (the submit button).

**Determinism / scope.** Read-only LG-01b consumes no RNG into the simulator — `rng = random.Random()` is default-seeded and flows only into `_generate_teams` (intentionally random per create, no SIM-07 / SIM-08 contract interaction); no simulation mechanics change → **no Score Calibration re-baseline**. **No model change** (`matches/models.py` read-only at LG-01b), no migration (LG-01's `0029_league_season_match_fk.py` is the final migration in the LG-01x stack until LG-01c), no ADR write ([ADR-0014](../../docs/adr/0014-league-season-foundation.md) + [ADR-0015](../../docs/adr/0015-schedule-on-demand-no-fixture-rows.md) cover the foundation, LG-01b is a CRUD surface and needs no design record), no CONTEXT.md edit (`League` / `Season` / `Standings` glossary entries exist from LG-01), no "Start Season" UI / POST endpoint (the `draft → active` transition via `Season.start_season()` is deferred — LG-01b leaves the Season in `draft` indefinitely and the LG-01 standings page renders the draft preview), no JS, no API / DRF endpoint, no `messages.success(...)` flash, no new dependency, no edit to `teams/views.py` / `teams/forms.py` / `teams/constants.py`, no edit to `templates/leagues/list.html` (LG-01a's `league-create-link` continues to point at the now-resolving URL without template-side changes), no edit to `LeagueAdmin` / `SeasonAdmin`, no Free Agents Team touch.

**Tests** live in NEW `matches/tests/test_league_create.py` with 4 `TestCase` subclasses: `TestLeagueCreateGet` (GET → 200, `assertTemplateUsed("leagues/create.html")`, all 9 locked DOM ids present, `schedule_format` `<select>` carries `disabled`, `reverse("league_create")` resolves), `TestLeagueCreatePost` (POST valid → 302 to `reverse("season_standings", args=[season.id])`, the locked League / Season / Team row shapes, each Team has 6 active-slot Players, the redirect target returns 200 exercising the LG-01 standings view's `is_draft_preview` branch, plus an `N=16` boundary creating 16 Teams + 96 Players), `TestLeagueCreateFormValidation` (per-field validator errors for missing `league_name`, `num_teams=5`, `mean=-1`, `mean=101`, `std_dev=0`, `std_dev=41`, empty `start_date`, plus a `schedule_format="double_round_robin"` tamper-POST that still persists the Season with `schedule_format="single_round_robin"`), and `TestSeamWithGenerateTeams` (**locked: NO `mock.patch` on `_generate_teams`** — the real function is exercised end-to-end so signature drift surfaces as a test failure rather than a silent mock pass; the transaction-rollback test patches `Season.objects.create` (NOT `_generate_teams`) to raise mid-flow and asserts post-raise zero `League` + zero `Team` rows, pinning the `@transaction.atomic` boundary against future refactors). Tests must NOT touch `simulate_scheduled_round` or any simulator code path (LG-01b runs no simulation; entering the simulator would be a scope leak).

**Locked names.** URL `POST /leagues/create/` + `GET /leagues/create/` (URL name `league_create`, no `app_name`); view `matches.views.league_create`; form class `matches.forms.CreateLeagueForm`; form fields `league_name, season_name, start_date, num_teams, schedule_format, mean, std_dev` (7 fields in pinned order, `players_per_team` is NOT a field); template `templates/leagues/create.html`; cross-app import `from teams.views import _generate_teams`; DOM ids `league-create-form` / `league-create-league-name` / `league-create-season-name` / `league-create-start-date` / `league-create-num-teams` / `league-create-schedule-format` / `league-create-mean` / `league-create-std-dev` / `league-create-submit`; redirect URL name `season_standings` (reverse kwarg `season_id`); test file `matches/tests/test_league_create.py` with classes `TestLeagueCreateGet` / `TestLeagueCreatePost` / `TestLeagueCreateFormValidation` / `TestSeamWithGenerateTeams`; locked literals `players_per_team = 6` / `mode = "league"` / `state = "active"` (League) / `state = "draft"` (Season) / `schedule_format = "single_round_robin"`. Seam contract: [`.claude/worktrees/lg-01b-seam-contract.md`](../../.claude/worktrees/lg-01b-seam-contract.md).

## LG-01c league / season dashboard

Read-only dashboard views over the LG-01 foundation: `GET /leagues/<int:league_id>/` and `GET /seasons/<int:season_id>/`. Both render a state badge + placeholder action button + top-3 standings snippet + next round + round count + three leaders snippets, branching on the displayed Season's `state` (`draft` / `active` / `completed`) plus the league-only `none` branch when the League has zero Seasons. Action buttons (`Start Season` / `Play Next` / `Start Next Season`) are `<button disabled>` placeholders keyed off Season state; their POST counterparts are deferred to LG-01d / LG-01e. The season dashboard adds a 5-entry sidebar with live links to the LG-01 standings / schedule pages plus disabled `<span>` placeholders for Teams / History (deferred to LG-01f / LG-01g). **No model change, no migration, no ADR, no CONTEXT.md edit, no POST endpoint, no simulator touch.**

**URLs.** Two single-line inserts, no new URL include file. `matches/league_urls.py` gains `path("<int:league_id>/", views.league_dashboard, name="league_dashboard")` inserted **AFTER** the LG-01b `path("create/", …)` line and **BEFORE** the LG-01a `path("", views.league_list, …)` line — Django URL resolution is first-match so the typed `<int:league_id>/` pattern matches only digit-only paths, leaving `/leagues/` (list) and `/leagues/create/` (form) untouched; final `urlpatterns` order is `[create/, <int:league_id>/, ""]`. `matches/season_urls.py` gains `path("<int:season_id>/", views.season_dashboard, name="season_dashboard")` inserted at the **top** of `urlpatterns` so it does not get shadowed by the longer LG-01 `<int:season_id>/standings/` / `<int:season_id>/schedule/` patterns; final order is `[<int:season_id>/, <int:season_id>/standings/, <int:season_id>/schedule/]`. Full URLs `/leagues/<int:league_id>/` and `/seasons/<int:season_id>/`; reverse names `league_dashboard` and `season_dashboard` (bare names, no `app_name`, mirroring `league_list` / `league_create` / `season_standings` / `season_schedule`); both **GET-only** with explicit `if request.method != "GET": return HttpResponseNotAllowed(["GET"])` as the **first** line of each view body (mirrors the `movement_heatmap` / `export_round_report` 405 guard precedent).

**Views** (`matches/views.py`). `league_dashboard(request: HttpRequest, league_id: int) -> HttpResponse` and `season_dashboard(request: HttpRequest, season_id: int) -> HttpResponse` are both undecorated (no `@transaction.atomic` — read-only; no `@require_GET` — the explicit `HttpResponseNotAllowed` guard is the locked pattern), each `get_object_or_404(League, pk=league_id)` / `(Season, pk=season_id)` for the 404 branch. The league view's **season-pick logic** (locked, in order): consult `league.active_season` (the LG-01 `@property` — `seasons.exclude(state="completed").order_by("-id").first()` semantics; the implementation **calls the property, not re-implements the query**); when non-`None`, `displayed_season = active` and `season_mode = "draft"` if `active.state == "draft"` else `"active"`; else fall back to `completed_recent = league.seasons.filter(state="completed").order_by("-id").first()` and set `displayed_season = completed_recent` with `season_mode = "completed"`, else `displayed_season = None` with `season_mode = "none"`. The season view's pick is trivial — `displayed_season = season` and `season_mode = season.state` (one of `"draft" | "active" | "completed"`, **never `"none"`** since the Season exists by virtue of URL resolution). Body assembly delegates to the shared private module-level helper `_build_dashboard_context(displayed_season: Season | None, season_mode: str) -> dict` (RV-01 / HX-03 `_`-prefixed flat-helper precedent) which returns the **11-key body context** `displayed_season, season_mode, standings_snippet, next_fixture, round_count_completed, round_count_total, leaders_points, leaders_tags, leaders_ratio, action_button_label, action_button_state`; the league view's final context is the body context plus the `league` key (12 keys total) and the season view's is the body context plus `season, sidebar_active="overview", sidebar_links` (15 keys total — `displayed_season` is kept `== season` for template-include parity with the league dashboard).

**Branch-specific population.** `"none"` (league-only — no Season): `standings_snippet = []`, `next_fixture = None`, `round_count_* = 0`, `leaders_* = []`, `action_button_label = "No Season"`, `action_button_state = "none"`. `"draft"`: standings snippet is the zero-filled top-3 from `displayed_season.teams.all()` sorted by name ascending, each row a dict with the 9 LG-01 standings keys all zeroed (`team_id, matches_played=0, wins=0, losses=0, ties=0, league_points=0, round_wins=0, total_score=0, rank=i+1`) paired with its `team`; `next_fixture = None`, `round_count_* = 0`, `leaders_* = []`, `action_button_label = "Start Season"`, `action_button_state = "start_season"`. `"active"`: standings via `compute_standings(...)` (LG-01) over `Match.objects.filter(season=displayed_season, is_completed=True)`, top 3 rows paired with their Teams via a single `Team.objects.in_bulk(...)`; `fixtures = generate_schedule(displayed_season.starting_team_ids_json, displayed_season.schedule_format)`; `played_keys = {(frozenset({gr.match.team_red_id, gr.match.team_blue_id}), gr.round_number) for gr in GameRound.objects.filter(match__season=displayed_season).select_related("match")}`; `fixture = find_next_fixture(fixtures, played_keys)` (`None` ⇒ `next_fixture = None`, else built into the 7-key frozen `next_fixture` dict); `round_count_completed, round_count_total = round_progress(fixtures, played_keys)`; `leaders_* = compute_leaders(player_rounds, stat, limit=3)` per stat; `action_button_label = "Play Next"`, `action_button_state = "play_next"`. `"completed"`: standings same as `"active"`; `find_next_fixture` returns `None` on an all-played Season (the LG-01 `complete_if_finished` completion invariant) so `next_fixture = None`; `round_progress` returns `(len(fixtures), len(fixtures))`; leaders same as `"active"`; `action_button_label = "Start Next Season"`, `action_button_state = "start_next_season"`.

**Pure module `matches/season_dashboard.py`** ships the leader-aggregation + next-fixture + round-progress helpers. **Frozen import allowlist** (`dataclasses`, `typing`, optionally `collections.defaultdict`) — **NO** Django, NO ORM, NO `random` / `secrets`, NO `datetime`, NO file I/O, NO logging, NO `matches.schedule_generator` (the module consumes `ScheduleFixture` instances passed in by the view via `TYPE_CHECKING` / string annotations — the dataclass shape is the cross-module contract, the import allowlist stays truly frozen). Defended by `TestNoDjangoImportsLeaked` (subprocess fresh-import + `sys.modules` walk, mirrors the HX-03 / HX-04 / RES-04 / LG-01 / LG-01a / LG-01b precedent). The module surfaces the frozen dataclass **`LeaderRow(player_id, player_name, role, team_id, team_name, value, games_played, rank)`** (`@dataclass(frozen=True)`, **8 fields in pinned order**), plus three functions: `compute_leaders(player_rounds: list[dict], stat: str, limit: int = 3) -> list[LeaderRow]` (aggregates one entry per `PlayerRoundState` row into a ranked top-`limit` leaders snippet; stat vocabulary is the **3 locked strings** `"points_per_game"` ⇒ `mean(points_scored)`, `"tags_per_game"` ⇒ `mean(tags_made)`, `"tag_ratio"` ⇒ `sum(tags_made) / max(sum(times_tagged), 1)` — the canonical CONTEXT.md sum/sum form, **NOT** mean of per-row ratios; the `max(..., 1)` denominator clamp avoids div-by-zero and matches the existing `Player.career_stats` rule, `value` is `float` even when both sums are 0 (`0 / 1 = 0.0`); deterministic sort ladder `value` desc → `games_played` desc → `player_id` asc; `rank` populated 1-based dense in iteration order; **empty input ⇒ `[]`** immediately; **unknown stat string ⇒ `ValueError(f"Unknown stat {stat!r}; expected one of points_per_game, tags_per_game, tag_ratio")`**; defensive "last row wins" for inconsistent role / team across a player's group — the view passes rows in `id` ascending so "last" == most-recent `PlayerRoundState`), `find_next_fixture(fixtures, played_keys) -> Optional[ScheduleFixture]` (returns the first `ScheduleFixture` whose `(frozenset({team_a_id, team_b_id}), round_number)` is NOT in `played_keys` — side-agnostic `frozenset` match; empty `fixtures` ⇒ `None`, all played ⇒ `None`), and `round_progress(fixtures, played_keys) -> tuple[int, int]` (`completed` = count of fixtures matched against `played_keys`, `total = len(fixtures)`; `completed` is **NOT** `len(played_keys)` — extra `GameRound` rows that don't correspond to a fixture are not double-counted, the defensive HX-03 precedent for data-drift; empty fixtures ⇒ `(0, 0)`).

**Player-round seam dict** (the only thing crossing the view ↔ pure-module seam for leader aggregation, **frozen 7 keys, every key required**): `player_id, player_name, role, team_id, team_name, tags_made, times_tagged, points_scored`. The view materialises one entry per `PlayerRoundState` row in the Season's completed Rounds via the locked queryset `PlayerRoundState.objects.filter(game_round__match__season=displayed_season).select_related("player", "game_round", "game_round__match").order_by("id")` (single `select_related`-flattened query, `order_by("id")` is what makes the "last row wins" defensive fallback deterministic); `player_name` reads via `prs.player.name`, `team_id` / `team_name` resolve from `prs.game_round.team_red` / `team_blue` keyed off `prs.team_color` (`"red"` ⇒ `team_red`, `"blue"` ⇒ `team_blue`). In the `"none"` branch `displayed_season is None` and the queryset is **not** issued — `leaders_* = []` directly.

**`next_fixture` dict shape** (frozen 7 keys, built view-side from a `ScheduleFixture` + the two Teams via a single `Team.objects.in_bulk(...)` per view call): `matchday` (1-based from `ScheduleFixture.matchday`), `round_number` (1 or 2), `team_a_id`, `team_a_name`, `team_b_id`, `team_b_name`, `date` — the `date` derived as `season.start_date + timedelta(days=(matchday - 1) * 7)` mirroring the LG-01 `season_schedule` per-matchday date derivation byte-for-byte so the dashboard and the schedule page agree on matchday dates.

**Templates** under `templates/leagues/` and `templates/seasons/`. `templates/leagues/dashboard.html` extends `base.html`, `{% block title %}{{ league.name }} — League{% endblock %}` (locked exact string, em-dash U+2014), with the following **10 locked DOM ids** and branch-presence rules: `league-dashboard-header` (always, outer header with `{{ league.name }}`), `league-dashboard-state-badge` (always, renders `season_mode`; the `"none"` branch renders the literal `"No Season"`), `league-dashboard-action-button` (always, the `<button disabled data-action-state="{{ action_button_state }}">` placeholder with text `== action_button_label`; the HTML `disabled` attribute MUST be present), `league-dashboard-standings-snippet` (draft / active / completed only, iterates the `(row, team)` tuples of `standings_snippet`), `league-dashboard-next-round` (active / completed only, renders the `next_fixture` body or the `"All fixtures played"` stub when `next_fixture is None and season_mode == "completed"`; **omitted entirely** in `"draft"` / `"none"`), `league-dashboard-round-count` (active / completed only, renders `{{ round_count_completed }} / {{ round_count_total }}`), `league-dashboard-leaders-points` / `league-dashboard-leaders-tags` / `league-dashboard-leaders-ratio` (active / completed only, each iterates its `leaders_*` list), and `league-dashboard-no-season-notice` (only when `season_mode == "none"`, contains the substring `"No Season"`). `templates/seasons/dashboard.html` extends `base.html`, `{% block title %}{{ season.league.name }} — {{ season.name }}{% endblock %}` (locked exact string), with **15 locked DOM ids**: `season-dashboard-header` (always), `season-dashboard-state-badge` (always), `season-dashboard-action-button` (always, same `<button disabled data-action-state="…">` shape as the league dashboard), `season-dashboard-sidebar` (always, outer `<nav>` / `<ul>` wrapping the 5 sidebar entries), `season-dashboard-sidebar-standings` (always, live `<a href>` reversed via `season_standings`), `season-dashboard-sidebar-schedule` (always, live `<a href>` reversed via `season_schedule`), `season-dashboard-sidebar-teams` (always, disabled `<span class="…disabled…">` — **NO `<a href>`**, the disabled placeholder), `season-dashboard-sidebar-history` (always, disabled `<span>`, same shape), `season-dashboard-standings-snippet` (always — the container is present even in `"draft"` where it iterates zero rows, so tests can assert the DOM id), `season-dashboard-next-round` (active / completed only), `season-dashboard-round-count` (active / completed only), `season-dashboard-leaders-points` / `season-dashboard-leaders-tags` / `season-dashboard-leaders-ratio` (active / completed only).

**`sidebar_links` shape** (frozen 5 entries in pinned order): `{"key": "overview", "label": "Overview", "url": None, "disabled": False, "active": True}`, `{"key": "standings", "label": "Standings", "url": reverse("season_standings", args=[season.id]), "disabled": False, "active": False}`, `{"key": "schedule", "label": "Schedule", "url": reverse("season_schedule", args=[season.id]), "disabled": False, "active": False}`, `{"key": "teams", "label": "Teams", "url": None, "disabled": True, "active": False}`, `{"key": "history", "label": "History", "url": None, "disabled": True, "active": False}`. The template iterates the list — `disabled=True` entries render as `<span class="sidebar-link disabled">…</span>` (no `<a href>` so they cannot be clicked); the overview entry (`key="overview"`, `active=True`, `url=None`, `disabled=False`) renders as `<span class="sidebar-link active">Overview</span>` (active is the current page, no link needed). `sidebar_active = "overview"` always at LG-01c — the only sidebar key the season dashboard ever marks active.

**Raw-href patterns** (LG-01a deferred broken-link precedent, locked). Per-leader anchors inside each `leaders_*` container render the **raw string `/players/{{ row.player_id }}/career-stats/`** (NOT `{% url 'player_career_stats' ... %}`) — LG-01c does NOT mount that route, the broken-link tolerance is locked; anchor text is `{{ row.player_name }}` and the `{{ row.value|floatformat:2 }}` is rendered adjacent. The **"View all leaders"** anchor below the three snippets renders the raw string `/leagues/{{ league.id }}/leaders/` on the league dashboard and `/seasons/{{ season.id }}/leaders/` on the season dashboard — both placeholders, both 404 at LG-01c merge time; tests assert the literal href substring is rendered. Standings-snippet rendering iterates the `(row, team)` tuples — only the iteration is pinned, not the exact cell layout (the Code agent picks Bootstrap class names). The action button's `<button disabled>` has text `== action_button_label` and a `data-action-state="{{ action_button_state }}"` attribute (tests assert both the label and the data attribute).

**Determinism / scope.** Both views are **pure read-derivations** — no writes, no RNG, no simulation kicked off, no `_flush_to_db` touch, no SIM-07 / SIM-08 contract interaction, no Score Calibration re-baseline obligation. The pure module consumes no RNG, no Django, no ORM, no `datetime` — it is unit-testable with zero DB. The 4 `season_mode` literals (`"draft" / "active" / "completed" / "none"`), the 4 `action_button_state` literals (`"start_season" / "play_next" / "start_next_season" / "none"`), and the 5 sidebar key literals (`"overview" / "standings" / "schedule" / "teams" / "history"`) are implementation enums — NOT domain language, NOT added to CONTEXT.md.

**Scope-out (locked).** No model change (`matches/models.py` read-only at LG-01c). No migration (LG-01's `0029_league_season_match_fk.py` remains the final LG-01x migration). No ADR write (LG-01c is a read-only view layer over already-decided foundations — nothing surprising-without-context, nothing hard-to-reverse, no real trade-off requiring a record; the seam contract is the only artifact). No CONTEXT.md edit (`League` / `Season` / `Standings` glossary entries exist from LG-01; leader-stat terminology and the 4 season-mode / 4 action-state / 5 sidebar-key implementation enums are documented inline here and in the pure module's docstring). No POST endpoint (both views are GET-only with explicit `HttpResponseNotAllowed(["GET"])`; the placeholder `<button disabled>`s are HTML-attribute disabling only — no `<form>` wrapper, no `csrf_token`, no `request.method == "POST"` branch). No `Season.start_season()` UI wire-up (deferred to LG-01d). No `simulate_scheduled_round` touch (LG-01c imports no simulator, runs no simulation, the LG-01d "Play Next" POST is deferred). No LG-01d / LG-01e / LG-01f / LG-01g logic (Play Next, Start Next Season chain, Teams tab, History tab are deferred). No Teams view, no History view (sidebar entries are disabled `<span>` placeholders). No `/leagues/<id>/leaders/` or `/seasons/<id>/leaders/` URL mount (the "View all leaders" anchors are raw hrefs that 404 — LG-01a deferred-broken-link precedent). No `/players/<id>/career-stats/` URL mount (the per-leader anchors are raw hrefs; the route may exist as a separate `teams/` URL but LG-01c does NOT depend on whether it does — tests check the literal href string, not URL resolution). No JS, no Chart.js, no htmx, no inline `<script>` blocks (server-rendered HTML only). No API / DRF endpoint. No new dependency. No edit to `matches/models.py` / `matches/simulation/` / `matches/standings.py` / `matches/schedule_generator.py` (all LG-01 pure modules consumed verbatim). No edit to `templates/seasons/standings.html` / `templates/seasons/schedule.html` / `templates/leagues/list.html` / `templates/leagues/create.html` (LG-01 / LG-01a / LG-01b templates unchanged). No edit to `LeagueAdmin` / `SeasonAdmin` (LG-01 admin registrations unchanged). No `messages.success(...)` / `django.contrib.messages` usage. No simulation mechanics change → **no Score Calibration re-baseline obligation**.

**Locked names.** URL paths `GET /leagues/<int:league_id>/` + `GET /seasons/<int:season_id>/`; URL names `league_dashboard` + `season_dashboard` (bare, no `app_name`); views `matches.views.league_dashboard` + `matches.views.season_dashboard`; shared body-context helper `matches.views._build_dashboard_context`; pure module `matches/season_dashboard.py`; dataclass `season_dashboard.LeaderRow(player_id, player_name, role, team_id, team_name, value, games_played, rank)` (8 fields in pinned order); functions `season_dashboard.compute_leaders` + `season_dashboard.find_next_fixture` + `season_dashboard.round_progress`; stat vocabulary literals `"points_per_game"` + `"tags_per_game"` + `"tag_ratio"`; season-mode literals `"draft"` + `"active"` + `"completed"` + `"none"` (`"none"` is league-only); action-button-state literals `"start_season"` + `"play_next"` + `"start_next_season"` + `"none"`; sidebar key literals `"overview"` + `"standings"` + `"schedule"` + `"teams"` + `"history"`; player-round seam dict 7 keys `player_id, player_name, role, team_id, team_name, tags_made, times_tagged, points_scored`; `next_fixture` seam dict 7 keys `matchday, round_number, team_a_id, team_a_name, team_b_id, team_b_name, date`; templates `templates/leagues/dashboard.html` (block title `{{ league.name }} — League`) + `templates/seasons/dashboard.html` (block title `{{ season.league.name }} — {{ season.name }}`); league DOM ids `league-dashboard-header` / `league-dashboard-state-badge` / `league-dashboard-action-button` / `league-dashboard-standings-snippet` / `league-dashboard-next-round` / `league-dashboard-round-count` / `league-dashboard-leaders-points` / `league-dashboard-leaders-tags` / `league-dashboard-leaders-ratio` / `league-dashboard-no-season-notice`; season DOM ids `season-dashboard-header` / `season-dashboard-state-badge` / `season-dashboard-action-button` / `season-dashboard-sidebar` / `season-dashboard-sidebar-standings` / `season-dashboard-sidebar-schedule` / `season-dashboard-sidebar-teams` / `season-dashboard-sidebar-history` / `season-dashboard-standings-snippet` / `season-dashboard-next-round` / `season-dashboard-round-count` / `season-dashboard-leaders-points` / `season-dashboard-leaders-tags` / `season-dashboard-leaders-ratio`; raw href patterns `/players/{{ row.player_id }}/career-stats/` (per-leader anchor) + `/leagues/{{ league.id }}/leaders/` (league "View all leaders" anchor) + `/seasons/{{ season.id }}/leaders/` (season "View all leaders" anchor); test files `matches/tests/test_season_dashboard_logic.py` (pure-unit) + `matches/tests/test_league_dashboard.py` (Django `TestCase`) + `matches/tests/test_season_dashboard_view.py` (Django `TestCase`) with test classes `TestComputeLeadersEmpty` / `TestComputeLeadersSinglePlayer` / `TestComputeLeadersTiebreak` / `TestComputeLeadersDeterministic` / `TestComputeLeadersRoleMix` / `TestComputeLeadersStatVocabulary` / `TestComputeLeadersLimit` / `TestComputeLeadersDefensiveLastWins` / `TestFindNextFixture` / `TestRoundProgress` / `TestNoDjangoImportsLeaked` (pure-unit), `TestLeagueDashboardRouting` / `TestLeagueDashboardSeasonPick` / `TestLeagueDashboardDraftBranch` / `TestLeagueDashboardActiveBranch` / `TestLeagueDashboardCompletedBranch` / `TestLeagueDashboardNoneBranch` (league view), `TestSeasonDashboardRouting` / `TestSeasonDashboardStateMatrix` / `TestSeasonDashboardSidebar` / `TestSeasonDashboardBody` (season view). Seam contract: [`.claude/worktrees/lg-01c-seam-contract.md`](../../.claude/worktrees/lg-01c-seam-contract.md).

**Superseded by LG-01f** ([ADR-0017](../../docs/adr/0017-league-context-nav-shape.md)). The LG-01c-locked 5-entry per-season `season-dashboard-sidebar` block (entries `overview / standings / schedule / teams / history`) is replaced wholesale by LG-01f's 14-entry zengm-shaped sidebar partial `templates/_partials/league_sidebar.html` (1 top + 6 LEAGUE + 4 TEAM + 3 PLAYERS, see **LG-01f league history** below). The LG-01c `sidebar_links` 5-entry context shape is replaced by the 14-entry list built by `_build_league_sidebar_links`; the LG-01c `sidebar_active="overview"` literal is replaced with `sidebar_active=None` on the season dashboard (no entry in the 14-entry shape matches the season dashboard — tests assert zero active entries). The LG-01c DOM ids `season-dashboard-sidebar` / `season-dashboard-sidebar-standings` / `season-dashboard-sidebar-schedule` / `season-dashboard-sidebar-teams` / `season-dashboard-sidebar-history` are **deleted** (post-LG-01f tests assert they are ABSENT from the rendered HTML); the LG-01c module-level helper `_season_sidebar_links` in `matches/views.py` is **deleted wholesale**; the LG-01c test class `TestSeasonDashboardSidebar` in `matches/tests/test_season_dashboard_view.py` is **deleted wholesale** (its 5-entry assertions are obsolete under the 14-entry shape — see LG-01f's `TestLg01fSidebarRendered` for the replacement). Every other LG-01c surface — the dashboard action-button DOM ids, `data-action-state` attributes, leaders / standings snippet DOM ids, the per-leader raw-href patterns, the `_build_dashboard_context` 11-key body context, the pure module `matches/season_dashboard.py` and its 3 functions / `LeaderRow` dataclass — is preserved verbatim and continues to pass.

## LG-01d play season

Write-surface layer over the LG-01c dashboards — turns the previously-disabled `action_button_state="play_next"` / `"start_season"` placeholders into a Play dropdown driving five new endpoints. **Five new view functions** appended to `matches/views.py`: `start_season(request, season_id) -> HttpResponse` (POST sync, flips `draft → active` via `Season.start_season()`, idempotent on the LG-01 `Season.clean()` "already active" race via the substring `"non-completed"` match on `ValidationError` messages), `play_week(request, season_id) -> HttpResponse` (POST sync, inline `with transaction.atomic():` block wrapping every Round in the next unplayed matchday — whole-matchday atomic, rolls back the entire matchday on any inner exception), `play_two_months(request, season_id) -> JsonResponse` (POST async, enqueues `play_season_task.delay(season_id, max_matchdays=8)` and returns `JsonResponse({"job_id", "season_id"}, status=202)`), `play_until_end(request, season_id) -> JsonResponse` (POST async, identical except `max_matchdays=None`), `play_status(request, season_id, job_id) -> JsonResponse` (GET-only polling endpoint, no 404 path — an unknown `job_id` resolves to Celery `PENDING` ⇒ `"running"` via the API-03 expiry-asymmetry semantics). All five views use the locked `HttpResponseNotAllowed([...])` guard pattern as the first line of the body (mirrors `movement_heatmap` / `export_round_report` / LG-01c `season_dashboard`); no `@require_POST` / `@require_GET` decorator, no view-level `@transaction.atomic`. **One new flat `_`-prefixed helper** `matches.views._build_play_status_response(async_result, *, season_id) -> dict` (RV-01 / HX-03 / LG-01c `_build_dashboard_context` precedent) assembles the 5-key polling JSON; reuses the existing API-03 `_celery_state_to_job_status` helper **verbatim** — no rename, no fork.

**URLs**. Five new path entries in `matches/season_urls.py`, all inserted **BEFORE** the LG-01 `<int:season_id>/standings/` and `<int:season_id>/schedule/` entries (first-match resolution): `path("<int:season_id>/start-season/", views.start_season, name="start_season")`, `path("<int:season_id>/play-week/", views.play_week, name="play_week")`, `path("<int:season_id>/play-two-months/", views.play_two_months, name="play_two_months")`, `path("<int:season_id>/play-until-end/", views.play_until_end, name="play_until_end")`, `path("<int:season_id>/play-status/<str:job_id>/", views.play_status, name="play_status")`. All bare URL names (no `app_name`); final order is `[<int:season_id>/ (LG-01c dashboard), start-season/, play-week/, play-two-months/, play-until-end/, play-status/<str:job_id>/, standings/ (LG-01), schedule/ (LG-01)]`.

**Celery task** `matches/tasks.py::play_season_task` — `@shared_task(bind=True, name="matches.play_season")`, signature `(self, season_id: int, max_matchdays: int | None = None) -> dict`. Body steps (locked, in order): `import django.db` (API-03 `close_old_connections` precedent), deferred imports of `Season` / `BatchSimulator` / `generate_schedule` / `select_play_fixtures` / `Team` (mirrors the existing `_resolve_arena_map` deferred-import pattern keeping `matches/tasks.py`'s module-level surface lean), load `season = Season.objects.get(id=season_id)`, materialise `fixtures = generate_schedule(season.starting_team_ids_json or [], season.schedule_format)`, build the Side-agnostic `played_keys = {(frozenset({gr.match.team_red_id, gr.match.team_blue_id}), gr.round_number) for gr in GameRound.objects.filter(match__season=season).select_related("match")}`, call `to_play = select_play_fixtures(fixtures, played_keys, max_matchdays)`, then loop `for k, fixture in enumerate(to_play)` calling `BatchSimulator().simulate_scheduled_round(season, team_a, team_b, fixture.round_number)` (no `arena_map` kwarg — deferred to LG-01j) and emitting `self.update_state(state="PROGRESS", meta={"completed": k + 1, "total": n})` AFTER each Round (so `completed` is the count of Rounds already committed). Empty to-play list ⇒ returns `{"completed": 0, "total": 0}` immediately. `finally: django.db.close_old_connections()`. **No outer `@transaction.atomic` on the task body** — each Round's atomic commit is the existing `simulate_scheduled_round` decorator. A mid-loop exception propagates (Celery records `FAILURE`); every completed Round survives because it was its own atomic commit; the Season stays `active`; the user re-clicking Play resumes from where the last successful Round left off. **This is a load-bearing decision recorded in [ADR-0016](../../docs/adr/0016-play-season-job-execution-model.md).**

**Pure module extension** — `matches/season_dashboard.py` (the LG-01c file, **frozen import allowlist unchanged**: `dataclasses` / `typing` / `collections` only, no Django / no ORM / no `random` / no `datetime` / no I/O / no logging / no `matches.schedule_generator` import — `ScheduleFixture` instances cross the seam as duck-typed input). The defensive `TestNoDjangoImportsLeaked` subprocess check **must keep passing** after the two new appended functions. **`find_next_matchday(fixtures, played_keys) -> Optional[int]`** walks `fixtures` in canonical `generate_schedule(...)` iteration order and returns the `matchday` of the first fixture whose Side-agnostic `(frozenset({team_a_id, team_b_id}), round_number)` key is NOT in `played_keys`; empty / all-played ⇒ `None`. **`select_play_fixtures(fixtures, played_keys, max_matchdays) -> list`** walks `fixtures` once collecting unplayed fixtures whose `matchday` is among the first `max_matchdays` distinct unplayed matchdays seen (or **all** unplayed fixtures when `max_matchdays is None`); canonical iteration order preserved in the output. The algorithm uses a single sweep — once the distinct-matchday set reaches `max_matchdays`, the sweep stops accepting fixtures whose `matchday` is not already in the set.

**Polling JSON shape** (locked, exactly 5 keys in this order): `{"status": str, "completed": int, "total": int, "error": str | None, "season_id": int}`. **`status`** ∈ `"running" | "complete" | "error"` via the shared `_celery_state_to_job_status` helper (`PENDING` / `STARTED` / `PROGRESS` / `RETRY` / unknown ⇒ `"running"`; `SUCCESS` ⇒ `"complete"`; `FAILURE` / `REVOKED` ⇒ `"error"`). **`completed` / `total`** are Round-level counts (NOT matchday-level): read from `async_result.info["completed"]` / `["total"]` on `PROGRESS`, from `async_result.result["completed"]` / `["total"]` on `SUCCESS`, `0` / `0` otherwise. **`error`** = `str(async_result.info)` on `FAILURE` / `REVOKED`, `None` otherwise. **`season_id`** is echoed from the URL kwarg, which is **authoritative** over the `?season_id=` query param the JS client carries for stateless polling URLs. POST response shapes (async) carry only 2 keys: `{"job_id": str, "season_id": int}` returned with `status=202`; the two sync POSTs (`start_season`, `play_week`) return HTTP 302 redirects to `season_dashboard` on success and never return JSON.

**View context keys** — both LG-01c dashboards (`season_dashboard` + `league_dashboard`) gain two new context keys: `play_error: str | None` (populated on a sync POST failure re-render with `str(exc)` of the caught `ValidationError` / `ValueError`; `None` on the normal GET render) and `play_job_id: str | None` (always `None` at LG-01d — the async POSTs return 202 JSON and the JS handles polling in-page rather than redirecting with a `job_id` in context; reserved key for future extension, tests assert presence with value `None`).

**Templates** — `templates/seasons/dashboard.html` and `templates/leagues/dashboard.html` are **modified**: the LG-01c `<button disabled>` placeholder in the `{season,league}-dashboard-action-button` slot is replaced by branched markup keyed off `action_button_state`. `"start_season"` ⇒ a single-button Start Season `<form method="post">` (POSTs to `start_season`); `"play_next"` ⇒ a Bootstrap-style dropdown with three submit forms (One Week / Two Months / Until End of Season, POSTing to `play_week` / `play_two_months` / `play_until_end`); `"start_next_season"` and `"none"` keep the LG-01c `<button disabled>` placeholder. **14 new locked DOM ids — 7 per dashboard, symmetric across the Season and League surfaces**: (Season) `season-dashboard-play-dropdown` (always when an action button renders — outer `<div>` wrapping the form(s)), `season-dashboard-play-start-season` (only in `start_season` state — the Start Season `<form>`), `season-dashboard-play-one-week` / `-play-two-months` / `-play-until-end` (only in `play_next` state — the three submit forms), `season-dashboard-play-error` (only when `play_error` is truthy — element rendering `{{ play_error }}`), `season-dashboard-play-progress` (always, rendered but hidden by default; populated by JS during polling). (League) `league-dashboard-play-dropdown` / `-play-start-season` / `-play-one-week` / `-play-two-months` / `-play-until-end` / `-play-error` / `-play-progress` — identical structure, identical branch rules, mirrored exactly when `displayed_season` is active. The LG-01c-locked `{season,league}-dashboard-action-button` ids continue to be present on the outer wrapper in all 4 states for LG-01c test backwards-compatibility; the LG-01d ids stack underneath that id rather than replacing it.

**Inline polling JS** — both dashboard templates carry an inline `<script>` block (no external JS file, no htmx, no framework). The One Week form submits normally (sync — server-side redirect); the Two Months and Until End forms intercept submit via `addEventListener("submit", e => { e.preventDefault(); fetch(form.action, {method: "POST", body: new FormData(form), …}).then(r => r.json()).then(json => startPolling(json.job_id, json.season_id)); })`. Polling cycle is 1000 ms via `setInterval`, hitting `{% url 'play_status' season_id=season.id job_id='JOB' %}?season_id={{ season.id }}` with `'JOB'` substituted client-side. On `data.status === "complete"` ⇒ `clearInterval` + `window.location.reload()`. On `data.status === "error"` ⇒ render `data.error` into the `play-error` element and re-enable the dropdown. **The JS is duplicated across the two templates** (no Django `{% include %}` for the JS block) — keeping it inline and per-template is the LG-01d simplicity choice over factoring out a shared partial.

**Determinism / scope.** LG-01d is pure orchestration over the existing `simulate_scheduled_round` per-Round entry point — per-Round RNG consumption is byte-for-byte unchanged. **No simulation mechanics change → no Score Calibration re-baseline.** Concurrency at the DB layer is handled by the existing Side-agnostic Match find-or-create in `simulate_scheduled_round`; the task body does not need additional locking. The 5-key polling JSON shape, the 3-value status vocabulary, and the per-Round atomic commits decision are all locked at the seam contract.

**Scope-out (locked).** **No model change, no migration** (`matches/models.py` / `matches/migrations/` read-only at LG-01d). **No `django.contrib.messages` usage** — sync errors flow through the `play_error` context key. **No new dependency** (no JS framework, no htmx, no Alpine, no new `requirements.txt` entry). **No `master_seed` UI exposure** — the Celery task signature does not accept `master_seed`; LG-01d does not ship a determinism-pin surface. **No mid-job cancel UI** — no `AsyncResult.revoke` call, no "Cancel run" button, no cooperative-cancel polling inside the task body. **No top-nav refactor / sidebar / URL nesting** — deferred to LG-01h. **No per-Season arena map options** — `simulate_scheduled_round` called with `arena_map=None` (3-zone fallback); deferred to LG-01j. **No "One Week (Live)" replay surface** — deferred to LG-01i, depends on CAR-01. **No tournament-aware "Until playoffs" relabel** — LG-01d ships the "Until end of season" label; LG-02 relabels once tournaments land. **No `simulate_match` change** (sandbox 2-Round-atomic entry point untouched). **No `simulate_scheduled_round` change** (LG-01 per-Round entry point consumed verbatim). **No edit to `matches/models.py` / `matches/standings.py` / `matches/schedule_generator.py` / `matches/simulation/`** (all LG-01 / LG-01a / LG-01b / LG-01c modules read-only). **No edit to `LeagueAdmin` / `SeasonAdmin`**. **No API / DRF endpoint** — `/api/seasons/<id>/play-*/` REST surfaces deferred. **No JS file added to `static/`**; the polling JS is inline per template. **No edit to `CONTEXT.md` beyond the two pinned edits** (extend **Job**, add **Matchday**). **No new ADR beyond `0016-play-season-job-execution-model.md`.** **No `Season.matchday_cadence_days` field** (7-day cadence stays hardcoded in the LG-01 `season_schedule` view + the LG-01c `next_fixture.date` derivation). **No edit to the LG-01c pure module's 3 existing functions** (`compute_leaders` / `find_next_fixture` / `round_progress`); only `find_next_matchday` and `select_play_fixtures` are appended.

**Tests** live in **3 files** (2 NEW + 1 EXTENDED) with **11 new test classes** under `matches/tests/`: `test_league_play.py` (NEW, `SimpleTestCase` pure-unit on the same frozen import allowlist as LG-01c's `test_season_dashboard_logic.py` — locally-stubbed `@dataclass(frozen=True)` fixture shape, no `matches.schedule_generator` import; classes `TestFindNextMatchday` + `TestSelectPlayFixtures`); `test_league_play.py` (NEW, Django `TestCase` exercising `play_season_task` under `CELERY_TASK_ALWAYS_EAGER=True` via the existing API-03 `LF_CELERY_EAGER=1` conftest; classes `TestPlaySeasonTaskHappyPath` + `TestPlaySeasonTaskMaxMatchdays` + `TestPlaySeasonTaskPerRoundCommit` + `TestPlaySeasonTaskTeamLookup`); `views_tests.py` (EXTENDED — 5 new test classes appended, no existing class modified; classes `TestLg01dStartSeason` + `TestLg01dPlayWeek` + `TestLg01dPlayTwoMonths` + `TestLg01dPlayUntilEnd` + `TestLg01dPlayStatus`). Tests use small-N seeded simulations (N=2 / N=3) per the project rule; happy-path EAGER tests do NOT `mock.patch("matches.tasks.play_season_task.delay")` so signature drift between view and task surfaces as a test failure.

**Locked names.** URL paths `POST /seasons/<int:season_id>/start-season/` + `POST /seasons/<int:season_id>/play-week/` + `POST /seasons/<int:season_id>/play-two-months/` + `POST /seasons/<int:season_id>/play-until-end/` + `GET /seasons/<int:season_id>/play-status/<str:job_id>/`; URL names `start_season` + `play_week` + `play_two_months` + `play_until_end` + `play_status` (bare, no `app_name`); views `matches.views.start_season` + `matches.views.play_week` + `matches.views.play_two_months` + `matches.views.play_until_end` + `matches.views.play_status`; helper `matches.views._build_play_status_response` (flat `_`-prefixed module-level, `(async_result, *, season_id) -> dict`); reused helper `matches.views._celery_state_to_job_status` (API-03, verbatim); Celery task `matches.tasks.play_season_task` (`@shared_task(bind=True, name="matches.play_season")`, `(self, season_id, max_matchdays=None) -> dict`); Celery broker name `"matches.play_season"`; pure functions `matches.season_dashboard.find_next_matchday` + `matches.season_dashboard.select_play_fixtures`; `max_matchdays` literals `1` (One Week) / `8` (Two Months) / `None` (Until End); polling JSON 5 keys `status, completed, total, error, season_id`; polling status vocabulary `"running"` / `"complete"` / `"error"`; POST async response 2 keys `job_id, season_id`; `played_keys` shape `set[tuple[frozenset[int], int]]` (Side-agnostic); context keys `play_error` + `play_job_id` (both `str | None`, both added to Season + League dashboard contexts); templates `templates/seasons/dashboard.html` + `templates/leagues/dashboard.html` (both MODIFIED — action-button slot now branched markup); Season DOM ids `season-dashboard-play-dropdown` / `season-dashboard-play-start-season` / `season-dashboard-play-one-week` / `season-dashboard-play-two-months` / `season-dashboard-play-until-end` / `season-dashboard-play-error` / `season-dashboard-play-progress`; League DOM ids `league-dashboard-play-dropdown` / `league-dashboard-play-start-season` / `league-dashboard-play-one-week` / `league-dashboard-play-two-months` / `league-dashboard-play-until-end` / `league-dashboard-play-error` / `league-dashboard-play-progress`; ADR file `docs/adr/0016-play-season-job-execution-model.md`; CONTEXT.md edits — **Job** entry extended ("Three kinds today: Batch run job, Save-games job, Play Season job …") + **Matchday** term added to `### League and seasons`; idempotency token `"non-completed"` (substring matched in `ValidationError` messages to detect the LG-01 `Season.clean()` "already active" double-submit race); `arena_map` policy — `simulate_scheduled_round` called without the kwarg (default `None`, 3-zone fallback), deferred to LG-01j; test files `matches/tests/test_league_play.py` (NEW) + `matches/tests/test_league_play.py` (NEW) + `matches/tests/views_tests.py` (EXTENDED) with classes `TestFindNextMatchday` / `TestSelectPlayFixtures` (pure-unit), `TestPlaySeasonTaskHappyPath` / `TestPlaySeasonTaskMaxMatchdays` / `TestPlaySeasonTaskPerRoundCommit` / `TestPlaySeasonTaskTeamLookup` (Celery EAGER), `TestLg01dStartSeason` / `TestLg01dPlayWeek` / `TestLg01dPlayTwoMonths` / `TestLg01dPlayUntilEnd` / `TestLg01dPlayStatus` (view extensions). Seam contract: [`.claude/worktrees/lg-01d-seam-contract.md`](../../.claude/worktrees/lg-01d-seam-contract.md).

## LG-01e start next season

Write-surface POST endpoint that fills the previously-disabled LG-01c-locked `action_button_state="start_next_season"` placeholder slot on both dashboards — `POST /leagues/<int:league_id>/next-season/` creates a fresh draft `Season` inside the same `League` with copied teams + auto-generated name + Jan-1-next-year `start_date`, then redirects to the new Season's LG-01c dashboard. The LG-01c `<button disabled data-action-state="start_next_season">` placeholder becomes a real `<form>` on both `templates/leagues/dashboard.html` and `templates/seasons/dashboard.html`; the LG-01c-locked `{league,season}-dashboard-action-button` outer-wrapper `<span>` ids continue to wrap the new form in all 4 `action_button_state` branches (LG-01c-test backwards compatibility, mirrors LG-01d's stacking pattern verbatim). **No model change, no migration, no ADR, no CONTEXT.md edit, no new pure module, no simulator touch, no JS, no `django.contrib.messages` usage, no async / Celery, no admin change.**

**URL.** A single new path entry in `matches/league_urls.py` — `path("<int:league_id>/next-season/", views.next_season, name="next_season")` — inserted **AFTER** the LG-01c `path("<int:league_id>/", views.league_dashboard, name="league_dashboard")` line and **BEFORE** the LG-01a `path("", views.league_list, name="league_list")` line; the LG-01b `path("create/", …)` literal stays at the top. Final `urlpatterns` order is `[create/, <int:league_id>/, <int:league_id>/next-season/, ""]`. URL name `next_season` is bare (no `app_name`, mirrors LG-01a / LG-01b / LG-01c precedent — `league_list` / `league_create` / `league_dashboard`); reverse via `reverse("next_season", kwargs={"league_id": league.id})`. POST-only — `if request.method != "POST": return HttpResponseNotAllowed(["POST"])` as the **first** line of the view body (LG-01d `start_season` / `play_week` precedent; no `@require_POST` decorator).

**View.** `matches.views.next_season(request: HttpRequest, league_id: int) -> HttpResponse` is appended to `matches/views.py`, decorated `@transaction.atomic` (single decorator, no other middleware — mirrors LG-01b `league_create`). The body runs **4 guards in pinned order**: (1) **405 on non-POST** (`HttpResponseNotAllowed(["POST"])` — first line, before any ORM hit, LG-01d pattern); (2) **404 on missing League** via `league = get_object_or_404(League, pk=league_id)`; (3) **302 redirect** to `season_dashboard` of `league.active_season` when a non-completed Season already exists (active-Season guard — idempotent on the double-submit race; consults the LG-01 `League.active_season` `@property` directly, NOT a re-implemented `.exclude(state="completed")` query — the same property `league_dashboard` reads; the redirect lands the user on the in-progress Season's LG-01c dashboard so they can act from there); (4) **400 `HttpResponseBadRequest("No completed Season in this League.")`** when `latest_completed = league.seasons.filter(state="completed").order_by("-id").first()` returns `None` — defensive guard that should never fire from the LG-01c UI (the `action_button_state="start_next_season"` branch only renders when `displayed_season.state == "completed"`, and the active-Season guard in step 3 catches the alternative paths), but pins clean-400 behaviour against a direct curl / replay POST so a `NoneType` `AttributeError` cannot crash the view. **Body step order (locked, no steps reordered)** after the guards: (a) compute `name = f"Season {league.seasons.count() + 1}"` — `.count()` evaluated BEFORE the create so the new Season takes the next sequential index (Season 1 already exists ⇒ count == 1 ⇒ new Season is `"Season 2"`); (b) `start_date = date(latest_completed.start_date.year + 1, 1, 1)` — calendar-year jump, Jan 1 of next year (NOT a `+ 365 days` calculation, NOT the PLAN.md original `7 * 2 * (N-1) days` formula which was ambiguous and superseded at grilling time); (c) `schedule_format = latest_completed.schedule_format` carried over verbatim (at LG-01e merge time the only valid value is `"single_round_robin"` per the LG-01 single-element `SCHEDULE_FORMATS` tuple, but the contract passes through whatever the previous Season had so future schedule formats inherit automatically); (d) `state = "draft"` explicit on `Season.objects.create(...)` (field-level default, kept explicit for clarity, LG-01b precedent); (e) **NOT set** on the new Season: `starting_team_ids_json` (snapshotted by `Season.start_season()` at activation time, NOT at create — LG-01 precedent) and `champion_team` (`None` by default, only stamped by `complete_if_finished`); (f) `new_season = Season.objects.create(league=league, name=name, start_date=start_date, schedule_format=schedule_format, state="draft")`; (g) **copy teams from the snapshot (NOT the live M2M)**: `team_ids = latest_completed.starting_team_ids_json or []` (defensive `or []` — mirrors the LG-01 schedule generator's `season.starting_team_ids_json or []` precedent), `teams_qs = Team.objects.filter(id__in=team_ids)`, `new_season.teams.add(*teams_qs)` (M2M bulk-add, no per-team `.save()`, LG-01b precedent). The **snapshot-as-source-of-truth rule** is load-bearing: copying from `starting_team_ids_json` rather than `latest_completed.teams.all()` is defence-in-depth that mirrors the LG-01 schedule generator's frozen-snapshot precedent; teams admin-added directly to the completed Season's live M2M post-completion do NOT leak into the next Season. Missing-team ids (e.g. an admin-deleted Team) are silently dropped by the `IN` clause — no explicit error, no log line, the new Season simply has fewer teams. (h) `return redirect("season_dashboard", season_id=new_season.id)` → HTTP 302 to the LG-01c new Season's dashboard, which renders in `season_mode == "draft"` because the new Season is `draft`. The `@transaction.atomic` decorator wraps the entire body — a failure in any step (e.g. `Season.objects.create` raises `IntegrityError`, or `new_season.teams.add(*teams_qs)` raises mid-flow) rolls back the new Season row + any M2M rows atomically; no half-created Season can exist on error. **No explicit savepoint** inside the body — the decorator-level atomic is sufficient. **No `play_error` population** — the LG-01d-added `play_error` context key on both dashboards is **NOT** populated by LG-01e: the active-Season guard is a redirect and the no-completed guard is a 400; neither re-renders a dashboard with `play_error` set. There is no LG-01e-specific error to display.

**Cross-app imports** — LG-01e introduces **zero truly new** top-of-file imports. Every name it needs is already at the top of `matches/views.py`: `from teams.models import Team, Player` (LG-01c `_build_dashboard_context` materialises standings via `Team.objects.in_bulk(...)`), `from datetime import date` (LG-01b uses `start_date`, LG-01 schedule view uses `timedelta`), `from django.db import transaction` (LG-01b), `from django.shortcuts import render, get_object_or_404, redirect` (LG-01b), `from django.http import HttpResponseNotAllowed` (LG-01c / LG-01d), `from .models import League, Season` (LG-01). `HttpResponseBadRequest` may or may not be present on the existing `from django.http import …` line — the Code agent's responsibility to defensively check existing imports and add only the names actually missing, NOT re-add a duplicate line. The defensive check + no-duplicate rule applies to every import in this list.

**Template wiring.** Both `templates/leagues/dashboard.html` and `templates/seasons/dashboard.html` are **MODIFIED** — the LG-01c-locked `{% else %}` branch (which currently renders the `<button disabled data-action-state="start_next_season">` placeholder for both the `"start_next_season"` and `"none"` states via fall-through) is split into TWO branches: `{% elif action_button_state == "start_next_season" %}` becomes a real `<form method="post" action="{% url 'next_season' league_id=… %}" class="d-inline">` containing `{% csrf_token %}` + a single `<button type="submit" data-action-state="{{ action_button_state }}">{{ action_button_label }}</button>` (which renders as `"Start Next Season"`); the `{% else %}` fall-through keeps the LG-01c `<button disabled data-action-state="{{ action_button_state }}">{{ action_button_label }}</button>` for the `"none"` state on the league dashboard (the season dashboard never reaches `"none"` per the LG-01c invariant, but the `{% else %}` is preserved for parity). **`league_id` derivation**: on the **league dashboard** the template has `league` in context so the URL is `{% url 'next_season' league_id=league.id %}`; on the **season dashboard** the template has `season` in context so the URL is `{% url 'next_season' league_id=season.league_id %}` — `season.league_id` (the `_id` accessor) avoids the JOIN that `season.league.id` would trigger; the value is identical. The LG-01c-locked `{league,season}-dashboard-action-button` outer-wrapper `<span>` ids continue to wrap the new form in ALL four `action_button_state` branches for LG-01c-test backwards compatibility (mirrors LG-01d's stacking pattern verbatim — the LG-01e form ids stack underneath that wrapper rather than replacing it); the LG-01c-locked `data-action-state="{{ action_button_state }}"` attribute is carried on the submit `<button type="submit">` inside the form (NOT on the outer wrapper `<span>`) so existing LG-01c tests that scan for `data-action-state="start_next_season"` continue to pass post-LG-01e. **2 NEW locked DOM ids**: `league-dashboard-next-season-form` (the `<form method="post">` element's `id` attribute on `templates/leagues/dashboard.html`, only when `action_button_state == "start_next_season"`) and `season-dashboard-next-season-form` (same on `templates/seasons/dashboard.html`, only when `action_button_state == "start_next_season"`). The `{% csrf_token %}` is mandatory inside the form (Django CSRF middleware, LG-01d precedent); the submit text is literally `"Start Next Season"` (rendered via the LG-01c-locked `action_button_label` context key, which `_build_dashboard_context` already sets to `"Start Next Season"` in the `season_mode == "completed"` branch). **NO inline JS, NO `<script>` block, NO `fetch()` interception** — the form submits synchronously via the browser's native form submission; the server returns a 302 redirect; the browser follows to the new Season's dashboard (unlike LG-01d's async Play Two Months / Until End forms). **NO extra `<div>` wrapper** — the form sits directly inside the LG-01c-locked outer-wrapper `<span>`. The exact Bootstrap CSS class names on the form / button are at Code agent's discretion — only the form id, the `action` URL, the submit text, the `{% csrf_token %}` presence, and the `data-action-state` attribute are pinned.

**Context keys** — **no new context keys**. LG-01c provides `action_button_label = "Start Next Season"` and `action_button_state = "start_next_season"` in the `season_mode == "completed"` branch (both consumed verbatim by the LG-01e template branch — label as submit text, state as the `data-action-state` attribute), plus `league` (league dashboard, read as `league.id`) / `season` (season dashboard, read as `season.league_id`) for the `{% url 'next_season' league_id=… %}` reverse. LG-01d provides `play_error: str | None` and `play_job_id: str | None` on both dashboards — LG-01e **reads neither and populates neither** (the LG-01e error paths redirect or return 400, neither re-renders a dashboard; LG-01e is sync, no Celery, no polling). The LG-01c `_build_dashboard_context` pure helper is NOT edited — its 11-key body context is consumed verbatim. The `matches/season_dashboard.py` pure module gains **zero new functions**.

**Scope-out (locked).** No model change (`matches/models.py` read-only at LG-01e). No migration (LG-01's `0029_league_season_match_fk.py` remains the final LG-01x migration). No ADR write ([ADR-0014](../../docs/adr/0014-league-season-foundation.md) + [ADR-0015](../../docs/adr/0015-schedule-on-demand-no-fixture-rows.md) + [ADR-0016](../../docs/adr/0016-play-season-job-execution-model.md) cover the foundation, the schedule surface, and the play job-execution model; LG-01e is a thin CRUD POST endpoint with nothing surprising-without-context, nothing hard-to-reverse, no real trade-off requiring a record). No CONTEXT.md edit (`League` / `Season` / `Standings` / `Matchday` / `Job` glossary entries already exist; "Start Next Season" is a UI label, not a domain term). No new pure module (LG-01e is pure CRUD; no aggregation worth factoring out). **No "Archive League" toggle UI** — deferred to admin-only access (`LeagueAdmin` already supports the `state="archived"` flip); no public-facing button at LG-01e, narrowing the original PLAN.md scope. **No edit-draft UI** — editing a `draft` Season's roster / start_date / name is admin-only at LG-01e merge time (`SeasonAdmin` already supports inline edits via `filter_horizontal=("teams",)` for the M2M and default ModelAdmin form fields for the scalars). **No `Season.state="archived"` value** — completed Seasons are already effectively read-only per the LG-01 invariants (idempotent `complete_if_finished`, M2M frozen by `starting_team_ids_json` snapshot); no state-machine extension at LG-01e. No edit to `matches/models.py` / `matches/simulation/` / `matches/standings.py` / `matches/schedule_generator.py` / `matches/season_dashboard.py` / `matches/tasks.py` (LG-01 / LG-01c / LG-01d pure modules and the LG-01 simulator consumed verbatim — no new function, no edit to any existing function). No edit to `LeagueAdmin` / `SeasonAdmin`. No edit to `templates/leagues/list.html` / `templates/leagues/create.html` / `templates/seasons/standings.html` / `templates/seasons/schedule.html` (LG-01 / LG-01a / LG-01b templates unchanged; only the two LG-01c-introduced dashboard templates are modified). No edit to `matches/forms.py` (LG-01e takes no form input — the POST carries only `csrfmiddlewaretoken`, every new Season field is derived server-side from `latest_completed`). **No simulator touch, no RNG consumption, no SIM-07 / SIM-08 contract interaction, no Score Calibration re-baseline.** No JS, no inline `<script>` block, no htmx, no Alpine, no Bootstrap-JS interaction. No new dependency. No API / DRF endpoint (`/api/leagues/<id>/next-season/` is deferred — LG-01e is UI-only). No `messages.success(...)` / `django.contrib.messages` flash (the 302 redirect to the new Season's dashboard IS the user feedback). No backfill — existing pre-LG-01e completed Seasons are not touched; the first user click on any post-LG-01e completed Season creates the new Season in-place. No top-nav refactor / sidebar / URL nesting (deferred to LG-01h alongside LG-01d). No re-baseline of LG-01c / LG-01d tests — the LG-01c `TestSeasonDashboardStateMatrix::test_action_button_state_data_attribute_per_state` test scans for `data-action-state="start_next_season"` on any element inside the dashboard (not specifically a `<button disabled>`), so it continues to pass post-LG-01e because the new `<form>` carries the same attribute on its submit button.

**Tests** live in **3 files** (1 NEW + 2 EXTENDED) with **9 + 1 + 1 new test classes** under `matches/tests/`: `test_league_next_season.py` (NEW, Django `TestCase`, classes `TestNextSeasonRouting` + `TestNextSeasonHappyPath` + `TestNextSeasonNameFormat` + `TestNextSeasonStartDate` + `TestNextSeasonScheduleFormatCarry` + `TestNextSeasonTeamsCopiedFromSnapshot` + `TestNextSeasonActiveSeasonGuard` + `TestNextSeasonNoCompletedGuard` + `TestNextSeasonAtomicity`); `test_league_dashboard.py` (EXTENDED — append a single new class `TestLg01eDashboardWiring` to the LG-01c test file, no existing class modified — asserts the league dashboard's completed branch renders the `<form id="league-dashboard-next-season-form">` with the correct action URL, csrf token, submit text, and `data-action-state="start_next_season"`, and that the draft / active / none branches DO NOT render the form id); `test_season_dashboard_view.py` (EXTENDED — append `TestLg01eDashboardWiring` to the LG-01c file — symmetric assertions on the season dashboard, with the action URL derived from `season.league_id` for JOIN-free reverse). Tests must NOT touch `simulate_scheduled_round` / `simulate_match` / `save_games` or any simulator entry point (LG-01e runs no simulation, a test that accidentally enters the simulator is a scope leak and locked out). Tests must NOT `mock.patch` the ORM beyond the single `Season.objects.create` patch in `TestNextSeasonAtomicity` (which forces the rollback path — the LG-01b transaction-rollback precedent); every other test exercises the real ORM path end-to-end so signature drift between LG-01e's call sites and the ORM surfaces as a test failure rather than a silent mock pass.

**Determinism / scope.** LG-01e is a thin CRUD POST — no writes to `_flush_to_db`, no simulation, no RNG draw, no `BatchSimulator` call. **No SIM-07 / SIM-08 contract interaction, no Score Calibration re-baseline obligation.** The active-Season guard is the only concurrency consideration (a double-submit race lands the second click on the in-progress Season's LG-01c dashboard, idempotent); the `@transaction.atomic` boundary rolls back the new Season + M2M rows atomically on any mid-flow exception.

**Locked names.** URL path `POST /leagues/<int:league_id>/next-season/` (inserted AFTER LG-01c `<int:league_id>/` and BEFORE LG-01a `""` in `matches/league_urls.py`, final order `[create/, <int:league_id>/, <int:league_id>/next-season/, ""]`); URL name `next_season` (bare, no `app_name`); URL file edit `matches/league_urls.py` (single-line insert); view `matches.views.next_season` (`(request: HttpRequest, league_id: int) -> HttpResponse`, decorated `@transaction.atomic`, POST-only via `HttpResponseNotAllowed(["POST"])` as the first line of the body); redirect target on success URL name `season_dashboard` (LG-01c — `reverse("season_dashboard", args=[new_season.id])` → HTTP 302); redirect target for the active-Season guard URL name `season_dashboard` (`reverse("season_dashboard", args=[league.active_season.id])` → HTTP 302); 400 response `HttpResponseBadRequest("No completed Season in this League.")` (exact body literal); 405 response `HttpResponseNotAllowed(["POST"])` (first line of view body, before any ORM hit); 404 response `get_object_or_404(League, pk=league_id)` (after the 405 guard); active-Season check `league.active_season` (the LG-01 `@property`, NOT a re-implemented `.exclude(state="completed")` query); latest-completed query `league.seasons.filter(state="completed").order_by("-id").first()` (reverse accessor `seasons` from LG-01 `related_name`); templates MODIFIED `templates/leagues/dashboard.html` + `templates/seasons/dashboard.html` (LG-01c `{% else %}` split into `{% elif action_button_state == "start_next_season" %}` + `{% else %}` — `"start_next_season"` gets a real `<form>`, `"none"` keeps the `<button disabled>`); NEW DOM ids `league-dashboard-next-season-form` + `season-dashboard-next-season-form` (the `<form method="post">` elements' `id` attributes, only when `action_button_state == "start_next_season"`); preserved LG-01c DOM ids `league-dashboard-action-button` + `season-dashboard-action-button` (outer-wrapper `<span>` continues to wrap the form in all 4 branches, LG-01c-test backwards compatibility); preserved LG-01c attribute `data-action-state="{{ action_button_state }}"` (carried on the submit `<button type="submit">` inside the form, NOT on the outer wrapper); locked literals `name = f"Season {league.seasons.count() + 1}"` (`.count()` evaluated BEFORE the create), `state = "draft"` (explicit on `Season.objects.create(...)`), `start_date = date(latest_completed.start_date.year + 1, 1, 1)` (calendar-year jump, Jan 1 of next year), `schedule_format = latest_completed.schedule_format` (carry over verbatim from previous Season), submit-button label `"Start Next Season"` (matches the LG-01c `action_button_label`); locked 400-body literal `"No completed Season in this League."`; snapshot read `latest_completed.starting_team_ids_json or []` (defensive `or []`, LG-01 schedule generator precedent); team resolution `Team.objects.filter(id__in=team_ids)` (single `IN` query, missing ids silently dropped); M2M populate `new_season.teams.add(*teams_qs)` (bulk-add, no per-team `.save()`, LG-01b precedent); cross-app imports `from teams.models import Team` + `from datetime import date` + `from django.db import transaction` + `from django.shortcuts import redirect, get_object_or_404` + `from django.http import HttpResponseNotAllowed, HttpResponseBadRequest` + `from .models import League, Season` (all already imported at top of `matches/views.py` per LG-01 / LG-01b / LG-01c / LG-01d, defensive check + no-duplicate rule applies — `HttpResponseBadRequest` may need adding to the existing `from django.http import …` line if not present); context keys READ `league` / `season` / `action_button_state` / `action_button_label` (LG-01c); context keys NOT used `play_error` / `play_job_id` (LG-01d-added, LG-01e does NOT populate either — errors redirect or 400, never re-render; LG-01e is sync, no Celery); pure module touched (none — `matches/season_dashboard.py` is NOT edited); new pure functions (none — LG-01e adds zero pure functions); test files `matches/tests/test_league_next_season.py` (NEW) + `matches/tests/test_league_dashboard.py` (EXTENDED, append `TestLg01eDashboardWiring` only) + `matches/tests/test_season_dashboard_view.py` (EXTENDED, append `TestLg01eDashboardWiring` only); test classes `TestNextSeasonRouting` / `TestNextSeasonHappyPath` / `TestNextSeasonNameFormat` / `TestNextSeasonStartDate` / `TestNextSeasonScheduleFormatCarry` / `TestNextSeasonTeamsCopiedFromSnapshot` / `TestNextSeasonActiveSeasonGuard` / `TestNextSeasonNoCompletedGuard` / `TestNextSeasonAtomicity` (NEW file) + `TestLg01eDashboardWiring` (league dashboard EXTENDED file) + `TestLg01eDashboardWiring` (season dashboard EXTENDED file). Seam contract: [`.claude/worktrees/lg-01e-seam-contract.md`](../../.claude/worktrees/lg-01e-seam-contract.md).

## LG-01f league history

Read-only paginated **League History** page at `GET /leagues/<int:league_id>/history/` plus a project-wide nav refactor: the page itself renders one row per Season in the League (newest first, the in-progress Season pinned above the body with an "In progress" badge and live standings), and the same merge adds a new 14-entry zengm-shaped sidebar partial wired on 5 pages plus a Bootstrap top-bar `League ▾` dropdown replacing the LG-01a `Leagues` link. The History link's per-user target is resolved by a new `core.context_processors.league_nav` context processor reading `request.session["last_league_id"]`, which every League-context view now writes near the top of its body. **No model change, no migration, no new pure module, no simulator touch, no JS framework, no Celery, no `messages.*`, no API endpoint, no new dependency, no admin change, no CONTEXT.md edit.** Design decisions live in [ADR-0017](../../docs/adr/0017-league-context-nav-shape.md).

**URL.** Single-line insert in `matches/league_urls.py` — `path("<int:league_id>/history/", views.league_history, name="league_history")` — inserted **AFTER** the LG-01e `<int:league_id>/next-season/` line and **BEFORE** the LG-01a `path("", views.league_list, …)` line; final `urlpatterns` order `[create/, <int:league_id>/, <int:league_id>/next-season/, <int:league_id>/history/, ""]`. URL name `league_history` is bare (no `app_name`, mirrors LG-01a / LG-01b / LG-01c / LG-01e precedent). GET-only via `if request.method != "GET": return HttpResponseNotAllowed(["GET"])` as the **first** line of the body (LG-01c / LG-01d / LG-01e pattern); no `@require_GET` decorator, no `@transaction.atomic` (read-only).

**View.** `matches.views.league_history(request: HttpRequest, league_id: int) -> HttpResponse` is undecorated. Body runs the locked **9-step sequence**: (1) 405 guard before any ORM hit; (2) `league = get_object_or_404(League, pk=league_id)`; (3) materialise `seasons_qs = league.seasons.select_related("champion_team").prefetch_related("matches", "teams").filter(state__in=["active", "draft", "completed"]).order_by("-id")` then `seasons = list(seasons_qs)`; (4) collect `team_ids` from `season.starting_team_ids_json` for completed Seasons and `s.teams.all()` for active/draft Seasons (the prefetch cache fires; no per-Season query) then `teams_by_id = Team.objects.in_bulk(team_ids)`; (5) `in_progress_season = next((s for s in seasons if s.state in {"active", "draft"}), None)` + `completed_seasons = [s for s in seasons if s.state == "completed"]` (LG-01 invariant: ≤ 1 non-completed Season per League); (6) paginate **completed_seasons only** via standard Django `Paginator(completed_seasons, per_page=_coerce_per_page(request.GET.get("per_page"), default=10))` then `page_obj = paginator.get_page(_coerce_page(request.GET.get("page"), default=1))`; (7) build `in_progress_row = _build_history_row(in_progress_season, teams_by_id, is_in_progress=True)` (or `None`) and `completed_rows = [_build_history_row(s, teams_by_id, is_in_progress=False) for s in page_obj.object_list]`; (8) `request.session["last_league_id"] = league.id` (as `int`, NOT string — so the `league_nav` processor can `reverse(...)` cleanly; fires AFTER the 404 guard so a stale 404 doesn't pin a deleted League id, BEFORE the template render); (9) `return render(request, "leagues/history.html", context)` with the locked **9 context keys** `league, in_progress_row, completed_rows, page_obj, paginator, per_page, per_page_options=(10, 25, 50, 100), sidebar_links, sidebar_active="history"`. **Total DB queries**: 3 for data — `League` 404, the Seasons + prefetch materialisation, and `Team.objects.in_bulk(...)`. **`compute_standings(...)` runs in-Python** on each Season's prefetched `season.matches.all()` filtered to `is_completed=True`; zero additional queries. **`sidebar_links` is built via `_build_league_sidebar_links(league, displayed_season, "history")`** where `displayed_season = league.active_season or league.seasons.filter(state="completed").order_by("-id").first()` (same chain as LG-01c so the sidebar's Standings / Schedule links target the same Season the dashboards would).

**Four new module-level `_`-prefixed flat helpers** in `matches/views.py` (RV-01 / HX-03 / LG-01c / LG-01d / LG-01e precedent): `_build_history_row(season: Season, teams_by_id: dict[int, Team], *, is_in_progress: bool) -> dict` (returns the frozen 11-key row dict; keyword-only `is_in_progress` prevents positional inversion; zero DB hits — consumes the prefetch cache + the `teams_by_id` lookup); `_build_league_sidebar_links(league: League, displayed_season: Season | None, sidebar_active: str | None) -> list[dict]` (returns exactly 14 dicts in pinned section order with the 6-key shape `key, label, section, url, disabled, active`); `_coerce_per_page(raw: str | None, default: int = 10) -> int` (whitelist `(10, 25, 50, 100)`, invalid ⇒ `default`); `_coerce_page(raw: str | None, default: int = 1) -> int` (positive-int string semantics; Django `Paginator.get_page` further clamps too-large pages to last page silently). **The LG-01c `_season_sidebar_links` helper is DELETED wholesale** (5-entry sidebar replaced by the 14-entry one).

**Row-dict shape (frozen, 11 keys):** `season_id: int, season_name: str, season_url: str, start_date: datetime.date, teams_enrolled: int, matches_played: int, champion: Team | None, runner_up: Team | None, tournament_champion: None, top_three: list[Team | None], is_in_progress: bool`. `season_url = reverse("season_dashboard", args=[season.id])`; `teams_enrolled = len(season.starting_team_ids_json or [])` for completed, `season.teams.count()` via the prefetch when `starting_team_ids_json is None` on a draft Season; `matches_played = len([m for m in season.matches.all() if m.is_completed])` over the prefetch (NOT a `.filter(is_completed=True).count()` query); `champion` is `season.champion_team` with a `teams_by_id.get(standings[0].team_id)` fallback when `champion_team is None` (defensive — the LG-01 invariant fills it at completion, but pre-LG-01 data drift is possible); `runner_up = teams_by_id.get(standings[1].team_id)` when `len(standings) >= 2` else `None`; `tournament_champion` is **always `None`** at LG-01f (LG-02 reservation — the key is reserved now so the template doesn't change at LG-02 land time); `top_three` is exactly length 3 (`None` padding when fewer than 3 teams have played, rendered as `"—"`); `is_in_progress` carries through to the template branch. Cells 6/8/9/10 (Runner-Up + top-3) render live standings for the in-progress row too — informative even when no champion has been crowned.

**Per-row 10-column order** (left to right, locked): Season name (live link to `{% url 'season_dashboard' season.id %}`); Start date (`{{ season.start_date|date:"Y-m-d" }}`); # teams enrolled; Total Matches played; Champion (team name for completed, **literal `"In progress"`** badge for the in-progress row inside an element whose CSS class contains the substring `"in-progress"`); Runner-Up (else `"—"`); Tournament Champion (literal `"—"` em-dash U+2014 placeholder; LG-02 fills later); 1st / 2nd / 3rd place (standings ranks 1–3, `"—"` fallback). `compute_standings` is the LG-01 pure module `matches.standings.compute_standings` — consumed verbatim; helper reads `standings[i].team_id` (`StandingsRow` dataclass attribute) with a `getattr(row, "team_id", row["team_id"])` adapter for forward-compat. `teams_by_id.get(...)` (NOT `[]`) so a stale team-id from an admin-deletion does not crash the template.

**14-entry sidebar list** (locked pinned order, single `top` entry then 6 LEAGUE then 4 TEAM then 3 PLAYERS): index 0 `(top, dashboard, "Dashboard", LIVE — `reverse("league_dashboard", args=[league.id])`)`; indexes 1–6 LEAGUE — `(league, standings, "Standings", LIVE conditional on `displayed_season is not None` → `reverse("season_standings", args=[displayed_season.id])` else `None` disabled)`, `(league, schedule, "Schedule", LIVE conditional same fallback rule → `reverse("season_schedule", args=[displayed_season.id])`)`, `(league, playoffs, "Playoffs", disabled)`, `(league, finances, "Finances", disabled)`, `(league, history, "History", LIVE — `reverse("league_history", args=[league.id])`)`, `(league, power_rankings, "Power Rankings", disabled)`; indexes 7–10 TEAM — `(team, roster, "Roster", disabled)`, `(team, schedule_team, "Schedule", disabled)`, `(team, finances_team, "Finances", disabled)`, `(team, history_team, "History", disabled)`; indexes 11–13 PLAYERS — `(players, free_agents, "Free Agents", disabled)`, `(players, trade, "Trade", disabled)`, `(players, trading_block, "Trading Block", disabled)`. The TEAM section's Schedule entry uses `key="schedule_team"` (NOT `"schedule"`) and the TEAM History uses `key="history_team"` — the `_team` suffix on the keys disambiguates from their LEAGUE-section counterparts (the labels collide; the keys must not). **Schedule lives in LEAGUE as a 6th entry** (2026-05-27 resolution, [ADR-0017](../../docs/adr/0017-league-context-nav-shape.md)) diverging from zengm's TEAM-section Schedule — rationale: in this project the schedule is league-level (full per-Season fixture list) not per-team. **Per-entry 6-key dict shape**: `{key: str, label: str, section: str, url: str | None, disabled: bool, active: bool}` — `disabled = (url is None)` (derived, not an explicit argument), `active = (entry["key"] == sidebar_active)` (string equality; `sidebar_active=None` ⇒ every entry `active=False`).

**Active-key mapping** (which page sets `sidebar_active` to what): League dashboard (`league_dashboard`, LG-01c) ⇒ `"dashboard"`; League history (`league_history`, NEW) ⇒ `"history"`; Season dashboard (`season_dashboard`, LG-01c) ⇒ `None` (the LG-01c "Overview" entry is gone — no entry in the 14-entry shape matches the season dashboard, so the sidebar renders with zero active entries; tests assert this); Season standings (`season_standings`, LG-01) ⇒ `"standings"` (matches LEAGUE > Standings); Season schedule (`season_schedule`, LG-01) ⇒ `"schedule"` (matches LEAGUE > Schedule — exactly one active entry on the Schedule page).

**Templates.** **One NEW partial** `templates/_partials/league_sidebar.html` — no `{% extends %}`; outer `<nav id="league-sidebar">`; iterates `sidebar_links` grouping by `entry["section"]` in pinned order `[top, league, team, players]` with `<h6>` section-header labels `"LEAGUE" / "TEAM" / "PLAYERS"` (the `"top"` section has no header — Dashboard sits directly above the LEAGUE header); per-entry rendering — `entry["disabled"]` ⇒ `<span id="sidebar-{section}-{key}" class="...disabled...">{{ entry.label }}</span>` (NO `<a href>`), else `<a id="sidebar-{section}-{key}" href="{{ entry.url }}" class="...">{{ entry.label }}</a>`; if `entry["active"]` the `class` substring contains `"active"`. **One NEW page template** `templates/leagues/history.html` extending `base.html`, `{% block title %}{{ league.name }} — History{% endblock %}` (em-dash U+2014, locked exact format); structure `<div class="d-flex">{% include "_partials/league_sidebar.html" %}<main>...</main></div>`; the empty notice replaces the table when both `in_progress_row` and `completed_rows` are empty; the only inline JS allowed is the per-page-selector `onchange="this.form.submit()"` (LG-00c precedent — Code agent may also use a visible submit button instead). **Five MODIFIED templates** — `templates/base.html` (LG-01a `<a id="leagues-nav-link" href="…">Leagues</a>` replaced by a Bootstrap `<li class="nav-item dropdown">` carrying **toggle text `"League ▾"`** with caret U+25BE and preserving the LG-01a-locked DOM id `leagues-nav-link` on the toggle `<a class="nav-link dropdown-toggle">` — clicking still navigates to `/leagues/` via the `href` AND opens the dropdown via `data-bs-toggle="dropdown"`; both behaviours coexist; the dropdown menu carries **5 items in locked order top to bottom**: Standings (disabled `<span class="dropdown-item disabled">`), Playoffs (disabled), Finances (disabled), History (LIVE `<a class="dropdown-item" id="league-history-topbar-link" href="{{ top_bar_history_url }}">History</a>`), Power Rankings (disabled); no inline JS — Bootstrap 5's built-in dropdown component is the only dep), `templates/leagues/dashboard.html` (insert sidebar partial; `sidebar_active="dashboard"` added to context by `league_dashboard`), `templates/seasons/dashboard.html` (insert sidebar partial; `sidebar_active=None` added to context by `season_dashboard`; **the LG-01c-locked 5-entry `season-dashboard-sidebar*` markup block is REMOVED** wholesale), `templates/seasons/standings.html` (insert sidebar partial; `sidebar_active="standings"` added to context by `season_standings`; flex-container restructure around sidebar + main content), `templates/seasons/schedule.html` (insert sidebar partial; `sidebar_active="schedule"` added to context by `season_schedule`).

**Context processor (NEW).** `core.context_processors.league_nav(request: HttpRequest) -> dict[str, str]` in NEW file `core/context_processors.py`, registered in `settings.TEMPLATES[0]["OPTIONS"]["context_processors"]` (after the Django built-ins, alphabetical at Code agent's discretion). Returns the single-key dict `{"top_bar_history_url": <resolved URL string>}` via the locked 3-step resolution chain: (1) if `request.session["last_league_id"]` is present **AND** `League.objects.filter(pk=lid).exists()` ⇒ return `reverse("league_history", kwargs={"league_id": lid})`; (2) else if **exactly one** League exists in the DB ⇒ return `reverse("league_history", kwargs={"league_id": <that id>})` — probed via `League.objects.values_list("id", flat=True)[:2]` so the count query is bounded (we only need "0 or 1 or 2+"); (3) else (zero or 2+, no session pin) ⇒ return `reverse("league_list")`. The session-id `exists()` probe defends against stale pins (admin deletion of a League whose id is still in some user's session must not crash the reverse). Read via `getattr(request, "session", {})` so a `RequestFactory()`-built request without `.session` doesn't crash. At most 2 lightweight queries per request — no caching (deferred; if profiling later flags the cost, set `request.session["resolved_history_url"]` alongside `last_league_id`).

**Session-write site.** Every League-context view writes `request.session["last_league_id"] = <league_id>` (as `int`, NOT string — so the processor can `reverse(...)` cleanly) **AFTER** the 405 / 404 guards (so a stale 404 doesn't pin a deleted League id) and **BEFORE** the final template render / redirect. The locked write sites: `league_dashboard` (LG-01c, `league.id`, after 404 + before `_build_dashboard_context`); `league_history` (LG-01f, `league.id`, after 404 + before render); `season_dashboard` (LG-01c, `season.league_id`, after 404 + before `_build_dashboard_context`); `season_standings` (LG-01, `season.league_id`, after 404 + before render); `season_schedule` (LG-01, `season.league_id`, after 404 + before render); `next_season` (LG-01e, `league.id`, **BEFORE the redirect return statement** so the session cookie is set before the response is built — Django's session middleware commits on response, not inside the atomic block); `start_season` / `play_week` / `play_two_months` / `play_until_end` / `play_status` (LG-01d, each `season.league_id`, after 404; `play_status` is a polling endpoint so the write fires on every poll, keeping `last_league_id` fresh as the user watches a job). The view doesn't need to validate the session id on the write side — the context processor's `exists()` probe is the defensive layer.

**Locked DOM ids** (the page surface). Sidebar (16 ids): `league-sidebar` (outer `<nav>`), `sidebar-top-dashboard`, `sidebar-league-standings`, `sidebar-league-schedule`, `sidebar-league-playoffs`, `sidebar-league-finances`, `sidebar-league-history`, `sidebar-league-power_rankings`, `sidebar-team-roster`, `sidebar-team-schedule_team` (note `_team` suffix on `key`), `sidebar-team-finances_team`, `sidebar-team-history_team`, `sidebar-players-free_agents`, `sidebar-players-trade`, `sidebar-players-trading_block`. History page body: `league-history-table` (only when ≥ 1 row), `league-history-empty-notice` (only when zero Seasons; substring `"No Seasons yet"`), `league-history-in-progress-row` (only when an active/draft Season exists), `league-history-row-{season_id}` (one per completed Season on the current page), `league-history-pagination` (only when `paginator.num_pages > 1`), `league-history-per-page-form`, `league-history-per-page-select`. Top-bar dropdown: `leagues-nav-link` (preserved from LG-01a, now on the dropdown toggle `<a class="nav-link dropdown-toggle">`) + `league-history-topbar-link` (the History `<a class="dropdown-item">`). **Locked CSS-class substrings**: `"active"` (active sidebar entry), `"disabled"` (disabled entries), `"in-progress"` (in-progress badge in the Champion cell), `"in-progress-row"` (the `<tr>` row-level styling hook). **Locked literals**: `"In progress"` (Champion cell), `"—"` (em-dash U+2014, Tournament Champion + empty top-3 ranks), `"No Seasons yet"` (empty notice), `"LEAGUE" / "TEAM" / "PLAYERS"` (section headers), `"League ▾"` (toggle text, U+25BE caret), per-page whitelist `(10, 25, 50, 100)`, query params `?per_page=` + `?page=`.

**Pagination.** Standard Django `django.core.paginator.Paginator` over `completed_seasons` (the in-progress row is **NOT** in `completed_seasons`, so it appears on every page — with `per_page=10` page 1 = 1 in-progress row + 10 completed rows = 11 `<tr>` in `<tbody>`; page 2 = 1 in-progress + up to 10 completed). `_coerce_per_page` whitelists `(10, 25, 50, 100)`; invalid (`?per_page=foo`, `?per_page=999`, `?per_page=-5`, `?per_page=0`) ⇒ default 10. `_coerce_page` is positive-int string semantics; invalid (`?page=foo`, `?page=-1`, `?page=0`) ⇒ default 1; `paginator.get_page(...)` further clamps too-large pages to the last page silently. Per-page selector at `<form id="league-history-per-page-form" method="get">` wrapping `<select id="league-history-per-page-select" name="per_page">` with the four options; pagination `<nav id="league-history-pagination">` rendered **ONLY** when `paginator.num_pages > 1` (empty / single-page omits the `<nav>`). Pagination links carry the current `per_page` in their query string so the user's choice persists across page navigation (Code agent picks the link-assembly style — Django `{% querystring %}` tag, manual concat, or a `pagination_querystring: str` 10th context key).

**Empty state.** When `len(seasons) == 0` (no completed AND no in-progress Season): render `<div id="league-history-empty-notice">` containing the substring `"No Seasons yet"` (locked exact substring, no em-dash variant); omit the `<table>` body entirely; the sidebar partial + top-bar dropdown still render. `paginator.num_pages == 0` so the pagination `<nav>` is also omitted.

**In-progress row variants.** `<tr id="league-history-in-progress-row" class="in-progress-row ...">` (locked CSS-class substring `"in-progress-row"`); Champion cell renders literal `"In progress"` inside an element whose CSS class contains substring `"in-progress"`; cells 6/8/9/10 render live standings from `compute_standings(...)` over completed Matches so far (may be `[]`, in which case all four cells render `"—"`). Pinned at the top of the table — rendered ABOVE the `<tbody>` for the completed rows, NOT interleaved. Repeats on every paginated page (the in-progress row is informative even when no champion has been crowned yet — Runner-Up + top-3 surface the current standings).

**Determinism / scope.** Read-only — no writes, no RNG, no simulation, no `_flush_to_db` touch, **no SIM-07 / SIM-08 contract interaction, no Score Calibration re-baseline obligation**. The view body issues exactly 3 SQL queries for the data; `compute_standings` runs in-Python on the prefetched matches list. The 14 `sidebar_active` literals + `None` are implementation enums, not domain language. Sidebar / topnav / dropdown / session-pin terminology is implementation language — **CONTEXT.md is NOT edited** ([ADR-0017](../../docs/adr/0017-league-context-nav-shape.md) §10; the `League` / `Season` / `Standings` / `Matchday` / `Job` glossary entries already exist from LG-01 / LG-01d). LG-01f surfaces a partial skeleton of the LG-01h mode-based base.html restructure (4 LIVE sidebar entries + 1 LIVE top-bar item out of 14 + 5) but does **NOT** implement mode-switching (LG-01h scope).

**Scope-out (locked).** No model change (`matches/models.py` / `teams/models.py` / `core/models.py` read-only at LG-01f). No migration (LG-01e's `0029_*` remains the final LG-01x migration). No new pure module (LG-01f is thin view-glue plus one context processor; no aggregation worth factoring out — `matches/season_dashboard.py` gains zero new functions AND is NOT consumed; `matches/standings.py` consumed verbatim; `matches/schedule_generator.py` NOT consumed). No simulator touch / no RNG / no `BatchSimulator` call. No JS framework / htmx / Alpine / Stimulus — only Bootstrap 5's built-in dropdown JS (already a project dep) plus the optional `onchange="this.form.submit()"` per-page selector inline JS (LG-00c precedent); no `<script>` blocks anywhere new. No API / DRF endpoint (`/api/leagues/<id>/history/` deferred — LG-01f is UI-only). No `django.contrib.messages` flash. No new dependency (no `pip install`, no `requirements.txt` edit, no npm install). No admin change (`LeagueAdmin` / `SeasonAdmin` / `TeamAdmin` unchanged). No CONTEXT.md edit. No edit to `matches/standings.py` / `matches/schedule_generator.py` / `matches/season_dashboard.py` / `matches/tasks.py`. No edit to `templates/leagues/list.html` / `templates/leagues/create.html`. No edit to LG-01d play-related templates beyond what the modified-template list covers. No "Archive League" toggle UI (deferred, admin-only). No "Edit Draft Season" UI (deferred, admin-only via `SeasonAdmin.filter_horizontal=("teams",)`). No `Season.state="archived"` value (completed Seasons already effectively read-only per LG-01). No expansion of the `sidebar_active` enum beyond the 14 locked literals + `None` (LG-02+ flips disabled entries to live as features ship; e.g. `"playoffs"` becomes live when the Playoffs UI ships). No backfill for legacy completed Seasons without `champion_team` set beyond the defensive `standings[0]` fallback in the Champion cell. No top-nav refactor beyond the `Leagues` → `League ▾` dropdown swap — the **mode-based base.html restructure** (different top-bar per LEAGUE / TEAM / PLAYERS mode, sandbox-vs-league mode-switching) is **LG-01h's** scope; LG-01f partially skeletons it via the sidebar's 4-section grouping but does NOT implement mode-switching. No new URL routes beyond `/leagues/<id>/history/` (the disabled sidebar entries + disabled top-bar items do NOT mount routes — they render as `<span class="disabled">` with no `<a href>`). No re-baseline of LG-01c / LG-01d / LG-01e tests beyond the single deletion of `TestSeasonDashboardSidebar` from `test_season_dashboard_view.py` (its 5-entry assertions are obsolete under the 14-entry shape); every other LG-01c / LG-01d / LG-01e test continues to pass without modification (the LG-01c dashboard's action-button DOM ids, `data-action-state` attributes, leaders / standings snippet DOM ids, etc. are all preserved by the flex-container restructure).

**Tests** live in **3 NEW files + 5+ EXTENDED files** under `matches/tests/`. `test_league_history.py` (NEW, Django `TestCase`, 8 classes — `TestLeagueHistoryRouting` (reverse / 200 / 404 / 405 / template), `TestLeagueHistoryEmptyState` (substring `"No Seasons yet"`; sidebar still rendered), `TestLeagueHistoryCompletedRows` (Season-name link, `Y-m-d` date, `teams_enrolled` from `starting_team_ids_json`, `matches_played` counts only `is_completed=True`, champion-cell renders team name, Runner-Up from standings rank 2, Tournament Champion `"—"`, top-3 + `"—"` padding, newest-first by id, `id="league-history-row-{n}"`), `TestLeagueHistoryInProgressRow` (`id="league-history-in-progress-row"` above completed rows, CSS substring `"in-progress-row"`, Champion cell `"In progress"` not team name, live standings in top-3, NOT counted in `per_page`, draft Season also pinned, absent when all completed), `TestLeagueHistoryChampionFallback` (`champion_team=None` ⇒ standings rank-1 fallback; `champion_team` present takes precedence), `TestLeagueHistoryPagination` (default 10, 25/50/100 accepted, invalid ⇒ 10, page invalid ⇒ 1, too-large clamps, links carry `per_page`, in-progress on every page, `<nav>` omitted on single page), `TestLeagueHistorySidebar` (`id="league-sidebar"` present, `id="sidebar-league-history"` has `"active"`, `id="sidebar-top-dashboard"` not active, all 14 entries present), `TestLeagueHistorySessionWrite` (GET writes `client.session["last_league_id"]`; 404 does NOT write)). `test_league_sidebar.py` (NEW, Django `TestCase` — the helper reads `League.seasons.filter(state="completed")` so DB-touching; classes `TestBuildLeagueSidebarLinks` (active-Season ⇒ Standings + Schedule target active; only-completed ⇒ target most-recent completed; zero Seasons ⇒ both entries disabled `url=None`; draft Season ⇒ both LIVE via `league.active_season`; Dashboard / History entries always LIVE; `sidebar_active` literals mark exactly the matching entry active; `None` ⇒ zero active; `"schedule"` marks LEAGUE > Schedule active not TEAM > `schedule_team`; etc.) and `TestSidebarLinkShape` (exactly 14 entries in pinned order; section order `top, league, team, players`; each entry has exactly 6 keys; 10 disabled entries; LIVE entries have `url: str, disabled: False`; `schedule_team` not `schedule`; `history_team` not `history`)). `test_league_nav_context_processor.py` (NEW, Django `TestCase`, class `TestLeagueNavContextProcessor` exercising the processor directly via `RequestFactory()` — session-pin with existing League returns its history URL; stale session id falls through to single-League branch / list-page when zero; single-League no-session returns history URL; 2+ Leagues no-session returns list URL; zero Leagues no-session returns list URL; returned key is exactly `top_bar_history_url`; no-crash when request has no `.session`). `test_league_dashboard.py` (EXTENDED — append `TestLg01fSidebarRendered` (sidebar partial rendered, Dashboard entry active, 14 entries, History entry URL targets this League's history) + `TestLg01fSessionWrite` (writes `last_league_id == league.id`; 404 does not write); no existing LG-01c / LG-01e class modified). `test_season_dashboard_view.py` (EXTENDED — **DELETE `TestSeasonDashboardSidebar` wholesale** (its 5-entry LG-01c assertions are obsolete under the 14-entry shape); append `TestLg01fSidebarRendered` (sidebar partial rendered, `sidebar_active=None` ⇒ zero active entries, 14 entries, LG-01c DOM ids ABSENT) + `TestLg01fSessionWrite` (writes `last_league_id == season.league_id`)). `views_tests.py` (EXTENDED — append sidebar + session assertions to LG-01 `season_standings` + `season_schedule` tests; for standings `id="sidebar-league-standings"` has `"active"`; for schedule `id="sidebar-league-schedule"` has `"active"`; both write `last_league_id`). LG-01d view test files (EXTENDED — one `test_lg01f_session_writes_last_league_id` per `start_season` / `play_week` / `play_two_months` / `play_until_end` / `play_status` view-test class). `test_league_next_season.py` (EXTENDED — `test_lg01f_session_writes_last_league_id_before_redirect` asserting 302 AND `client.session["last_league_id"] == league.id` — session middleware commits the cookie before sending the redirect). Tests must NOT touch `simulate_scheduled_round` / `simulate_match` / `save_games` or any simulator entry point (LG-01f runs no simulation; accidentally entering the simulator is a scope leak and locked out); tests must NOT `mock.patch` the ORM beyond `@override_settings` / `TestCase` machinery; tests that exercise an `"active"` or `"completed"` Season hand-construct persisted `Match` + `GameRound` + `PlayerRoundState` rows (LG-01c fixture-pattern precedent).

**Locked names.** URL path `GET /leagues/<int:league_id>/history/` (inserted AFTER LG-01e `<int:league_id>/next-season/` and BEFORE LG-01a `""` in `matches/league_urls.py`, final order `[create/, <int:league_id>/, <int:league_id>/next-season/, <int:league_id>/history/, ""]`); URL name `league_history` (bare, no `app_name`); view `matches.views.league_history`; helpers `matches.views._build_history_row` / `matches.views._build_league_sidebar_links` / `matches.views._coerce_per_page` / `matches.views._coerce_page`; deleted helper `matches.views._season_sidebar_links` (LG-01c 5-entry, removed wholesale); context processor `core.context_processors.league_nav` in NEW file `core/context_processors.py` (registered in `settings.TEMPLATES[0]["OPTIONS"]["context_processors"]`); sidebar partial `templates/_partials/league_sidebar.html` (NEW); page template `templates/leagues/history.html` (NEW, block title `{{ league.name }} — History` em-dash U+2014); modified templates `templates/base.html` + `templates/leagues/dashboard.html` + `templates/seasons/dashboard.html` + `templates/seasons/standings.html` + `templates/seasons/schedule.html`; `sidebar_active` literals `"dashboard" / "standings" / "schedule" / "playoffs" / "finances" / "history" / "power_rankings" / "roster" / "schedule_team" / "finances_team" / "history_team" / "free_agents" / "trade" / "trading_block" / None`; sidebar section literals `"top" / "league" / "team" / "players"`; session key `request.session["last_league_id"]` (int); context keys (history view) `league` / `in_progress_row` / `completed_rows` / `page_obj` / `paginator` / `per_page` / `per_page_options=(10,25,50,100)` / `sidebar_links` / `sidebar_active="history"`; context key (top-bar, from processor) `top_bar_history_url`; row-dict 11 keys `season_id, season_name, season_url, start_date, teams_enrolled, matches_played, champion, runner_up, tournament_champion, top_three, is_in_progress`; sidebar-entry-dict 6 keys `key, label, section, url, disabled, active`; per-page whitelist `(10, 25, 50, 100)`; pinned literals `"In progress"` / `"—"` / `"No Seasons yet"` / `"LEAGUE" / "TEAM" / "PLAYERS"` / `"League ▾"`; CSS-class substrings `"active"` / `"disabled"` / `"in-progress"` / `"in-progress-row"`; preserved LG-01a DOM id `leagues-nav-link` (now on dropdown toggle); NEW top-bar DOM id `league-history-topbar-link`; sidebar DOM ids `league-sidebar` / `sidebar-top-dashboard` / `sidebar-league-standings` / `sidebar-league-schedule` / `sidebar-league-playoffs` / `sidebar-league-finances` / `sidebar-league-history` / `sidebar-league-power_rankings` / `sidebar-team-roster` / `sidebar-team-schedule_team` / `sidebar-team-finances_team` / `sidebar-team-history_team` / `sidebar-players-free_agents` / `sidebar-players-trade` / `sidebar-players-trading_block`; history-page DOM ids `league-history-table` / `league-history-empty-notice` / `league-history-in-progress-row` / `league-history-row-{season_id}` / `league-history-pagination` / `league-history-per-page-form` / `league-history-per-page-select`; deleted LG-01c DOM ids `season-dashboard-sidebar` / `season-dashboard-sidebar-standings` / `season-dashboard-sidebar-schedule` / `season-dashboard-sidebar-teams` / `season-dashboard-sidebar-history`; deleted LG-01c test class `TestSeasonDashboardSidebar`; test files `matches/tests/test_league_history.py` (NEW) + `matches/tests/test_league_sidebar.py` (NEW) + `matches/tests/test_league_nav_context_processor.py` (NEW) + EXTENDED `matches/tests/test_league_dashboard.py` + `matches/tests/test_season_dashboard_view.py` + `matches/tests/views_tests.py` + the LG-01d view-test files + `matches/tests/test_league_next_season.py`; test classes `TestLeagueHistoryRouting` / `TestLeagueHistoryEmptyState` / `TestLeagueHistoryCompletedRows` / `TestLeagueHistoryInProgressRow` / `TestLeagueHistoryChampionFallback` / `TestLeagueHistoryPagination` / `TestLeagueHistorySidebar` / `TestLeagueHistorySessionWrite` / `TestBuildLeagueSidebarLinks` / `TestSidebarLinkShape` / `TestLeagueNavContextProcessor` / `TestLg01fSidebarRendered` / `TestLg01fSessionWrite`. ADR [ADR-0017](../../docs/adr/0017-league-context-nav-shape.md). Seam contract: [`.claude/worktrees/lg-01f-seam-contract.md`](../../.claude/worktrees/lg-01f-seam-contract.md).

## LG-01h global nav restructure

**Mode-based base.html restructure** + **19 placeholder pages behind a single shared `coming_soon` view** + **sidebar shape expansion from 14 to 23 entries**, all driven by a new `core.context_processors.app_mode` path-prefix rule that distinguishes league-mode pages (`/leagues/*`, `/seasons/*`) from sandbox-mode pages (everything else, including `/`, `/teams/`, `/players/`, `/matches/`, `/maps/`, `/help/*`, `/tools/*`). LG-01h fulfils the LG-01h promise that LG-01f's partial skeleton stepped toward — see [ADR-0017](../../docs/adr/0017-league-context-nav-shape.md) §4 — without breaking any of the LG-01f / LG-01g surfaces. **No model change, no migration, no new pure module, no simulator touch, no RNG, no `_flush_to_db` touch, no SIM-07 / SIM-08 contract interaction, no Score Calibration re-baseline, no new ADR, no CONTEXT.md edit, no JS framework, no Celery, no `messages.*`, no API endpoint, no new dependency, no admin change.**

**Mode-detecting context processor.** `core.context_processors.app_mode(request: HttpRequest) -> dict[str, str]` is APPENDED to the LG-01f-created `core/context_processors.py` (NOT a new file). Returns the single-key dict `{"app_mode": "league" | "sandbox"}` via the locked path-prefix rule: `request.path.startswith("/leagues/") or request.path.startswith("/seasons/")` ⇒ `"league"`; everything else ⇒ `"sandbox"`. Reads via `getattr(request, "path", "/")` so a `RequestFactory()`-built request without `.path` doesn't crash. Registered in `settings.TEMPLATES[0]["OPTIONS"]["context_processors"]` **immediately after** `core.context_processors.league_nav` (locked order — `league_nav` first, `app_mode` second). Locked literals `"league"` and `"sandbox"` and the locked context key `app_mode`.

**Base.html mode branching.** `templates/base.html` is MODIFIED — the existing `<div class="navbar-nav ms-auto">` block (LG-01a `Leagues` link replaced by the LG-01f `League ▾` dropdown plus the 6 LG-01a flat sandbox links) is restructured around `{% if app_mode == "league" %}` / `{% else %}`. **League mode** renders, in pinned left-to-right order: brand link (unchanged, `{% url 'landing' %}`), then `League ▾` (LG-01f dropdown preserved, all 5 items now LIVE), then `Help ▾` (NEW), then `Tools ▾` (NEW). **Sandbox mode** renders the brand link, then the 6 LG-01a flat sandbox links in their existing left-to-right order (Teams / Players / Matches / Batch Sim / Create Team / Maps — verbatim `<a class="nav-link" href="{% url '…' %}">` from current `base.html` lines 30–35), then `League ▾`, then `Help ▾`, then `Tools ▾`. The LG-01a-locked outer wrapper `<div class="container">` + `<button class="navbar-toggler">` + `<div class="collapse navbar-collapse" id="mainNav">` markup is preserved verbatim around both branches. The LG-01a-locked DOM id `leagues-nav-link` on the dropdown toggle and the LG-01f-locked DOM id `league-history-topbar-link` on the History `<a class="dropdown-item">` are preserved verbatim in both branches.

**Top-bar dropdown items (Part a flip).** `League ▾` dropdown (preserved structure, items flip from disabled `<span>` to LIVE `<a>` where placeholder URLs apply): item 1 **Standings** flips to LIVE pointing at `season_standings` resolved via the `top_bar_standings_url` chain (the LG-01f `top_bar_history_url` resolution pattern extended); item 2 **Playoffs** flips to LIVE pointing at the placeholder URL `coming_soon_playoffs`; item 3 **Finances** flips to LIVE pointing at `coming_soon_finances`; item 4 **History** stays LIVE (LG-01f, unchanged, `{{ top_bar_history_url }}`); item 5 **Power Rankings** flips to LIVE pointing at `coming_soon_power_rankings`. Order locked top-to-bottom: Standings / Playoffs / Finances / History / Power Rankings. DOM ids on each `<a class="dropdown-item">`: `league-standings-topbar-link` / `league-playoffs-topbar-link` / `league-finances-topbar-link` / `league-history-topbar-link` (LG-01f preserved) / `league-power-rankings-topbar-link`. The existing `league_nav` processor gains **4 new keys** alongside `top_bar_history_url`: `top_bar_standings_url`, `top_bar_playoffs_url`, `top_bar_finances_url`, `top_bar_power_rankings_url` (all resolved via the same 3-step session-pin → single-League → list-page chain LG-01f locked). When no League exists, every key resolves to `reverse("league_list")` (LG-01f fallback precedent).

`Help ▾` dropdown (NEW, 6 items in pinned top-to-bottom order, ALL placeholder URLs): **Overview** (`coming_soon_help_overview`), **Changes** (`coming_soon_help_changes`), **Custom Rosters** (`coming_soon_help_custom_rosters`), **Debugging** (`coming_soon_help_debugging`), **LOL GM Forums** (`coming_soon_help_lol_gm_forums`), **Zen GM Forums** (`coming_soon_help_zen_gm_forums`). Toggle text `"Help ▾"` (U+25BE), toggle DOM id `help-nav-link`, per-item DOM ids `help-overview-topbar-link` / `help-changes-topbar-link` / `help-custom-rosters-topbar-link` / `help-debugging-topbar-link` / `help-lol-gm-forums-topbar-link` / `help-zen-gm-forums-topbar-link`.

`Tools ▾` dropdown (NEW, 4 items in pinned top-to-bottom order, ALL placeholder URLs): **Achievements** (`coming_soon_tools_achievements`), **Screenshot** (`coming_soon_tools_screenshot`), **Enable Debug Mode** (`coming_soon_tools_debug_mode`), **Reset DB** (`coming_soon_tools_reset_db`). Toggle text `"Tools ▾"` (U+25BE), toggle DOM id `tools-nav-link`, per-item DOM ids `tools-achievements-topbar-link` / `tools-screenshot-topbar-link` / `tools-debug-mode-topbar-link` / `tools-reset-db-topbar-link`.

**Shared placeholder view.** NEW view `matches.views.coming_soon(request: HttpRequest, feature_key: str, league_id: int | None = None, team_id: int | None = None) -> HttpResponse` is the single shared `<h1>Coming soon</h1>` view for every placeholder URL. Undecorated, GET-only via `if request.method != "GET": return HttpResponseNotAllowed(["GET"])` as the **first** line of the body (LG-01c / LG-01d / LG-01e / LG-01f / LG-01g locked pattern). Body in pinned order: (1) 405 guard before any ORM hit; (2) when `league_id is not None` ⇒ `league = get_object_or_404(League, pk=league_id)` else `league = None`; (3) `team_id` kwarg reserved for forward-compat with LG-02+; (4) `feature = _FEATURE_REGISTRY.get(feature_key)` ⇒ `Http404(f"Unknown placeholder feature {feature_key!r}.")` when `None`; (5) when `league is not None` write `request.session["last_league_id"] = league.id` (extends the LG-01f session-write site list); (6) `displayed_season = league.active_season or league.seasons.filter(state="completed").order_by("-id").first()` when `league is not None`, else `None`; (7) `sidebar_active = feature["sidebar_active"]` (a literal from the 23-entry enum or `None`); `sidebar_links = _build_league_sidebar_links(league, displayed_season, sidebar_active)` when `league is not None`, else `sidebar_links = []` (Help / Tools placeholders render with an empty sidebar — sandbox-mode pages); (8) `return render(request, "_placeholder.html", context)` with the locked **7 context keys** `league` / `displayed_season` / `feature_key` / `feature_label` (= `feature["label"]`) / `feature_section` (= `feature["section"]`) / `sidebar_links` / `sidebar_active`. The view lives in `matches/views.py` even for the Help / Tools entries (single shared view; no `core/views.py` duplication).

**`_FEATURE_REGISTRY` vocabulary (35 entries).** Module-level hard-coded dict `matches.views._FEATURE_REGISTRY: dict[str, dict[str, str | None]]` keyed on `feature_key` with value dicts `{label: str, section: str, sidebar_active: str | None}`; `section ∈ {"league", "team", "players", "stats", "help", "tools"}`. Full locked vocabulary: **League-scoped (10)** — `league_playoffs` / `league_finances` / `league_power_rankings` (3 NEW under LG-01h) plus 7 already-LIVE registry entries reserved for future flips; **Team-scoped (3)** — `team_roster` / `team_finances` / `team_history` (sidebar_active `"roster"` / `"finances_team"` / `"history_team"`); **Players-scoped (6)** — `players_free_agents` / `players_trade` / `players_trading_block` / `players_prospects` / `players_watch_list` / `players_hall_of_fame`; **Stats-scoped (6)** — `stats_game_log` / `stats_league_leaders` / `stats_player_ratings` / `stats_player_stats` / `stats_team_stats` / `stats_statistical_feats`; **Help (6)** — `help_overview` / `help_changes` / `help_custom_rosters` / `help_debugging` / `help_lol_gm_forums` / `help_zen_gm_forums` (labels `"Overview"` / `"Changes"` / `"Custom Rosters"` / `"Debugging"` / `"LOL GM Forums"` / `"Zen GM Forums"`, all `section="help"`, all `sidebar_active=None`); **Tools (4)** — `tools_achievements` / `tools_screenshot` / `tools_debug_mode` / `tools_reset_db` (labels `"Achievements"` / `"Screenshot"` / `"Enable Debug Mode"` / `"Reset DB"`, all `section="tools"`, all `sidebar_active=None`).

**Placeholder template.** NEW `templates/_placeholder.html` extending `base.html`, `{% block title %}{{ feature_label }} — Coming Soon{% endblock %}` (em-dash U+2014, locked exact format). Structure: when `league` is non-`None`, wrap `<div class="d-flex">{% include "_partials/league_sidebar.html" %}<main>...</main></div>` (sidebar partial consumed unchanged from LG-01f); else render `<main>...</main>` directly (no sidebar — Help / Tools placeholders are sandbox-mode, mode-aware via `app_mode`). Locked DOM ids inside `<main>`: `coming-soon-header` (wraps `<h1>{{ feature_label }}</h1>`) / `coming-soon-section-badge` (badge rendering `{{ feature_section }}` for visual context) / `coming-soon-message` (`<p>` containing the locked exact substring `"Coming soon"`) / `coming-soon-feature-key` (`<small>` rendering `{{ feature_key }}` for developer-debug visibility). No inline JS, no `<script>` block, no form, no `<button>`.

**URL routes.** NEW URL include file `core/help_urls.py` (mounted at `/help/`) with 6 paths in pinned order: `overview/` / `changes/` / `custom-rosters/` / `debugging/` / `lol-gm-forums/` / `zen-gm-forums/` reverse-named `coming_soon_help_overview` / `coming_soon_help_changes` / `coming_soon_help_custom_rosters` / `coming_soon_help_debugging` / `coming_soon_help_lol_gm_forums` / `coming_soon_help_zen_gm_forums`. The include file does `from matches import views` (cross-app import; Help URLs route to the shared `matches.views.coming_soon` to avoid duplication). NEW URL include file `core/tools_urls.py` (mounted at `/tools/`) with 4 paths in pinned order: `achievements/` / `screenshot/` / `debug-mode/` / `reset-db/` reverse-named `coming_soon_tools_achievements` / `coming_soon_tools_screenshot` / `coming_soon_tools_debug_mode` / `coming_soon_tools_reset_db`. Both files use no `app_name` (bare URL namespace, LG-01a/b/c/d/e/f/g precedent). `matches/league_urls.py` EXTENDED with **15 new path entries** inserted **AFTER** the LG-01g `<int:league_id>/team_schedule/<int:team_id>/` line and **BEFORE** the LG-01a `""` line — 3 League-scoped (`<int:league_id>/playoffs/` ⇒ `coming_soon_playoffs`, `<int:league_id>/finances/` ⇒ `coming_soon_finances`, `<int:league_id>/power-rankings/` ⇒ `coming_soon_power_rankings`), 3 Team-scoped under `<int:league_id>/team/<slug>/` (`coming_soon_team_roster` / `coming_soon_team_finances` / `coming_soon_team_history`; the sidebar TEAM section's hrefs resolve via the LG-01g `_resolve_current_team_for_sidebar` chain in the same way TEAM > Schedule does), 6 Players-scoped under `<int:league_id>/players/<slug>/` (`coming_soon_free_agents` / `coming_soon_trade` / `coming_soon_trading_block` / `coming_soon_prospects` / `coming_soon_watch_list` / `coming_soon_hall_of_fame`), 6 Stats-scoped under `<int:league_id>/stats/<slug>/` (`coming_soon_game_log` / `coming_soon_league_leaders` / `coming_soon_player_ratings` / `coming_soon_player_stats` / `coming_soon_team_stats` / `coming_soon_statistical_feats`). **LEAGUE > Standings stays LIVE via LG-01f's `season_standings` — no new `<int:league_id>/standings/` route is mounted.** `laserforce_simulator/urls.py` MODIFIED with 2 single-line inserts `path("help/", include("core.help_urls"))` + `path("tools/", include("core.tools_urls"))`, inserted alphabetically among the existing `include(...)` lines (Code agent's discretion on exact placement; tests only assert reverse resolution).

**Sidebar partial extension.** `templates/_partials/league_sidebar.html` is MODIFIED — the existing `{% regroup sidebar_links by section as sections %}` loop already handles arbitrary section keys (LG-01f). One edit: add `{% elif section.grouper == "stats" %}<h6 class="text-muted text-uppercase small mt-3 mb-1">STATS</h6>{% endif %}` adjacent to the existing `{% elif section.grouper == "players" %}` branch (locked exact `<h6>` markup mirroring the existing 3 section headers). The partial otherwise consumes the helper output unchanged — no per-entry markup changes, no new DOM ids beyond the `sidebar-{section}-{key}` pattern (LG-01f locked) which extends to the 9 new entries automatically. Section iteration order (locked, from `regroup`): `top` → `league` → `team` → `players` → `stats`. The `"help"` and `"tools"` sections appear in `_FEATURE_REGISTRY` `section` field but NOT in the sidebar partial's iteration (Help / Tools placeholders render with `sidebar_links = []`).

**`_build_league_sidebar_links` extension.** `matches.views._build_league_sidebar_links(league, displayed_season, sidebar_active) -> list[dict]` is EXTENDED in-place (LG-01g precedent — same signature, body extended, **NOT** renamed to `_v2`). Returns exactly **23 dicts** in pinned order (was 14 at LG-01f, 14 still at LG-01g): index 0 (top, `dashboard`) → indexes 1–6 LEAGUE (6 entries — Standings LIVE via `season_standings`, **Schedule** LIVE via `season_schedule` per ADR-0017 §2 divergence-from-zengm, Playoffs LIVE via `coming_soon_playoffs`, Finances LIVE via `coming_soon_finances`, History LIVE via `league_history`, Power Rankings LIVE via `coming_soon_power_rankings`) → indexes 7–10 TEAM (4 entries — Roster LIVE via `coming_soon_team_roster`, Schedule LG-01g `schedule_team` LIVE via `team_schedule`, Finances LIVE via `coming_soon_team_finances`, History LIVE via `coming_soon_team_history`) → indexes 11–16 PLAYERS (6 entries — Free Agents / Trade / Trading Block LG-01f keys now LIVE plus 3 NEW Prospects / Watch List / Hall of Fame, all LIVE via `coming_soon_*`) → indexes 17–22 STATS (6 entries, entire section NEW — Game Log / League Leaders / Player Ratings / Player Stats / Team Stats / Statistical Feats, all LIVE via `coming_soon_*`). Each entry preserves the LG-01f 6-key dict shape `{key, label, section, url, disabled, active}`. League-scoped URLs (every entry except the top Dashboard) require `league.id` for reverse; helpers internally call `reverse("coming_soon_*", kwargs={"league_id": league.id})`. When `league is None` (Help / Tools mode), the helper is not called; when `league is not None` but `displayed_season is None`, the Standings / Schedule entries fall back to `url=None, disabled=True` (LG-01f rule, preserved verbatim).

**`sidebar_active` enum extends from 14 to 23 values + `None`.** Full locked list: `"dashboard"`, `"standings"`, `"schedule"`, `"playoffs"`, `"finances"`, `"history"`, `"power_rankings"`, `"roster"`, `"schedule_team"`, `"finances_team"`, `"history_team"`, `"free_agents"`, `"trade"`, `"trading_block"`, `"prospects"`, `"watch_list"`, `"hall_of_fame"`, `"game_log"`, `"league_leaders"`, `"player_ratings"`, `"player_stats"`, `"team_stats"`, `"statistical_feats"`, `None`. Key-collision rule (locked, extends LG-01g `_team` suffix precedent): LEAGUE > Schedule keeps `"schedule"`; TEAM > Schedule uses `"schedule_team"`; TEAM > Finances uses `"finances_team"`; TEAM > History uses `"history_team"`. The 9 new entries (3 PLAYERS additions + 6 STATS) introduce zero new collisions.

**Page wiring is zero-edit.** Every page that already renders the LG-01f sidebar partial (League dashboard, League history, Season dashboard, Season standings, Season schedule, Team Schedule from LG-01g) **automatically picks up the 23-entry shape** via the modified helper — no per-page template edit required beyond `templates/_partials/league_sidebar.html` and `templates/base.html`. The 6 existing pages render the same partial; the partial reads the same `sidebar_links` context key; only the list length and the disabled→LIVE flips change. **Confirmed zero-edit templates**: `templates/leagues/dashboard.html`, `templates/leagues/history.html`, `templates/leagues/team_schedule.html` (LG-01g), `templates/seasons/dashboard.html`, `templates/seasons/standings.html`, `templates/seasons/schedule.html`. **Confirmed edited templates**: `templates/base.html` (mode branching + Help / Tools dropdowns), `templates/_partials/league_sidebar.html` (STATS section header), `templates/_placeholder.html` (NEW).

**Tests.** Live in **3 NEW files + 6 EXTENDED files** under `matches/tests/`. NEW: `test_topnav.py` (Django `TestCase`, class `TestAppModeContextProcessor` covering `/` / `/teams/` / `/players/` / `/matches/` / `/maps/` / `/help/overview/` / `/tools/achievements/` ⇒ sandbox; `/leagues/` / `/leagues/1/` / `/leagues/1/history/` / `/seasons/1/` / `/seasons/1/standings/` ⇒ league; edge cases empty `request.path` + missing `.path` attribute via raw `RequestFactory()`); `test_coming_soon.py` (`TestComingSoonRouting` covering 200 happy path / 405 on POST / 404 on stale `league_id` / 404 on unknown `feature_key` / sidebar rendered in league branch / sidebar empty in Help / Tools branch / `feature_label` injected into `<h1>` / `coming-soon-message` substring `"Coming soon"` / 7 context keys / `app_mode` matches URL-prefix rule, plus `TestComingSoonFeatureRegistry` asserting all 35 entries with 3 value-dict keys each + every `sidebar_active` in the 23+`None` enum + Help / Tools entries `sidebar_active=None`, plus `TestComingSoonSessionWrite` writing `last_league_id` on league-scoped placeholders and NOT on Help / Tools); `test_topnav.py` (Django `TestCase` exercising `client.get("/")` ⇒ sandbox markup, `client.get("/teams/")` ⇒ sandbox links + Help / Tools dropdowns, `client.get(f"/leagues/{league.id}/")` ⇒ flat sandbox links ABSENT + Help / Tools dropdowns rendered + `League ▾` with all 5 items LIVE). EXTENDED: `test_league_sidebar.py` (length 14 → 23, section counts `top=1, league=6, team=4, players=6, stats=6`, NEW `TestLg01hStatsSection` / `TestLg01hPlayersExpansion` / `TestLg01hDisabledFlipsLive`); `test_league_nav_context_processor.py` (4 new keys `top_bar_standings_url` / `top_bar_playoffs_url` / `top_bar_finances_url` / `top_bar_power_rankings_url` resolving via the same 3-step chain); `test_league_history.py`, `test_league_dashboard.py`, `test_season_dashboard_view.py`, `views_tests.py` (every assertion of the form `len(sidebar_links) == 14` updated to `== 23`; every assertion enumerating the 14 entries updated to the 23-entry list; section-count assertions 4 → 5). Tests must NOT touch `simulate_scheduled_round` / `simulate_match` / `save_games` or any simulator entry point (LG-01h runs no simulation).

**Scope-out (locked).** No model change. No migration (LG-01g's `0030_league_current_team.py` remains the latest LG-01x migration). No simulator touch. No RNG consumption. No `_flush_to_db` touch. No SIM-07 / SIM-08 contract interaction. No Score Calibration re-baseline obligation. No new ADR (ADR-0017 receives a Consequences-extension note recording the 14→23-entry shape change, the 4-state `sidebar_active` enum extension to 23-value, and the mode-based base.html branching). No CONTEXT.md edit (every term — sandbox / league / placeholder / mode / dropdown / sidebar / app_mode — is implementation language, not domain; the existing `League` / `Season` / `Standings` / `Matchday` / `Job` / `Team schedule` / `Current team` glossary entries cover the domain). No real implementation of the 19 placeholder pages (each just renders `<h1>Coming soon</h1>` via the shared template). No JS framework / htmx / Alpine / Stimulus / new inline `<script>` blocks (Bootstrap 5 dropdown JS is the only existing dep). No new dependency. No API / DRF endpoint. No `django.contrib.messages` flash. No admin change. No backfill. No edit to `matches/standings.py` / `matches/schedule_generator.py` / `matches/season_dashboard.py` / `matches/tasks.py` / `matches/simulation/` / `matches/season_urls.py` / `matches/forms.py`. No edit to `teams/models.py` / `teams/views.py` / `teams/forms.py` / `teams/admin.py` / `teams/constants.py` / `teams/player_generator.py`. No edit to existing dashboard / standings / schedule / history / team_schedule templates (they consume the modified partial + helper unchanged). No `<int:team_id>/` placeholder routes at LG-01h (Team-scoped placeholders are `<int:league_id>/`-keyed; LG-02+ may convert to `<int:team_id>/` paths once Team detail surfaces exist). No mode-toggle UI (mode is path-driven only). No multiplayer mode (deferred per ADR-0017 §1).

**Locked names.** URL include files `core/help_urls.py` (NEW) + `core/tools_urls.py` (NEW); URL include EXTENDED `matches/league_urls.py` (15 new path entries); URL names `coming_soon_help_overview` / `coming_soon_help_changes` / `coming_soon_help_custom_rosters` / `coming_soon_help_debugging` / `coming_soon_help_lol_gm_forums` / `coming_soon_help_zen_gm_forums` / `coming_soon_tools_achievements` / `coming_soon_tools_screenshot` / `coming_soon_tools_debug_mode` / `coming_soon_tools_reset_db` / `coming_soon_playoffs` / `coming_soon_finances` / `coming_soon_power_rankings` / `coming_soon_team_roster` / `coming_soon_team_finances` / `coming_soon_team_history` / `coming_soon_free_agents` / `coming_soon_trade` / `coming_soon_trading_block` / `coming_soon_prospects` / `coming_soon_watch_list` / `coming_soon_hall_of_fame` / `coming_soon_game_log` / `coming_soon_league_leaders` / `coming_soon_player_ratings` / `coming_soon_player_stats` / `coming_soon_team_stats` / `coming_soon_statistical_feats`; view `matches.views.coming_soon`; module-level constant `matches.views._FEATURE_REGISTRY` (35-entry hard-coded dict); context processor `core.context_processors.app_mode` (NEW) + `core.context_processors.league_nav` (EXTENDED — 4 new keys `top_bar_standings_url` / `top_bar_playoffs_url` / `top_bar_finances_url` / `top_bar_power_rankings_url`); modified helper `matches.views._build_league_sidebar_links` (signature unchanged, body returns 23 entries); template (NEW) `templates/_placeholder.html` (block title `{{ feature_label }} — Coming Soon`, em-dash U+2014); modified templates `templates/base.html` + `templates/_partials/league_sidebar.html`; locked DOM ids — top-bar dropdown toggles `leagues-nav-link` (preserved LG-01a/f) + `help-nav-link` (NEW) + `tools-nav-link` (NEW); `League ▾` items `league-standings-topbar-link` + `league-playoffs-topbar-link` + `league-finances-topbar-link` + `league-history-topbar-link` (preserved LG-01f) + `league-power-rankings-topbar-link`; Help items `help-overview-topbar-link` / `help-changes-topbar-link` / `help-custom-rosters-topbar-link` / `help-debugging-topbar-link` / `help-lol-gm-forums-topbar-link` / `help-zen-gm-forums-topbar-link`; Tools items `tools-achievements-topbar-link` / `tools-screenshot-topbar-link` / `tools-debug-mode-topbar-link` / `tools-reset-db-topbar-link`; placeholder-page DOM ids `coming-soon-header` / `coming-soon-section-badge` / `coming-soon-message` / `coming-soon-feature-key`; sidebar partial section header NEW `<h6>STATS</h6>`; sidebar-entry DOM ids extend to `sidebar-stats-game_log` / `sidebar-stats-league_leaders` / `sidebar-stats-player_ratings` / `sidebar-stats-player_stats` / `sidebar-stats-team_stats` / `sidebar-stats-statistical_feats` + `sidebar-players-prospects` / `sidebar-players-watch_list` / `sidebar-players-hall_of_fame` (LG-01f pattern `sidebar-{section}-{key}` extends automatically); context keys (`coming_soon` view) `league` / `displayed_season` / `feature_key` / `feature_label` / `feature_section` / `sidebar_links` / `sidebar_active`; context key (global, from `app_mode`) `app_mode` ∈ `"league"` / `"sandbox"`; context keys (global, from `league_nav`) `top_bar_history_url` (preserved) + 4 NEW; `sidebar_active` 23-value enum + `None` listed verbatim above; section literals `"top"` / `"league"` / `"team"` / `"players"` / `"stats"` (sidebar partial) plus `"help"` / `"tools"` (in `_FEATURE_REGISTRY` `section` field only); locked literals `"Coming soon"` (placeholder body substring) + `"League ▾"` / `"Help ▾"` / `"Tools ▾"` (dropdown toggle text, U+25BE) + `"STATS"` (sidebar section header) + `"sandbox"` / `"league"` (`app_mode` values); test files `matches/tests/test_topnav.py` (NEW) + `test_coming_soon.py` (NEW) + `test_topnav.py` (NEW); EXTENDED test files `matches/tests/test_league_sidebar.py` + `test_league_nav_context_processor.py` + `test_league_history.py` + `test_league_dashboard.py` + `test_season_dashboard_view.py` + `views_tests.py`. ADR [ADR-0017](../../docs/adr/0017-league-context-nav-shape.md) extended at code time with a Consequences-extension note. Seam contract: [`.claude/worktrees/lg-01h-seam-contract.md`](../../.claude/worktrees/lg-01h-seam-contract.md).

## LG-01k top nav modes

**Three-mode topnav restructure** — extends the LG-01h `core.context_processors.app_mode` enum from 2 values (`"league"` / `"sandbox"`) to **3 values** (`"start"` / `"league"` / `"sandbox"`), rewrites the `core.context_processors.league_nav` return shape from 5 URL keys to 2 keys (drops `top_bar_history_url` / `top_bar_standings_url` / `top_bar_playoffs_url` / `top_bar_finances_url` / `top_bar_power_rankings_url`, adds `top_bar_links: list[dict]` + `top_bar_dashboard_url: str`), and rewrites `templates/base.html`'s `<div class="navbar-nav ms-auto">` block around a 3-way `{% if app_mode == "league" %}` / `{% elif app_mode == "sandbox" %}` / `{% else %}` branch where the `{% else %}` arm is the start-mode (path == `/`) minimum-viable Tools ▾ + Help ▾ pair. LG-01k is best understood as an **in-place body extension** of both LG-01h context processors plus a topnav-block rewrite — `_build_league_sidebar_links` becomes the single source of truth for both the LG-01f sidebar partial AND the LG-01k league-mode topbar (flipping a disabled→LIVE in the helper updates both surfaces at once; LG-01k does NOT edit the helper, only consumes it read-only). **No model change, no migration, no simulator touch, no RNG, no `_flush_to_db` touch, no SIM-07 / SIM-08 contract interaction, no Score Calibration re-baseline, no new ADR, no CONTEXT.md edit, no admin change, no JS framework, no Celery, no `messages.*`, no API endpoint, no new dependency, no edit to `_partials/league_sidebar.html`, no edit to `_build_league_sidebar_links`, no edit to any view or URL include.**

**Mode-detecting context processor (extension).** `core.context_processors.app_mode(request: HttpRequest) -> dict[str, str]` is REWRITTEN in place — signature unchanged, return-key unchanged, return-value-type still `dict[str, str]` (all 3 enum values are strings). Locked 3-way path-prefix rule applied in this exact order so that `/` does NOT fall into sandbox: (1) **`path == "/"` (exact match)** ⇒ `"start"`; (2) **`path.startswith("/leagues/") or path.startswith("/seasons/")`** ⇒ `"league"`; (3) **everything else** (including empty string, missing `.path` attribute, `/teams/`, `/players/`, `/matches/`, `/maps/`, `/help/*`, `/tools/*`, any unknown path) ⇒ `"sandbox"`. The defensive read distinguishes "missing attribute" from "explicit `/`" via `path = getattr(request, "path", None)` — `None` and `""` both fall through to the sandbox return (rule 3); only an explicit `path == "/"` string trips the start return (rule 1). Locked literals `"start"` (NEW) / `"league"` (preserved) / `"sandbox"` (preserved) and the locked context key `app_mode`. Settings registration order in `settings.TEMPLATES[0]["OPTIONS"]["context_processors"]` is UNCHANGED from LG-01h — `core.context_processors.league_nav` first, `core.context_processors.app_mode` second.

**League-nav context processor (extension).** `core.context_processors.league_nav(request: HttpRequest) -> dict[str, Any]` is REWRITTEN — return-type annotation widens from `dict[str, str]` (LG-01h) to `dict[str, Any]` because `top_bar_links` is a `list[dict]`, not a `str`. The **5 LG-01h URL keys** `top_bar_history_url` / `top_bar_standings_url` / `top_bar_playoffs_url` / `top_bar_finances_url` / `top_bar_power_rankings_url` are DELETED from the return dict (zero callers remain after the `base.html` rewrite). The processor now returns exactly **2 keys**: (1) `top_bar_links: list[dict]` — the **23-entry output of `matches.views._build_league_sidebar_links(league, displayed_season, sidebar_active=None)`** for the resolved League + displayed Season, or `[]` when no League can be resolved (the `sidebar_active=None` argument is locked — no entry renders with the `active` styling in the topnav, since active-styling belongs to the sidebar partial); (2) `top_bar_dashboard_url: str` — `reverse("league_dashboard", kwargs={"league_id": league.id})` for the resolved League, or `reverse("league_list")` when no League can be resolved. The **3-step League resolution chain** is identical to LG-01f / LG-01h (session-pin `last_league_id` → single-League in DB via bounded `[:2]` probe → fallback to list-page), the **displayed-Season resolution** is identical to LG-01h's standings chain (`league.active_season` → most-recent completed via `league.seasons.filter(state="completed").order_by("-id").first()` → `None`), and the **defensive DB-error handling** is identical to LG-01h (every ORM call wrapped `try: ... except DatabaseError:` logging at DEBUG, broken-transaction renders falling through to the empty-list + list-page fallback). The lazy local-import `from matches.models import League` inside the function body STAYS (avoids the `core ↔ matches` apps-loading cycle); a new lazy local-import `from matches.views import _build_league_sidebar_links` joins it inside the function body, not at module scope, to preserve the LG-01f apps-loading-cycle guard. The 5 deleted keys are NOT replaced by 5 individual context keys — `top_bar_links` carries the same URLs structurally (Standings / Playoffs / Finances / History / Power Rankings appear as entries at indexes 1–6 of the 23-entry list with `section="league"`), and the template iterates the regrouped list rather than referencing per-URL keys.

**Base.html mode branching (rewrite).** `templates/base.html` is MODIFIED — the existing LG-01h `<div class="navbar-nav ms-auto">` block is **fully rewritten** around a 3-way `{% if app_mode == "league" %}` / `{% elif app_mode == "sandbox" %}` / `{% else %}` branch. The brand link `<a class="navbar-brand" href="{% url 'landing' %}">⚡ Laserforce Manager</a>` is preserved verbatim across all 3 modes. The LG-01a-locked outer wrapper `<div class="container">` + `<button class="navbar-toggler">` + `<div class="collapse navbar-collapse" id="mainNav">` markup is preserved verbatim around the 3-branch block. The `{% else %}` branch is the **start mode** (path == `/`) — the simplest branch (Tools ▾ + Help ▾ only) and the natural default — placing it as `{% else %}` minimises the visual complexity of the most-frequently-loaded path (the landing page). **Order delta from LG-01h**: Tools is now BEFORE Help (LG-01h had Help-then-Tools; LG-01k swaps to Tools-then-Help). This applies in all 3 modes.

**League-mode block (7 elements).** Pinned left-to-right order: `[⌂ home icon] | League ▾ | Team ▾ | Players ▾ | Stats ▾ | Tools ▾ | Help ▾`. The 7 elements: (1) **Dashboard home-icon link** — `<a class="nav-link" id="dashboard-nav-link" href="{{ top_bar_dashboard_url }}" aria-label="League dashboard">⌂</a>`; **the home-icon text content is the literal character `⌂` (U+2302 HOUSE)** — no Bootstrap Icons CDN, no `<i>` element, no SVG, no 🏠 emoji (the emoji renders inconsistently across Windows / cp1252 terminals and project shell conventions prefer ASCII / BMP characters); (2) **`League ▾` dropdown toggle** `<a class="nav-link dropdown-toggle" id="league-nav-link" href="#" role="button" data-bs-toggle="dropdown" aria-expanded="false">League ▾</a>` (the `s` is dropped from the LG-01h `leagues-nav-link` id — LG-01k uses the singular `league-nav-link` form to match the `section="league"` vocabulary of the regrouped `top_bar_links`) followed by a `<ul class="dropdown-menu" aria-labelledby="league-nav-link">` containing the LEAGUE section of `top_bar_links` (6 entries: Standings / Schedule / Playoffs / Finances / History / Power Rankings); (3) **`Team ▾` dropdown toggle** id `team-nav-link` containing the TEAM section (4 entries: Roster / Schedule / Finances / History); (4) **`Players ▾` dropdown toggle** id `players-nav-link` containing the PLAYERS section (6 entries: Free Agents / Trade / Trading Block / Prospects / Watch List / Hall of Fame); (5) **`Stats ▾` dropdown toggle** id `stats-nav-link` containing the STATS section (6 entries: Game Log / League Leaders / Player Ratings / Player Stats / Team Stats / Statistical Feats); (6) **`Tools ▾` dropdown toggle** preserved verbatim from LG-01h (4 items, ids `tools-nav-link` + `tools-{achievements,screenshot,debug-mode,reset-db}-topbar-link`); (7) **`Help ▾` dropdown toggle** preserved verbatim from LG-01h (6 items, ids `help-nav-link` + `help-{overview,changes,custom-rosters,debugging,lol-gm-forums,zen-gm-forums}-topbar-link`).

**Sandbox-mode block (8 elements).** Pinned left-to-right order: `Teams | Players | Matches | Batch Sim | Create Team | Maps | Tools ▾ | Help ▾` — 6 flat LG-01a anchors preserved verbatim (1: `team_list` Teams; 2: `player_list` Players, LG-01a-locked DOM id `player-list-nav-link` preserved; 3: `match_list` Matches; 4: `simulate_batch` Batch Sim; 5: `team_create` Create Team; 6: `map_list` Maps — anchors 1, 3, 4, 5, 6 carry no DOM id, matching LG-01a) followed by the universal Tools ▾ + Help ▾ dropdowns. **Delta from LG-01h**: the LG-01h sandbox branch included a `League ▾` dropdown after the 6 flat links — in LG-01k this `League ▾` dropdown is **REMOVED from sandbox mode entirely**. The intent: a user in sandbox mode is not browsing a League, so the League menu surface is irrelevant; flipping into League mode happens via clicking a mode card on `/` or navigating to a `/leagues/*` / `/seasons/*` URL, which path-flips `app_mode` to `"league"` and re-renders the topnav accordingly.

**Start-mode block (2 elements).** Pinned left-to-right order: `Tools ▾ | Help ▾`. That's it — no `League ▾`, no Dashboard icon, no flat sandbox links, no `player-list-nav-link`. The start page (`/`) presents the minimum-viable topnav — only the universal Tools / Help surfaces. The user lands at `/`, picks a mode card (per LG-01a `mode-card-sandbox` / `mode-card-league` / `mode-card-multiplayer`), and only then does the topnav populate with the mode-specific surfaces. The Tools ▾ + Help ▾ markup is identical across all 3 modes; the Code agent MAY (locked optional) factor the ~14 lines of duplication into a small `{% include "_partials/topnav_tools_help.html" %}` partial included at the end of each branch — if the partial is created, its path is locked as `templates/_partials/topnav_tools_help.html`; if the agent inlines the markup 3× instead (simpler diff, more lines), that is equally acceptable since the test plan asserts on DOM ids, not on inclusion structure.

**Section-dropdown iteration pattern.** Applies to all 4 league-mode section dropdowns (League / Team / Players / Stats). The template uses `{% regroup top_bar_links by section as sections %}` at the start of the league branch, then per-section renders the entries by filtering on `section.grouper`; inside each section's `<ul>` the per-entry rendering branches on `entry.disabled` — `{% if entry.disabled %}<li><span class="dropdown-item disabled">{{ entry.label }}</span></li>{% else %}<li><a class="dropdown-item" id="topbar-{{ entry.section }}-{{ entry.key }}" href="{{ entry.url }}">{{ entry.label }}</a></li>{% endif %}`. The **`topbar-{section}-{key}` DOM-id pattern** is locked and mirrors the LG-01f `sidebar-{section}-{key}` pattern verbatim; concrete league-mode ids that result for the 22 dropdown entries (excluding the top Dashboard entry rendered as the leading icon link, NOT in any dropdown) are `topbar-league-standings` / `topbar-league-schedule` / `topbar-league-playoffs` / `topbar-league-finances` / `topbar-league-history` / `topbar-league-power_rankings` / `topbar-team-roster` / `topbar-team-schedule_team` / `topbar-team-finances_team` / `topbar-team-history_team` / `topbar-players-free_agents` / `topbar-players-trade` / `topbar-players-trading_block` / `topbar-players-prospects` / `topbar-players-watch_list` / `topbar-players-hall_of_fame` / `topbar-stats-game_log` / `topbar-stats-league_leaders` / `topbar-stats-player_ratings` / `topbar-stats-player_stats` / `topbar-stats-team_stats` / `topbar-stats-statistical_feats`. **The top Dashboard entry (`section="top", key="dashboard"`) of `top_bar_links` is filtered OUT of the regrouped iteration** — it surfaces only via the leading `dashboard-nav-link` icon, not in any dropdown; the template skips `section.grouper == "top"` inside the `{% for section in sections %}` loop, so no `topbar-top-dashboard` DOM id is emitted. Disabled-entry semantics: when `displayed_season is None` the helper emits Standings / Schedule entries with `url=None, disabled=True`; the template renders these as `<span class="dropdown-item disabled">` per the branch above, and the disabled `<span>` does NOT receive an `id` (the `topbar-{section}-{key}` id is only emitted on LIVE `<a>` elements — disabled entries have no DOM id and tests must not assert on them).

**Single source of truth.** `matches.views._build_league_sidebar_links` becomes the sole producer of the per-section entry list consumed by BOTH the LG-01f sidebar partial AND the LG-01k league-mode topbar — flipping a disabled→LIVE in the helper (e.g. LG-02 lights up Playoffs by changing the helper's index-3 entry from `url=None, disabled=True` to `url=reverse(...), disabled=False`) updates both surfaces at once with zero per-surface edit. LG-01k does NOT edit the helper itself; it is **read-only consumed** by the `league_nav` processor's new lazy import. The existing sidebar callers (`coming_soon`, `league_dashboard`, `season_dashboard`, etc.) keep their `sidebar_active=<literal>` callsites unchanged; only the new topbar consumer passes `sidebar_active=None`.

**Retired DOM ids and URL keys.** The following LG-01h DOM ids are RETIRED in LG-01k (they do not appear anywhere in the rewritten `base.html`): `leagues-nav-link` (was on the LG-01h League ▾ dropdown toggle in both sandbox and league branches; replaced by `league-nav-link` — note the dropped trailing `s`); `league-standings-topbar-link` (replaced by `topbar-league-standings`); `league-playoffs-topbar-link` (replaced by `topbar-league-playoffs`); `league-finances-topbar-link` (replaced by `topbar-league-finances`); `league-history-topbar-link` (replaced by `topbar-league-history`); `league-power-rankings-topbar-link` (replaced by `topbar-league-power_rankings` — note underscore not hyphen, matches the helper's `key="power_rankings"`). The following LG-01h **context keys** are RETIRED: `top_bar_history_url` / `top_bar_standings_url` / `top_bar_playoffs_url` / `top_bar_finances_url` / `top_bar_power_rankings_url`. **Every test, template, docstring, and CLAUDE.md reference to these 5 keys + 6 DOM ids is updated to the new shape.** The retired-id list is exhaustive — Tools / Help ids all stay verbatim, and the LG-01a `player-list-nav-link` on the sandbox Players link is preserved.

**Tests.** Live in **1 NEW file + 3 EXTENDED files** under `matches/tests/`. NEW: `test_topnav.py` (Django `TestCase`, classes `TestLg01kStartModeTopbar` covering `client.get("/")` ⇒ only `tools-nav-link` + `help-nav-link` + `mode-picker` sanity check + absence of every league/sandbox-only id + all 4 Tools child ids present + all 6 Help child ids present; `TestLg01kSandboxModeTopbar` covering `client.get("/teams/")` ⇒ 6 flat anchors by href + `player-list-nav-link` + `tools-nav-link` + `help-nav-link`, ABSENCE of `dashboard-nav-link` / `league-nav-link` / `team-nav-link` / `players-nav-link` / `stats-nav-link` / `leagues-nav-link` (retired), Tools-before-Help source-order check via string-index, plus `League ▾` substring absent from sandbox; `TestLg01kLeagueModeTopbar` covering `client.get(f"/leagues/{league.id}/")` ⇒ `dashboard-nav-link` present with `⌂` U+2302 character inside the anchor body and `href` resolving to `reverse("league_dashboard", kwargs={"league_id": league.id})` + all 4 section toggles + Tools / Help + absence of all flat sandbox anchors + at least one `topbar-{section}-{key}` id per section + `topbar-top-dashboard` substring ABSENT + the 6 retired LG-01h ids ABSENT). EXTENDED: `test_topnav.py` (existing class `TestAppModeContextProcessor` gains 3 new methods — `test_start_mode_for_exact_root_path` asserting `RequestFactory().get("/")` ⇒ `"start"`, `test_sandbox_mode_for_empty_path` asserting `.path = ""` ⇒ `"sandbox"` (empty string does NOT match the exact `/` rule), `test_sandbox_mode_for_missing_path_attribute` asserting a raw object with no `.path` attribute via `type("R", (), {})()` ⇒ `"sandbox"`; existing LG-01h tests on `/leagues/` / `/seasons/` / `/teams/` prefixes stay unchanged). EXTENDED: `test_league_nav_context_processor.py` — the 5 LG-01h test methods on the retired URL keys are DELETED and replaced with new methods covering the 2-key return shape — `top_bar_links` is a `list` of length 23 when a League exists / `[]` on fallback (zero Leagues, 2+ Leagues without session pin, broken transaction); `top_bar_dashboard_url` resolves to `reverse("league_dashboard", kwargs={"league_id": league.id})` when a League exists / `reverse("league_list")` on fallback; the 5 old keys are ABSENT from the return dict; the helper is called with `sidebar_active=None` (monkeypatched recording of the kwarg); the displayed-Season chain works (active → most-recent-completed → None); when `displayed_season is None` the returned `top_bar_links` still has 23 entries but Standings / Schedule entries are `url=None, disabled=True`; the top entry `[0]` has `section="top", key="dashboard"` (present in `top_bar_links` — it's the TEMPLATE that filters it from the regrouped iteration, not the processor). MINIMAL EDIT: `test_topnav.py` — any assertions referencing the retired ids (`leagues-nav-link`, `league-standings-topbar-link`, etc.) are deleted or updated to the new pattern; assertions on the 5 retired URL context keys are deleted; assertions on Tools / Help DOM ids stay verbatim. The LG-01h file is NOT replaced wholesale — leaving it alone preserves the LG-01h behavioural test history at that filename and avoids merge confusion; `test_topnav.py` is the new authority for topbar DOM assertions. Tests must NOT touch `simulate_scheduled_round` / `simulate_match` / `save_games` or any simulator entry point (LG-01k runs no simulation).

**Scope-out (locked).** No model change. No migration. No simulator touch. No RNG consumption. No `_flush_to_db` touch. No SIM-07 / SIM-08 contract interaction. **No Score Calibration re-baseline obligation** — LG-01k is a UI restructure; no simulation mechanics change. No new ADR (ADR-0017 is unchanged; the LG-01k modification is at the implementation layer, the LG-01h architectural decision still stands). No CONTEXT.md edit — `start` / `sandbox` / `league` are implementation enum values for topnav rendering, not domain language. No new dependency. No API / DRF endpoint. No `django.contrib.messages` flash. No admin change. No JS framework / htmx / Alpine / Stimulus / inline `<script>` blocks (Bootstrap 5 dropdown JS already in `base.html` is the only existing dep). No new template tag library, no new Django context processor beyond the existing 2. No edit to `templates/_partials/league_sidebar.html`. No edit to `matches.views._build_league_sidebar_links` (read-only consumed by both sidebar and topbar). No edit to any view function. No edit to any URL include file. No edit to `core/views.py` / `matches/views.py` / `settings.py` (the `TEMPLATES` context-processor registration list is unchanged — only the existing 2 entries are reused). No mode-toggle UI (mode is path-driven only). No multiplayer mode (deferred per ADR-0017 §1). No new placeholder views or `coming_soon_*` URL names — LG-01k strictly reuses the LG-01h URL names. No edit to the LG-01h `coming_soon` view / `_FEATURE_REGISTRY` / `templates/_placeholder.html`. No edit to the LG-01a `landing` view / `templates/core/landing.html`. No backfill.

**Locked names.** Context processor function names — `core.context_processors.app_mode` (signature unchanged, body extended 2→3 branches) + `core.context_processors.league_nav` (signature unchanged, body rewritten — drops 5 keys, adds 2 keys, return-type annotation widens from `dict[str, str]` to `dict[str, Any]`); context keys (NEW) — `top_bar_links` (`list[dict]`, 23 entries or `[]`) + `top_bar_dashboard_url` (`str`); context keys (RETIRED) — `top_bar_history_url` / `top_bar_standings_url` / `top_bar_playoffs_url` / `top_bar_finances_url` / `top_bar_power_rankings_url`; context key `app_mode` 3-value enum (LOCKED) — `"start"` (NEW) / `"league"` (preserved) / `"sandbox"` (preserved); helper consumed read-only — `matches.views._build_league_sidebar_links(league, displayed_season, sidebar_active=None)` (23-entry return, body unchanged from LG-01h); files modified — `laserforce_simulator/core/context_processors.py` + `laserforce_simulator/templates/base.html`; files new (test) — `laserforce_simulator/matches/tests/test_topnav.py`; files extended (test) — `laserforce_simulator/matches/tests/test_topnav.py` + `laserforce_simulator/matches/tests/test_league_nav_context_processor.py` + `laserforce_simulator/matches/tests/test_topnav.py` (minimal-edit removal of retired-id references); file new (optional partial, Code agent's discretion) — `laserforce_simulator/templates/_partials/topnav_tools_help.html`; DOM ids NEW (league mode only) — `dashboard-nav-link` + `league-nav-link` (replaces retired `leagues-nav-link`) + `team-nav-link` + `players-nav-link` + `stats-nav-link` + the 22 `topbar-{section}-{key}` ids listed above (`topbar-league-standings` / `topbar-league-schedule` / `topbar-league-playoffs` / `topbar-league-finances` / `topbar-league-history` / `topbar-league-power_rankings` / `topbar-team-roster` / `topbar-team-schedule_team` / `topbar-team-finances_team` / `topbar-team-history_team` / `topbar-players-free_agents` / `topbar-players-trade` / `topbar-players-trading_block` / `topbar-players-prospects` / `topbar-players-watch_list` / `topbar-players-hall_of_fame` / `topbar-stats-game_log` / `topbar-stats-league_leaders` / `topbar-stats-player_ratings` / `topbar-stats-player_stats` / `topbar-stats-team_stats` / `topbar-stats-statistical_feats`); DOM ids PRESERVED from LG-01h — `tools-nav-link` + `tools-achievements-topbar-link` / `tools-screenshot-topbar-link` / `tools-debug-mode-topbar-link` / `tools-reset-db-topbar-link` + `help-nav-link` + `help-overview-topbar-link` / `help-changes-topbar-link` / `help-custom-rosters-topbar-link` / `help-debugging-topbar-link` / `help-lol-gm-forums-topbar-link` / `help-zen-gm-forums-topbar-link`; DOM id PRESERVED from LG-01a — `player-list-nav-link` (sandbox mode only); DOM ids RETIRED — `leagues-nav-link` / `league-standings-topbar-link` / `league-playoffs-topbar-link` / `league-finances-topbar-link` / `league-history-topbar-link` / `league-power-rankings-topbar-link`; toggle text literals — `League ▾` / `Team ▾` / `Players ▾` / `Stats ▾` / `Tools ▾` / `Help ▾` (all trailing U+25BE) plus home-icon text content `⌂` (U+2302, HOUSE); DOM-id pattern locked — `topbar-{section}-{key}` (mirrors LG-01f `sidebar-{section}-{key}`); test classes — `TestLg01kStartModeTopbar` / `TestLg01kSandboxModeTopbar` / `TestLg01kLeagueModeTopbar` (NEW file) plus extensions to `TestAppModeContextProcessor` and `TestLeagueNavContextProcessor`. Seam contract: [`.claude/worktrees/lg-01k-seam-contract.md`](../../.claude/worktrees/lg-01k-seam-contract.md).

## LG-01j per-Season arena map configuration

**Per-Season arena map configuration** with TWO ship modes `single` (one fixed `ArenaMap` for every Round of the Season) and `random_per_round` (deterministic per-Round draw from a pool by fixture identity) alongside the preserved `none` default (3-zone fallback — the LG-01d behaviour today, with no `arena_map` attached to the Round). The user picks the mode at create-League time only — the LG-01b `CreateLeagueForm` gains 2 new fields and the LG-01b view body materialises the M2M in the same `@transaction.atomic` block; mid-League edits are admin-only via Django admin (no edit URL, no edit view, no edit form, no edit template ships). LG-01e (Start Next Season) carries the previous Season's map configuration forward verbatim from the FROZEN SNAPSHOT — not the live M2M. The two LG-01d play paths (`play_season_task` async + `play_week` sync) resolve each Round's `arena_map` from the snapshot via a new module-level helper `matches.tasks._resolve_fixture_map(season, fixture, pool_by_id) -> ArenaMap | None` and pass the resolved value through the already-supported `BatchSimulator.simulate_scheduled_round(..., arena_map=…)` kwarg (SIM-09). Dashboards (League + Season, LG-01c) render a read-only `map_config_label` string showing the active configuration. **The third "per-sub-league rotation" mode is deferred to SUB-01 post-CAR-03** (no third enum value reserved at LG-01j — when SUB-01 lands, a new migration will add it). **No model change beyond the 3 NEW `Season` fields, no simulator touch, no RNG churn on the simulator's seed chain, no `_flush_to_db` touch, no SIM-07 / SIM-08 contract interaction, no Score Calibration re-baseline obligation triggered by LG-01j alone (folds into the existing pending post-MOVE-01 re-baseline alongside MOVE-02 / MOVE-03 / MOVE-04 / SIM-09), no new ADR (the decisions are reversible — model fields + a deterministic helper, with the existing CONTEXT.md domain language extension covering the vocabulary).**

**Model fields.** `Season` grows 3 NEW fields via migration `matches/migrations/0031_season_map_mode_pool.py` (next sequential after the latest LG-01x migration, pure schema — no data-migration step needed): (1) `map_mode: models.CharField(max_length=32, choices=[("none", "3-zone fallback"), ("single", "Single map"), ("random_per_round", "Random per Round")], default="none")` — no `db_index` (low cardinality, not filter-keyed); (2) `map_pool: models.ManyToManyField("core.ArenaMap", blank=True, related_name="seasons_using_pool")` — string ref to avoid circular import, `blank=True` so empty pool is valid at the ORM level (form / `save_related` enforces mode-vs-pool rules), reverse accessor `arena_map.seasons_using_pool` LOCKED; (3) `starting_map_pool_ids_json: models.JSONField(null=True, blank=True, default=None)` — mirrors the LG-01 `starting_team_ids_json` snapshot signature verbatim, `None` pre-activation, `[]` activated-with-no-maps, `[id1, id2, …]` activated-with-maps (sorted ascending by `ArenaMap.id` for determinism on re-activation of a re-drafted Season).

**`Season.clean()` extension.** PRESERVES the LG-01-locked `≤1 non-completed Season per League` invariant verbatim. APPENDS a defensive `map_mode ∈ {"none", "single", "random_per_round"}` enum check raising `ValidationError({"map_mode": "Unknown map mode."})` for unknown values (defensive against admin-side raw assignments — Django's `CharField.choices` already enforces this on `full_clean()`, but model-level explicit check guards the admin path). **M2M pool-count rules are NOT enforced in `Model.clean()`** — M2M rows aren't visible to `Model.clean()` (they exist only after `save()`). The mode-vs-pool-count rules live form-side (`CreateLeagueForm.clean()`) only.

**`Season.start_season()` extension.** PRESERVES the existing `@transaction.atomic` decorator, draft→active flip, `<2 teams` guard, `starting_team_ids_json` snapshot, and return shape — every existing LG-01 contract is preserved verbatim. APPENDS ONE line AFTER the existing `starting_team_ids_json` snapshot and BEFORE the existing `self.save()`: `self.starting_map_pool_ids_json = sorted([m.id for m in self.map_pool.all()])`. Locked algorithm details: sorted **ascending by `id`** (determinism — re-activation of a re-drafted Season yields identical snapshot); empty pool ⇒ `[]` NOT `None` (`None` is reserved for pre-activation; `[]` is "activated with no maps" — the two are deliberately distinct); single ORM query (`self.map_pool.all()` evaluates once into the list comprehension); snapshot happens inside the existing `@transaction.atomic` block so partial-failure rolls back the snapshot too.

**`CreateLeagueForm` extension (LG-01b).** `matches.forms.CreateLeagueForm` is EXTENDED in-place — **NOT renamed, NOT converted to a `ModelForm`**. The existing 7 fields (`league_name` / `season_name` / `start_date` / `num_teams` / `schedule_format` / `mean` / `std_dev`) remain unchanged in name, type, order, and `clean_*` validation. APPENDS 2 NEW fields at the END of the field declaration block: (1) `map_mode = forms.ChoiceField(choices=Season._meta.get_field("map_mode").choices, initial="none", required=True, label="Map mode")` — pulls choices from the model field (single source of truth), initial `"none"` matches the model default, `required=True` (no blank choice); (2) `map_pool = forms.ModelMultipleChoiceField(queryset=_maps_with_confirmed_config(), required=False, label="Map pool")` — `required=False` because mode `none` accepts empty pool, REUSES the existing module-level helper `matches.forms._maps_with_confirmed_config()` verbatim (also used by `MatchSetupForm` / `SingleRoundSetupForm`; returns only `ArenaMap` rows with at least one confirmed `MapZoneConfig` — half-built maps excluded), default `forms.SelectMultiple` widget (no JS framework — matches LG-01h scope-out). **Final field order, in pinned position**: `league_name` (1) → `season_name` (2) → `start_date` (3) → `num_teams` (4) → `schedule_format` (5) → `mean` (6) → `std_dev` (7) → `map_mode` (8) → `map_pool` (9). Total **9 fields**.

**`CreateLeagueForm.clean()` extension.** The existing `clean()` body is PRESERVED verbatim. APPENDS 3 new mode-vs-pool rules raising `ValidationError({"map_pool": "…"})` — errors attach to `map_pool` (NOT `map_mode`) so help text is co-located with the field the user clicked wrong. Locked error message strings (byte-equal): (1) `mode == "none" and len(pool) > 0` ⇒ `"Map pool must be empty when Map mode is '3-zone fallback'."`; (2) `mode == "single" and len(pool) != 1` ⇒ `"Map pool must contain exactly 1 map when Map mode is 'Single map'."`; (3) `mode == "random_per_round" and len(pool) < 1` ⇒ `"Map pool must contain at least 1 map when Map mode is 'Random per Round'."`. Defensive read inside `clean()`: `mode = cleaned_data.get("map_mode")` and `pool = cleaned_data.get("map_pool") or []` — when `map_mode` failed its own field-level validation (`mode is None`), skip the cross-field rules early.

**`league_create` view extension (LG-01b).** `matches.views.league_create` is EXTENDED in-place — **NOT renamed**. The existing body (form rendering, POST validation, `@transaction.atomic` block, `Season.objects.create(...)`, `season.teams.add(*created_teams)`, redirect to `season_standings`) is PRESERVED verbatim, including the LG-01g `league.current_team` auto-set between `League.objects.create(...)` and `Season.objects.create(...)`. Two body edits inside the existing `@transaction.atomic`: (1) the `Season.objects.create(...)` call also passes `map_mode=cleaned["map_mode"]` (or assigns post-create — Code agent's discretion; tests assert `season.map_mode == cleaned["map_mode"]`); (2) AFTER the existing `season.teams.add(*created_teams)` line, append `season.map_pool.set(cleaned["map_pool"])` — materialises the M2M rows in the same atomic block. Redirect target (`season_standings` with `kwargs={"season_id": season.id}`) and method-allow guard (`if request.method != "POST"` rendering the form) are UNCHANGED. The view decorator stays the LG-01b-locked form (no `@login_required` / `@require_http_methods` added — preservation of the existing decorator stack is part of the locked seam).

**`next_season` view extension (LG-01e).** `matches.views.next_season` is EXTENDED in-place — **NOT renamed**. The existing body (`@transaction.atomic`, locate `latest_completed`, create `new_season` with `schedule_format=latest_completed.schedule_format` carry-forward, `new_season.teams.add(*teams_qs)`, redirect to `season_dashboard`) is PRESERVED verbatim, including the existing `latest_completed is None` guard ("No completed Season"). Inside the existing `@transaction.atomic` body, AFTER the existing `new_season.teams.add(*teams_qs)` line, in pinned order: (1) `new_season.map_mode = latest_completed.map_mode` (verbatim carry-forward — mirrors the existing `schedule_format` pattern); (2) `pool_ids = latest_completed.starting_map_pool_ids_json or []` — read from the FROZEN SNAPSHOT, NOT the live `latest_completed.map_pool` M2M (the live M2M may have drifted via admin edits; the snapshot is what the Season ACTUALLY played with — and is the source of truth post-activation); (3) `new_season.map_pool.set(ArenaMap.objects.filter(id__in=pool_ids))` — defensive: deleted-after-activation maps silently drop out of the queryset; (4) `new_season.save()` (the carry-forward assignments need persistence; the existing `next_season` body may already `save()` once — Code agent ensures a single `save()` covers all field assignments). Redirect target (`season_dashboard`) is UNCHANGED.

**`map_config_label` on `_build_dashboard_context` (LG-01c).** `matches.views._build_dashboard_context(displayed_season, season_mode) -> dict` (LG-01c-locked helper) grows from **11 to 12 context keys** — the existing 11 keys are PRESERVED verbatim; ONE new key `map_config_label: str` is computed before the return. Locked 4-branch `if/elif/else` ladder in precedence order: (1) `displayed_season is None` OR `season_mode == "none"` (the LG-01c `season_mode` distinguishes "no season picked" from "showing this Season" — distinct from `Season.map_mode`) ⇒ `"Map: 3-zone fallback (no map)"`; (2) `displayed_season.map_mode == "none"` ⇒ `"Map: 3-zone fallback (no map)"`; (3) `displayed_season.map_mode == "single"` ⇒ resolve `pool_ids = displayed_season.starting_map_pool_ids_json or []` for active / completed, live M2M for draft; if non-empty, `map_obj = ArenaMap.objects.filter(id=pool_ids[0]).first()`; if `map_obj is not None` ⇒ `f"Map: Single — {map_obj.name}"` (em-dash U+2014, single SPACE on both sides); pool empty OR map missing ⇒ `"Map: Single — (map deleted)"`; (4) `displayed_season.map_mode == "random_per_round"` ⇒ resolve `pool_ids` as above; `maps = ArenaMap.objects.filter(id__in=pool_ids).order_by("name")` — sorted by NAME ascending (NOT id); `names = [m.name for m in maps]`; `len(names) == 0` ⇒ `"Map: Random per Round (no maps)"`; else ⇒ `f"Map: Random per Round ({len(names)} maps: {', '.join(names)})"`. **5 locked label string literals (byte-equal, em-dash U+2014)**: `"Map: 3-zone fallback (no map)"` / `"Map: Single — Alpha Arena"` / `"Map: Single — (map deleted)"` / `"Map: Random per Round (3 maps: Alpha Arena, Bravo Arena, Charlie Arena)"` / `"Map: Random per Round (no maps)"`.

**`_resolve_fixture_map` helper (NEW).** Module-level flat helper (NO class) in `matches/tasks.py`: `def _resolve_fixture_map(season: "Season", fixture: "ScheduleFixture", pool_by_id: dict[int, "ArenaMap"]) -> "ArenaMap | None"`. Locked 4-branch body algorithm: (1) `mode = season.map_mode`; (2) `mode == "none"` ⇒ return `None`; (3) `mode == "single"` ⇒ `pool_ids = season.starting_map_pool_ids_json or []`; if `not pool_ids` ⇒ return `None` (defensive); otherwise `chosen_id = pool_ids[0]` and return `pool_by_id.get(chosen_id)` (`None` if admin deleted the row after activation); (4) `mode == "random_per_round"` ⇒ `pool_ids = season.starting_map_pool_ids_json or []`; if `not pool_ids` ⇒ return `None`; build seed `seed_str = f"{season.id}|{fixture.matchday}|{fixture.round_number}|{fixture.team_a_id}|{fixture.team_b_id}"` (LOCKED format — 5 components, pipe-separated, no spaces, exact order); `rng = random.Random(seed_str)` (a fresh `Random` per fixture — does NOT share state with the simulator's RNG); `chosen_id = rng.choice(pool_ids)` (`pool_ids` is already sorted ascending from `start_season()` so the choice is deterministic); return `pool_by_id.get(chosen_id)`; (5) unknown `mode` ⇒ `raise ValueError(f"Unknown map_mode: {mode!r}")`. **Purity**: NO Django ORM access inside the helper — the view does `ArenaMap.objects.in_bulk(...)` upfront and passes `pool_by_id` in. The helper consumes only `season.id` / `season.map_mode` / `season.starting_map_pool_ids_json` (duck-typed — works against any object with those 3 attributes) + a `ScheduleFixture`-shaped object exposing `.matchday` / `.round_number` / `.team_a_id` / `.team_b_id` + a `dict[int, ArenaMap]`. This makes the helper pure unit-testable with `@dataclass` stubs and zero DB. **Locked location**: `matches/tasks.py` NOT `matches/season_dashboard.py` (the latter has a frozen no-Django-import allowlist from LG-01h scope-out; `matches/tasks.py` already imports Django + Celery and is the natural home for batch-simulation glue).

**`play_season_task` body extension (LG-01d, `matches/tasks.py`).** EXTENDED in-place — **NOT renamed**. The existing body (load `season`, build `to_play` list of `ScheduleFixture` rows, per-fixture `BatchSimulator().simulate_scheduled_round(...)` call, deferred imports, `MatchJob` progress writes) is PRESERVED verbatim, including the existing `@shared_task` decorator and exception handling. Body changes in pinned order: (1) add a deferred import alongside the existing block: `from core.models import ArenaMap`; (2) AFTER loading `season` and BEFORE the per-fixture loop, resolve the map pool ONCE — `pool_ids = season.starting_map_pool_ids_json or []` then `pool_by_id: dict[int, ArenaMap] = ArenaMap.objects.in_bulk(pool_ids)` (single ORM query regardless of `len(to_play)`, OUTSIDE the per-fixture loop); (3) INSIDE the per-fixture loop, BEFORE the existing `BatchSimulator().simulate_scheduled_round(...)` call, resolve the map: `arena_map = _resolve_fixture_map(season, fixture, pool_by_id)`; (4) pass through to the simulator (only the new kwarg is added; all other args UNCHANGED): `BatchSimulator().simulate_scheduled_round(season, team_a, team_b, fixture.round_number, arena_map=arena_map)`. The `BatchSimulator.simulate_scheduled_round` signature already accepts `arena_map: ArenaMap | None = None` per SIM-09 — **NO simulator edit is needed**.

**`play_week` synchronous path (LG-01d).** `matches.views.play_week` (or the actual function name if drifted — Code agent locates by behaviour) MIRRORS the `play_season_task` body changes — same deferred import `from core.models import ArenaMap`, same ONE-OFF `pool_by_id` resolution OUTSIDE the inline `with transaction.atomic():` block (or at the top of the view body before the atomic block — Code agent's discretion, as long as the bulk fetch happens once per request not per fixture), same per-fixture `arena_map = _resolve_fixture_map(season, fixture, pool_by_id)` call using the SAME helper as `play_season_task`, same `arena_map=arena_map` kwarg passed through to `simulate_scheduled_round`. The redirect target, matchday-advance logic, and `@transaction.atomic` boundary are UNCHANGED.

**Templates (MODIFIED, 3, 0 NEW).** (1) `templates/leagues/dashboard.html` — render `{{ map_config_label }}` inside `<div id="league-dashboard-map-config" class="text-muted small mt-1">…</div>`, placed IMMEDIATELY UNDER the LG-01f-locked DOM id `league-dashboard-action-button` and IMMEDIATELY ABOVE the LG-01c-locked DOM id `league-dashboard-standings-snippet`; (2) `templates/seasons/dashboard.html` — render `{{ map_config_label }}` inside `<div id="season-dashboard-map-config" class="text-muted small mt-1">…</div>`, placed IMMEDIATELY UNDER `season-dashboard-action-button` and IMMEDIATELY ABOVE `season-dashboard-standings-snippet`; (3) `templates/leagues/create.html` — render TWO new field rows for `{{ form.map_mode }}` + `{{ form.map_pool }}`, each with a `<label>` and a per-field error block, placed AFTER the existing `team_stat_std_dev` field row and BEFORE the submit button — DOM id `league-create-map-mode` on the `<select>` for `map_mode` and `league-create-map-pool` on the `<select multiple>` for `map_pool` (tests assert these DOM ids exist, NOT the exact rendering path — Code agent may render via raw `<select>` markup or via `{{ form.map_mode }}` with a widget-attrs `id` override).

**Admin change (`matches/admin.py`).** ONE edit only: `SeasonAdmin.filter_horizontal` extends from `("teams",)` to `("teams", "map_pool")`. `map_mode` surfaces via the default model-form render with NO `SeasonAdmin` edit (it auto-appears as a `<select>` for the `choices`-bearing field). No other admin change at LG-01j — `LeagueAdmin` / `TeamAdmin` / `ArenaMapAdmin` are UNCHANGED.

**Scope-out (locked).** **No edit URL after create** (mid-League changes are admin-only via the `SeasonAdmin` `filter_horizontal` extension — the ONLY admin change). **No mode (b) per-sub-league rotation** (deferred to SUB-01 post-CAR-03 — already in PLAN.md; no third enum value reserved at LG-01j; SUB-01 will add it via a new migration). **No new ADR** (decisions are reversible — model fields + a deterministic helper; CONTEXT.md domain language extension covers the vocabulary). **No `score_averages` / batch-sim path change** (LG-01j is league-only — `score_averages` consumes `BatchSimulator.run` directly, not `play_season_task` / `play_week`, and is unaffected). **No `master_seed` UI exposure** (the per-fixture seed is computed internally; no user-facing seed input). **No edit to LG-01f sidebar / LG-01h top-bar** (no new placeholder URLs ship at LG-01j; the sidebar 23-entry shape + top-bar dropdowns from LG-01h are preserved verbatim). **No simulation mechanics change** ⇒ **no Score Calibration re-baseline obligation triggered by LG-01j alone** (folds into the existing pending post-MOVE-01 re-baseline alongside MOVE-02 / MOVE-03 / MOVE-04 / SIM-09). **No edit to `simulate_scheduled_round` / `simulate_match` / `_flush_to_db`** (the simulator already supports the `arena_map=` kwarg per SIM-09). **No edit to `select_play_fixtures` / `find_next_matchday` / `matches/season_dashboard.py` pure module** (the frozen no-Django import allowlist in `season_dashboard.py` is preserved — the helper lives in `matches/tasks.py`). **No `Season.archive` / edit-draft UI** (LG-01j inherits the existing draft-edit story). **No edit to `LeagueAdmin`** (the new fields surface via the default `SeasonAdmin` form-render). **No API / DRF endpoint** (LG-01j is pure server-rendered). **No edit to `MatchSetupForm` / `SingleRoundSetupForm`** (sandbox flows already have an `arena_map` field — unchanged). **No backfill** (pre-LG-01j Seasons take the `map_mode="none"` default + empty pool + `None` snapshot, yielding 3-zone fallback at simulation time — the LG-01d behaviour preserved). **No `django.contrib.messages` flash** (creation success redirects to `season_standings` per LG-01b, no flash). **No JS framework / htmx / Alpine / inline `<script>`** (LG-01h scope-out preserved; default `SelectMultiple` widget is fine for the small confirmed-map list). **No new dependency**. **No new CONTEXT.md section** (3 entries appended to the existing `### League and seasons` section, NOT a new section). **No edit to `MatchJob` model** (the task body change is internal to `play_season_task`; job progress tracking is unaffected).

**Locked names.** Model fields (3 NEW on `Season`) `map_mode` (CharField, choices, default `"none"`, max_length 32) / `map_pool` (M2M to `ArenaMap`, blank=True, related_name `"seasons_using_pool"`) / `starting_map_pool_ids_json` (JSONField, null=True, default=None); mode enum literals `"none"` (display `"3-zone fallback"`) / `"single"` (display `"Single map"`) / `"random_per_round"` (display `"Random per Round"`); migration filename `matches/migrations/0031_season_map_mode_pool.py`; NEW helper `matches.tasks._resolve_fixture_map(season, fixture, pool_by_id) -> ArenaMap | None` (module-level, no class, pure — no ORM); form fields (2 NEW on `CreateLeagueForm`) `map_mode` (`forms.ChoiceField`, choices from model field, initial `"none"`, required=True) / `map_pool` (`forms.ModelMultipleChoiceField`, queryset `_maps_with_confirmed_config()`, required=False); form helper REUSED `matches.forms._maps_with_confirmed_config() -> QuerySet[ArenaMap]` (already exists — no edit, no redefinition); EXTENDED views `matches.views.league_create` / `matches.views.next_season` / `matches.views._build_dashboard_context` (body extensions only — no rename, no signature change); EXTENDED tasks `matches.tasks.play_season_task` / `matches.views.play_week` (body extensions only — same `@shared_task` decorator, same name); template names (3 MODIFIED, 0 NEW) `templates/leagues/dashboard.html` / `templates/seasons/dashboard.html` / `templates/leagues/create.html`; DOM ids (4 NEW) `league-dashboard-map-config` / `season-dashboard-map-config` / `league-create-map-mode` / `league-create-map-pool`; label string literals (5 locked — byte-equal, em-dash U+2014) `"Map: 3-zone fallback (no map)"` / `"Map: Single — {name}"` (e.g. `"Map: Single — Alpha Arena"`) / `"Map: Single — (map deleted)"` / `"Map: Random per Round ({n} maps: {comma_joined_names})"` (e.g. `"Map: Random per Round (3 maps: Alpha, Bravo, Charlie)"`) / `"Map: Random per Round (no maps)"`; form `clean()` error messages (3 locked — byte-equal) `"Map pool must be empty when Map mode is '3-zone fallback'."` / `"Map pool must contain exactly 1 map when Map mode is 'Single map'."` / `"Map pool must contain at least 1 map when Map mode is 'Random per Round'."`; `_resolve_fixture_map` `ValueError` message `f"Unknown map_mode: {mode!r}"`; seed-string format (byte-locked) `f"{season.id}|{fixture.matchday}|{fixture.round_number}|{fixture.team_a_id}|{fixture.team_b_id}"` (5 components, pipe `|`-separated, no spaces, exact order); context key (NEW on `_build_dashboard_context`) `map_config_label: str` (12th key on the existing 11-key dict); CONTEXT.md term names (3 NEW) `Map mode` / `Map pool` / `Per-fixture map resolution` (appended to `### League and seasons` section after the existing `Team schedule` entry, in that order); test files (2 NEW) `matches/tests/test_season_map_config.py` + `matches/tests/test_season_map_config.py`; test files (5 EXTENDED) `matches/tests/test_league_create.py` + `test_league_next_season.py` + `test_league_play.py` + `test_league_dashboard.py` + `test_season_dashboard_view.py`; test classes (NEW) `TestSeasonMapModeField` / `TestSeasonMapPoolField` / `TestSeasonStartingMapPoolSnapshot` / `TestSeasonStartSeasonSnapshotsMapPool` (in `test_season_map_config.py`); `TestResolveFixtureMapNone` / `TestResolveFixtureMapSingle` / `TestResolveFixtureMapRandomPerRound` / `TestResolveFixtureMapUnknownMode` / `TestResolveFixtureMapMissingMap` (in `test_season_map_config.py`); `TestLeagueCreateMapMode` / `TestLeagueCreateMapPool` (in `test_league_create.py`); `TestNextSeasonMapConfigCarryForward` (in `test_league_next_season.py`); `TestPlaySeasonTaskMapResolution` (in `test_league_play.py`); `TestLg01jLeagueDashboardMapConfig` (in `test_league_dashboard.py`); `TestLg01jSeasonDashboardMapConfig` (in `test_season_dashboard_view.py`); admin change (only one) `matches/admin.py` `SeasonAdmin.filter_horizontal` extends from `("teams",)` to `("teams", "map_pool")`; M2M reverse-accessor name `arena_map.seasons_using_pool` (locked via `related_name="seasons_using_pool"` on `Season.map_pool`). Seam contract: [`.claude/worktrees/lg-01j-seam-contract.md`](../../.claude/worktrees/lg-01j-seam-contract.md).

## LG-01z league sidebar screens

Turns 17 of the LG-01h "coming soon" league-sidebar placeholders into real
pages: **11 live read-only screens** + **6 explainer stubs** for features
still blocked on unbuilt models. All read-only — **no model change, no
migration, no simulator touch, no RNG, no Score Calibration re-baseline.**
Per-screen status + blockers in [`sub-plan.md`](../../sub-plan.md); seam
contract at [`.claude/worktrees/lg-01z-seam-contract.md`](../../.claude/worktrees/lg-01z-seam-contract.md).

**Package `matches/league_screens/`** (NEW) — one module per screen, each
exposing a single GET-only view `<key>(request, league_id)` re-exported from
the package `__init__`. The 11 views: `power_rankings`, `team_roster`,
`team_history`, `free_agents`, `watch_list`, `game_log`, `league_leaders`,
`player_ratings`, `player_stats`, `team_stats`, `statistical_feats`. Each
follows the shared contract: GET-guard (`HttpResponseNotAllowed(["GET"])`) →
`get_object_or_404(League)` → `request.session["last_league_id"] = league.id`
→ `displayed_season = league.active_season or league.seasons.filter(
state="completed").order_by("-id").first()` → `sidebar_links =
_build_league_sidebar_links(league, displayed_season, sidebar_active="<key>")`
→ aggregation → render `templates/leagues/<key>.html` (the `d-flex` +
`_partials/league_sidebar.html` shell). Empty-state (no Season) renders a
`<key>-empty-notice` with the substring `"No Season"`. **Watch List** is the
one exception to GET-only: a `?action=add|remove&player_id=<id>` GET toggle
mutates `request.session["watch_list"]` (a list of int ids) then redirects to
the bare URL — a browser-session-local convenience list (no model, migrates to
per-user when UX-01 lands).

**Pure logic modules** (NEW, top-level `matches/`, frozen `dataclasses` /
`typing` / `collections` import allowlist, each with a `TestNoDjangoImportsLeaked`
subprocess check): `power_rankings_logic.py` (composite power score = sum of
three per-League min-max-normalized components — team mean `overall_rating`,
win%, avg per-Round score diff; `compute_power_rankings` + `sort_power_rankings`
with `SORT_KEYS` for the sortable columns, rank preserved as the canonical
power-score position), `league_leaders_logic.py` (`compute_leaderboards` → 4
top-N boards: avg tags / avg score / fewest-tagged / tag ratio; reuses the
LG-01c `LeaderRow`), `season_player_stats.py` (per-player HX-01 STAT_KEYS
aggregation — counts summed, `mvp`/`accuracy` averaged), `team_stats_logic.py`
(`aggregate_team_stats` over per-round + per-event dicts; event→column mapping
`base_capture`/`missiled`(+`hit`)/`special`-nuke-detonation/`nuke_cancelled`),
`team_history_logic.py` (`compute_overall_record` / `compute_season_rows` /
`compute_player_rollups` — green = currently on team, blue = now elsewhere,
derived from `PlayerRoundState` history vs current `Player.team`), and
`stat_feats.py` (9 feat predicates → `FeatRecord`s, deep-linking to the Round).
Screens with trivial aggregation (Game Log, Player Ratings, Free Agents, Team
Roster, Watch List) keep their logic in the view and reuse the LG-00c sort
helpers (`teams.views._coerce_sort` / `_coerce_dir` / `_SORT_KEYS`).

**Central wiring** (`matches/league_urls.py` + `matches/league_views.py::
_build_league_sidebar_links` + `matches/views.py::_FEATURE_REGISTRY`): each
live screen gets a real `path("<int:league_id>/…/", league_screens.<view>,
name="<url_name>")` route (URL names `league_power_rankings`, `team_roster`,
`team_history`, `players_free_agents`, `players_watch_list`, `stats_game_log`,
`stats_league_leaders`, `stats_player_ratings`, `stats_player_stats`,
`stats_team_stats`, `stats_statistical_feats`); the sidebar builder's entry is
repointed from `_cs("coming_soon_<x>")` to `_cs("<url_name>")` — and because
`_build_league_sidebar_links` is the **single source of truth** for both the
LG-01f sidebar AND the LG-01k topbar, both surfaces flip live at once. The 11
now-live `coming_soon_*` routes + `_FEATURE_REGISTRY` entries are removed,
leaving the registry at **17 entries** (7 still-blocked LEAGUE/TEAM/PLAYERS
placeholders + 6 Help + 4 Tools). The 7 blocked placeholder entries gain a
`blocker` string rendered by `coming_soon` into `_placeholder.html` (DOM id
`coming-soon-blocker`).

**Tests:** `matches/tests/test_league_<screen>.py` per screen (view 200 / 405 /
404 / session-write / empty-state / DOM ids + sort/toggle behaviour, plus
pure-unit + purity classes for the logic modules). The LG-01h
`test_coming_soon.py` (registry now 17, value-dicts may carry `blocker`)
and `test_league_sidebar.py` (6 entries point at live URLs) were updated as
blast-radius. A pre-existing RNG-flaky `test_batch_sim.py::TestBugfixMedicHits
Tracked` (unseeded 95%-hit) was pinned deterministically with the file's own
`patch("random.randint", return_value=1)` idiom.

**LG-06a polish.** Free Agents / Player Ratings / Player Stats (already
paginated view-side) gained the 10/25/50/100 page-size `<select>` (LG-01f
`history.html` precedent) via a `per_page_options` context key fed from the
shared `_LG01F_PER_PAGE_OPTIONS`; Team History gained `Paginator` wiring on the
**Players section only** (`page_obj` / `paginator` /
`players_querystring_without_page`, carries `team_id` + omits `page`), Overall +
Seasons untouched. The per-page `<form>` preserves other params via hidden
inputs (`sort`+`dir`, or `team_id` on Team History) and omits `page` to reset to
page 1; new DOM ids `<screen>-per-page-form` / `-select` +
`team-history-players-pagination`. UI-only, no model/simulator change. Seam
contract: [`.claude/worktrees/lg-06a-seam-contract.md`](../../.claude/worktrees/lg-06a-seam-contract.md).

**LG-06b polish.** Player Ratings / Player Stats / Statistical Feats gained an
"All Teams" + per-enrolled-team `?team_id=<id>` `<select>` via a shared
validator `league_views._coerce_team_id(raw, enrolled_ids)` (mirrors
`_coerce_per_page`; the single source for all three) returning the int id iff it
parses **and** is enrolled, else `None` (All Teams). Each view adds
`enrolled_teams` (`displayed_season.teams.order_by("name")`) + `selected_team_id`.
Filter points differ: Ratings filters the queryset (`qs.filter(team_id=…)` after
`_enrolled_player_queryset`), Stats filters rows post-`aggregate_player_stats` on
`PlayerStatRow.team_id`, Feats filters the seam **inputs** before
`stat_feats.scan_feats` (`player_rounds` by `team_id`, `matches` by red/blue id)
— `stat_feats.py` untouched. `team_id` carries in both querystring helpers + a
hidden per-page-form input on the two paginated screens (picker form omits
`page`); Feats has no pagination/sort. DOM ids
`{player-ratings,player-stats,statistical-feats}-team-filter-{form,select}`.
UI-only, read-only, no model/simulator change. Seam contract:
[`.claude/worktrees/lg-06b-seam-contract.md`](../../.claude/worktrees/lg-06b-seam-contract.md).

**LG-06c polish.** Team History / Game Log / League Leaders / Watch List /
Statistical Feats gained the LG-00c sortable-column-header pattern, sorting
**view-side** with in-memory `sorted(key=…, reverse=(dir=="desc"))` on the
already-materialized rows — the pure modules `stat_feats.py` /
`team_history_logic.py` / `league_leaders_logic.py` (incl. `LeaderRow`, whose
`rank` stays the frozen metric standing) are untouched, sorted on their OUTPUT.
The single new shared helper `league_views._coerce_sort_key(raw, allowed,
default)` (returns `raw` iff in the `allowed` frozenset, else `default`; mirrors
`_coerce_per_page` / `_coerce_team_id`) is the sole source of sort-key
coercion; `teams.views._coerce_dir` is imported and reused verbatim. Multi-table
screens namespace their params so one table never resets a sibling — Team
History (`players_sort`/`players_dir`, `seasons_sort`/`seasons_dir`) and League
Leaders (per-board `<board>_sort`/`<board>_dir` across the four
`avg_tags`/`avg_score`/`fewest_tagged`/`tag_ratio` boards) — while single-table
screens (Game Log, Watch List, Statistical Feats) use one `?sort=&dir=`. On the
LG-06a-paginated Team History Players table the sort runs BEFORE `Paginator`
(global top row leads), with the extended `players_querystring_without_page`
carrying `players_sort`/`players_dir` on pagination links and a sibling
`players_querystring_without_sort_page` backing the headers so a sort change
resets to page 1. Sort coexists with the existing `?team_id=` filters on Game
Log + Statistical Feats; Team History's Overall `dl` stays unsorted. Key tuples
are `None`-safe with a per-screen secondary tiebreak; new DOM ids
`<screen>[-<table>]-th-<key>` with ` ↑`/` ↓` glyphs on the active header.
UI-only, read-only, no model/simulator change. Seam contract:
[`.claude/worktrees/lg-06c-seam-contract.md`](../../.claude/worktrees/lg-06c-seam-contract.md).

**LG-06d polish.** Player Stats / Team Stats / League Leaders / Statistical Feats
/ Game Log / Power Rankings gained a `?season=` selector listing each of this
League's Seasons newest-first plus a **Career** entry (aggregate across all of
THIS League's Seasons); no `?season=` param keeps the current `displayed_season`
(backward-compatible). Two new shared coercers in `league_views.py` mirror the
`_coerce_per_page` / `_coerce_team_id` forgiving precedent:
`_coerce_season(raw, valid_season_ids, default)` (returns the literal `"career"`
sentinel iff `raw == "career"`, else the int id iff it parses **and** is in the
valid set, else the caller's `default` = the `displayed_season` id or `None`) and
`_coerce_rate(raw, default="total")` (returns one of the locked literals
`"total"` / `"per_game"` / `"per_10"`, else default). Career is a **view-side
queryset switch** — each screen swaps its round/match filter from
`...match__season=<season>` to `...match__season__league=league` and reuses its
existing pure aggregation module **verbatim** (`aggregate_player_stats`,
`team_stats_logic.aggregate_team_stats`, `league_leaders_logic.compute_leaderboards`,
`stat_feats.scan_feats`, the Game Log in-view round-row build,
`power_rankings_logic.compute_power_rankings` are all indifferent to one-season
vs. all-seasons, consuming a flat list of per-Round/per-Match dicts). Player
Stats additionally gained a `?rate=` toggle (Totals / Per Game / **Per 10 min** —
the laser-tag analogue of ZenGM's Per-36) via a new pure fn
`season_player_stats.apply_rate(rows, rate)` that transforms the summed count
columns **only** (`SUMMED_KEYS`); MVP / Acc% / Tag Ratio / Survival
(`AVERAGED_KEYS` + `DERIVED_KEYS`) and `games` pass through untouched. Per-10
denominator = the player's total uptime, `stats["survival"] * games` (survival is
the per-Round mean survival-seconds, so ×games rebuilds summed uptime), i.e.
`value * 600 / total_uptime_seconds` with a `<= 0` → `0.0` guard; per-game =
`value / games`. The Player Stats pipeline is `aggregate_player_stats` →
`apply_rate` → `team_id` filter → `sort_player_stats` → `Paginator`, so sorting
runs on the **rate-adjusted** displayed value (ZenGM behaviour). `season` (and
`rate` on Player Stats) is carried through every querystring helper, hidden
per-page / team-filter form input, and sort-header href on the touched screens;
changing `season` or `rate` omits `page` to reset to page 1 (LG-06a/b/c
precedent). New DOM ids `<screen>-season-filter-{form,select}` (prefixes
`player-stats`, `team-stats`, `league-leaders`, `statistical-feats`, `game-log`,
`power-rankings`) plus `player-stats-rate-{form,select}`. **Team History is
excluded** — it is natively all-time and its Seasons tab already is the
per-season view, so a season selector would be redundant there. UI-only,
read-only — no model, migration, simulator, RNG, or Score Calibration
re-baseline (CONTEXT.md gained the **Per-10-minute rate** + **Career view
(league-scoped)** terms; no ADR). Cross-cutting **C1 / C2 / C7**. Seam contract:
[`.claude/worktrees/lg-06d-seam-contract.md`](../../.claude/worktrees/lg-06d-seam-contract.md).

**LG-06e polish.** Statistical Feats was reshaped from a `<ul>` of ~9
category-best entries into ZenGM's **per-game feed**: one sortable row per
(player, round) that achieved a feat, with that round's box-score line +
Opp / Result / Season, deep-linking to the Round. The pure module
`stat_feats.py` had its OUTPUT SHAPE rewritten — the 9-finder /
single-`FeatRecord` design is gone; `scan_feats(player_rounds, matches) ->
tuple[list[FeatRow], list[TeamFeatRecord]]` now emits one frozen `FeatRow` per
qualifying (player, round), each carrying a `stats` mapping over the new pinned
`BOX_SCORE_KEYS` (the 12 `season_player_stats.STAT_KEYS` per-round PLUS
`nuke_detonations`), view-computed Opp / per-Round Result / Season descriptors,
and a non-empty stacked `feats` tuple of `FeatBadge(kind, label, is_season_best)`.
**Hybrid qualification:** a row is included iff it crosses ANY per-game threshold
(`TRIPLE_NUKE_THRESHOLD=3`, `HIGH_TAGS_THRESHOLD=20`, `HIGH_POINTS_THRESHOLD=12000`,
`HIGH_MVP_THRESHOLD=15`, `HIGH_RESUPPLIES_THRESHOLD=20`, `HIGH_MISSILES_THRESHOLD=8`,
plus boolean `medic_shutout` / `perfect_heavy`) OR is a season-best leader for any
of the 5 `SEASON_BEST_STATS` (each yields exactly one guaranteed leader row,
tiebreak highest value -> highest `round_id` -> lowest `player_id`, all-zero-max
skipped) tagged `is_season_best=True`; a row both crossing a threshold AND leading
its kind collapses to ONE badge, season-best winning. Feat kinds are pinned in
`FEAT_KINDS` (8 `(kind, label)` pairs). `comeback_win` moved OUT of the feed into
a separate **Team feats** section — `find_comeback_win(matches) ->
list[TeamFeatRecord]` (return type changed from `Optional[FeatRecord]`; detection
logic unchanged). `scan_feats` guarantees a deterministic default order
(`round_id` DESC, then `player_id` ASC); module stays Django-free
(`TestNoDjangoImportsLeaked` retained). The view materialises the extended
per-(player,round) seam dicts — Opp / Result / Season computed **view-side**, with
**per-Round** Result from `GameRound.red_points`/`blue_points` (NOT the Match
outcome), `mvp = float(prs.get_mvp)` (property) and
`accuracy = float(prs.get_accuracy())` (**method, call with `()`**), and
`nuke_detonations` from the existing `event_type="special"`/`points_awarded=500`
detonation pass — then layers **LG-06a pagination** (`Paginator` AFTER sort,
`_LG01F_PER_PAGE_OPTIONS`) and **expanded LG-06c sort** over the full box-score
column set (`_FEATS_SORT_KEYS` frozenset of every descriptor + 13 box-score keys,
`_FEATS_SORT_KEYS_DISPLAY`, the `_feat_row_sort_value` extractor,
`teams.views._coerce_dir` reused) with **default sort = most recent first**
(`("round", "desc")`) and a `(round_id desc, player_id asc)` secondary tiebreak;
the Team-feats list is not paginated. Coexists with the LG-06b `?team_id=` filter
(applied to the seam INPUTS, `stat_feats.py` untouched by the filter) + the LG-06d
`?season=` selector (incl. Career); changing season/team/sort/per-page omits
`page`. The template `statistical_feats.html` was rewritten into the sortable
`statistical-feats-table` (DOM ids `statistical-feats-th-<key>` per column with
` ↑`/` ↓` glyphs, `stat-feat-badge-<kind>` badges with a `(season best)` /
`season-best` suffix, `statistical-feats-per-page-{form,select}` / `-pagination`)
plus the separate `statistical-feats-team-feats` section (`stat-team-feat-<kind>`),
preserving the LG-06b/d filter ids + the `stat-feats-empty-notice`. Read-only — no
model, migration, URL, simulator, RNG, Score Calibration re-baseline, CONTEXT.md
edit (the **Statistical feat** term was already finalized), or ADR. Cross-cutting
Statistical-Feats reshape. Seam contract:
[`.claude/worktrees/lg-06e-seam-contract.md`](../../.claude/worktrees/lg-06e-seam-contract.md).

## LG-06f watch list (+ watch flag)

The Watch List screen was reshaped from the 3-column bookmark table into the
**Player-Stats column set filtered to watched players** (zero-fill for watched
players with no Rounds in scope), a ZenGM-style **watch flag** landed on **8
league screens**, and watch lists became **per-League** in the browser session.
UI-only — **no model, migration, simulator, RNG, or Score Calibration
re-baseline** (CONTEXT.md gained the **Watch list** + **Watch flag** terms; no
ADR). Seam contract:
[`.claude/worktrees/lg-06f-seam-contract.md`](../../.claude/worktrees/lg-06f-seam-contract.md).

**Per-League session store.** `request.session["watch_lists"]: dict[str,
list[int]]` keyed by `str(league_id)`, value an ordered list of watched Player
ids (e.g. `{"3": [12, 47], "8": [12]}`). The pre-LG-06f global singular
`request.session["watch_list"]` key is **ABANDONED** — no migration, no
read-compat, no fallback (session data is disposable, ADR-0004 precedent). The
single source-of-truth reader `league_views._watched_player_ids(request,
league_id) -> set[int]` (alongside `_coerce_per_page` / `_coerce_team_id` /
`_coerce_season`) reads `session["watch_lists"].get(str(league_id), [])`, coerces
each entry to int (silently dropping non-ints), never raises (missing key ⇒
`set()`), and is consumed by BOTH the context processor AND the screen view.

**Context processor.** `core.context_processors.watch_list(request) ->
{"watched_player_ids": set[int]}` (alongside `league_nav` / `app_mode`,
lazy-importing `_watched_player_ids` to dodge the `core ↔ matches` apps cycle)
resolves `league_id` from `request.resolver_match.kwargs` defensively — no
`resolver_match` or no `league_id` kwarg (off-League, a pre-resolution 404, or a
`RequestFactory()` request) ⇒ `{"watched_player_ids": set()}`. **Registered
immediately AFTER `core.context_processors.app_mode`** in
`settings.TEMPLATES[0]["OPTIONS"]["context_processors"]`, so every league-screen
template sees `watched_player_ids` with zero per-view wiring.

**Toggle endpoint.** `matches.league_screens.watch_list.watch_list_toggle(
request, league_id) -> JsonResponse` — **POST-only** (`HttpResponseNotAllowed(
["POST"])` first line), CSRF-protected (NOT `@csrf_exempt`), re-exported from
`league_screens/__init__`. URL name `watch_list_toggle`, route
`/leagues/<int:league_id>/players/watch-list/toggle/` (inserted right after the
`players_watch_list` route in `league_urls.py`). Step order: `get_object_or_404(
League)` → coerce `player_id` from POST (invalid ⇒ 400 `{"error": "invalid
player_id"}`) → `Player.objects.filter(pk=...).exists()` (unknown ⇒ 400 `{"error":
"unknown player_id"}`, a locked decision — not a silent no-op) → flip membership
in `watch_lists[str(league_id)]` → `session.modified = True` → return `{"watched":
bool, "player_id": int}` (`watched` is the NEW post-flip state). Per-League
isolation falls out of the `str(league_id)` key — toggling in League A never
touches League B's list.

**Pure zero-fill helper.** `season_player_stats.zero_fill_watched(rows,
watched_ids, identity_by_id) -> list[PlayerStatRow]` (alongside
`aggregate_player_stats` / `apply_rate` / `sort_player_stats`, **no new imports** —
the module's frozen no-Django allowlist is preserved) keeps only aggregated rows
whose `player_id in watched_ids`, then appends a zero row for each watched id with
no Round in scope. A zero row carries `games=0` and `stats={k: 0.0 for k in
STAT_KEYS + DERIVED_KEYS}` with `player_name` / `team_id` / `team_name` / `role`
from `identity_by_id[pid]`; a watched id absent from `identity_by_id` (e.g. a
deleted Player) is **silently skipped**. Output order is locked: aggregated rows
first (incoming order), then zero rows in **ascending player-id order**
(`sorted(missing_ids)`) — deterministic for the unit test even though downstream
`apply_rate` / `sort_player_stats` may re-order.

**Rewritten screen view.** `watch_list(request, league_id)` is GET-only EXCEPT
the retained `?action=clear` branch (now clears `watch_lists[str(league_id)]`,
sets `session.modified = True`, redirects to the bare URL). The legacy
`?action=add|remove` branches and the add-form are GONE. The view reuses the
player_stats machinery — `_build_round_dicts` is **imported from**
`matches.league_screens.player_stats` — with the **locked pipeline**
`_build_round_dicts → aggregate_player_stats → zero_fill_watched → apply_rate →
sort_player_stats → Paginator` (`zero_fill_watched` runs BEFORE `apply_rate` so
zero rows take the same rate pass-through, where the `games <= 0 ⇒ 0.0` guard
leaves zeros as zeros). `identity_by_id` is built view-side from
`Player.objects.filter(pk__in=watched_ids).select_related("team")` as `{pid:
{"player_name","team_id","team_name","role"}}`. The screen carries the full
Player-Stats kit **minus the team filter** (the Watch List is a personal
cross-team set) — season selector (+ Career) via `_resolve_season_scope`, rate
toggle via `_coerce_rate`, per-page via `_coerce_per_page` / `_coerce_page`,
sortable columns via `coerce_sort` / `coerce_dir` / `sort_player_stats` — with
new DOM ids `watch-list-{per-page,season-filter,rate}-{form,select}` /
`watch-list-th-{key}` / `watch-list-pagination` mirroring `player-stats-*`,
preserving `watch-list-table`, `watch-list-empty-notice` (the `"No Season"`
substring branch retained) and `watch-list-remove-all` (Remove All →
`?action=clear`), and `sidebar_active="watch_list"`. **Removed ids:**
`watch-list-add` / `-select` / `watch-list-row-{id}` (add-form + old 3-column rows
gone); a per-row flag replaces the per-row Remove control.

**Flag partials + 8-screen surface.** `templates/_partials/watch_flag.html`
renders a `<button type="button" class="watch-flag">` (`.watch-flag-on` added when
`player_id in watched_player_ids`, red styling keys off that class) with
`data-player-id` + `data-toggle-url="{% url 'watch_list_toggle' league.id %}"` and
**NO unique `id`** (League Leaders and Statistical Feats can render the same
player on multiple rows — a per-row id would collide). Include contract:
`{% include "_partials/watch_flag.html" with player_id=<id> %}` (the partial reads
`watched_player_ids` + `league.id` from context). `watch_flag_script.html` carries
a single **delegated** click `<script>` (bound once via `closest(".watch-flag")`),
included **exactly once per page**, that fetch-POSTs to `btn.dataset.toggleUrl`
with the `X-CSRFToken` cookie header and a `player_id` body, then on the response
adds/removes `.watch-flag-on` on **all** buttons sharing that `data-player-id`
(handles the multi-row case). The flag wires into the player-name cell of **8
screens** — `player_stats`, `player_ratings`, `free_agents`, `league_leaders`
(all 4 boards), `statistical_feats`, `team_roster` (both sections),
`team_history`, and the rewritten `watch_list` — adjacent to the existing
career-stats link (the link is kept). **Tests:**
`matches/tests/test_watch_flag.py` (partial render + context-processor
resolution), `matches/tests/test_watch_toggle.py` (endpoint add/remove,
per-League isolation, 405/400/404, CSRF), and
`matches/tests/test_league_watch_list.py` (screen + the pure `zero_fill_watched`
unit tests). The league-pinned **career-page** flag was split off to **LG-06h**
(the global `/players/<id>/stats/` page is league-agnostic, so its flag has no
League to toggle against).

## LG-06g standings form + side detail

The LG-01 Season **Standings** table gained **8 form / side-detail columns** and
every column became sortable (LG-06c pattern). Read-only — **no model, migration,
URL, simulator, RNG, or Score Calibration re-baseline; no ADR**; CONTEXT.md
carries the new **Standings form** + **Side split** terms. Seam contract:
[`.claude/worktrees/lg-06g-seam-contract.md`](../../.claude/worktrees/lg-06g-seam-contract.md).

**Pure module (`matches/standings.py`, extended in place).** `StandingsRow` grew
from 9 to **17 fields** — the 8 new ones appended after `rank` (pinned order):
`match_streak: tuple[str,int]`, `match_l5: tuple[int,int,int]`,
`round_streak: tuple[str,int]`, `round_l5: tuple[int,int,int]`,
`red_wlt: tuple[int,int,int]`, `blue_wlt: tuple[int,int,int]`,
`red_points_for: int`, `blue_points_for: int`. The dataclass holds **structured
numerics only** (streak as `(kind, length)` where `kind ∈ {"W","L","T",""}`;
records/L5 as `(W,L,T)`); the template formats display strings and the view
derives sort keys. `compute_standings(completed_matches, enrolled_teams,
season_rounds)` gained the 3rd param — the Match dict is now **9 keys** (adds
`date_played`) and `season_rounds` is a **6-key** dict (`round_id, team_red_id,
team_blue_id, red_points, blue_points, date_played`). The frozen import allowlist
(`dataclasses`/`typing`/`collections`) is unchanged (`TestNoDjangoImportsLeaked`
still passes — `date_played` values cross the seam as already-comparable dict
values, never imported).

**Two corpora (by design).** Match-grain — existing `wins/losses/ties/
league_points/round_wins/total_score` + `match_streak` + `match_l5` — read the
**completed-Match** corpus. Round-grain — `round_streak`, `round_l5`, and all
four side-split columns — read **every persisted Season Round** including Rounds
of in-progress (`is_completed=False`) Matches. Streak runs from the most recent,
both grains ordered chronologically by `(date_played, id)` asc; L5 is `(W,L,T)`
over the last ≤5.

**Side split is per PHYSICAL side.** Read straight off `GameRound.team_red`/
`team_blue` (the actual physical sides, SIM-08) + `red_points`/`blue_points` —
**never** the Match-level `red_*`/`blue_*` fields (team-position-keyed:
`Match.red_round2_points` is team_red's points while it physically played BLUE in
R2). Round result: red wins iff `red_points > blue_points`, blue iff the reverse,
tie iff equal. `red_wlt`/`red_points_for` aggregate Rounds the team physically
held red, `blue_*` symmetric; a team aggregates into both across the Season.
`round_streak`/`round_l5` are the team's own side-agnostic W/L/T.

**All 17 columns sortable (`matches/league_views.py::season_standings`).**
View-side sort on the materialized rows after `compute_standings`, via
`_coerce_sort_key` (new `_STANDINGS_SORT_KEYS` frozenset of 17 keys; default
`("rank","asc")` ⇒ no `?sort` renders today's order) + `teams.views._coerce_dir`
(newly imported). Helpers `_standings_sort_value` / `_streak_sort_value` /
`_standings_row_attr` (attr-or-key adapter so the draft-preview dict rows sort
through the same path); record/L5 sort `(wins desc, losses asc)`, streaks by
signed run length, **`rank` stays frozen** (never renumbered — LG-06c
League-Leaders precedent). The view builds `season_rounds` from
`GameRound.objects.filter(match__season=season).values(…)`, adds `date_played`
to the Match dicts, and adds context keys `sort` / `dir` / `sort_keys`
(= `_STANDINGS_SORT_KEYS_DISPLAY`) / `querystring_without_sort_dir`; the
draft-preview branch emits the 8 new fields zeroed and still sorts. Template
`templates/seasons/standings.html` swapped its 9 hardcoded `<th>` for the
sort-header loop (DOM ids `season-standings-th-<key>`, ` ↑`/` ↓` glyphs) + 17
`<td>`, preserving `season-standings-table` / `-empty` / `-draft-preview-banner`
/ `season-state-badge`. Tests: `matches/tests/test_standings.py` (pure-unit) +
`matches/tests/test_season_views.py` (view/DOM, classes
`TestLg06gStandingsFormSideDetail` / `TestLg06gStandingsDraftPreview`).

## LG-06h league player page

A read-only **League player page** — the in-League destination of every
player-name link on the 8 LG-06f league screens, mirroring the ZenGM player
profile pinned to one League. Read-only — **no model, migration, simulator, RNG,
or Score Calibration re-baseline; no ADR**; CONTEXT.md already carries the
**League player page** term. Seam contract:
[`.claude/worktrees/lg-06h-seam-contract.md`](../../.claude/worktrees/lg-06h-seam-contract.md).

**Route + view.** URL name `league_player_detail`, path
`/leagues/<int:league_id>/players/<int:player_id>/`, added to
`matches/league_urls.py` among the `players/*` routes (after
`players_free_agents` / `players_watch_list` / `watch_list_toggle`, before the
`league_list` catch-all — the digit-only `<int:player_id>` converter cannot
shadow the literal `players/free-agents/` / `players/watch-list/` routes). The
view `matches/league_screens/player_detail.py::player_detail(request,
league_id, player_id) -> HttpResponse` is **GET-only** (`HttpResponseNotAllowed(
["GET"])` first line) and re-exported from `league_screens/__init__`
(`from .player_detail import player_detail` + `__all__` append, mirroring
`watch_list` / `free_agents`). Step order: GET guard → `get_object_or_404(
League, pk=league_id)` → `get_object_or_404(Player, pk=player_id)` (404 only on a
missing League OR missing Player — never on "player not in this League") →
`request.session["last_league_id"] = league.id` → `displayed_season =
league.active_season or league.seasons.filter(state="completed").order_by(
"-id").first()` → `_build_league_sidebar_links(league, displayed_season,
sidebar_active=None)` (no sidebar entry matches this page, so **every entry
renders inactive**) → build the RS rows → `render(request,
"leagues/player_detail.html", context)`.

**LENIENT rendering.** Any valid `(League, Player)` pair renders 200. When the
player has Rounds in zero of this League's Seasons, `rs_rows == []` and
`career_row is None` ⇒ the template shows `league-player-rs-stats-empty` in place
of `league-player-rs-stats-table`; the header, Potential placeholder, and all 5
stubs still render (a current free agent with no League Rounds is a 200, not a
404).

**RS aggregation is VIEW-SIDE — no new pure module.** Reuse, by import,
`_build_round_dicts` from `matches.league_screens.player_stats` and
`aggregate_player_stats` (+ `PlayerStatRow`) from
`matches.season_player_stats`. Per-Season rows: for each `season in
league.seasons.all()` (newest-first, `order_by("-id")`), build a `prs_filter` of
`{"game_round__match__season": season, "player_id": player.id}`, run
`_build_round_dicts → aggregate_player_stats`, **skip the Season when the round
dicts are empty**, else emit one row from `agg[0]`. Career-in-league row: one
league-wide pass with `prs_filter = {"game_round__match__season__league":
league, "player_id": player.id}` (the LG-06d Career scope filtered to this
player), `year = "Career"`; empty ⇒ `career_row = None`. **Per-Season Team is
derived from the player's actual Rounds that Season** — the aggregated row's
last-seen `team_name`/`team_id` (resolved per-Round inside `_build_round_dicts`
from `team_color` against `game_round.team_red`/`team_blue`), NOT the current
`Player.team` — so a dropped/transferred player shows the team they played for.
No MVP/accuracy recomputation in the view (those come from `get_mvp` /
`get_accuracy` inside `_build_round_dicts`). The table is **not sortable and not
paginated** (a single player across a handful of Seasons — a deliberate
simplification vs. the multi-player Player Stats screen, so no `?sort` / `?dir` /
`?per_page` handling).

**Context keys (frozen).** `league` (the pinned League — used by the watch-flag
partial's `{% url 'watch_list_toggle' league.id %}` + sidebar), `player`,
`displayed_season` (`Season | None`), `sidebar_links` (the 23-entry
`_build_league_sidebar_links` output), `sidebar_active` (locked `None`), `rs_rows`
(`list[dict]`, newest-first, `[]` ⇒ empty-state), `career_row` (`dict | None`),
`stat_columns`. The view does **not** add `watched_player_ids` — the global
`core.context_processors.watch_list` processor supplies it. `stat_columns` is the
STAT portion of `_PLAYER_STATS_COLUMNS` (imported from `player_stats`) sliced
`[2:]` — drop the `name` (col 0, single player) and `team` (col 1, rendered as a
dedicated per-Season prefix column) entries, leaving the 15 stat columns
`games … combo_resupply_count`; the template prefixes **Year** + **Team** columns
itself. Row dict shape (per-Season and Career identical): `{"year": str,
"season_id": int | None, "team_name": str, "team_id": int, "games": int,
"stats": Mapping[str, float]}` — Career uses `year = "Career"`, `season_id =
None`, and the league-wide last-seen team.

**Template + DOM ids.** `templates/leagues/player_detail.html` (NEW) extends
`base.html` and uses the `d-flex` + `{% include "_partials/league_sidebar.html"
%}` shell like the other `templates/leagues/*.html` screens. 11 locked DOM ids:
`league-player-detail` (root), `league-player-header`, `league-player-overall`,
`league-player-potential` (renders the literal `—` em-dash placeholder),
`league-player-ratings`, `league-player-rs-stats-table` (only when rows exist),
`league-player-rs-stats-empty` (only on the empty-state), and the 5 "coming soon"
stubs `league-player-{playoffs,ratings-history,awards,salaries,transactions}-stub`
(each contains the case-insensitive `Coming soon` substring; may cite its
blocking task). The header's EXTERNAL link reverses `player_career_stats` (the
one place in the league templates that still points at the global career page);
the watch flag is `{% include "_partials/watch_flag.html" with
player_id=player.id %}` with `{% include "_partials/watch_flag_script.html" %}`
exactly **once** near the end of `{% block content %}` (never inside a loop).

**8-screen repoint.** Every player-name link on the LG-06f league screens was
repointed from `player_career_stats` (global) to `league_player_detail`
(in-League), keeping the watch-flag include verbatim — only the `<a>` `href`
changes: `player_stats` (`row.player_id`), `player_ratings` (`player.id`),
`free_agents` (`player.id`), `league_leaders` (`row.player_id` ×4 boards),
`statistical_feats` (`row.player_id` — previously plain text, **now wrapped** in a
link), `team_roster` (`player.id` ×2 sections), `team_history` (`p.player_id`),
and `watch_list` (`row.player_id`). The sandbox/global `templates/teams/
player_list.html` and `templates/teams/player_detail.html` stay on
`player_career_stats` (they are league-agnostic). Tests:
`matches/tests/test_league_player_detail.py` (routing / 405 / 404-both-ids /
session write / lenient empty-state / RS rows + Career row / per-Season Team
derived from Rounds / watch flag + single script / external career link /
Potential placeholder / 5 stubs / 23-entry sidebar with zero active); existing
link-target assertions on the 8 screens were repointed to the new route.

## Tests

`matches/tests/` package:
- `test_batch_sim.py` — `BatchSimulator` mechanics (the sole engine post-SIM-09)
- `test_simulation_view_paths.py` — SIM-09 view-path tests: `BatchSimulator.simulate_match` / `simulate_single_round_detailed` DB writes, `@transaction.atomic` rollback on failure, per-Match colour swap (round 2 args reversed, `match.red_round2_points == round2.blue_points`), `ROUND_TICKS` class-attribute patchability, `_flush_to_db` `arena_map`/`zone_size` persistence on every path, fresh per-round 63-bit seed
- `test_map_loader.py` — pins the public surface of `matches/sim_helpers/map_loader.py` (`load_map_context`, `resolve_map_data`, `build_movement_ctx`, `zone_from_cell`, `build_spawn_assignments`); broader behaviour coverage stays in `test_map.py`
- `test_map.py` — map-related tests: adjacency building, A* pathfinding, movement events, cell-aware movement, batch-sim with map (`TestMap02CellMovement`); LOS target filtering and wall-blocking acceptance tests (`TestMap03LOSTargeting`, `TestMap03DBIntegration`); base-sight gate unit tests and DB integration (`TestMap04BaseInteraction`, `TestMap04DBIntegration`); `compute_high_los_ranking` sort correctness and strong-spots view endpoints (`TestMap05ComputeHighLosRanking`, `TestMap05StrongSpotsViews`); 25 pure unit + 2 DB tests for MAP-07 wall types (`TestMap07WallTypes`, `TestMap07DBIntegration`)
- `test_goal_selection.py` — MAP-05 tests: `TestMap05RoleAwareGoal` (17 pure unit tests for Scout→high-LOS, Heavy healthy→strong spots, Heavy unhealthy→medic/ammo, Medic→low-LOS in Heavy's sight, Ammo→high-LOS in Heavy's sight, Commander→enemy medic, action-driven tag/resupply/hide, critical-resource override, default-enemy-base); `TestMap05DBIntegration` (4 DB tests for `_resolve_map_data` 10-tuple, `_build_movement_ctx` MAP-05 keys, empty-list fallback when configs absent)
- `test_map_high_ground.py` — MAP-09 tests: elevation/wall-height DB round-trips, `can_shoot_over_wall` formula, `_has_los` elevation-aware LOS (including asymmetric LOS regression), `elevation_hit_modifier` formula, backwards-compat with no elevation key, `_resolve_map_data` returns `elevation_grid` at index 9
- `test_mechanics.py` — pure-unit tests for `mechanics.py` functions (`shot_cooldown`, `choose_tag_target`, `choose_resupply_target`, `choose_zone_change`); no DB required
- `test_roster.py` — team/player roster validation
- `test_mvp.py` — MVP scoring formulas; `TestCalculateMvp` tests `calculate_mvp` directly without the ORM
- `test_player_h2h_stats.py` — HX-04 pure-unit tests for matches/player_h2h_stats.py (7 public functions + TestNoDjangoImportsLeaked)
- `test_weights.py` — weight function unit tests (`TestWeightFunctions`)
- `test_spawn_assigner.py` — 15 unit tests for `assign_spawn_cells`: happy path role→cell mapping, pool exhaustion overflow, empty/missing pools, blue-team symmetry
- `views_tests.py` — view behaviour: URL routing, form submissions, context keys
- `test_serializers.py` — unit tests for all five serializer classes (including list vs detail split)
- `test_apis.py` — HTTP-level tests for `/api/matches/` and `/api/rounds/` (including `/events/` action)
- `test_tag_cooldown.py` — 23 pure-unit tests for MECH-02 same-target restriction and `game_awareness` gate; no DB required
- `test_nuke.py` — 15 pure-unit tests for MECH-03 Commander nuke-stacking: `_commander_nuke_gate` threshold table, `_get_commander_weights` gating, edge cases at each SP/awareness boundary; no DB required
- `test_nuke.py` — N regression tests for MECH-05: same-tick and within-fuse cancellation in `BatchSimulator`, tick-ordering guarantee; no DB required (the legacy "BatchSimulator consistency with ResourceBasedSimulator" assertion was retired by SIM-09)
- `test_nuke.py` — N pure-unit tests for MECH-04: reaction probability formula, survival-mode goal override, MECH-06 tag-cancel path, no-reaction for low-awareness players; no DB required
- `test_player_memory.py` — 75 pure-unit tests for MECH-06: memory staleness thresholds by role, `_cell_from_memory`/`_known_enemies_from_memory` helpers, teamwork bias, score-broadcast weights, nuke-activation broadcast, medic-under-fire alert, MECH-04 TODO hook, communication broadcast, `PlayerState` memory field defaults
- `test_heatmap.py` — pure-unit tests for `reconstruct_cell_occupancy`: empty-trail survived/eliminated edges, single 1-cell Advance rounding, multi-cell Advance even apportionment (including the "all fractional credit swept by rounding" boundary), stationary slice between two Advances, post-elimination cutoff, sum-vs-`len(result)` rounding-slack bound, and a "no Django imports leaked" defensive check; no DB required
- `test_heatmap.py` — DB/view tests for the per-round `movement_heatmap` page (200 on map-active round, "No map" notice on map-less round, 404 missing round, 405 non-GET) and the `map_heatmap_data` JSON endpoint (200 merged sums across two rounds, `team_color=red` filter pins via a red-player-only cell, 404 missing map, 400 missing/invalid `zone_size` and `team_color`, 405 non-GET)
- `test_simulation_view_paths.py` (extended by RES-04 with `test_flush_to_db_populates_cell_occupancy_json_when_map_active`) — pins that `_flush_to_db` writes a dict-of-`str(int)`-keyed-dicts-with-`r,c`-keys-and-int-values on a map-active round and `null` on a map-less round; **further extended by RV-02** with `_flush_to_db` populating `highlights_json` (the second `save(update_fields=["highlights_json"])`), and the `nuke_cancelled` / `medic_reset` emit + `cancel_logged` de-dup at the chokepoint (the chokepoint moved from `BatchSimulator._record_down` to `sim_helpers.down.record_down` by the shot-resolver consolidation; the SIM-09 RV-02 test classes were migrated to build a `RoundContext` and call the new pure function)
- `test_round_views.py` — pure-unit tests for `build_highlights` (no DB / no Django required): the 6 kinds, `nuke_detonation` activation-vs-detonation discrimination, `team_elimination`-from-result, base captures **not** flagged as a highlight, the 60-tick `scoring_burst` window, id→name/team resolution with absent-id `None`, tick-ascending sort, and empty-input edges
- `views_tests.py` (extended by RV-02) — the Highlights tab render path and the `highlights_json` context key
- `test_round_views.py` — pure-unit tests for `build_round_report` / `should_watermark` (no DB / no Django required): returns `b"%PDF"`-prefixed non-empty bytes for `watermark=True` and `watermark=False`, the `should_watermark` truth table (`True is True` / `False is False`), an empty / early-eliminated zeroed `report_data` (empty player lists, `map_name=None`, `winner_name=None`) renders without crashing, and a "no Django imports leaked into `pdf_report.py`" defensive check (mirrors the RES-04 pattern) — built from a hand-crafted `report_data` dict literal, **no ORM**
- `views_tests.py` (extended by RV-03) — `export_round_report` on a saved `GameRound`: GET → 200 + `Content-Type: application/pdf` + `Content-Disposition` `attachment; filename="round-<id>-...pdf"` shape + `b"%PDF"` body, 404 on a missing id, 405 on POST, and both `is_simulated=True` / `is_simulated=False` rounds returning 200 + `b"%PDF"` (exercises both watermark branches end-to-end)
- `test_h2h_stats.py` — pure-unit tests for the 8 public functions of `matches/h2h_stats.py` (no DB / no Django required, built from hand-crafted dict-list seam inputs): `TestComputeMatchRecord` (empty input zeros, W/L/T counted correctly, null winner counts as tie, defensive unknown winner id counts as tie), `TestComputeRoundRecord` (empty zeros, higher team_a score is win, lower is loss, equal is tie), `TestComputeScoreMargin` (empty zero with no div-by-zero, signed mean from team_a perspective, negative when team_b dominates), `TestComputeAvgSurvivors` (empty zeros, per-team mean independent), `TestTopImpactfulPerTeam` (empty both-`None`, top by cumulative MVP per team, player appearing on both teams attributed per Round, lower-`player_id` tiebreaker, only-team_a returns team_b `None`), `TestComputePerMapBreakdown` (empty `[]`, one row per `arena_map_id`, `None` labelled `"No map (3-zone)"`, sorted by games desc), `TestMarginSeries` and `TestCumulativeWlSeries` (empty `[]`, chronological with `(date, round_id)` tiebreaker / ties don't move running diff, `list[list]` not `list[tuple]`), plus `TestNoDjangoImportsLeaked` (subprocess fresh-import `matches.h2h_stats` then walk `sys.modules` and assert no module name starts with `django` — mirrors HX-01 / HX-02 / RES-04 / RV-03)
- `views_tests.py` (extended by HX-03) — `TestHx03HeadToHead` Django `TestCase` covering all 20 locked test names: picker mode for missing params (both / only `team_a`), `404` for unresolvable `team_a` / `team_b`, error mode when `team_a == team_b` (banner + picker re-renders), empty-history results mode renders `h2h-no-games-notice`, full results render all locked headline DOM ids (`h2h-match-record` / `h2h-round-record` / `h2h-score-margin` / `h2h-team-a-survivors` / `h2h-team-b-survivors` / `h2h-top-impactful-a` / `h2h-top-impactful-b`), per-map breakdown table includes the `"No map (3-zone)"` row, detail list unifies Matches + standalone Rounds, two charts render canvas + `json_script` blocks, `?provenance=real|sim|invalid` filters / forgiving fallback, `?from=&to=` date filter applied to both Rounds and Matches, invalid `from` silently ignored, Side-agnostic pairing (team_a red in one Match / blue in another), team-switcher player appears in both team pools (per-Round attribution), Match record applies the `is_completed=True` filter only, and the Match record excludes a Match when `provenance=real` and either of its two Rounds is simulated (the both-Rounds-must-match-provenance rule)
- `views_tests.py` (extended by HX-04) — `TestHx04PlayerHeadToHead` Django `TestCase` covering all 24 locked test names: picker mode for missing params (both / only `player_a`), `404` for unresolvable `player_a` / `player_b`, error mode when `player_a == player_b` (banner + picker re-renders), empty-basket results mode renders `player-h2h-no-games-notice`, **same-team Rounds excluded from basket** + opposite-teams Round included, full results render headline DOM ids (`player-h2h-round-record` / `player-h2h-score-margin` / `player-h2h-tags-a-to-b` / `player-h2h-tags-b-to-a`), per-role breakdown table render, per-map breakdown table includes the `"No map (3-zone)"` row, detail list reverse-chronological, two charts render canvas + `json_script` blocks, **asymmetric tag direction A→B distinct from B→A**, `?role=` both-semantics include (both played role) + exclude (only one played role) + invalid silently ignored, `?provenance=real|sim|invalid` filters / forgiving fallback, `?from=&to=` date filter applied to Rounds, invalid `from` silently ignored, **Side-agnostic per-Round `team_color` attribution** (player_a red in one Round / blue in another), and career-page anchor (`templates/teams/player_career_stats.html`) renders the `player-h2h-link` with `?player_a=` pre-filled
- `test_season_dashboard_logic.py` — LG-01c pure-unit tests for `matches/season_dashboard.py` (`compute_leaders` / `find_next_fixture` / `round_progress` + `LeaderRow`): empty input → `[]`, single-player happy path per stat, `tag_ratio` uses `max(..., 1)` denominator clamp on a zero-`times_tagged` row, multi-row `tag_ratio` aggregation is `sum/sum` not mean-of-per-row-ratios, sort-ladder tiebreaks (`value` desc → `games_played` desc → `player_id` asc), deterministic repeated-call equality, role mix does not affect ranking, `limit` caps + `limit > input` returns all + default `limit=3`, defensive "last row wins" on inconsistent role across a player's group, unknown stat string raises `ValueError`, `find_next_fixture` empty / all-played / first-unplayed / side-agnostic `frozenset` match, `round_progress` empty / no-played / all-played / partial / extra-played-keys-not-counted, plus `TestNoDjangoImportsLeaked` subprocess fresh-import check
- `test_league_dashboard.py` — LG-01c Django `TestCase` for the league dashboard's 4 season-pick branches + DOM-id presence: routing (200 / 404 / 405 / reverse / template), season pick (no Season ⇒ `"none"`, draft Season ⇒ `"draft"`, active Season ⇒ `"active"`, completed-only fallback to most-recent, active takes precedence over completed), draft branch (`action_button_state="start_season"` + `disabled` attr + standings snippet sorted by team-name asc top-3 + the 3 leaders DOM ids + `league-dashboard-next-round` + `league-dashboard-round-count` absent), active branch (`action_button_state="play_next"` + standings via `compute_standings` + next round with team names + date + round-count format + 3 leaders rendered top-3 + per-leader raw `/players/<id>/career-stats/` href + raw `/leagues/<id>/leaders/` "View all leaders" href), completed branch (`action_button_state="start_next_season"` + next round renders "All fixtures played" + round count `total / total`), none branch (`league-dashboard-no-season-notice` substring `"No Season"` + `action_button_label="No Season"` + the 7 active-branch body DOM ids absent)
- `test_season_dashboard_view.py` — LG-01c Django `TestCase` for the season dashboard's state matrix + 5-entry sidebar + body integration: routing (200 / 404 / 405 / reverse / template), state matrix (draft / active / completed each renders the locked DOM-id set with active-only ids absent in draft + action-button label and `data-action-state` per state), sidebar (`sidebar_links` has 5 entries in pinned order `overview, standings, schedule, teams, history` + `sidebar_active="overview"` + standings link reverses to `season_standings` + schedule link reverses to `season_schedule` + teams renders as disabled `<span>` no href + history renders as disabled `<span>` no href), body (leaders use the `compute_leaders` pure module end-to-end via a known-PRS active Season, `next_fixture` omitted when completed and all played, per-leader raw `/players/<id>/career-stats/` href, raw `/seasons/<id>/leaders/` "View all leaders" href)
- `conftest.py` — shared `make_team_with_slots(prefix)` helper

## LG-02a sandbox single-elimination tournament

A **standalone sandbox Tournament** — a persisted single-elimination bracket
decoupled from any League / Season (`season`-less, no `generate_schedule`). Three
new models, one pure module, six views under a new `/tournaments/` mount, a
bracket-tree detail page, and a sandbox-nav entry. See
[ADR-0019](../../docs/adr/0019-tournament-bracket-model.md) for the persisted
standalone-sandbox decision; seam contract:
[`.claude/worktrees/lg-02a-seam-contract.md`](../../.claude/worktrees/lg-02a-seam-contract.md).
CONTEXT.md `### Tournaments` carries the 8 locked terms (**Tournament** /
**Bracket** / **Bracket round** / **Bracket node** / **Bracket seed** /
**Seeding** / **Advancement** / **Bye**). Migration:
`matches/migrations/0033_tournament.py` (new models only — **no `RunPython`, no
backfill**, ADR-0004 precedent; `0032` was taken by `0032_league_free_agent_pool`).

**Models (`matches/models.py`, after `Season`).** `Tournament` (`name`,
`format` enum default `"single_elimination"` — present-but-single, extensible;
3-state `state` machine `setup` → `active` → `completed`; `created_at`;
`champion` FK to `teams.Team` SET_NULL, `related_name="tournaments_won"`).
Methods: `lock_and_build()` (`@transaction.atomic`, `setup` → `active`: validates
participant count **≥ 4**, builds + persists the `BracketNode` tree from the
current Seeding via `matches.bracket.build_bracket`, flips `state="active"`;
raises `django.core.exceptions.ValidationError` on count < 4 or wrong state — the
seeding-editable window closes here, mirroring `Season.start_season`'s draft→active
M2M lock); `is_locked` property (True iff `state != "setup"`);
`find_next_playable_node()` (delegates to `matches.bracket.find_next_node`).
`TournamentParticipant` (`tournament` CASCADE `related_name="participants"`,
`team` CASCADE `related_name="+"`, `seed` `PositiveIntegerField` — 1-based Bracket
seed, lower int = stronger; `Meta.ordering = ["seed"]` so participants iterate in
Seeding order; `UniqueConstraint`s `uniq_tournament_seed` + `uniq_tournament_team`).
`BracketNode` (`tournament` CASCADE `related_name="nodes"`; tree coords
`bracket_round` 1-based + `position` 0-based; team slots `team_a` / `team_b`
nullable SET_NULL; `seed_a` / `seed_b` carry each slot's Bracket seed alongside it
so the tie-break and bye carry-forward need no participant re-query; `match` FK to
`matches.Match` SET_NULL `related_name="bracket_node"` — NULL until played, a bye
node stays NULL; Advancement pointer `advances_to` self-FK `related_name="feeders"`
+ `advances_to_slot` `"a"`/`"b"`; `is_bye` flag; `winner` FK SET_NULL;
`UniqueConstraint` `uniq_tournament_round_position`). **A node = exactly one
2-round `Match`** (no series / best-of-N — deferred LG-02b).

**Pure module `matches/bracket.py`** owns bracket **structure + Seeding + bye
placement + tie-break math** — frozen import allowlist **`dataclasses`, `typing`,
`math`, `collections` ONLY** (NO Django / ORM / `random` / `datetime` / I/O /
logging), enforced by `matches/tests/test_bracket.py::TestNoDjangoImportsLeaked`
(subprocess fresh-import + `sys.modules` walk, mirroring
`matches/standings.py` / `matches/schedule_generator.py`). Frozen dataclasses
`BracketNodeSpec` / `ParticipantSpec`. Functions: `default_seed_order(team_ratings:
list[tuple[int, float]]) -> list[int]` (default Seeding = mean active-player
`overall_rating` **DESC**, then `team_id` ASC tiebreak — the SAME talent ranking
the LG-01c draft-preview standings use); `build_bracket(participants) ->
list[BracketNodeSpec]` (single-elim tree for **arbitrary N ≥ 4**; size = next
power of two ≥ N; standard `1vN, 2v(N-1), …` seed pairing; the top `(size − N)`
Bracket seeds get a round-1 **Bye**; wires `advances_to`/`advances_to_slot`, final
node `advances_to=None`); `find_next_node(nodes) -> dict | None` (lowest
`(bracket_round, position)` playable node — both slots filled, not bye, no
winner/match yet); `advance_winner(nodes, node_position, winner_id, winner_seed) ->
list[dict]` (parent-slot mutation dicts `{"bracket_round","position","slot",
"team_id","seed"}`); `resolve_bye_chain(nodes) -> list[dict]` (cascade byes at
build time into the next round, same mutation shape); `break_tie(seed_a,
best_round_score_a, seed_b, best_round_score_b) -> int` (**tie-break rule:** higher
best single-Round score advances, else the higher Bracket seed = **lower seed
int** — pure integer compare, no re-sim).

**View ↔ pure seam = ints / dicts ONLY.** The pure module never sees a `Team`,
`Match`, or `BracketNode` ORM instance and never returns a Django object. The view
flattens `BracketNode` rows to plain dicts via the private helper
`_node_to_dict(node) -> dict` (keys `bracket_round`, `position`, `team_a_id`,
`team_b_id`, `seed_a`, `seed_b`, `is_bye`, `match_id`, `winner_id`, `advances_to`
= `(bracket_round, position)` tuple or `None`, `advances_to_slot`) before calling
`find_next_node` / `advance_winner` / `resolve_bye_chain`; `default_seed_order`
gets `(team_id, mean_overall_rating)` tuples built from `Team.active_players` +
`Player.overall_rating`; `break_tie` gets four ints. The view applies the returned
mutations to the ORM.

**Views (`matches/tournament_views.py`, NEW) + URLs
(`matches/tournament_urls.py`, NEW — bare names, no `app_name`, mirrors
`season_urls.py`).** Mounted `path("tournaments/", include("matches.tournament_urls"))`
in the project `urls.py` after `matches/` (everything not under `/leagues/` or
`/seasons/` resolves to `app_mode == "sandbox"` via the LG-01k path-prefix
processor — **no processor change**). The six views / URL names:
`tournament_list` (GET, newest-first `order_by("-id")`); `tournament_create`
(GET form / POST `@transaction.atomic` — team source = **select existing** via
`Team.objects.regular()` + **generate** via `from teams.views import
_generate_teams` (the LG-01b cross-app seam, signature unchanged) — creates the
`Tournament(state="setup")` + `TournamentParticipant` rows with default Seeding).
**Roster-eligibility gate:** only Teams with a full valid roster
(`Team.is_valid_roster` — all 6 slots filled, no duplicate player) may enter a
Tournament. `available_teams` is the `Team.objects.regular()` list filtered to
`is_valid_roster` (slot FKs `select_related`-ed so the property adds no
per-Team query), so the select list never offers an incomplete-roster Team; a
tampered/stale POST of an ineligible id is re-validated server-side and rejected
with an error re-render (nothing created); and the generate path clamps
players-per-team to **>= 6** (`generate_ppt = max(6, generate_ppt)`) so it can
never create an unplayable participant;
`tournament_detail` (GET-only, the bracket tree + seeding-edit form + play
controls); `tournament_reseed` (POST `@transaction.atomic`, persists a manually
reordered Seeding, **rejected once `is_locked`**); `tournament_lock` (POST,
`lock_and_build()`, `ValidationError` → redirect + `messages.error`);
`tournament_play_next` (POST `@transaction.atomic`, state must be `"active"` —
sims **ONE** Match `BatchSimulator().simulate_match(node.team_a, node.team_b,
match_type="tournament")`, resolves the winner incl. the `break_tie` fallback when
`match.winner is None`, Advances into the parent slot, stamps `champion` +
`state="completed"` on the final). Guard idiom: non-allowed method →
`HttpResponseNotAllowed([...])` as the first body line (mirrors `movement_heatmap`
/ `export_round_report`). Play is **synchronous game-by-game** (one POST per
node).

**Templates** (`templates/matches/tournament_{list,create,detail}.html`, each
extends `base.html`). Locked DOM ids: list — `tournament-list-table`,
`tournament-list-empty` (`No tournaments yet`), `tournament-create-link`, per-row
`state-badge` class; create — `tournament-create-form`, `tournament-create-name`,
`tournament-create-team-select` (multi-select of `Team.objects.regular()`),
`tournament-create-generate-count`, `tournament-create-generate-ppt` (defaults 6),
`tournament-create-submit`, `tournament-create-no-teams-notice`; detail — the
**bracket tree** container `tournament-bracket` with one column per Bracket round
`tournament-bracket-round-{n}` (1-based) and one node `tournament-node-{bracket_round}-{position}`
(bye node carries the `bye-node` class), the setup-only Seeding form
`tournament-seeding-form` / `tournament-seed-input-{team_id}` /
`tournament-seeding-submit`, play controls `tournament-lock-form` /
`tournament-lock-submit` (setup), `tournament-play-next-form` /
`tournament-play-next-submit` (active), `tournament-champion-banner` (completed,
substring `Champion`), and `tournament-detail-empty` (`No participants`).
`tournament_detail` context keys (frozen): `tournament`, `participants`, `rounds`
(`[{"bracket_round": int, "nodes": list[node_view_dict]}]`), `next_node`,
`is_locked`, `can_play`. Sandbox-nav: a flat anchor
`<a id="tournaments-nav-link" …>Tournaments</a>` in the `{% elif app_mode ==
"sandbox" %}` branch of `templates/base.html` (after `Maps`, before the tools/help
include).

**Admin (`matches/admin.py`, after `SeasonAdmin`).** `TournamentAdmin`
(`list_display` name/format/state/champion/created_at + `TournamentParticipantInline`
/ `BracketNodeInline`), `TournamentParticipantAdmin`, `BracketNodeAdmin`. No
existing registration touched.

**Tests.** `matches/tests/test_bracket.py` (pure-unit, no DB —
`TestDefaultSeedOrder`, `TestBuildBracketPowerOfTwo` N=4/8/16,
`TestBuildBracketWithByes` N=5/6/12, `TestFindNextNode`, `TestAdvanceWinner`,
`TestResolveByeChain`, `TestBreakTie`, `TestNoDjangoImportsLeaked`);
`matches/tests/test_tournament_models.py` (`lock_and_build` transition +
`ValidationError` on N<4, constraints, `find_next_playable_node`);
`matches/tests/test_tournament_views.py` (DOM ids + 200/302/404/405, setup→active→
completed, POST-generate via the **real** `_generate_teams` (no `mock.patch` so
signature drift fails loudly), forced-tie tie-break path, champion stamped on
final).

**Scope-out (LOCKED — DEFERRED, do NOT build here):** CSV participant import +
async "play-all" / Celery (LG-02a-2); series / best-of-N nodes (LG-02b);
double-elimination / round-robin / RR→DE / Swiss (format enum extensible, only
`single_elimination` ships); in-League / in-Season embedding (Tournament is
standalone, `season`-less); batch-N tournament simulation; any
`simulate_match` / `simulate_scheduled_round` change (consumed verbatim,
`arena_map=None` 3-zone fallback per node); no per-Tournament arena-map config; no
backfill / `RunPython`; no CONTEXT.md term beyond the 8 `### Tournaments` entries.

## LG-02a-2 CSV participant import + async play-all

Two ergonomics follow-ups over the shipped LG-02a Tournament — **CSV participant
import** (LG-00b reuse) and **async play-all** (Celery). Seam contract:
[`.claude/worktrees/lg-02a-2-seam-contract.md`](../../.claude/worktrees/lg-02a-2-seam-contract.md).
**No model, no migration, no ADR** — per-node-atomic follows ADR-0016, CSV reuse
follows LG-00b; both reversible. **No edit** to `simulate_match`, the bracket
build/advance logic, or `teams/` (cross-app **read-only** imports only).

**Pure addition — `matches/bracket.py::stage_progress(nodes: list[dict]) ->
tuple[int, int]`.** STAGE-based progress for a bracket: `total` = max
`bracket_round` = ⌈log₂(size)⌉ Bracket rounds (0 when empty); `completed` = count
of rounds where **every non-bye node** (`not is_bye`) has `winner_id is not None`
(a round of all byes is vacuously complete). Reads ONLY `bracket_round` / `is_bye`
/ `winner_id` off the existing `_node_to_dict` flat dicts — respects the frozen
`dataclasses`/`typing`/`math`/`collections`-only allowlist (no new import;
`TestNoDjangoImportsLeaked` still passes).

**Engine module — `matches/tournament_engine.py` (NEW).**
`play_next_node(tournament) -> BracketNode | None` (`@transaction.atomic`)
**extracts** the per-node resolve/advance body out of the old inline
`tournament_play_next`: `find_next_playable_node()` → sim ONE Match
`BatchSimulator().simulate_match(..., match_type="tournament")` (deferred import)
→ resolve winner incl. the `break_tie` fallback on `match.winner is None` → stamp
`winner` + `save(update_fields=["match","winner"])` → `advance_winner` parent-slot
mutations applied to the ORM → stamp `champion` + `state="completed"` on the final
node; returns `None` when nothing is playable. **One node = one transaction**
(ADR-0016 per-node-atomic). The sync `tournament_views.py::tournament_play_next`
is **refactored** to keep its HTTP shell (POST-only, `state != "active"` guard) and
just call `play_next_node`; its inline sim/resolve/advance block is **deleted**.

**Celery task — `matches/tasks.py::play_tournament_task(self, tournament_id) ->
dict`** (`@shared_task(bind=True, name="matches.play_tournament")`, study
`play_season_task`). Loops `while play_next_node(tournament) is not None`, after
each node recomputing `stage_progress` and `self.update_state(state="PROGRESS",
meta={"completed": int, "total": int})`; returns final `{"completed", "total"}`
(**stage counts, NOT node counts**). Inactive-state (`state != "active"`)
early-returns the current stage progress (no-op). **NO outer
`@transaction.atomic`** — per-node atomicity comes from `play_next_node`'s
decorator (a mid-loop FAILURE leaves every already-resolved node committed →
resumable on re-invoke); `django.db.close_old_connections()` in `finally`.

**Three new views/URLs (`tournament_views.py` / `tournament_urls.py`).**
`tournament_play_all` (POST-only, `HttpResponseNotAllowed(["POST"])` first line;
`play_tournament_task.apply_async((tournament_id,), retry=False)` → `JsonResponse({job_id,
tournament_id}, status=202)`; **409** `{"error": ...}` when `state != "active"`;
**503** `{"error": ...}` when the enqueue raises `kombu.exceptions.OperationalError`
— a clean JSON broker-down error, NOT a 500 HTML page, so the UI never chokes on
the response. `retry=False` so an unreachable broker fails after one bounded attempt
instead of retry-hanging the request);
`tournament_play_status` (GET-only → `JsonResponse(_build_tournament_play_status_response(
AsyncResult(job_id), tournament_id=...))`, the locked 5-key JSON `{status,
completed, total, error, tournament_id}` mirroring
`matches/views.py::_build_play_status_response`, REUSING `_celery_state_to_job_status`
verbatim, stage counts with defensive `int(... or 0)` / `isinstance` guards);
`tournament_import_participants` (POST `@transaction.atomic`). URL order: the three
paths (`<id>/play-all/`, `<id>/play-status/<job_id>/`, `<id>/import-participants/`)
append after the existing `<id>/play-next/`.

**CSV import (full LG-00b reuse).** `tournament_import_participants` is
**setup-only** (`is_locked` ⇒ `messages.error` + redirect, no writes). Happy path:
`RosterImportForm(request.POST, request.FILES)` → `parse_roster_csv` →
`_check_db_slot_collisions` → `_apply_roster` returning `(created_teams,
appended_teams, player_count)`; **ONLY `created_teams`** become
`TournamentParticipant`s (brand-new Teams ⇒ no `uniq_tournament_team` collision;
`appended_teams` are created/extended but **NOT auto-added**); then **re-seed the
whole field by talent** (`_team_mean_rating` → `default_seed_order`, rewriting
every `seed` via the same two-phase large-offset write `tournament_reseed` uses to
dodge `uniq_tournament_seed`) → redirect. Error branch (`RosterImportError` OR
form-invalid): `transaction.set_rollback(True)` + **re-render**
`tournament_detail.html` HTTP 200 with the bound form + `exc.errors` (zero writes).
New private `_detail_context(tournament)` helper shares the detail context between
`tournament_detail` and the import-error re-render — the **6 frozen LG-02a keys**
(`tournament` / `participants` / `rounds` / `next_node` / `is_locked` / `can_play`)
**plus** `import_form` (`RosterImportForm()` unbound) and `import_row_errors`
(`list[RowError]`, default `[]`).

**Cross-app / cross-module imports** (read-only, added to `tournament_views.py`):
`from teams.forms import RosterImportForm`; `from teams.roster_importer import
parse_roster_csv, RosterImportError`; `from teams.views import
_check_db_slot_collisions, _apply_roster`; `from matches.views import
_celery_state_to_job_status`. Template-download URL name `import_roster_template`
(LG-00b) is reused.

**Template surfaces (`templates/matches/tournament_detail.html`).** Setup-state
"Import Participants (CSV)" form (`<form enctype="multipart/form-data">`): DOM ids
`tournament-import-form` / `-file` / `-submit` / `-template-link` / `-errors`
(error block, only when `import_row_errors` non-empty) + per-row
`tournament-import-error-{row_num}-{field|"row"}` (mirrors LG-00b
`roster_import.html`). Active-state "Play All": `tournament-play-all-form` /
`-submit` / `-progress` (progress `hidden` by default), with a single inline
**1000 ms poll JS** block (mirrors the LG-01d seasons `dashboard.html`):
on submit it **immediately** disables the button + shows a `Starting…` indicator
(instant in-progress feedback before the first poll), reads the POST response as
**text first** and `try/catch`-parses JSON so a non-JSON error page (e.g. a 500
when the broker is down) shows a **friendly fallback** message — never the cryptic
`Unexpected token '<'` JSON-parse crash — then fetch-POST → `startPolling(job_id)`
against `tournament_play_status`, updates progress as `Running… stage X / Y` from
`completed`/`total`, `reload()` on `status === "complete"`, surfaces `error` +
re-enables on `status === "error"`, swallows network blips. The single-step
`tournament-play-next-form` is **unchanged**.

**Job-term extension.** The CONTEXT.md **Job** term gains a **4th kind** — a
**Play Tournament job** (play every remaining decisive Bracket node to a champion;
progress = completed Bracket stage) + the `/tournaments/<id>/play-all/` URL. **No
new term** — the **Roster import** term is reused unedited.

**Determinism / scope-out (LOCKED).** **Non-deterministic** — `simulate_match`
draws fresh per-round seeds, so Play Tournament games are NOT
master-seed-replayable: **no SIM-07 / SIM-08 interaction, NO Score Calibration
re-baseline**. No CSV preview/commit UI, no per-tournament arena map, no async on
the single-step `tournament-play-next` (stays synchronous). Tests:
`test_bracket.py` (extend — `TestStageProgress` + purity assertion),
`test_tournament_engine.py` (NEW), `test_tournament_tasks.py` (NEW, under
`CELERY_TASK_ALWAYS_EAGER`), `test_tournament_views.py` (extend).

## LG-02b best-of-N series nodes

Generalises a **Bracket node** from holding **one** 2-round `Match` to holding a
best-of-N **Series** of Matches — the node Advances only when one Team **clinches**
the Match-win majority. Builds directly on LG-02a (the persisted single-elim
bracket) and LG-02a-2 (the per-Match engine + async play-all). See
[ADR-0020](../../docs/adr/0020-best-of-n-series-bracket-nodes.md) for the
Series-via-through-model decision; seam contract:
[`.claude/worktrees/lg-02b-seam-contract.md`](../../.claude/worktrees/lg-02b-seam-contract.md).
CONTEXT.md `### Tournaments` carries the **Series** / **Series length** terms
(added at grilling time — reuses the existing **Bracket node** / **clinch** /
**Advancement** vocabulary, no new glossary entry beyond those two). Migration:
`matches/migrations/0034_*` (ops in **pinned order** `AddField(Tournament.
series_length)` → `CreateModel(SeriesMatch)` → `RemoveField(BracketNode.match)`
— **no `RunPython`, no backfill**, ADR-0004 disposable-sandbox precedent).

**Model changes (`matches/models.py`).** New `Tournament.series_length`
(`PositiveSmallIntegerField`, choices `(1, "Best of 1")`/`(3, "Best of 3")`/
`(5, "Best of 5")`, `default=1`) — set at create-time only, **immutable once the
Tournament leaves `setup`** (the existing `lock_and_build` `state != "setup"`
guard freezes it on the setup→active transition; no view rewrites it on a
non-`setup` Tournament). **Bo1 (`series_length == 1`) is byte-equivalent to
LG-02a** — one Match, clinch threshold 1, identical Advancement. New `SeriesMatch`
through-model (appended after `BracketNode`): `node` FK CASCADE
`related_name="series_matches"` (deleting a node drops its Series); `match` FK to
`matches.Match` SET_NULL/nullable `related_name="series_match"` (mirrors the old
`BracketNode.match` semantics — deleting a Match must not cascade the Series row);
`game_number` 1-based `PositiveIntegerField`; `winner` FK to `teams.Team` SET_NULL
`related_name="+"` (the per-Match decisive Team — `match.winner` or the `break_tie`
result when `match.winner is None`); `Meta.ordering=["game_number"]` +
`UniqueConstraint(fields=["node","game_number"], name="uniq_seriesmatch_node_game")`
(one row per (node, game)). **The node's win tally is DERIVED** by counting
`SeriesMatch.winner` rows per team-slot — **counters are never stored**;
`node.winner` is stamped only on clinch. The LG-02a `BracketNode.match` FK
(`related_name="bracket_node"`) is **dropped wholesale** — the per-Match link now
lives on `SeriesMatch.match`, every old reader of `node.match` / `node.match_id` /
the `match_id` dict key moves to the Series-derived path. No alias retained.

**Pure `matches/bracket.py` additions** (frozen `dataclasses`/`typing`/`math`/
`collections`-only allowlist unchanged — both functions add **no new import**;
`TestNoDjangoImportsLeaked` still passes). `clinch_threshold(series_length: int)
-> int` = `(series_length // 2) + 1` (Bo1→1, Bo3→2, Bo5→3; pure integer math, no
odd-ness validation — callers only pass the locked `1`/`3`/`5`). `series_winner_slot(
wins_a: int, wins_b: int, series_length: int) -> Optional[str]` returns `"a"` when
`wins_a >= threshold`, `"b"` when `wins_b >= threshold`, else `None` (Series still
undecided) — `wins_a` checked **first** so the function is total and deterministic
on every integer input (a malformed both-at-threshold input resolves to `"a"`
rather than raising; odd N + Series-stops-on-clinch makes both-at-threshold
unreachable in practice). `_node_to_dict` gains **3 derived keys** — `wins_a` /
`wins_b` (`sum(1 for sm in node.series_matches.all() if sm.winner_id ==
node.team_a_id / node.team_b_id)`) and `series_length` (`node.tournament.
series_length`) — and **drops `match_id`** (the field is gone); the caller
prefetches `series_matches` + the tournament's `series_length` so no per-node N+1.
The `find_next_node` playable predicate swaps the old `winner_id IS NULL AND
match_id IS NULL` checks for **`series_winner_slot(wins_a, wins_b, series_length)
is None`** — a node is playable iff both slots are filled, it is not a bye, and the
Series is not yet clinched (for Bo1 this is exactly the old behaviour:
`series_winner_slot(0, 0, 1) is None` ⇒ playable until the first Match is recorded).
**`stage_progress`, `build_bracket`, `advance_winner`, `resolve_bye_chain`,
`break_tie`, `default_seed_order`, and the two dataclasses are unchanged** — in
particular `stage_progress` still reads `winner_id` (stamped only on clinch), so it
keeps reporting Bracket-round completion with zero edits.

**Engine rewrite (`matches/tournament_engine.py::play_next_node`).** Signature
unchanged (`play_next_node(tournament) -> BracketNode | None`, `@transaction.
atomic`), but the **transaction boundary is now per-MATCH** (one Series Match = one
atomic commit) — extends ADR-0016's per-node-atomic to per-Match-atomic. Algorithm:
`find_next_playable_node()` (now §2d-clinch-aware, skips a clinched node) → compute
the current derived tally from existing `SeriesMatch` rows → sim **ONE** Match
`BatchSimulator().simulate_match(node.team_a, node.team_b, match_type="tournament")`
(**sides fixed across the Series** — `team_a`/`team_b` argument order constant,
no home/away alternation) → resolve the per-Match decisive winner exactly as
LG-02a-2 (`break_tie(node.seed_a, best_a, node.seed_b, best_b)` on `match.winner is
None`, mapping the seed back to the Team) → `SeriesMatch.objects.create(node=node,
match=match, game_number=node.series_matches.count() + 1, winner=match_winner)` →
recompute the tally → `slot = series_winner_slot(wins_a, wins_b, node.tournament.
series_length)`. **If `slot is None`** (Series not yet clinched) ⇒ **return `node`
now** (no `node.winner` write, no Advancement — the next call resolves the next
Match of the same node). **On clinch** (`slot` is `"a"`/`"b"`): set `node.winner` +
`winner_seed`, `save(update_fields=["winner"])` (the list **drops `"match"`** — no
longer a `BracketNode` field), build the flat `_node_to_dict` list, `advance_winner`
into the parent slot, and stamp `champion` + `state="completed"` when the clinched
node is the final (`advances_to_id is None`). Returns `None` when nothing is
playable. **Bo1 equivalence:** step creates game 1, `series_winner_slot(1, 0, 1) ==
"a"` ⇒ clinch on the first Match ⇒ identical single-Match advance — the only
structural difference vs LG-02a-2 is the played `Match` lives on a `SeriesMatch`
row, not `BracketNode.match`. **Callers unchanged in signature/route:**
`tournament_play_next`, `play_tournament_task` (the Celery `while play_next_node(...)
is not None` loop now iterates **once per Match**, so a Bo3/Bo5 drains over more
steps), `tournament_play_all`, `tournament_play_status` all keep their URLs +
5-key status JSON; `stage_progress` still reports Bracket-round completion.

**View / template surface.** Create form (`tournament_create` /
`tournament_create.html`): reads a new POST field `series_length` (parsed to int;
invalid/absent ⇒ default `1`; only `1`/`3`/`5` accepted, anything else falls back
to `1` — forgiving-fallback precedent), stamped via `Tournament.objects.create(...,
series_length=series_length)`; rendered as a `<select name="series_length">` with
DOM id **`tournament-create-series-length`** (options Bo1 selected / Bo3 / Bo5),
placed in `tournament-create-form` before the submit. Detail page (`_build_rounds`
/ `tournament_detail.html`): each node view-dict gains derived `wins_a` / `wins_b`
(counted from the node's `SeriesMatch` rows, `series_matches` prefetched) and
**drops the `match` key** (per-node link is now per-SeriesMatch — the template
renders a "View match" link per played Series Match, or omits it); a per-node
**Series-score** element with DOM id **`tournament-node-series-score-{bracket_round}-
{position}`** renders the running `{{ node.wins_a }}–{{ node.wins_b }}` (en-dash,
e.g. `2–1`) for every non-bye node (a Bo1 node reads `1–0`/`0–1`/`0–0`). The
**champion** still surfaces via the unchanged `tournament-champion-banner`;
`tournament.series_length` is read directly off the `tournament` object in the
template (no new context key — the frozen `_detail_context` keys are unchanged,
only the shape of each `rounds[*].nodes[*]` dict changes).

**Tests.** `matches/tests/test_bracket.py` (extend — `TestClinchThreshold`,
`TestSeriesWinnerSlot`, Series cases on `TestFindNextNode`, purity still green);
`test_tournament_models.py` (extend — `SeriesMatch` create/ordering/
`uniq_seriesmatch_node_game`/CASCADE/SET_NULL, `series_length` default + choices +
state-immutability, `_node_to_dict` derived keys with no `match_id`);
`test_tournament_engine.py` (extend — one `SeriesMatch` per call + no `node.winner`
until clinch, clinch→advance→champion, Bo1 one-call equivalence, per-Match
`break_tie`, atomicity); `test_tournament_views.py` (extend — create-form select
+ POST persistence + fallback, detail Series-score element); `test_tournament_
tasks.py` (extend — `play_tournament_task` drains a Bo3 to a champion, Advance only
on clinch). Tests assert on the pure functions, `SeriesMatch` rows, `node.winner` /
Advancement, and DOM ids — **never on exact simulated point totals**
(non-deterministic).

**Scope-out (LOCKED — DEFERRED, do NOT build here):** **per-Bracket-round Series
escalation** (Bo1 early → Bo5 final — LG-02b applies a single per-Tournament
`series_length` to every node; deferred to LG-02b-2); **home/away side alternation**
(sides fixed `team_a` red / `team_b` blue every Match); **deterministic /
master-seed-replayable Series** (`simulate_match` draws fresh per-round seeds ⇒
**non-deterministic, no SIM-07/08, NO Score Calibration re-baseline**); any
`simulate_match` / `simulate_scheduled_round` change (consumed verbatim,
`arena_map=None` 3-zone fallback per Match); **backfill / `RunPython`** (none —
pure schema ops, ADR-0004); a **Series-level tiebreaker** (odd N always clinches;
ties broken per-Match by the unchanged `break_tie`); **dead-rubber Matches** (the
Series stops the moment a Team clinches); any new CONTEXT.md term beyond
**Series** / **Series length** (already written).

## LG-02b-2 per-Bracket-round series escalation

Generalises the LG-02b best-of-N **Series length** from a **single flat
per-Tournament value applied to every node** into a **per-Bracket-round** value
anchored to **depth from the final** — Bo1 early rounds → Bo3 semis → Bo5 final,
or any independent mix. Builds directly on LG-02b: the pure clinch engine
(`clinch_threshold`, `series_winner_slot`, `count_series_wins`, the `SeriesMatch`
through-model, the per-Match-atomic `play_next_node` body) is **UNCHANGED** —
**only the *source* of the `series_length` argument moves from tournament-level to
node-level**. [ADR-0020](../../docs/adr/0020-best-of-n-series-bracket-nodes.md) was
**extended** for the per-round escalation (do NOT re-write it); seam contract:
[`.claude/worktrees/lg-02b-2-seam-contract.md`](../../.claude/worktrees/lg-02b-2-seam-contract.md).
CONTEXT.md `### Tournaments` carries **Series length** (revised) + **Series
escalation** (added at grilling time — both already written, not re-touched).
Migration: `matches/migrations/0035_*` (ops in **pinned order**
`RemoveField(Tournament.series_length)` → 4× `AddField(Tournament.*_series_length)`
→ `AddField(BracketNode.series_length)` — **no `RunPython`, no backfill**,
ADR-0004 disposable-sandbox precedent; dep `0034_tournament_series`).

**Depth-from-final anchoring.** A node's Series length is resolved from its
**depth below the final**, `depth = total_rounds - bracket_round` (where
`total_rounds = max(spec.bracket_round …)` for the built bracket): **depth 0** =
the final, **depth 1** = semifinal, **depth 2** = quarterfinal, **depth ≥ 3** =
every earlier round (all collapse to the single `earlier` slot — no fifth tier).
**No monotonicity constraint** — the four slots may be any of `{1,3,5}` in any
order (a Bo5 quarterfinal feeding a Bo1 final is permitted; the model does not
enforce escalation, the user picks four independent values).

**Model changes (`matches/models.py`).** The LG-02b `Tournament.series_length`
flat field is **DROPPED wholesale** — no alias, no property shim; every reader
moves to `node.series_length` (see below). **ADD four
`PositiveSmallIntegerField`s** to `Tournament`, declared in the block where
`series_length` lived, in order `final_series_length`, `semifinal_series_length`,
`quarterfinal_series_length`, `earlier_series_length` — each with the identical
choices `(1, "Best of 1")`/`(3, "Best of 3")`/`(5, "Best of 5")` and `default=1`,
set at create-time only and **immutable once the Tournament leaves `setup`** (the
resolved N is frozen onto each node at lock time; the four fields are never
re-read after lock). **Bo1-everywhere (all four `1`, the migration default) is
byte-equivalent to LG-02b/LG-02a** — every node stamped `1`,
`series_winner_slot(1, 0, 1) == "a"`, single-Match clinch. **ADD
`BracketNode.series_length`** (`PositiveSmallIntegerField`, `default=1`, **no
choices** — it carries the already-resolved int, mirroring how `seed_a`/`seed_b`
carry resolved ints without choices). It is the **resolved** best-of-N for the
node, stamped at lock time on **every** persisted node including bye nodes (bye
nodes get a depth-resolved value but are **inert** — the engine skips `is_bye`).
`SeriesMatch` (model, `related_name="series_matches"`, `uniq_seriesmatch_node_game`,
`Meta.ordering=["game_number"]`) and `count_series_wins(series_matches, team_a_id,
team_b_id) -> tuple[int, int]` are **unchanged**.

**`lock_and_build` stamps the node (`matches/models.py`).** Inside the existing
`@transaction.atomic` `lock_and_build`, after `build_bracket(...)` produces
`specs`, compute `total_rounds = max(spec.bracket_round for spec in specs)` and add
the kwarg `series_length=series_length_for_round(spec.bracket_round, total_rounds,
final=self.final_series_length, semifinal=self.semifinal_series_length,
quarterfinal=self.quarterfinal_series_length, earlier=self.earlier_series_length)`
to the **existing** `BracketNode.objects.create(...)` loop (both `spec.bracket_round`
and `total_rounds` are in scope there — no follow-up pass needed; `series_length_for_round`
is imported alongside the existing `build_bracket`/`resolve_bye_chain`/`ParticipantSpec`
block). This stamps EVERY persisted node incl. byes; the `resolve_bye_chain` cascade
and `advances_to` wiring passes are untouched.

**Pure `matches/bracket.py` addition** (frozen `dataclasses`/`typing`/`math`/
`collections`-only allowlist unchanged — the new function adds **no new import**;
`TestNoDjangoImportsLeaked` still passes). NEW
`series_length_for_round(bracket_round: int, total_rounds: int, *, final: int,
semifinal: int, quarterfinal: int, earlier: int) -> int`: `bracket_round` and
`total_rounds` are **positional**, the four slot args are **keyword-only** (after
`*`). Algorithm (locked, pure, total, never raises): `depth = total_rounds -
bracket_round`; `depth == 0` → `final`; `depth == 1` → `semifinal`; `depth == 2`
→ `quarterfinal`; **`else` (depth ≥ 3, or any defensive out-of-range value) →
`earlier`** (the `if/elif/elif/else` chain makes `earlier` the catch-all; no
validation of the four slot values — callers pass the locked 1/3/5 choices).
`clinch_threshold`, `series_winner_slot`, `count_series_wins`, `find_next_node`,
`build_bracket`, `advance_winner`, `resolve_bye_chain`, `break_tie`,
`stage_progress`, `default_seed_order`, and the two dataclasses are all
**unchanged** — `find_next_node` still reads the `series_length` dict key (now
sourced from `node.series_length` via `_node_to_dict`), no edit to it.

**Seam read-source swap.** The clinch math is untouched; the ONLY change is
*where the `series_length` argument comes from* — tournament-level → **node-level**:
- `_node_to_dict(node)` keeps every key it has today; its `"series_length"` value
  now reads `node.series_length` (was `node.tournament.series_length`). `wins_a` /
  `wins_b` (via `count_series_wins`) and `advances_to` (the self-FK) are
  unchanged. Post-swap `_node_to_dict` has **no** `node.tournament` access.
- `tournament_engine.py::play_next_node` (`@transaction.atomic`, body otherwise
  verbatim): the step-6 clinch check reads `series_winner_slot(wins_a, wins_b,
  node.series_length)` (was `node.tournament.series_length`). Every other step
  (find playable node, sim ONE Match, `break_tie` fallback,
  `SeriesMatch.objects.create`, recompute via `count_series_wins`, stamp
  `node.winner` + `save(update_fields=["winner"])`, `advance_winner`,
  `champion`/`state="completed"` on the final) is unchanged. Because no flatten/
  advance-path code reads `node.tournament` post-swap, `select_related("tournament")`
  **may be dropped** from the `play_next_node` flat-list build and
  `find_next_playable_node` (keep `"advances_to"` + the `series_matches` prefetch)
  — a perf nicety, **not pinned** (no query-count assertion; leaving it in is also
  fine). The Code agent confirms no residual `node.tournament` reader before dropping.

**View / template surface.** Create form (`tournament_create` /
`tournament_create.html`): the single `series_length` POST parse is replaced by
**four** — POST fields `final_series_length` / `semifinal_series_length` /
`quarterfinal_series_length` / `earlier_series_length`, each int-coerced with a
forgiving fallback to `1` on `TypeError`/`ValueError` then forced into `{1,3,5}`
(anything else → `1`) **independently**, **no monotonicity constraint**; the
create call passes all four (`Tournament.objects.create(name=..., state="setup",
final_series_length=..., semifinal_series_length=..., quarterfinal_series_length=...,
earlier_series_length=...)`). Rendered as **four `<select>`s** (each Bo1/Bo3/Bo5,
**Bo1 selected by default**) in `tournament-create-form` before the submit, with
locked DOM ids `tournament-create-final-series-length` /
`tournament-create-semifinal-series-length` /
`tournament-create-quarterfinal-series-length` /
`tournament-create-earlier-series-length` (the old single
`tournament-create-series-length` id is **removed** — no element carries it).
Detail page (`_build_rounds` / `tournament_detail.html`): each node view-dict
**gains** `series_length: int` ← `node.series_length` (read straight off the node
row the loop already iterates); `_detail_context` keeps its frozen LG-02a/LG-02a-2
keys verbatim (`tournament`, `participants`, `rounds`, `next_node`, `is_locked`,
`can_play`, `import_form`, `import_row_errors`) — **no new top-level context key**.
For each **non-bye** node the template renders a Bo-N label beside the existing
`tournament-node-series-score-{br}-{pos}` element, locked DOM id
**`tournament-node-series-length-{bracket_round}-{position}`**, **label text shape
`Bo{n}`** (`Bo{{ node.series_length }}` — e.g. `Bo1`, `Bo3`, `Bo5`); bye nodes have
no Series and get no such label. The existing series-score element and the
`tournament-champion-banner` are unchanged.

**Admin (`matches/admin.py`).** The four new `Tournament` fields and
`BracketNode.series_length` **auto-surface** in the default change forms (editable
`PositiveSmallIntegerField`s with `choices` render as `<select>`s with no
`fields`/`fieldsets` declaration). **No `list_display` change** on `TournamentAdmin`
or `BracketNodeAdmin` (existing tuples stay verbatim).

**Non-deterministic** (`simulate_match` draws fresh per-round seeds) ⇒ **no SIM-07/
SIM-08 interaction, NO Score Calibration re-baseline** (no simulation mechanics
change). **Scope-out (LOCKED — do NOT build):** per-node arbitrary override UI
(escalation is depth-anchored via four slots only); monotonicity enforcement
(none); home/away side alternation (sides fixed); any clinch-engine /
`simulate_match` change (consumed verbatim); deterministic / master-seed-replayable
Series; backfill / `RunPython`; a fifth depth tier (depth ≥ 3 collapse to
`earlier`); any new CONTEXT.md term / new ADR (Series length revised + Series
escalation added already; ADR-0020 already extended).

**Locked names.** Models: **DROP** `Tournament.series_length`; **ADD**
`Tournament.final_series_length` / `.semifinal_series_length` /
`.quarterfinal_series_length` / `.earlier_series_length` (each
`PositiveSmallIntegerField`, choices `1`/`3`/`5`, default `1`) + `BracketNode.
series_length` (`PositiveSmallIntegerField`, default `1`, no choices, stamped at
`lock_and_build`); migration `matches/migrations/0035_*` (dep
`0034_tournament_series`; ops `RemoveField(Tournament.series_length)` → 4×
`AddField(Tournament.*)` → `AddField(BracketNode.series_length)`; no `RunPython`).
Pure: `series_length_for_round(bracket_round, total_rounds, *, final, semifinal,
quarterfinal, earlier) -> int` (`depth = total_rounds - bracket_round`; 0→final,
1→semifinal, 2→quarterfinal, ≥3→earlier; four slot args keyword-only). Engine:
`play_next_node` clinch check reads `node.series_length`; `select_related(
"tournament")` droppable. Create: POST `final_series_length` /
`semifinal_series_length` / `quarterfinal_series_length` / `earlier_series_length`
(int-coerced, forced into `{1,3,5}`, forgiving fallback `1`, no monotonicity);
selects `tournament-create-{final,semifinal,quarterfinal,earlier}-series-length`
(Bo1 default); old `tournament-create-series-length` removed. Detail:
`_build_rounds` node dict gains `series_length`; per-non-bye-node Bo-N label
`tournament-node-series-length-{bracket_round}-{position}`, text `Bo{n}`.

**Tests.** `matches/tests/test_bracket.py` (extend — `series_length_for_round`
depth boundaries + N=4/8/16 worked cases + keyword-only purity; clinch helpers +
`TestNoDjangoImportsLeaked` still green); `test_tournament_models.py` (extend/migrate
— `lock_and_build` stamps `node.series_length` per depth **incl. byes**, the four
new fields exist/default `1`/carry the 1/3/5 choices, `BracketNode.series_length`
exists/defaults `1`, the old `Tournament.series_length` field is **gone**,
`_node_to_dict` reads `node.series_length`); `test_tournament_views.py` (extend/
migrate — four selects by DOM id default Bo1 + old id absent, POST persists all
four, independent forgiving fallback, detail per-non-bye Bo-N label by DOM id);
`test_tournament_engine.py` (extend/migrate — node reads its **own**
`series_length` not `node.tournament.*`, Bo3 clinches at 2, Bo1 unchanged);
`test_tournament_tasks.py` (migrate the `_active_series_tournament` helper to the
four-field shape). Tests assert on the pure function, the stamped
`BracketNode.series_length`, the four `Tournament` fields, the absence of the old
field, the DOM ids, and `node.winner`/Advancement — **never on exact simulated
point totals** (non-deterministic).

## LG-02c double-elimination tournaments

Extends the LG-02a/b single-elimination `BracketNode` tree into **two coupled
brackets** — a **Winners bracket** and a **Losers bracket** joined by a **Grand
final with Bracket reset** — as a second `Tournament.format` enum value driven by
a new pure builder, hosting **both** sub-brackets in the *existing* `BracketNode`
table (one table + a sub-bracket tag, **not** a second `LoserBracketNode` model).
The single-elim path is **byte-unchanged** end to end. Builds directly on LG-02b
(the `SeriesMatch` clinch engine) and LG-02b-2 (depth-from-final escalation, here
re-anchored to depth-from-Grand-final). See
[ADR-0021](../../docs/adr/0021-double-elimination-bracket.md) for the
two-bracket-one-table / Bracket-reset / naive-same-position-drop decision and its
rejected alternatives (separate LB table, single GF no reset, power-of-two-only,
per-node Series override); seam contract:
[`.claude/worktrees/lg-02c-seam-contract.md`](../../.claude/worktrees/lg-02c-seam-contract.md).
CONTEXT.md `### Tournaments` carries **Winners bracket** / **Losers bracket** /
**Drop** / **Grand final** / **Bracket reset** (added at grilling time — already
written, not re-touched; ADR-0021 likewise already written). Migration:
`matches/migrations/0036_*` (dep `0035_tournament_series_escalation`; ops in
**pinned order** `AlterField(Tournament.format)` — choices-only widen, no DB-level
enforcement, included so `makemigrations --check` is clean → 3×
`AddField(BracketNode.bracket_type / loser_advances_to / loser_advances_to_slot)`
→ `RemoveConstraint(uniq_tournament_round_position)` →
`AddConstraint(uniq_tournament_bracket_round_position)` — **no `RunPython`, no
backfill**, ADR-0004 disposable-sandbox precedent).

**Model changes (`matches/models.py`).** `Tournament.FORMAT_CHOICES` gains
`("double_elimination", "Double elimination")` — the `format` field declaration is
otherwise unchanged (`CharField(max_length=32)`, `default="single_elimination"`).
`BracketNode` gains three fields: **`bracket_type`** (`CharField(max_length=12)`,
choices `"winners"`/`"losers"`/`"grand_final"`, **`default="winners"`** so
single-elim rows default cleanly — the exact strings are LOCKED; the engine
ordering, the view section split, and the DOM ids all key on them); a
**loser-destination** pointer **`loser_advances_to`** (self-FK, `null=True`,
`on_delete=SET_NULL`, related_name **`"loser_feeders"`** — parallels the winner
`advances_to` / related_name `"feeders"`) carrying where **THIS node's loser**
Drops; and **`loser_advances_to_slot`** (`CharField(max_length=1)`, choices
`a`/`b`, nullable). LB nodes set `loser_advances_to = NULL` (their loser is
eliminated); single-elim WB nodes likewise NULL (loser eliminated exactly as
today — byte-unchanged). The LG-02a `uniq_tournament_round_position` constraint
(fields `["tournament", "bracket_round", "position"]`) is **removed** and replaced
by **`uniq_tournament_bracket_round_position`** which **adds `bracket_type`** to
the field tuple — so a WB and an LB node may now share `(bracket_round, position)`
while a duplicate within one `bracket_type` is still rejected. `Meta.ordering`
stays `["bracket_round", "position"]` (the engine re-sorts via `find_next_node`'s
total order; the view groups by `bracket_type`). `SeriesMatch` /
`count_series_wins` / the `series_matches` join are **UNCHANGED**.

**`lock_and_build` branches on `format` (`matches/models.py`).** A **single
dispatch** at the top of the `@transaction.atomic` build: `if self.format ==
"double_elimination":` build via `build_double_elim_bracket([ParticipantSpec(...)
for p in participants])`, **else** `build_bracket(...)` (the existing path,
byte-unchanged). The persist loop, the `advances_to` wiring pass, the
`resolve_bye_chain` cascade pass, and the `series_length` stamping pass are
**shared** across both formats, with three DE-only additions: (1) persist
`bracket_type=spec.bracket_type` on every `BracketNode.objects.create(...)`; (2) a
**third wiring pass** (after the `advances_to` pass) wires the
`loser_advances_to` self-FK from `spec.loser_advances_to` — a **triple**
`(bracket_type, bracket_round, position)` coord, since a WB→LB Drop crosses
brackets — plus `loser_advances_to_slot = spec.loser_advances_to_slot` (the
`node_by_pos` key becomes the triple for DE; single-elim may keep the 2-tuple or
adopt `("winners", …)` — internal, not asserted); (3) `series_length` stamping
uses **`series_length_for_depth(spec.depth, …)`** for DE (the spec carries `depth`
directly) versus the existing `series_length_for_round(spec.bracket_round, …)` for
single-elim, which is **byte-unchanged**. Every DE node — **including byes** — is
stamped. The `>= 4` participant guard, the `state != "setup"` guard, and the
`state="active"` flip are unchanged. `find_next_playable_node` keeps its
signature; its prefetch **widens** to `select_related("advances_to",
"loser_advances_to")` so `_node_to_dict` reads the loser pointer without an N+1,
and the match-back loop adds `bracket_type` to the comparison key.

**`_node_to_dict` gains three keys (`matches/models.py`).** On top of every
existing key it adds **`bracket_type`** (`node.bracket_type`),
**`loser_advances_to`** — a **3-tuple** `(node.loser_advances_to.bracket_type,
.bracket_round, .position)` or `None` — and **`loser_advances_to_slot`**
(`"a"`/`"b"`/`None`). **Deliberate asymmetry (LOCKED):** the existing
`advances_to` key **stays a 2-tuple** `(bracket_round, position)` (back-compat with
single-elim `advance_winner`, whose winner stays in the same bracket), while
`loser_advances_to` is a **3-tuple** carrying the destination bracket (the Drop
crosses brackets). For single-elim rows the three new keys read
`("winners", None, None)` ⇒ no downstream behaviour change.

**Pure `matches/bracket.py` additions** (frozen `dataclasses`/`typing`/`math`/
`collections`-only allowlist **unchanged** — the new functions add **no new
import**; `TestNoDjangoImportsLeaked` stays green). `BracketNodeSpec` gains four
**appended-with-defaults** fields (`bracket_type="winners"`,
`loser_advances_to=None` 3-tuple `(bracket_type, round, position)`,
`loser_advances_to_slot=None`, `depth=None` — distance-to-GF1) so `build_bracket`'s
existing first-10-field construction stays valid **byte-for-byte**.
**`series_length_for_depth(depth, *, final, semifinal, quarterfinal, earlier) ->
int`** is extracted as the pure depth→slot dispatch (0→final, 1→semifinal,
2→quarterfinal, ≥3→earlier via an if/elif catch-all — total, never raises; the
four slot args keyword-only); **`series_length_for_round` is refactored to
delegate** to it (`series_length_for_depth(total_rounds - bracket_round, …)`) with
**byte-identical** public signature + behaviour (single-elim escalation
transparent). **`build_double_elim_bracket(participants) -> list[BracketNodeSpec]`**
emits the full two-tree node-spec list for arbitrary **N ≥ 4 with byes**: the WB
is the existing single-elim tree (size = next pow2 ≥ N, top `(size − N)` seeds get
WB byes, reusing `build_bracket`'s seeding/pairing); the LB consumes WB-round
losers via a **naive same-position drop** (loser of WB-round-*r* position *i* →
the matching LB slot by position — **NO anti-rematch folding** this slice, a known
limitation deferred); each WB node's `loser_advances_to` points at its LB
destination + slot; GF1 (the lower `bracket_round`, `bracket_type="grand_final"`)
takes the WB champ (slot "a") + LB champ (slot "b"), GF1's `loser_advances_to`
points at **GF2** (so the LB-champ path Advances both into GF2), GF2's
`advances_to`/`loser_advances_to` are `None` (final node); **GF1/GF2 depth = 0**,
WB-final & LB-final depth 1, etc. Raises `ValueError` on `len < 4` or duplicate
seeds/team_ids (mirrors `build_bracket`). The WB/LB `(bracket_round, position)`
numbering is the builder's internal choice — only the cross-bracket wiring coords
+ `depth` are asserted.

**`advance_loser` is a SEPARATE pure function (`matches/bracket.py`).**
`advance_loser(nodes, node_position=(bracket_type, bracket_round, position),
loser_id, loser_seed) -> list[dict]` returns the parent-slot mutations that Drop
the loser into the resolved node's `loser_advances_to` slot — each mutation dict
carries `bracket_type`/`bracket_round`/`position`/`slot`/`team_id`/`seed`; empty
list when `loser_advances_to is None` (LB node, GF2, single-elim WB node).
**DECISION LOCKED: this is NOT a generalization of `advance_winner`** —
`advance_winner` stays **byte-unchanged** (its mutation dicts have **no
`bracket_type` key**, preserving single-elim and every existing
`test_advance_winner` case), and the engine makes **two explicit calls**
(`advance_winner` then `advance_loser`) on a WB/GF1 clinch. `resolve_bye_chain` is
**generalized to collapse Drop byes**: a WB **Bye** produces a winner but **no
loser**, so the LB slot its `loser_advances_to` points at receives no Drop — if
that slot's only feeder is the bye's loser-drop, the LB node has a permanently
empty slot and **collapses** (surviving opponent auto-advances, `is_bye=True`,
`winner_id` set), exactly as the existing winner-side bye cascade collapses an
unopposed slot. Its single-elim behaviour is **byte-identical** (every existing
`test_resolve_bye_chain` case green; still returns `[]` with no byes); DE
collapse/loser-drop mutations gain a `bracket_type` key, winner-side mutations
keep their existing shape. `find_next_node` keeps its **playable predicate
byte-identical** and changes **only its sort key** to `(_BRACKET_RANK
winners<losers<grand_final, bracket_round asc, position asc)` — for a single-elim
field every node is rank-0 `"winners"` so the order **collapses to
`(bracket_round, position)`** exactly as today; the bracket rank is purely a
deterministic tiebreak when multiple cross-bracket nodes are simultaneously ready.
`stage_progress` generalizes: **`total`** = count of distinct
`(bracket_type, bracket_round)` groups (was `max(bracket_round)`; single-elim
collapses to the same number, byte-unchanged), **`completed`** = count of groups
where every non-bye, non-inert node has `winner_id is not None` (an inert
auto-resolved GF2 already satisfies `winner_id is not None`, so no special-case),
`(0, 0)` on empty. `clinch_threshold`, `series_winner_slot`, `count_series_wins`,
`advance_winner`, `build_bracket`, `break_tie`, `default_seed_order`, and
`ParticipantSpec` are **UNCHANGED**.

**Engine (`matches/tournament_engine.py::play_next_node`).** Stays **ONE**
per-Match-atomic loop for **both** formats — the body is **verbatim** through the
clinch check (find next playable node → sim ONE Match → `break_tie` fallback →
`SeriesMatch.objects.create` → recompute via `count_series_wins` →
`series_winner_slot(…, node.series_length)` → return `node` un-advanced when
`slot is None`). On clinch the tail changes: (1) stamp `node.winner` +
`winner_seed`, `save(update_fields=["winner"])` (unchanged); (2) build the flat
list via `_node_to_dict` over `tournament.nodes.select_related("advances_to",
"loser_advances_to").prefetch_related("series_matches")` (adds
`"loser_advances_to"`); (3) **winner advance** (`advance_winner` — unchanged
shape, parent resolved within the winner's own `advances_to` target bracket so the
GF1→GF2 cross is handled by GF1's own pointer); (4) **loser Drop** (DE only) —
when `node.bracket_type in ("winners", "grand_final")` AND
`node.loser_advances_to_id is not None`, compute the non-winning slot's team+seed
and call `advance_loser`, applying each mutation to the LB/GF2 parent keyed by
`(mut["bracket_type"], mut["bracket_round"], mut["position"])`; a **single-elim WB
node** has `loser_advances_to_id is None` ⇒ `advance_loser` is skipped / returns
`[]` ⇒ loser eliminated exactly as today; (5) **Grand-final Bracket reset** — on a
clinched **GF1** (its `advances_to` points at GF2): if the GF1 winner **==** the
WB champ (slot "a"), stamp **`GF2.winner` inert** (`save(update_fields=["winner"])`
so `find_next_node` never returns GF2 — the bye-style auto-resolve precedent) +
`tournament.champion` + `state="completed"` immediately; if the GF1 winner == the
LB champ (slot "b"), the step-4 Drop has already Advanced the WB champ into GF2
slot "a" and the winner-advance into GF2 slot "b" ⇒ **GF2 is now playable**, no
champion yet; (6) **final node** (single-elim final OR GF2, `advances_to_id is
None`) stamps `tournament.champion` + `state="completed"` (unchanged shape).
**Callers unchanged in signature/route:** `tournament_play_next`,
`play_tournament_task` (loops `play_next_node` unchanged), `tournament_play_all`,
`tournament_play_status` keep their URLs + 5-key status JSON; `stage_progress`
now reports stage completion across **both brackets + GF**.

**View / template surface.** Create form (`tournament_create` /
`tournament_create.html`): reads a new POST field **`format`** (forgiving-fallback
mirroring the series-length parses — accept only `"single_elimination"` /
`"double_elimination"`, anything absent/tampered falls back to
`"single_elimination"`) and passes it as the `format=` kwarg into the existing
`Tournament.objects.create(...)`; rendered as one `<select name="format">` with
DOM id **`tournament-create-format`** (options "Single elimination" selected /
"Double elimination"), placed above the four series-length selects — every other
create-form id (`-name`, `-team-select`, `-generate-*`, the four `-*-series-length`,
`-submit`, `-no-teams-notice`) unchanged. Detail page (`_build_rounds` /
`tournament_detail.html`): each node view-dict gains **`bracket_type`**, and
`_build_rounds`'s **return shape changes** from a flat `[{bracket_round, nodes}]`
to a **3-key dict** `{"winners": […], "losers": […], "grand_final": […]}` — for a
single-elim tournament `"losers"` and `"grand_final"` are **empty lists** and
`"winners"` carries the full tree (no single-elim render regression). The frozen
`_detail_context` keys are unchanged — **only the *value-shape* of `rounds`
changes**, no new top-level context key. The template renders **three sections**
(DOM ids `tournament-bracket-winners` / `-losers` / `-grand-final`, per-round
column `tournament-bracket-{bracket_type}-round-{n}`) reusing the existing
node-card + series-score + Bo-N-label markup. **Single-elim keeps the legacy DOM
ids (LOCKED):** the template **branches on `tournament.format`** — single-elim
renders `tournament-bracket` / `tournament-bracket-round-{n}` /
`tournament-node-{round}-{position}` / `-series-score-{round}-{position}` /
`-series-length-{round}-{position}` (every LG-02a/b view test stays green, Losers
& Grand-final sections **absent**), while DE renders the namespaced
**`tournament-node-{bracket_type}-{bracket_round}-{position}`** /
`-series-score-{bracket_type}-{br}-{pos}` / `-series-length-{bracket_type}-{br}-{pos}`.
The Grand-final section renders both GF1 and GF2 cards; the inert auto-resolved
GF2 (WB-champ-wins case) carries the `bye-node` class so the existing "no
series-score for bye nodes" branch suppresses its Bo-N label cleanly.
`tournament-champion-banner`, the Play controls (`-lock-form` / `-play-next-form`
/ `-play-all-form` / `-play-all-progress` / `-play-all-error` + the poll JS), the
import + seeding form ids, and `tournaments-nav-link` are **unchanged**. The three
new `BracketNode` fields auto-surface in the default `BracketNodeAdmin` change
form and the widened `format` choices in `TournamentAdmin` — **no `list_display` /
inline / registration change**.

**Tests.** `matches/tests/test_bracket.py` (extend — `TestSeriesLengthForDepth`
depth boundaries + keyword-only + delegation parity with
`series_length_for_round`; `TestBuildDoubleElimBracket` N=4/8 no-bye + N=5/6 WB-bye
node counts / loser-drop coords / GF1→GF2 / depth / `ValueError`;
`TestAdvanceLoser`; `TestResolveByeChainDropBye` collapse; `TestFindNextNodeBracketOrder`;
`TestStageProgressDoubleElim`; `TestNoDjangoImportsLeaked` + every existing
single-elim case still green); `test_tournament_models.py` (extend —
`TestBracketNodeDoubleElimFields` defaults/choices/renamed constraint,
`TestLockAndBuildDoubleElim` N=4/6 wiring + per-depth `series_length` incl. byes,
`TestLockAndBuildSingleElimUnchanged` regression, extended `Test_node_to_dict` DE
3-tuple keys + single-elim `("winners", None, None)`); `test_tournament_views.py`
(extend — `TestCreateFormFormat` select + persist + fallback,
`TestDetailDoubleElimSections` three containers + DE ids,
`TestDetailSingleElimIdsUnchanged` legacy-id regression);
`test_tournament_engine.py` (extend — `TestPlayNextNodeDoubleElimDrop` WB loser
Drop, `TestPlayNextNodeGrandFinalReset` both reset branches,
`TestPlayNextNodeSingleElimUnchanged` regression); `test_tournament_tasks.py`
(extend — `TestPlayTournamentTaskDoubleElim` drains a DE bracket to a champion,
stage counts over both brackets + GF). Tests assert on the pure functions, the
persisted `BracketNode` fields (`bracket_type` / `loser_advances_to` /
`series_length`), `node.winner` / both-bracket Advancement / `tournament.champion`
/ `state`, and the DOM ids — **never on exact simulated point totals**
(non-deterministic ⇒ **no SIM-07/SIM-08 interaction, NO Score Calibration
re-baseline**).

**Scope-out (LOCKED — DEFERRED, do NOT build here):** **anti-rematch folding** in
the Losers bracket (LB consumes WB losers via a naive same-position drop only —
folding deferred to a follow-up); **round robin / RR→double-elim / Swiss** (the
`format` enum is extensible but only `single_elimination` + `double_elimination`
ship); a **single Grand final (no reset)** (rejected — the Bracket reset GF1+GF2
is the locked ADR-0021 design); a **separate `LoserBracketNode` model / second
table** (rejected — one table + `bracket_type` tag); **`advance_winner`
generalization** (stays byte-unchanged; a separate `advance_loser` carries the
loser path); **home/away side alternation** (sides fixed `team_a` red / `team_b`
blue every Match, LG-02b locked); **deterministic / master-seed-replayable
Series** (`simulate_match` draws fresh per-round seeds ⇒ non-deterministic);
any `simulate_match` / `simulate_scheduled_round` change (consumed verbatim,
`arena_map=None` 3-zone fallback per Match); **backfill / `RunPython`** (none —
pure forward-only schema ops, ADR-0004); in-League / in-Season tournament
embedding (Tournament stays standalone, `season`-less); any new CONTEXT.md term
beyond **Winners bracket** / **Losers bracket** / **Drop** / **Grand final** /
**Bracket reset** (already written) and any new ADR (ADR-0021 already written).

## LG-02c round robin tournaments

Adds a third `Tournament.format` enum value **`"round_robin"`** (label
`"Round robin"`): a **flat double round-robin** where every enrolled Team plays
every other **twice** (one fixture per leg), with **NO advancement** — the
champion is simply the **Standings** leader once every node is resolved. Unlike
the elim formats, RR has no bracket tree: it is a flat set of `BracketNode` rows
**with no `advances_to` / `loser_advances_to` edges**. It reuses three existing
pure seams verbatim — `matches/schedule_generator.py::generate_schedule` (the
fixture list), `matches/standings.py::compute_standings` (the ranked table), and
the LG-02b `SeriesMatch` clinch engine (Bo1 per fixture) — so it ships as a
**choices widen + two `Tournament` methods + one engine guard + a `_BRACKET_RANK`
entry**, with **no new pure builder** and **no `matches/bracket.py` import-allowlist
change**. The single- and double-elim paths are **byte-unchanged**. See
[ADR-0021](../../docs/adr/0021-double-elimination-bracket.md) (extended — its
Consequences set the "new format = new enum value + reused/new pure seam"
precedent, and the round-robin extension was finalised at grilling time, not
re-touched here); seam contract:
[`.claude/worktrees/lg-02c-round-robin-seam-contract.md`](../../.claude/worktrees/lg-02c-round-robin-seam-contract.md).
**No new CONTEXT.md term** — RR reuses **Tournament** / **Bracket node** /
**Standings** vocabulary. Migration:
`matches/migrations/0037_tournament_round_robin.py` (dep
`0036_bracketnode_double_elimination`; two `AlterField`s — `Tournament.format`
choices widen + `BracketNode.bracket_type` choices widen, choices-only, no DB-level
enforcement, included so `makemigrations --check` is clean — **no `RunPython`, no
backfill**, ADR-0004 disposable-sandbox precedent).

**Enum / `_BRACKET_RANK` literals (LOCKED).** `Tournament.FORMAT_CHOICES` gains
`("round_robin", "Round robin")` as its **third** entry (field declaration
unchanged — `CharField(max_length=32)`, `default="single_elimination"`).
`BracketNode.bracket_type` choices gain `("round_robin", "Round robin")` as their
**fourth** entry (`"round_robin"` is 11 chars, fits the existing `max_length=12`;
field declaration otherwise unchanged, `default="winners"`).
`matches/bracket.py::_BRACKET_RANK` gains **`"round_robin": 3`** (rank 3) — RR
nodes never coexist with WB/LB/GF nodes in one Tournament, so the absolute rank is
cosmetic, but the entry is **required** so `_BRACKET_RANK.get(...)` never falls back
to the `0` default for an RR node (defence in depth, asserted in the pure test). It
is a pure dict-literal edit — **no new import**, `TestNoDjangoImportsLeaked` stays
green.

**Structure — flat `BracketNode` set, no edges.** A round-robin Tournament is
**one `BracketNode` row per fixture** from the FULL output of
`generate_schedule(team_ids)` — that full output **is** a double round-robin (each
unordered pair appears **twice**, once per `round_number` leg `1`/`2`). For N=4 it
yields 12 fixtures (6 per leg); N=6 → 30; N=8 → 56; odd N drops the bye sentinel
`-1`. Each `ScheduleFixture` is the frozen dataclass `(matchday: int 1-based,
round_number: int 1|2, team_a_id: int = min(pair), team_b_id: int = max(pair))`,
sorted `(matchday, team_a_id)` and a pure function of the *set* of `team_ids`.

**`lock_and_build` third branch (`matches/models.py`).** The fixtures→`BracketNode`
build lives in the **MODEL layer** as a third `format` branch alongside the
existing single/double-elim branches — **no new builder is added to
`matches/bracket.py`**; `generate_schedule` (in the also-frozen
`schedule_generator.py`) is **deferred-imported inside `lock_and_build`** (`from
.schedule_generator import generate_schedule`, joining the existing deferred
`from .bracket import (...)` block). The pre-existing `>= 4`-participant guard and
the `state != "setup"` guard **precede** the dispatch unchanged, and the
`state="active"` + `save(update_fields=["state"])` tail is **shared** and
unchanged. The RR branch builds a `team_by_id` map and a `seed_by_team` map from
`self.participants`, calls `generate_schedule(team_ids)`, and creates one node per
fixture with the **LOCKED kwarg set**: `tournament=self`,
**`bracket_round=fixture.matchday`** (1-based), **`position`** = the **0-based
index of the fixture within its matchday** (enumerate each matchday group in
`generate_schedule` order), **`bracket_type="round_robin"`**,
`team_a=team_by_id[fixture.team_a_id]` / `team_b=team_by_id[fixture.team_b_id]`
(both **FIXED at lock**), `seed_a`/`seed_b` from `seed_by_team`, `is_bye=False`,
`advances_to_slot=None`, `loser_advances_to_slot=None`, `winner=None`, and
**`series_length=1`** (Bo1 — RR is always best-of-1). The `(bracket_round=matchday,
position=index-in-matchday)` pair is unique within `bracket_type="round_robin"`, so
it satisfies the existing `uniq_tournament_bracket_round_position` constraint. After
create the FK pointers `advances_to` / `loser_advances_to` are left **unset (both
`None`)** — RR nodes never advance: the branch runs **NO `advances_to` /
`loser_advances_to` wiring pass and NO `resolve_bye_chain`**. (`generate_schedule`
raises `ValueError` on `len < 2`, but the `>= 4` guard already prevents that — no
extra guard needed.)

**Two new `Tournament` methods (`matches/models.py`) — REUSE `compute_standings`.**
Both deferred-import `from .standings import compute_standings` (itself a frozen
pure module). The reused seam (verified, LG-06g):
`compute_standings(completed_matches, enrolled_teams, season_rounds=None) ->
list[StandingsRow]` — `completed_matches` is a list of **9-key** dicts (`match_id,
team_red_id, team_blue_id, winner_team_id` [`int | None`, `None`=tie],
`red_rounds_won, blue_rounds_won, red_total_points, blue_total_points,
date_played`), `enrolled_teams` is a list of `(team_id, team_name)` tuples (every
enrolled team — teams with no matches get a zero-filled row), `season_rounds` is a
list of **6-key** dicts (`round_id, team_red_id, team_blue_id, red_points,
blue_points, date_played`); it returns the **17-field** frozen `StandingsRow`
(`team_id, matches_played, wins, losses, ties, league_points, round_wins,
total_score, rank, match_streak, match_l5, round_streak, round_l5, red_wlt,
blue_wlt, red_points_for, blue_points_for`), sorted `league_points desc → round_wins
desc → total_score desc → team_name asc` with a 1-based dense `rank`
(`league_points = 3*wins + 1*ties`).

- **`round_robin_standings(self) -> list[StandingsRow]`** assembles the three
  `compute_standings` inputs from this Tournament's RR nodes and returns the ranked
  rows — used by BOTH the engine (champion) and the detail view (standings table),
  and returns a row for **every enrolled team** (zero-filled before any play).
  `enrolled_teams` comes from `self.participants.select_related("team")`
  (`(p.team_id, p.team.name)`); `completed_matches` is one 9-key dict per RR node
  whose `winner_id is not None` (its single Bo1 `SeriesMatch` has been played),
  reading `match_id` / side ids / rounds-won / total-points / `date_played` off the
  played `Match` (side-faithful — `team_a` plays red, `team_b` blue per
  `simulate_match`, but read from the persisted `Match`) with the **LOCKED**
  `winner_team_id = node.winner_id` (the node winner — equals `match.winner_id` on a
  clean win and the `break_tie` result on a true tie, **never `None`** for a resolved
  RR node); `season_rounds` is one 6-key dict per persisted `GameRound` of each
  played node's `Match` (`GameRound.team_red` is the team that PHYSICALLY played red
  — SIM-08 — exactly what the side-split columns key on). **No seed-aware tiebreak
  override** — the champion uses `compute_standings`' built-in `team_name asc` final
  tiebreak (locked).
- **`complete_round_robin_if_finished(self) -> None`** parallels
  `Season.complete_if_finished` (LG-01) and is **idempotent**: no-op unless
  `format == "round_robin"` and `state == "active"`; the RR is finished iff **every**
  RR node (`self.nodes.filter(bracket_type="round_robin")`) has `winner_id is not
  None` (RR nodes are never `is_bye`, so no bye exclusion); if all resolved it sets
  `champion_id = round_robin_standings()[0].team_id` (Standings leader), `state =
  "completed"`, `save(update_fields=["champion", "state"])` — with a defensive `if
  rows:` guard before indexing (mirrors `Season.complete_if_finished`).

**Engine — `play_next_node` RR guard (`matches/tournament_engine.py`).**
`play_next_node` stays **ONE** per-Match-atomic loop for all three formats — its
body is **verbatim** through the clinch check (find next playable node → sim ONE
Match via `BatchSimulator().simulate_match(node.team_a, node.team_b,
match_type="tournament")` → `break_tie` fallback on `match.winner is None` →
`SeriesMatch.objects.create(...)` → recompute via `count_series_wins` →
`series_winner_slot(wins_a, wins_b, node.series_length)`; an RR node is
`series_length == 1`, so it clinches on its single Match exactly like a Bo1 elim
node). On clinch, **AFTER** stamping `node.winner` and **BEFORE** the elim
`_node_to_dict` flatten / `advance_winner` / `advance_loser` / crown block, a
**LOCKED guard `if tournament.format == "round_robin":`** runs
`tournament.complete_round_robin_if_finished()` then `return node` — skipping the
elim advance/crown logic entirely. This guard is **required**: because every RR
node has `advances_to=None`, the elim "crown when `advances_to` is `None`" rule
would otherwise wrongly crown a champion on the **first** resolved node; instead the
champion + completion are decided by `complete_round_robin_if_finished` only after
**all** nodes resolve. **`find_next_node` is UNCHANGED** — its playable predicate
(`team_a_id is not None and team_b_id is not None and not is_bye and
series_winner_slot(...) is None`) treats an unplayed RR node (`wins_a=0, wins_b=0`,
both slots filled, `is_bye=False`, `series_length=1`) as playable and a resolved one
as skipped, and its sort key `(_BRACKET_RANK[bracket_type], bracket_round,
position)` orders RR nodes deterministically by `(3, matchday, position)`.
**Callers unchanged:** `tournament_play_next` (sync view), `play_tournament_task`
(its `while play_next_node(...) is not None` loop drains every RR node one Match at
a time), `tournament_play_all`, `tournament_play_status` keep their URLs + the 5-key
status JSON; `stage_progress` (unchanged) reports per-`(bracket_type, bracket_round)`
group completion — for RR that is **per-matchday** progress, a sensible Play-All
stages readout.

**View / template surface.** Create form (`tournament_create` /
`tournament_create.html`): the existing `<select name="format">` (DOM id
**`tournament-create-format`**) gains a third option — value **`"round_robin"`**,
label **`"Round robin"`** — alongside single/double-elim; the view's
forgiving-fallback parse widens to accept `"round_robin"` (anything
absent/tampered still falls back to `"single_elimination"`), and the four
`*_series_length` POST fields are still parsed/passed but are **inert** for RR
(RR forces Bo1 at the node level). The four series-length selects
(`tournament-create-final-series-length` / `-semifinal-` / `-quarterfinal-` /
`-earlier-series-length`) are **hidden client-side** when the format select reads
`round_robin` (a small inline `onchange` toggle — behaviour pinned, exact JS at the
Code agent's discretion; no server-side change, the inert values do no harm).
Detail page (`tournament_detail` in `matches/tournament_views.py` /
`tournament_detail.html`): `_build_rounds` **keeps its 3-key elim dict**
`{"winners", "losers", "grand_final"}` (all three lists **empty** for an RR
Tournament — existing elim tests stay green), and RR rides on **two NEW top-level
context keys** added to `_detail_context` (defaulted `rr_crosstable=[]` /
`rr_standings=[]` for elim so the template references them unconditionally,
populated only in the RR branch): **`rr_crosstable`** (the N×N crosstable, a
`list[dict]` of per-team row descriptors in standings order — the precise nesting is
the Code agent's discretion, only the cell-mapping rule below and the DOM ids are
load-bearing) and **`rr_standings`** = `tournament.round_robin_standings()` (the
live standings table, zero-filled in `setup`/early `active`, final once
`completed`). The crosstable is built in a separate helper (suggested
`_build_rr_crosstable`), not by overloading `_build_rounds`.

**Crosstable cell-mapping rule (LOCKED).** The crosstable is N×N indexed
`cell[row_team][col_team]`. Each fixture has two legs (two nodes, `round_number==1`
and `round_number==2`); the node carries `team_a` (=`min(pair)` id) / `team_b`
(=`max(pair)` id) fixed at lock. **Leg `round_number == 1` → `cell[team_a][team_b]`**
(team_a is the row/home team for leg 1); **leg `round_number == 2` →
`cell[team_b][team_a]`** (team_b is the row/home team for the reverse fixture);
**diagonal `cell[t][t]` → always blank**. Because `generate_schedule` does **not**
persist `round_number` onto the `BracketNode` (the node only stores
`bracket_round=matchday` / `position`), the **view must recover each leg's
`round_number`** by re-deriving the schedule (`fixtures = generate_schedule(team_ids)`,
matching each persisted RR node to its fixture by the `(matchday,
position-within-matchday)` key the builder used) and reading `round_number` off the
matched `ScheduleFixture`. Each filled cell shows the leg's score from the **row
team's** perspective and links to the played Match; an unplayed leg renders empty /
"—". The RR branch renders **only** the two RR tables — outer `<table>` DOM ids
**`tournament-rr-crosstable`** and **`tournament-rr-standings`** — plus the reused
play controls; the elim WB/LB/GF section containers (`tournament-bracket*`,
single-elim `tournament-node-*`) are **absent** for RR, and conversely the two RR
ids are absent for elim (the template **branches on `tournament.format`**). RR nodes
are always Bo1, so the detail page renders **no** per-node series-score / Bo-N labels
for RR. **Reused VERBATIM** (no new ids, shared across all formats): the lock control
(`tournament-lock-form` / `-submit`), play-next (`tournament-play-next-form` /
`-submit`), play-all (`tournament-play-all-form` / `-submit` / `-progress` + poll JS),
the import + seeding forms, and the champion banner **`tournament-champion-banner`**
(rendered when `tournament.champion` is set — the RR completion path stamps it
identically). The widened `format` / `bracket_type` choices auto-surface in admin —
no `list_display` / inline / registration change.

**Determinism / scope.** **Non-deterministic** per-Match sims — `simulate_match`
draws fresh per-round seeds, so RR Tournament games are **NOT**
master-seed-replayable ⇒ **no SIM-07 / SIM-08 interaction, NO Score Calibration
re-baseline**. **No `simulate_match` / `simulate_scheduled_round` change** (consumed
verbatim, `arena_map=None` 3-zone fallback per node). **No new pure builder, no
`bracket.py` import-allowlist change** (only `_BRACKET_RANK` gains a literal entry).
**No backfill / `RunPython`** (ADR-0004). **No new ADR** (decisions reversible — a
choices widen + two `Tournament` methods + a deferred import). **No new CONTEXT.md
term** (RR reuses Tournament / Bracket node / Standings).

**Scope-out (LOCKED — DEFERRED, do NOT build here):** **RR → double-elimination**
(an RR seeding phase feeding the LG-02c DE bracket as a finals stage — still its own
grill) and **Swiss** (pairings-from-standings, ⌈log₂(N)⌉ rounds) both remain NOT
STARTED; **in-League / in-Season embedding** is out of scope — the RR Tournament
stays standalone and `season`-less, exactly like LG-02a/b/c; **deterministic /
master-seed-replayable Series** (`simulate_match` draws fresh per-round seeds);
**home/away side alternation** (sides fixed `team_a` red / `team_b` blue every Match,
LG-02b locked); any new CONTEXT.md term or new ADR.

**Tests.** `matches/tests/test_bracket.py` (extend — **`TestBracketRankRoundRobin`**:
`_BRACKET_RANK["round_robin"] == 3`, and a flat list of RR-only node dicts ordered by
`find_next_node` returns the lowest `(matchday, position)` UNPLAYED node and skips
clinched ones; `TestNoDjangoImportsLeaked` still green — no new import).
`matches/tests/test_tournament_models.py` (extend —
**`TestTournamentRoundRobinFormat`** `format` / `bracket_type` accept/persist
`"round_robin"`; **`TestLockAndBuildRoundRobin`** N=4 → 12 nodes all
`bracket_type="round_robin"` / `series_length=1` / `advances_to_id` +
`loser_advances_to_id` `None` / `is_bye=False`, every unordered pair twice, `position`
0-based within each matchday, `state="active"`, N=6 node count;
**`TestRoundRobinStandings`** one `StandingsRow` per enrolled team (zero-filled
pre-play), and after hand-stamping a node's `winner` + a played
`SeriesMatch`/`Match`/`GameRound` the standings reflect the win — assert on
`wins`/`league_points`/`rank` ORDER, never exact points;
**`TestCompleteRoundRobinIfFinished`** no-op when not all resolved, flips to
`"completed"` + `champion == round_robin_standings()[0].team_id` when all resolved,
idempotent, no-op for non-`round_robin` / non-`active`). `test_tournament_engine.py`
(extend — **`TestPlayNextNodeRoundRobinNoEarlyCrown`** the first `play_next_node`
resolves one node but does **NOT** crown / complete despite `advances_to is None`;
**`TestPlayNextNodeRoundRobinCompletes`** draining to `None` resolves every node and
exactly then stamps champion + `"completed"`, no node ever gets an `advance_winner`
mutation). `test_tournament_views.py` (extend — **`TestCreateFormRoundRobin`** the
format select offers `round_robin`, a POST persists `format == "round_robin"`, a
tampered/absent value falls back to `single_elimination`;
**`TestDetailRoundRobinCrosstable`** renders `tournament-rr-crosstable` (leg
`round_number==1` in `cell[team_a][team_b]`, leg `2` in `cell[team_b][team_a]`,
diagonal blank) + `tournament-rr-standings`, elim containers absent, shared lock /
play-next / play-all + champion banner render, the four series-length selects hidden).
`test_tournament_tasks.py` (extend — **`TestPlayTournamentTaskRoundRobin`** under
`CELERY_TASK_ALWAYS_EAGER`, `play_tournament_task` drains an RR Tournament to
completion + stamps champion + `"completed"`, `stage_progress` reports per-matchday
stage counts). Tests assert on the pure functions, persisted node/row shapes,
`node.winner` / `tournament.champion` / `tournament.state`, standings ORDER, and the
DOM ids — **never on exact simulated point totals** (non-deterministic).
`compute_standings` itself is already covered pure-unit by `test_standings.py`
(LG-06g) — no new pure standings tests for RR (the RR methods only *assemble* the
seam dicts; that assembly is DB-level).

**Locked names (quick index).** Enum: `Tournament.format == "round_robin"` (label
`"Round robin"`); `BracketNode.bracket_type == "round_robin"` (label `"Round
robin"`); `_BRACKET_RANK["round_robin"] = 3`. Model methods:
`Tournament.round_robin_standings(self) -> list[StandingsRow]`;
`Tournament.complete_round_robin_if_finished(self) -> None`. Build: third `format`
branch in `Tournament.lock_and_build()`; deferred import `from .schedule_generator
import generate_schedule`; RR node kwarg set (`bracket_type="round_robin"`,
`series_length=1`, `advances_to`/`loser_advances_to` left `None`, `is_bye=False`,
`bracket_round=matchday`, `position=index-in-matchday`). Engine: `play_next_node` RR
guard (`if tournament.format == "round_robin":` stamp winner →
`complete_round_robin_if_finished()` → `return node`); `find_next_node` UNCHANGED.
Reused pure seam: `compute_standings(completed_matches, enrolled_teams,
season_rounds)` — 9-key match dict / 6-key season_rounds dict / `(id, name)` enrolled
tuple / 17-field `StandingsRow`. Migration:
`matches/migrations/0037_tournament_round_robin.py`, dep
`0036_bracketnode_double_elimination`, two `AlterField`s, no `RunPython`. NEW DOM
ids: `tournament-rr-crosstable`, `tournament-rr-standings`. Reused verbatim:
`tournament-create-format`, `tournament-champion-banner`, `tournament-lock-*`,
`tournament-play-next-*`, `tournament-play-all-*`. NEW context keys: `rr_crosstable`,
`rr_standings` (empty for elim; existing `rounds` 3-key dict shape unchanged).
Crosstable rule: leg `round_number==1` → `cell[team_a][team_b]`; leg `2` →
`cell[team_b][team_a]`; diagonal blank.

## LG-02c round robin → double elimination tournaments

Adds a fourth `Tournament.format` enum value
**`"round_robin_double_elim"`** (label `"Round robin → Double elimination"`,
em-dash arrow `→` U+2192): a **two-stage** format that composes the two shipped
LG-02c formats. A round-robin **Seeding stage** (the SHIPPED double round-robin,
verbatim — one `bracket_type="round_robin"` node per `generate_schedule` fixture,
no edges, Bo1) plays to completion; its final **Standings rank** then seeds a
double-elimination **Finals stage** (the SHIPPED ADR-0021 WB+LB+Grand-final tree)
built **lazily** when the last Seeding node resolves. Builds on both predecessors'
pure + persist machinery verbatim — the new work is (a) **one** pure builder that
fuses a re-tagged Winners bracket with a pre-seeded Losers bracket, (b) a
**deferred finals build** triggered when the last RR node resolves, (c) a shared
persist helper **extracted** from `lock_and_build`, and (d) the create-form combo
select + detail cut-line markers. **Non-deterministic** (`simulate_match` draws
fresh per-round seeds) ⇒ **no SIM-07/SIM-08 interaction, NO Score Calibration
re-baseline**. ADR-0021 is **EXTENDED** for the deferred-build decision; **no new
ADR, no new CONTEXT.md term** beyond the already-written **Round robin → double
elimination**. Seam contract:
[`.claude/worktrees/lg-02c-rr-de-seam-contract.md`](../../.claude/worktrees/lg-02c-rr-de-seam-contract.md).

**Enum / fields (LOCKED).** `Tournament.FORMAT_CHOICES` gains
`("round_robin_double_elim", "Round robin → Double elimination")` as its **fourth**
entry (label uses the em-dash arrow `→` U+2192); the `format` field declaration is
otherwise unchanged (`CharField(max_length=32)`, `default="single_elimination"` —
`"round_robin_double_elim"` is 23 chars, fits 32). Two NEW
`PositiveSmallIntegerField(default=0)` fields, declared **immediately after the
four `*_series_length` fields** (before `created_at`/`champion`):
**`Tournament.wb_advancers`** (how many top-ranked teams enter the Winners bracket)
and **`Tournament.lb_advancers`** (how many next-ranked teams pre-seed the Losers
bracket). **Neither carries `choices`** — the create form's `rrde_combo` select is
the single source of valid shape combos; the model holds the resolved ints (mirrors
how `BracketNode.series_length` carries a resolved int with no choices). Both are
**create-time only, frozen at lock** — no view rewrites them post-setup; meaningful
only for `format == "round_robin_double_elim"`, left `0` for every other format. No
`bracket_type` change (the Finals stage reuses `winners`/`losers`/`grand_final`); no
`_BRACKET_RANK` change (`"round_robin": 3` already present). Migration
`matches/migrations/0038_tournament_rr_de.py` (dep
`0037_tournament_round_robin`; three ops — `AlterField(Tournament.format)` choices
widen to the 4-tuple → `AddField(wb_advancers)` → `AddField(lb_advancers)`; **no
`RunPython`, no backfill**, ADR-0004 disposable-sandbox precedent).

**Locked constraints (shape vs count).** The valid `(wb_advancers, lb_advancers)`
combos are exactly six: **`wb ∈ {4, 8, 16}`** (a power of two) and
**`lb ∈ {0, wb // 2}`** — `4/0, 4/2, 8/0, 8/4, 16/0, 16/8`. The **SHAPE**
(power-of-two `wb`, `lb ∈ {0, wb//2}`) is enforced at the **create form** (the
`rrde_combo` select enumerates only the six combos); the **COUNT fit** (`wb <= n`
and `wb + lb <= n`, where `n = len(participants)`) is validated at
**`lock_and_build`** and raises `django.core.exceptions.ValidationError`
(`"wb_advancers exceeds participant count."` / `"wb_advancers + lb_advancers
exceeds participant count."`), surfaced by the existing lock view via
`messages.error` (LG-02a precedent).

**Seeding-stage build at lock (`matches/models.py`).** The existing RR guard in
`lock_and_build` widens from `if self.format == "round_robin":` to **`if
self.format in ("round_robin", "round_robin_double_elim"):`** — the RR-node build
is **byte-identical** for both formats (one `BracketNode` per `generate_schedule`
fixture, `bracket_type="round_robin"`, `series_length=1`, no advance edges, no
`resolve_bye_chain`). Inside this branch, the RRDE format additionally runs the
lock-time count validation above (raising `ValidationError` on a bad fit) before
the shared `state="active"` + `save(update_fields=["state"])` tail. **The RRDE lock
builds ONLY the RR Seeding nodes — the DE Finals are NOT built at lock**; they are
deferred (below). The elim branch (single/double-elim) is unchanged.

**Deferred Finals build — `build_de_finals_if_rr_finished(self) -> None`
(`@transaction.atomic`).** Triggered when the **last** Seeding node resolves; no-op
unless ALL guards hold (in order): (1) `self.format == "round_robin_double_elim"`;
(2) `self.state == "active"`; (3) every `bracket_type="round_robin"` node has
`winner_id is not None` (`not self.nodes.filter(bracket_type="round_robin",
winner__isnull=True).exists()`); (4) **idempotency** — the finals are not already
built (`not self.nodes.exclude(bracket_type="round_robin").exists()`; a second call
no-ops). When all pass: read `rows = self.round_robin_standings()` (the SHIPPED
RR-rank-ordered standings, `rows[0]` is RR rank 1), then split by rank —
`upper = [ParticipantSpec(team_id=rows[i].team_id, seed=i + 1) for i in
range(self.wb_advancers)]` (top `wb` → WB seeds `1..wb`, seed = 1-based RR rank),
`lower = [ParticipantSpec(team_id=rows[self.wb_advancers + j].team_id,
seed=self.wb_advancers + j + 1) for j in range(self.lb_advancers)]` (next `lb` → LB
pre-seeds, seeds `wb+1..wb+lb`), and **the rest of `rows` are eliminated** (never
enter the Finals). Call `specs = build_rr_de_finals_bracket(upper, lower)`, resolve
each spec's `series_length` via `series_length_for_depth(spec.depth, final=...,
semifinal=..., quarterfinal=..., earlier=...)` from the four create-time slots
(distance-to-GF1 escalation, exactly as a plain DE), and persist + wire via
`self._persist_elim_specs(specs, ...)`. The Tournament **STAYS `state="active"`**
across the seeding→finals transition; the champion is crowned later by the DE Grand
final (via `play_next_node`'s unchanged crown block). **No byes** in the finals
(see invariant below), so the `resolve_bye_chain` pass inside the helper is a no-op.

**NEW pure builder — `build_rr_de_finals_bracket(upper_specs, lower_specs) ->
list[BracketNodeSpec]` (`matches/bracket.py`).** The fused WB+LB+GF finals builder.
`upper_specs` are the Winners-bracket starters (RR-rank-ordered, `seed = RR rank`,
`len == wb_advancers ∈ {4, 8, 16}`, a power of two); `lower_specs` are the
Losers-bracket pre-seeds (RR-rank-ordered, `seed = wb+1 .. wb+lb`, `len` either `0`
or `wb_advancers // 2`). Output is a `list[BracketNodeSpec]` consumed by the **SAME**
persist+wire path as `build_double_elim_bracket` output — every spec carries
`bracket_type` (`winners`/`losers`/`grand_final`), 2-tuple `advances_to` +
`advances_to_slot`, 3-tuple `loser_advances_to` `(bracket_type, bracket_round,
position)` + `loser_advances_to_slot`, `is_bye`, `winner_id`,
`seed_a`/`seed_b`/`team_a_id`/`team_b_id`, and **`depth`** (distance to GF1: GF1/GF2
= 0, WB-final & LB-final = 1, earlier rounds deeper). Two cases:

- **`lower_specs == []` — plain DE delegation.** With no LB pre-seeds the finals are
  a plain top-`wb` double-elimination: the builder **MUST produce EXACTLY
  `build_double_elim_bracket(upper_specs)`** — it **delegates directly** (the
  simplest, locked choice), so the result is provably identical (the pure test
  asserts spec-list equality). No re-tagging, no LB pre-fill.
- **`lower_specs` non-empty — fused WB+LB.** The WB is `build_bracket(upper_specs)`
  **re-tagged `bracket_type="winners"`** (reuse `build_double_elim_bracket`'s own WB
  re-tag pass — same seeding/pairing, no byes since `len(upper_specs)` is a power of
  two). The **only** difference from a plain DE is the LB round-1 wiring: **LB-R1**
  has `wb/2` nodes (== `lb_advancers`), each with **slot "a" PRE-FILLED with a
  `lower_spec`** (`team_a_id`/`seed_a` set; `team_b_id`/`seed_b` left `None` until a
  WB-R1 dropper arrives) in seed order (LB pre-seed `wb+1` → LB-R1 pos 0 slot "a", …);
  and **each WB-R1 node's `loser_advances_to` points at the matching LB-R1 node's
  slot "b"** (naive same-position drop, WB-R1 pos `i` → LB-R1 pos `i` slot "b" —
  `wb/2` WB-R1 nodes ↔ `wb/2` LB-R1 nodes, exactly paired). WB-R(r≥2) losers drop
  into the LB exactly as `build_double_elim_bracket` wires them (the existing naive
  same-position drop — **NO anti-rematch folding**, inherited limitation). GF1/GF2
  are emitted as in a plain DE (GF1 = WB champ slot "a" + LB champ slot "b", GF1's
  `loser_advances_to` → GF2 for the Bracket reset; GF2 final, both pointers `None`;
  GF1/GF2 `depth = 0`). **No-byes invariant:** `wb` is a power of two filled by
  exactly `wb` real teams, and `lb = wb/2` exactly fills LB-R1 slot "a" (or `lb = 0`
  ⇒ plain DE over a power-of-two field), so the output has **zero `is_bye` nodes**
  and the deferred build's `resolve_bye_chain` pass is a no-op. **Import-allowlist
  guarantee:** the builder adds **no new import** — it composes `build_bracket` /
  `build_double_elim_bracket` and constructs `BracketNodeSpec`s, all in-module;
  `TestNoDjangoImportsLeaked` stays green.

**Extracted shared persist helper — `_persist_elim_specs(self, specs, ...) -> None`
(`matches/models.py`).** The DE-node persist loop + the `advances_to` /
`loser_advances_to` wiring passes + the `resolve_bye_chain` cascade pass are
**extracted** out of `lock_and_build` into this private helper so BOTH the existing
single/double-elim lock path AND the deferred finals build reuse it **verbatim**.
The helper does: (1) the `BracketNode.objects.create(...)` persist loop (carrying
`bracket_type`, `team_a`/`team_b` via a `team_by_id` map built from
`self.participants`, `seed_a`/`seed_b`, `is_bye`, `advances_to_slot`,
`loser_advances_to_slot`, `winner`, and the caller-resolved `series_length`); (2)
the `advances_to` wiring pass (2-tuple coord); (3) the `loser_advances_to` wiring
pass (3-tuple coord); (4) the `resolve_bye_chain` cascade. **Boundary note:**
`series_length` stamping stays in the **CALLER** — the single/double-elim
`lock_and_build` resolves `series_length_for_round` / `series_length_for_depth` per
spec before persisting, and the deferred build resolves
`series_length_for_depth(spec.depth, ...)` the same way; the exact length-resolution
boundary (a parallel lookup vs four ints + an `is_de` flag) is **Code agent's
discretion**, but the persist + three wiring passes MUST move into the helper
unchanged and the existing single/double-elim `lock_and_build` behaviour MUST stay
**byte-identical** (same nodes, edges, `series_length`, byes — pinned by
`TestLockAndBuildSingleElimUnchanged` / `...DoubleElimUnchanged`). **Stage is
DERIVED, not stored:** "Finals built" iff
`self.nodes.exclude(bracket_type="round_robin").exists()` — **no `stage` column**.

**Engine — `play_next_node` guard keyed on `node.bracket_type`
(`matches/tournament_engine.py`).** The current RR guard (after the `node.winner`
stamp) **rekeys** from `if tournament.format == "round_robin":` to
**`if node.bracket_type == "round_robin":`** and dispatches on format:
`format == "round_robin"` → `tournament.complete_round_robin_if_finished()`;
`format == "round_robin_double_elim"` → `tournament.build_de_finals_if_rr_finished()`;
then **`return node`** (a resolved Seeding node NEVER falls through to the elim
advance/drop/GF-reset/crown block). DE-stage nodes (`bracket_type in
winners`/`losers`/`grand_final`) **fall through to the UNCHANGED elim block** — even
inside an RRDE tournament: winner advance, loser Drop, GF1 Bracket-reset, GF2 crown,
all **byte-unchanged**. When the last RR node resolves in an RRDE tournament,
`build_de_finals_if_rr_finished()` persists the Finals, the tournament stays
`active`, and the next `play_next_node` call finds the first playable DE node.
**Callers unchanged** (`tournament_play_next`, `play_tournament_task`,
`tournament_play_all`, `tournament_play_status` keep their URLs + 5-key status JSON);
`stage_progress` reports completion across the RR groups THEN the WB/LB/GF groups —
the finals groups appear only after the deferred build, so Play-All progress
naturally extends mid-run as the Finals materialize. `find_next_node` is unchanged.

**View / template surface.** Create form (`tournament_create` /
`tournament_create.html`): the `<select name="format">` (DOM id
**`tournament-create-format`**) gains a fourth option — value
**`round_robin_double_elim`**, label **`Round robin → Double elimination`**
(em-dash arrow U+2192); the view's forgiving-fallback parse widens to accept it
(absent/tampered → `single_elimination`). NEW single `<select name="rrde_combo">`
(DOM id **`tournament-create-rrde-combo`**) enumerating the six valid combos
(`4/0, 4/2, 8/0, 8/4, 16/0, 16/8` — exact value-string format at Code agent's
discretion), parsed into `wb_advancers` / `lb_advancers`; parse is forgiving (absent/
invalid on an RRDE create → first combo `(4, 0)`; ignored on a non-RRDE create,
both advancers persist `0`). The combo select is shown client-side **only** when the
format select reads `round_robin_double_elim` (hidden otherwise — reuse the existing
inline `onchange` toggle that hides the series-length selects for `round_robin`;
behaviour pinned, exact JS at Code agent's discretion). `Tournament.objects.create(...)`
passes `wb_advancers=` / `lb_advancers=` (`0`/`0` for non-RRDE). Detail page
(`_detail_context` / `tournament_detail.html`): two NEW context keys —
**`tournament_stage`** (`str`, DERIVED not stored: `"setup"` / `"seeding"` /
`"finals"` / `"completed"`) and **`cut_labels`** (`dict[int, str]`,
`team_id -> "wb" | "lb" | "out"`, built from `round_robin_standings()`: top
`wb_advancers` → `"wb"`, next `lb_advancers` → `"lb"`, rest → `"out"`; populated only
in the seeding stage of an RRDE tournament, `{}` otherwise). The existing keys
(`tournament`, `participants`, `rounds`, `next_node`, `is_locked`, `can_play`,
`import_form`, `import_row_errors`, `rr_crosstable`, `rr_standings`) are
**unchanged**. NEW DOM ids: **`tournament-stage-badge`** (renders `tournament_stage`,
e.g. "Seeding stage" / "Finals stage") and a per-standings-row cut class substring
**`tournament-standings-cut-wb`** / **`-lb`** / **`-out`** on each RR standings row in
the seeding stage (so tests can assert which teams are tagged Winners / Losers /
Eliminated). During seeding, reuse the RR crosstable + standings VERBATIM
(`tournament-rr-crosstable` / `tournament-rr-standings`), adding the cut class + the
stage badge; once Finals are built, ALSO render the DE three-section tree reusing the
EXISTING DE rendering (`tournament-bracket-winners` / `-losers` / `-grand-final` +
the per-section `tournament-bracket-{bracket_type}-round-{n}` columns +
`tournament-champion-banner`). `_build_rounds` / `_build_rr_crosstable` are
**unchanged** — `_build_rounds` already returns the 3-key
`{"winners", "losers", "grand_final"}` dict, populated once finals nodes exist; the
template branches on `tournament.format`.

**Determinism / scope.** **Non-deterministic** per-Match sims (`simulate_match`
draws fresh per-round seeds) ⇒ **no SIM-07/SIM-08 interaction, NO Score Calibration
re-baseline**. No `simulate_match` change. ADR-0021 **EXTENDED** for the
deferred-build decision (no new ADR — reversible: a fused builder + a deferred build
+ an extracted helper); **no new CONTEXT.md term** (the **Round robin → double
elimination** term was finalised at grilling time). **Scope-out (LOCKED — DEFERRED):**
**anti-rematch LB folding** (the LB consumes WB losers via the naive same-position
drop, inherited from `build_double_elim_bracket`); **Swiss** seeding stage;
**in-League / in-Season embedding** (the Tournament stays standalone, `season`-less);
**fully-general `wb`/`lb` counts** (only the six locked combos ship); **home/away
side alternation** (sides fixed, LG-02b locked); **deterministic / master-seed-
replayable Series**.

**Tests:** `matches/tests/test_bracket.py` (extend —
`TestBuildRrDeFinalsBracket`: `lower_specs == []` ⇒ result EQUALS
`build_double_elim_bracket(upper_specs)` for `wb = 4/8/16`; non-empty `lb = wb/2`
for `wb = 4/8` — WB re-tagged `winners`, LB-R1 slot "a" pre-filled in seed order,
each WB-R1 `loser_advances_to` → matching LB-R1 slot "b", GF1/GF2 wiring + `depth`,
**no `is_bye` nodes**, LB-round count `== 2W - 1`; `TestNoDjangoImportsLeaked` green).
`matches/tests/test_tournament_models.py` (extend — `wb_advancers`/`lb_advancers`
exist/default `0`/no choices; `format` accepts `round_robin_double_elim`;
`lock_and_build` builds ONLY RR nodes for RRDE and raises `ValidationError` on the two
count-fit failures; `build_de_finals_if_rr_finished` no-op unless all guards hold +
idempotent + seeds Finals from RR standings rank; `_persist_elim_specs` extraction
keeps single/double-elim `lock_and_build` byte-identical via
`TestLockAndBuildSingleElimUnchanged` / `...DoubleElimUnchanged`).
`matches/tests/test_tournament_engine.py` (extend — resolving the LAST RR node
triggers the deferred Finals build, tournament stays `active`; draining the Finals
crowns the champion via the GF crown block; a Seeding node never gets an
`advance_winner` mutation; a DE-stage node advances/drops/resets normally).
`matches/tests/test_tournament_views.py` (extend — create form offers the
`round_robin_double_elim` option + the `tournament-create-rrde-combo` select with the
six combos; POST persists `format`/`wb_advancers`/`lb_advancers` with forgiving
fallback; detail renders `tournament-stage-badge` seeding-vs-finals strings + the
`tournament-standings-cut-{wb|lb|out}` substrings tagging the right teams; reused RR +
DE DOM ids present). `matches/tests/test_tournament_tasks.py` (extend — under
`CELERY_TASK_ALWAYS_EAGER`, `play_tournament_task` drains an RRDE tournament through
BOTH stages — seeding RR nodes THEN the auto-built DE finals — to a champion +
`state="completed"`; `stage_progress` reports per-group completion across the RR
groups then the WB/LB/GF groups). Tests assert on pure functions, persisted
node/row/edge shapes, `bracket_type` / `series_length`, `node.winner` / `champion` /
`state`, standings ORDER, and DOM ids — **never on exact simulated point totals**.

## LG-02c swiss tournaments

Adds a **fifth** `Tournament.format` enum value **`"swiss"`** (label `"Swiss"`):
a **flat, edge-less** Swiss-system format for the standalone sandbox Tournament,
built on the four shipped LG-02c formats. Every Swiss node is a Bo1 pairing with
`advances_to=None`, `loser_advances_to=None`, `is_bye=False`, `series_length=1` —
there is **no advancement tree** and **no final node**; the champion is the
**Standings leader (Buchholz re-ranked)** after the last Swiss round resolves. The
new work is (a) **two** pure functions — a single round builder that serves both
the round-1 seed fold and the later greedy ranked sweep, plus a Buchholz re-rank
layer — (b) a **per-round DEFERRED build** triggered each time a round's last node
resolves, (c) a `compute_standings`-input assembly helper **extracted** from
`round_robin_standings`, and (d) the create-form round-count input + the per-round
pairings/standings detail page. **Non-deterministic** (`simulate_match` draws fresh
per-round seeds) ⇒ **no SIM-07/SIM-08 interaction, NO Score Calibration
re-baseline**. ADR-0021 is **EXTENDED** for the per-round deferred build + Buchholz
re-rank decision; **no new ADR**, and **no new CONTEXT.md term** beyond the
already-written **Swiss** + **Buchholz** terms (CONTEXT.md already carries them — do
not edit). Seam contract:
[`.claude/worktrees/lg-02c-swiss-seam-contract.md`](../../.claude/worktrees/lg-02c-swiss-seam-contract.md).

**Enum / bracket_type / field (LOCKED).** `Tournament.FORMAT_CHOICES` gains
`("swiss", "Swiss")` as its **fifth** entry (`format` field declaration otherwise
unchanged — `CharField(max_length=32)`, `"swiss"` is 5 chars). `BracketNode`'s
`bracket_type` choices gain `("swiss", "Swiss")` as their **fifth** entry
(declaration unchanged: `CharField(max_length=12)`, `default="winners"` —
`"swiss"` fits 12), and `matches/bracket.py`'s `_BRACKET_RANK` gains
**`"swiss": 4`** (so `find_next_node`'s `_BRACKET_RANK.get(bracket_type, 0)` sort
key slots Swiss in with **no `find_next_node` edit**; its playable predicate already
treats an unplayed Bo1 Swiss node as playable and a resolved one as skipped). One
NEW field: **`Tournament.swiss_rounds`**
(`PositiveSmallIntegerField(default=0)`, declared **immediately after
`lb_advancers`**, **no `choices`**) — the total number of Swiss rounds; `0` = auto.
It is **create-time, resolved-and-frozen at lock**: at lock
`total = swiss_rounds or math.ceil(math.log2(N))`, **clamped to `[1, N-1]`**, then
**written back** into `swiss_rounds` so the played-out round count is fixed.
Meaningful only for `format == "swiss"`; left `0` for every other format. Migration
`matches/migrations/0039_tournament_swiss.py` (dep `0038_tournament_rr_de`; three
ops in pinned order — `AlterField(Tournament.format)` choices-widen to the 5-tuple →
`AlterField(BracketNode.bracket_type)` choices-widen to add `("swiss", "Swiss")` →
`AddField(Tournament.swiss_rounds)`; **no `RunPython`, no backfill**, ADR-0004
disposable-sandbox precedent).

**EVEN-N only — no byes ever (LOCKED).** Swiss admits any **even** participant
count; an **odd** count raises `django.core.exceptions.ValidationError` at
`lock_and_build` with the EXACT message
**`"Swiss requires an even number of participants."`** (surfaced by the lock view
via `messages.error`, LG-02a precedent). There are **no bye nodes** at any point —
every Swiss node is a real pairing, so `resolve_bye_chain` is never invoked (flat,
like the RR branch).

**Round-1 build at lock = seed "fold" (`matches/models.py`).** Inside the existing
`@transaction.atomic` `lock_and_build`, AFTER the `setup`-guard and the `>= 4`
participant guard, a **dedicated `if self.format == "swiss":` branch** (its own
branch — NOT folded into the RR branch, because it needs the even-N guard + the
round-count freeze + the fold pairing + the `bracket_type="swiss"` tag) does:
raise on odd N; resolve+clamp+freeze `swiss_rounds`; compute the **seed fold** —
sort participants by Bracket `seed` ascending, split in half, and interleave so
consecutive pairs are `(seed[i], seed[i + N/2])` for `i` in `0..N/2-1`; then call
`build_swiss_round(fold_order, seed_by_team, set(), bracket_round=1)` and persist
each spec as a `BracketNode` (`bracket_type="swiss"`, `series_length=1`,
`is_bye=False`, all advance/loser slots `None`, `winner=None`). Tail:
`state="active"` then `save(update_fields=["state", "swiss_rounds"])`.
`build_swiss_round` is **deferred-imported inside the branch** (mirrors the RR
branch's deferred `from .schedule_generator import generate_schedule`); `import math`
is used (already at module scope, or added). The R1 build emits **only** the
round-1 nodes (`N/2` of them) — later rounds are deferred.

**Later rounds DEFERRED — `advance_swiss_if_round_finished(self) -> None`
(`@transaction.atomic`).** Triggered when the **current (highest) Swiss round's last
node resolves**; no-op unless `format == "swiss"` and `state == "active"`. It finds
the highest `bracket_round` among the Swiss nodes; if any node in that round still
has `winner_id is None`, it no-ops (round not finished). When the round IS resolved:
if `current_round < self.swiss_rounds` it builds the **next** round's nodes via a
**greedy ranked sweep** — `rows = self.swiss_standings()`, `ranked_team_ids =
[row.team_id for row in rows]`, `played_pairs = self._swiss_played_pairs()`, then
`build_swiss_round(ranked_team_ids, seed_by_team, played_pairs,
bracket_round=current_round + 1)` — persists the new nodes, and the Tournament
**STAYS `active`**. If `current_round == self.swiss_rounds` it crowns:
`champion_id = swiss_standings()[0].team_id`, `state="completed"`,
`save(update_fields=["champion", "state"])`. The pairing walks
`swiss_standings()` top-down, pairing each unpaired team with the **next unpaired
team it has NOT already played**; if the trailing teams can only be paired by
replaying, it **ALLOWS the rematch** (no backtracking — the last fallback pair is the
next unpaired team regardless of `played_pairs`).

**No draws ⇒ `league_points = 3 * wins`.** Each Bo1 Match resolves to a single
winner via the inherited `break_tie` (unchanged in `play_next_node`), so there are
no draws and a team's `league_points` are purely `3 * wins`.

**Buchholz tiebreak — ORDERING-ONLY, over the frozen `compute_standings`.** A team's
Buchholz = the **sum of its opponents' final `league_points`** across all Swiss
pairings it played (a rematch counts twice — Buchholz sums per played pairing). The
**Swiss ranking ladder** is `league_points desc → Buchholz desc → round_wins desc →
total_score desc → team_name asc`. Buchholz is **NOT a displayed column** and
`matches/standings.py::compute_standings` is a **FROZEN shared module — NOT
modified**; instead a separate **pure re-rank layer** takes the `compute_standings`
rows + the played-pairs opponent graph and re-sorts them. Because the input rows are
already in `compute_standings`' final order (which ends `team_name asc`), the re-rank
uses a **STABLE sort** keyed on `(-league_points, -buchholz, -round_wins,
-total_score)` and the pre-existing `team_name asc` survives as the stable tiebreak —
so **no team-name lookup crosses the pure seam**.

**NEW pure functions — `matches/bracket.py` (frozen import allowlist UNCHANGED;
`TestNoDjangoImportsLeaked` stays green).** Both add **no new import** (only
`dataclasses` / `typing` / `math` / `collections`, all already present):

- **`build_swiss_round(ranked_team_ids, seed_by_team, played_pairs, bracket_round)
  -> list[BracketNodeSpec]`** — **ONE** function for BOTH the R1 fold and the later
  greedy sweep; the variant is selected by the **caller** via what it passes. **R1
  (fold):** caller passes `ranked_team_ids` = the pre-computed fold order +
  `played_pairs = set()` — with an empty `played_pairs` the "not yet played" check
  never fires, so greedily pairing consecutive teams reproduces exactly the fold
  pairing. **Later rounds (greedy sweep):** caller passes `ranked_team_ids` = the
  current `swiss_standings()` rank order + the non-empty `played_pairs`; the function
  walks top-down, pairing each unpaired team with the next unpaired team it has NOT
  already played, allowing a rematch only as the trailing fallback (no backtracking).
  `seed_by_team` (`dict[int, int]`, **every** team id → its Bracket seed) is used
  ONLY to stamp `seed_a`/`seed_b` on each node (so the engine's seed-keyed tie-break
  works even though pairing order is rank/fold-based, not seed-based). `played_pairs`
  is a `set[frozenset[int]]` (side-agnostic). Returns one `BracketNodeSpec` per
  pairing, `position` 0-based ascending in pairing order, each
  `bracket_type="swiss"`, `is_bye=False`, all advance/loser slots `None`,
  `winner_id=None`, `depth=None` (the spec carries **no `series_length`** — the node
  row gets `series_length=1` at create). **Pure / total** (never raises): an odd
  `len(ranked_team_ids)` is the caller's responsibility (the even-N guard fires at
  lock); defensively the trailing unpaired team is dropped.
- **`swiss_buchholz_rerank(rows, opponents_by_team) -> list[StandingsRow]`** — the
  pure Buchholz re-rank layer. `rows` is the `StandingsRow` list from
  `compute_standings` (the 17-field frozen dataclass); `opponents_by_team` is
  `team_id -> list[opponent_team_id]` (one entry per played pairing — a rematch
  appears twice). Builds `points_by_team = {row.team_id: row.league_points}`, then
  `buchholz(t) = sum(points_by_team.get(opp, 0) for opp in
  opponents_by_team.get(t, []))`, re-sorts via the **STABLE sort** on
  `(-league_points, -buchholz, -round_wins, -total_score)`, and returns a NEW list of
  `StandingsRow` with `rank` renumbered **1-based dense**
  (`dataclasses.replace(row, rank=i + 1)`), every other field copied verbatim — **no
  Buchholz value leaks into the row**. Empty `rows` ⇒ `[]`. (`StandingsRow` is never
  imported at module scope in `bracket.py`; the return annotation is a string
  forward-ref, no import added.)

The `BracketNodeSpec` / `ParticipantSpec` dataclasses and every existing pure
function (`build_bracket` / `build_double_elim_bracket` /
`build_rr_de_finals_bracket` / `find_next_node` / `advance_winner` /
`advance_loser` / `resolve_bye_chain` / `series_length_for_*` /
`default_seed_order` / `stage_progress` / `count_series_wins` / `break_tie`) are
**UNCHANGED**.

**Model helpers — `matches/models.py`.** The `compute_standings`-input assembly is
**extracted** out of `round_robin_standings()` into a private
**`_standings_over_nodes(self, node_qs) -> list[StandingsRow]`** (builds
`enrolled_teams` + `completed_matches` + `season_rounds` from a queryset of resolved
Bo1 nodes and returns `compute_standings(...)`); `round_robin_standings()` then
becomes a one-liner over `self.nodes.filter(bracket_type="round_robin")` and its
**external behaviour stays byte-identical** (pinned by a regression test). NEW
**`swiss_standings(self) -> list[StandingsRow]`** = `_standings_over_nodes` over the
Swiss nodes, fed through `swiss_buchholz_rerank` with the opponent graph. NEW private
**`_swiss_opponent_graph(self) -> dict[int, list[int]]`** (builds the played-pairs
opponent graph from every Swiss node with both slots filled — a rematch contributes
to both lists each time) and **`_swiss_played_pairs(self) -> set[frozenset[int]]`**
(side-agnostic frozenset pairing keys from the Swiss nodes). `_node_to_dict`,
`find_next_playable_node`, `count_series_wins`,
`complete_round_robin_if_finished`, `build_de_finals_if_rr_finished`,
`_persist_elim_specs` are UNCHANGED — `_node_to_dict` already yields
`bracket_type="swiss"` / `advances_to=None` / `loser_advances_to=None` /
`series_length=1` with no edit.

**Engine — `play_next_node` Swiss guard (`matches/tournament_engine.py`).** The body
is verbatim through the clinch check + `node.winner` stamp. A Swiss branch is added
**alongside** the existing RR/RR→DE `node.bracket_type == "round_robin"` guard and
**before** the elim `_node_to_dict` flatten / `advance_winner` / `advance_loser` /
crown block: `if node.bracket_type == "swiss":
tournament.advance_swiss_if_round_finished(); return node`. The `return node` means a
resolved Swiss node NEVER reaches the elim advance/crown block — without it the
"crown when `advances_to is None`" elim rule would wrongly crown on the FIRST resolved
Swiss node. Callers (`tournament_play_next`, `play_tournament_task`,
`tournament_play_all`, `tournament_play_status`) are UNCHANGED in
signature/route; `play_tournament_task`'s `while play_next_node(...) is not None`
loop drains every Swiss node one Match at a time and naturally extends mid-run as each
deferred round's pairings materialize; `stage_progress` (unchanged) reports
per-`(bracket_type, bracket_round)` group completion — for Swiss, per-round progress.

**View / template surface.** Create form (`tournament_create` /
`tournament_create.html`): the `<select name="format">` (DOM id
**`tournament-create-format`**) gains a fifth `<option value="swiss">Swiss</option>`;
the view's format whitelist appends `"swiss"` (absent/tampered → `single_elimination`
fallback). NEW numeric input (DOM id **`tournament-create-swiss-rounds`**, name
`swiss_rounds`, `min="0"`, `value="0"`) wrapped in a `.tournament-create-swiss-rounds-row`
(mirrors the `*-series-length-row` / `*-rrde-combo-row` pattern), shown client-side
**only** when the format select reads `swiss`. A forgiving **`_parse_swiss_rounds(raw)
-> int`** coerces the POST field to a non-negative int (absent/blank/invalid/negative
⇒ `0` = auto; clamping happens at lock), passed as `swiss_rounds=` into
`Tournament.objects.create(...)` (`0` and harmless for non-Swiss). The
`tournamentCreateToggle(value)` JS shows `.tournament-create-swiss-rounds-row` only
for `swiss`, and **hides** the series-length selects AND the rrde-combo control for
`swiss` (Swiss is always Bo1, no DE finals — the existing `value === "round_robin"`
hide rule widens to `value === "round_robin" || value === "swiss"`). Detail page
(`_detail_context` / `tournament_detail.html`): two NEW context keys — **`swiss_rounds_view`**
(`list[{round_number, pairings}]`, the Swiss nodes grouped by `bracket_round`, each
pairing a node-view dict in the SAME shape `_build_rounds` builds — reusing the
node-card include — assembled by a small helper, suggested
`_build_swiss_rounds(tournament)`, NOT by overloading the 3-key elim `_build_rounds`)
and **`swiss_standings`** (`list[(StandingsRow, Team)]`, the Buchholz-ranked rows
paired with their Team via the LG-01 `rows_with_teams` precedent). Both default to
`[]` for non-Swiss (mirrors `rr_crosstable=[]` / `rr_standings=[]`); every existing
context key (`tournament`, `participants`, `rounds`, `next_node`, `is_locked`,
`can_play`, `import_form`, `import_row_errors`, `rr_crosstable`, `rr_standings`,
`tournament_stage`, `cut_labels`) is UNCHANGED. `_tournament_stage` gains an explicit
`if tournament.format == "swiss": return "swiss"` for the active case (so the
`tournament-stage-badge` reads "Swiss stage"; the badge guard widens to fire for
swiss). NEW DOM ids (LOCKED): **`tournament-swiss-rounds`** (outer container of the
per-round pairing sections), **`tournament-swiss-round-{n}`** (one section per Swiss
round, `n` = 1-based), **`tournament-swiss-standings`** (the Buchholz-ranked standings
table), and per-pairing **`tournament-node-swiss-{bracket_round}-{position}`** cards
(reusing the existing `tournament-node-{bracket_type}-{bracket_round}-{position}`
convention; Swiss nodes are Bo1 ⇒ NO per-node Series-score / Bo-N label, same as RR).
The Swiss render block is gated `{% elif tournament.format == "swiss" %}` in the
existing format ladder. **REUSED VERBATIM** (no new ids): `tournament-champion-banner`
(stamped identically on the Swiss completion path), the lock control
(`tournament-lock-form`/`-submit`), play-next (`tournament-play-next-form`/`-submit`),
play-all (`tournament-play-all-form`/`-submit`/`-progress` + poll JS), the import +
seeding forms, and `tournaments-nav-link`. The elim WB/LB/GF containers
(`tournament-bracket*`) and the RR ids
(`tournament-rr-crosstable`/`tournament-rr-standings`) are ABSENT for Swiss; the Swiss
ids are ABSENT for every other format (the template branches on `tournament.format`).
`_build_rounds`'s 3-key `{"winners", "losers", "grand_final"}` return is UNCHANGED —
all three lists are empty for a Swiss Tournament (Swiss nodes are never bucketed into
its three sections).

**Determinism / scope.** **Non-deterministic** per-Match sims (`simulate_match` draws
fresh per-round seeds) ⇒ **no SIM-07/SIM-08 interaction, NO Score Calibration
re-baseline**. No `simulate_match` change. ADR-0021 **EXTENDED** for the per-round
deferred build + the Swiss-only Buchholz re-rank layer (no new ADR); **no new
CONTEXT.md term** (the **Swiss** + **Buchholz** terms were finalised at grilling and
CONTEXT.md is left untouched). With Swiss shipped, all **four** LG-02c bracket
formats are complete; the LG-02x player-pool formats remain the next LG-02 Part-1
work.

**Tests:** `matches/tests/test_bracket.py` (extend — `TestBuildSwissRound`: R1 fold
for N=4/8/16 emits the exact fold pairing from the interleaved order + empty
`played_pairs`, `bracket_type="swiss"`, advance/loser slots `None`, `is_bye=False`,
no `series_length` on the spec, 0-based `position`, seeds from `seed_by_team`;
later-round greedy sweep pairs each unpaired team with the next not-yet-played team;
allow-rematch fallback forces a trailing rematch with no crash/no dropped team for
even N. `TestSwissBuchholzRerank`: ladder correctness — Buchholz breaks an equal-points
tie, then `round_wins`/`total_score`, `team_name asc` surviving via the stable sort;
ORDERING-ONLY — all 17 fields preserved except `rank` renumbered 1-based dense, no
Buchholz value in the row; empty input ⇒ `[]`. `TestBracketRankSwiss`:
`_BRACKET_RANK["swiss"] == 4`. `TestNoDjangoImportsLeaked` STILL green).
`matches/tests/test_tournament_models.py` (extend — `TestSwissRoundsField`
(exists/default `0`/no choices); `TestSwissLockAndBuild` (even-N happy path builds
ONLY R1 nodes, count `N/2`, all `swiss`/Bo1/no-edges/no-bye, `state="active"`, fold
pairing; round-count resolve/clamp/freeze written back; odd-N ⇒ `ValidationError`
with the EXACT message); `TestStandingsOverNodesExtraction` (`round_robin_standings()`
byte-identical post-refactor); `TestSwissStandingsBuchholz` (hand-stamped Swiss nodes
across ≥2 rounds, `swiss_standings()` ORDER reflects the Buchholz ladder — NOT exact
points); `TestAdvanceSwissIfRoundFinished` (unfinished round ⇒ no-op; resolved +
`current < swiss_rounds` ⇒ next round built greedily, stays active; resolved +
`current == swiss_rounds` ⇒ champion = `swiss_standings()[0]`, `state="completed"`;
played-pairs rematch only as trailing fallback)). `matches/tests/test_tournament_engine.py`
(extend — `TestPlayNextNodeSwiss`: a resolved Swiss node never gets an
`advance_winner` mutation; resolving the round's LAST node triggers the next-round
build, stays active; the final round's completion crowns `swiss_standings()[0]` and
flips `state="completed"`). `matches/tests/test_tournament_views.py` (extend —
`TestCreateFormSwiss`: the format select offers `swiss`, a `swiss_rounds` input with
DOM id `tournament-create-swiss-rounds` exists, a POST persists
`format == "swiss"` + the coerced `swiss_rounds`, forgiving parse, format fallback;
`TestDetailSwiss`: detail renders `tournament-swiss-rounds` with per-round
`tournament-swiss-round-{n}` sections + `tournament-node-swiss-{br}-{pos}` cards +
`tournament-swiss-standings`, the series-length selects + rrde-combo hidden for swiss,
the reused champion/lock/play-next/play-all ids present, elim + RR ids absent).
`matches/tests/test_tournament_tasks.py` (extend — `TestPlayTournamentTaskSwiss`:
under `CELERY_TASK_ALWAYS_EAGER`, `play_tournament_task` drains a full Swiss
Tournament (all rounds) to a champion + `state="completed"`; `stage_progress` reports
per-round stage counts). Tests assert on pure functions, persisted node/row shapes,
`bracket_type`/`series_length`, `node.winner`/`champion`/`state`, standings ORDER,
and DOM ids — **never** on exact simulated point totals.

**Locked names:** see the seam contract
[`.claude/worktrees/lg-02c-swiss-seam-contract.md`](../../.claude/worktrees/lg-02c-swiss-seam-contract.md)
for the authoritative list of every name / signature / dict-key / DOM-id / literal
(format `"swiss"` + label, `_BRACKET_RANK["swiss"] = 4`, `swiss_rounds` field,
`build_swiss_round` / `swiss_buchholz_rerank`, `_standings_over_nodes` /
`swiss_standings` / `_swiss_opponent_graph` / `_swiss_played_pairs` /
`advance_swiss_if_round_finished`, the new DOM ids + context keys, the even-N error
string, and migration `0039_tournament_swiss`).

## LG-02x-1 random draw player-pool tournament

Adds a NEW **orthogonal** `Tournament.team_assembly` mode **`"random_draw"`** (vs the
default `"preset"`): instead of enrolling existing `Team`s, a **player pool** registers,
the system runs a **deterministic tier-balanced draw** into marked `is_draw_team`
Teams, and each game **Round** assigns roles dynamically before it sims. The draw
Tournament then runs the **shipped LG-02c RR→DE bracket unchanged** —
`team_assembly` is a SEPARATE field, **NOT a new `format` value**: a Random Draw
Tournament keeps `format == "round_robin_double_elim"`, so every RR→DE path
(`lock_and_build`, `_persist_elim_specs`, `round_robin_standings`,
`build_de_finals_if_rr_finished`, `play_next_node`, `stage_progress`, the detail
crosstable / cut-labels / DE-finals surfaces) is **byte-unchanged**. Pool intake,
the draw, the relaxed roster rule, and per-Round dynamic roles **all key off
`team_assembly == "random_draw"`**. **Non-deterministic** (the role draw consumes a
fresh `random.Random()`, the per-Match sims draw fresh per-round seeds) ⇒ **no
SIM-07 / SIM-08 interaction, NO Score Calibration re-baseline** (no simulation
*mechanics* change — the hook only swaps which Player occupies each role slot before a
normal round). **NEW [ADR-0022](../../docs/adr/0022-random-draw-player-pool-tournament.md)**
records the tier-balanced draw + per-Round dynamic roles + relaxed draw-team roster
ownership; **no new CONTEXT.md term** beyond the already-finalised **Player pool /
Drawn-team membership / Random Draw / Tier / Role assignment mode** terms (CONTEXT.md
carries them — do not edit). Duos / Trios + a `TournamentSubGroup` model are
**DEFERRED to LG-02x-2**. Seam contract:
[`.claude/worktrees/lg-02x-1-seam-contract.md`](../../.claude/worktrees/lg-02x-1-seam-contract.md).

**Two NEW `Tournament` fields (LOCKED) — `matches/models.py`.**
**`team_assembly`** (`CharField(max_length=16, choices=TEAM_ASSEMBLY_CHOICES,
default="preset")`) where `TEAM_ASSEMBLY_CHOICES = (("preset", "Preset teams"),
("random_draw", "Random draw player pool"))`. **`role_assignment_mode`**
(`CharField(max_length=16, choices=ROLE_ASSIGNMENT_CHOICES, default="random")`) where
`ROLE_ASSIGNMENT_CHOICES = (("random", "Random per team per Round"), ("per_tier",
"Per-tier bijection (both teams)"))`. Both are **create-time only**, declared
**immediately after** the `wb_advancers` / `lb_advancers` / `swiss_rounds` block and
**before** `created_at` / `champion`. `team_assembly` default `"preset"` ⇒ every
existing Tournament is `preset`, byte-unchanged; meaningful for any `format` but the
pool/draw/per-Round-roles machinery fires **only** when `== "random_draw"`.
`role_assignment_mode` is **meaningful only when `team_assembly == "random_draw"`**
(ignored for `preset`).

**NEW model `TournamentPlayerEntry` (LOCKED) — `matches/models.py`, after
`BracketNode` / `SeriesMatch`.** The durable **pool registration AND draw result** —
the source of truth for `(player, tier, drawn_team)`. A drawn Team's `slot_*` FKs hold
only the **transient** per-Round role assignment; the (player, tier, team) truth lives
here. Fields: `tournament` (FK `matches.Tournament`, **CASCADE**,
`related_name="player_entries"`), `player` (FK `teams.Player`, **CASCADE**,
`related_name="tournament_entries"`), `tier`
(`PositiveSmallIntegerField(null=True, blank=True)` — `null` after pool intake /
before the draw, `1..6` after, **tier 1 = strongest band**), `drawn_team` (FK
`teams.Team`, **SET_NULL**, nullable, `related_name="drawn_player_entries"` — a drawn
Team deleted out of band leaves the entry's tier intact). `Meta.ordering =
["tournament_id", "tier", "player_id"]` (deterministic tier-ascending, player-id
tiebreak — matches the draw's tiebreak) and a **`UniqueConstraint(fields=["tournament",
"player"], name="uniq_tournament_player_entry")`** — the structural guarantee a Player
cannot sit in two drawn Teams of the **same** Tournament; across **different**
Tournaments a Player may belong to many drawn Teams (the cross-tournament sharing rule).

**NEW `Team.is_draw_team` field + relaxed `roster_errors` — `teams/models.py`.**
`is_draw_team = models.BooleanField(default=False)` marks a drawn Team (**NO FK to
Tournament / Match** — the durable link lives on `TournamentPlayerEntry`, avoiding a
`teams → matches` dependency inversion). The "all players belong to this team"
`roster_errors` check is **relaxed for draw teams only** — the existing
`for player, role, slot_name in filled:` belongs-to-team loop (the
`player.team_id != self.pk` check) is wrapped in `if not self.is_draw_team:`. **Kept
unchanged** for draw teams: the all-6-slots-filled check, the duplicate-player check,
and the role-distribution (Scout-only-twice) check — only the ownership check is
relaxed. See [`teams/CLAUDE.md`](../teams/CLAUDE.md) `### Team.is_draw_team`.

**NEW pure module `matches/draw.py`.** Tier-balanced draw math + the two
role-assignment-mode bijection builders — **pure Python, no Django / ORM / I/O /
logging** (frozen import allowlist: `dataclasses`, `typing`, `random`, `collections`;
NO `django.*`, NO `datetime`, NO file I/O), defended by `TestNoDjangoImportsLeaked`
mirroring `matches/bracket.py` / `standings.py` / `schedule_generator.py`. `random`
is allowlisted because the role-assignment builders consume an **injected**
`random.Random` (the per-Round role draw); the **draw computation itself consumes NO
RNG**. Public surface:

- **`ROLE_SLOTS: tuple[str, ...] = ("commander", "heavy", "scout_1", "scout_2",
  "medic", "ammo")`** — the 6 `Team.slot_*` suffixes, fixed order.
- **`@dataclass(frozen=True) DrawnTeamPlan`** — `team_index: int` (0-based draw order),
  `player_ids: tuple[int, ...]` (the 6 player ids, tier 1..6 order),
  `tiers: tuple[int, ...]` (parallel to `player_ids`, the tier 1..6 of each).
- **`compute_draw(pool: list[tuple[int, float]]) -> list[DrawnTeamPlan]`** — **STRAIGHT
  TIERS + GREEDY BALANCE, deterministic, consumes NO RNG.** `pool` is `(player_id,
  overall_rating)`. Precondition (caller-validated): `len(pool) % 6 == 0` and
  `len(pool) >= 24` (≥ 4 teams) — raises `ValueError` otherwise. Algorithm: (1) sort
  by `overall_rating` **DESC**, then `player_id` **ASC** (tiebreak); (2) `T =
  len(pool) // 6` teams, form **6 contiguous tiers** of `T` players (tier 1 =
  strongest band = first `T`, …, tier 6 = weakest); (3) for each tier 1..6 in order,
  assign the strongest-remaining tier player to the **currently-weakest team** (lowest
  running total rating across already-processed tiers; `team_index` ASC tiebreak),
  one player per team per tier; (4) return one `DrawnTeamPlan` per team
  (`team_index` 0..T-1, `player_ids`/`tiers` ordered tier 1..6). **Idempotent** — same
  pool → identical output (a re-roll is a no-op; **admin hand-edits are the variation
  mechanism**).
- **`build_random_role_assignment(tier_player_ids: list[int], rng: random.Random) ->
  dict[str, int]`** — `random` mode, per TEAM independently: shuffle the team's 6
  tier-ordered ids into the 6 `ROLE_SLOTS`, returns `{slot_suffix: player_id}` over all
  6 slots (one rng shuffle).
- **`build_per_tier_role_assignment(rng: random.Random) -> dict[int, str]`** —
  `per_tier` mode: draw ONE `{tier (1..6): slot_suffix}` bijection for the Round
  (one rng shuffle), applied to **BOTH** teams (equal-tier players play the same role);
  the caller applies it to each team's tier→player map.

**Simulator seam — `BatchSimulator.simulate_match` (`matches/simulation/entrypoints.py`).**
The signature gains an **additive, keyword-only** `before_round_hook=None`
(`Optional[Callable[[int, Team, Team], None]]`): `simulate_match(self, team_red,
team_blue, match_type="friendly", *, arena_map=None, before_round_hook=None) ->
Match`. Default `None` ⇒ **byte-unchanged** for every existing caller (preset
tournaments, sandbox, season play). Callable signature **`before_round_hook(round_number:
int, team_red, team_blue) -> None`** where `round_number` is `1` or `2` and the Teams
are **as passed into that round's internal `_simulate_and_flush_round` call** (round 2
receives the swapped order). The hook mutates the drawn Teams' `slot_*` FKs **in
memory** before the round sims. **Two insertion points:** immediately after
`match = Match.objects.create(...)` and **before** the round-1
`_simulate_and_flush_round(team_red, team_blue, ...)` call →
`if before_round_hook is not None: before_round_hook(1, team_red, team_blue)`; after
the round-1 column-copy block and **before** the round-2
`_simulate_and_flush_round(team_blue, team_red, ...)` call →
`if before_round_hook is not None: before_round_hook(2, team_blue, team_red)`
(**same `(team_blue, team_red)` order** `simulate_match` uses for round 2). The hook
fires **once per round** so the 2 Rounds of one Match get **independent** role
assignments (re-draw every Round); `_simulate_and_flush_round` reads `team.active_roster`
off the now-mutated `slot_*` FKs, so the in-memory rewrite takes effect for that
round's sim. **No change to `_simulate_and_flush_round` itself.**

**Engine seam — `play_next_node` draw branch + `_build_role_hook`
(`matches/tournament_engine.py`).** `play_next_node`'s single
`BatchSimulator().simulate_match(node.team_a, node.team_b, match_type="tournament")`
call becomes a `team_assembly`-keyed branch: when `tournament.team_assembly ==
"random_draw"`, build `hook = _build_role_hook(tournament)` and pass it as
`before_round_hook=hook`; the `else` branch is **byte-identical** to today (preset path
unchanged). Every other line of `play_next_node` (per-Match-atomic body, clinch,
advance, RR / Swiss / RR→DE guards, crown) is **untouched**. The NEW module-level
helper **`_build_role_hook(tournament) -> Callable`** reads
`tournament.role_assignment_mode` and returns a closure `(round_number, team_red,
team_blue)` that: (1) for each of the two drawn Teams loads its
`TournamentPlayerEntry` rows (`tournament.player_entries.filter(drawn_team=team)`) and
builds the tier→player_id map; (2) draws a **fresh per-Round RNG** `rng =
random.Random()` (default OS entropy, fresh every call — tournament sims are
non-deterministic); (3) **`random` mode:** call `build_random_role_assignment` **per
team independently**; **`per_tier` mode:** call `build_per_tier_role_assignment`
**once** and apply that single `{tier: slot}` bijection to BOTH teams' tier→player
maps; (4) rewrite **both** drawn Teams' `slot_*` FKs **in memory** from the resulting
`{slot_suffix: player_id}` maps (`team.slot_commander_id` … `team.slot_ammo_id`) —
**no `.save()`** (transient per-Round assignment; the durable truth is the
`TournamentPlayerEntry` tier + `drawn_team`). The role draw uses a fresh
`random.Random()`, **NOT the SIM-07 seed chain** — no SIM-07/08 interaction, no Score
Calibration re-baseline.

**Views / URLs (`matches/tournament_views.py`, `matches/tournament_urls.py`).** All new
URL names are **bare** (no `app_name`, mounted at `/tournaments/`).

- **CHANGED — `tournament_create`.** Reads a new POST field **`team_assembly`**
  (forgiving fallback: only `"preset"` / `"random_draw"`, else `"preset"`) and
  **`role_assignment_mode`** (only `"random"` / `"per_tier"`, else `"random"`); stamps
  both via `Tournament.objects.create(...)`. The `format` select is **unchanged** — a
  `random_draw` Tournament uses `format="round_robin_double_elim"` (the `rrde_combo`
  wb/lb select is reused verbatim). For `random_draw` **participants are NOT chosen at
  create time** — the Tournament is created in `setup` with an **empty** pool, filled
  on the detail page. NEW create-form DOM ids: **`tournament-create-team-assembly`**
  (the `<select name="team_assembly">`), **`tournament-create-role-assignment-mode`**
  (the `<select name="role_assignment_mode">`, shown by the existing
  `tournamentCreateToggle` JS only when `team_assembly == "random_draw"`). Every
  existing create-form id is unchanged.
- **NEW — pool intake (three sources), draw, re-roll, hand-edit.** Mirror the
  LG-02a/a-2 Team-intake at **Player** granularity. All POST, `@transaction.atomic`,
  **setup-only** (reject with `messages.error` + redirect once `tournament.is_locked`).
  All create `TournamentPlayerEntry` rows (tier `null`, `drawn_team` `null` until the
  draw). Generated / CSV Players are created on the Free Agents Team via
  `teams.models.get_free_agents_team()`.

  | URL name | Path | View fn | What it does |
  |---|---|---|---|
  | `tournament_pool_add_existing` | `<id>/pool/add-existing/` | `tournament_pool_add_existing` | Add selected existing `Player`s as pool entries. |
  | `tournament_pool_generate` | `<id>/pool/generate/` | `tournament_pool_generate` | Generate N fresh Players via the LG-00 pure generator (`draw_stats` / `draw_preferred_roles`) on the Free Agents Team, add as pool entries. |
  | `tournament_pool_import` | `<id>/pool/import/` | `tournament_pool_import` | CSV import via the LG-00b `RosterImportForm` + `parse_roster_csv`; **each CSV row = one pool Player** (team-grouping IGNORED). |
  | `tournament_pool_remove` | `<id>/pool/remove/` | `tournament_pool_remove` | Remove a pool entry (by `player_id` / entry id) while in setup. |
  | `tournament_draw` | `<id>/draw/` | `tournament_draw` | Run / re-roll the draw: validate pool size, build drawn Teams + participants, persist. |
  | `tournament_draw_edit` | `<id>/draw/edit/` | `tournament_draw_edit` | Admin hand-edit of a drawn entry's `tier` / `drawn_team` (the variation mechanism). |

  Place these adjacent to the other `<int:tournament_id>/…` routes (after
  `import-participants/`). The existing **`tournament_lock`** view is **reused
  unchanged** to reach `active`: `lock_and_build()` takes the RR→DE branch over the
  drawn Teams (now `TournamentParticipant` rows), validating `>= 4` participants as
  today — **no `lock_and_build` change** (the draw must have produced participants +
  drawn Teams before lock).

- **CSV-for-a-player-pool reconciliation (`tournament_pool_import`).** The LG-00b /
  LG-02a-2 `parse_roster_csv` groups rows by team (`ParsedRoster.by_team`); a player
  pool needs players, not team-grouped rosters. So `tournament_pool_import` calls
  `parse_roster_csv(decoded_text)` (reuse — header validation, per-row coercion,
  bundled `RosterImportError`), **ignores `by_team`**, iterates `parsed.rows` (flat
  CSV-order) and treats **each `ParsedRow` as one pool Player** —
  `Player.objects.create(team=get_free_agents_team(), name=row.name,
  preferred_roles=row.preferred_roles, **row.profile, **row.stats)` then a
  `TournamentPlayerEntry`. The CSV `role` (slot intent) and `team` columns are **NOT
  used** (slots are per-Round draw assignments; team membership is the pool). Does
  **NOT** call `_check_db_slot_collisions` / `_apply_roster` (roster-slot helpers
  irrelevant to a flat pool). Error branch: `transaction.set_rollback(True)` + re-render
  the detail page (**HTTP 200**) with the bound form + `exc.errors` (mirrors
  `tournament_import_participants`).

- **`tournament_draw` persistence.** (1) Validate pool: `N =
  tournament.player_entries.count()`; require `N % 6 == 0` AND `N >= 24` (≥ 4 teams) —
  else `messages.error` + redirect, **no writes**. (2) Build the `(player_id,
  overall_rating)` pool list (`entry.player.overall_rating`), call `compute_draw(pool)`
  (pure). (3) **Re-roll cleanup:** if drawn Teams already exist, delete them
  (`is_draw_team=True` for this tournament) + their `TournamentParticipant` rows and
  null the entries' `tier` / `drawn_team` (idempotent — `compute_draw` is
  deterministic, so a re-roll reproduces the same split; hand-edits are the variation).
  (4) For each `DrawnTeamPlan`: create `Team.objects.create(name="<Draw Team N>",
  is_draw_team=True)`, set the 6 `slot_*` FKs from the tier-ordered `player_ids` via an
  **initial valid no-duplicate assignment** (e.g. tier order → `ROLE_SLOTS` order;
  satisfies the relaxed `roster_errors`), `team.save()`; create
  `TournamentParticipant(tournament=tournament, team=team, seed=N+1)` (1-based draw
  order / RR seed, mirrors LG-02a); fill each member entry's `tier` + `drawn_team`. (5)
  **Does NOT reassign `Player.team`** — drawn Teams reference borrowed Players via slot
  FKs only; `PlayerRoundState` references the real Player so career stats stay unified.

- **`_detail_context(tournament)` additions.** Keeps its existing 14 keys verbatim and
  adds (empty/defaulted for `preset`): **`team_assembly`** (`str`),
  **`role_assignment_mode`** (`str`), **`pool_entries`**
  (`list[TournamentPlayerEntry]`, `select_related("player", "drawn_team")`, ordered
  tier-then-player-id; `[]` for preset), **`pool_size`** (`int`), **`is_drawn`**
  (`bool` = `tournament.player_entries.filter(drawn_team__isnull=False).exists()`),
  **`pool_import_form`** (`RosterImportForm()`) and **`pool_import_row_errors`**
  (`list[RowError]`, default `[]`) — the player-pool CSV intake form + errors (parallel
  to the existing `import_form` / `import_row_errors`).

**Template (`templates/matches/tournament_detail.html`) — new surface, LOCKED DOM
ids.** Rendered **only** when `team_assembly == "random_draw"`. Pool intake (setup):
`tournament-pool-section` (wrapper); `tournament-pool-add-existing-form` / `-select` /
`-submit`; `tournament-pool-generate-form` / `-count` / `-mean` / `-std-dev` /
`-submit`; `tournament-pool-import-form` / `-file` / `-submit` / `-template-link` /
`-errors` + per-row `tournament-pool-import-error-{row_num}-{field|"row"}`;
`tournament-pool-table` (one row `tournament-pool-entry-{player_id}` each) with
per-entry `tournament-pool-remove-{player_id}`; `tournament-pool-size` (renders
`pool_size`); `tournament-pool-invalid-notice` (shown when `pool_size % 6 != 0 or
pool_size < 24`, carrying the substrings `divisible by 6` / `at least 24`). Draw:
`tournament-draw-form` / `-submit` (enabled only when pool size valid + setup);
`tournament-draw-reroll-submit` (re-roll, same endpoint, shown once drawn);
`tournament-draw-table` (one section `tournament-draw-team-{team_id}` per drawn team,
one row per member tagged with its `tier`); the hand-edit
`tournament-draw-edit-form` / per-entry `tournament-draw-edit-{player_id}`. **Reused
verbatim:** the lock control (`tournament-lock-form` / `-submit`), play controls
(`tournament-play-next-*` / `tournament-play-all-*`), and the champion banner
(`tournament-champion-banner`). Once drawn + locked, the Tournament renders the
existing RR→DE crosstable / cut-labels / DE-finals surfaces over the drawn Teams
**unchanged**.

**Admin (`matches/admin.py`).** `TournamentPlayerEntry` registered after the existing
tournament admins — `list_display = ("tournament", "player", "tier", "drawn_team")`.
The two new `Tournament` fields and `Team.is_draw_team` auto-surface on the existing
change forms; existing inlines reused; no existing registration touched.

**Migrations (no `RunPython`, no backfill — ADR-0004 disposable-sandbox precedent).**
**`matches/migrations/0040_tournament_random_draw.py`** (dep `0039_tournament_swiss`
+ the new `teams` migration — cross-app dependency, since
`TournamentPlayerEntry.drawn_team` and the draw both reference the new `Team` field):
ops in pinned order — `AddField(Tournament.team_assembly)` →
`AddField(Tournament.role_assignment_mode)` → `CreateModel(TournamentPlayerEntry)`
(incl. the `UniqueConstraint` + `Meta.ordering`). **`teams/migrations/00XX_team_is_draw_team.py`**
(next sequential `teams` number, resolved via `makemigrations teams`): a single
`AddField(Team.is_draw_team)`.

**Determinism / scope.** **The draw is deterministic** (straight-tiers + greedy
balance, no RNG — re-roll idempotent; admin hand-edit is the variation mechanism); the
**per-Round role draw is non-deterministic** (fresh `random.Random()` every Round). The
per-Match sims stay non-deterministic ⇒ **no SIM-07 / SIM-08 interaction, NO Score
Calibration re-baseline** (no simulation mechanics change — only which Player occupies
each role slot). **Players are referenced, not reassigned** — `Player.team` stays put;
ownership lives on `TournamentPlayerEntry`; a Player may be on draw teams across
different Tournaments but **never two in the same Tournament** (the
`unique(tournament, player)` constraint). **Scope-out (DEFERRED to LG-02x-2):** Duos /
Trios player pairs/triples on 6v6 teams, the `TournamentSubGroup` model, and
per-subgroup stat tracking — this slice is **single-Player pool only**.

**Tests:** `matches/tests/test_draw.py` (NEW — pure, no DB: `compute_draw`
straight-tier formation + greedy-balance to the weakest team + deterministic /
idempotent + rating-DESC / player-id-ASC sort + `ValueError` on `N % 6 != 0` and
`N < 24` + N=24/30/48 worked cases; `build_random_role_assignment` permutation of all
6 `ROLE_SLOTS` / one shuffle / no dup; `build_per_tier_role_assignment` `{tier: slot}`
bijection / one shuffle; `TestNoDjangoImportsLeaked`). `test_tournament_models.py`
(extend — `team_assembly` / `role_assignment_mode` choices + defaults;
`TournamentPlayerEntry` create / `unique(tournament, player)` rejection / CASCADE on
tournament delete / SET_NULL on team delete / `Meta.ordering`). `teams/tests/test_models.py`
(extend — relaxed roster rule: a draw team with borrowed Players has no "does not
belong" error, but a duplicate player OR a 3rd non-Scout role **still** errors, and a
non-draw team with a foreign player **still** errors; `Team.is_draw_team` default +
persistence). `test_simulation_view_paths.py` (extend — `before_round_hook=None` is
byte-unchanged; a hook is invoked once per round with `(round_number, team_red,
team_blue)`, round 2 receiving swapped `(team_blue, team_red)`; a hook rewriting
`slot_*` FKs changes the roster the round sims against). `test_tournament_engine.py`
(extend — a `random_draw` Tournament routes `play_next_node` through the hook path; a
`preset` Tournament's `simulate_match` call is unchanged, no hook). `test_tournament_views.py`
(extend — create-form selects + POST persistence + fallback; pool intake (existing /
generate / CSV) creates entries on the Free Agents Team; CSV error branch re-renders
200 with `exc.errors` and zero writes; `tournament_draw` validates `N % 6` / `N >= 24`,
builds drawn Teams + participants + fills tier/drawn_team, re-roll idempotent,
hand-edit mutates a single entry; lock reached via `tournament_lock` over drawn Teams;
the new `_detail_context` keys + pool/draw DOM ids render). `test_tournament_tasks.py`
(extend — `play_tournament_task` drains a `random_draw` RR→DE Tournament to a champion
under `CELERY_TASK_ALWAYS_EAGER`, **non-deterministic** — assert champion stamped +
`state="completed"`, never exact point totals). Tests assert on pure functions,
persisted row shapes, constraints, the hook contract, DOM ids — **never** on exact
simulated point totals.

**Locked names:** see the seam contract
[`.claude/worktrees/lg-02x-1-seam-contract.md`](../../.claude/worktrees/lg-02x-1-seam-contract.md)
for the authoritative list of every name / signature / dict-key / DOM-id / literal
(`team_assembly` / `role_assignment_mode` + choices, `TournamentPlayerEntry`,
`compute_draw` / `DrawnTeamPlan` / `ROLE_SLOTS` / `build_random_role_assignment` /
`build_per_tier_role_assignment`, `simulate_match(before_round_hook=...)`,
`_build_role_hook`, the 7 view fns + URL names, `Team.is_draw_team`, the DOM ids +
`_detail_context` keys, the N-divisibility rule, and migrations
`0040_tournament_random_draw` + `teams 00XX_team_is_draw_team`).

## LG-02-Part2a season phase foundation

Introduces the persisted **`SeasonPhase`** model and retrofits the **Season**
read-path so the per-Season schedule is sourced through a single **chokepoint on
`Season`** instead of reading `Season.schedule_format` / calling
`generate_schedule(...)` inline at every site. This is the **foundation slice**
of LG-02-Part2 — it generalises the previously-implicit "a Season *is* a single
**round-robin**" assumption into an **ordered list of typed phases**. The
**LG-02-Part2 grill (2026-06-04)** resolved that this phase model **IS** the
LG-06 phased-lifecycle model — off-season / regular / tournament are *phase
types*, not a parallel abstraction. **Zero user-visible change** in Part2a: a
one-phase `round_robin` Season is byte-identical to today's Season, and existing
phase-less Seasons keep playing via the implicit-single-phase fallback. The
composer UI (Part2b), per-phase format (Part2b), the heterogeneous multi-phase
play loop + `SeasonPhase → Tournament` embed (Part2c), and the `member_night`
phase are all **scoped out** (see below). **NEW
[ADR-0023](../../docs/adr/0023-season-phase-composable-structure.md)** records
the ordered-typed-phase decision, the no-backfill defensive fallback, and the
forward one-directional `SeasonPhase → Tournament` FK; the **Season phase**
CONTEXT.md glossary term carries the domain language (do not edit). Seam
contract:
[`.claude/worktrees/lg-02-part2a-seam-contract.md`](../../.claude/worktrees/lg-02-part2a-seam-contract.md).

**NEW model `SeasonPhase` (LOCKED) — `matches/models.py`, declared immediately
after the `Season` class and before `Tournament`.** One ordered unit of a
Season's structure. Fields: **`season`** (`FK("matches.Season",
on_delete=CASCADE, related_name="phases")`); **`ordinal`**
(`PositiveSmallIntegerField()` — **1-based** ordering within the Season, no
default, set explicitly at create); **`phase_type`** (`CharField(max_length=16,
choices=PHASE_TYPE_CHOICES, default="round_robin")` — `max_length=16` headroom
for the 12-char `"member_night"`). The class attribute **`PHASE_TYPE_CHOICES`**
declares **all three** values now even though only `round_robin` has behaviour
this slice: `(("round_robin", "Round-robin"), ("tournament", "Tournament"),
("member_night", "Member night"))`. `Meta.ordering = ["ordinal"]` and a
**`UniqueConstraint(fields=["season", "ordinal"],
name="uniq_season_phase_ordinal")`** — the structural guarantee that two phases
of the *same* Season cannot share an ordinal (the same ordinal is fine across
*different* Seasons). `__str__` (locked shape): `f"{self.season} — phase
{self.ordinal} ({self.phase_type})"` (em-dash U+2014, matching the
`Season.__str__` convention). **NO FK to `Tournament`** and **NO per-phase
`schedule_format`** field this slice — the `round_robin` phase resolves fixtures
via the legacy `Season.schedule_format` (which **stays as-is**); both are
Part2b/Part2c.

**Migration `0041_season_phase` — `CreateModel`-only, NO `RunPython`.** Single
`CreateModel(SeasonPhase)` carrying the `UniqueConstraint` + `Meta.ordering`;
dependency `("matches", "0040_tournament_random_draw")`. **No `RunPython`, no
`RunSQL`, no backfill, no data migration** — the
[ADR-0004](../../docs/adr/0004-simulation-data-is-disposable.md) disposable-data
precedent (same as the LG-01 `0029` and every prior `Season`/`Match` add).
Legacy phase-less Seasons rely on the in-memory fallback **forever** — they are
never backfilled with a row.

**Chokepoint on `Season` (LOCKED) — two read-only / pure-derivation methods (no
DB write, no RNG).** **`Season.ordered_phases() -> list[SeasonPhase]`** returns
the Season's phases in ordinal order: `list(self.phases.all())` when ≥ 1
persisted row exists (`Meta.ordering` guarantees order); when **zero** rows
exist it returns a **one-element list whose member is a real but UNSAVED
`SeasonPhase`** — the implicit fallback (see below). **`Season.scheduled_fixtures()
-> list[ScheduleFixture]`** returns the flat fixture list for the schedule: this
slice it is exactly the `round_robin` phase's list, sourced via
`generate_schedule(team_ids, self.schedule_format)` where `team_ids` follows the
existing, now-centralised rule — **draft** Season ⇒ `sorted(t.id for t in
season.teams.all())`; **active/completed** Season ⇒
`list(season.starting_team_ids_json or [])`. Exactly ONE `round_robin` phase
exists this slice (explicit or implicit), so the return is the single RR fixture
list — **NO cross-phase composition, NO matchday offsetting** (Part2c). Returns
**`[]`** when `len(team_ids) < 2` (mirrors the current per-site guard) — never
raises. `ScheduleFixture` is the existing frozen dataclass from
`matches/schedule_generator.py`, **consumed verbatim**. `scheduled_fixtures()`
is the **sole** `generate_schedule(...)` caller for the Season read-path after
this slice.

**Implicit-fallback representation (LOCKED).** The phase-less fallback is a
**real but UNSAVED `SeasonPhase` instance** — NOT a sentinel class, NOT a dict —
built as `SeasonPhase(season=self, ordinal=1, phase_type="round_robin")` with
**no `.save()`** (so `pk is None`; needs no DB row). Locked rationale: a real
instance keeps the downstream type uniform (`list[SeasonPhase]`), exposes
`.phase_type` / `.ordinal` / `.season` for Part2b/2c without a shim, and `pk is
None` is the unambiguous "implicit" marker a test asserts on. A private
`Season._implicit_phase()` builder is **Code-agent discretion** (only the
returned type + field values + `pk is None` are pinned).

**Read-path retrofit — every Season-read call site routes through the
chokepoint.** Each was an inline `generate_schedule(team_ids,
…schedule_format)`; each becomes `… = season.scheduled_fixtures()` (or
`self.scheduled_fixtures()`), preserving byte-identical output: **`Season._is_finished`**
(`models.py:1015` — the `< 2`-team / empty-fixtures early-`False` guard becomes
`if not fixtures: return False`; behaviour-equivalence is load-bearing — a
phase-less Season MUST return today's result); **`play_season_task`**
(`tasks.py:190` — downstream `played_keys` build / `select_play_fixtures` /
per-fixture `simulate_scheduled_round` loop unchanged); **`season_schedule`**
(`league_views.py:371`); **`_build_dashboard_context`** (`league_views.py:646`);
**`league_history`** Play-Week preview (`league_views.py:1512`);
**`team_schedule`** (`league_views.py:1822`). `Season.complete_if_finished`
(`models.py:984`) is **unchanged** (routes through `_is_finished`). The pure
module **`matches/season_dashboard.py`** (`find_next_fixture` / `round_progress`
/ `find_next_matchday` / `select_play_fixtures` / `compute_leaders` /
`LeaderRow`) is **NOT edited** — it stays pure (frozen no-Django import
allowlist, defended by `TestNoDjangoImportsLeaked`); the **caller** builds the
fixtures via `scheduled_fixtures()` and passes the list in, exactly as today.
**Deliberately NOT touched:** `Tournament.lock_and_build` (`models.py:1233`) and
`tournament_views.py:435` both call `generate_schedule` for a **standalone
Tournament**, not a Season — the chokepoint is Season-only.

**Create-on-Season-create — one explicit `round_robin` phase per new Season.**
Both `@transaction.atomic`-decorated draft-creation views gain one
`SeasonPhase.objects.create(season=season, ordinal=1, phase_type="round_robin")`
**inside the existing atomic block, after the `Season.objects.create(...)`** (so
a rollback drops the phase too): **`league_create`** (`league_views.py`, after
the create at `:527`) and **`next_season`** (after the create at `:1900`). No
other site writes a Season this slice. **Admin-created Seasons get the fallback
for free** (zero rows ⇒ implicit phase) — no admin-side creation hook.

**Admin (`matches/admin.py`).** `SeasonPhase` is added to the model import and
registered after the existing `SeasonAdmin` (no existing registration touched):
`@admin.register(SeasonPhase)` `SeasonPhaseAdmin(list_display = ("season",
"ordinal", "phase_type"))`. An optional `SeasonPhaseInline(TabularInline,
model=SeasonPhase, extra=0)` on `SeasonAdmin.inlines` is **Code-agent
discretion** (the standalone registration is the mandatory part).

**Determinism.** **No simulator change**, **no RNG** consumed by any new code —
the model methods are pure derivations over the ORM + the existing deterministic
`generate_schedule`, which is a pure function of the *set* of `team_ids`, so
routing it through the chokepoint changes nothing about which fixtures are
produced. **No SIM-07 / SIM-08 contract interaction, NO Score Calibration
re-baseline.** The `BatchSimulator` and `generate_schedule` are consumed
verbatim.

**Scope-out (LOCKED — DO NOT build here).** **Composer UI** (picking/ordering
phases at create-League time) → Part2b. **Per-phase `schedule_format`** → none;
the RR phase reads `Season.schedule_format`; Part2b. **Multi-phase play loop** /
cross-phase matchday offsetting / per-phase standings scoping → Part2c
(`scheduled_fixtures()` returns exactly ONE RR list this slice).
**`SeasonPhase → Tournament` FK** + tournament embed → Part2b/2c.
**`member_night` / `tournament` phase behaviour** → declared in the enum, inert
this slice (only `round_robin` resolves fixtures); `member_night` is deferred to
its own sandbox grill (see PLAN.md). **CONTEXT.md** ("Season phase" term) and
**ADR-0023** are already written.

**Tests:** `matches/tests/test_season_phase.py` (NEW — model fields / locked
types + defaults / `PHASE_TYPE_CHOICES` declares all three; `Meta.ordering ==
["ordinal"]` (phases inserted 2,1,3 iterate 1,2,3); the
`uniq_season_phase_ordinal` constraint rejects a duplicate `(season, ordinal)`
but allows the same ordinal across different Seasons; `season.phases` reverse
accessor + CASCADE delete; `ordered_phases()` returns explicit phases in ordinal
order, and a phase-less Season returns a one-element list whose member is an
**unsaved** `SeasonPhase` with `pk is None`, `phase_type == "round_robin"`,
`ordinal == 1`, `season == self`; the **behaviour-equivalence guarantee** — a
Season with one explicit `round_robin` phase vs an otherwise-identical
phase-less Season return the **identical** `scheduled_fixtures()` list and
produce the **identical** `_is_finished()` / `complete_if_finished()` outcome
(same state flip, same `champion_team` id) over hand-built `Match`/`GameRound`
rows — asserting on schema-level outcomes, **not** simulated point totals;
`scheduled_fixtures()` returns `[]` for a `< 2`-team Season without raising).
`matches/tests/test_league_create.py` (extend — a successful `league_create`
POST creates the Season AND exactly one `SeasonPhase(ordinal=1,
phase_type="round_robin")`; the existing rollback test leaves **zero**
`SeasonPhase` rows). `matches/tests/test_league_next_season.py` (extend —
`next_season` creates the new draft Season AND its one `round_robin` phase).
`matches/tests/views_tests.py` (extend — read-path equivalence at the view
layer: the rendered schedule/dashboard for a phase-less Season is byte-identical
to one with an explicit RR phase). `matches/tests/test_league_play.py` (extend —
`play_season_task` over a phase-less Season plays the same fixtures it does
today, under the existing `CELERY_TASK_ALWAYS_EAGER` conftest). Tests assert on
persisted row shapes, the constraint, ordering, the fallback representation, and
behaviour-equivalence — **never** on exact simulated point totals.

**Locked names:** see the seam contract
[`.claude/worktrees/lg-02-part2a-seam-contract.md`](../../.claude/worktrees/lg-02-part2a-seam-contract.md)
for the authoritative list — `SeasonPhase` (fields `season` /
`FK(Season, CASCADE, related_name="phases")`, `ordinal` /
`PositiveSmallIntegerField` 1-based, `phase_type` / `CharField(max_length=16,
default="round_robin")`), `PHASE_TYPE_CHOICES` (all three values),
`Meta.ordering = ["ordinal"]` + `uniq_season_phase_ordinal`, the `Season.phases`
reverse accessor, the chokepoint `Season.ordered_phases()` /
`Season.scheduled_fixtures()`, the unsaved-`SeasonPhase` implicit fallback
(optional `Season._implicit_phase()`), the two `SeasonPhase.objects.create(...)`
creation sites (`league_create` / `next_season`), `SeasonPhaseAdmin`
(+ optional `SeasonPhaseInline`), and the migration `0041_season_phase` (dep
`0040_tournament_random_draw`, `CreateModel`-only).

## LG-02-Part2b create-league phase composer

Builds on the Part2a foundation by giving the **create-League** surface a
vanilla-JS **"+" composer** that writes **multiple ordered `SeasonPhase` rows**
(versus Part2a's single auto-created `round_robin` phase), plus two **dormant**
`SeasonPhase` columns and a new **pure** parsing module. A Season's structure is
now author-composable as an ordered list of phase *types* (RR / Tournament) at
create time; the composer serializes the ordered rows into a hidden wire-format
field, the form's `clean()` parses it through the pure module, and **both**
`SeasonPhase`-creation sites (`league_create`, `next_season`) loop over the
parsed specs. **The read-path is UNCHANGED** — the Part2a chokepoint
`Season.scheduled_fixtures()` still calls `generate_schedule(team_ids,
Season.schedule_format)` and **ignores the phase rows**: it plays the first
`round_robin` phase and treats `tournament` phases as invisible. Part2b *writes*
more rows but *reads* none of them. **No simulator change, no RNG, no SIM-07 /
SIM-08 interaction, NO Score Calibration re-baseline, no read-path / chokepoint
change.** The forward `SeasonPhase → Tournament` FK column lands here but is
**ALWAYS NULL in Part2b** (the lazy build + hand-off is Part2c). The
[ADR-0023](../../docs/adr/0023-season-phase-composable-structure.md)
ordered-typed-phase decision and the **Season phase** CONTEXT.md glossary term
already carry the domain language (both already written — do not edit). Seam
contract:
[`.claude/worktrees/lg-02-part2b-seam-contract.md`](../../.claude/worktrees/lg-02-part2b-seam-contract.md).

**Two NEW dormant `SeasonPhase` columns (LOCKED) — `matches/models.py`, appended
immediately after the existing `phase_type` field.** Everything else on
`SeasonPhase` is UNCHANGED (`PHASE_TYPE_CHOICES`, `season`, `ordinal`,
`phase_type`, `Meta.ordering = ["ordinal"]`, the `uniq_season_phase_ordinal`
constraint, `__str__`). **`schedule_format`**
(`CharField(max_length=32, null=True, blank=True)`) — **dormant**: nothing reads
it this slice. At create, a `round_robin` phase **copies `Season.schedule_format`**
(today's value `"single_round_robin"`); a `tournament` phase gets **`NULL`**.
**`tournament`** (`ForeignKey("matches.Tournament", null=True, blank=True,
on_delete=models.SET_NULL, related_name="season_phases")`) — the forward
one-directional `SeasonPhase → Tournament` link, **ALWAYS NULL in Part2b**. The
reverse accessor is `tournament.season_phases`; the ref is **same-app**
(`matches.Tournament`) so there is **no cross-app dependency**. Tournament stays
season-agnostic ([ADR-0019](../../docs/adr/0019-tournament-bracket-model.md)
survives) — the FK points one way only.

**Migration `0042_seasonphase_format_tournament` — two `AddField`, NO
`RunPython`.** Dependency `("matches", "0041_season_phase")` (the latest matches
migration ⇒ this is `0042`). Two `AddField` ops in order (`schedule_format`,
then `tournament`). **No `RunPython`, no `RunSQL`, no backfill, no data
migration** — the
[ADR-0004](../../docs/adr/0004-simulation-data-is-disposable.md) disposable-data
precedent (same posture as `0041`). The `tournament` FK references
`matches.Tournament` (same app) so there is **no cross-app migration
dependency**.

**NEW pure module `matches/phase_composer.py` (LOCKED) — Django-free composer
parser.** Mirrors the `matches/standings.py` / `matches/schedule_generator.py`
purity discipline: a **frozen import allowlist of `dataclasses` and `typing`
ONLY** — NO `django`, NO ORM, NO `random` / `datetime` / `json` / I/O /
logging — defended by **`TestNoDjangoImportsLeaked`** (subprocess fresh-import +
`sys.modules` walk). The **frozen dataclass** **`PhaseSpec`** has three fields:
`ordinal` (`int`, 1-based, contiguous `1..N` in composer order), `phase_type`
(`str`, `"round_robin"` | `"tournament"`), and `schedule_format`
(`Optional[str]` — the season format for an RR phase, `None` for a tournament
phase). The **function** **`parse_phase_composition(raw: str, *,
season_schedule_format: str) -> list[PhaseSpec]`** parses the composer's
serialized output. The **wire format (LOCKED) is comma-separated phase-type
tokens** — e.g. `"round_robin,tournament"` — parsed with `str.split(",")` and a
`str.strip()` per token (chosen over JSON to keep the allowlist minimal, no
`json` import; the template serializes the ordered rows into this exact form).
**Behaviour:** an **empty / blank `raw`** (`""` or whitespace-only after strip)
**short-circuits to exactly one** `PhaseSpec(ordinal=1,
phase_type="round_robin", schedule_format=season_schedule_format)` — the Part2a
default — **before any validation** (so it is never treated as "zero RR").
Otherwise: split on `,`, strip each token, assign **contiguous ordinals 1..N**
in composer order, set `schedule_format = season_schedule_format` for
`round_robin` specs and `None` for `tournament` specs. Valid phase types are
**`"round_robin"` and `"tournament"` only** — `"member_night"` is **NOT
selectable** in Part2b and is rejected as an unknown type. The module raises
**plain `ValueError`** (NOT `django.core.exceptions.ValidationError` — keep it
Django-free) with these **exact message strings**: zero `round_robin` in a
non-empty composition ⇒ `"composition must contain at least one round-robin
phase"`; an unknown phase type (incl. `"member_night"` or any non-RR /
non-tournament token) ⇒ `f"unknown phase type: {token!r}"`; malformed input
(an empty token between commas like `"round_robin,,tournament"`, or any token
empty after strip) ⇒ `"malformed phase composition"`. **Validation order**
within a non-empty `raw`: tokenise → reject malformed (empty token) → reject
unknown type per token → after building specs, reject zero `round_robin`.

**Form (`matches/forms.py`, `CreateLeagueForm`) — hidden `phases` field +
`clean()` seam.** A new hidden field **`phases`** (`forms.CharField(widget=
forms.HiddenInput(attrs={"id": "league-create-phases"}), required=False)`)
carries the serialized composition; the existing **disabled Season-level
`schedule_format` `ChoiceField` STAYS unchanged** (it is the live read-path
source, locked at `"single_round_robin"`). `clean()` (preserving all existing
LG-01j map-mode-vs-pool rules verbatim) calls the pure module
`parse_phase_composition(cleaned_data.get("phases", "") or "",
season_schedule_format=cleaned_data.get("schedule_format") or
"single_round_robin")` inside a `try`; on `ValueError` it re-raises as a
`forms.ValidationError(str(exc))` attached to the **`"phases"`** field, else it
stashes the result under **`cleaned_data["phase_specs"]`** (`list[PhaseSpec]`).
The `season_schedule_format` argument is the form's own `schedule_format` value
(the disabled field's locked `"single_round_robin"`).

**Views (`matches/league_views.py`) — both creation sites loop over the specs.**
In **`league_create`** (~line 553, inside the existing `@transaction.atomic`
block) the single `SeasonPhase.objects.create(season=season, ordinal=1,
phase_type="round_robin")` is **replaced by a loop** over
`form.cleaned_data["phase_specs"]`, each iteration creating a `SeasonPhase` with
`season=season`, `ordinal=spec.ordinal`, `phase_type=spec.phase_type`,
`schedule_format=spec.schedule_format`, and **`tournament=None`** (always). In
**`next_season`** (~line 1942, inside its existing `@transaction.atomic` block)
there is **no composer** — it **carries the previous Season's composition
forward** (mirroring the team-id / map-pool carry-forward): the single
`SeasonPhase.objects.create(...)` is replaced by a **copy loop** over the source
Season's `phases.all()` (the `latest_completed` carry-forward source Season;
`Meta.ordering=["ordinal"]` guarantees order), copying `ordinal`, `phase_type`,
and `schedule_format` **verbatim** while **resetting `tournament=None`**. Both
loops stay inside the existing atomic blocks so a rollback drops the phases too.

**Template (`templates/leagues/create.html`) — vanilla-JS composer (no
framework, inline `<script>`, per the LG-01d precedent).** A **"+ Add block"**
button clones a row template into the composer container; each row has a
phase-type `<select>` (`round_robin` / `tournament`) and, for a `round_robin`
row, a `schedule_format` `<select>` with the single option `single_round_robin`
(mirroring the disabled Season-level one); rows are removable (reorder optional).
On submit the JS serializes the ordered rows into the hidden
`#league-create-phases` input in the **wire format pinned above** (comma-joined
phase-type tokens in row order, e.g. `"round_robin,tournament"`). A **"member
nights coming soon"** note and a per-tournament-block **"build coming in a later
release"** flag communicate the deferred surfaces. **LOCKED DOM ids / class
substring:** `league-create-phases-composer` (outer composer container `<div>`);
`league-create-add-block` (the "+ Add block" button); `league-create-phases`
(the hidden input — also the form field's widget id above);
`league-create-phase-row-{i}` (per-row wrapper, `{i}` = 0-based row index
assigned by JS); `league-create-phase-type-{i}` (per-row phase-type `<select>`);
`league-create-phase-format-{i}` (per-row `schedule_format` `<select>` on RR
rows); `league-create-member-night-note` (the "member nights coming soon" note);
and the CSS-class **substring** `phase-tournament-pending` (the per-tournament
"build coming later" flag). All new ids are net-new and **do not collide** with
the existing create.html ids (`league-create-form`, `league-create-league-name`,
`league-create-season-name`, `league-create-start-date`,
`league-create-num-teams`, `league-create-schedule-format`,
`league-create-mean`, `league-create-std-dev`, `league-create-map-mode`,
`league-create-map-pool`, `league-create-submit`).

**Admin (`matches/admin.py`).** `SeasonPhaseAdmin.list_display` extends from
`("season", "ordinal", "phase_type")` to **`("season", "ordinal",
"phase_type", "schedule_format", "tournament")`** — no other admin change.

**Composer scope + validity rules.** The composer offers **`round_robin` and
`tournament` only** (no `member_night`), and the non-RR (`tournament`) phases are
**persisted but dormant** — the Part2a chokepoint plays the **first `round_robin`
phase** via `Season.schedule_format` and never reads the others. Validity (the
pure module enforces): **≥ 1 `round_robin`** phase required; **`tournament`
phases may sit anywhere** (no ordering constraint this slice); an **empty
composer ⇒ a single `round_robin`** (Part2a equivalence); **contiguous ordinals
1..N** in composer order.

**Determinism / read-path-unchanged note.** **No simulator change, no RNG**
consumed by any new code — the composer parser is a pure string→specs derivation
and the creation loops are plain ORM writes. The Season read-path / chokepoint is
**byte-identical to Part2a** (it still plays exactly the first RR phase), so
there is **NO Score Calibration re-baseline** and no SIM-07 / SIM-08 contract
interaction. The dormant columns are written-but-never-read this slice.

**Deferred to Part2c (DO NOT build here).** The **per-phase seeding-mode /
tournament-kind field** on `SeasonPhase` (season-ending Standings-seeded vs
mid-season strength-/un-seeded, with its compose-time validity rule) → Part2c.
**Wiring the chokepoint to `phase.schedule_format`** (this slice's dormant
column) + the first alternative regular-season format → Part2c (until then the
read-path reads `Season.schedule_format`). **Per-tournament-block configuration**
(format / `team_assembly` / seeding) + the lazy tournament **build / hand-off**
(populating the `tournament` FK) → Part2c. `member_night` stays inert (its own
sandbox grill — see PLAN.md / LG-07).

**Tests:** `matches/tests/test_phase_composer.py` (NEW — pure-unit +
`TestNoDjangoImportsLeaked`): empty `raw` → the single RR default; an RR spec's
`schedule_format` is copied from `season_schedule_format`; a tournament spec's
`schedule_format` is `None`; contiguous ordinals `1..N`; multi-phase order
preserved; a zero-RR composition raises `ValueError("composition must contain at
least one round-robin phase")`; an unknown `phase_type` raises `ValueError`; a
`member_night` token is rejected (unknown-type); a malformed `raw` (empty token)
raises `ValueError("malformed phase composition")`; the purity subprocess check.
`matches/tests/test_season_phase.py` (EXTEND — `schedule_format` nullable +
default-`None` for a tournament phase; the `tournament` FK nullable + `SET_NULL`
+ `related_name="season_phases"`). `matches/tests/test_league_create.py` (EXTEND
— the composer happy path persists multiple ordered `SeasonPhase` rows with
correct ordinals / types / `schedule_format` and `tournament=None`; an empty
composer ⇒ a single `round_robin` (Part2a equivalence); a no-RR composition is
rejected at the form layer leaving **zero** League / Season / phase rows created
— transaction atomicity; the existing single-phase tests still pass).
`matches/tests/test_league_next_season.py` (EXTEND — `next_season` copies the
previous Season's full phase composition forward — ordinals / types /
`schedule_format` — with `tournament` reset to `NULL`).

**Locked names:** see the seam contract
[`.claude/worktrees/lg-02-part2b-seam-contract.md`](../../.claude/worktrees/lg-02-part2b-seam-contract.md)
for the authoritative list — **model fields** `SeasonPhase.schedule_format`
(`CharField(max_length=32, null=True, blank=True)`) / `SeasonPhase.tournament`
(`FK(matches.Tournament, null=True, blank=True, on_delete=SET_NULL,
related_name="season_phases")`); **migration**
`0042_seasonphase_format_tournament` (dep `0041_season_phase`, two `AddField`, no
`RunPython`); **pure module** `matches/phase_composer.py` — dataclass
`PhaseSpec(ordinal, phase_type, schedule_format)`, fn
`parse_phase_composition(raw, *, season_schedule_format) -> list[PhaseSpec]`;
**wire format** comma-separated phase-type tokens (`"round_robin,tournament"`,
`str.split(",")`); **`ValueError` strings** `"composition must contain at least
one round-robin phase"` / `f"unknown phase type: {token!r}"` / `"malformed phase
composition"`; **form** field `phases` (`HiddenInput`, id `league-create-phases`,
`required=False`) + `cleaned_data["phase_specs"]` (existing disabled
`schedule_format` field unchanged); **views** `league_create` (~553) spec loop /
`next_season` (~1942) carry-forward copy loop, both inside the existing
`@transaction.atomic`, `tournament=None` always; **template DOM ids**
`league-create-phases-composer`, `league-create-add-block`,
`league-create-phases`, `league-create-phase-row-{i}`,
`league-create-phase-type-{i}`, `league-create-phase-format-{i}`,
`league-create-member-night-note`, class substring `phase-tournament-pending`;
**admin** `SeasonPhaseAdmin.list_display = ("season", "ordinal", "phase_type",
"schedule_format", "tournament")`; **read-path UNCHANGED, `tournament` FK always
NULL, no re-baseline.**

## LG-02-Part2c-1 RR → single-elimination playoff embed

The first slice of LG-02-Part2c — a **thin orchestration layer** that takes a
Season composed of an ordered `round_robin` phase then a `tournament` phase,
plays the regular season, **auto-builds** a standings-seeded single-elimination
playoff bracket the moment the RR phase completes (matchups visible **before**
any playoff click), then drains the bracket to crown the **Season champion**. It
replaces Part2b's "play the first `round_robin` phase only" read-path with a
**phase cursor** + two **lifecycle hooks** on `Season`, an **auto-build** that
wires an existing standalone `Tournament` (consumed VERBATIM) into a
`SeasonPhase`, two new play views + a Celery task that drain the already-shipped
tournament engine, dashboard + template wiring to surface the playoff button
group, and one compose-time guard. **No `Match.season_phase` FK, no Match
migration, no simulator change, no tournament engine change, no Score Calibration
re-baseline.** The [ADR-0023](../../docs/adr/0023-season-phase-composable-structure.md)
ordered-typed-phase decision (extended with a "Part2c-1 consequences" addendum)
and the **Season phase** CONTEXT.md glossary term carry the domain language. Seam
contract:
[`.claude/worktrees/lg-02-part2c-1-seam-contract.md`](../../.claude/worktrees/lg-02-part2c-1-seam-contract.md).

**Phase cursor + derived completion (`matches/models.py`, on `Season`).**
**`Season.current_phase() -> SeasonPhase | None`** is a pure read-derivation (no
DB write, no RNG): it walks `ordered_phases()` (the Part2a chokepoint — ordinal
order, or the one-element implicit-`round_robin` fallback list with `pk is None`)
and returns the **first INCOMPLETE phase by ordinal**, or `None` once every phase
is complete. Completion is **DERIVED, not stored** — there is **no
`SeasonPhase.state` field**. The single derivation site is the private
**`Season._phase_complete(phase) -> bool`** (lives on `Season`, NOT `SeasonPhase`,
so it can reach `scheduled_fixtures()` / `_is_finished()` without a back-reference
dance): a `round_robin` phase is complete ⇔ the existing **`_is_finished()`**
all-fixtures-played check (REUSED verbatim — byte-identical to today for the
one-RR-phase case); a `tournament` phase is complete ⇔
**`phase.tournament_id is not None AND phase.tournament.state == "completed"`**.
A phase-less / single-RR-phase Season returns its (implicit or explicit) RR phase
while the RR is unfinished, then `None` once finished — exactly mirroring today's
"Season is done when all fixtures played". `member_night` and any future type are
inert this slice (the compose guard forbids composing one), so `_phase_complete`
returns `False` for them (unreachable). **NOTE on multi-RR:** this slice composes
exactly one RR phase, so `_is_finished()` (which covers the whole RR fixture list)
is the correct per-phase RR completion; per-phase RR fixture scoping for multi-RR
is **Part2c-2**.

**Auto-build on RR completion (`Season.activate_pending_tournament_phase() ->
None`, `@transaction.atomic`, idempotent).** The build hook. It is a **no-op**
when `current_phase()` is `None`, isn't a `tournament` phase, is already built
(`tournament_id is not None` — the idempotency guard), is the implicit fallback
(`pk is None`), or its **preceding** phase (`Season._preceding_phase(phase)` — the
phase one ordinal lower) isn't complete. Otherwise it builds: reads the preceding
phase's **final Standings** (`Season._final_standings_for_phase(phase)`, which
reuses `matches.standings.compute_standings` with the **exact** 8-key match dicts +
`(id, name)` enrolled tuples the old `_stamp_champion` assembled — `season_rounds`
omitted), creates a `Tournament(format="single_elimination",
team_assembly="preset", state="setup", name=f"{self.name} Playoffs")`, creates
**one `TournamentParticipant` per season team** with **`seed = StandingsRow.rank`**
(rank 1 → seed 1; `compute_standings` zero-fills every enrolled team so seeds are
dense `1..N`, satisfying `uniq_tournament_seed`), sets `phase.tournament` +
`phase.save(update_fields=["tournament"])`, then calls
**`tournament.lock_and_build()`** (the existing setup → active transition;
validates `>= 4` participants, builds + persists the `BracketNode` tree from the
seeds). The bracket is **visible immediately** — matchups exist before any playoff
click. A second call after a successful build hits the `tournament_id is not None`
guard (idempotent). A Season with `< 4` teams cannot reach a playoff
(`lock_and_build` raises `ValidationError`) — a degenerate config, not a happy
path.

**Rewritten completion (`Season.complete_if_finished() -> None`, REWRITTEN,
`@transaction.atomic`).** No-op on non-`active`; else it gates on the **FINAL
phase** (last ordinal, `ordered_phases()[-1]`) being `_phase_complete`, then
stamps the champion via **`Season._stamp_champion_for_final_phase(final_phase)`**
(which **replaces** the now-removed `_stamp_champion` — its standings-rank-1 logic
moves behind the RR branch). Champion = `final_phase.tournament.champion` when the
final phase is a tournament (with a defensive `None` guard that never blocks in
practice, since the engine stamps `state="completed"` + `champion` together), else
`compute_standings(...)[0]` of the final RR phase. **`_is_finished()` is
UNCHANGED** (still the RR all-fixtures-played check). **BYTE-IDENTICAL fallback:**
for a single-RR-phase Season (explicit or implicit `pk is None`), `final_phase` is
that RR phase, `_phase_complete(final_phase) == _is_finished()`, and the champion
is `compute_standings(...)[0]` — the same state flip + same `champion_team` id as
today.

**Post-round hook wiring (`matches/simulation/entrypoints.py`).**
`BatchSimulator.simulate_scheduled_round` already calls
`season.complete_if_finished()` after persistence (twice — once per Round branch).
Each of those two sites gains a **`season.activate_pending_tournament_phase()`
call IMMEDIATELY BEFORE** the existing `season.complete_if_finished()` call (the
build hook fires first, then the completion check). **Ordering is load-bearing:**
the build hook runs first so the moment the last RR fixture lands, the tournament
phase is built and becomes `current_phase()`; the completion check that follows
then sees the final phase is the (now-incomplete) tournament phase and does NOT
prematurely complete the Season. Both new calls are idempotent / no-op except at
the exact RR-completion boundary, so adding them to every persisted Round is safe
and cheap. **No simulator mechanics change, no RNG**; the
`simulate_scheduled_round` signature is unchanged.

**Celery task (`matches/tasks.py`) — `play_playoffs_task`.**
**`@shared_task(bind=True, name="matches.play_playoffs")`**, signature
`(self, season_id: int) -> dict`. Mirrors the `play_tournament_task` body pattern:
deferred imports, an inactive/unbuilt-guard early return of `{"completed": 0,
"total": 0}`, then **`while play_next_node(tournament) is not None`** drains the
bracket one Match at a time, emitting a stage-progress `update_state(state=
"PROGRESS", meta={"completed", "total"})` after each, with a
`finally: django.db.close_old_connections()`. After draining,
`season.complete_if_finished()` crowns the Season champion (the tournament final
phase is now complete; `tournament.champion` is set). **Return shape LOCKED:**
`{"completed": int, "total": int}` — **STAGE** counts from
`matches.bracket.stage_progress` (reused VERBATIM, fed by `_node_to_dict` over the
tournament's nodes), NOT node counts — matching the `play_tournament_task` shape
exactly so `_build_play_status_response` reads it unchanged. **NO outer
`@transaction.atomic`** — `play_next_node` is already `@transaction.atomic` per
Match (ADR-0016 precedent), so a mid-drain failure leaves every resolved node
committed and is resumable.

**Two play views (`matches/league_views.py`).**
**`play_single_round(request, season_id) -> HttpResponse`** (sync): POST-only
(`HttpResponseNotAllowed(["POST"])` first, 405 on GET — the LG-01d idiom), writes
`last_league_id` after the 404 guard, requires `current_phase()` to be a built
(`tournament_id is not None`) tournament phase else re-renders the dashboard with
`play_error` (the `_render_season_dashboard_error` 400-equivalent), plays **exactly
one** playoff Match via `play_next_node` (deferred import), then
`season.complete_if_finished()` (crowns the Season if the final node landed), then
**302 redirect** to `season_dashboard`.
**`play_playoffs(request, season_id) -> JsonResponse`** (async): POST-only (405
otherwise), same built-tournament guard but returns **409** JSON `{"error": ...}`
on mismatch (async endpoints return JSON, not a dashboard re-render — the
`tournament_play_all` precedent); happy path ⇒ `play_playoffs_task.delay(season_id)`
→ **202** JSON `{job_id, season_id}` (mirrors `play_two_months` / `play_until_end`).
**Polling REUSES** the LG-01d `play_status` view + `_build_play_status_response` +
`_celery_state_to_job_status` **verbatim** (same URL name, same 5-key JSON
`{status, completed, total, error, season_id}`) — the playoff task's stage-count
return is read unchanged from `async_result.info` / `.result`. **No change** to
`play_status` or `_build_play_status_response`.

**URL routes (`matches/season_urls.py`).** Two new bare-named entries —
`play_single_round` (`<int:season_id>/play-single-round/`) and `play_playoffs`
(`<int:season_id>/play-playoffs/`) — inserted **after** the LG-01d play routes and
**before** the `standings/` / `schedule/` entries (first-match resolution).
`play_status` is **reused** for the playoff job — no new status route.

**Compose-time guard (`matches/phase_composer.py`).** `parse_phase_composition`
gains ONE new rule: a `tournament` phase requires a **preceding** `round_robin`
phase. It fires **after** the existing zero-RR check (once all tokens are
known-valid and ≥ 1 RR exists, walk the specs in order and raise if any
`tournament` spec precedes the first `round_robin` spec). New `ValueError` string
(LOCKED, byte-equal): **`"a tournament phase requires a preceding round-robin
phase"`**. Pure `ValueError` (the module stays Django-free — frozen
`dataclasses` / `typing` allowlist unchanged, `TestNoDjangoImportsLeaked` still
passes); the form layer re-wraps as a `forms.ValidationError` attached to
`phases`. `member_night` remains rejected at the unknown-type step.

**Dashboard context + templates.** `_build_dashboard_context` (rendered by both
the League and Season dashboards) grows by **four playoff-cursor keys**, computed
from `displayed_season.current_phase()`: **`playoff_phase_active`** (`bool` — `True`
iff `current_phase()` is a tournament phase that is built **+ active**
(`tournament_id is not None AND tournament.state == "active"`));
**`playoff_tournament_id`** (`int | None` — `phase.tournament_id` when a tournament
phase is built, active **or** completed); **`playoff_completed`** (`bool` — `True`
iff a tournament phase exists, is built, and `tournament.state == "completed"`);
**`has_following_tournament_phase`** (`bool` — `True` iff the phase list contains a
`tournament` phase at an ordinal **after** the current RR phase). The existing
`action_button_state` / `action_button_label` keys are UNCHANGED in name; the
playoff buttons are a **separate group**, not a replacement for the action-button
slot. **NEW DOM ids** (both surfaces; they stack underneath the LG-01c/d
action-button + play-dropdown slot): the Play Single Round form / submit
(`{season,league}-dashboard-play-single-round-form` / `-submit`), the Play
Playoffs form / submit / progress
(`{season,league}-dashboard-play-playoffs-form` / `-submit` / `-progress`), and the
**View bracket** link (`{season,league}-dashboard-view-bracket-link`). Both playoff
buttons render **only when `playoff_phase_active`**; the View-bracket link renders
**whenever `playoff_tournament_id is not None`** (built tournament phase, active OR
completed) with `href = {% url 'tournament_detail' playoff_tournament_id %}` (the
existing standalone bracket page — **do NOT embed the bracket**, decision #9). The
forms POST to `play_single_round` / `play_playoffs`. **Conditional terminal-label
relabel:** the LG-01d terminal play-dropdown button labeled **"Until End of
Season"** is relabeled to **"Until Playoffs"** **iff `has_following_tournament_phase`**
— the form action / behaviour (`play_until_end`) and the button DOM id
(`{season,league}-dashboard-play-until-end`) are UNCHANGED, only the visible label
text swaps. The Play Playoffs form's **inline poll JS** intercepts submit,
fetch-POSTs, reads the 202 `{job_id, season_id}`, then polls `play_status` on a
1000 ms interval updating `-play-playoffs-progress` and reloading on
`status === "complete"` (mirrors the LG-01d `play_two_months` / `play_until_end`
inline JS verbatim — duplicated per template, no shared partial); Play Single Round
submits synchronously (server-side 302). **Poll UX (shared with the LG-01d play
dropdown):** the poll interval is **500 ms** and every progress block (the LG-01d
`-play-progress` and the Part2c-1 `-play-playoffs-progress`, on both dashboards)
carries a Bootstrap `spinner-border` (class `play-progress-spinner` /
`playoffs-progress-spinner`) shown alongside the live "Running… X / Y" counter so
the run reads as alive between per-round counter ticks; the spinner is hidden in
`clearPolling()` (on `complete` / `error`) and on an enqueue failure, and re-shown
by `showProgress()` on a retry.

**Tournament Matches stay `season=NULL` (decision #3).** The tournament engine
(`play_next_node`, `simulate_match(match_type="tournament")`) is consumed
VERBATIM — playoff Matches never get a `season` FK, so the playoff is invisible to
season-scoped history. There is **NO `Match.season_phase` FK, NO Match migration,
NO re-baseline, NO migration of any kind** this slice (the FK + season-linked
playoff Match history are deferred to **Part2c-2** alongside multi-RR). Because
`_final_standings_for_phase` filters `Match.objects.filter(season=self,
is_completed=True)`, tournament Matches never pollute the RR standings query.

**Determinism.** RR sims are unchanged (byte-identical). Tournament sims are
**non-deterministic** (`simulate_match` draws fresh per-round seeds), so tests
assert on `state` / `champion_team` id / bracket-node winners — **NEVER** on exact
simulated point totals. **No SIM-07 / SIM-08 interaction, NO Score Calibration
re-baseline** (extend ADR-0023, no new ADR).

**Scope-out (DEFERRED to Part2c-2 — DO NOT build here).** Multi-RR play loop +
`Match.season_phase` FK + cross-phase matchday offsetting; per-phase
`schedule_format` chokepoint wiring; the per-phase seeding-mode field + mid-season
tournaments; per-tournament-block config (format / top-N cut); non-single-elim
embeds (double-elim / RR / Swiss / RR→DE as a finals stage); season-linked playoff
Match history; weekly playoff pacing.

**Tests:** `matches/tests/test_phase_composer.py` (EXTEND — `"tournament,
round_robin"` raises the new `ValueError`; `"round_robin,tournament"` parses to 2
ordered specs; the zero-RR string still fires first; purity still passes).
`matches/tests/test_season_phase.py` / a new `matches/tests/test_season_playoff.py`
(cursor/completion derivation; auto-build seeds by standings rank + is idempotent;
`complete_if_finished` champion = tournament champion for a drained playoff and
`compute_standings(...)[0]` byte-identical for a single-RR-phase Season;
`play_playoffs_task` drains to champion under `CELERY_TASK_ALWAYS_EAGER`; the two
views' status codes (302 / dashboard-error / 405 and 202 / 409 / 405); the
dashboard context keys per cursor sub-state + the conditional terminal label) —
**never** on exact simulated point totals.

**Locked names (quick index):**
- **`Season` methods (`matches/models.py`):** `current_phase(self) ->
  SeasonPhase | None`; `_phase_complete(self, phase) -> bool`;
  `_preceding_phase(self, phase) -> SeasonPhase | None`;
  `activate_pending_tournament_phase(self) -> None` (`@transaction.atomic`,
  idempotent); `_final_standings_for_phase(self, phase) -> list[StandingsRow]`;
  `complete_if_finished(self) -> None` (REWRITTEN, `@transaction.atomic`);
  `_stamp_champion_for_final_phase(self, final_phase) -> None` (replaces the
  removed `_stamp_champion`).
- **Seed mapping:** `TournamentParticipant.seed = StandingsRow.rank` (rank 1 →
  seed 1). **Tournament create:** `format="single_elimination"`,
  `team_assembly="preset"`, `state="setup"`, `name=f"{season.name} Playoffs"`;
  then `tournament.lock_and_build()`.
- **Phase-completion rule:** RR ⇔ `_is_finished()`; tournament ⇔
  `phase.tournament_id is not None AND phase.tournament.state == "completed"` —
  in `Season._phase_complete`.
- **Hook wiring (`matches/simulation/entrypoints.py`):** `simulate_scheduled_round`
  calls `activate_pending_tournament_phase()` then `complete_if_finished()` after
  persistence in BOTH Round branches.
- **Celery task (`matches/tasks.py`):** `play_playoffs_task`,
  `@shared_task(bind=True, name="matches.play_playoffs")`, `(self, season_id:
  int) -> dict`, returns `{"completed": int, "total": int}` (stage counts from
  `matches.bracket.stage_progress`).
- **Views (`matches/league_views.py`):** `play_single_round(request, season_id)
  -> HttpResponse` (sync POST, 302 / dashboard-error / 405);
  `play_playoffs(request, season_id) -> JsonResponse` (async POST, 202 / 409 /
  405). **Reused:** `play_status` + `_build_play_status_response` +
  `_celery_state_to_job_status`.
- **URL names (`matches/season_urls.py`):** `play_single_round`
  (`<int:season_id>/play-single-round/`), `play_playoffs`
  (`<int:season_id>/play-playoffs/`), before `standings/` / `schedule/`.
- **Compose guard `ValueError` (`matches/phase_composer.py`):** `"a tournament
  phase requires a preceding round-robin phase"`, after the zero-RR check.
- **Dashboard context keys:** `playoff_phase_active`, `playoff_tournament_id`,
  `playoff_completed`, `has_following_tournament_phase`.
- **DOM ids (season / league):**
  `{season,league}-dashboard-play-single-round-form` / `-submit`;
  `{season,league}-dashboard-play-playoffs-form` / `-submit` / `-progress`;
  `{season,league}-dashboard-view-bracket-link`. Terminal label "Until Playoffs"
  iff `has_following_tournament_phase`. View-bracket link → `{% url
  'tournament_detail' playoff_tournament_id %}`.
- **ADR:** extend [ADR-0023](../../docs/adr/0023-season-phase-composable-structure.md)
  (no new ADR). **No `Match.season_phase` FK, no Match migration, no
  simulator/engine change, no re-baseline.**

See the seam contract
[`.claude/worktrees/lg-02-part2c-1-seam-contract.md`](../../.claude/worktrees/lg-02-part2c-1-seam-contract.md)
for the authoritative names + behaviours.

## LG-02-Part2c-2 multi-round-robin season

The next slice of LG-02-Part2c — a **thin orchestration layer** that generalises
the Part2c-1 single-RR-then-single-elim path into a **multi-round-robin** season.
It adds a `Match.season_phase` FK, a per-phase find-or-create key, per-phase RR
completion, a multi-RR play loop, and **cross-phase global-continuous matchday
offsetting**. The supported + tested composition is **one-or-more `round_robin`
phases then an OPTIONAL trailing `tournament`** (RR1→RR2, RR1→RR2→playoff). The
tournament engine (`simulate_match(match_type="tournament")` / `play_next_node`)
and the Part2c-1 auto-build / cursor / completion machinery are consumed
**VERBATIM**; legacy phase-less and single-RR seasons stay **byte-identical**.
**No simulator mechanics change, no tournament-engine change, no composer/form/
template change, no Score Calibration re-baseline, no SIM-07/08 interaction.** The
[ADR-0023](../../docs/adr/0023-season-phase-composable-structure.md)
ordered-typed-phase decision (extended with a "Part2c-2 consequences" addendum)
and the **Matchday** / **Season phase** CONTEXT.md glossary touch-ups carry the
domain language. Seam contract:
[`.claude/worktrees/lg-02-part2c-2-seam-contract.md`](../../.claude/worktrees/lg-02-part2c-2-seam-contract.md).

**`Match.season_phase` FK + migration `0043` (`matches/models.py`).** A new
optional FK on `Match` mirrors the existing `Match.season` SET_NULL block:
`season_phase = models.ForeignKey("matches.SeasonPhase", null=True, blank=True,
on_delete=models.SET_NULL, related_name="matches")`, declared immediately after
`Match.season`. **RR Matches now carry BOTH `season=<season>` AND
`season_phase=<rr phase>`**; tournament/playoff Matches stay `season=NULL,
season_phase=NULL` (engine consumed verbatim) and legacy phase-less seasons
(implicit `pk is None` phase) keep `season_phase=NULL` — byte-identical. The
reverse accessor `SeasonPhase.matches` reuses the same `related_name` label
`Season.matches` uses, but the owning models differ (`Season` vs `SeasonPhase`)
so the two reverse accessors don't collide. Migration
`matches/migrations/0043_match_season_phase.py` (dep
`0042_seasonphase_format_tournament`) is a **single `AddField`** — **NO
`RunPython`, NO backfill, NO data migration** (the [ADR-0004](../../docs/adr/0004-simulation-data-is-disposable.md)
disposable-data posture, same as `0029` / `0041` / `0042`). The FK is the
load-bearing reason this slice exists: it makes the find-or-create key
phase-aware so identical pairings in different RR phases are DISTINCT Matches.

**By-phase fixture seam + global-continuous matchday offset (`Season`, in
`matches/models.py`).** A NEW `Season.scheduled_fixtures_by_phase(self) ->
list[tuple[SeasonPhase, list[ScheduleFixture]]]` returns one `(phase, fixtures)`
tuple per `round_robin` phase in ordinal order (tournament phases contribute no
fixtures — they drain via the bracket, not `generate_schedule`). **Phase k's
fixtures are offset by the SUM of all prior RR phases' matchday spans** (a phase's
un-offset span is `max(f.matchday for f in <base>)`), so the whole season is one
monotonic 1..N matchday calendar and the `date = start_date + (matchday-1)*7`
derivation keeps working. Each offset fixture is a NEW `ScheduleFixture` with
`matchday = original + offset` (round_number / team ids unchanged); fixtures come
from `generate_schedule(team_ids, phase.schedule_format or self.schedule_format)`
(both currently resolve to `single_round_robin`, so output is byte-identical for
the single-RR case). `Season.scheduled_fixtures(self) -> list[ScheduleFixture]` is
**REWRITTEN to be the flat concatenation** of every phase's offset fixtures —
preserving its return type, its `[]`-on-`<2`-teams guard, and byte-identical
output for a single-RR (or phase-less) Season (one phase, offset 0) so every flat
caller (`_is_finished`, `season_schedule`, `_build_dashboard_context`,
`league_history`, `team_schedule`) is unchanged. A new private
`Season._scheduled_team_ids(self) -> list[int]` extracts the draft-vs-snapshot
`team_ids` rule (no behaviour change) so the by-phase seam and per-phase
completion read one source.

**Per-phase RR completion (`Season`, in `matches/models.py`).**
`Season._phase_complete(self, phase)` is **REWRITTEN** so the `round_robin` branch
is **per-phase EXCEPT the implicit fallback**: `phase.pk is None` ⇒ the
whole-season `_is_finished()` (byte-identical legacy path); a persisted RR phase ⇒
the NEW private `Season._rr_phase_complete(self, phase) -> bool`. The tournament
branch and trailing `return False` are unchanged. `_rr_phase_complete` is the
per-phase analogue of `_is_finished`: it reads THIS phase's offset fixtures (via
the NEW private `Season._fixtures_for_phase(self, phase) -> list[ScheduleFixture]`,
the matching entry from `scheduled_fixtures_by_phase()`) and scopes the
played-rounds query by **`GameRound.objects.filter(match__season_phase=phase)`**,
comparing against the same Side-agnostic `(frozenset({team ids}), round_number)`
key `_is_finished` uses (the offset only changes `matchday`, which the key does not
read). This makes the cursor finish RR1 before RR2 opens — and because
`current_phase()` already walks `ordered_phases()` calling `_phase_complete` per
phase, **no `current_phase` edit is needed**. `_final_standings_for_phase` stays
**whole-season** (`Match.objects.filter(season=self, is_completed=True)`) so
standings are **cumulative across all RR phases** (a trailing playoff seeds from
the cumulative leader; an RR-final-phase champion = cumulative leader).

**Phase-aware find-or-create key (`matches/simulation/entrypoints.py`).**
`simulate_scheduled_round` gains a **keyword-only `season_phase=None`** appended
after `arena_map=None` (`def simulate_scheduled_round(self, season, team_a,
team_b, round_number, *, arena_map=None, season_phase=None) -> GameRound`).
**Default `None` = legacy/sandbox unchanged.** The Side-agnostic find-or-create
key becomes **`(season, season_phase, frozenset({team_a_id, team_b_id}))`** — both
lookup queries filter `season=season, season_phase=season_phase`, and the Round-1
create stamps `season_phase=season_phase`. So identical pairings in DIFFERENT RR
phases become DISTINCT Matches; `season_phase=None` keeps the key
`(season, NULL, frozenset)`, byte-identical to today's `(season, frozenset)`. The
two post-round hooks (`activate_pending_tournament_phase()` then
`complete_if_finished()`) are **UNCHANGED** — `complete_if_finished` already gates
on the final phase, and `_phase_complete` now resolves per-phase RR, so the season
completes only when the truly-final phase finishes. **No new RNG draw** — only the
Match the round attaches to changes.

**Phase-aware pure helpers (`matches/season_dashboard.py`).** The module stays
**Django-free** (frozen `collections`/`dataclasses`/`typing` allowlist;
`TestNoDjangoImportsLeaked` must keep passing). `select_play_fixtures` and
`find_next_matchday` become phase-aware via **PLAIN INT phase-ids** — no Django,
no `SeasonPhase` import. Their `fixtures` arg is now a
`list[tuple[int | None, ScheduleFixture]]` (each `(phase_id, fixture)`) and
`played_keys` is `set[tuple[int | None, frozenset[int], int]]`; the per-fixture
key built inside the sweep becomes the 3-tuple `(phase_id, frozenset({team ids}),
round_number)`. Because the play loop feeds OFFSET fixtures, the existing "next
`max_matchdays` distinct matchdays" sweep selects a contiguous GLOBAL window that
naturally spans the RR1→RR2 boundary (`max_matchdays is None` ⇒ all unplayed
pairs). **`find_next_fixture` / `round_progress` / `compute_leaders` / `LeaderRow`
stay UNCHANGED on the FLAT 2-tuple key shape** `(frozenset, round_number)` — they
serve the dashboard's whole-season next/progress display over the flat
`scheduled_fixtures()` + flat `played_keys` (already global-continuous via the
offset), so no phase-aware variant is needed for them. The seam split:
play-loop helpers attribute each Round to its owning phase (3-tuple); dashboard
helpers need only whole-season next/progress (2-tuple); both shapes are plain-int
/ frozenset / dataclass, so the module stays Django-free.

**Play-loop wiring (`matches/tasks.py::play_season_task`,
`matches/league_views.py::play_week`).** Both sites are rewritten to iterate
**by phase**: build `by_phase = season.scheduled_fixtures_by_phase()`, a
`phase_by_id = {phase.id: phase for phase, _ in by_phase}` map, a flat
`fixtures = [(phase.id, fixture) for phase, pf in by_phase for fixture in pf]`
list, and a phase-aware `played_keys = {(gr.match.season_phase_id,
frozenset({gr.match.team_red_id, gr.match.team_blue_id}), gr.round_number) for gr
in GameRound.objects.filter(match__season=season).select_related("match")}`. The
per-fixture loop unpacks `(phase_id, fixture)` and passes
`season_phase=phase_by_id.get(phase_id)` into `simulate_scheduled_round`. For a
phase-less season `scheduled_fixtures_by_phase()` yields one tuple whose
`phase.id is None`, so `phase_by_id.get(None)` → `None` → `season_phase=None`
flows through, preserving legacy `season_phase=NULL` Matches. The
`_resolve_fixture_map` / `in_bulk` / progress / `close_old_connections`
machinery, the 405 guard, `last_league_id` write, and the success/error responses
are otherwise unchanged. **`play_two_months` / `play_until_end` are UNCHANGED** —
they enqueue `play_season_task`; the `max_matchdays=8 | None` window carries
through `select_play_fixtures` unchanged over the global-continuous matchdays.

**Scope-out (DEFERRED to Part2c-3 — DO NOT build here).** Per-phase
`schedule_format` wiring beyond the read + the first alternative regular-season
format; the per-phase seeding-mode field + mid-season tournaments; per-tournament-
block config (format / top-N cut); non-single-elim embeds (double-elim / RR /
Swiss / RR→DE as a finals stage); a season-linked playoff Match-history surface;
weekly playoff pacing. `_final_standings_for_phase` stays whole-season (cumulative
standings are the intended behaviour, not a deferral).

**Determinism.** RR sims are byte-identical per Round (no mechanics change).
Tournament sims stay **non-deterministic** — tests assert on `state` /
`champion_team` id / bracket-node winners and on Match counts / `season_phase_id`
attribution, **NEVER** on exact simulated point totals. **No SIM-07 / SIM-08
interaction, NO Score Calibration re-baseline** (extend ADR-0023, no new ADR).

**Locked names (quick index):**
- **Model + migration (`matches/models.py`):** `Match.season_phase =
  models.ForeignKey("matches.SeasonPhase", null=True, blank=True,
  on_delete=models.SET_NULL, related_name="matches")`; reverse accessor
  `SeasonPhase.matches`; migration
  `matches/migrations/0043_match_season_phase.py` (dep
  `0042_seasonphase_format_tournament`, single `AddField`, NO `RunPython`).
- **`Season` methods (`matches/models.py`):**
  `scheduled_fixtures_by_phase(self) -> list[tuple[SeasonPhase,
  list[ScheduleFixture]]]` (NEW — per-phase offset fixtures);
  `scheduled_fixtures(self) -> list[ScheduleFixture]` (REWRITTEN — flat
  concatenation of by-phase offset fixtures);
  `_phase_complete(self, phase) -> bool` (REWRITTEN — RR branch per-phase via
  `_rr_phase_complete`, `pk is None` ⇒ `_is_finished()`);
  `_rr_phase_complete(self, phase) -> bool` (NEW — per-phase RR, scoped
  `match__season_phase=phase`);
  `_fixtures_for_phase(self, phase) -> list[ScheduleFixture]` (NEW private — THIS
  phase's offset fixtures);
  `_scheduled_team_ids(self) -> list[int]` (NEW private — extracted
  draft-vs-snapshot rule). UNCHANGED: `current_phase`, `_preceding_phase`,
  `_final_standings_for_phase` (whole-season — cumulative standings),
  `activate_pending_tournament_phase`, `_stamp_champion_for_final_phase`,
  `complete_if_finished`, `_is_finished`, `ordered_phases`, `_implicit_phase`,
  `start_season`.
- **Matchday offset:** phase k offset = sum of prior RR phases' matchday spans;
  per-phase span = `max(f.matchday for f in <un-offset base>)`; result is one
  monotonic 1..N calendar; `date = start_date + (matchday-1)*7` unchanged.
- **Find-or-create key (`matches/simulation/entrypoints.py`):**
  `(season, season_phase, frozenset({team_a_id, team_b_id}))`; signature gains
  keyword-only `season_phase=None`
  (`simulate_scheduled_round(self, season, team_a, team_b, round_number, *,
  arena_map=None, season_phase=None) -> GameRound`); post-round hooks
  (`activate_pending_tournament_phase()` then `complete_if_finished()`) UNCHANGED.
- **Pure helpers (`matches/season_dashboard.py`) — phase-aware, Django-free:**
  `select_play_fixtures(fixtures, played_keys, max_matchdays)` and
  `find_next_matchday(fixtures, played_keys)` where `fixtures: list[tuple[int |
  None, ScheduleFixture]]`, `played_keys: set[tuple[int | None, frozenset[int],
  int]]`. UNCHANGED on the FLAT 2-tuple shape: `find_next_fixture`,
  `round_progress`, `compute_leaders`, `LeaderRow`. `TestNoDjangoImportsLeaked`
  must keep passing.
- **played_keys shape (play loop):** `(season_phase_id, frozenset({team_red_id,
  team_blue_id}), round_number)` from `match.season_phase_id` (plain `int |
  None`). FLAT dashboard `played_keys` stays the 2-tuple `(frozenset,
  round_number)`.
- **Play-loop sites:** `matches/tasks.py::play_season_task` and
  `matches/league_views.py::play_week` — both build `by_phase` / `phase_by_id` /
  flat `[(phase.id, fixture)]` / phase-aware `played_keys`, call
  `select_play_fixtures(...)`, pass `season_phase=phase_by_id.get(phase_id)`.
  `play_two_months` / `play_until_end` UNCHANGED.
- **Composer / form / template:** UNCHANGED (`parse_phase_composition` already
  permits multiple `round_robin` tokens, ≥1 RR, no cap; the Part2c-1
  tournament-must-follow-RR guard stays).
- **ADR:** extend [ADR-0023](../../docs/adr/0023-season-phase-composable-structure.md)
  with a "Part2c-2 consequences" addendum (no new ADR). **No new CONTEXT.md
  domain term, no simulator/engine change, no re-baseline.**

See the seam contract
[`.claude/worktrees/lg-02-part2c-2-seam-contract.md`](../../.claude/worktrees/lg-02-part2c-2-seam-contract.md)
for the authoritative names + behaviours.

## LG-02-Part2c-3a double round-robin regular-season format

The first sub-slice of the re-sliced LG-02-Part2c-3 — a **thin orchestration
layer** that lands the **first alternative regular-season `schedule_format`**,
**`double_round_robin`**, as a single `SeasonPhase` format, wiring the Part2b
**dormant per-phase `schedule_format` column end-to-end** for the first time. A
`double_round_robin` phase has every enrolled pair meet **twice within one phase**
as **two distinct Matches**, discriminated by a NEW `Match.leg` field;
`generate_schedule` gains the format, `simulate_scheduled_round` gains a `leg`
find-or-create dimension, and `leg` threads through per-phase RR completion / the
play loops / the Django-free pure helpers / the FLAT dashboard overlays. The
simulator, RNG, and tournament engine are consumed **VERBATIM**; legacy
phase-less, single-RR, and tournament/playoff Matches stay **`leg=1` ⇒
byte-identical**. **No simulator mechanics change, no RNG change, no
tournament-engine change, no Score Calibration re-baseline.** The
[ADR-0023](../../docs/adr/0023-season-phase-composable-structure.md) decision
(extended with a "Part2c-3a consequences" addendum, no new ADR) and the
**Matchday** / **Season phase** CONTEXT.md glossary touch-ups carry the domain
language (`double_round_robin` is a `schedule_format` value, not a new domain
term). Seam contract:
[`.claude/worktrees/lg-02-part2c-3a-seam-contract.md`](../../.claude/worktrees/lg-02-part2c-3a-seam-contract.md).

**`Match.leg` field + migration `0044` (`matches/models.py`).** A new
`leg = models.PositiveSmallIntegerField(default=1)` on `Match`, declared
immediately after `Match.season_phase` (no `db_index`, no choices, no
`null`/`blank`). It **discriminates the two legs of a `double_round_robin`
pairing**; `single_round_robin`, legacy phase-less, and tournament/playoff Matches
stay `leg=1` (the default) ⇒ byte-identical. Migration
`matches/migrations/0044_match_leg.py` (dep `0043_match_season_phase`) is a
**single `AddField`** — **NO `RunPython`, NO backfill, NO data migration** (the
[ADR-0004](../../docs/adr/0004-simulation-data-is-disposable.md) disposable-data
posture, same as `0029` / `0041` / `0042` / `0043`; existing rows take
`default=1`). `leg` is the load-bearing reason this slice exists: it makes the
find-or-create key distinguish the same pairing's two legs as DISTINCT Matches.

**Schedule generation (`matches/schedule_generator.py`).** The module stays
**Django-free** (frozen `dataclasses` / `typing` allowlist; no new import).
`ScheduleFixture` gains a trailing **`leg: int = 1`** (appended LAST, default `1`,
constructed by keyword everywhere) ⇒ a no-`leg` construction is equality-identical
to every existing test construction and to the Part2c-2 offset re-construction
once it carries `leg=f.leg`. `SCHEDULE_FORMATS = ("single_round_robin",
"double_round_robin")`. `generate_schedule(team_ids, "double_round_robin")`
returns the `single_round_robin` fixture list for `team_ids` (every fixture
`leg=1`, the existing circle-method output — round_number 1 on matchdays
`1..n-1`, round_number 2 on `n..2*(n-1)`) **CONCATENATED** with the **same**
fixtures re-emitted with **`leg=2`** and matchday **offset by `2*(n-1)`** (same
`round_number` / `team_a_id` / `team_b_id`), then final-**sorted by
`(matchday, team_a_id)`** (the same key the single-RR path uses; leg is implied by
matchday since the two legs occupy disjoint contiguous matchday ranges). So leg 2
plays **sequentially after** leg 1 on one monotonic `1..4*(n-1)` matchday calendar
within the phase (`n` = the existing bye-padded even slot count `len(slots)`, so
the offset is the existing `2 * (n - 1)`). The `single_round_robin` path is
**byte-identical** (its fixtures already carry the `leg=1` default); the existing
`ValueError` on `schedule_format not in SCHEDULE_FORMATS` and on
`len(team_ids) < 2` is unchanged.

**Phase-aware find-or-create key (`matches/simulation/entrypoints.py`).**
`simulate_scheduled_round` gains a **keyword-only `leg: int = 1`** appended after
`season_phase=None` (`def simulate_scheduled_round(self, season, team_a, team_b,
round_number, *, arena_map=None, season_phase=None, leg: int = 1) -> GameRound`).
**Default `1` = byte-identical to every existing caller** (sandbox, season play,
tests passing no `leg`). The Side-agnostic find-or-create key becomes
**`(season, season_phase, frozenset({team_a_id, team_b_id}), leg)`** — both
`.filter(...)` lookups gain `leg=leg` and the Round-1 `Match.objects.create(...)`
stamps `leg=leg`. So the two legs of a pairing become DISTINCT Matches; `leg=1`
collapses the key to `(season, season_phase, frozenset, 1)` — byte-identical to
the Part2c-2 `(season, season_phase, frozenset)` plus a constant. The
`season_phase.pk is None ⇒ None` coercion, the round_number guards, the per-round
colour swap, the post-round hooks (`activate_pending_tournament_phase()` **then**
`complete_if_finished()`), and the RNG draw are **UNCHANGED**.

**Leg threading on `Season` (`matches/models.py`).** **`_is_finished`**
(whole-season RR check, legacy/implicit-phase path) — its played-keys set gains
`leg` from `gr.match.leg` (`(frozenset({team ids}), round_number, leg)`) and the
per-fixture compare key gains `fixture.leg` (for a phase-less / single-RR Season
every `leg == 1` ⇒ byte-identical). **`_rr_phase_complete`** (per-phase RR
completion, `match__season_phase=phase` scoped) — same change: played-keys become
`(frozenset({team ids}), round_number, gr.match.leg)` and the fixture compare key
`(frozenset({fixture.team_a_id, fixture.team_b_id}), fixture.round_number,
fixture.leg)` — this is what makes a double-RR phase require **both** legs of every
pairing before the phase completes. **`_final_standings_for_phase` is UNCHANGED** —
standings stay cumulative whole-season; a double-RR pairing is two distinct Matches,
each a row in `Match.objects.filter(season=self, is_completed=True)`, so both legs
count automatically. `scheduled_fixtures_by_phase` / `_fixtures_for_phase` /
`scheduled_fixtures` need no leg-specific edit **beyond carrying `leg=f.leg`
through the offset re-construction** (else leg-2 fixtures would collapse to leg-1
and break the key + completion).

**Leg on the Django-free pure helpers (`matches/season_dashboard.py`).** The
module stays **Django-free** (`leg` is a plain int read off the duck-typed fixture;
no new import; `TestNoDjangoImportsLeaked` must keep passing). The play-loop
helpers `select_play_fixtures` / `find_next_matchday` gain `leg` in their
per-fixture key: **OLD** `(phase_id, frozenset({team ids}), round_number)` →
**NEW** `(phase_id, frozenset({team ids}), round_number, leg)` (`leg = fixture.leg`);
`played_keys` becomes `set[tuple[int | None, frozenset[int], int, int]]`, the
`fixtures` arg shape `list[tuple[int | None, ScheduleFixture]]` is UNCHANGED (the
`ScheduleFixture` now carries `.leg`). The FLAT dashboard helpers `find_next_fixture`
/ `round_progress` gain `leg` in their 2-tuple key: **OLD**
`(frozenset({team ids}), round_number)` → **NEW** `(frozenset({team ids}),
round_number, leg)` — **REQUIRED** because a double-RR phase now holds the same
`(pair, round_number)` **twice** in the flat list, so without `leg` the second leg
would be treated as already-played. `compute_leaders` / `LeaderRow` UNCHANGED. (See
the §3 key-tuple table in the seam contract for all OLD → NEW shapes.)

**Play-loop wiring + FLAT overlay sites (`matches/tasks.py`,
`matches/league_views.py`).** **`play_season_task`** (`tasks.py`) and **`play_week`**
(`league_views.py`) build leg-bearing `played_keys` entries — `(gr.match.season_phase_id,
frozenset({gr.match.team_red_id, gr.match.team_blue_id}), gr.round_number,
gr.match.leg)` — and pass **`leg=fixture.leg`** into `simulate_scheduled_round`;
`play_two_months` / `play_until_end` are **UNCHANGED** (they enqueue
`play_season_task`; the `max_matchdays` window carries through). The three FLAT
2-tuple overlay sites that feed `find_next_fixture` / `round_progress` each gain
`leg` (reading `match.leg` off the `GameRound.match` already in scope /
`select_related("match")`): **`_build_dashboard_context`** (`played_keys` entries
+ fixture compare key), **`season_schedule`** (`played_by_key` dict key + per-fixture
lookup), **`team_schedule`** (`played_keys` set + `fixture_by_key` dict +
per-fixture/per-round lookup). Each becomes `(..., round_number, leg)` so a
double-RR phase's two legs are distinct.

**Composer (`matches/phase_composer.py`) — per-token `type[:format]` wire format.**
The wire format **extends** from comma-separated phase-**TYPE** tokens to
comma-separated **`type[:format]`** tokens, e.g.
`"round_robin:double_round_robin,tournament"`. A **bare `round_robin`** token (no
colon) defaults to **`single_round_robin`** (Part2b backward-compat — existing
serialized values parse identically); a **`tournament`** token carries **no format**
⇒ `PhaseSpec.schedule_format=None` (a `tournament:anything` token is malformed).
`parse_phase_composition` splits each non-empty token on the **first** `:` only
into `(type_part, format_part)`, rejects an empty `type_part` (malformed),
validates `type_part` against `{round_robin, tournament}` (unknown-type
`ValueError`), and for `round_robin` resolves `schedule_format = format_part or
"single_round_robin"` then validates it against the valid set
`{"single_round_robin", "double_round_robin"}`, raising a **NEW pure
`ValueError(f"unknown schedule_format: {fmt!r}")`** (LOCKED string; module stays
Django-free; the form re-wraps as a `forms.ValidationError` on `phases`). All
existing `ValueError` strings are **preserved verbatim** (`"malformed phase
composition"`, `f"unknown phase type: {token!r}"`, `"composition must contain at
least one round-robin phase"`, `"a tournament phase requires a preceding
round-robin phase"`); the `PhaseSpec` shape (`ordinal, phase_type,
schedule_format`) is **UNCHANGED**. The form's `clean()` call site is unchanged
(per-token format wins; `season_schedule_format` stays the fallback for a bare
`round_robin` token, semantics still "bare ⇒ single_round_robin"). **Template
(`templates/leagues/create.html`):** the per-phase `<select>` gains a
`double_round_robin` option and `serialize()` emits each RR row as
`round_robin:<format>` (a `tournament` row emits the bare token); **all Part2b DOM
ids are UNCHANGED** (`league-create-phases-composer`, `league-create-add-block`,
`league-create-phases`, `league-create-phase-row-{i}`,
`league-create-phase-type-{i}`, `league-create-phase-format-{i}`,
`league-create-member-night-note`, class substring `phase-tournament-pending`).

**`next_season` carry-forward — NO-OP.** `next_season`
(`matches/league_views.py`) already copies each source phase's `schedule_format`
**verbatim** into the new draft Season's phases (the Part2b carry-forward loop), so
a `double_round_robin` phase reproduces automatically with **no edit**. Admin is
also unchanged (`SeasonPhaseAdmin.list_display` already includes `schedule_format`;
`Match.leg` auto-appears on the default change form, not load-bearing).

**Backward-compat invariants ("stays byte-identical").** `single_round_robin` —
every fixture `leg=1`, `generate_schedule(..., "single_round_robin")` output
identical to today. Legacy / phase-less Season — find-or-create key
`(season, None, frozenset, 1)` collapses to today's `(season, None, frozenset)`
plus a constant; `_is_finished` played-keys all `leg=1`. Tournament / playoff
Matches — `simulate_match` never sets `leg` ⇒ default `1`; `season=NULL,
season_phase=NULL` unchanged. Bare `round_robin` wire token ⇒ `single_round_robin`.
`ScheduleFixture(...)` without `leg` ⇒ `leg == 1`.

**Determinism.** RR sims are byte-identical per Round (no mechanics change). Tests
assert schema-level outcomes — Match counts, `leg` values, completion flags,
standings ORDER, the generated fixture list (count / matchday spans / leg values /
sort) — **NEVER** raw simulated point totals. **No re-baseline / extend ADR-0023**
(no new ADR).

**Locked names (quick index):**
- **Model + migration (`matches/models.py`):** `Match.leg =
  models.PositiveSmallIntegerField(default=1)` (after `Match.season_phase`);
  migration `matches/migrations/0044_match_leg.py` (dep `0043_match_season_phase`,
  single `AddField`, NO `RunPython`).
- **Schedule generator (`matches/schedule_generator.py`):** `ScheduleFixture.leg:
  int = 1` (trailing); `SCHEDULE_FORMATS = ("single_round_robin",
  "double_round_robin")`; `generate_schedule(team_ids, "double_round_robin")` =
  leg-1 list ⊕ leg-2 list offset by `2*(n-1)`, sorted `(matchday, team_a_id)`,
  monotonic `1..4*(n-1)`.
- **Find-or-create key (`matches/simulation/entrypoints.py`):**
  `(season, season_phase, frozenset({team_a_id, team_b_id}), leg)`; signature gains
  keyword-only `leg: int = 1` (`simulate_scheduled_round(self, season, team_a,
  team_b, round_number, *, arena_map=None, season_phase=None, leg: int = 1) ->
  GameRound`); post-round hooks UNCHANGED.
- **`Season` methods (`matches/models.py`):** `_is_finished` /
  `_rr_phase_complete` played-keys + fixture-compare keys gain `leg`
  (`gr.match.leg` / `fixture.leg`); `_final_standings_for_phase` UNCHANGED
  (cumulative); `scheduled_fixtures_by_phase` offset re-construction carries
  `leg=f.leg`.
- **Pure helpers (`matches/season_dashboard.py`) — Django-free:**
  `select_play_fixtures` / `find_next_matchday` key → 4-tuple `(phase_id,
  frozenset, round_number, leg)`, `played_keys: set[tuple[int | None,
  frozenset[int], int, int]]`; `find_next_fixture` / `round_progress` key →
  3-tuple `(frozenset, round_number, leg)`. UNCHANGED: `compute_leaders`,
  `LeaderRow`. `TestNoDjangoImportsLeaked` must keep passing.
- **Play-loop + FLAT overlay sites:** `tasks.play_season_task` /
  `league_views.play_week` (leg-bearing `played_keys`, `leg=fixture.leg`);
  `league_views._build_dashboard_context` / `season_schedule` / `team_schedule`
  (FLAT `played` keys gain `leg` from `gr.match.leg` / `fixture.leg`).
  `play_two_months` / `play_until_end` UNCHANGED.
- **Composer (`matches/phase_composer.py`):** per-token `type[:format]` wire
  format; bare `round_robin` ⇒ `single_round_robin`; `tournament` ⇒
  `schedule_format=None`; NEW `ValueError(f"unknown schedule_format: {fmt!r}")`;
  `PhaseSpec` shape unchanged. Template `templates/leagues/create.html` gains a
  `double_round_robin` option + `round_robin:<format>` serialize; **all Part2b DOM
  ids unchanged**.
- **No-op / no-change:** `next_season` (carries `schedule_format` forward
  verbatim), `SeasonPhaseAdmin`, `_final_standings_for_phase`, the simulator / RNG
  / tournament engine.
- **ADR:** extend [ADR-0023](../../docs/adr/0023-season-phase-composable-structure.md)
  with a "Part2c-3a consequences" addendum (no new ADR). **No new CONTEXT.md domain
  term** (`double_round_robin` is a `schedule_format` value), no re-baseline.

See the seam contract
[`.claude/worktrees/lg-02-part2c-3a-seam-contract.md`](../../.claude/worktrees/lg-02-part2c-3a-seam-contract.md)
for the authoritative names + behaviours.

## LG-02-Part2c-3b per-phase tournament_mode field

A **fully dormant** slice that adds the per-phase **`SeasonPhase.tournament_mode`**
field — the season-ending (`standings`) vs mid-season (`strength` / `unseeded` /
`random_draw`) flavour selector — and threads it through the compose/creation/
carry-forward seam **always as `"standings"` this slice**. No composer picker, no
wire-format mode token, no compose-guard relaxation, no build branch — all
deferred to Part2c-3c. No simulator / RNG change → **no Score Calibration
re-baseline**. Extends
[ADR-0023](../../docs/adr/0023-season-phase-composable-structure.md) (Part2c-3b
consequences addendum, no new ADR); the CONTEXT.md **Season phase** entry carries
the `tournament_mode` vocabulary (+ the stale Part2c-2 → Part2c-3b fix). Seam
contract:
[`.claude/worktrees/lg-02-part2c-3b-seam-contract.md`](../../.claude/worktrees/lg-02-part2c-3b-seam-contract.md).

**Model (`matches/models.py`, `SeasonPhase`).** Class attr
**`TOURNAMENT_MODE_CHOICES`** declares all four values now (the `member_night`
declared-but-inert precedent — only `standings` has build behaviour this slice):
`("standings", "Season-ending: from Standings")`, `("strength", "Mid-season: by
team strength")`, `("unseeded", "Mid-season: random seed")`, `("random_draw",
"Mid-season: drawn pool -> RR->DE")`. The field
**`tournament_mode = models.CharField(max_length=16, choices=TOURNAMENT_MODE_CHOICES,
default="standings")`** is declared immediately after the `tournament` FK — no
constraint, no `db_index`. Meaningful only for `tournament` phases (`round_robin`
phases carry the inert default). **`unseeded` ≠ `random_draw`**: `unseeded`
randomly seeds the season's *existing preset teams*; `random_draw` builds *fresh
balanced teams from a player pool* (reuses the LG-02x-1 `team_assembly="random_draw"`
+ `format="round_robin_double_elim"` machinery). **Only `standings` requires a
preceding `round_robin` phase** — already enforced for every `tournament` block by
the existing blanket `parse_phase_composition` preceding-RR guard (the compose-time
validity rule is UNCHANGED this slice).

**Migration `0045_seasonphase_tournament_mode`** (dep `0044_match_leg`): a single
`AddField`, **NO `RunPython` / NO backfill** ([ADR-0004](../../docs/adr/0004-simulation-data-is-disposable.md)
posture — existing standings-playoff phases inherit `default="standings"`).

**Pure module (`matches/phase_composer.py`).** `PhaseSpec` gains a trailing
**`tournament_mode: str = "standings"`** (appended LAST with a default ⇒ existing
keyword constructions stay equality-identical, the c-3a `ScheduleFixture.leg`
precedent). **Wire format UNCHANGED** — `tournament_mode` is **not** parsed from
the wire this slice; `parse_phase_composition` leaves every spec at the
`"standings"` default and a `tournament:<mode>` token still raises
`"malformed phase composition"` (the existing tournament-takes-no-format rule),
reserving the `:` syntax for the c-3c picker. The frozen import allowlist
(`dataclasses` / `typing`) + `TestNoDjangoImportsLeaked` are unchanged.

**Creation / carry-forward (`matches/league_views.py`).** Both
`SeasonPhase.objects.create(...)` loops stamp the field — `league_create` adds
`tournament_mode=spec.tournament_mode` and `next_season` adds
`tournament_mode=src.tournament_mode` (verbatim carry-forward, mirroring the
`schedule_format` copy) so a future non-`standings` mode (c-3c) reproduces across
seasons. Both edits sit inside the existing `@transaction.atomic` blocks.

**Admin (`matches/admin.py`).** `SeasonPhaseAdmin.list_display` appends
`"tournament_mode"`.

**UNCHANGED (→ c-3c).** `Season.activate_pending_tournament_phase` still hardcodes
standings-seeding (the default already matches, so byte-identical); the composer
template / wire format / guard; the read-path, simulator, RNG, and `Match` model.
**No re-baseline.**

**Tests.** `test_season_phase.py` (field default / choices / `max_length==16` /
all-four-persist), `test_phase_composer.py` (`PhaseSpec` default + parser stamps
`"standings"` + `tournament:<mode>` still malformed + purity), `test_league_create.py`
(every composed phase persists `"standings"`), `test_league_next_season.py`
(**hand-set a source phase's `tournament_mode="strength"` via ORM, assert the
carry-forward preserves it** — the load-bearing forward-compat guard for c-3c).

## LG-02-Part2c-3c mid-season tournaments

The slice that makes a `tournament` **Season phase** sitting **mid-season** (between
two `round_robin` phases, or **first**) actually BUILD — turning the c-3b dormant
`tournament_mode` field live for **`strength`** + **`unseeded`** (`random_draw` stays
**DEFERRED**, rejected by the parser + offered as a disabled "coming soon" composer
option). It relaxes the standings-only flavour of the Part2c-1 preceding-RR compose
guard, branches the build by seeding mode, fires the build at `start_season` for a
first-phase tournament, and adds a play-loop **barrier** so the RR loop halts at an
incomplete mid-season tournament and the bracket drains through the EXISTING
`play_single_round` / `play_playoffs` views before later RR phases play. **NO
migration** (`tournament_mode` exists from c-3b). The simulator / RNG / tournament
engine are consumed **VERBATIM**; the Django-free pure helpers + `scheduled_fixtures*`
+ `matches/season_dashboard.py` are **UNTOUCHED** (`TestNoDjangoImportsLeaked` stays
green) → **no Score Calibration re-baseline**. Extends
[ADR-0023](../../docs/adr/0023-season-phase-composable-structure.md) (Part2c-3c
consequences addendum, no new ADR); the CONTEXT.md **Season phase** + **Matchday**
glossary touch-ups carry the build-now / barrier-drain domain language. Seam contract:
[`.claude/worktrees/lg-02-part2c-3c-seam-contract.md`](../../.claude/worktrees/lg-02-part2c-3c-seam-contract.md).

**Locked names (NEW / CHANGED).**
- `Season._seed_order_for_phase(self, phase) -> list[int]` (`matches/models.py`) —
  **NEW private**; the mode-branching seed-vector builder (see below).
- `Season.activate_pending_tournament_phase(self) -> None` `@transaction.atomic`
  (`matches/models.py`) — **CHANGED**: generalised gate (prior-is-None now permitted
  for a non-`standings` first phase) + mode branch via `_seed_order_for_phase`.
- `Season.start_season(self) -> None` `@transaction.atomic` (`matches/models.py`) —
  **CHANGED**: adds an `activate_pending_tournament_phase()` call INSIDE the atomic
  block, after the snapshot writes + `state="active"` + `save()`.
- `Season._tournament_barrier_ordinal(self) -> int | None` (`matches/models.py`) —
  **NEW private** (may inline): the ordinal of the first incomplete `tournament` phase,
  else `None`.
- `Season.playable_fixtures_by_phase(self) -> list[tuple[SeasonPhase, list[ScheduleFixture]]]`
  (`matches/models.py`) — **NEW**: `scheduled_fixtures_by_phase()` filtered to RR phases
  whose `ordinal < barrier`.
- `parse_phase_composition(raw, *, season_schedule_format) -> list[PhaseSpec]`
  (`matches/phase_composer.py`) — **CHANGED**: parses the `tournament[:mode]` token.
- `play_season_task` (`matches/tasks.py`) / `play_week` (`matches/league_views.py`) —
  **CHANGED**: one-line swap `scheduled_fixtures_by_phase()` →
  `playable_fixtures_by_phase()`.
- `_build_dashboard_context` / `_playoff_cursor_keys` (`matches/league_views.py`) —
  **CHANGED**: add the `following_tournament_is_final: bool` context for the terminal
  label split.

**Wire format + ValueError (`matches/phase_composer.py`).** The `tournament` token
becomes **`tournament[:mode]`** — each token splits on the FIRST `:` into
`(type_part, format_part)`; for a `tournament` token `format_part` is the **MODE**
(versus an RR token where it is the schedule format). Bare `tournament` ⇒
`tournament_mode="standings"`. Valid modes this slice: `standings`, `strength`,
`unseeded`. `random_draw` **AND any unknown string** ⇒ NEW locked
`ValueError(f"unknown tournament_mode: {mode!r}")`. A `tournament` token sets
`schedule_format=None`, `tournament_mode=mode`. Every pre-existing `ValueError` string
is preserved VERBATIM (`"malformed phase composition"`,
`f"unknown phase type: {token!r}"` — `member_night` still rejected at the **type**
level, `f"unknown schedule_format: {fmt!r}"`,
`"composition must contain at least one round-robin phase"`,
`"a tournament phase requires a preceding round-robin phase"`). The module stays
**Django-free** (frozen `dataclasses` / `typing` allowlist); the form layer re-wraps
the plain `ValueError` as a `forms.ValidationError` on `phases`. `PhaseSpec` shape
UNCHANGED (`(ordinal, phase_type, schedule_format, tournament_mode)`).

**Compose-guard relaxation (`matches/phase_composer.py`).** The **≥1-round-robin** rule
is kept VERBATIM. The `"a tournament phase requires a preceding round-robin phase"`
string is preserved but now fires **ONLY** when
`spec.phase_type == "tournament" AND spec.tournament_mode == "standings" AND not
seen_round_robin` — so `strength` / `unseeded` may sit **anywhere, including first**,
and a mid-season `standings` tournament is ALLOWED (there is no
"standings-must-be-final" guard, only "standings-must-have-a-preceding-RR").

**Build differential (`Season.activate_pending_tournament_phase` +
`_seed_order_for_phase`).** The existing idempotency guards (`phase is None` /
`phase.phase_type != "tournament"` / `phase.tournament_id is not None` /
`phase.pk is None`) are kept. The gate generalises: for `standings`, `prior is None or
not _phase_complete(prior)` returns (unchanged); for `strength` / `unseeded`, a NULL
prior is PERMITTED, and a present prior must still be `_phase_complete` (the barrier
guarantees this). `_seed_order_for_phase` branches on `tournament_mode`:
- **`standings`** → `[row.team_id for row in self._final_standings_for_phase(prior)]`
  (rank order; `prior` non-None per the gate) — byte-identical to today's ordering.
- **`strength`** → `bracket.default_seed_order([(tid, mean_overall_rating) for tid in
  team_ids])` where `team_ids = self.starting_team_ids_json or []` and
  `mean_overall_rating = mean(p.overall_rating for p in Team(tid).active_players)`
  (`Team.active_players` property; `Player.overall_rating` property; mean of an empty
  active-players list ⇒ `0.0`, guarding `ZeroDivisionError`). `default_seed_order` sorts
  mean DESC then `team_id` ASC.
- **`unseeded`** → a fresh `random.Random()` shuffle of `team_ids` (`random` imported
  locally; **NOT** the SIM-07 seed chain — non-deterministic, no contract interaction).

The **shared build tail is mode-independent**: create a `single_elimination` Tournament
(`team_assembly="preset"`, `state="setup"`), one `TournamentParticipant` per ordered
team with **`seed = position + 1`** (byte-identical to today's `seed=row.rank` for
`standings`, since `_final_standings_for_phase` returns dense 1..N ranks), set
`phase.tournament` + `save(update_fields=["tournament"])`, then `lock_and_build()`. The
tournament **name** is `f"{self.name} Playoffs"` for `standings` else
`f"{self.name} Tournament"`.

**Build trigger (`Season.start_season`).** Gains an
`activate_pending_tournament_phase()` call INSIDE the existing `@transaction.atomic`
block, AFTER the snapshot writes (`starting_team_ids_json` /
`starting_map_pool_ids_json`) and `state="active"` + `save()` — so a first-phase
`strength` / `unseeded` tournament builds the instant the Season activates. The existing
post-`simulate_scheduled_round` hook (which already calls
`activate_pending_tournament_phase()` before `complete_if_finished()`) is UNCHANGED — it
covers the mid-season-after-RR case; the method is idempotent so calling it at both sites
is safe.

**Barrier (`playable_fixtures_by_phase` + two swaps).**
`playable_fixtures_by_phase()` = `scheduled_fixtures_by_phase()` filtered to RR phases
whose `ordinal` is strictly LESS than `_tournament_barrier_ordinal()` (the first
incomplete `tournament` phase's ordinal); when no tournament phase is incomplete it
returns the full output. `_tournament_barrier_ordinal()` walks `ordered_phases()` and
returns the ordinal of the first `tournament` phase that is NOT `_phase_complete`, else
`None`. Two one-line play-loop swaps —
`matches/tasks.py::play_season_task` and `matches/league_views.py::play_week` swap
`scheduled_fixtures_by_phase()` → `playable_fixtures_by_phase()`; everything else in
both loops (`phase_by_id`, the flat `[(phase.id, fixture)]` build, the leg-bearing
`played_keys`, `select_play_fixtures`, offsets, `arena_map` resolution) is UNCHANGED.
`play_two_months` / `play_until_end` enqueue `play_season_task` — UNCHANGED. **Why:**
with a mid-season tournament the RR loop must halt before later RR phases so the bracket
(built by the hook) drains through `play_single_round` / `play_playoffs` first; once the
tournament phase completes, the barrier advances past it and the later RR phases become
playable.

**Dashboard label split (`_build_dashboard_context` / templates).** A new
`following_tournament_is_final: bool` (computed in / alongside `_playoff_cursor_keys`)
drives the terminal play button: when the next tournament phase after the current RR
phase is the FINAL phase (last ordinal) → label **"Until Playoffs"**; when it is
mid-season (not last ordinal) → **"Until Tournament"**. `has_following_tournament_phase`
still gates whether the tournament-aware label shows at all. Touches both
`templates/seasons/dashboard.html` and `templates/leagues/dashboard.html`. **LOCKED:**
the playoff button-group DOM ids + the `play_until_end` action are UNCHANGED (visible
label text only); the Part2c-1 playoff context keys + Play Single Round / Play Playoffs /
View bracket controls are UNCHANGED — they key off `current_phase()` being a built
tournament phase, so a mid-season bracket drains through them with no structural change.

**Composer (`templates/leagues/create.html`).** A tournament composer row gains a mode
`<select>` with locked DOM id **`league-create-phase-mode-{i}`** (`{i}` = the existing
0-based JS `rowSeq` index). Options `standings` / `strength` / `unseeded` selectable;
**`random_draw` a DISABLED "coming soon" option** (the `member_night` deferral pattern).
Shown for `tournament` rows only (hidden for `round_robin`, mirroring how
`phase-format-select` is RR-only via `applyType()`). `serialize()` emits
`tournament:<mode>` for a tournament row (reading the row's `.phase-mode-select`,
default `"standings"`); RR rows still emit `round_robin:<format>`. The
`phase-tournament-pending` note is PRESERVED; all existing Part2b / c-3a DOM ids
unchanged.

**UNCHANGED (→ later slices).** `random_draw` build (DEFERRED); the pure
`select_play_fixtures` / `find_next_matchday` / `find_next_fixture` / `round_progress` /
`compute_leaders` helpers + `scheduled_fixtures` / `scheduled_fixtures_by_phase` (the
DISPLAY path); `bracket.default_seed_order` body, `Tournament.lock_and_build`,
`TournamentParticipant`, `simulate_match` / `simulate_scheduled_round`, the tournament
engine; `complete_if_finished` / `_stamp_champion_for_final_phase` (champion still from
the FINAL phase). **No migration, no re-baseline.**

**Tests.** `test_phase_composer.py` (bare `tournament` ⇒ `standings`;
`tournament:strength` / `:unseeded` / `:standings` parse + stamp; `tournament:random_draw`
/ `:bogus` ⇒ `unknown tournament_mode`; `member_night` still `unknown phase type`; guard
relaxation — `tournament:standings,round_robin` raises, `tournament:strength,round_robin`
/ `:unseeded,...` / `round_robin,tournament:standings` /
`round_robin,tournament:standings,round_robin` do NOT; purity); seed-vector unit
(`unseeded` ⇒ valid permutation with dense seeds 1..N, NOT an exact order; `strength` ⇒
`default_seed_order` of `(team_id, mean rating)`); DB `TestCase` (build at `start_season`
for a first-phase `strength`/`unseeded`; build post-round mid-season-after-RR; barrier
excludes post-barrier RR fixtures then re-admits after the tournament completes —
assert on the **set** of excluded fixtures, NOT point totals; mid-season `standings`
seeds from cumulative standings-so-far; champion still from final phase; dashboard label
split). **Assertion discipline:** schema-level outcomes only (participant seeds, excluded
fixtures, state flips, champion id, label string) — NEVER raw simulated point totals
(tournament sims are non-deterministic).

## LG-02-Part2c-3d per-tournament-block configuration

Surfaces **per-`tournament`-block config** on `SeasonPhase` — a **dormant**
`tournament_format` column (written-but-unread by the build) and a **live**
`tournament_cut` top-N participant cut applied to the seeded order before the
bracket builds. A **pure orchestration/config slice** — NO simulator mechanics
change, NO RNG change, NO tournament-engine change (`play_next_node` /
`lock_and_build` consumed VERBATIM), **no Score Calibration re-baseline**;
`tournament_cut=0` (the default) is **byte-identical to today** (full participant
set). Extends
[ADR-0023](../../docs/adr/0023-season-phase-composable-structure.md) (Part2c-3d
consequences addendum, no new ADR); the CONTEXT.md **Season phase** entry carries
the config-split vocabulary. Seam contract:
[`.claude/worktrees/lg-02-part2c-3d-seam-contract.md`](../../.claude/worktrees/lg-02-part2c-3d-seam-contract.md).

**Model (`matches/models.py`, `SeasonPhase`).** Two new columns, appended
immediately after the existing `tournament_mode` field (before `class Meta`):
- **`tournament_format = models.CharField(max_length=32,
  choices=TOURNAMENT_FORMAT_CHOICES, default="single_elimination")`** — **DORMANT**
  (written-but-unread by the build this slice). Class attr
  **`TOURNAMENT_FORMAT_CHOICES`** is INLINED on `SeasonPhase` (it must NOT reference
  `Tournament.FORMAT_CHOICES` — `Tournament` is declared LATER in the file, so a
  class-body reference fails at eval; the c-3b `TOURNAMENT_MODE_CHOICES`-inlined
  precedent). Its **5 tuples mirror `Tournament.FORMAT_CHOICES` byte-for-byte**:
  `("single_elimination", "Single elimination")`, `("double_elimination", "Double
  elimination")`, `("round_robin", "Round robin")`, `("round_robin_double_elim",
  "Round robin → Double elimination")` (the `→` is U+2192), `("swiss", "Swiss")`.
- **`tournament_cut = models.PositiveSmallIntegerField(default=0)`** — **LIVE**
  (`0` = no cut = all enrolled teams). No `db_index`, no `null`/`blank`, no
  constraint on either column.

**Migration `0046_seasonphase_format_cut`** (dep `0045_seasonphase_tournament_mode`):
exactly **2× `AddField`** in order — `tournament_format` THEN `tournament_cut` —
**NO `RunPython` / NO backfill** ([ADR-0004](../../docs/adr/0004-simulation-data-is-disposable.md)
posture — existing tournament phases inherit `tournament_format="single_elimination"`
+ `tournament_cut=0`).

**Build cut slice (`Season.activate_pending_tournament_phase`,
`@transaction.atomic`).** The method is UNCHANGED except for **ONE inserted guard**,
placed AFTER `order = self._seed_order_for_phase(phase)` and BEFORE the existing
`if not order: return`:

```python
        order = self._seed_order_for_phase(phase)
        if phase.tournament_cut:
            order = order[:phase.tournament_cut]
        if not order:
            return
```

It keeps the top `cut` seeds of the already-ordered (any-mode) seed vector — dense
seeds `1..cut`. **Cut semantics:** `tournament_cut == 0` ⇒ slice not applied (full
set, byte-identical to today); `cut > 0` ⇒ `order[:cut]`; `cut > len(order)` ⇒ a
Python no-op slice past end (all teams). `Season._seed_order_for_phase` is
**BYTE-IDENTICAL / NOT edited** — the cut applies to its OUTPUT at the caller, never
inside it. The build tail STAYS hardcoded: `Tournament.objects.create(...,
format="single_elimination", team_assembly="preset", state="setup")`, the
participant loop `seed = position + 1`, `phase.tournament` /
`save(update_fields=["tournament"])`, `lock_and_build()`. **`tournament_format` is
written-but-unread** — an admin who sets `tournament_format="swiss"` STILL gets a
single-elim bracket (a known, ACCEPTABLE admin foot-gun this slice; the live format
build is c-3e).

**Pure module (`matches/phase_composer.py`).** `PhaseSpec` gains a trailing
**`tournament_cut: int = 0`** (appended LAST with a default ⇒ existing keyword
constructions stay equality-identical, the c-3a `leg` / c-3b `tournament_mode`
precedent). The frozen import allowlist (`dataclasses` / `typing` ONLY) is
UNCHANGED — the new code adds **NO import**, so `TestNoDjangoImportsLeaked` stays
green.

**Wire grammar `tournament[:mode[:cut]]` (`matches/phase_composer.py`).** The
**tournament branch only** switches from `token.partition(":")` to
**`parts = token.split(":")`**: `parts[0]` = type, `parts[1]` = mode (default
`"standings"`), `parts[2]` = cut string (default `"0"`). The **RR branch grammar is
UNCHANGED** — still `round_robin[:schedule_format]` (max 2 parts via `partition`; a
3rd part → malformed). Locked tournament-token rules:
- bare `tournament` ⇒ mode `"standings"`, cut `0`.
- `tournament:strength` ⇒ mode `"strength"`, cut `0`.
- `tournament:standings:8` ⇒ mode `"standings"`, cut `8`.
- `len(parts) > 3` ⇒ EXISTING `ValueError("malformed phase composition")`.
- empty cut (e.g. `tournament:standings:`) / non-int cut ⇒ EXISTING
  `"malformed phase composition"`.
- mode not in `_VALID_TOURNAMENT_MODES` ⇒ EXISTING
  `ValueError(f"unknown tournament_mode: {mode!r}")` (verbatim; the tuple
  `("standings", "strength", "unseeded")` is UNCHANGED).
- parsed `cut != 0 and cut < 4` ⇒ NEW LOCKED
  **`ValueError(f"tournament cut must be 0 or at least 4: {cut}")`** (`cut` is the
  parsed int, no `!r`) — the floor `{0} ∪ {≥4}`.

Validation order on the tournament branch (LOCKED): split → reject `len(parts) > 3`
(malformed) → mode-membership check → parse cut (empty / non-int → malformed) →
cut-floor check. `member_night` is still rejected at the phase-**type** level by the
existing `f"unknown phase type: {token!r}"`. Every pre-existing `ValueError` string
is preserved VERBATIM (`"malformed phase composition"`,
`f"unknown schedule_format: {schedule_format!r}"`,
`f"unknown tournament_mode: {mode!r}"`, `f"unknown phase type: {token!r}"`,
`"composition must contain at least one round-robin phase"`,
`"a tournament phase requires a preceding round-robin phase"`). The module stays
**Django-free**; the form layer re-wraps the plain `ValueError` as a
`forms.ValidationError` on `phases`. **Validation is PARSER-ONLY** — NO
`Season.clean()` / `SeasonPhase.clean()` guard is added; a `cut` leaving `< 4`
participants at runtime is caught defence-in-depth by the EXISTING
`Tournament.lock_and_build` ≥4-participant `ValidationError`.

**Creation / carry-forward (`matches/league_views.py`).** Both
`SeasonPhase.objects.create(...)` loops sit inside their existing
`@transaction.atomic` blocks:
- `league_create` (~L559) adds **`tournament_cut=spec.tournament_cut`** and does
  **NOT** set `tournament_format` (there is no `PhaseSpec.tournament_format`; the
  column default `"single_elimination"` applies).
- `next_season` (~L2137) carries forward **BOTH**
  `tournament_cut=src.tournament_cut` AND `tournament_format=src.tournament_format`
  verbatim (the persisted source `SeasonPhase` row has both real columns).

**Composer (`templates/leagues/create.html`).** A tournament composer row gains two
new tournament-rows-only controls (same `applyType()` show/hide rule as the mode
`<select>`):
- **Cut input** — `<input type="number" min="0">` with DOM id
  **`league-create-phase-cut-{i}`** (`{i}` = the existing `rowSeq` index), class hook
  **`phase-cut-input`**, **default value `0`**, wired to `serialize()` on `change`.
- **Disabled tournament-format `<select>`** — DOM id
  **`league-create-phase-tournament-format-{i}`** (DISTINCT from the RR
  `league-create-phase-format-{i}`), single visible option text **"Single
  elimination (more formats coming soon)"**, `disabled` attribute present. It
  serializes **NOTHING** (`serialize()` must NOT read it — the build hardcodes
  `format="single_elimination"`, the column default covers persistence; a visual
  placeholder, the `phase-tournament-pending` / disabled-`random_draw`-option
  precedent).

`serialize()` emits **`tournament:<mode>:<cut>`** for a tournament row (reading the
row's `.phase-mode-select` then `.phase-cut-input`, cut defaulting to `"0"`); RR rows
still emit `round_robin:<format>` UNCHANGED. The default `value="0"` on the cut input
guarantees a parseable `"0"` for an untouched tournament row. All existing Part2b /
c-3a / c-3c DOM ids unchanged (`league-create-phases-composer`,
`league-create-add-block`, `league-create-phases`, `league-create-phase-row-{i}`,
`league-create-phase-type-{i}`, `league-create-phase-format-{i}`,
`league-create-phase-mode-{i}`, `league-create-member-night-note`, the
`phase-tournament-pending` class).

**Admin (`matches/admin.py`).** `SeasonPhaseAdmin.list_display` appends
`"tournament_format"`, `"tournament_cut"` (now 8 entries).

**Backward-compat invariants.** `tournament_cut == 0` ⇒ byte-identical to today
(full participant set; `order[:cut]` slice not applied). Bare `tournament` /
`tournament:strength` wire tokens parse identically to c-3c (mode resolved, cut `0`);
every Part2b / c-3a / c-3c serialized value parses unchanged. `cut > enrolled-team
count` ⇒ no-op slice (all teams). `tournament_format` is written-but-unread (the
acceptable admin foot-gun above).

**UNCHANGED (→ c-3e).** The LIVE format picker + per-format sub-config + the
non-single-elim build that READS `tournament_format` (DEFERRED to c-3e — the c-3b→c-3c
dormant→live rhythm); `team_assembly` (subsumed by the deferred
`tournament_mode="random_draw"`); `Season._seed_order_for_phase` body; the tournament
engine (`play_next_node` / `lock_and_build` / `TournamentParticipant`); the simulator,
RNG, and `Match` model; the read-path / barrier / dashboard label split (c-3c).
**No re-baseline.**

**Tests.** `test_phase_composer.py` (cut grammar — `tournament:standings:8` ⇒ cut 8,
`tournament:strength` ⇒ cut 0, bare `tournament` ⇒ cut 0; floor —
`tournament:standings:3` ⇒ the new cut-floor `ValueError`, `:0` accepted; malformed —
`len(parts) > 3` / empty cut / non-int cut ⇒ `"malformed phase composition"`;
back-compat — every c-3c serialized value parses unchanged; `PhaseSpec` default
`tournament_cut == 0`; purity `TestNoDjangoImportsLeaked`),
`test_season_phase.py` (`tournament_format` default `"single_elimination"` +
`TOURNAMENT_FORMAT_CHOICES` 5 tuples + `max_length==32`; `tournament_cut` default `0`
+ `PositiveSmallIntegerField`), `test_season_playoffs.py` (NOTE the trailing `s` —
the build cut: participant COUNT == `cut` + dense seeds `1..N` + champion stamped +
the built tournament's `format` stays `"single_elimination"`; `cut=0` ⇒ full set;
`cut > enrolled` ⇒ all teams), `test_league_create.py` (a composed tournament phase
persists its `tournament_cut`; `tournament_format` defaults to
`"single_elimination"`), `test_league_next_season.py` (**hand-set a source phase's
`tournament_cut=8` + `tournament_format` e.g. `"swiss"` via ORM, assert the
carry-forward preserves BOTH**). **Assertion discipline:** schema-level outcomes only
(participant seeds/count, built `format`, champion id, parsed cut) — NEVER raw
simulated point totals (tournament sims are non-deterministic).

## Sub-packages

- [`sim_helpers/CLAUDE.md`](sim_helpers/CLAUDE.md) — `BatchSimulator` helper modules: `PlayerState` dataclass, action weights, pathfinding, `mechanics.py` (pure game mechanics), `combat.py` (shared combat resolution), `role_constants.py` (canonical role stats), `score_calculator.py` (MVP formula), `map_context.py` (typed map wrapper), `map_loader.py` (map-loading helpers extracted from RBS by SIM-09), `pending_events.py` (typed pending-queue dataclasses), `spawn_assigner.py` (spawn logic)
- [`management/commands/CLAUDE.md`](management/commands/CLAUDE.md) — `score_averages` and `game_analysis` management commands