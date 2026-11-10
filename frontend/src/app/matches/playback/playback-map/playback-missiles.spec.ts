import { TimelineEvent } from '../../../api/rounds.models';
import { timelineEvent } from '../../testing/timeline-fixtures';
import { lockOverlaysAt, lockSpans } from './playback-missiles';

// Ace (red commander, 101) locks Bolt (blue heavy, 201) unless a test says otherwise. MECH-21: the
// engine writes how each lock ends, a `missiled` (always a hit) or a `lock_cancelled` with a reason.
const ev = (overrides: Partial<TimelineEvent> = {}): TimelineEvent =>
  timelineEvent({ tick: 10, points: 0, metadata: {}, ...overrides });
const locking = (tick: number, overrides: Partial<TimelineEvent> = {}, lockTicks = 2) =>
  ev({ event_type: 'locking', tick, metadata: { lock_ticks: lockTicks }, ...overrides });
const missiled = (tick: number, overrides: Partial<TimelineEvent> = {}) =>
  ev({ event_type: 'missiled', tick, metadata: { result: 'hit' }, ...overrides });
const cancelled = (tick: number, reason: string, overrides: Partial<TimelineEvent> = {}) =>
  ev({ event_type: 'lock_cancelled', tick, metadata: { reason }, ...overrides });

const ACE_ON_BOLT = { shooterId: 101, targetId: 201, side: 'red', lockTicks: 2 } as const;

describe('lockSpans', () => {
  it("ends at the shooter's missiled on its target, as a hit", () => {
    expect(lockSpans([locking(10), missiled(12)])).toEqual([
      { ...ACE_ON_BOLT, start: 10, end: 12, ending: 'hit' },
    ]);
  });

  it('ends at a lock_cancelled with reason dodge, as a dodge', () => {
    expect(lockSpans([locking(10), cancelled(12, 'dodge')])).toEqual([
      { ...ACE_ON_BOLT, start: 10, end: 12, ending: 'dodge' },
    ]);
  });

  it.each(['sight', 'shot', 'retarget', 'down', 'out'])(
    'ends at a lock_cancelled with reason %s, as lost, on its tick',
    (reason) => {
      expect(lockSpans([locking(10), cancelled(11, reason)])).toEqual([
        { ...ACE_ON_BOLT, start: 10, end: 11, ending: 'lost' },
      ]);
    },
  );

  it('a lock with neither row (open at round end) ends at start + lock_ticks, lost', () => {
    expect(lockSpans([locking(1798)])).toEqual([
      { ...ACE_ON_BOLT, start: 1798, end: 1800, ending: 'lost' },
    ]);
  });

  it("reads the lock's length from its own metadata.lock_ticks", () => {
    expect(lockSpans([locking(10, {}, 5)])).toEqual([
      { ...ACE_ON_BOLT, lockTicks: 5, start: 10, end: 15, ending: 'lost' },
    ]);
  });

  it("another player's missile or cancel, or the shooter's missile on another target, ends nothing", () => {
    const events = [
      locking(10),
      missiled(12, { actor_id: 102 }),
      cancelled(12, 'dodge', { actor_id: 201, actor_side: 'blue', target_id: 101 }),
      missiled(12, { target_id: 202 }),
    ];
    expect(lockSpans(events)).toEqual([{ ...ACE_ON_BOLT, start: 10, end: 12, ending: 'lost' }]);
  });

  it('only a later row ends a lock: a retarget cancels the old lock and starts the new one', () => {
    // The engine logs the old lock's cancel, then the new locking, on one tick.
    const events = [
      locking(10),
      cancelled(11, 'retarget'),
      locking(11, { target_id: 202 }),
      missiled(13, { target_id: 202 }),
    ];
    expect(lockSpans(events).map((s) => [s.targetId, s.start, s.end, s.ending])).toEqual([
      [201, 10, 11, 'lost'],
      [202, 11, 13, 'hit'],
    ]);
  });

  it('a cancel logged after the lock on its own tick ends it there', () => {
    expect(lockSpans([locking(10), cancelled(10, 'shot')])).toEqual([
      { ...ACE_ON_BOLT, start: 10, end: 10, ending: 'lost' },
    ]);
  });

  it("the shooter's own tag or miss does not end a lock: the engine's row does", () => {
    const events = [locking(10), ev({ event_type: 'tag', tick: 11 }), missiled(12)];
    expect(lockSpans(events)[0]).toMatchObject({ end: 12, ending: 'hit' });
  });

  it('a locking event with no target makes no span', () => {
    expect(lockSpans([locking(10, { target_id: null, target_side: null })])).toEqual([]);
  });
});

// Everyone moves every tick, so a position read at the wrong tick shows: Ace (101) stands at
// (tick, 0), Bolt (201) at (100 + tick, 50), Cy (102) at (0, tick). Anyone else is unknown.
function moving(id: number, tick: number): readonly [number, number] | null {
  switch (id) {
    case 101:
      return [tick, 0];
    case 201:
      return [100 + tick, 50];
    case 102:
      return [0, tick];
    default:
      return null;
  }
}

function overlaysAt(events: TimelineEvent[], cursor: number, positionAt = moving) {
  return lockOverlaysAt(lockSpans(events), cursor, positionAt);
}

describe('lockOverlaysAt: the building lock', () => {
  it("draws from shooter to target at the cursor's positions, progress over lock_ticks", () => {
    expect(overlaysAt([locking(10), missiled(12)], 11).locks).toEqual([
      { side: 'red', from: [11, 0], to: [111, 50], progress: 0.5 },
    ]);
    expect(overlaysAt([locking(10, {}, 4), missiled(14)], 11).locks[0].progress).toBe(0.25);
  });

  it('shows from its start tick (progress 0) and is gone at its end tick', () => {
    const events = [locking(10), missiled(12)];
    expect(overlaysAt(events, 10).locks.map((l) => l.progress)).toEqual([0]);
    expect(overlaysAt(events, 9.9).locks).toEqual([]);
    expect(overlaysAt(events, 12).locks).toEqual([]);
  });

  it('a cancelled lock stops building at the cancel tick, its progress still over lock_ticks', () => {
    const events = [locking(10), cancelled(11, 'sight')];
    expect(overlaysAt(events, 10.5).locks.map((l) => l.progress)).toEqual([0.25]);
    expect(overlaysAt(events, 11).locks).toEqual([]);
  });

  it('a player with no position draws no lock', () => {
    expect(overlaysAt([locking(10, { target_id: 999 })], 11).locks).toEqual([]);
  });

  it('a shooter and target on the same spot draw no lock', () => {
    const together = (): readonly [number, number] => [5, 5];
    expect(overlaysAt([locking(10)], 11, together).locks).toEqual([]);
  });
});

describe('lockOverlaysAt: the marks', () => {
  it('a hit bursts on the target where it stood at the hit, fading over LINE_TICKS', () => {
    const events = [locking(10), missiled(12)];
    expect(overlaysAt(events, 12).marks).toEqual([
      { kind: 'hit', side: 'red', at: [112, 50], alpha: 1, tick: 12 },
    ]);
    expect(overlaysAt(events, 14).marks.map((m) => [m.kind, m.at, m.alpha])).toEqual([
      ['hit', [112, 50], 1 - 2 / 2.5],
    ]);
    expect(overlaysAt(events, 14.1).marks).toEqual([]);
    expect(overlaysAt(events, 11.9).marks).toEqual([]);
  });

  it("a dodge breaks the reticle on the dodger (the target), in the shooter's Side", () => {
    expect(overlaysAt([locking(10), cancelled(12, 'dodge')], 13).marks).toEqual([
      { kind: 'dodge', side: 'red', at: [112, 50], alpha: 1 - 1 / 2.5, tick: 12 },
    ]);
  });

  it('any other cancel fizzles on the target where it stood at the cancel', () => {
    expect(overlaysAt([locking(10), cancelled(11, 'down')], 11).marks).toEqual([
      { kind: 'lost', side: 'red', at: [111, 50], alpha: 1, tick: 11 },
    ]);
  });

  it('a lock open at round end fizzles at start + lock_ticks', () => {
    expect(overlaysAt([locking(10)], 12).marks).toEqual([
      { kind: 'lost', side: 'red', at: [112, 50], alpha: 1, tick: 12 },
    ]);
  });

  it('a mark on a player with no position is skipped', () => {
    expect(
      overlaysAt([locking(10, { target_id: 999 }), missiled(12, { target_id: 999 })], 12).marks,
    ).toEqual([]);
  });

  it('a missiled with no lock before it draws no mark', () => {
    expect(overlaysAt([missiled(12)], 12).marks).toEqual([]);
  });
});
