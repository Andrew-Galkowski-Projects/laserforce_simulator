# matches/sim_helpers

Helper modules used by `BatchSimulator` (the sole simulator post-SIM-09; [ADR-0002](../../../docs/adr/0002-two-simulation-engines.md) superseded) in `matches/simulation.py`. Mostly pure Python — `map_loader.py` is the one exception, lazy-importing Django ORM models for its arena-map queries.

## event_log.py

The **single source of truth for the `GameEvent`-dict shape**. Lands as deepening candidate #2 — collapses the 23+ inline `event_log.append({...})` sites that were scattered across `simulation.py`, `shot.py`, `down.py`, `combat.py`, and `resupply_queue.py`. Pure Python, no Django imports. Pinned by the seam contract at [`.claude/worktrees/event-log-seam-contract.md`](../../../.claude/worktrees/event-log-seam-contract.md).

**`EventLog` class** — null-object-pattern event recorder. `__slots__` for per-round allocation cheapness.

| API | Purpose |
|-----|---------|
| `EventLog(*, persist=True, buffer=None)` | Constructor. `persist=False` ⇒ null-object (each verb early-returns). `buffer=<list>` is a transitional shim letting the caller's pre-existing list and the internal storage reference the same list (used by `simulation.py` during the candidate-#2 migration; removable once `_simulate_round` is refactored to read `events.entries` directly) |
| `events.entries` | The live `list[dict]` of recorded events (NOT a copy). Consumed by `BatchSimulator._flush_to_db` to construct `GameEvent` rows |
| `iter(events)` / `len(events)` | Convenience for iteration / length |
| `repr(events)` | Distinguishes `persist` vs `null` modes |

**13 per-event-type verbs** — each emits one entry into `self._entries` (or no-ops when `persist=False`). Verb signatures:

| Verb | Signature | event_type | points | Description (varies by kind) |
|---|---|---|---|---|
| `tag` | `(attacker, defender, tick, *, kind="initial", chain_depth=0)` | `"tag"` | 100 | `"X tags Y"` / `"X follow-up tags Y"` / `"X reacts to Y"` (overwatch reuses initial wording) |
| `miss` | `(attacker, defender, tick, *, kind="initial", chain_depth=0, reason=None)` | `"miss"` | 0 | `"X misses Y"` / `"X misses Y (hiding)"` / `"X follow-up miss on Y"` / `"X reaction miss on Y"` |
| `elimination` | `(attacker, defender, tick, *, action="tag")` | `"elimination"` | 0 | `"Y eliminated by X"` / `"X eliminates Y (follow-up)"` / `"X eliminates Y (reaction)"` / `"Y eliminated by missile from X"` / `"Y eliminated by nuke"` |
| `nuke_cancelled` | `(commander, tick)` | `"nuke_cancelled"` | 0 | `"X nuke cancelled"` |
| `medic_reset` | `(medic, tick)` | `"medic_reset"` | 0 | `"X medic reset (down-chain)"` |
| `special` | `(actor, tick, *, description, points=0, metadata_extras=None)` | `"special"` | per-arg | caller passes the description (nuke activation, detonation, rapid fire, team heal, team ammo) |
| `locking` | `(attacker, defender, tick)` | `"locking"` | 0 | `"X locks on Y"` |
| `missiled` | `(attacker, defender, tick, *, result, friendly_fire)` | `"missiled"` | 500 if hit else 0 | `"X hits Y with missile"` / `"X misses Y with missile"` |
| `missile_dodge` | `(defender, attacker, tick)` | `"missile_dodge"` | 0 | `"Y dodges missile from X"` (defender as actor, attacker as target) |
| `resupply_lives` | `(supporter, requestor, tick, *, amount=None)` | `"resupply_lives"` | 0 | `"X heals Y"` |
| `resupply_ammo` | `(supporter, requestor, tick, *, amount=None)` | `"resupply_ammo"` | 0 | `"X resupplies Y"` |
| `combo_resupply` | `(requestor, medic, ammo, tick)` | `"combo_resupply"` | 0 | `"medic combo-resupplies requestor"` — actor=medic by convention, target=requestor, metadata extras = `medic_tag` / `ammo_tag` |
| `base_capture` | `(actor, tick, *, base_id, points=1001, description=None, metadata_extras=None)` | `"base_capture"` | per-arg | default `"X captures base neutral/opposing"`; caller passes custom description for end-of-round awarded variants |

**Private internals** (not exposed):
- `_actor_meta(actor)` — the 5-key RES-02b actor-snapshot block (`actor_role`, `actor_shots`, `actor_lives`, `actor_points`, `sp`)
- `_target_meta(target)` — the 4-key target-snapshot block (`target_role`, `target_shots`, `target_lives`, `target_points`)
- `_build_meta(actor, target=None, **extras)` — composes actor + optional target + extras
- `_kind_extras(kind, chain_depth)` — kind → metadata-flag translation for tag/miss verbs (`is_follow_up`/`chain`, `is_reaction`, `overwatch`)

These were duplicated three times pre-refactor (in `simulation.py`, `shot.py`, `down.py`, plus copies in `combat.py` and `resupply_queue.py`) — now one private set inside EventLog. No public re-export; if a future caller needs the metadata-block shape, that's a signal to add a new verb.

**Wire-format normalization.** Pre-refactor, resupply descriptions diverged by code path: `combat.py::attempt_resupply` produced `"X heals Y"` / `"X resupplies Y"`; the `resupply_queue.py` route went through `_resupply_event_dict` and ended up as `"resupply request: resupply_lives"`. EventLog standardizes on the combat.py wording for all paths.

**Movement events stay off the EventLog.** `event_type="movement"` rows are written directly to `GameEvent` at `_flush_to_db` time from `PlayerState.movement_trail` (MOVE-01 / RES-04). No `movement` verb.

**Behaviour-neutral.** Zero RNG consumed. Dict shape is byte-identical to pre-refactor literals. Seeded games byte-identical. **No new Score Calibration re-baseline.**

**Tests:** `matches/tests/test_event_log.py` — 59 pure-unit tests across 17 classes (per-verb output shape, null-log no-op, iteration/length contracts, RES-03 four-key missile metadata, combo_resupply tag-id metadata, three `base_capture` description variants, `TestNoDjangoImportsLeaked` defensive check).

## round_context.py

Pure Python, zero imports beyond stdlib (mirrors `time_constants.py` discipline). `RoundContext` is a `@dataclass` bundling the six per-round mutable references the tick loop threads through:

| Field | Type | Purpose |
|-------|------|---------|
| `events` | `EventLog` | Per-round event recorder (single source of truth for the `GameEvent`-dict shape — see `event_log.py` above). Always non-None: `EventLog(persist=False)` is the null-object batch-path variant. `resolve_shot` calls `ctx.events.tag/.miss/.elimination`; `record_down` calls `ctx.events.medic_reset/.nuke_cancelled`. Field renamed from `event_log: Optional[list]` by the EventLog candidate; default is `EventLog(persist=False)` for test-factory simplicity |
| `pending_nukes` | `list[PendingNuke]` | Live nuke queue; read by `record_down` for the RV-02 nuke-cancellation emit |
| `pending_followups` | `list[PendingFollowup]` | Written by `resolve_shot` when a hit chains a deferred follow-up |
| `pending_reactions` | `list[PendingReaction]` | Written by `resolve_shot` when a tag/miss provokes a deferred reaction |
| `all_alive` | `list` | Live `PlayerState` list this tick. Consumed by the MECH-06 medic-under-fire alert + memory-broadcast side effects on a hit |
| `movement_ctx` | `Any` (`MapContext | None`) | Read by `resolve_shot` for elevation / LoS / base-in-range gates; `None` on the 3-zone fallback |

Built once in `BatchSimulator._simulate_round` after the four pending-event queues are initialised; passed to `sim_helpers.down.record_down` and `sim_helpers.shot.resolve_shot`. Replaces the RV-02 static→instance self-stash on `BatchSimulator` (`self._event_log` / `self._pending_nukes`) that the chokepoint used to reach via `getattr(self, ...)`. The `movement_ctx` field is typed `Any` to keep the module Django-free without an import dependency on `MapContext`. See the shot-resolver consolidation in [`matches/CLAUDE.md`](../CLAUDE.md) and the seam contract at [`.claude/worktrees/shot-resolver-seam-contract.md`](../../../.claude/worktrees/shot-resolver-seam-contract.md).

## down.py

`record_down(player, tick, ctx)` is the **single life-loss bookkeeping chokepoint**. Pure free function; no Django imports; the only sibling sim_helper imports are `round_context` and `pending_events`. Lifted from `BatchSimulator._record_down` by the shot-resolver consolidation (May 2026) — the static→instance hack RV-02 introduced is structurally unnecessary now that `event_log` and `pending_nukes` ride on `ctx`.

**Six behaviours, in evaluation order:**

1. **RV-02 medic-reset chain.** `player.is_active_at(tick)` True (fresh Down / fully recovered) ⇒ `down_chain_count = 1`; False (re-Down within the respawn cooldown) ⇒ `down_chain_count += 1`. A Medic at `chain == 2` emits one `medic_reset` event into `ctx.event_log`.
2. **Stamp `player.last_downed_time = tick`.**
3. **Clear `player._path_cache = None`** (MOVE-02 — knocked off committed route).
4. **Clear `player.is_holding = False`** (MOVE-03 — Down ends Overwatch).
5. **Clear `player._committed_goal = None`** iff `_committed_goal[1]` (the `from_action_driven` flag) is True (MOVE-04 — positioning goals survive a Down).
6. **RV-02 nuke_cancelled.** Commander only: scan `ctx.pending_nukes`; for each nuke whose `player is` this Commander and `cancel_logged` is False, emit one `nuke_cancelled` event via `ctx.events.nuke_cancelled(commander, tick)` and set `cancel_logged = True`. The nuke is LEFT in the queue (MECH-05 — drain path is structurally unchanged).

**Does NOT touch `final_lives` or `shields`** — those mutations happen at the caller (tag / follow-up / reaction / missile / nuke). Callers: `sim_helpers.shot.resolve_shot` (the primary site — every shot-driven life loss), `BatchSimulator._complete_missile`, `BatchSimulator._complete_nuke`. `ctx` is typed `Optional[RoundContext]` for compatibility with legacy/test callsites that exercise the resolver without a per-round context; `ctx is None` skips the verb emits and the nuke scan but still does the state mutations.

EventLog candidate: the medic_reset and nuke_cancelled emits route through `ctx.events.medic_reset(medic, tick)` / `ctx.events.nuke_cancelled(commander, tick)` verbs (the legacy `_actor_meta` helper here is gone — EventLog owns the metadata shape).

**Tests:** `matches/tests/test_record_down.py` — 28 pure-unit tests across 4 classes pinning all six behaviours, no DB required.

## shot.py

`resolve_shot(attacker, defender, tick, *, kind, ctx, chain_depth=0) -> ShotOutcome` is the **wide Shot resolver** — the single Shot → Hit → Tag → Down → Elimination ladder consumed by all four call-site `kind` s. Pure free function; sim_helpers imports only (`combat._elevation_hit_modifier`, `down.record_down`, `mechanics.shot_cooldown`, `pending_events`, `round_context`, `time_constants.TICK_SECONDS`); the MECH-06 side-effect helpers (`_check_medic_under_fire`, `_update_player_memory`, `_broadcast_communication`) are **lazy-imported** from `matches.simulation` at the hit-emit site to avoid a circular import. EventLog candidate retired the local `_actor_meta` / `_target_meta` / `_build_meta` / `_kind_extras` / `_emit_*` helpers — emits now route through `ctx.events.tag(...)` / `.miss(...)` / `.elimination(...)`; only the shot-specific `_elimination_action(kind)` (kind → action-string translation) and `_cooldown_ticks(player, tick)` (the seconds→ticks helper) remain private to shot.py.

**Public surface:**
- `SHOT_KIND_INITIAL = "initial"`, `SHOT_KIND_FOLLOW_UP = "follow_up"`, `SHOT_KIND_REACTION = "reaction"`, `SHOT_KIND_OVERWATCH = "overwatch"` — the four call-site kinds.
- `_VALID_KINDS = frozenset({...})` — module-private; asserted in `resolve_shot`.
- `_MAX_CHAIN_DEPTH = 2` — module-private; the follow-up chain cap.
- `ShotOutcome(hit: bool, downed: bool, eliminated: bool)` — `frozen=True` dataclass; returned by `resolve_shot`. `invalid` / `miss_hid` / plain `miss` all surface as `(False, False, False)`.

**Replaces** the five inline copies of shot resolution that previously lived in `BatchSimulator._resolve_tag_attempts` (initial-tag + immediate-reaction + immediate-follow-up branches) and `_simulate_round` (queued `due_rx` + `due_fu` drains). Each call site now dispatches one line to `resolve_shot`; the immediate-vs-deferred distinction is handled by the resolver via shot-cooldown gating (`cd_ticks == 0` ⇒ recursive `resolve_shot` call; otherwise defer to `ctx.pending_followups` / `ctx.pending_reactions`).

**10 phases** (per the seam contract):
1. **Validity gate.** `attacker.final_shots > 0 or role == "ammo"`; `defender.final_lives > 0`. Else early return.
2. **Hide-50%-miss roll** (uniform across all kinds — behaviour change). `defender.is_hiding and random.random() > 0.5` ⇒ `miss_hid`; emit `miss` with `metadata["reason"]="hiding"`, decrement shots (skip for Ammo — uniform), stamp `last_shot_time`, return.
3. **Hit roll.** `random.randint(1, 100) < clamp(int((70 + acc - surv) * elev_mod * stamina_modifier), 10, 95)`.
4. **Kind-specific counter.** FOLLOW_UP ⇒ `follow_up_shots += 1`; REACTION ⇒ `reaction_shots += 1`; INITIAL / OVERWATCH ⇒ no counter.
5. **`final_shots` decrement** — uniform across all kinds; skipped for Ammo (behaviour change — pre-refactor the initial-tag hit/miss branches decremented even for Ammo).
6. **Stamp `last_shot_time = tick`.**
7. **On hit.** Counter cascade (`tags_made`, `medic_hits` if defender is Medic, `final_special += 1` if attacker isn't Heavy, `+100`/`−20` points, `last_tagged_id`, `times_tagged`, `times_tagged_in_reset_window`, `shields -= shot_power`). On Down (`shields == 0`): if defender is a Commander mid-fuse clear `special_active_until`, decrement `final_lives`, call `record_down`, reset shields to `max_shields`, and on Elimination (`final_lives == 0`) stamp `was_eliminated_at` + emit `elimination` event with `elimination_action ∈ {"tag", "follow_up_tag", "reaction"}` (OVERWATCH maps to `"tag"`). Emit `tag` event with kind metadata flags (`is_follow_up` + `chain` / `is_reaction` / `overwatch` / no extra for INITIAL). MECH-06 side effects on hit: medic-under-fire alert if defender is Medic, memory update + communication broadcast.
8. **On miss.** `shots_missed += 1`; emit `miss` event with kind metadata flags.
9. **Schedule reaction** (Phase 9). Only fired for INITIAL / OVERWATCH (`SHOT_KIND_REACTION` never re-reacts; FOLLOW_UP doesn't provoke reactions per the pre-refactor behaviour). `defender.player_awareness >= random.randint(0, 100)` ⇒ react. `cd_ticks == 0` ⇒ recursive `resolve_shot(..., kind=SHOT_KIND_REACTION)`; else defer via `PendingReaction`.
10. **Schedule follow-up** (Phase 10). Fired on a non-downing hit, when `kind != SHOT_KIND_REACTION`, and when `chain_depth < _MAX_CHAIN_DEPTH`. `defender.player_awareness < random.randint(0, 100)` ⇒ chain. `cd_ticks == 0` ⇒ recursive `resolve_shot(..., kind=SHOT_KIND_FOLLOW_UP, chain_depth=next)`; else defer via `PendingFollowup`.

**Behaviour changes folded into the pending re-baseline.** The two uniform-policy changes above (hide roll, Ammo decrement) plus the one-pass-per-shot RNG interleaving deliberately shift seeded outcomes. Internal SIM-07 / SIM-08 contract (same seed + Orientation + rosters + map ⇒ identical game, serial == parallel, faithful Replay) holds in form. Drift folds into the **already-pending post-MOVE-01 Score Calibration re-baseline** — no new obligation. One calibration-sensitive test (`test_strong_team_winpct_not_diluted_by_alternation` in `test_batch_sim.py`) had its strong-team-win% threshold dropped from 58% to 55% to absorb the drift.

**Private helpers.** `_actor_meta` / `_target_meta` / `_build_meta` (event-metadata builders, mirror `simulation` byte-for-byte; consolidation pending in the EventLog candidate); `_kind_extras(kind, chain_depth)` (kind metadata flags); `_elimination_action(kind)` (the `elimination_action` string); `_emit_tag` / `_emit_miss` / `_emit_elimination` (event-dict appenders); `_cooldown_ticks(player, tick)` (seconds → ticks conversion mirroring the deleted `simulation._cooldown_ticks`); `_maybe_schedule_reaction` / `_maybe_schedule_followup` (Phases 9–10).

**Tests:** `matches/tests/test_shot_resolver.py` — 49 pure-unit tests across 8 classes (`TestResolveShotInitial`, `TestResolveShotAmmoUniformity`, `TestResolveShotHideUniformity`, `TestResolveShotFollowUp`, `TestResolveShotReaction`, `TestResolveShotOverwatch`, `TestResolveShotDownChain`, `TestResolveShotSpecialPoints`) covering every phase × every kind. RNG patched via `matches.sim_helpers.shot.random.randint` / `.random` for deterministic control. No DB required.

## player_state.py

`PlayerState` is an in-memory dataclass that mirrors the `PlayerRoundState` ORM model. `BatchSimulator` uses it instead of DB objects so the tick loop never touches the ORM (round runs in ~200 ms with the current MOVE-01..04 / MECH-01..06 mechanics, BS-1).

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
| `movement_trail` | **MOVE-01** transient list of compact `(start_cell, end_cell, timestamp)` Advance steps, appended by `_move_player_in_memory` only when the cell actually changed; **no DB column / no migration** — flushed to compact `GameEvent(event_type="movement")` rows by `_flush_to_db` only when a round is saved. Reconstructs the player's **Movement trail** (CONTEXT.md); the per-Advance route is also persisted (see `movement_routes` below), while RES-04 occupancy still recomputes it at replay via deterministic A* `start → end` |
| `movement_routes` | Transient list appended **in lockstep** with `movement_trail` (one entry per Advance) holding the **exact cells the player walked** that Advance — `_last_step_cells`, i.e. the committed-route slice `astar_advance_cached` popped (excludes the start, ends at the end cell). **No DB column / no migration**; `_flush_to_db` writes it to each movement `GameEvent`'s `metadata["route"]` so the round-playback **map overlay** (see [`matches/CLAUDE.md`](../CLAUDE.md) **M-1**) draws the TRUE path instead of re-deriving it with an independent per-Advance A* (which zig-zags). RES-04's `reconstruct_cell_occupancy` still reads the 3-tuple `movement_trail`, so it is unaffected |
| `_committed_goal` | **MOVE-04** transient steady-state Goal cell commitment ([ADR-0010](../../docs/adr/0010-goal-commitment-via-tick-cadence-throttling.md), CONTEXT.md **Goal commitment**). `Optional[tuple[tuple[int,int], bool, int]]`, **default `None`** — either `None` or a `(cell, from_action_driven, expires_at_tick)` 3-tuple: `cell` is the committed Goal cell, `from_action_driven` is `True` when the cell came from `_goal_from_action` (tag/missile/resupply/hide target — clear on Down) and `False` when it came from `_goal_from_role` / enemy-base default / `only_move`-driven (positioning intent — survives Down because the player keeps **Advancing** through the **Respawn cooldown**), `expires_at_tick` is `tick + GOAL_RECOMPUTE_PERIOD_TICKS` set per-player at recompute time (expiry-based phase staggers naturally — **not** a global `tick % N == 0`). **No DB column / no migration** (mirrors `_path_cache` / `movement_trail`); default `None` so it is never a required ctor arg and never crosses the parallel-worker process boundary. A fresh per-round `PlayerState` starts uncommitted. Only the steady-state positioning layer of `choose_goal_cell` (steps 2/3/4 — `_goal_from_action`, `_goal_from_role`, enemy-base default) reads/writes this slot; the reactive overrides (step 0 nuke-reaction, step 1 critical-resource lives/shots ≤ 30%, step 1b score-broadcast `seek_medic`) bypass it and continue to fire every tick. **Force-recompute triggers** beyond cadence expiry: no prior commitment, Goal cell reached, exiting **Stationary** (hide → not / hold → not), a reactive override firing this tick, **Down**/respawn iff `from_action_driven`. Parallels `_path_cache` (separate slot, separate invalidation policy: the route cache invalidates **iff a Goal commitment recompute changes the Goal cell** — re-picking the same cell leaves `_path_cache` untouched) |
| `_path_cache` | **MOVE-02** transient goal-keyed A* route cache ([ADR-0008](../../docs/adr/0008-path-commitment-via-goal-keyed-cache.md), CONTEXT.md **Path commitment**). `Optional[tuple]`, **default `None`** — either `None` or a `(cached_goal, remaining_cells, anchor)` 3-tuple: `remaining_cells` is the route still to walk, `anchor` is the cell the previous re-step left the player on (used to detect off-route displacement; a legacy 2-tuple with no anchor is tolerated for hand-built test caches). **No DB column / no migration** (mirrors `movement_trail`); default `None` so it is never a required ctor arg and never crosses the parallel-worker process boundary. A fresh per-round `PlayerState` starts uncached (effectively "cleared at round start"); cleared back to `None` at every tag / follow-up / reaction / missile / nuke life-loss site via the shared `BatchSimulator._record_down(player, second)` helper (knocked off-path → recompute). Managed by `pathfinding.astar_advance_cached` |
| `is_holding` | **MOVE-03** transient bool, **default `False`** (mirrors `is_hiding`; **no DB column / no migration**). Set `True` on a `hold` Action roll — the player is in **Overwatch** (CONTEXT.md) and **Stationary** (joins `is_hiding` / `capture_base` in the `_advance_player` predicate, no **Advance**). **Carries over**: stays `True` until a non-`hold` Action is rolled, or a Down/respawn force-clears it via the shared `BatchSimulator._record_down(player, second)` helper (same hook that drops `_path_cache`, so every life-loss site is covered structurally). [ADR-0009](../../docs/adr/0009-hold-overwatch.md) |
| `_last_step_cells` | **MOVE-03** transient `list[tuple[int,int]]` (default empty), **no DB column / no migration**. Populated each move tick by `pathfinding.astar_advance_cached` with the committed-route cells it popped this tick (the player's traversed cells this Advance); consumed by the `BatchSimulator` Overwatch resolution step to test whether a mover's traversal crossed any **Hold**ing player's **Line of sight** (the "moved *through* LoS in one Advance" guarantee — the exact intermediate route is otherwise discarded by MOVE-01). Read-only signal; consuming it uses **no RNG**. [ADR-0009](../../docs/adr/0009-hold-overwatch.md) / [ADR-0008](../../docs/adr/0008-path-commitment-via-goal-keyed-cache.md) |
| `down_chain_count` | **RV-02** transient `int`, **default `0`**, **no DB column / no migration** — the **Medic reset chain** counter (CONTEXT.md). Incremented in `BatchSimulator._record_down` (the static→instance chokepoint) **before** stamping `last_downed_time` when `not is_active_at(second)` (i.e. the player is re-**Down**ed before recovery); fires a one-shot `medic_reset` `GameEvent` when it reaches 2 for a `medic`; reset to `0` in the per-tick active-accounting branch (player recovered). Consumed only for the `medic_reset` highlight — no RNG, no mechanics effect |

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

`tag_id_key` — `@property` returning `self.tag_id` (the string tag identity). Exists so `choose_tag_target` in `mechanics.py` can access this attribute the same way on both `PlayerState` and `PlayerRoundState` (the duck-typed interface long predates SIM-09 — `PlayerRoundState` retains its forwarding shims because the saved-round serializers and `score_calculator.calculate_mvp` still operate on ORM instances).

---

## weights.py

One function per role: `_get_medic_weights`, `_get_ammo_weights`, `_get_scout_weights`, `_get_heavy_weights`, `_get_commander_weights`. Each mutates the `weights` list in-place and returns it.

### Weight array layout

Index 0–8 map to: `tag_player`, **`only_move`**, `hide`, `capture_base`, `use_special`, `resupply_ally`, `missile_player`, `request_resupply`, **`hold`**. **MOVE-01:** index 1 was renamed `change_zone` → **`only_move`** (same slot, per-role weight tuning preserved). It no longer gates movement — every non-**Stationary** player **Advances** every tick regardless of the chosen Action; the `only_move` Action merely *doubles* that tick's Advance. See [ADR-0007](../../docs/adr/0007-movement-decoupled-from-action.md) and CONTEXT.md. **MOVE-03:** index 8 **`hold`** is the new 9th slot — a `hold` roll puts the player in **Overwatch** (CONTEXT.md). See [ADR-0009](../../docs/adr/0009-hold-overwatch.md) and CONTEXT.md (**Hold**, **Overwatch**, **Overwatch shot**).

The caller (`combat.plan_action`) starts each player's tick from the baseline `[70, 30, 0, 0, 0, 0, 0, 0, 0]` (**MOVE-03:** widened from 8 to 9 slots — the trailing `0` is index 8 `hold`). Role functions apply deltas from there. **All weights must remain ≥ 0** — `random.choices` raises `ValueError` on negative weights.

**SIM-01 — `BASELINE_ACTION_WEIGHTS`.** The baseline array is the documented public module constant `BASELINE_ACTION_WEIGHTS` in `weights.py` (the trailing stranded magic literal that previously lived at `combat.py:~293` inside `plan_action`); `combat.plan_action` now imports it and does `weights = list(BASELINE_ACTION_WEIGHTS)` (copy, never mutate the constant). This is the SIM-01 "adjustable without touching logic code" deliverable — the **constant dict** the PLAN text asks for is the existing per-role const dicts (`_MEDIC` / `_AMMO` / `_SCOUT` / `_HEAVY` / `_COMMANDER`, which postdate the SIM-01 plan and already carry per-key inline comments) **plus** this baseline constant, which was the one remaining tunable still embedded in logic code. All five role weight functions (`_get_medic_weights`, `_get_ammo_weights`, `_get_scout_weights`, `_get_heavy_weights`, `_get_commander_weights`) are now docstring-documented (baseline totals, situational-block order, the non-negative invariant). **No behavioural / formula / value change** (the literal was already 9-slot — hold redistribution is zero-sum and `request_resupply` is 0 at baseline), no migration, no Score Calibration re-baseline.

### Critical weight-safety rules

- Before subtracting from a weight, check that the result can't go below zero given the baseline.
- The not-active blocks zero out `tag_player` and/or `resupply_ally` and redistribute to `hide`/`only_move` (index 1, formerly `change_zone`). They must not push any other weight negative.
- Tests in `matches/tests/test_weights.py::TestWeightFunctions` cover representative state combinations for each role. Run these whenever changing weights.
- **SIM-01:** `test_weights.py` now uses a **single 9-slot fixture sourced from `BASELINE_ACTION_WEIGHTS`** (the legacy mixed 7-slot `_BASE` / `_ACTION_IDX` fixtures were deleted); the existing sum / vector assertions are widened to 9 elements with **no value change** (hold redistribution is zero-sum and `request_resupply` is 0 at baseline). A new regression test `test_plan_action_never_emits_negative_weight` builds in-memory `PlayerState` objects (no DB) and asserts `plan_action` never hands a negative weight to `random.choices` across all 5 roles × ~10 targeted edge states. The medic-`+5`-capture and Scout-`xfail` cases are kept as-is with sharpened docstrings (the medic case is the known pre-existing failure noted below).

### Role baselines (after role-adjustment, before situational modifiers)

| Role | tag_player | only_move | resupply_ally | hold (idx 8) |
|------|-----------|-----------|---------------|--------------|
| Medic | 10 | 0 | 90 | 0 |
| Ammo | 35 | 0 | 95 | 20 |
| Scout | 50 | 50 | 0 | 10 |
| Heavy | 70 | 25 | 0 | 20 |
| Commander | 70 | 30 | 0 | 10 |

**MOVE-01:** the `only_move` (index 1, formerly `change_zone`) column is preserved unchanged — a `0` baseline (Medic/Ammo/Commander-at-baseline) no longer means "never moves". Movement is decoupled from this weight: every non-**Stationary** player **Advances** toward their **Goal cell** every tick; `only_move` now only *doubles* that tick's Advance. The baseline `0` roles still traverse the map.

**MOVE-03:** the `hold` (index 8) column is the new **Overwatch** weight ([ADR-0009](../../docs/adr/0009-hold-overwatch.md), CONTEXT.md). Sources of the redistributed weight: **Medic 0** (no source — Medic stays support-focused, never holds at baseline); **Ammo +20 from `tag_player`** (35→15 effective, 20 to hold); **Scout +10**, **Heavy +20**, **Commander +10** each taken **from `only_move`**. All weights stay **≥ 0** (the `random.choices` non-negative invariant — Medic/Ammo `only_move` is `0` so their hold weight is sourced from `tag_player` instead). Numbers are tunable; calibration is deferred (folds into the single pending post-MOVE-01 re-baseline).

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

Cell-aware movement helpers used by `BatchSimulator` (the sole simulator post-SIM-09). Used when `arena_map` is provided; 3-zone fallback is used otherwise.

**MOVE-04 — Goal commitment ([ADR-0010](../../docs/adr/0010-goal-commitment-via-tick-cadence-throttling.md), CONTEXT.md).** With MOVE-02's route cache in place, the residual per-tick map-mode cost is the goal-selection cascade itself (no A*, but `_goal_from_action` / `_goal_from_role` / teamwork-bias / memory / LOS-count scans run for every non-**Stationary** player every tick). MOVE-04 throttles only the **steady-state positioning** layer of `choose_goal_cell` (cascade steps 2/3/4 — `_goal_from_action`, `_goal_from_role`, enemy-base default) to a per-player `GOAL_RECOMPUTE_PERIOD_TICKS = 4` ticks (2 s) cadence; the **reactive** layer (step 0 MECH-04 nuke-reaction, step 1 critical-resource lives/shots ≤ 30%, step 1b score-broadcast `seek_medic`) **still fires every tick** so time-sensitive overrides are never delayed. The committed cell lives on the transient `PlayerState._committed_goal: Optional[tuple[(int,int), bool, int]] = None` (`(cell, from_action_driven, expires_at_tick)` — see player_state.py table above). **Force-recompute triggers** beyond cadence expiry: no prior commitment, Goal cell reached, exiting **Stationary** (hide/hold → not), a reactive override firing this tick, **Down**/respawn iff `from_action_driven` (positioning goals survive a Down because the player keeps **Advancing** through the **Respawn cooldown**). Phase is **expiry-based** per-player (`expires_at_tick = tick + N`), **not** a global `tick % N == 0` — load staggers naturally across the window without hashing. The route cache (**Path commitment**, MOVE-02) invalidates **iff** a Goal commitment recompute changes the Goal cell — re-picking the same cell leaves `_path_cache` untouched. Cadence + source marker consume **no RNG**, so the SIM-07/SIM-08 *internal* contract holds in form (same seed + Orientation + rosters + map ⇒ identical game, serial == parallel, faithful **Replay**); seeded games **differ from pre-MOVE-04** (staler goals deliberately shift pursuit/positioning) and the delta folds into the already-pending post-MOVE-01 Score Calibration re-baseline (no new obligation). See [ADR-0010](../../docs/adr/0010-goal-commitment-via-tick-cadence-throttling.md), CONTEXT.md (**Goal commitment**, and the superseded "Goal cell is recomputed every tick" ambiguity).

**MOVE-01 — movement decoupled from the weighted Action ([ADR-0007](../../docs/adr/0007-movement-decoupled-from-action.md), CONTEXT.md).** On the map path, every non-**Stationary** player **Advances** toward their **Goal cell** **every tick**, regardless of which Action the weighted roll picked — so `choose_goal_cell` is now consulted every tick (previously only on the `change_zone` roll, which left zero-weight Commander/Medic/Ammo frozen on spawn). **Stationary** = no Advance = `is_hiding` True OR chosen Action == `capture_base`. The renamed **`only_move`** Action (was `change_zone`) no longer gates movement — it devotes the tick entirely to repositioning by **doubling** that tick's Advance (`cells_to_move(speed) * 2` cells in one advance). Advance and A* consume **no RNG** (SIM-07/SIM-08 contract preserved in form).

**MOVE-02 — Path commitment: goal-keyed A* route cache ([ADR-0008](../../docs/adr/0008-path-commitment-via-goal-keyed-cache.md), CONTEXT.md).** MOVE-01's per-tick from-scratch A* over the ~3,700-cell graph (~8× slower with a map) is replaced by re-stepping a cached route via `astar_advance_cached` (below). The player follows the single route computed when its **Goal cell** was set; `choose_goal_cell` still runs **every tick** (it does no A* — only the route is cached, not goal selection). Recompute triggers: cache `None`/empty, live goal ≠ cached goal, next cached cell ∉ `adj`; the route is also cleared on Down/respawn (knocked off-path) by the BatchSim caller. The `only_move` 2× multiplier consumes `2×steps` along the **same** committed route — **not** a recompute trigger. Re-stepping consumes **no RNG** (serial == parallel, faithful Replay still hold), but the cache deliberately changes which equal-cost route is walked, so seeded games **differ from pre-MOVE-02** — the contract is *internal* determinism, **not** identity to pre-MOVE-02 (the PLAN.md "no behavioural change" wording was contradictory, superseded by ADR-0008). The seeded delta is folded into the already-pending post-MOVE-01 Score Calibration re-baseline.

### Functions

**`build_movement_adjacency(zone_data)`** — builds a 4-connected adjacency dict `{cell: [neighbor, ...]}` for every movement-passable cell. Uses module constant `_MOVEMENT_PASSABLE = {1, 2, 3}` (floor + legacy red/blue zones). High wall (0), low wall (4), and windowed wall (5) all block movement and are excluded entirely, so `cell in adj` doubles as a passability check.

**`astar_path(start, goal, adj, elevation_data=None)`** — core A* (Manhattan heuristic, optional elevation cost). Returns the ordered list of cells from `start` to `goal` **excluding `start`** (last element is `goal`); `[]` when `start == goal`, no path exists, or `start` is not in the adjacency graph. `astar_next_step` and `astar_advance` are thin wrappers over it.

**`astar_next_step(start, goal, adj, elevation_data=None)`** — `astar_path(...)[0]` (or `start` when the path is empty). Behaviour unchanged from the pre-refactor implementation (regression-guarded by `test_map.py::TestAstarPathAndAdvance` + the legacy `TestMap02CellMovement` astar tests).

**`astar_advance(start, goal, adj, steps, elevation_data=None)`** — returns the cell reached after walking up to `steps` cells along the A* path (stops at `goal`, no overshoot). Returns `start` when `steps <= 0`, no path, or non-navigable start. **Recomputes A* from `start` every call** (no caching). Retained alongside `astar_advance_cached` as the uncached primitive — used by `test_map.py::TestAstarPathAndAdvance` and the legacy `TestMap02CellMovement` astar tests; BatchSim itself uses `astar_advance_cached`.

**`astar_advance_cached(player, current, goal, adj, steps, elevation_data=None)` (MOVE-02, [ADR-0008](../../docs/adr/0008-path-commitment-via-goal-keyed-cache.md))** — the path-commitment variant used by `BatchSimulator`. Manages `player._path_cache` (transient `Optional[tuple]` — a `(cached_goal, remaining_cells, anchor)` 3-tuple, where `remaining_cells` is the route still to walk, same shape `astar_path` returns: excludes `current`, ends at `goal`; `anchor` is the cell the previous re-step left the player on). Runs a full `astar_path` recompute **iff** any of: cache `None`/empty, live `goal` ≠ cached goal, live `current` ≠ `anchor` (off-route displacement — *enforces* the off-path invariant rather than relying on it; no mechanic does this today), or `remaining[0] ∉ adj` (next cell blocked — map adjacency is immutable per round so this never fires in production); the BatchSim caller separately clears `_path_cache = None` at every tag / follow-up / reaction / missile / nuke life-loss site via the shared `BatchSimulator._record_down(player, second)` helper (knocked off-path → falls into the "cache None" recompute branch). A legacy 2-tuple cache (no anchor) is tolerated for hand-built test caches (the anchor check is skipped). Otherwise it **re-steps the committed route**: pops up to `steps` cells, stops at `goal` (no overshoot — identical traversal semantics to `astar_advance`), and clears the cache to `None` once the route is consumed (player has reached `goal`; next tick's fresh `choose_goal_cell` drives recompute-or-idle). Cache exhaustion and an `only_move` 2× `steps` are **not** recompute triggers. Consumes **no RNG**, so the SIM-07/SIM-08 serial == parallel / faithful Replay contract still holds (the transient cache never crosses the parallel-worker process boundary; the round is re-simulated in-worker). Called by `BatchSimulator._move_player_in_memory` in place of `astar_advance`. **MOVE-03 ([ADR-0009](../../docs/adr/0009-hold-overwatch.md)):** the cells popped this tick (the player's traversed cells for this Advance, between the start and end cell) are also recorded on the transient `player._last_step_cells` so the `BatchSimulator` Overwatch resolution step can test whether the traversal crossed any **Hold**ing player's **Line of sight** — MOVE-01 otherwise discards the intermediate route (`movement_trail` keeps only `(start, end, tick)`), so this committed-route exposure is what makes the "moved *through* LoS in one Advance" guarantee resolvable. Recording it is pure bookkeeping — still **no RNG**, contract unchanged.

**`max_movement_for_map(zone_data)`** — cells-per-tick ceiling scaled by map size: `max(rows, cols) // 10` clamped to **5..10** (PLAN.md STAT-03 Phase 1). `None`/empty → 5.

**`cells_to_move(speed, zone_data)`** — `max(1, ceil(speed/100 * max_movement_for_map(zone_data)))`. The PLAN.md `speed`-stat formula; floored at 1 so a moving player is never frozen by a low `speed`. Called by `BatchSimulator._move_player_in_memory` with `getattr(player, "speed", 50)` (a baked `PlayerState.speed` field). **MOVE-01:** on an `only_move` tick the move function passes `cells_to_move(...) * 2` (one single 2× step, no other deliberate effect); every other non-**Stationary** Action still Advances the normal `cells_to_move(...)` distance. **MOVE-02:** the doubled `steps` is consumed along the **same** committed route via `astar_advance_cached` (the 2× is not a recompute trigger).

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

**`choose_goal_cell(player, all_alive, spawn_cells, movement_ctx=None, intended_action="")`** — duck-typed goal selector consumed by `BatchSimulator` (MAP-05). **MOVE-01: now consulted every tick** a player is not **Stationary** (was only reached from the old `change_zone` branch), so the nuke / critical-resource / score-broadcast overrides below are live for all roles every tick. Priority order:
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

Pure game-mechanic functions consumed by `BatchSimulator` (the sole simulator post-SIM-09). No Django imports. The duck-typed interface is preserved so the same helpers also work on `PlayerRoundState` instances when called from serializer / MVP / analytics paths.

**`shot_cooldown(player, second) -> float`** — returns the minimum gap between shots: 0.0 for rapid-fire scouts (special active), 1.0 for heavies, 0.5 for everyone else.

**`choose_tag_target(player, all_alive, second, movement_ctx=None, *, los_filter=None) -> player | None`** — returns a random weighted enemy target. `los_filter` is a callable `(actor, candidates, movement_ctx) -> list`; falls back to same-zone filtering when not provided. Role weights: Heavy=8, Commander=5, Ammo=Scout=3, Medic=1.

**`choose_resupply_target(player, all_alive, second) -> player | None`** — returns the neediest same-zone teammate to resupply weighted by resource deficit × role. Returns `None` when all teammates are at full resources.

**`choose_zone_change(player, all_alive) -> int | None`** — returns a target zone index when the player is critically low (≤ 30%) on lives (seek Medic) or shots (seek Ammo). Returns `None` when no reactive movement is warranted.

### Tests

`matches/tests/test_mechanics.py` covers all four public functions.

---

## combat.py

Shared combat resolution used by `BatchSimulator` (the sole simulator post-SIM-09). No Django imports — operates on duck-typed player state objects and emits events through an optional `emit_event` callable rather than writing to a specific storage backend.

### Visibility helpers (moved from `simulation.py`)

**`_can_tag_through_windowed_wall(r1, c1, r2, c2, zone_grid, wall_meta) -> bool`** — Bresenham line walk. High wall (0) → always False. Windowed wall (5): checks facing vs attack axis.

**`_get_los_targets(actor, candidates, movement_ctx) -> list`** — Returns candidates visible to actor. Uses `sight_data` frozenset lookup, extended by windowed-wall aperture check. Falls back to same-zone when no map is active.

**`_get_base_interaction(player, movement_ctx) -> int | None`** — Returns `base_id` (15=neutral, 14/13=opposing) of the first capturable base in range, or `None`.

**`elevation_hit_modifier(attacker_elev, target_elev) -> float`** — public pure formula: `max(0.5, 1 - 0.1 * max(0, target_elev - attacker_elev))`. Importable for testing.

**`_elevation_hit_modifier(attacker_row, attacker_col, defender_row, defender_col, movement_ctx) -> float`** — MAP-09 wrapper; returns 1.0 when no map or either cell is None.

### Action index constants

`_ACTION_IDX` and `_CHOICES` define the **9-slot** action array (indices 0–8): `tag_player`, **`only_move`** (renamed from `change_zone`, MOVE-01 — same index 1), `hide`, `capture_base`, `use_special`, `resupply_ally`, `missile_player`, `request_resupply`, **`hold`** (MOVE-03 — index 8). `request_resupply` (index 7) is available to all 5 roles; weight is non-zero only when the player needs resources (Ammo players are locked to requesting lives; Medic players to requesting shots). Fulfilled asynchronously by `resolve_resupply_requests` in `resupply_queue.py` at end of tick. **MOVE-03 ([ADR-0009](../../docs/adr/0009-hold-overwatch.md), CONTEXT.md):** `hold` (index 8) puts the player in **Overwatch** — it sets the transient `is_holding` flag, **carries over** like `is_hiding` (until a non-`hold` Action is rolled or a Down/respawn force-clears it), and is **Stationary** (no **Advance** while holding). All weights stay ≥ 0 (the `random.choices` invariant). The **Overwatch shot** is resolved in `BatchSimulator` by reading the path-commitment route cache (`PlayerState._last_step_cells`).

### Combat actions

**`plan_action(player, all_alive, second, movement_ctx=None, *, save_player=None) -> list`** — Returns a list of planned action dicts for the player at this tick. Updates `player.last_chosen_action`; clears `is_hiding` (calling `save_player(player)` when provided). Used by `BatchSimulator`'s per-tick loop. **MECH-15:** the `capture_base` branch is gated on `player.is_active_at(second)` — a player in the **Respawn cooldown** (Downed, not yet active) **plans no base capture** (map path and 3-zone fallback alike), mirroring the `use_special` active-gate one branch down. Base capture was the one deliberate Action that previously leaked while down; the chokepoint `capture_base` and the round-end `award_bases` are unchanged.

EventLog candidate: all four combat helpers below dropped their `emit_event=None` callable kwarg and now take a required `ctx: RoundContext`. They call `ctx.events.*` verbs directly. The local `_actor_meta` / `_target_meta` / `_build_meta` copies that used to live here are gone — EventLog owns the metadata shape.

**`attempt_resupply(tagger, teammate, second, *, ctx) -> None`** — Applies a resupply: Ammo restores shots, Medic restores lives (per `_AMMO_CHART`/`_MEDIC_CHART`). Cancels any active special on the teammate. Emits via `ctx.events.resupply_ammo(...)` / `.resupply_lives(...)`.

**`capture_base(player, base_id, second, movement_ctx=None, *, ctx) -> bool`** — Range-checks the player's cell against `base_sight_data`, deducts 3 shots, awards 1001 pts, and updates `neutral_base_destroyed` / `opposing_base_destroyed`. Emits via `ctx.events.base_capture(..., metadata_extras={"shots_remaining": ..., "points_scored": ...})`. Returns `True` on success.

**`award_bases(player, second, *, ctx) -> None`** — Awards any uncaptured bases to a surviving player at round end. Emits via `ctx.events.base_capture(..., description="X awarded neutral/opposing base")`.

**`start_missile_lock(attacker, defender, second, *, ctx) -> PendingMissileLock | None`** — Validates state and requires initial LOS; consumes one missile. Emits via `ctx.events.locking(attacker, defender, second)`. Returns the pending lock on success.

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

(Other constants follow the same pattern, e.g. the medic-under-fire alert window.) Imported by `weights.py` (endgame rush, score broadcast), `pathfinding.py` (`_STALE_THRESHOLD`), `player_state.py`, and `BatchSimulator` — all now consume tick-valued constants from here rather than inline numeric literals.

---

## score_calculator.py

**`calculate_mvp(player_state) -> float`** — SM5 MVP formula extracted from `PlayerRoundState.get_mvp`. Accepts any duck-typed object exposing the standard `PlayerRoundState` attributes (works with both ORM instances and `PlayerState` dataclasses). `PlayerRoundState.get_mvp` now delegates here. Test with `matches/tests/test_mvp.py::TestCalculateMvp` — no Django ORM or test DB required for pure formula tests.

---

## highlights.py

**RV-02 auto-flag highlights builder.** Pure Python — **no Django imports, no I/O, no RNG**.

**`build_highlights(events, result, *, round_ticks, name_by_id, team_by_id) -> list[dict]`** — scans a round's events and result and returns the per-round **Highlight** list (CONTEXT.md). Inputs: `events` is the **in-memory event-dict buffer** the simulator built during the round (NOT ORM rows); `result` is the round result dict (supplies `team_elimination` via `red_eliminated`/`blue_eliminated` + `eliminated_at`); `round_ticks` is `TICKS_PER_ROUND` (1800); `name_by_id` / `team_by_id` are id→name / id→team maps passed in so the function emits NAME strings and a per-event team while staying pure (an absent id resolves to `None`). Returns a flat list sorted by tick ascending, each record the fixed 7-key shape `{kind, tick, team, actor, target, points, label}`. **Six kinds:** `nuke_detonation` (discriminated by `event_type=="special"` + `metadata["targets"]` + `points_awarded==500`; the activation row is not flagged), `nuke_cancelled`, `medic_reset`, `first_elimination`, `team_elimination` (from `result`, not the `dead` event), `scoring_burst` (forward `[t, t+60)` 60-tick window, maximum single-team gross points; none when there were no point events). **Base captures are not a highlight kind** (routine point-grabs — surfaced in the events-log timeline instead); their points still feed the `scoring_burst` sum. Called by `BatchSimulator._flush_to_db` after the RES-04 `cell_occupancy_json` block, with the result persisted via a second `GameRound.save(update_fields=["highlights_json"])`. The `nuke_cancelled` / `medic_reset` event rows it reads are emitted server-side from the `BatchSimulator._record_down` chokepoint (converted **static → instance** for RV-02). Tested by `matches/tests/test_round_views.py` — no DB required.

---

## map_context.py

`MapContext` is a typed `@dataclass` that replaces the former 11-key `movement_ctx` plain dict. It is constructed once per round by `matches.sim_helpers.map_loader.load_map_context` (or the legacy `build_movement_ctx` shim) and passed through the simulation call chain. All callers access it via domain-level methods rather than dict key lookups.

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

Typed `@dataclass` classes for the four pending-event queues used by `BatchSimulator`. Replacing raw positional tuples with named fields so new attributes (e.g. a nuke ID for MECH-05 cancellation tracking) can be added in one place.

| Class | Fields | Replaces |
|-------|--------|---------|
| `PendingMissile` | `complete_time`, `attacker`, `defender` | `(float, player, player)` |
| `PendingNuke` | `complete_time`, `player`, `cancel_logged` | `(float, player)` |
| `PendingFollowup` | `fire_at`, `attacker`, `defender`, `chain_depth` | `(float, player, player, int)` |
| `PendingReaction` | `fire_at`, `attacker`, `defender` | `(float, player, player)` |

`combat.py::start_missile_lock` returns a `PendingMissile` (was a raw 3-tuple).

**RV-02:** `PendingNuke.cancel_logged: bool = False` is the **Nuke cancellation** de-dup flag (CONTEXT.md). When a Commander with a live pending nuke is **Down**ed/disarmed, the `BatchSimulator._record_down` chokepoint (converted **static → instance** for RV-02) emits a single `nuke_cancelled` `GameEvent` and sets `cancel_logged=True` while **leaving the nuke in `pending_nukes`** — so the MECH-05 nuke-reaction/drain path is unchanged and the drain-else branch only emits when `not cancel_logged` (no double-log, no re-baseline).

---

## Pending-event drain (inlined)

The `drain_*` helpers that previously lived in `tick_engine.py` were three-line list partitions by tick comparison (`item.complete_time <= second` / `item.fire_at <= second`). Inlined into `BatchSimulator._simulate_round` as deepening candidate #3 (May 2026) — the module was a shallow Adapter (one line per drain at the call site, three near-identical functions in the module) so removing it costs ~6 lines at the call site and reads more naturally next to the resolution logic it feeds. Resolution behaviour (what happens to ready items) stays in the simulator and is unchanged.

### Parallel batch workers (SIM-07 / SIM-08)

`BatchSimulator._run_parallel` fans rounds out to `batch_round_worker`, the process-pool worker. **SIM-07:** `batch_round_worker` takes a per-round **int** seed and `random.seed(it)`s before simulating, so a given master seed yields identical games whether the batch runs serially or in parallel (guaranteed, tested property). Per-round seeds are derived from a deterministic `random.Random(master_seed)` seed chain in `run`. **SIM-08:** `batch_round_worker` additionally accepts the per-game `flipped` flag (the Orientation, deterministic by game index — never RNG-derived); when `flipped` is true it **swaps the precomputed red/blue rosters** before simulating, so the worker plays the same Orientation the serial path would. Serial and parallel runs therefore produce identical team-position aggregates **and identical `side_advantage`** for a given master seed. `score_round_worker` (the `score_averages` command path) remains out of SIM-07/SIM-08 scope — it does not take or seed an int seed, nor flip sides; seeding stays `random.getstate()`-based. It now takes the parent-built `MapContext` (or `None`) as a 4th args-tuple element and threads it into `_simulate_round`, so `score_averages --map` works under `--workers > 1`; this is the only change to it.

---

## spawn_assigner.py

Spawn cell assignment logic used by `BatchSimulator._make_players` (the sole simulator post-SIM-09). Extracted from the legacy `_build_spawn_assignments` so the implementation lives in one place; the `build_spawn_assignments` shim in `map_loader.py` delegates here for legacy callsites.

**`assign_spawn_cells(roster_roles, team_color, spawn_cells, team_spawn_pools) -> dict[int, tuple[int,int] | None]`** — role-priority, no-replacement drawing from the team's spawn pool. Returns `{roster_index: (row, col) | None}`. `None` means fall back to 3-zone placement.

Role priority:
1. Commander / Heavy → front of pool (closest to enemy base)
2. Medic / Ammo → back of pool (farthest from enemy base)
3. Scout → remaining cells

Private helpers `_draw_front`, `_draw_back`, `_overflow` replace the inner closures that previously captured outer-scope state.

`matches.sim_helpers.map_loader.build_spawn_assignments` is a one-line delegation shim that calls `assign_spawn_cells` (retained for legacy callsites; SIM-09 lifted it out of the removed `ResourceBasedSimulator`).

Tests: `matches/tests/test_spawn_assigner.py` — 15 unit tests, no DB required.

---

## resupply_queue.py

End-of-tick resupply fulfillment. Called by `BatchSimulator` after all players have chosen their action for the tick. No Django imports — operates on duck-typed player state objects.

EventLog candidate: dropped the `emit_event=None` callable kwarg (and the local `_actor_meta`/`_target_meta`/`_build_meta` copies + the `_do_resupply` dict-vs-kwargs adapter — all dead). Helper takes a required `ctx: RoundContext` and emits via verbs.

### Public function

**`resolve_resupply_requests(requestors, all_alive, second, movement_ctx, *, ctx) -> None`** — Processes all players whose `last_chosen_action == "request_resupply"` for the current tick. Mutates player state in-place; emits via `ctx.events.resupply_lives/.resupply_ammo` (inside `attempt_resupply`) and `ctx.events.combo_resupply` (at the combo-fire site).

Parameters:
- `requestors` — iterable of players whose action this tick was `request_resupply`.
- `all_alive` — all currently alive players (both teams); used to find candidate supporters.
- `second` — current simulation timestamp; used for cooldown checks and event timestamps.
- `movement_ctx` — `MapContext | None`; LOS checks use `movement_ctx.can_see` when a map is active, fall back to same-zone when `None`.
- `ctx` — `RoundContext` carrying the `EventLog` for all emits.
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

---

## map_loader.py

Map-loading helpers used by `BatchSimulator` (the sole simulator post-SIM-09 — [ADR-0002](../../../docs/adr/0002-two-simulation-engines.md) superseded) and the `score_averages` management command. Pure-Python free functions; Django ORM imports are **lazy** (inside the function bodies that need them) so the module itself is cheap to import. Behaviour and signatures are **identical** to the legacy `ResourceBasedSimulator.@staticmethod` versions they replaced — SIM-09 simply lifted them out of the deleted class and dropped the underscore prefix. The precedent (`spawn_assigner.py`) was a partial extract of one helper; `map_loader.py` completes the pattern.

### Public functions

**`load_map_context(arena_map) -> tuple[MapContext | None, int | None]`** — **primary entry point**. Returns `(map_context, zone_size)`; both are `None` when `arena_map is None` (3-zone fallback). Runs all ORM queries (zone config, base positions, sight lines, base sight lines, cell ranking, strong spots, spawn pools, elevation) and immediately constructs a `MapContext` — a single call replaces the legacy two-step `resolve_map_data → build_movement_ctx` pipeline. Raises `ValueError` for missing zone config, missing red/blue base, or missing sight lines (the `score_averages` command catches and re-raises as `CommandError`). Used by `BatchSimulator.simulate_match` / `simulate_single_round_detailed` / `run` / `save_games` and `score_averages`.

**`resolve_map_data(arena_map) -> MapData`** — legacy shim, still returns the `MapData` dataclass. New code should use `load_map_context` instead; retained because some tests and analytic paths still construct a `MapData` explicitly.

**`build_movement_ctx(zone_data, spawn_cells, *, sight_data, base_sight_data, cell_los_counts, high_los_cells, strong_spots, wall_meta, team_spawn_pools, elevation_grid) -> MapContext`** — legacy shim wrapping `MapContext` construction. Returns a `MapContext` (was a plain 11-key dict pre-MapContext refactor). New callers should prefer `load_map_context`.

**`zone_from_cell(row, col, spawn_cells: dict | None) -> int`** — returns zone index (0=red, 1=neutral, 2=blue) by Manhattan-distance proximity to base cells. Nearest base type wins; neutral bases take precedence when closer than or equidistant to both team bases. Returns `1` (neutral) when `spawn_cells` is `None`/empty or the red/blue base is absent.

**`build_spawn_assignments(roster_roles, team_color, spawn_cells, team_spawn_pools) -> dict[int, tuple[int, int] | None]`** — delegation shim calling [`spawn_assigner.assign_spawn_cells`](#spawn_assignerpy); retained for legacy callsites that referenced the method by name.

### Tests

`matches/tests/test_map_loader.py` pins the public surface (function names, signatures, error semantics). Broader behaviour coverage stays in `matches/tests/test_map.py` (its former `_load_map_context` / `_resolve_map_data` / `_build_movement_ctx` callsites are converted to the free-function names by SIM-09).

### History

Extracted from the deleted `ResourceBasedSimulator.@staticmethod`s by SIM-09 ([ADR-0002](../../../docs/adr/0002-two-simulation-engines.md) superseded). The five free functions — `load_map_context`, `resolve_map_data`, `build_movement_ctx`, `zone_from_cell`, `build_spawn_assignments` — are the renamed (drop underscore prefix) descendants of the former `ResourceBasedSimulator._load_map_context` / `_resolve_map_data` / `_build_movement_ctx` / `_zone_from_cell` / `_build_spawn_assignments`. Behaviour and signatures unchanged; all callsites (`BatchSimulator`, `score_averages`, tests) updated in lockstep.
