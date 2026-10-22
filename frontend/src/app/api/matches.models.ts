// Match shapes, snake_case exactly as matches/serializers.py and matches/forms.py
// send / bind them (no mapping layer).
import { ListParams } from './api-client';

/** `Match.MATCH_TYPES` keys. */
export type MatchType = 'tournament' | 'friendly';

/**
 * `MatchSerializer` (`fields = "__all__"` plus `round_ids`): every list row,
 * `GET matches/{id}/` and the create response. Foreign keys are ids.
 * `*_eliminated_at` is a tick; 1801 means the team survived.
 */
export interface Match {
  id: number;
  /** The Match's GameRound ids. */
  round_ids: number[];
  match_type: MatchType;
  /** ISO 8601 datetime. */
  date_played: string;
  red_round1_points: number;
  blue_round1_points: number;
  red_round1_eliminated: boolean;
  blue_round1_eliminated: boolean;
  round1_eliminated_at: number;
  red_round2_points: number;
  blue_round2_points: number;
  red_round2_eliminated: boolean;
  blue_round2_eliminated: boolean;
  round2_eliminated_at: number;
  red_bonus_points: number;
  blue_bonus_points: number;
  leg: number;
  is_completed: boolean;
  team_red: number;
  team_blue: number;
  winner: number | null;
  team_red_name: string;
  team_blue_name: string;
  /** null when `winner` is null (a tie, or a Match not finished yet). */
  winner_name: string | null;
  /** The `Match` properties: round 1 + round 2 + elimination bonus. */
  red_total_points: number;
  blue_total_points: number;
  red_rounds_won: number;
  blue_rounds_won: number;
  season: number | null;
  season_phase: number | null;
  conference: number | null;
  /** The Account id; null for an Unmanaged row. */
  manager: number | null;
}

/**
 * `MatchSetupForm` fields. Teams must be the caller's own valid rosters;
 * `arena_map` must have a confirmed zone config (omit for the 3-zone fallback).
 */
export interface MatchInput {
  team_red: number;
  team_blue: number;
  match_type: MatchType;
  arena_map?: number | null;
}

/**
 * `MatchFilterSet` plus `ordering` (`date_played`, `id`; `-` descends).
 * `team` matches either side; dates are `YYYY-MM-DD`, inclusive.
 */
export interface MatchListParams extends ListParams {
  team?: number;
  season?: number;
  match_type?: MatchType;
  date_played_after?: string;
  date_played_before?: string;
  ordering?: string;
}
