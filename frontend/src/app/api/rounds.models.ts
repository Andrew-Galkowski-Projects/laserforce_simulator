// GameRound / PlayerRoundState / GameEvent shapes, snake_case exactly as
// matches/serializers.py and matches/forms.py send / bind them (no mapping layer).
// Ticks: 1 tick = 0.5 s, a round is 0-1800; 1801 means "survived".
import { ListParams } from './api-client';

/** `GameRoundListSerializer`: a row of `GET rounds/`. Foreign keys are ids. */
export interface GameRoundListItem {
  id: number;
  /** null for a standalone Round. */
  match: number | null;
  round_number: number;
  team_red: number | null;
  team_blue: number | null;
  /** ISO 8601 datetime. */
  date_played: string;
  red_points: number;
  blue_points: number;
  red_team_eliminated: boolean;
  blue_team_eliminated: boolean;
  /** Tick of the team elimination; 1801 if neither team was eliminated. */
  eliminated_at: number;
  winner: number | null;
  is_completed: boolean;
  team_red_name: string;
  team_blue_name: string;
  /** null when `winner` is null (a tie). */
  winner_name: string | null;
}

/** `PlayerRoundStateSerializer` (every model field but `game_round`). */
export interface PlayerRoundState {
  id: number;
  /** The Player's id. */
  player: number;
  team_color: 'red' | 'blue';
  role: string;
  starting_lives: number;
  starting_shots: number;
  starting_special: number;
  starting_missiles: number;
  /** `PlayerRoundState.tag_id` choice (0-15). */
  last_tagged_id: number;
  shields: number;
  last_downed_time: number | null;
  neutral_base_destroyed: boolean;
  opposing_base_destroyed: boolean;
  /** 0 = red zone, 1 = neutral, 2 = blue zone. */
  zone_fallback: number;
  cell_row: number | null;
  cell_col: number | null;
  special_active_until: number | null;
  is_hiding: boolean;
  final_lives: number;
  final_shots: number;
  final_special: number;
  final_missiles: number;
  final_medic_hits: number;
  points_scored: number;
  tags_made: number;
  shots_missed: number;
  times_tagged: number;
  specials_used: number;
  own_specials_cancelled: number;
  enemy_nuke_cancels: number;
  ally_nuke_cancels: number;
  medic_lives_removed_from_nuke: number;
  lives_lost_to_nukes: number;
  missiles_landed: number;
  times_missiled: number;
  resupplies_given: number;
  combo_resupply_count: number;
  times_tagged_in_reset_window: number;
  follow_up_shots: number;
  reaction_shots: number;
  ticks_active: number;
  ticks_not_targetable: number;
  ticks_reset_window: number;
  missile_points: number;
  /** Per-opponent counts keyed by Player id. */
  specific_tags: Record<string, unknown>;
  was_eliminated_at: number;
}

/**
 * One **Round scoreboard** row (`matches/services.py` `_player_row`): the 28
 * `round_summary.PLAYER_ROW_KEYS` plus `player_id`.
 */
export interface ScoreboardRow {
  player_id: number;
  name: string;
  role: string;
  team_color: 'red' | 'blue';
  /** Ticks; 1801 (or 0, legacy) means survived; null on legacy rows (then `final_lives` decides). */
  was_eliminated_at: number | null;
  /** `m:ss` when eliminated, else `''`. */
  eliminated_timestamp: string;
  is_eliminated: boolean;
  final_lives: number;
  points_scored: number;
  /** The SM5 MVP score (2 decimals). */
  mvp: number;
  tags_made: number;
  times_tagged: number;
  /** Integer percentage, 0-100. */
  accuracy: number;
  final_shots: number;
  final_special: number;
  shots_used: number;
  missiles_used: number;
  starting_missiles: number;
  missiles_landed: number;
  times_missiled: number;
  final_medic_hits: number;
  medic_lives_removed_from_nuke: number;
  follow_up_shots: number;
  reaction_shots: number;
  resupplies_given: number;
  specials_used: number;
  combo_resupply_count: number;
  specific_tags_count: number;
  special_cost: number;
}

/** One Side of `round_scoreboard`: rows ordered `-points_scored, role, name`. */
export interface ScoreboardSide {
  players: ScoreboardRow[];
  survivors: number;
  /** True when no player on this Side has lives left. */
  eliminated: boolean;
  /** The elimination bonus this Side earned (the other Side was eliminated), else 0. */
  elim_bonus: number;
}

/** `GameRoundSerializer.scoreboard`, grouped by the Side each player played. */
export interface RoundScoreboard {
  red: ScoreboardSide;
  blue: ScoreboardSide;
}

/** `GameRoundSerializer`: `GET rounds/{id}/` and the create response. */
export interface GameRound extends GameRoundListItem {
  player_states: PlayerRoundState[];
  scoreboard: RoundScoreboard;
}

/** `GameEvent.EVENT_TYPES` keys. */
export type GameEventType =
  | 'tag'
  | 'locking'
  | 'missiled'
  | 'special'
  | 'miss'
  | 'resupply_ammo'
  | 'resupply_lives'
  | 'elimination'
  | 'team_elimination'
  | 'nuke_cancelled'
  | 'medic_reset';

/** `GameEventSerializer` (every model field but `game_round`): a row of `GET rounds/{id}/events/`. */
export interface GameEvent {
  id: number;
  /** Ticks into the round. */
  timestamp: number;
  event_type: GameEventType;
  points_awarded: number;
  description: string;
  metadata: Record<string, unknown>;
  /** The acting Player's id. */
  actor: number;
  /** The target Player's id, if any. */
  target: number | null;
}

/**
 * `SingleRoundSetupForm` fields. Teams must be the caller's own valid rosters;
 * `arena_map` must have a confirmed zone config (omit for the 3-zone fallback).
 */
export interface RoundInput {
  team_red: number;
  team_blue: number;
  arena_map?: number | null;
}

/** `GameRoundFilterSet` plus `ordering` (`date_played`, `round_number`, `id`; `-` descends). */
export interface RoundListParams extends ListParams {
  match?: number;
  arena_map?: number;
  /** true: only Single Rounds (no Match); false: only a Match's Rounds. */
  single_round?: boolean;
  ordering?: string;
}

/**
 * `GameEventFilterSet`. Events always come in `(timestamp, id)` order;
 * `timestamp_min` / `timestamp_max` are inclusive ticks.
 */
export interface GameEventListParams extends ListParams {
  event_type?: GameEventType;
  actor?: number;
  target?: number;
  timestamp_min?: number;
  timestamp_max?: number;
}
