// The four event charts' data (ANG-06g, spec #350): pure, no Angular and no Chart.js.
import { Role } from '../../api/players.models';
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

/** A Chart overlay kind's filter label and the `--lf-*` token its lines are drawn in. */
export interface OverlayKindDef {
  kind: OverlayKind;
  label: string;
  token: string;
}

/**
 * The Chart overlay kinds, in filter order. RV-04 (Highlights) and RES-05 (Medic hits) each add
 * one entry here, plus the events it marks in `buildEventCharts`.
 */
export const OVERLAY_KINDS: readonly OverlayKindDef[] = [
  { kind: 'elimination', label: 'Eliminations', token: '--lf-ev-elimination' },
  { kind: 'special', label: 'Specials', token: '--lf-ev-special' },
  { kind: 'nuke', label: 'Nukes', token: '--lf-ev-nuke' },
];

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
 * an empty series. The overlays come from the timeline (see `overlaysOf`).
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
  return { players, series, overlays: overlaysOf(timeline) };
}

/**
 * The Chart overlays, by time:
 * - an Elimination, labelled with the eliminated player (a team Elimination is not one);
 * - a Nuke, from each `nuke_detonation` Highlight with a tick, labelled with the Commander;
 * - a Special, from each `special` event that is not one of those detonations (same tick and
 *   actor), labelled with the actor. The detonation rule stays on the server (`highlights.py`):
 *   on a round with no stored Highlights, a detonation stays a Special.
 */
function overlaysOf(timeline: RoundTimeline): ChartOverlay[] {
  const sec = (tick: number): number => tick / TICKS_PER_SECOND;
  const nukes: ChartOverlay[] = [];
  const detonations = new Set<string>();
  for (const h of timeline.highlights) {
    if (h.kind !== 'nuke_detonation' || h.tick === null) continue;
    nukes.push({ sec: sec(h.tick), kind: 'nuke', label: h.actor ?? '' });
    detonations.add(`${h.tick}|${h.actor}`);
  }
  const marked: ChartOverlay[] = [];
  for (const e of timeline.events) {
    if (e.event_type === 'elimination') {
      marked.push({ sec: sec(e.tick), kind: 'elimination', label: e.target_name ?? '' });
    } else if (e.event_type === 'special' && !detonations.has(`${e.tick}|${e.actor_name}`)) {
      marked.push({ sec: sec(e.tick), kind: 'special', label: e.actor_name });
    }
  }
  // A stable sort: at one tick, the events keep their log order and the Nukes come after.
  return [...marked, ...nukes].sort((a, b) => a.sec - b.sec);
}

/** What the filter bar has ticked. */
export interface ChartFilters {
  sides: ReadonlySet<Side>;
  /** Lower-case role names. */
  roles: ReadonlySet<string>;
  players: ReadonlySet<number>;
  overlays: ReadonlySet<OverlayKind>;
  /** Draws the dashed Red / Blue totals. */
  sideTotals: boolean;
}

/** The roles in filter order (ADR-0043: CMD / HVY / SCT / AMO / MED). */
export const CHART_ROLES: readonly Role[] = ['commander', 'heavy', 'scout', 'ammo', 'medic'];

/** Everything ticked but Side totals (the Django page's defaults). */
export function defaultFilters(players: readonly ChartPlayer[]): ChartFilters {
  return {
    sides: new Set<Side>(['red', 'blue']),
    roles: new Set(CHART_ROLES),
    players: new Set(players.map((p) => p.id)),
    overlays: new Set(OVERLAY_KINDS.map((k) => k.kind)),
    sideTotals: false,
  };
}

/** The ids of the players whose Side, role and own box are all ticked, in player order. */
export function visiblePlayers(
  players: readonly ChartPlayer[],
  filters: ChartFilters,
): Set<number> {
  return new Set(
    players
      .filter(
        (p) =>
          filters.sides.has(p.side) &&
          filters.roles.has(p.role.toLowerCase()) &&
          filters.players.has(p.id),
      )
      .map((p) => p.id),
  );
}

/**
 * Each Side's total line over its visible players: at every time any of them changes, the sum
 * (`'sum'`) or the average (`'avg'`, for SP) of each one's latest value. A player counts from
 * their first point; a Side with no visible players has no points.
 */
export function sideTotals(
  players: readonly ChartPlayer[],
  series: ReadonlyMap<number, readonly ChartPoint[]>,
  visible: ReadonlySet<number>,
  mode: 'sum' | 'avg',
): Record<Side, ChartPoint[]> {
  const total = (side: Side): ChartPoint[] => {
    const lines = players
      .filter((p) => p.side === side && visible.has(p.id))
      .map((p) => series.get(p.id) ?? []);
    const times = [...new Set(lines.flatMap((line) => line.map((pt) => pt.sec)))].sort(
      (a, b) => a - b,
    );
    return times.map((sec) => {
      const values = lines
        .map((line) => valueAt(line, sec))
        .filter((v): v is number => v !== undefined);
      const sum = values.reduce((a, b) => a + b, 0);
      return { sec, value: mode === 'sum' ? sum : sum / values.length };
    });
  };
  return { red: total('red'), blue: total('blue') };
}

/** A line's value at `sec` (its latest point at or before it), `undefined` before its first. */
function valueAt(line: readonly ChartPoint[], sec: number): number | undefined {
  let value: number | undefined;
  for (const pt of line) {
    if (pt.sec > sec) break;
    value = pt.value;
  }
  return value;
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
