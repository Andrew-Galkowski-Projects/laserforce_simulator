# Missile event split: locking + missiled

**Status:** Accepted (RES-03, 2026-05-20)

## Context

Pre-RES-03 missile activity was represented as a single `GameEvent` row at
**Missile** resolution: `event_type="missile"`, written once per resolved
missile at `matches/simulation.py:~L2228`. That representation collapsed two
distinct moments — the lock/fire tick and the resolution tick — into one row at
the resolution tick. The timeline could not distinguish a missile in flight
from a resolved one (no fire-tick row exists for the in-flight phase), and a
fired-but-cancelled missile (locking actor **Down**ed before resolution) left
**no** log trace at all: resolution never ran, so the only event the legacy
representation would have written never fired. The missile analogue of the
MECH-05 nuke-cancellation rule — that a fired-but-cancelled attack should leave
a fire-tick trace, not a resolution-tick one — was therefore unenforceable
because the fire tick had no event at all.

The RES-02 review surfaced a separate but related bug in the same surface area.
Three resource-reconstruction scanners in
`templates/matches/game_round_events.html` (`chart-shots`, `chart-lives`,
`chart-points`) and the analyser at `game_analysis.py:186` compared against
`event_type == "missile_hit"` — a string the simulator **never** emitted
(actual `event_type` was `"missile"`). Missile-driven resource changes
were silently missing from those three charts. The substring `passes()` filter
on the timeline still matched the actual `"missile"` string, so the bug was
invisible to the eye but corrupted the chart scanners. RES-02 deferred the
fix; RES-03 absorbs it.

## Decision

Split the missile event into two event types. Remove the legacy
`event_type="missile"` value from production entirely.

1. **`event_type="locking"`** — emitted at the missile *fire / lock-start*
   tick. Carries `metadata = {"actor_role", "target_role"}`. Marks the moment
   the missile leaves the shooter; persists in the log even if the missile is
   subsequently cancelled. The **Locking event** in CONTEXT.md.

2. **`event_type="missiled"`** — emitted at the missile *resolution* tick.
   Carries `metadata = {"result": "hit"|"miss", "friendly_fire": bool,
   "actor_role", "target_role"}`. One row per resolved missile; never emitted
   when the locking actor was **Down**ed before resolution. The **Missiled
   event** in CONTEXT.md.

3. **`friendly_fire` is server-emitted.** The simulator writes
   `metadata["friendly_fire"]: bool` to every `missiled` event, computed as
   `actor.team_color == target.team_color`. The view never derives it from
   player-FK chasing and team-colour invariants; the contract is single-source
   on the event row. Mirrors the RES-02 single-source precedent for
   `metadata["sp"]`.

4. **Friendly-fire hits count as hits** in the missile-log efficiency summary.
   A missile that *landed* is a hit; the `friendly_fire` flag carries the
   qualitative distinction. Efficiency % = `hits / fired × 100` is computed
   view-side (no model property); the FF flag drives a CSS class on the row
   (substring `friendly-fire`), not a separate counter.

5. **Seam helper:** `start_missile_lock` in `matches/sim_helpers/combat.py`
   gains an `emit_event: Callable | None = None` kwarg, mirroring the
   `attempt_resupply` and `capture_base` precedent exactly — the helper appends
   the `locking` event via the passed-in callable; helpers don't import the
   simulator. Resolution stays on `BatchSimulator._complete_missile` and writes
   the `missiled` event directly into the event_log it already has.

6. **Down/respawn invariant:** if the locking actor is **Down**ed before
   resolution, no `missiled` event fires (the `locking` event remains). The
   pending-lock state is cleared on every life-loss site via the shared
   `BatchSimulator._record_down` helper — the same hook that drops
   `_path_cache` and `is_holding` — so the invariant is structurally enforced
   without per-site review. Mirrors the MECH-05 nuke-cancellation rule.

## Rejected alternatives

**Single `event_type="missile_hit"` (rename without split).** Closes the
chart-scanner bug — the literal would now match what the simulator emits — but
loses the fire-tick distinction entirely. The log still can't tell us when the
missile was launched, and a cancelled missile still leaves no trace. The
chart-scanner fix and the fire-tick visibility are independent design goals;
solving only one is a regression on the other.

**Single `event_type="missile"` with a `metadata["phase"] = "fire"|"resolved"`
discriminator.** Two moments, one event type, one extra metadata key. Avoids
two strings but pays for it at every filter: every timeline filter, every
chart scanner, and every test now has to check `event_type == "missile" AND
metadata["phase"] == "..."` instead of `event_type == "..."`. Django's
event-log idiom and the existing event-type-keyed filter UI both reward
distinct event types; phase discriminators on shared types are the harder
shape to maintain.

**`friendly_fire` derived view-side from team-colour comparison.** The view
chases the actor/target player FKs, reads their team_colour, compares. Avoids
adding a metadata key. Rejected: couples the view to player FK chasing and to
the team-colour invariant (which can drift if a future refactor renames or
re-types the colour field); the RES-02 precedent for SP snapshots is
server-side single-source on the event row. Cheaper at view-time, harder to
test, and re-derivation can disagree with the simulator's own view (the actor's
team at the *resolution tick* is what counts; view-time joins read the current
player record).

**Exclude friendly-fire hits from the hit count** (treat FF as a wasted
missile). Considered because FF intuitively *feels* like a wasted missile.
Rejected: the missile-log surface is a record of *missile outcomes*, not a
moral judgement on shot selection. A friendly-fire hit consumed a Missile,
landed on a player, dealt the damage of a hit, and reduced shields — every
mechanical predicate that makes a missile a "hit" is satisfied. The
`friendly_fire` boolean carries the qualitative meaning more honestly than a
separate counter would; the view renders friendly-fire rows with a
distinguishing CSS class so the user can see the qualifier at a glance. A
separate `friendly_fire_hits` counter could be added later without contract
churn if usage proves it valuable.

## Consequences

**Persisted-event delta: zero rows backfilled.** Per
[ADR-0004](0004-simulation-data-is-disposable.md), simulation data is
disposable. Old rounds in dev/test DBs that pre-date RES-03 retain their
legacy `event_type="missile"` rows; they simply won't show up in the new
missile-log view (which filters by `event_type="missiled"`). Old rows are not
rewritten and not deleted; new rounds get the split representation from
their first tick.

**Seeded games are unchanged in mechanics.** The two-event split adds a
`locking` row to the log between the existing lock-start and the existing
resolution but does not change any game-mechanics RNG draw, any tag/hit
resolution, or any timer. The SIM-07 / SIM-08 *internal* determinism contract
holds in form (same seed + Orientation + rosters + map ⇒ identical game;
serial == parallel; faithful **Replay**). No Score Calibration re-baseline is
triggered (unlike MOVE-02 / MOVE-03 / MOVE-04, which deliberately moved
seeded outcomes).

**Four-file `"missile_hit"` literal scrub.** The scanner at
`game_analysis.py:186` and the three chart scanners in
`templates/matches/game_round_events.html` are updated to compare against
`"missiled"` instead of the never-emitted `"missile_hit"`; the legacy
`event_type="missile"` emit at `simulation.py:~L2228` is replaced by the
`"missiled"` write. The spec's legacy-literal guard
(`test_res03_missile_log_spec.py::test_legacy_missile_event_type_string_is_absent_from_codebase`)
scans every production `.py` and `.html` to keep the scrub from regressing.

**No coupling to outstanding re-baselines.** ADR-0011 sits alongside
[ADR-0008](0008-path-commitment-via-goal-keyed-cache.md) (Path commitment),
[ADR-0009](0009-hold-overwatch.md) (Hold/Overwatch), and
[ADR-0010](0010-goal-commitment-via-tick-cadence-throttling.md) (Goal
commitment) and adds **no** new re-baseline obligation. The pending
post-MOVE-01 Score Calibration re-baseline absorbs only the MOVE-02/03/04 +
SIM-09 behavioural deltas; RES-03 is event-log-shape only.
