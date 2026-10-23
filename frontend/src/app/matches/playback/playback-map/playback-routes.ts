// The beta Playback map's route maths, ported from the Django round-events overlay
// (`pbInitMap` / `pbPosAt` / `pbDrawMap` in templates/matches/game_round_events.html). Beta: the
// owner is still changing it (MOVE-07), so it is deliberately not pinned by tests.
import { PlaybackMapPlayer, PlaybackSnapshot } from '../../../api/rounds.models';
import { TICKS_PER_SECOND } from '../../shared/clock';
import { snapshotAt } from '../snapshot-at';

/** Comet-tail length in seconds (32 ticks). */
export const TRAIL_SEC = 16;
/** Trail sample spacing in seconds, on an absolute grid. */
export const TRAIL_DT = 0.1;

/** `[seconds, row, col]`: a cell the player stands on at that time. */
export type Keyframe = readonly [number, number, number];

/**
 * A player's route as keyframes in seconds: the spawn at 0, then for each Advance at tick `ts`
 * the polyline's cells spread evenly over `ts/2 .. (ts+1)/2`. A hold keyframe at the Advance's
 * start is added only after a real stationary gap: a duplicate-time point would make the
 * Catmull-Rom curve overshoot.
 */
export function routeKeyframes(player: PlaybackMapPlayer): Keyframe[] {
  const kf: Keyframe[] = [[0, player.spawn[0], player.spawn[1]]];
  let prev: readonly [number, number] = player.spawn;
  for (const [ts, poly] of player.moves) {
    if (!poly.length) {
      continue;
    }
    const t0 = ts / TICKS_PER_SECOND;
    const t1 = (ts + 1) / TICKS_PER_SECOND;
    if (t0 > kf[kf.length - 1][0] + 1e-6) {
      kf.push([t0, prev[0], prev[1]]);
    }
    poly.forEach((cell, i) => {
      kf.push([t0 + (t1 - t0) * ((i + 1) / poly.length), cell[0], cell[1]]);
      prev = cell;
    });
  }
  return kf;
}

/** Uniform Catmull-Rom on one axis. */
function catmullRom(p0: number, p1: number, p2: number, p3: number, u: number): number {
  const u2 = u * u;
  const u3 = u2 * u;
  return (
    0.5 *
    (2 * p1 +
      (-p0 + p2) * u +
      (2 * p0 - 5 * p1 + 4 * p2 - p3) * u2 +
      (-p0 + 3 * p1 - 3 * p2 + p3) * u3)
  );
}

/** The interpolated `[row, col]` (fractional) at `t` seconds; clamped to the route's ends. */
export function positionAt(kf: readonly Keyframe[], t: number): [number, number] {
  if (t <= kf[0][0]) {
    return [kf[0][1], kf[0][2]];
  }
  const last = kf[kf.length - 1];
  if (t >= last[0]) {
    return [last[1], last[2]];
  }
  let lo = 0;
  let hi = kf.length - 1;
  while (lo + 1 < hi) {
    const mid = (lo + hi) >> 1;
    if (kf[mid][0] <= t) {
      lo = mid;
    } else {
      hi = mid;
    }
  }
  const k0 = kf[Math.max(0, lo - 1)];
  const k1 = kf[lo];
  const k2 = kf[lo + 1];
  const k3 = kf[Math.min(kf.length - 1, lo + 2)];
  const span = k2[0] - k1[0];
  const u = span > 0 ? (t - k1[0]) / span : 0;
  return [catmullRom(k0[1], k1[1], k2[1], k3[1], u), catmullRom(k0[2], k1[2], k2[2], k3[2], u)];
}

/**
 * The trail's sample times ending at `t`: fixed multiples of `TRAIL_DT` (an absolute grid, so the
 * vertices stay put as the cursor moves and don't shimmer with play speed), then `t` itself.
 */
export function trailTimes(t: number): number[] {
  const start = Math.max(0, t - TRAIL_SEC);
  const times: number[] = [];
  for (let i = Math.ceil(start / TRAIL_DT); i * TRAIL_DT <= t - 1e-6; i++) {
    times.push(i * TRAIL_DT);
  }
  times.push(t);
  return times;
}

/**
 * Whether the player is deactivated at `tick` (the map dims their marker and trail): eliminated,
 * or within `respawnTicks` of their last Down.
 */
export function deactivatedAt(
  snapshots: readonly PlaybackSnapshot[],
  tick: number,
  respawnTicks: number,
): boolean {
  const snap = snapshotAt(snapshots, tick);
  if (!snap) {
    return false;
  }
  return snap.eliminated || (snap.downed_at !== null && tick - snap.downed_at < respawnTicks);
}

/** A cell's centre in image pixels: `((c + 0.5) * zone, (r + 0.5) * zone)`. */
export function cellToPixel(row: number, col: number, zone: number): [number, number] {
  return [(col + 0.5) * zone, (row + 0.5) * zone];
}
