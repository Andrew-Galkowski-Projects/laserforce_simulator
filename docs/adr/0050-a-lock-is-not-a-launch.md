# A Lock is not a launch

**Status:** Accepted (MECH-21, 2026-11-09). Supersedes the fire-tick meaning of `locking` in
[ADR-0011](0011-missile-event-split.md); its `friendly_fire` and seam decisions stand.

## Context

ADR-0011 treated `locking` as the moment the missile was fired. The engine spent the missile there,
resolved it three ticks later as a hit or a dodge (`missiled result="miss"` plus a `missile_dodge`
row), and dropped a lock lost to sight with no row at all. A player could hold several locks at
once (6.6% of locks in a 12-seed Syracuse probe), and lockers shot during most locks and still
landed the missile.

In the arena a Lock is the locker holding their sights on a target. The missile is spent only when
the trigger is pulled after the third tone, and a launched missile always lands (an LFstats `0306`
is always on the locked target). Of 2617 real locks, 53% became a missile, 41% ended in the
locker's own shot and 5% in a new lock.

## Decision

- `locking` marks the lock start, and nothing is spent there.
- After `LOCK_TICKS` (2 ticks, 1.0 s), the lock launches. That writes `missiled` with
  `result="hit"` and spends the missile.
- A lock that ends any other way writes one `lock_cancelled` row at the tick it ends, with
  `metadata.reason` in `sight | dodge | shot | retarget | down | out`, and spends nothing.
- `missile_dodge` and `result="miss"` are retired.
- A player holds at most one Lock.

## Consequences

- Seeded rounds change: missiles per round, the RNG draw order, and every stat built on missiles.
- Rounds stored before this change keep the old semantics. They are not migrated (ADR-0004).
- The missile log's `hits / fired` is always 100% under these rules, so it becomes conversion
  (missiles / locks).
- Playback ends a lock at the engine's own row instead of inferring it from tick arithmetic.

## Rejected alternatives

- **Keep the missile spent at lock start and only add a cancel row.** The log would still record
  a cost the arena never charges, and the Playback pip would still drop at the wrong moment.
- **Keep `missiled result="miss"` for a dodge.** A dodge happens before the launch in the arena,
  so recording it as a launched miss would put back the confusion this ADR removes.
