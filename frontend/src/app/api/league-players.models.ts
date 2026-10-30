// League Player screen shapes (ANG-06-16, spec #428 F2), snake_case exactly as the
// `LeagueScreensMixin` actions send them: `league_player_payload` (+ B3 `can_link_self_insert`),
// `watch_player` / `unwatch_player` (`unwatch_all`'s `{removed}` body is not read), and the params of `free_agents` and
// `watch_list` (their rows are ANG-06-15's `PlayerRatingRow` / `PlayerStatRow`). No mapping layer.
import { ListParams } from './api-client';
import { PlayerRef, TeamRef } from './analytics.models';
import { SeasonParam, StatRate } from './league-stats.models';
import { SeasonRef } from './tournaments.models';

/** The League player page's bio block. */
export interface LeaguePlayerBio {
  age: number | null;
  /** Free text, e.g. `5'11"`; may be empty. */
  height: string;
  home_site: string;
  started_playing_age: number | null;
  total_games: number;
  preferred_roles: string[];
}

/** A Regular Season or Career stat row (`_stat_row_payload`). */
export interface LeaguePlayerStatRow {
  team: TeamRef | null;
  games: number;
  /** The Player Stats keys (`points_scored`, `mvp`, ..., `combo_resupply_count`). */
  stats: Record<string, number>;
}

/** One Regular Season row; `season.name` is the row's year label. */
export interface LeaguePlayerSeasonRow extends LeaguePlayerStatRow {
  season: SeasonRef;
}

/** One Season's awards. */
export interface LeaguePlayerAward {
  season: SeasonRef;
  award_labels: string[];
}

/** One Ratings history row. */
export interface RatingsHistoryRow {
  season: SeasonRef;
  age: number | null;
  overall_rating: number;
  potential: number | null;
  /** The 19 rating stats by field name. */
  stats: Record<string, number>;
}

/** `GET leagues/{id}/players/{player_id}/` `data`. */
export interface LeaguePlayer {
  player: PlayerRef;
  watched: boolean;
  overall_rating: number;
  potential: number | null;
  bio: LeaguePlayerBio;
  /** The 19 current rating stats by field name. */
  stats: Record<string, number>;
  seasons: LeaguePlayerSeasonRow[];
  /** Null when the Player has no Rounds in the League. */
  career: LeaguePlayerStatRow | null;
  awards: LeaguePlayerAward[];
  ratings_history: RatingsHistoryRow[];
  /**
   * B3: the Player's Team has a manager and it is the requesting Account. "This is me" also
   * needs the Player not to be the Account's Self-insert already (`currentUser().self_insert`).
   */
  can_link_self_insert: boolean;
}

/** The `PUT` / `DELETE leagues/{id}/watch-list/{player_id}/` 200. */
export interface WatchResult {
  player: PlayerRef;
  watched: boolean;
}

/** `FreeAgentsParamsForm`: `ordering` over the Player Ratings sort keys (default `team`). Paged. */
export interface FreeAgentsParams extends ListParams {
  ordering?: string;
  page: number;
  page_size: number;
}

/** `WatchListParamsForm`: Player Stats' params without a Team (default `-points_scored`). Paged. */
export interface WatchListParams extends ListParams {
  season?: SeasonParam;
  rate?: StatRate;
  ordering?: string;
  page: number;
  page_size: number;
}
