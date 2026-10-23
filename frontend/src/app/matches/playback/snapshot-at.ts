import { PlaybackSnapshot } from '../../api/rounds.models';

/**
 * The latest snapshot with `snapshot.tick <= tick`, or `null` before the first one. `snapshots`
 * must be in tick order (as `RoundPlayback` sends them). A binary search: O(log n) per call.
 */
export function snapshotAt(
  snapshots: readonly PlaybackSnapshot[],
  tick: number,
): PlaybackSnapshot | null {
  let lo = 0;
  let hi = snapshots.length - 1;
  let found: PlaybackSnapshot | null = null;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (snapshots[mid].tick <= tick) {
      found = snapshots[mid];
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return found;
}
