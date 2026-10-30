// League Team screen shapes (ANG-06-16, spec #428 F1), snake_case exactly as the
// `LeagueScreensMixin` / `LeagueViewSet` team actions send them: `team_roster` (B1 bio fields via
// `player_bio`), `team_schedule` (`build_team_schedule`), `team_history` and
// `team_history_players`. Each of the first three sends `meta.team_options` (B2). No mapping layer.
import { ListParams } from './api-client';
import { PlayerRef, TeamRef } from './analytics.models';
import { LeagueRef } from './leagues.models';
import { Role } from './players.models';
import { SeasonRef } from './tournaments.models';

/** One roster row: the Player, its watch flag and its bio (B1); `role` on a starter only. */
export interface RosterRow {
  /** A starter's slot role; absent on a bench row. */
  role?: Role;
  player: PlayerRef;
  watched: boolean;
  overall_rating: number;
  potential: number | null;
  age: number | null;
  /** Free text, e.g. `5'11"`; may be empty. */
  height: string;
  home_site: string;
  started_playing_age: number | null;
  total_games: number;
}

/** `GET leagues/{id}/teams/{team_id}/roster/` `data`. */
export interface TeamRoster {
  team: TeamRef;
  starters: RosterRow[];
  bench: RosterRow[];
}

/** One Upcoming row of the Team schedule (`_build_team_schedule_rows`). */
export interface ScheduleRow {
  matchday: number;
  round_number: number;
  /** `YYYY-MM-DD`. */
  date: string;
  red_team_id: number;
  red_team_name: string;
  blue_team_id: number;
  blue_team_name: string;
}

/** The picked Team's result in a played Round. */
export type ScheduleOutcome = 'W' | 'L' | 'T';

/** One Completed row: an Upcoming row plus the played Round and its score. */
export interface CompletedScheduleRow extends ScheduleRow {
  game_round_id: number;
  red_score: number;
  blue_score: number;
  outcome: ScheduleOutcome;
}

/** One member-night game a Player of the Team appeared in (`_member_night_game_dict`). */
export interface MemberNightAppearance {
  match_id: number;
  team_a_name: string;
  team_b_name: string;
  is_completed: boolean;
  red_points: number;
  blue_points: number;
  winner_name: string | null;
  rounds: { id: number; round_number: number }[];
  /** The Team's Players who played, by name. */
  players: string[];
}

/** `GET leagues/{id}/teams/{team_id}/schedule/` `data` (a 404 with no Season). */
export interface TeamSchedule {
  league: LeagueRef;
  season: SeasonRef;
  team: TeamRef;
  upcoming: ScheduleRow[];
  completed: CompletedScheduleRow[];
  member_night_appearances: MemberNightAppearance[];
}

/** Team History's Overall block. */
export interface TeamHistoryOverall {
  wins: number;
  losses: number;
  ties: number;
  playoff_appearances: number;
  championships: number;
}

/** One Team History Seasons row; `year` / `rank` are null when unknown. */
export interface TeamHistorySeason {
  season: SeasonRef;
  year: number | null;
  wins: number;
  losses: number;
  ties: number;
  rank: number | null;
}

/** `GET leagues/{id}/teams/{team_id}/history/` `data`: Seasons year descending. */
export interface TeamHistory {
  team: TeamRef;
  overall: TeamHistoryOverall;
  seasons: TeamHistorySeason[];
}

/** One Team History Players row (`team_history_players`). */
export interface TeamHistoryPlayerRow {
  player: PlayerRef;
  watched: boolean;
  /** Whether the Player is on this Team now (else: now on another team). */
  on_team: boolean;
  games_played: number;
  last_season_year: number | null;
  /** `points_scored`, `tags_made`, `times_tagged`, `missiles_landed`, `resupplies_given`, `specials_used`. */
  stats: Record<string, number>;
}

/**
 * `TeamHistoryPlayersParamsForm`: `ordering` is one of `TEAM_HISTORY_PLAYERS_SORT_KEYS` (name,
 * games_played, points_scored, tags_made, times_tagged, missiles_landed, resupplies_given,
 * specials_used, last_season_year), or `-key`; default `name`. Always paged.
 */
export interface TeamHistoryPlayersParams extends ListParams {
  ordering?: string;
  page: number;
  page_size: number;
}
