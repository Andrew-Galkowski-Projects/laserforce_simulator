# matches/

Handles match creation, game round simulation, event logging, and result views.

## Models (`matches/models.py`)

**`Match`**: Two `GameRound`s; teams swap colors between rounds. Winner is determined by rounds won, then total cumulative points. A 10,000-point bonus is awarded for eliminating the opposing team entirely.

**`GameRound`**: One of the two rounds in a match; represents a 15-minute simulation. Has an optional `arena_map` FK (`core.ArenaMap`, null/blank, SET_NULL) and `zone_size` IntegerField (null/blank). Both are set by the simulator when a map is provided; null means the round ran with the 3-zone fallback. `rng_seed` (`BigIntegerField`, null/blank, SIM-07) holds the 63-bit integer seed `random.seed()`'d before this round was simulated, making the round replayable via `BatchSimulator.replay_round`; null means the round predates SIM-07, was produced by RBS, or is otherwise not replayable (no backfill, ADR-0004). Replay is faithful **only** while the round's rosters and map config are unchanged — the seed captures randomness, not world state (roster/map snapshotting is deferred, not SIM-07). Rationale: storing a seed rather than `random.getstate()` is recorded in [ADR-0005](../../docs/adr/0005-rng-seed-not-state-for-replay.md).

**`PlayerRoundState`**: Starting resources are role-dependent (lives, shots, special, missiles). Tracks final resource counts, tags, misses, zone visits, MVP score. `was_eliminated_at` stores the **tick** of final elimination (`1801` = survived the full round, the `SURVIVED_SENTINEL`; was `901` before TIME-01). The MVP formula is role-specific and weighted heavily toward that role's primary contribution. Also tracks `follow_up_shots` (total follow-up shots fired after non-downing hits) and `reaction_shots` (shots fired in response to being tagged or missed). Uptime breakdown fields (TIME-01 rename from `seconds_*`): `ticks_active` (ticks the player was fully active and targetable), `ticks_not_targetable` (ticks spent in the post-tag deactivation window), and `ticks_reset_window` (ticks spent in the taggable portion of the reset window between deactivation and full return); these reconcile with derived dead-time (`1800 - was_eliminated_at`) to exactly 1800 ticks per player. Scoring breakdown: `missile_points` (total points earned from missile hits and base destructions, separate from tag points). Cell position: `cell_row` and `cell_col` (IntegerFields, null/blank) store the player's current cell when a map is used — updated each tick after movement. `zone_fallback` (was `current_zone` DB column) stores the zone index (0=red, 1=neutral, 2=blue); `current_zone` is a `@property` that reads `zone_fallback`. The simulator updates `zone_fallback` after each cell move via `player.save(update_fields=["cell_row", "cell_col", "zone_fallback"])`. `combo_resupply_count` (IntegerField, default=0) tracks the number of times this player received both a lives and a shots resupply in the same tick (combo resupply); incremented by `resolve_resupply_requests` in `sim_helpers/resupply_queue.py`.

Forwarding properties added to satisfy the duck-type interface required by `sim_helpers/combat.py` and `mechanics.py` (so shared functions work with both `PlayerRoundState` and `PlayerState`): `accuracy`, `survival`, `name`, `player_awareness` (all delegating to `self.player`), `last_shot_time` (backed by a transient `_last_shot_time` attribute, default −99.0), and `tag_id_key` (returns `self.get_tag_id()`). No DB fields — no migration required. The `accuracy`, `survival`, and `player_awareness` properties call `self.player.stat_for_simulation(stat_name, self.role)` to apply the preferred-role 20% boost (capped at 100) rather than reading the raw field directly.

`get_mvp` is now a thin delegating property that calls `calculate_mvp(self)` from `matches.sim_helpers.score_calculator`. `max_lives` and `max_shots` now look up `MAX_LIVES`/`MAX_SHOTS` from `matches.sim_helpers.role_constants` rather than inline dicts.

**`GameEvent`**: Every action (tag, missile, special, miss, resupply, base capture, elimination, movement) is logged here with an actor, optional target, `timestamp` in **ticks** (TIME-01 — was "seconds into the round"; the REST API returns this raw, the `÷2`-to-seconds conversion happens only at HTML/CLI), points, and a JSON `metadata` field. Movement events (`event_type="movement"`) carry `cell_row`, `cell_col`, `new_zone`, and `actor_role` in `metadata` for replay. Combo resupply events use `event_type="combo_resupply"` and carry `{"medic_tag": <str>, "ammo_tag": <str>}` in `metadata`; these fire when a player receives both lives and shots in the same tick. Single resupply events continue to use `event_type="resupply_lives"` or `event_type="resupply_ammo"`.

## Simulation Engine (`matches/simulation.py`)

Two simulators live in `matches/simulation.py`:

**`ResourceBasedSimulator`** — DB-backed, writes `GameEvent` rows and `PlayerRoundState`. Used for full match simulation with event replay. Prefer this when you need the game event log or a persisted round. All match and single-round creation views use this exclusively — the legacy `SimpleMatchSimulator` has been removed. **TIME-01 (minimal conversion):** RBS keeps a second-internal tick loop but converts `×2` only at the persist/`GameEvent`/API boundary, so every value it writes (`timestamp`, `ticks_*`, `was_eliminated_at`) is in ticks. The constructor arg is `duration_ticks` (was `duration`); callsites pass `duration_ticks=40` / `120`.

Public methods accept an optional keyword-only `arena_map` parameter:
- `simulate_match(team_red, team_blue, match_type="friendly", *, arena_map=None)`
- `simulate_single_round_detailed(team_red, team_blue, *, arena_map=None)`
- `simulate_detailed_round(team_red, team_blue, match=None, round_number=1, *, arena_map=None)`

Static helpers:
- `_load_map_context(arena_map) -> tuple[MapContext | None, int | None]` — **primary map-loading entry point**. Merges the former two-step `_resolve_map_data` → `_build_movement_ctx` pipeline into one call: runs all ORM queries (zone config, base positions, sight lines, base sight lines, cell ranking, strong spots, spawn pools, elevation) and immediately constructs a `MapContext` object. Returns `(None, None)` when `arena_map` is `None` (3-zone fallback). Raises `ValueError` for the same missing-config cases as the old pipeline. `simulate_detailed_round` and `BatchSimulator.run` both use this exclusively.
- `_resolve_map_data(arena_map)` — **legacy shim, retained for test compatibility**. Still returns a `MapData` dataclass. New code should use `_load_map_context` instead.
- `_build_movement_ctx(zone_data, spawn_cells, ...)` — **retained for backward compat**. Now returns a `MapContext` object (was a plain 11-key dict). Prefer `_load_map_context` for new callers.
- `_build_spawn_assignments(roster_roles, team_color, spawn_cells, team_spawn_pools) -> dict[int, tuple | None]` — **delegation shim** that calls `assign_spawn_cells` from `sim_helpers/spawn_assigner.py`. The spawn logic itself now lives there; this shim exists for callers that reference the method by name.
- `_zone_from_cell(row, col, spawn_cells: dict | None)` — returns zone index (0=red, 1=neutral, 2=blue) by Manhattan-distance proximity to base cells. Nearest base type wins; neutral bases take precedence when closer than or equidistant to both team bases. Returns 1 (neutral) when `spawn_cells` is None/empty or red/blue base is absent.

Module-level class:
- `MapData` — dataclass retained for `_resolve_map_data` backward compat: `zone_size`, `spawn_cells`, `zone_data`, `sight_data`, `base_sight_data`, `cell_ranking`, `strong_spots`, `wall_meta`, `spawn_pools`, `elevation_grid`. New code uses `MapContext` (see `sim_helpers/map_context.py`) which replaces the old 11-key `movement_ctx` dict.

Visibility, elevation, and base-interaction helpers have moved to `matches/sim_helpers/combat.py` — see [`sim_helpers/CLAUDE.md`](sim_helpers/CLAUDE.md) for their full descriptions. `simulation.py` now imports them from `combat` rather than defining them inline.

Cell-aware movement (MAP-02/05, active when `movement_ctx is not None` and `player.cell_row is not None`):
- `_choose_goal_cell(player, all_alive, movement_ctx, intended_action="")` — delegates to `pathfinding.choose_goal_cell`; passes the player's `last_chosen_action` as `intended_action` so movement is action-aware (MAP-05). Default goal is the enemy base cell.
- `_move_to_cell(player, second, goal_cell, movement_ctx)` — calls `astar_next_step`, updates `cell_row`/`cell_col`/`zone_fallback`, saves to DB, writes a `GameEvent(event_type="movement")`.

When no map is assigned (`movement_ctx is None`), the old `_change_zone` 3-zone fallback is used (MAP-06 compatibility).

**`BatchSimulator`** — pure in-memory, no DB writes. Uses `PlayerState` dataclasses (see `matches/sim_helpers/player_state.py`). Used by `score_averages` and batch win-rate analysis. A round typically runs in ~25 ms vs ~9 s for the DB-backed simulator. **TIME-01:** BatchSim is **fully tick-native** — its loop counter, all `ticks_*` accumulators, `was_eliminated_at`, and every game-logic edge are in ticks (1800-tick round, no seconds anywhere internally).

`run(team_red, team_blue, n=100, *, arena_map=None, master_seed=None)` — accepts an optional `arena_map` keyword argument; when provided, resolves map data, builds a `movement_ctx`, and passes it to `_simulate_round` so players navigate by A* rather than the 3-zone fallback. `_make_players` accepts `spawn_cells` and `zone_data` kwargs and initialises `cell_row`/`cell_col` from the team's spawn cell; it also bakes boosted stat values (via `stat_for_simulation`) into each `PlayerState` at construction so the in-memory simulation never calls back to the ORM for per-tick stat reads. `_move_player_in_memory` mirrors `_move_to_cell` but updates `player.current_zone` directly without any DB writes.

**SIM-07 seeding:** `master_seed` defaults to `None`, in which case each batch run draws a fresh random master from an independent OS-entropy generator; tests pin it to watch aggregate results move after weight/logic changes. Per-round int seeds are derived from a deterministic `random.Random(master_seed)` ("seed chain") — same master seed ⇒ same chain ⇒ identical games. `_run_parallel` and `batch_round_worker` take an int seed and `random.seed(it)`, so **serial and parallel runs produce identical games for a given master seed** (a guaranteed, tested property). `avg_seeds` / `outlier_seeds` are `list[int]`. `replay_round(red_roster, blue_roster, seed, movement_ctx=None)` does `random.seed(seed)` then `_simulate_round`; `save_games` takes a `list[int]` and persists each via `_flush_to_db(..., rng_seed=...)` onto `GameRound.rng_seed`. Replay is faithful only while rosters and map config are unchanged (the seed captures randomness, not world state). `score_round_worker` (the `score_averages` path) is intentionally unchanged and out of SIM-07 scope.

Both simulators follow the same per-tick loop:

1. Process pending missiles/nukes that have completed their delay
2. Process pending deferred follow-up and reaction shots (shots scheduled by shot-cooldown logic)
3. Each active player picks an action (weighted random by role, zone, remaining resources)
4. Resolve the action — update state and optionally write a `GameEvent`
5. Check for team eliminations

Action weights are in `matches/sim_helpers/weights.py`. See [`sim_helpers/CLAUDE.md`](sim_helpers/CLAUDE.md) for details.

**STAT-03 stat wiring** (weights.py / combat.py): `decision_making` applies a post-role spread multiplier (`factor = 1 + dm/100`) — best-weight action × factor, all others ÷ factor (clamped ≥ 0). `stamina` is checked every 10% of round elapsed; when `stamina < elapsed_%`, `stamina_penalty_count` increments, stacking −10% on `change_zone` weight and −5% on hit-chance (`stamina_hit_modifier = max(0.5, 1 − 0.05 × count)`). The schedule is proportional (`elapsed / round_duration`) so it is **unit-agnostic and unchanged by TIME-01** — only the tick-valued round duration feeds it. `special_usage` multiplies the `use_special` weight delta by `special_usage / 50` across all roles. `accuracy` / `survival` feed hit-chance as `70 + accuracy − survival` (confirmed, no change). `resupply_efficiency` scales the `request_resupply` action weight (index 7) for all roles; `resupply_synergy` scales the `resupply_ally` weight for Medic/Ammo players — both wired in MECH-01 (former TODO blocks removed). `teamwork` and `communication` are fully wired as of MECH-06 (former skeleton TODO blocks removed) — see MECH-06 player memory section above for behavioral details. `request_resupply` (action index 7) is available to all roles; at end of each tick `resolve_resupply_requests` from `sim_helpers/resupply_queue.py` is called to fulfill pending requests — see [`sim_helpers/CLAUDE.md`](sim_helpers/CLAUDE.md) for full resolution logic.

**MECH-04 nuke reaction** (both simulators): each tick, `_apply_nuke_reaction_flags` resets then sets `reacting_to_nuke` for every active player on the nuke-targeted team. Each player rolls `reaction_probability = (game_awareness + player_awareness) / 200`. When reacting: Medic/Ammo rush toward the neediest ally (by lives ratio for Medic, shots ratio for Ammo) to maximise resupply output before the nuke lands; their `tag_player` weight also transfers to `resupply_ally + 20` (in `weights.py`). Non-support players with lives ≤ 30% of max override their movement goal to the allied Medic cell (survival mode); lives > 30% — the MECH-06 TODO hook is now filled: player seeks the enemy Commander's last-known cell from `player_memory` (if fresh) to attempt a tag-cancel. `reacting_to_nuke` is a transient bool on `PlayerState` (no DB column); set by `_apply_nuke_reaction_flags` each tick, read by `pathfinding.choose_goal_cell`.

**MECH-06 player memory** (both simulators): replaces perfect-knowledge enemy lookups in goal selection with a per-player memory dict. Key points:
- `player_memory` on `PlayerState`: `{tag_id: {"cell": (r,c), "timestamp": s, "role": role}}` — transient dict, no DB column, cleared at round start.
- Staleness thresholds by role of the remembered player: Heavy/Medic/Ammo = 60 s; Scout/Commander = 15 s. Stale slow-role entries → last-known cell still used; stale fast-role entries → fall through to role default.
- `communication` stat: per-tick probability (0–100%) that a player broadcasts their current LOS snapshot to all alive allies within `sqrt(rows² + cols²) / 2` Euclidean cells.
- `teamwork` stat (>50): on non-nuke-fuse ticks, biases goal selection toward high-LOS cells that are also within LOS of ≥1 alive ally (overlapping coverage).
- Score broadcast every 180 s: losing team → aggression weight +10; winning + low lives + medic dead → hide weight +20; winning + low lives + medic alive + ≥360 s remaining → movement override to allied medic cell. Implemented in `_apply_score_broadcast_weights` in `weights.py`.
- Nuke activation broadcast: when a Commander fires a nuke, all alive enemy players receive the Commander's current cell in their `player_memory`.
- Medic-under-fire alert: when a Medic is tagged 2× within 12 s, all alive teammates receive the Medic's current cell in their `player_memory`.
- Perfect knowledge retained for resupply resolution only (LOS/same-zone checks in `resupply_queue.py` are unchanged).

**MECH-05 nuke cancellation fix**: `BatchSimulator` nuke resolution now checks `n.player.special_active_until >= n.complete_time` (consistent with `ResourceBasedSimulator`) instead of the former `is_active_at`-only check. Nuke resolution is ordered after reaction/followup/tag processing so same-tick cancellations are applied before detonation.

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
- `test_spawn_assigner.py` — 15 unit tests for `assign_spawn_cells`: happy path role→cell mapping, pool exhaustion overflow, empty/missing pools, blue-team symmetry
- `views_tests.py` — view behaviour: URL routing, form submissions, context keys
- `test_serializers.py` — unit tests for all five serializer classes (including list vs detail split)
- `test_apis.py` — HTTP-level tests for `/api/matches/` and `/api/rounds/` (including `/events/` action)
- `test_mech02_tag_cooldown.py` — 23 pure-unit tests for MECH-02 same-target restriction and `game_awareness` gate; no DB required
- `test_mech03_nuke_stacking.py` — 15 pure-unit tests for MECH-03 Commander nuke-stacking: `_commander_nuke_gate` threshold table, `_get_commander_weights` gating, edge cases at each SP/awareness boundary; no DB required
- `test_mech05_nuke_cancellation.py` — N regression tests for MECH-05: same-tick and within-fuse cancellation, `BatchSimulator` consistency with `ResourceBasedSimulator`, tick-ordering guarantee; no DB required
- `test_mech04_nuke_reaction.py` — N pure-unit tests for MECH-04: reaction probability formula, survival-mode goal override, MECH-06 tag-cancel path, no-reaction for low-awareness players; no DB required
- `test_mech06_player_memory.py` — 75 pure-unit tests for MECH-06: memory staleness thresholds by role, `_cell_from_memory`/`_known_enemies_from_memory` helpers, teamwork bias, score-broadcast weights, nuke-activation broadcast, medic-under-fire alert, MECH-04 TODO hook, communication broadcast, `PlayerState` memory field defaults
- `conftest.py` — shared `make_team_with_slots(prefix)` helper

## Sub-packages

- [`sim_helpers/CLAUDE.md`](sim_helpers/CLAUDE.md) — `BatchSimulator` helper modules: `PlayerState` dataclass, action weights, pathfinding, `mechanics.py` (pure game mechanics), `combat.py` (shared combat resolution), `role_constants.py` (canonical role stats), `score_calculator.py` (MVP formula), `map_context.py` (typed map wrapper), `pending_events.py` (typed pending-queue dataclasses), `tick_engine.py` (shared drain helpers), `spawn_assigner.py` (shared spawn logic)
- [`management/commands/CLAUDE.md`](management/commands/CLAUDE.md) — `score_averages` and `game_analysis` management commands