// Player shapes, snake_case exactly as teams/serializers.py and teams/forms.py
// send / bind them (no mapping layer).
import { ListParams } from './api-client';

/** `teams.models.ROLE_CHOICES` keys. */
export type Role = 'commander' | 'heavy' | 'scout' | 'medic' | 'ammo';

/** The 19 gameplay stats (0-100). Note the capital `O` in `Offensive_synergy`. */
export interface PlayerStats {
  player_awareness: number;
  game_awareness: number;
  resource_awareness: number;
  decision_making: number;
  positioning: number;
  stamina: number;
  speed: number;
  flexibility: number;
  adaptability: number;
  communication: number;
  teamwork: number;
  Offensive_synergy: number;
  defensive_synergy: number;
  midfield_synergy: number;
  resupply_synergy: number;
  resupply_efficiency: number;
  accuracy: number;
  survival: number;
  special_usage: number;
}

/** `PlayerSerializer`: the full Player (detail, list, Team detail `players`). */
export interface Player extends PlayerStats {
  id: number;
  /** The Team's id. */
  team: number;
  name: string;
  preferred_roles: Role[];
  /** The profile fields (read-only here; written through `PlayerInput`). */
  age: number | null;
  started_playing_age: number | null;
  total_games: number;
  home_site: string;
  height: string;
  /** `Player.overall_rating`: the unweighted mean of the 19 stats. */
  overall_rating: number;
}

/** `PlayerInlineSerializer`: the slim Player inside a Team list row. */
export interface PlayerInline {
  id: number;
  name: string;
  preferred_roles: Role[];
}

/**
 * `PlayerForm` fields. Every key is optional: on add-player an omitted field
 * takes the HTML `player_add` initial; on PATCH it keeps the current value.
 * Edit Player sends a blank `total_games` or Stat as `null`; the server answers
 * with a field error for it.
 */
export interface PlayerInput extends Partial<Record<keyof PlayerStats, number | null>> {
  name?: string;
  preferred_roles?: Role[];
  age?: number | null;
  started_playing_age?: number | null;
  total_games?: number | null;
  home_site?: string;
  height?: string;
}

/**
 * `PlayerFilterSet` plus `ordering`. `ordering` takes the LG-00c sort keys
 * (`name`, `team`, `overall_rating`, `potential`, each stat in lower case, so
 * `offensive_synergy`); a `-` prefix descends.
 */
export interface PlayerListParams extends ListParams {
  team?: number;
  name?: string;
  ordering?: string;
}
