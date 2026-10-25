// The four event charts' data (ANG-06g, spec #350): pure, no Angular and no Chart.js.
import { PlaybackSnapshot, RoundPlayback, RoundTimeline, Side } from '../../api/rounds.models';
import { TICKS_PER_SECOND } from '../shared/clock';

/** One chart per key: Shots, Lives, Points and SP over time. */
export type ChartKey = 'shots' | 'lives' | 'points' | 'sp';

/** The cards' order. */
export const CHART_KEYS: readonly ChartKey[] = ['shots', 'lives', 'points', 'sp'];

/** One step of a player's line: the value from `sec` seconds until the next point. */
export interface ChartPoint {
  sec: number;
  value: number;
}

/** A charted player, from `RoundTimeline.players`. */
export interface ChartPlayer {
  id: number;
  name: string;
  side: Side;
  role: string;
}

/** A **Chart overlay** kind. */
export type OverlayKind = 'elimination' | 'special' | 'nuke';

/** One **Chart overlay**: a labelled vertical line at `sec`. */
export interface ChartOverlay {
  sec: number;
  kind: OverlayKind;
  label: string;
}

/** Everything the four charts draw. */
export interface EventCharts {
  /** In `RoundTimeline.players` order. */
  players: ChartPlayer[];
  /** Per chart, each player's points by player id. */
  series: Record<ChartKey, Map<number, ChartPoint[]>>;
  /** Ascending by `sec`. */
  overlays: ChartOverlay[];
}

/** The snapshot field each chart plots. */
const SNAPSHOT_FIELD: Record<ChartKey, keyof PlaybackSnapshot> = {
  shots: 'shots',
  lives: 'lives',
  points: 'score',
  sp: 'sp',
};

/**
 * Builds the charts' series from the Playback snapshots: one point per snapshot whose value
 * changed, starting with the tick-0 opening one. A timeline player with no Playback entry gets
 * an empty series.
 */
export function buildEventCharts(timeline: RoundTimeline, playback: RoundPlayback): EventCharts {
  const players = timeline.players.map(({ id, name, side, role }) => ({ id, name, side, role }));
  const snapshots = new Map(playback.players.map((p) => [p.id, p.snapshots]));
  const series = Object.fromEntries(
    CHART_KEYS.map((key) => [
      key,
      new Map(players.map((p) => [p.id, stepSeries(snapshots.get(p.id) ?? [], key)])),
    ]),
  ) as Record<ChartKey, Map<number, ChartPoint[]>>;
  return { players, series, overlays: [] };
}

function stepSeries(snapshots: readonly PlaybackSnapshot[], key: ChartKey): ChartPoint[] {
  const field = SNAPSHOT_FIELD[key];
  const points: ChartPoint[] = [];
  for (const snapshot of snapshots) {
    const value = snapshot[field] as number;
    if (points.at(-1)?.value !== value) {
      points.push({ sec: snapshot.tick / TICKS_PER_SECOND, value });
    }
  }
  return points;
}
