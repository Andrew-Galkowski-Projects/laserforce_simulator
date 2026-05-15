# Time unit: seconds-at-the-boundary now, tick-native migration deferred

**Status:** accepted — deferral phase closed 2026-05-15; TIME-01 now executing (see Amendment below)

The simulator advances in 0.5 s ticks but persists and displays all time in seconds (`db_second = int(second)`, `was_eliminated_at`, the SIM-06 `seconds_*` uptime fields). The accepted long-term direction is a fully **tick-native** internal model where seconds exist only at UI display boundaries (divide by 2), because mixing tick- and second-valued quantities silently corrupts derived analytics — e.g. `score_averages` sums `seconds_active` (would be ticks) against dead-time derived from `was_eliminated_at` (seconds), producing wrong percentages for any player who dies mid-round.

We deliberately did **not** do the tick-native conversion as part of SIM-06. SIM-06's scope is "close `_flush_to_db` field gaps"; going tick-native touches 22+ files including game-logic constants (`weights.py` endgame rush at `second >= 840`, score broadcast at `360`, MECH-06 staleness 60 s/15 s, the 8 s respawn, the STAT-03 stamina schedule) and `GameEvent.timestamp`, which is the backbone of the as-yet-unbuilt SIM-05 replay / RES-02 / RV-01 analytics. Folding that into SIM-06 would convert a ~5-file gap-closure into a cross-cutting rewrite with silent-corruption risk in features that don't exist yet — the failure mode Phase 0 exists to prevent.

**Considered options:**
- *Store raw ticks in `seconds_*` now* — rejected: corrupts `score_averages` percentages immediately; field name lies; every future consumer (RES-04, HX-01) re-inherits the bug.
- *Full tick-native migration inside SIM-06* — rejected: blast radius far exceeds the task; large unreviewable PR; blocks Phase 3.5 (SIM-07/08/09) on unrelated work.
- *Seconds now, tick-native as its own tracked task + this ADR* — accepted.

**Consequences:** SIM-06 stores the `seconds_*` fields in seconds (float `+= 0.5` accumulation truncated once by `IntegerField` at flush; ≤0.5 s/round error, consistent with the existing `int(second)` convention). A new PLAN.md item (TIME-01) tracks the tick-native migration; it must land **before** Phase 4 builds anything on `GameEvent.timestamp` so the timestamp unit is settled before replay/analytics depend on it.

---

## Amendment (2026-05-15): TIME-01 execution decisions

The deferral phase is closed; TIME-01 is being implemented. The grill resolved five decisions, two of which are hard to reverse and belong in this record:

1. **Tick-precision is genuine, not cosmetic.** Today both simulators loop in 0.5 s ticks but collapse every comparison to integer seconds via `db_second = int(second)`. Tick-native evaluates respawn/reset/fuse/cooldown edges at tick granularity. This *changes simulation outcomes* by sub-second fractions. Accepted deliberately — it is the correctness gain the ADR existed for. **Consequence:** every fixed-seed test asserting absolute point/elimination/event totals is re-baselined as part of TIME-01. The `test_same_seed_produces_identical_event_log` determinism harness still holds (it asserts seed→identical, not pinned values).

2. **The REST API returns raw ticks** (hard to reverse). `GameEvent.timestamp` and the renamed `ticks_*` `PlayerRoundState` fields are exposed as-is by the shipped `/api/` endpoints. The API is the data layer, not a display surface; the `÷2`-to-seconds conversion happens *only* at HTML templates and the `score_averages` / `game_analysis` CLI. Rejected: serializer-side `÷2` (re-introduces the unit-mixing this ADR fights and gives future Angular pre-divided data it cannot invert). This inverts the pre-TIME-01 CONTEXT.md rule that "all persisted and displayed time is seconds" and is locked before Phase 8 Angular consumes it.

3. **Single source of truth for tick constants.** All ~12 absolute time constants move to a new zero-dependency `matches/sim_helpers/time_constants.py` (e.g. `TICKS_PER_ROUND=1800`, `RESPAWN_TICKS=16`, `NOT_TARGETABLE_TICKS=8`, `ENDGAME_TICKS=1680`, `SCORE_BROADCAST_PERIOD_TICKS=360`, staleness `120`/`30`). The constant-by-constant audit becomes one reviewable file and future raw-seconds regressions are blocked at import.

4. **Survived sentinel `901 → 1801`** (numeric, ticks+1), applied uniformly to `PlayerRoundState.was_eliminated_at`, `Match.round1_eliminated_at`, `Match.round2_eliminated_at`, `GameRound.eliminated_at`; dead-time derivation `900 - x → 1800 - x`. Nullable-sentinel rejected as a wider semantic change than a unit migration.

5. **Constructor arg is ticks.** `ResourceBasedSimulator(duration=…)` → `duration_ticks=…`; the two callsites become `duration_ticks=40` / `120`. Code-facing arguments are ticks like everything else; only human-rendered output is seconds. The proportional stamina schedule (`int(second / round_duration * 100)`) is unit-agnostic and needs no constant conversion — only the renamed tick-valued round duration.
