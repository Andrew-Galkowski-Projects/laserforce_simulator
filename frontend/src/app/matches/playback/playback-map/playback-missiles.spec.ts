import { TimelineEvent } from '../../../api/rounds.models';
import { timelineEvent } from '../../testing/timeline-fixtures';
import { lockOverlaysAt, lockSpans } from './playback-missiles';

// Ace (red commander, 101) locks Bolt (blue heavy, 201) unless a test says otherwise.
const ev = (overrides: Partial<TimelineEvent> = {}): TimelineEvent =>
  timelineEvent({ tick: 10, points: 0, metadata: {}, ...overrides });
const locking = (tick: number, overrides: Partial<TimelineEvent> = {}) =>
  ev({ event_type: 'locking', tick, ...overrides });
const missiled = (tick: number, result: 'hit' | 'miss', overrides: Partial<TimelineEvent> = {}) =>
  ev({ event_type: 'missiled', tick, metadata: { result }, ...overrides });

describe('lockSpans', () => {
  it('a lock that hits on its third tick spans T to T+3 and ends in a result', () => {
    expect(lockSpans([locking(10), missiled(13, 'hit')])).toEqual([
      { shooterId: 101, targetId: 201, side: 'red', start: 10, end: 13, ending: 'result' },
    ]);
  });

  it('a dodge pairs by the dodger as actor and the shooter as target, and ends in a result', () => {
    const dodge = ev({ event_type: 'missile_dodge', tick: 13, actor_id: 201, target_id: 101 });
    expect(lockSpans([locking(10), dodge]).map((s) => s.ending)).toEqual(['result']);
  });

  it('a missile by the same pair off T+3 does not resolve this lock', () => {
    expect(lockSpans([locking(10), missiled(12, 'hit')]).map((s) => s.ending)).toEqual(['lost']);
    expect(lockSpans([locking(10), missiled(14, 'hit')]).map((s) => s.ending)).toEqual(['lost']);
  });

  it("another shooter's missile on the target at T+3 does not resolve this lock", () => {
    const other = missiled(13, 'hit', { actor_id: 102 });
    expect(lockSpans([locking(10), other]).map((s) => s.ending)).toEqual(['lost']);
  });

  it.each([
    ['a dodge by someone other than the target', { actor_id: 202, target_id: 101 }],
    ['a dodge of another shooter', { actor_id: 201, target_id: 102 }],
    ['a dodge off T+3', { tick: 12 }],
  ])('%s does not resolve this lock', (_, overrides) => {
    const dodge = ev({
      event_type: 'missile_dodge',
      tick: 13,
      actor_id: 201,
      target_id: 101,
      ...overrides,
    });
    expect(lockSpans([locking(10), dodge]).map((s) => s.ending)).toEqual(['lost']);
  });

  it("the shooter's missile on another player at T+3 does not resolve this lock", () => {
    const other = missiled(13, 'hit', { target_id: 202 });
    expect(lockSpans([locking(10), other]).map((s) => s.ending)).toEqual(['lost']);
  });

  it('a missile_dodge the wrong way round (shooter as actor) does not pair', () => {
    const wrong = ev({ event_type: 'missile_dodge', tick: 13, actor_id: 101, target_id: 201 });
    expect(lockSpans([locking(10), wrong]).map((s) => s.ending)).toEqual(['lost']);
  });

  it("the shooter's next lock before T+3 cuts the first one short, lost, and starts its own", () => {
    const spans = lockSpans([locking(10), locking(11, { target_id: 202 })]);
    expect(spans.map((s) => [s.targetId, s.start, s.end, s.ending])).toEqual([
      [201, 10, 11, 'lost'],
      [202, 11, 14, 'lost'],
    ]);
  });

  it.each([
    ['a tag', ev({ event_type: 'tag', tick: 12 })],
    ['a miss', ev({ event_type: 'miss', tick: 12 })],
    [
      'an active base capture',
      ev({
        event_type: 'base_capture',
        tick: 12,
        target_id: null,
        metadata: { shots_remaining: 2 },
      }),
    ],
  ])('the shooter pulling the trigger early (%s) cuts the lock at that tick, lost', (_, shot) => {
    expect(lockSpans([locking(10), shot])[0]).toMatchObject({ end: 12, ending: 'lost' });
  });

  it("the shooter's tag at exactly T+3 does not cut: the lock resolved first", () => {
    const shot = ev({ event_type: 'tag', tick: 13 });
    expect(lockSpans([locking(10), missiled(13, 'hit'), shot])[0]).toMatchObject({
      end: 13,
      ending: 'result',
    });
  });

  it('a round-end base award (no shots_remaining) is no trigger pull', () => {
    const award = ev({ event_type: 'base_capture', tick: 12, target_id: null, metadata: {} });
    expect(lockSpans([locking(10), award])[0]).toMatchObject({ end: 13 });
  });

  it("another player's tag does not cut the shooter's lock", () => {
    const other = ev({ event_type: 'tag', tick: 12, actor_id: 102 });
    expect(lockSpans([locking(10), other])[0]).toMatchObject({ end: 13 });
  });

  it('a lock by another player does not cut the shooter', () => {
    const spans = lockSpans([locking(10), locking(11, { actor_id: 102 })]);
    expect(spans[0]).toMatchObject({ end: 13 });
  });

  it('the same pair locked twice: the first is cut, the second pairs with the missile at its own T+3', () => {
    const spans = lockSpans([locking(10), locking(11), missiled(14, 'hit')]);
    expect(spans.map((s) => [s.start, s.end, s.ending])).toEqual([
      [10, 11, 'lost'],
      [11, 14, 'result'],
    ]);
  });

  it('a locking event with no target is no Lock, so it cuts nothing either', () => {
    const targetless = locking(11, { target_id: null, target_side: null });
    expect(lockSpans([locking(10), targetless])).toEqual([
      { shooterId: 101, targetId: 201, side: 'red', start: 10, end: 13, ending: 'lost' },
    ]);
  });

  it('a locking event with no target makes no span', () => {
    expect(lockSpans([locking(10, { target_id: null, target_side: null })])).toEqual([]);
  });

  it('a lock with nothing at T+3 runs its full length and ends lost', () => {
    expect(lockSpans([locking(10)])).toEqual([
      { shooterId: 101, targetId: 201, side: 'red', start: 10, end: 13, ending: 'lost' },
    ]);
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
  return lockOverlaysAt(lockSpans(events), events, cursor, positionAt);
}

describe('lockOverlaysAt: the building lock', () => {
  it("draws from shooter to target at the cursor's positions, progress over the 3 ticks", () => {
    expect(overlaysAt([locking(10), missiled(13, 'hit')], 11.5).locks).toEqual([
      { side: 'red', from: [11.5, 0], to: [111.5, 50], progress: 0.5 },
    ]);
  });

  it('shows from its start tick (progress 0) and is gone at its end tick', () => {
    const events = [locking(10), missiled(13, 'hit')];
    expect(overlaysAt(events, 10).locks.map((l) => l.progress)).toEqual([0]);
    expect(overlaysAt(events, 9.9).locks).toEqual([]);
    expect(overlaysAt(events, 13).locks).toEqual([]);
  });

  it('a cut lock stops building at the cut tick', () => {
    const events = [locking(10), ev({ event_type: 'tag', tick: 11 })];
    expect(overlaysAt(events, 10.9).locks).toHaveLength(1);
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
    const events = [locking(10), missiled(13, 'hit')];
    expect(overlaysAt(events, 13).marks).toEqual([
      { kind: 'hit', side: 'red', at: [113, 50], alpha: 1, tick: 13 },
    ]);
    expect(overlaysAt(events, 15).marks.map((m) => [m.kind, m.at, m.alpha])).toEqual([
      ['hit', [113, 50], 1 - 2 / 2.5],
    ]);
    expect(overlaysAt(events, 15.1).marks).toEqual([]);
    expect(overlaysAt(events, 12.9).marks).toEqual([]);
  });

  it("a dodge breaks the reticle on the dodger, in the shooter's Side, with no lost mark", () => {
    const dodge = ev({
      event_type: 'missile_dodge',
      tick: 13,
      actor_id: 201,
      actor_side: 'blue',
      target_id: 101,
      target_side: 'red',
    });
    const events = [locking(10), dodge, missiled(13, 'miss')];
    expect(overlaysAt(events, 14).marks).toEqual([
      { kind: 'dodge', side: 'red', at: [113, 50], alpha: 1 - 1 / 2.5, tick: 13 },
    ]);
  });

  it('a mark on a player with no position is skipped', () => {
    expect(overlaysAt([missiled(13, 'hit', { target_id: 999 })], 13).marks).toEqual([]);
  });

  it('a lock with no result fizzles on the target where it stood at T+3', () => {
    expect(overlaysAt([locking(10)], 13).marks).toEqual([
      { kind: 'lost', side: 'red', at: [113, 50], alpha: 1, tick: 13 },
    ]);
  });

  it('a cut lock fizzles at the cut, and its late hit still bursts (results always draw)', () => {
    const events = [locking(10), locking(11, { target_id: 102 }), missiled(13, 'hit')];
    expect(overlaysAt(events, 11).marks.map((m) => [m.kind, m.at, m.tick])).toEqual([
      ['lost', [111, 50], 11],
    ]);
    expect(overlaysAt(events, 13).marks.map((m) => [m.kind, m.at, m.tick])).toEqual([
      ['lost', [111, 50], 11],
      ['hit', [113, 50], 13],
    ]);
  });
});
