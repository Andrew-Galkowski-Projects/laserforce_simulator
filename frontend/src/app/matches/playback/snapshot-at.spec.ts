import { playbackSnapshot } from '../testing/playback-fixtures';
import { snapshotAt } from './snapshot-at';

describe('snapshotAt', () => {
  const snapshots = [
    playbackSnapshot({ tick: 0, score: 0 }),
    playbackSnapshot({ tick: 10, score: 100 }),
    playbackSnapshot({ tick: 25, score: 200 }),
    playbackSnapshot({ tick: 40, score: 300 }),
  ];

  it('returns null before the first snapshot', () => {
    expect(snapshotAt([playbackSnapshot({ tick: 5 })], 4)).toBeNull();
    expect(snapshotAt([], 100)).toBeNull();
  });

  it('returns the snapshot exactly on its tick', () => {
    expect(snapshotAt(snapshots, 0)?.score).toBe(0);
    expect(snapshotAt(snapshots, 10)?.score).toBe(100);
    expect(snapshotAt(snapshots, 25)?.score).toBe(200);
  });

  it('returns the latest earlier snapshot between ticks', () => {
    expect(snapshotAt(snapshots, 9)?.score).toBe(0);
    expect(snapshotAt(snapshots, 11)?.score).toBe(100);
    expect(snapshotAt(snapshots, 39.6)?.score).toBe(200);
  });

  it('returns the last snapshot after the last tick', () => {
    expect(snapshotAt(snapshots, 40)?.score).toBe(300);
    expect(snapshotAt(snapshots, 1800)?.score).toBe(300);
  });
});
