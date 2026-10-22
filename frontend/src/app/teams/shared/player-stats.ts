// The Player Stats as the Player form shows them (ANG-06b): the five groups and the
// live Overall rating.
import { PlayerStats } from '../../api/players.models';

/** The 19 Stats in five groups, in the order of Django's `teams/views.py` `_STAT_GROUPS`. */
export const STAT_GROUPS: readonly {
  label: string;
  stats: readonly { key: keyof PlayerStats; label: string }[];
}[] = [
  {
    label: 'Awareness',
    stats: [
      { key: 'player_awareness', label: 'Player Awareness' },
      { key: 'game_awareness', label: 'Game Awareness' },
      { key: 'resource_awareness', label: 'Resource Awareness' },
    ],
  },
  {
    label: 'Decision-making',
    stats: [{ key: 'decision_making', label: 'Decision Making' }],
  },
  {
    label: 'Physical',
    stats: [
      { key: 'positioning', label: 'Positioning' },
      { key: 'stamina', label: 'Stamina' },
      { key: 'speed', label: 'Speed' },
      { key: 'flexibility', label: 'Flexibility' },
      { key: 'adaptability', label: 'Adaptability' },
    ],
  },
  {
    label: 'Team',
    stats: [
      { key: 'communication', label: 'Communication' },
      { key: 'teamwork', label: 'Teamwork' },
    ],
  },
  {
    label: 'Role',
    stats: [
      { key: 'Offensive_synergy', label: 'Offensive Synergy' },
      { key: 'defensive_synergy', label: 'Defensive Synergy' },
      { key: 'midfield_synergy', label: 'Midfield Synergy' },
      { key: 'resupply_synergy', label: 'Resupply Synergy' },
      { key: 'resupply_efficiency', label: 'Resupply Efficiency' },
      { key: 'accuracy', label: 'Accuracy' },
      { key: 'survival', label: 'Survival' },
      { key: 'special_usage', label: 'Special Usage' },
    ],
  },
];

/** Every Stat key, in group order. */
export const STAT_KEYS: readonly (keyof PlayerStats)[] = STAT_GROUPS.flatMap((g) =>
  g.stats.map((s) => s.key),
);

/**
 * The Overall rating: the unweighted mean of the 19 Stats, as `Player.overall_rating`
 * (no Preferred-role boost; that is simulation-only). `null` when any Stat is missing,
 * not a number, or outside 0-100.
 */
export function overallRating(
  stats: Partial<Record<keyof PlayerStats, number | null>>,
): number | null {
  let sum = 0;
  for (const key of STAT_KEYS) {
    const value = stats[key];
    if (typeof value !== 'number' || Number.isNaN(value) || value < 0 || value > 100) {
      return null;
    }
    sum += value;
  }
  return sum / STAT_KEYS.length;
}
