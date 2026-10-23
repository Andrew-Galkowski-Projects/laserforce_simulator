import { playbackSnapshot } from '../../testing/playback-fixtures';
import { deactivatedAt } from './playback-routes';

describe('deactivatedAt (the map dims a deactivated player)', () => {
  const RESPAWN = 16;
  const downed = [playbackSnapshot(), playbackSnapshot({ tick: 100, downed_at: 100 })];

  it('is false for an active player, and with no snapshots at all', () => {
    expect(deactivatedAt([playbackSnapshot()], 50, RESPAWN)).toBe(false);
    expect(deactivatedAt([], 50, RESPAWN)).toBe(false);
  });

  it('is true from the Down until respawn_ticks have passed', () => {
    expect(deactivatedAt(downed, 99, RESPAWN)).toBe(false);
    expect(deactivatedAt(downed, 100, RESPAWN)).toBe(true);
    expect(deactivatedAt(downed, 115.5, RESPAWN)).toBe(true);
    expect(deactivatedAt(downed, 116, RESPAWN)).toBe(false);
  });

  it('is true for good once the player is eliminated', () => {
    const out = [playbackSnapshot(), playbackSnapshot({ tick: 300, eliminated: true })];
    expect(deactivatedAt(out, 299, RESPAWN)).toBe(false);
    expect(deactivatedAt(out, 1800, RESPAWN)).toBe(true);
  });
});
