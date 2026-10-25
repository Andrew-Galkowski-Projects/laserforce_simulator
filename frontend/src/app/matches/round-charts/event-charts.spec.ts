import {
  buildEventCharts,
  CHART_KEYS,
  ChartPlayer,
  ChartPoint,
  defaultFilters,
  OVERLAY_KINDS,
  sideTotals,
  visiblePlayers,
} from './event-charts';
import { playbackPlayer, playbackSnapshot, roundPlayback } from '../testing/playback-fixtures';
import { highlight, timeline, timelineEvent } from '../testing/timeline-fixtures';

/** A Nuke detonation: Ace's `special` at tick 300 carrying `targets` and 500 points. */
const detonation = timelineEvent({
  tick: 300,
  event_type: 'special',
  points: 500,
  description: 'Ace nuke detonated',
  target_id: null,
  target_name: null,
  target_side: null,
  metadata: { targets: [] },
});
const nukeHighlight = highlight({
  kind: 'nuke_detonation',
  tick: 300,
  team: 'red',
  actor: 'Ace',
  target: null,
  points: 500,
  label: 'Ace detonated a nuke',
});

describe('buildEventCharts', () => {
  it('lists the players in timeline order with their Side and role', () => {
    const charts = buildEventCharts(timeline(), roundPlayback());
    expect(charts.players.map((p) => [p.id, p.name, p.side, p.role])).toEqual([
      [101, 'Ace', 'red', 'commander'],
      [201, 'Bolt', 'blue', 'heavy'],
      [102, 'Cleo', 'red', 'medic'],
      [202, 'Dax', 'blue', 'ammo'],
    ]);
  });

  it('orders the charts Shots, Lives, Points, SP', () => {
    expect(CHART_KEYS).toEqual(['shots', 'lives', 'points', 'sp']);
  });

  it('starts every series at 0 s with the opening snapshot', () => {
    const charts = buildEventCharts(timeline(), roundPlayback());
    expect(charts.series.shots.get(201)).toEqual([{ sec: 0, value: 20 }]);
    expect(charts.series.lives.get(201)).toEqual([{ sec: 0, value: 10 }]);
    expect(charts.series.points.get(201)).toEqual([{ sec: 0, value: 0 }]);
    expect(charts.series.sp.get(201)).toEqual([{ sec: 0, value: 0 }]);
  });

  it('maps each snapshot to seconds (tick / 2) and reads score for Points', () => {
    const p = roundPlayback({
      players: [
        playbackPlayer(101, [
          playbackSnapshot(),
          playbackSnapshot({ tick: 120, score: 100, shots: 29, sp: 1 }),
          playbackSnapshot({ tick: 241, score: 80, shots: 29, lives: 14, sp: 1 }),
        ]),
      ],
    });
    const charts = buildEventCharts(timeline(), p);
    expect(charts.series.points.get(101)).toEqual([
      { sec: 0, value: 0 },
      { sec: 60, value: 100 },
      { sec: 120.5, value: 80 },
    ]);
    expect(charts.series.lives.get(101)).toEqual([
      { sec: 0, value: 15 },
      { sec: 120.5, value: 14 },
    ]);
  });

  it('drops a point whose value has not changed since the last one', () => {
    const p = roundPlayback({
      players: [
        playbackPlayer(101, [
          playbackSnapshot(),
          playbackSnapshot({ tick: 10, tags: 1 }),
          playbackSnapshot({ tick: 20, shots: 29 }),
        ]),
      ],
    });
    const charts = buildEventCharts(timeline(), p);
    expect(charts.series.shots.get(101)).toEqual([
      { sec: 0, value: 30 },
      { sec: 10, value: 29 },
    ]);
    expect(charts.series.sp.get(101)).toEqual([{ sec: 0, value: 0 }]);
  });

  it('gives a timeline player missing from playback an empty series, not a dropped one', () => {
    const p = roundPlayback({ players: [playbackPlayer(101)] });
    const charts = buildEventCharts(timeline(), p);
    expect(charts.players.map((pl) => pl.id)).toEqual([101, 201, 102, 202]);
    for (const key of CHART_KEYS) {
      expect(charts.series[key].get(201)).toEqual([]);
    }
  });

  describe('overlays', () => {
    it('lists the three kinds in the registry, labelled and coloured by event token', () => {
      expect(OVERLAY_KINDS).toEqual([
        { kind: 'elimination', label: 'Eliminations', token: '--lf-ev-elimination' },
        { kind: 'special', label: 'Specials', token: '--lf-ev-special' },
        { kind: 'nuke', label: 'Nukes', token: '--lf-ev-nuke' },
      ]);
    });

    it('marks each Elimination with the eliminated player, but not a team Elimination', () => {
      const t = timeline({
        events: [
          timelineEvent(),
          timelineEvent({ tick: 240, event_type: 'elimination', target_name: 'Bolt' }),
          timelineEvent({ tick: 500, event_type: 'team_elimination', target_name: null }),
        ],
      });
      expect(buildEventCharts(t, roundPlayback()).overlays).toEqual([
        { sec: 120, kind: 'elimination', label: 'Bolt' },
      ]);
    });

    it('marks each Special with the acting player', () => {
      const t = timeline({
        events: [
          timelineEvent({
            tick: 90,
            event_type: 'special',
            actor_name: 'Cleo',
            description: 'Cleo activates rapid fire',
          }),
        ],
      });
      expect(buildEventCharts(t, roundPlayback()).overlays).toEqual([
        { sec: 45, kind: 'special', label: 'Cleo' },
      ]);
    });

    it('shows a detonation as a Nuke from its Highlight, never also as a Special', () => {
      const t = timeline({ events: [detonation], highlights: [nukeHighlight] });
      expect(buildEventCharts(t, roundPlayback()).overlays).toEqual([
        { sec: 150, kind: 'nuke', label: 'Ace' },
      ]);
    });

    it('keeps a Special at the same tick by another player', () => {
      const other = { ...detonation, actor_name: 'Cleo', points: 0, metadata: {} };
      const t = timeline({ events: [detonation, other], highlights: [nukeHighlight] });
      expect(buildEventCharts(t, roundPlayback()).overlays).toEqual([
        { sec: 150, kind: 'special', label: 'Cleo' },
        { sec: 150, kind: 'nuke', label: 'Ace' },
      ]);
    });

    it('skips a Nuke Highlight with no tick, and ignores the other Highlight kinds', () => {
      const t = timeline({
        events: [],
        highlights: [{ ...nukeHighlight, tick: null }, highlight()],
      });
      expect(buildEventCharts(t, roundPlayback()).overlays).toEqual([]);
    });

    it('leaves a detonation under Specials on a round with no stored Highlights (pre-RV-02)', () => {
      const t = timeline({ events: [detonation], highlights: [] });
      expect(buildEventCharts(t, roundPlayback()).overlays).toEqual([
        { sec: 150, kind: 'special', label: 'Ace' },
      ]);
    });

    it('orders the overlays by time', () => {
      const t = timeline({
        events: [
          timelineEvent({ tick: 400, event_type: 'elimination', target_name: 'Dax' }),
          timelineEvent({ tick: 90, event_type: 'special', actor_name: 'Cleo' }),
        ],
        highlights: [nukeHighlight],
      });
      expect(buildEventCharts(t, roundPlayback()).overlays.map((o) => o.sec)).toEqual([
        45, 150, 200,
      ]);
    });
  });
});

const PLAYERS: ChartPlayer[] = [
  { id: 101, name: 'Ace', side: 'red', role: 'commander' },
  { id: 201, name: 'Bolt', side: 'blue', role: 'heavy' },
  { id: 102, name: 'Cleo', side: 'red', role: 'Medic' },
  { id: 202, name: 'Dax', side: 'blue', role: 'ammo' },
];

describe('defaultFilters', () => {
  it('ticks every Side, role, player and overlay kind, with Side totals off', () => {
    const f = defaultFilters(PLAYERS);
    expect([...f.sides]).toEqual(['red', 'blue']);
    expect([...f.roles]).toEqual(['commander', 'heavy', 'scout', 'ammo', 'medic']);
    expect([...f.players]).toEqual([101, 201, 102, 202]);
    expect([...f.overlays]).toEqual(['elimination', 'special', 'nuke']);
    expect(f.sideTotals).toBe(false);
  });
});

describe('visiblePlayers', () => {
  it('shows everyone under the default filters', () => {
    expect([...visiblePlayers(PLAYERS, defaultFilters(PLAYERS))]).toEqual([101, 201, 102, 202]);
  });

  it('shows a player only when their Side, role and own box are all ticked', () => {
    const f = defaultFilters(PLAYERS);
    expect([...visiblePlayers(PLAYERS, { ...f, sides: new Set(['red']) })]).toEqual([101, 102]);
    expect([...visiblePlayers(PLAYERS, { ...f, roles: new Set(['heavy', 'ammo']) })]).toEqual([
      201, 202,
    ]);
    expect([...visiblePlayers(PLAYERS, { ...f, players: new Set([101, 201]) })]).toEqual([
      101, 201,
    ]);
    expect([
      ...visiblePlayers(PLAYERS, { ...f, sides: new Set(['red']), players: new Set([101, 201]) }),
    ]).toEqual([101]);
  });

  it('matches roles case-insensitively', () => {
    const f = defaultFilters(PLAYERS);
    expect([...visiblePlayers(PLAYERS, { ...f, roles: new Set(['medic']) })]).toEqual([102]);
  });
});

describe('sideTotals', () => {
  const pts = (...pairs: [number, number][]): ChartPoint[] =>
    pairs.map(([sec, value]) => ({ sec, value }));
  const series = new Map<number, ChartPoint[]>([
    [101, pts([0, 30], [10, 29], [30, 28])],
    [102, pts([0, 15], [20, 14])],
    [201, pts([0, 20], [5, 18])],
    [202, pts([0, 15])],
  ]);
  const all = new Set([101, 102, 201, 202]);

  it('sums each Side over the union of its change times, carrying each value forward', () => {
    expect(sideTotals(PLAYERS, series, all, 'sum')).toEqual({
      red: pts([0, 45], [10, 44], [20, 43], [30, 42]),
      blue: pts([0, 35], [5, 33]),
    });
  });

  it('averages each Side for SP', () => {
    expect(sideTotals(PLAYERS, series, all, 'avg')).toEqual({
      red: pts([0, 22.5], [10, 22], [20, 21.5], [30, 21]),
      blue: pts([0, 17.5], [5, 16.5]),
    });
  });

  it('counts only the visible players', () => {
    expect(sideTotals(PLAYERS, series, new Set([101, 201]), 'sum')).toEqual({
      red: pts([0, 30], [10, 29], [30, 28]),
      blue: pts([0, 20], [5, 18]),
    });
  });

  it('gives a Side with no visible players no points', () => {
    expect(sideTotals(PLAYERS, series, new Set([101]), 'avg').blue).toEqual([]);
  });

  it('adds a player who joins late only from their first point', () => {
    const late = new Map<number, ChartPoint[]>([
      [101, pts([0, 10])],
      [102, pts([20, 4])],
    ]);
    expect(sideTotals(PLAYERS, late, new Set([101, 102]), 'sum').red).toEqual(
      pts([0, 10], [20, 14]),
    );
  });
});
