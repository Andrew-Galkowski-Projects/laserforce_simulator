import { STATS } from '../../api/testing/player-fixtures';
import { STAT_GROUPS, overallRating } from './player-stats';

describe('overallRating', () => {
  it('is the Stat value when all 19 are equal', () => {
    expect(overallRating({ ...STATS, accuracy: 50 })).toBe(50);
    expect(overallRating(Object.fromEntries(Object.keys(STATS).map((k) => [k, 90])))).toBe(90);
  });

  it('is the unweighted mean of a non-uniform set (as Player.overall_rating)', () => {
    // 17 at 50, accuracy 70, survival 88: 1008 / 19 = 53.0526...
    expect(overallRating({ ...STATS, accuracy: 70, survival: 88 })).toBeCloseTo(53.0526, 4);
  });

  it('is null when a Stat is missing or not a number', () => {
    const missing: Partial<typeof STATS> = { ...STATS };
    delete missing.speed;
    expect(overallRating(missing)).toBeNull();
    expect(overallRating({ ...STATS, speed: null })).toBeNull();
    expect(overallRating({ ...STATS, speed: Number.NaN })).toBeNull();
  });

  it('is null when a Stat is outside 0-100', () => {
    expect(overallRating({ ...STATS, speed: 101 })).toBeNull();
    expect(overallRating({ ...STATS, speed: -1 })).toBeNull();
    expect(overallRating({ ...STATS, speed: 0, accuracy: 100 })).not.toBeNull();
  });
});

describe('STAT_GROUPS', () => {
  it('lists the five groups in the Django order', () => {
    expect(STAT_GROUPS.map((g) => g.label)).toEqual([
      'Awareness',
      'Decision-making',
      'Physical',
      'Team',
      'Role',
    ]);
  });

  it('covers every PlayerStats key exactly once', () => {
    const keys = STAT_GROUPS.flatMap((g) => g.stats.map((s) => s.key));
    expect(keys).toHaveLength(19);
    expect(new Set(keys).size).toBe(19);
    expect([...keys].sort()).toEqual(Object.keys(STATS).sort());
  });

  it('labels Stats in plain words', () => {
    const role = STAT_GROUPS.find((g) => g.label === 'Role');
    expect(role?.stats[0]).toEqual({ key: 'Offensive_synergy', label: 'Offensive Synergy' });
  });
});
