import { MapBase } from '../../../api/maps.models';
import { TimelineEvent, TimelineEventType } from '../../../api/rounds.models';
import { timelineEvent } from '../../testing/timeline-fixtures';
import { baseMarkers, overlaysAt } from './playback-overlays';

// Ace (red, 101) stands at (0, 0) and Bolt (blue, 201) at (100, 0) at every tick unless a test
// says otherwise: an east-facing tag, so the 4 px right-hand offset is +4 in y (y grows down).
type Table = Record<string, readonly [number, number]>;
const STILL: Table = { 101: [0, 0], 201: [100, 0], 102: [50, 50] };

function fakePositions(
  table: Table = STILL,
  byTick: Record<string, readonly [number, number]> = {},
) {
  const calls: [number, number][] = [];
  const positionAt = (id: number, tick: number): readonly [number, number] | null => {
    calls.push([id, tick]);
    return byTick[`${id}@${tick}`] ?? table[id] ?? null;
  };
  return { positionAt, calls };
}

const ev = (overrides: Partial<TimelineEvent> = {}): TimelineEvent =>
  timelineEvent({ tick: 10, ...overrides });

function linesAt(events: TimelineEvent[], cursor = 10, bases: MapBase[] = []) {
  return overlaysAt(events, cursor, fakePositions().positionAt, bases).lines;
}

describe('overlaysAt: which events draw a line', () => {
  it('a tag draws a tag line in the actor Side colour', () => {
    const lines = linesAt([ev({ event_type: 'tag', actor_side: 'red' })]);
    expect(lines.map((l) => [l.kind, l.side, l.tick])).toEqual([['tag', 'red', 10]]);
  });

  it('a miss draws a miss line', () => {
    expect(linesAt([ev({ event_type: 'miss' })]).map((l) => l.kind)).toEqual(['miss']);
  });

  it('a missile with result hit is a missile-hit, any other result a missile-miss', () => {
    const lines = linesAt([
      ev({ event_type: 'missiled', metadata: { result: 'hit' } }),
      ev({ event_type: 'missiled', metadata: { result: 'miss' } }),
    ]);
    expect(lines.map((l) => l.kind)).toEqual(['missile-hit', 'missile-miss']);
  });

  it('all three resupply types draw a resupply line, actor to target', () => {
    const types: TimelineEventType[] = ['resupply_ammo', 'resupply_lives', 'combo_resupply'];
    const lines = linesAt(
      types.map((event_type) =>
        ev({ event_type, actor_id: 102, actor_side: 'blue', target_id: 201, target_side: 'blue' }),
      ),
    );
    expect(lines.map((l) => [l.kind, l.side])).toEqual([
      ['resupply', 'blue'],
      ['resupply', 'blue'],
      ['resupply', 'blue'],
    ]);
  });

  it('every other event type, and a targetless event, draws nothing', () => {
    const lines = linesAt([
      ev({ event_type: 'special' }),
      ev({ event_type: 'elimination' }),
      ev({ event_type: 'missile_dodge' }),
      ev({ event_type: 'movement' }),
      ev({ event_type: 'tag', target_id: null, target_side: null, target_name: null }),
    ]);
    expect(lines).toEqual([]);
  });
});

describe('overlaysAt: the line window and fade', () => {
  const ageOf = (age: number) => linesAt([ev({ tick: 10 })], 10 + age);

  it('a line shows from age 0 to age 2 (LINE_TICKS), with a fractional cursor', () => {
    expect(ageOf(-0.1)).toEqual([]);
    expect(ageOf(0)).toHaveLength(1);
    expect(ageOf(1.5)).toHaveLength(1);
    expect(ageOf(2)).toHaveLength(1);
    expect(ageOf(2.1)).toEqual([]);
  });

  it('a line fades as 1 - age / 2.5: 1 at age 0, 0.2 at age 2', () => {
    expect(ageOf(0)[0].alpha).toBeCloseTo(1);
    expect(ageOf(1)[0].alpha).toBeCloseTo(0.6);
    expect(ageOf(2)[0].alpha).toBeCloseTo(0.2);
  });

  it('only events inside the window draw', () => {
    const lines = linesAt([ev({ tick: 5 }), ev({ tick: 9 }), ev({ tick: 11 })], 10);
    expect(lines.map((l) => l.tick)).toEqual([9]);
  });
});

describe('overlaysAt: line ends', () => {
  it('takes both ends at the event tick, not the cursor', () => {
    // Bolt walks north after the tag: at the cursor he stands somewhere else.
    const { positionAt, calls } = fakePositions(STILL, {
      '101@11.5': [0, 40],
      '201@11.5': [100, -60],
    });
    const [line] = overlaysAt([ev({ tick: 10 })], 11.5, positionAt, []).lines;
    expect(calls.every(([, tick]) => tick === 10)).toBe(true);
    expect(line.from).toEqual([0, 4]);
    expect(line.to).toEqual([100, 4]);
  });

  it("shifts both ends 4 px to the actor's right: +y facing east", () => {
    const [line] = linesAt([ev()]);
    expect(line.from).toEqual([0, 4]);
    expect(line.to).toEqual([100, 4]);
  });

  it("shifts both ends 4 px to the actor's right: +x facing north", () => {
    const { positionAt } = fakePositions({ 101: [0, 100], 201: [0, 0] });
    const [line] = overlaysAt([ev()], 10, positionAt, []).lines;
    expect(line.from).toEqual([4, 100]);
    expect(line.to).toEqual([4, 0]);
  });

  it('a mutual tag gives two lines that do not overlap', () => {
    const lines = linesAt([
      ev(),
      ev({
        actor_id: 201,
        actor_side: 'blue',
        target_id: 101,
        target_side: 'red',
      }),
    ]);
    expect(lines.map((l) => [l.from, l.to])).toEqual([
      [
        [0, 4],
        [100, 4],
      ],
      [
        [100, -4],
        [0, -4],
      ],
    ]);
  });

  it('a player with no position draws nothing', () => {
    expect(linesAt([ev({ target_id: 999 })])).toEqual([]);
    expect(linesAt([ev({ actor_id: 999 })])).toEqual([]);
  });

  it('two players on the same point draw nothing', () => {
    const { positionAt } = fakePositions({ 101: [30, 30], 201: [30, 30] });
    expect(overlaysAt([ev()], 10, positionAt, []).lines).toEqual([]);
  });
});

describe('overlaysAt: base captures', () => {
  // Ace (red, at 0, 0) captures. Red's base is east of him, blue's south, and neutral_2 is the
  // nearer of two neutral bases (north).
  const BASES: MapBase[] = [
    { type: 'red', x_px: 200, y_px: 0 },
    { type: 'blue', x_px: 0, y_px: 200 },
    { type: 'neutral_1', x_px: 300, y_px: 300 },
    { type: 'neutral_2', x_px: 0, y_px: -80 },
  ];
  const ACTIVE = { shots_remaining: 0, points_scored: 1001 };
  const capture = (metadata: Record<string, unknown>, tick = 10): TimelineEvent =>
    ev({
      tick,
      event_type: 'base_capture',
      target_id: null,
      target_name: null,
      target_side: null,
      metadata,
    });
  const at = (events: TimelineEvent[], cursor = 10, bases = BASES) =>
    overlaysAt(events, cursor, fakePositions().positionAt, bases);

  it('draws a capture line from the capturer (offset) to the base, and a burst at the base', () => {
    const { lines, bursts } = at([capture({ base_id: 13, ...ACTIVE })]);
    expect(lines.map((l) => [l.kind, l.side, l.from, l.to])).toEqual([
      ['capture', 'red', [0, 4], [200, 4]],
    ]);
    expect(bursts).toEqual([{ side: 'red', at: [200, 0], alpha: 1, tick: 10 }]);
  });

  it('base_id 14 is the blue base', () => {
    const { bursts } = at([capture({ base_id: 14, ...ACTIVE })]);
    expect(bursts.map((b) => b.at)).toEqual([[0, 200]]);
  });

  it("base_id 15 is the neutral base nearest the capturer's position at the event tick", () => {
    const { lines, bursts } = at([capture({ base_id: 15, ...ACTIVE })]);
    expect(bursts.map((b) => b.at)).toEqual([[0, -80]]);
    expect(lines.map((l) => [l.from, l.to])).toEqual([
      [
        [4, 0],
        [4, -80],
      ],
    ]);
  });

  it('metadata.base_type names the base when present', () => {
    const { bursts } = at([capture({ base_id: 15, base_type: 'neutral_1', ...ACTIVE })]);
    expect(bursts.map((b) => b.at)).toEqual([[300, 300]]);
  });

  it('a round-end awarded capture (no shots_remaining) draws nothing', () => {
    const { lines, bursts } = at([capture({ base_id: 13 })]);
    expect(lines).toEqual([]);
    expect(bursts).toEqual([]);
  });

  it('a capture with no matching base draws nothing', () => {
    const red = BASES.filter((b) => b.type === 'red');
    const missing = [
      capture({ base_id: 14, ...ACTIVE }),
      capture({ base_id: 15, ...ACTIVE }),
      capture({ base_id: 13, base_type: 'neutral_3', ...ACTIVE }),
      capture({ base_id: 99, ...ACTIVE }),
    ];
    expect(at(missing, 10, red)).toEqual({ lines: [], bursts: [] });
  });

  it('the burst shows to age 4 (BURST_TICKS) and fades as 1 - age / 4.5; the line to age 2', () => {
    const events = [capture({ base_id: 13, ...ACTIVE })];
    expect(at(events, 9.9).bursts).toEqual([]);
    expect(at(events, 12.1).lines).toEqual([]);
    expect(at(events, 14).bursts[0].alpha).toBeCloseTo(1 - 4 / 4.5);
    expect(at(events, 14.1).bursts).toEqual([]);
  });
});

describe('baseMarkers', () => {
  it('letters the red, blue and four neutral bases R / B / N at their image px', () => {
    const bases: MapBase[] = [
      { type: 'red', x_px: 1, y_px: 2 },
      { type: 'blue', x_px: 3, y_px: 4 },
      { type: 'neutral_1', x_px: 5, y_px: 6 },
      { type: 'neutral_2', x_px: 7, y_px: 8 },
      { type: 'neutral_3', x_px: 9, y_px: 10 },
      { type: 'neutral_4', x_px: 11, y_px: 12 },
    ];
    expect(baseMarkers(bases)).toEqual([
      { type: 'red', kind: 'red', letter: 'R', x: 1, y: 2 },
      { type: 'blue', kind: 'blue', letter: 'B', x: 3, y: 4 },
      { type: 'neutral_1', kind: 'neutral', letter: 'N', x: 5, y: 6 },
      { type: 'neutral_2', kind: 'neutral', letter: 'N', x: 7, y: 8 },
      { type: 'neutral_3', kind: 'neutral', letter: 'N', x: 9, y: 10 },
      { type: 'neutral_4', kind: 'neutral', letter: 'N', x: 11, y: 12 },
    ]);
  });

  it('a map with no bases has no markers', () => {
    expect(baseMarkers([])).toEqual([]);
  });
});
