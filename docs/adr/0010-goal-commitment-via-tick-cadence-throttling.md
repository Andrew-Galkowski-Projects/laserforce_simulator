# Goal commitment: tick-cadence throttling of steady-state goal recompute

**Status:** accepted
**Date:** 2026-05-20

## Context

MOVE-01 made the goal-directed cell step (**Advance**) always-on per tick and
made `choose_goal_cell` consulted every tick for every non-**Stationary**
player. MOVE-02 (**Path commitment**,
[ADR-0008](0008-path-commitment-via-goal-keyed-cache.md)) cached the *route* to
the goal so per-tick A* re-planning was eliminated — but goal *selection* itself
still runs every tick, by design (the route cache invalidates iff the goal
changes; goal selection runs no A*).

The remaining per-tick cost is `choose_goal_cell`'s own cascade — `_goal_from_action`,
`_goal_from_role`, teamwork-bias filtering, memory lookups, LOS-count scans for
high/low-LOS candidates — multiplied by every alive player on every tick of the
1,800-tick round. On real maps this is a measurable share of the residual map-mode
cost after MOVE-02. The PLAN.md MOVE-04 entry parked goal-recompute throttling
as a *behavioural* perf lever, to be opened only if path caching alone was
insufficient — that condition is met.

The cascade is not uniform. Steps 0/1/1b are *reactive*: MECH-04 nuke-reaction,
critical-resource (lives or shots ≤ 30% → seek medic/ammo), score-broadcast
`seek_medic`. They override the steady-state goal precisely because their
trigger is time-sensitive — a nuke fuse is ~16 ticks, a low-lives player needs
the medic *now*. Steps 2/3/4 are *steady-state positioning*: action-driven
(tag/missile/resupply/hide target), role-positioning (Scout→high-LOS,
Heavy-healthy→strong spot, Medic→low-LOS-near-Heavy, etc.), and the enemy-base
default. These survive multi-tick staleness: a Scout's "nearest high-LOS cell"
doesn't change in 2 s; a Commander's enemy-medic target shifts on the order of
seconds, not ticks.

## Decision

Throttle only the steady-state positioning layer. `choose_goal_cell` steps
2/3/4 run once per **Goal commitment** window of `GOAL_RECOMPUTE_PERIOD_TICKS =
4` ticks (2 s) per player; reactive overrides (steps 0/1/1b) continue to run
every tick.

The committed destination lives on a transient
`PlayerState._committed_goal: Optional[tuple[tuple[int,int], bool, int]] =
None` — `(cell, from_action_driven, expires_at_tick)`. No DB column, no
migration (mirrors `_path_cache` / `movement_trail`); default `None` so it
never becomes a ctor arg and never crosses the parallel-worker process
boundary. A fresh per-round `PlayerState` starts uncommitted.

**Force-recompute triggers** beyond cadence expiry: no prior commitment; Goal
cell reached; exiting **Stationary** (hide → not-hide, hold → not-hold —
Stationary players don't Advance, so re-engaging movement re-asks the cascade);
a reactive override firing this tick (the committed steady-state goal is
dropped and re-derived once the reactive condition clears); **Down**/respawn
**iff** the committed goal came from action-driven targeting. Action-driven
goals (tag, missile, resupply, hide) target a specific enemy or low-LOS spot
that the player was pursuing before being Downed — the tactical premise is
stale after respawn. Positioning goals (role-positioning, enemy-base default,
`only_move`-driven) survive a Down: the player keeps **Advancing** through the
**Respawn cooldown**, and "Heavy moves to nearest strong spot" or "advance on
the enemy base" is still the right intent. The `from_action_driven` flag on
`_committed_goal` is the source marker that gates this.

**Phase is expiry-based** (`expires_at_tick = tick + N` set per-player on each
recompute), not a global `tick % N == 0` modulus. Per-player expiry naturally
staggers the per-tick recompute load across the window — different players
recompute on different ticks because they were initialised, Downed, or had
their commitment force-cleared at different ticks. A global modulus would
synchronise the spike: every 4 ticks, all ~12 alive players hit the cascade
together.

The route cache (**Path commitment**, MOVE-02 /
[ADR-0008](0008-path-commitment-via-goal-keyed-cache.md)) is invalidated iff a
Goal commitment recompute *changes* the Goal cell. Re-picking the same cell
leaves `_path_cache` untouched — the two commitments are separate per-player
slots, and the route invariant follows the goal value, not the recompute event.
A Goal commitment that drops on Down also drops the route on Down (Down already
clears `_path_cache` independently via the shared `_record_down` hook), so the
two clear-on-Down policies do not need to be synchronised at the call sites.

**Scope: `BatchSimulator` only.** `ResourceBasedSimulator` is DB-bound and
removed by the immediately-following SIM-09; investing in RBS throttling is
wasted, and the brief RBS/BatchSim goal-cadence divergence breaks no existing
guarantee (no RBS≡BatchSim identity contract exists). Same precedent as MOVE-02
and MOVE-03.

## Consequences

Seeded games differ from pre-MOVE-04. A player committed to a steady-state goal
for up to N−1 ticks past the point where the unthrottled cascade would have
picked a different one will Advance toward the stale cell for that window, then
re-derive. On real maps this changes LOS exposures, tag targets, and every
downstream seeded outcome. No backfill
([ADR-0004](0004-simulation-data-is-disposable.md)). The delta is absorbed by
the post-MOVE-01 Score Calibration re-baseline that is **already pending** —
MOVE-04 creates **no new** re-baseline obligation, mirroring MOVE-02 / MOVE-03.

The SIM-07/SIM-08 *internal* determinism contract is preserved in form: the
cadence schedule, the `from_action_driven` source marker, and the force-clear
predicates consume **no RNG**; only the existing reactive overrides and the
steady-state cascade do, and those are unchanged. Same seed + Orientation +
rosters + map ⇒ identical game; serial == parallel; faithful **Replay** still
holds (the transient `_committed_goal` never crosses the parallel-worker
process boundary; the round is re-simulated in-worker from the seed).

Hard to reverse for the same reason as
[ADR-0007](0007-movement-decoupled-from-action.md) /
[ADR-0008](0008-path-commitment-via-goal-keyed-cache.md) /
[ADR-0009](0009-hold-overwatch.md): returning to per-tick goal selection would
again move every seeded outcome and re-invalidate the calibrated targets.

Domain term **Goal commitment** is defined in CONTEXT.md, together with the
superseded "Goal cell is recomputed every tick" flagged-ambiguity entry. The
constant `GOAL_RECOMPUTE_PERIOD_TICKS = 4` is in `sim_helpers/time_constants.py`
alongside the other tick-valued constants. Map-mode perf measurement (cells/tick
recompute ratio and ms/round delta vs the MOVE-02 baseline) is in the PR body
rather than the ADR — the *decision* does not depend on the exact ratio.

## Rejected alternatives

**Per-role N (e.g. Scout/Commander 2, Heavy/Medic/Ambo 4).** Plausible because
fast-positioning roles (Scout chasing high-LOS, Commander chasing enemy medic)
shift targets faster than positional anchors (Heavy holding strong spot, Medic
sheltering near Heavy). Rejected for now: per-role tuning adds a calibration
dimension that the pending post-MOVE-01 re-baseline is not equipped to absorb
in one pass — the re-baseline already collapses MOVE-02 + MOVE-03 + MOVE-04
behavioural deltas into one set of role targets, and varying N per role would
multiply the tuning surface. Defer until after the re-baseline; revisit if
specific roles show staleness artefacts in the calibrated runs.

**Map-size-scaled N (`N = f(rows, cols)`).** Surface intuition: a bigger map
means longer paths, so the goal stays valid longer, so N can be larger.
Rejected: N is **time-domain**, not space-domain. What changes on a bigger map
is the *path length* (more ticks to walk), not the rate at which the goal
becomes stale (a Scout's nearest high-LOS cell goes stale on the timescale of
player movement and Downs, which are tick-rate-bound — not map-size-bound). A
time-domain constant correctly tracks the rate at which the *world* changes,
which is what the goal cascade re-reads. Map-size scaling would conflate two
independent quantities.

**Whole-cascade throttle (throttle steps 0/1/1b too).** Simpler — one rule, no
reactive/steady-state split. Rejected: delays nuke-reaction by up to N−1 ticks
on a ~16-tick nuke fuse (a meaningful fraction; the MECH-04 reaction window is
already tight). Lives-critical seek-medic delayed by up to N−1 ticks lets a
1-life player Advance past the medic toward a now-stale steady-state goal. The
reactive overrides exist *because* their trigger is time-sensitive; throttling
them is a behavioural regression, not a perf optimisation.

**Global `tick % N == 0` phase.** Every player recomputes on the same tick,
producing a synchronised cascade spike every 4 ticks while the other 3 ticks
do no goal work at all. Same total cost, worse worst-case latency, worse cache
locality. Rejected: expiry-based per-player phase (`expires_at_tick`) staggers
naturally because players were initialised, Downed, or had their commitment
force-cleared at different ticks; no hashing is needed to spread the load.

**Source-blind Down-clear (always drop `_committed_goal` on Down).** Reading
A of the Down-clear rule — simpler, no `from_action_driven` flag needed.
Rejected: over-clears positioning goals that survive a Down intact. A Heavy
committed to a strong spot is Downed mid-Advance, respawns, and should resume
Advancing to that same strong spot — the goal is still the right one. Clearing
unconditionally forces a re-derivation that picks the same cell most of the
time but burns a cascade run on every respawn, and worse, may pick a *different*
equal-cost strong spot (the role-positioning helpers don't guarantee
suffix-stability) — re-introducing a flavour of the pre-MOVE-02 "wobble" the
project already rejected. The two-bit source marker on `_committed_goal` is
cheap; the conditional clear is correct.

**Reusing `_path_cache[0]` as the committed goal (no dedicated
`_committed_goal` field).** The route cache already carries `cached_goal` as
its first tuple element; reading from there would avoid a second per-player
field. Rejected: the path cache and the goal commitment invalidate on
*different* triggers. The path cache invalidates on Down/respawn always, on
next-route-cell-blocked, and on off-route displacement; the goal commitment
invalidates on cadence expiry, on reactive overrides firing, on Stationary
exit, and on Down *only when action-driven*. Conflating the storage couples
the two invalidation rules and breaks both: clearing the path cache on
"next cell blocked" would unintentionally drop the steady-state goal commitment
mid-window; clearing the goal commitment on cadence expiry would unintentionally
invalidate the route. Two slots, two policies; the per-player memory cost is
negligible.
