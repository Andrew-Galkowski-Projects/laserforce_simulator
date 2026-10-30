// League stats screen shapes (ANG-06-15, spec #417), snake_case exactly as the
// `LeagueScreensMixin` actions send them (`league_screen_api_views.py`): `season_scope_meta`,
// `player_stat_rows`, `player_rating_rows`, `leaders`, `team_stats` (`TeamStatRow`),
// `feat_rows_payload` (`FeatRow`), `team_feats_payload`, `game_log`, `power_rankings`
// (`PowerRankingRow`), `playoffs` (`bracket_payload`, `WorldsQualifier`). No mapping layer.
import { ListParams } from './api-client';
import { PlayerRef, RoundRef, TeamRef } from './analytics.models';
import { LeaderRow } from './leagues.models';
import { BracketRound, SeasonRef } from './tournaments.models';

/** One `meta.season_scope.options[]` entry; `year` is the start date's year, or null. */
export interface SeasonOption {
  id: number;
  name: string;
  year: number | null;
}

/**
 * `meta.season_scope`: the resolved scope (a Season, `'career'`, or null when the League has no
 * Season) and the League's Seasons, newest first.
 */
export interface SeasonScope {
  selected: SeasonRef | 'career' | null;
  options: SeasonOption[];
}

export interface ScopedMeta {
  season_scope: SeasonScope;
}

/** The Teams enrolled in the displayed Season, by name (`[]` with no Season). */
export interface TeamOptionsMeta {
  team_options: TeamRef[];
}

/** `?season=`: a Season id or `career`; absent is the displayed Season. */
export type SeasonParam = number | 'career';

/** `?rate=` on Player Stats. */
export type StatRate = 'total' | 'per_game' | 'per_10';

/** One League Leaders row: the dashboard's `LeaderRow` plus the Player ref and watch flag. */
export interface LeaderBoardRow extends LeaderRow {
  player: PlayerRef;
  watched: boolean;
}

/** `GET leagues/{id}/leaders/`: four top-10 boards in rank order. */
export interface LeaderBoards {
  avg_tags: LeaderBoardRow[];
  avg_score: LeaderBoardRow[];
  fewest_tagged: LeaderBoardRow[];
  tag_ratio: LeaderBoardRow[];
}

/** One Player Stats row (`player_stat_rows`). */
export interface PlayerStatRow {
  player: PlayerRef;
  watched: boolean;
  team: TeamRef | null;
  role: string;
  games: number;
  stats: Record<string, number>;
}

/** One Player Ratings row (`player_rating_rows`). */
export interface PlayerRatingRow {
  player: PlayerRef;
  watched: boolean;
  preferred_roles: string[];
  overall_rating: number;
  potential: number | null;
  age: number | null;
  /** Free text, e.g. `5'11"`; may be empty. */
  height: string;
  home_site: string;
  started_playing_age: number | null;
  total_games: number;
  /** The 19 rating stats by field name. */
  stats: Record<string, number>;
}

/** One Team Stats row: the `TeamStatRow` dataclass plus `team`. */
export interface TeamStatRow {
  team_id: number;
  team_name: string;
  rounds_played: number;
  avg_points_for: number;
  avg_points_against: number;
  avg_margin: number;
  avg_survivors: number;
  total_tags_landed: number;
  total_times_tagged: number;
  base_captures: number;
  missiles_fired: number;
  missiles_hit: number;
  nukes_fired: number;
  nukes_landed: number;
  cancelled_nukes: number;
  team: TeamRef;
}

/** One `FeatBadge`. */
export interface FeatBadge {
  kind: string;
  label: string;
  is_season_best: boolean;
}

/** One Statistical Feats row: the `FeatRow` dataclass plus `player` and `watched`. */
export interface FeatRow {
  player_id: number;
  player_name: string;
  role: string;
  team_id: number | null;
  team_name: string;
  round_id: number;
  opp_team_name: string;
  result: string;
  season_id: number | null;
  season_name: string;
  /** The 13 box-score keys. */
  stats: Record<string, number>;
  feats: FeatBadge[];
  player: PlayerRef;
  watched: boolean;
}

/** One Team feat (today: comeback win); `team` is null when the winner can't be resolved. */
export interface TeamFeat {
  kind: string;
  label: string;
  team_name: string;
  team: TeamRef | null;
  round_id: number | null;
}

/** `GET leagues/{id}/statistical-feats/` `data`: the page of feats plus every Team feat. */
export interface StatisticalFeats {
  feats: FeatRow[];
  team_feats: TeamFeat[];
}

/** One Game Log row: a played Round and its score. */
export interface GameLogRow {
  round: RoundRef;
  red_points: number;
  blue_points: number;
  winner: TeamRef | null;
}

/** One Power Rankings row: the `PowerRankingRow` dataclass plus `team`. */
export interface PowerRankingRow {
  rank: number;
  team_id: number;
  team_name: string;
  mean_rating: number;
  win_pct: number;
  avg_score_diff: number;
  norm_rating: number;
  norm_win_pct: number;
  norm_score_diff: number;
  power_score: number;
  team: TeamRef;
}

/** A bracket's stage (`build_playoffs`): `''` for a Season-wide bracket. */
export type BracketStage = '' | 'worlds' | 'regional_playoff' | 'last_chance';

/** One playoff bracket (`bracket_payload`). */
export interface PlayoffBracket {
  /** The DOM-id discriminator, e.g. `2`, `2-1`, `2-1-lc`, `2-worlds`. */
  key: string;
  name: string;
  stage: BracketStage;
  /** `''` for a Season-wide or regional bracket. */
  stage_label: string;
  conference: { id: number; name: string } | null;
  champion: TeamRef | null;
  pending: boolean;
  rounds: BracketRound[];
}

/** `WorldsQualifier.PROVENANCE_*`. */
export type WorldsProvenance = 'conference_champion' | 'regular_season' | 'last_chance';

/** One Worlds qualifier: the `WorldsQualifier` dataclass plus `provenance_label`. */
export interface WorldsQualifier {
  team_id: number;
  team_name: string;
  conference_id: number;
  conference_name: string;
  tier: number;
  provenance: WorldsProvenance;
  matches_played: number;
  league_points: number;
  round_wins: number;
  total_score: number;
  seed: number;
  provenance_label: string;
}

/** `GET leagues/{id}/playoffs/` `data`; `season` is null when the League has no Season. */
export interface PlayoffsScreen {
  season: SeasonRef | null;
  brackets: PlayoffBracket[];
  worlds_qualifiers: WorldsQualifier[];
}

// Each method's query params, as the action's params Form names them. The URL keeps the Team
// as `?team=`; `stats-shared/stats-query.ts` sends it as `team_id`. `ordering` is `key` or `-key`.

/** A paged screen always sends `page` and `page_size`. */
interface PagedParams extends ListParams {
  page: number;
  page_size: number;
}

export interface LeadersParams extends ListParams {
  season?: SeasonParam;
}

export interface PlayerStatsParams extends PagedParams {
  season?: SeasonParam;
  rate?: StatRate;
  team_id?: number;
  ordering?: string;
}

export interface PlayerRatingsParams extends PagedParams {
  team_id?: number;
  ordering?: string;
}

export interface TeamStatsParams extends ListParams {
  season?: SeasonParam;
  ordering?: string;
}

export interface StatisticalFeatsParams extends PagedParams {
  season?: SeasonParam;
  team_id?: number;
  ordering?: string;
}

export interface GameLogParams extends ListParams {
  season?: SeasonParam;
  team_id?: number;
  ordering?: string;
}

export interface PowerRankingsParams extends ListParams {
  season?: SeasonParam;
  ordering?: string;
}

/**
 * Playoffs takes no Career (a bracket belongs to one Season): `career` is a 400. Typed as
 * `SeasonParam` so `statsApiParams` (with `career: false`, it never sends it) fits as is.
 */
export interface PlayoffsParams extends ListParams {
  season?: SeasonParam;
}
