# Path commitment: cached goal-keyed A* route, not per-tick re-planning

**Status:** accepted

MOVE-01 made the goal-directed cell step (**Advance**) always-on per tick. Its
performance cost was deferred to MOVE-02: `astar_path` runs a full from-scratch
A* over the ~3,700-cell passable graph **every move tick** just to take one
tick's worth of steps (measured: 2,752 ms/round with a map vs 354 ms/round on
the 3-zone fallback, ~8×). MOVE-02 caches the chosen **Goal cell** and its A*
path per player and re-steps the cached path each move tick, recomputing only
when the goal changes, the next cached cell is blocked, or the player is knocked
off-path (Down/respawn).

**Scope: `BatchSimulator` only.** The ~8× cost and the perf-critical paths
(`score_averages`, batch win-rate) are BatchSim. `ResourceBasedSimulator` is
DB-bound (~9 s/round; A* is not its bottleneck) and is removed as dead code by
the immediately-following SIM-09, so it is deliberately **not** cached — it
retains per-tick recompute for its short remaining life. RBS and BatchSim
already produce different games (RBS second-internal vs BatchSim tick-native;
no RBS≡BatchSim identity contract exists), so the brief RBS/BatchSim route
divergence before SIM-09 introduces no new guarantee break.

A grid has **many equal-cost shortest paths**. The pre-MOVE-02 code recomputes
`astar_path(current, goal)` from the *new* current cell every tick, so a player
could re-pick a different equal-cost route each tick (the path "wobbled"). A
goal-keyed cache instead commits the player to the single route computed when
the goal was set. `astar_path`'s heap orders on `(f, g, (r,c))` int tuples, so
both behaviours are fully deterministic and PYTHONHASHSEED-independent — the
SIM-07/SIM-08 *internal* contract (same seed + Orientation + rosters + map ⇒
identical game, serial == parallel) holds under either. But the two produce
**different cell sequences** on real maps, hence different LOS, Tags, and every
seeded outcome. A "pure performance, identical-games" cache is therefore **not
achievable for free**.

**Considered options:**
(a) *Recompute per tick but memoize* — rejected: the start cell changes every
tick so a `(current, goal)` key never hits within a traversal; caching the
whole path then verifying the fresh recompute equals the cached suffix still
pays the full A* each tick (no speedup).
(b) *Force bit-identity to MOVE-01* — rejected: would require a canonical A*
whose shortest path is provably *suffix-stable* (path from `C_k` == `C_k`-onward
of the path from `C_0` for every `C_0`). General grids do not admit a cheap
tie-break with that property, and adopting one would itself move every seeded
result — the very thing it set out to avoid.
(c) **(chosen)** *Path commitment* — the player follows the route computed when
its **Goal cell** was set, re-planning only on invalidation (goal change,
blocked next cell, knocked off-path). Captures essentially all of the ~8× win
(`choose_goal_cell` stays per-tick — it runs no A* — so goal selection is
unchanged), is more realistic (no 0.5 s re-planning), and stays fully
internally deterministic.

**Consequence:** seeded games differ from pre-MOVE-02 (no backfill,
[ADR-0004](0004-simulation-data-is-disposable.md)). This delta is **absorbed by
the post-MOVE-01 Score Calibration re-baseline that is already pending** (see
`matches/CLAUDE.md` Score Calibration Targets note) — MOVE-02 creates **no new**
re-baseline obligation. The contract MOVE-02 must hold is *internal*
determinism, not identity to MOVE-01 games; the "no behavioural change /
identical games" wording in the PLAN.md MOVE-02 entry was contradictory and is
superseded by this ADR. Hard to reverse for the same reason as
[ADR-0007](0007-movement-decoupled-from-action.md)/[ADR-0001](0001-time-unit-seconds-now-tick-native-later.md):
returning to per-tick re-planning would again move every seeded result and
invalidate the re-baselined targets. Goal-recompute throttling (a *behavioural*
perf lever — staler goals) is explicitly **out of MOVE-02 scope**, parked as
MOVE-04. The `hold`/overwatch action is split to MOVE-03. Domain term **Path
commitment** is defined in CONTEXT.md.
