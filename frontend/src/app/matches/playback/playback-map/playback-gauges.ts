// What each player carries on the Playback map (RV-07, spec #575): pure, so the canvas
// painting in playback-map.ts only draws what these return.
import { PlaybackMapPlayer, PlaybackSnapshot } from '../../../api/rounds.models';
import { snapshotAt } from '../snapshot-at';

/** A colour's red, green and blue channels, 0..255. */
export type Rgb = readonly [number, number, number];

/** One player's gauges at a tick: each level is 0..1 of the role's maximum. */
export interface PlayerGauges {
  /** Snapshot lives / `max_lives`, clamped 0..1: the left arc. */
  lives: number;
  /** Snapshot shots / `max_shots`, clamped 0..1: the right arc. */
  shots: number;
  /** Snapshot missiles (the lit pips), or `null` when `max_missiles` is 0. */
  missiles: number | null;
  /** The special bar (RV-07b), or `null` when `special_cost` is null (a Heavy). */
  special: SpecialBar | null;
}

/** A player's SP against its special's cost, and the special running at the tick. */
export interface SpecialBar {
  /** `floor(min(sp, MAX_SP) / cost)`: the completed laps. */
  lap: number;
  /** `(min(sp, MAX_SP) % cost) / cost`: the current lap's fill, 0..1. */
  fill: number;
  /** A Commander's nuke or a Scout's rapid fire; a Medic's or Ammo's special never runs. */
  running: 'none' | 'rapid-fire' | 'nuke';
  /** The nuke fuse's progress 0..1 (`fuseProgress`) while `running` is `'nuke'`, else `null`. */
  fuse: number | null;
}

/** The most SP a player can bank. */
export const MAX_SP = 99;

/** The special bar from `snap` (the snapshot at `tick`); `snapshots` and `tick` time the fuse. */
function specialBar(
  cost: number | null,
  snap: PlaybackSnapshot | null,
  snapshots: readonly PlaybackSnapshot[],
  tick: number,
): SpecialBar | null {
  if (cost === null) {
    return null;
  }
  const sp = Math.min(snap?.sp ?? 0, MAX_SP);
  const state = running(snap);
  return {
    lap: Math.floor(sp / cost),
    fill: (sp % cost) / cost,
    running: state,
    fuse: state === 'nuke' ? fuseProgress(snapshots, tick) : null,
  };
}

/** `value / max` clamped to 0..1; 0 when the maximum is not positive. */
function level(value: number, max: number): number {
  return max > 0 ? Math.min(1, Math.max(0, value / max)) : 0;
}

/**
 * The player's gauges at `tick`, read from the last snapshot at or before it (`snapshotAt`), so
 * a fractional cursor reads as the scoreboard does. No snapshot yet reads empty.
 */
export function playerGauges(
  player: PlaybackMapPlayer,
  snapshots: readonly PlaybackSnapshot[],
  tick: number,
): PlayerGauges {
  const snap = snapshotAt(snapshots, tick);
  return {
    lives: level(snap?.lives ?? 0, player.max_lives),
    shots: level(snap?.shots ?? 0, player.max_shots),
    missiles: player.max_missiles > 0 ? (snap?.missiles ?? 0) : null,
    special: specialBar(player.special_cost, snap, snapshots, tick),
  };
}

/** The longest nuke fuse in ticks: a fuse with no snapshot clearing `nuking` ends here. */
const LONGEST_FUSE = 14;

/**
 * A nuke fuse's progress at `tick`, 0..1, or `null` when no fuse burns then. The fuse is lit at
 * the first tick of the latest run of `nuking` snapshots at or before `tick`, and ends at the next
 * snapshot that clears `nuking` (detonation or cancel), else at lit + 14. The end tick itself
 * still reads 1; any later tick reads `null`. `snapshots` must be in tick order.
 */
export function fuseProgress(snapshots: readonly PlaybackSnapshot[], tick: number): number | null {
  let last = -1;
  for (let i = 0; i < snapshots.length && snapshots[i].tick <= tick; i++) {
    if (snapshots[i].nuking) {
      last = i;
    }
  }
  if (last < 0) {
    return null;
  }
  let first = last;
  while (first > 0 && snapshots[first - 1].nuking) {
    first--;
  }
  const lit = snapshots[first].tick;
  let next = last + 1;
  while (next < snapshots.length && snapshots[next].nuking) {
    next++;
  }
  const cleared = snapshots[next]?.tick;
  if (cleared !== undefined && cleared < tick) {
    return null;
  }
  const end = cleared ?? lit + LONGEST_FUSE;
  return Math.min(1, Math.max(0, (tick - lit) / (end - lit)));
}

function running(snap: PlaybackSnapshot | null): SpecialBar['running'] {
  if (snap?.nuking) {
    return 'nuke';
  }
  return snap?.rapid_fire ? 'rapid-fire' : 'none';
}

/** Lap `lap`'s colour from the lap tokens; a lap past the last token takes the last one. */
export function lapColour(lap: number, laps: readonly string[]): string {
  return laps[Math.min(lap, laps.length - 1)];
}

/** Whether any player with a special has its nuke or rapid fire running at `tick`. */
export function anySpecialRunning(
  players: readonly PlaybackMapPlayer[],
  snapshotsById: ReadonlyMap<number, readonly PlaybackSnapshot[]>,
  tick: number,
): boolean {
  return players.some((player) => {
    const snap = snapshotAt(snapshotsById.get(player.id) ?? [], tick);
    return player.special_cost !== null && running(snap) !== 'none';
  });
}

function mix(from: Rgb, to: Rgb, t: number): string {
  const [r, g, b] = from.map((c, i) => Math.round(c + (to[i] - c) * t));
  return `rgb(${r}, ${g}, ${b})`;
}

/**
 * An arc's colour at `level` (0..1): from `mid` to `full` by `(level - 0.5) * 2` at 0.5 and over,
 * else from `empty` to `mid` by `level * 2` (the prototype's rule).
 */
export function gaugeColour(level: number, full: Rgb, mid: Rgb, empty: Rgb): string {
  return level >= 0.5 ? mix(mid, full, (level - 0.5) * 2) : mix(empty, mid, level * 2);
}

/**
 * A colour's channels from `#rgb`, `#rrggbb`, `rgb(r g b / a)` or `rgb(r, g, b)` / `rgba(...)`
 * (the forms a browser computes a colour to; pass anything else through `resolveColor` first).
 * `null` for any other value, so a caller never paints silent black.
 */
export function parseRgb(css: string): Rgb | null {
  const value = css.trim();
  const hex = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(value);
  if (hex) {
    const six = hex[1].length === 3 ? [...hex[1]].map((d) => d + d).join('') : hex[1];
    const channel = (i: number): number => parseInt(six.slice(i, i + 2), 16);
    return [channel(0), channel(2), channel(4)];
  }
  const fn = /^rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)\s*(?:[,/]\s*[\d.]+%?\s*)?\)$/i.exec(
    value,
  );
  return fn ? [Math.round(+fn[1]), Math.round(+fn[2]), Math.round(+fn[3])] : null;
}
