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
  /** The special bar: RV-07b (#577) fills it in; always `null` until then. */
  special: null;
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
    special: null,
  };
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

/** A `#rrggbb` token as channels; anything else reads as black. */
export function hexRgb(hex: string): Rgb {
  const m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex.trim());
  return m ? [parseInt(m[1], 16), parseInt(m[2], 16), parseInt(m[3], 16)] : [0, 0, 0];
}
