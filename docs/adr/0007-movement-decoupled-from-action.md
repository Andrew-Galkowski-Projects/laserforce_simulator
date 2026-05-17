# Movement decoupled from the weighted Action; `only_move` doubles the Advance

**Status:** accepted

Before MOVE-01, a player only moved on a tick when the weighted **Action** roll
picked `change_zone`. That weight is **0** at baseline for Commander, Medic, and
Ammo, so on a real **Arena map** those three roles never left their **Spawn
cell** for the entire **Round** (measured: Commander 1,032 vs 9,952 target; Ammo
33 vs 3,242). The nuke / critical-resource / score-broadcast goal overrides live
*inside* `choose_goal_cell`, which was only reachable from the `change_zone`
branch, so they were dead for those roles too.

MOVE-01 makes the goal-directed cell step (**Advance**) an **always-on per-tick
behaviour**, independent of the weighted Action. Every non-**Stationary** player
Advances toward their **Goal cell** every tick; `choose_goal_cell` is consulted
every tick. **Stationary** = hiding (`is_hiding`) or capturing a **Base**
(`capture_base` Action) — the only two states that freeze a player in place; all
other Actions Advance while they act. The legacy `change_zone` Action is renamed
**`only_move`** and redefined: it no longer gates movement (every tick already
moves) — it devotes the tick entirely to repositioning by **doubling** that
tick's Advance distance, applying no other deliberate effect. Each cell stepped
through accumulates into a per-player **Movement trail** (ordered cell list, the
basis for the future RES-04 heatmap).

**Considered options:**
(a) *Keep `change_zone`/move as a normal weighted Action that simply gates
movement* — rejected: this is the broken status quo (zero-weight roles never
move).
(b) *Drop "move" from the Action set entirely; movement always-on with no
movement-flavoured Action; redistribute the freed `change_zone` weight across
the remaining Actions* — rejected: forces a full per-role weight redistribution
with no obvious target split, discards a useful "commit fully to repositioning"
signal, and makes the Score Calibration re-baseline harder to reason about
(every role's whole weight vector changes, not just the movement term).
(c) **(chosen)** *Keep the Action as `only_move`, always-on Advance, `only_move`
doubles that tick's Advance* — preserves the existing per-role weight tuning
(the slot still exists, only its meaning changes from "move at all" to "move
twice as far"), keeps a meaningful repositioning signal, and bounds the
behavioural change so the Score Calibration re-baseline is interpretable.

**Consequence:** every seeded outcome shifts — all roles now traverse the map,
collapsing the spawn-camped degenerate games, so the **Score Calibration
Targets** (tuned on the non-spatial 3-zone model) must be re-baselined against
the map model as part of MOVE-01. This is hard to reverse for the same reason
TIME-01's tick-precision change was ([ADR-0001](0001-time-unit-seconds-now-tick-native-later.md)):
re-coupling movement later would again move every seeded result and invalidate
the re-baselined targets. The SIM-07/SIM-08 determinism contract is unaffected
in *form* — same (seed, orientation, rosters, map) ⇒ identical game still holds —
but the games a given seed produces are different from pre-MOVE-01 (no backfill,
[ADR-0004](0004-simulation-data-is-disposable.md)). Domain terms (**Advance**,
**only_move**, **Stationary**, **Movement trail**) are defined in CONTEXT.md;
the `change_zone` → `only_move` rename is recorded there under Flagged
ambiguities. The performance cost of every role now moving (more A* per tick) is
deferred to MOVE-02 (goal/path caching).
