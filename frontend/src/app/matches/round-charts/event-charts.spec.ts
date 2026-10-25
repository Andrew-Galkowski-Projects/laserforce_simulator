import { buildEventCharts, CHART_KEYS } from './event-charts';
import { playbackPlayer, playbackSnapshot, roundPlayback } from '../testing/playback-fixtures';
import { timeline } from '../testing/timeline-fixtures';

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
});
