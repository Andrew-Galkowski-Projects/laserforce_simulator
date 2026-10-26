// Shared spec fixtures. Imported only by *.spec.ts files.
import { Player, PlayerStats } from '../players.models';

/**
 * The read-only fields every Player carries: a blank profile (no age, the model defaults), the
 * Overall of all-50 Stats and a placeholder Team name.
 */
export const PROFILE: Pick<
  Player,
  | 'age'
  | 'started_playing_age'
  | 'total_games'
  | 'home_site'
  | 'height'
  | 'overall_rating'
  | 'team_name'
> = {
  team_name: 'Team',
  age: null,
  started_playing_age: null,
  total_games: 0,
  home_site: '',
  height: '',
  overall_rating: 50,
};

/** All 19 stats at 50. */
export const STATS: PlayerStats = {
  player_awareness: 50,
  game_awareness: 50,
  resource_awareness: 50,
  decision_making: 50,
  positioning: 50,
  stamina: 50,
  speed: 50,
  flexibility: 50,
  adaptability: 50,
  communication: 50,
  teamwork: 50,
  Offensive_synergy: 50,
  defensive_synergy: 50,
  midfield_synergy: 50,
  resupply_synergy: 50,
  resupply_efficiency: 50,
  accuracy: 50,
  survival: 50,
  special_usage: 50,
};
