// The four analytics responses (ANG-06-9), snake_case exactly as the API sends them:
// team / player head-to-head (`build_team_h2h` / `build_player_h2h`), compare rounds
// (`build_round_comparison`) and Team match history (`team_match_history_payload`).
import { ListParams } from './api-client';
import { Role } from './players.models';
import { Side } from './rounds.models';

/** `{id, name}`: a Team (`team_ref`). */
export interface TeamRef {
  id: number;
  name: string;
}

/** A Player with its Team (`player_ref`); `team` is null for a teamless Player. */
export interface PlayerRef {
  id: number;
  name: string;
  team: TeamRef | null;
}

/** `?provenance=`: every game, real games only, or simulated only. */
export type Provenance = 'all' | 'real' | 'sim';

/** W-L-T over `n` games, from A's point of view. */
export interface WinLossRecord {
  wins: number;
  losses: number;
  ties: number;
  n: number;
}

/** Mean score margin (A − B) over `n` Rounds; 0 when `n` is 0. */
export interface ScoreMargin {
  mean_margin: number;
  n: number;
}

/** One row of the per-map table; `arena_map_name` is "No map (3-zone)" for a null map. */
export interface H2HMapRow {
  arena_map_id: number | null;
  arena_map_name: string;
  games: number;
  wins: number;
  losses: number;
  ties: number;
  mean_margin: number;
}

/** `[round #, value]` chart points, oldest Round first, x counting from 1. */
export type H2HSeries = [number, number][];

/** `GET teams/head-to-head/` params (`TeamH2HParamsForm`); dates are `YYYY-MM-DD`. */
export interface TeamH2HParams extends ListParams {
  team_a: number;
  team_b: number;
  provenance?: Provenance;
  from?: string;
  to?: string;
}

/** A Team's most impactful Player over the head-to-head Rounds. */
export interface ImpactfulPlayer {
  player_id: number;
  name: string;
  mvp_total: number;
  games: number;
}

/** One head-to-head game; `match_id` is null on a Round row, `round_id` on a Match row. */
export interface TeamH2HDetailRow {
  kind: 'match' | 'round';
  date_played: string;
  label: string;
  team_a_score: number;
  team_b_score: number;
  score_str: string;
  /** The winning Team's name, or "—". */
  winner_name: string;
  /** The Django page; the SPA links by `match_id` / `round_id` instead. */
  detail_url: string;
  match_id: number | null;
  round_id: number | null;
  is_simulated: boolean;
}

/** `GET teams/head-to-head/`: the **Head-to-head record** of two Teams. */
export interface TeamHeadToHead {
  team_a: TeamRef;
  team_b: TeamRef;
  provenance: Provenance;
  date_from: string | null;
  date_to: string | null;
  match_record: WinLossRecord;
  round_record: WinLossRecord;
  score_margin: ScoreMargin;
  avg_survivors: { team_a_avg: number; team_b_avg: number; n: number };
  top_impactful: { team_a: ImpactfulPlayer | null; team_b: ImpactfulPlayer | null };
  per_map_breakdown: H2HMapRow[];
  detail_list: TeamH2HDetailRow[];
  margin_series: H2HSeries;
  cumulative_wl_series: H2HSeries;
}

/** `GET players/head-to-head/` params (`PlayerH2HParamsForm`). */
export interface PlayerH2HParams extends ListParams {
  player_a: number;
  player_b: number;
  role?: Role;
  provenance?: Provenance;
  from?: string;
  to?: string;
}

/** Tags each way between the two Players: per-Round averages and totals. */
export interface TagStats {
  avg_tags_a_to_b: number;
  avg_tags_b_to_a: number;
  total_tags_a_to_b: number;
  total_tags_b_to_a: number;
  n: number;
}

/** One row of the per-Role table: the Role both Players played. */
export interface PlayerH2HRoleRow {
  role: Role;
  games: number;
  wins: number;
  losses: number;
  ties: number;
  mean_margin: number;
  avg_tags_a_to_b: number;
  avg_tags_b_to_a: number;
}

/** One head-to-head Round between the two Players, newest first. */
export interface PlayerH2HDetailRow {
  round_id: number;
  date_played: string;
  role_a: Role;
  role_b: Role;
  player_a_team_score: number;
  player_b_team_score: number;
  score_str: string;
  tags_a_to_b: number;
  tags_b_to_a: number;
  /** "Player A", "Player B" or "Tie". */
  winner_label: string;
  detail_url: string;
  is_simulated: boolean;
}

/** `GET players/head-to-head/`: the **Player head-to-head record**. */
export interface PlayerHeadToHead {
  player_a: PlayerRef;
  player_b: PlayerRef;
  role: Role | null;
  provenance: Provenance;
  date_from: string | null;
  date_to: string | null;
  round_record: WinLossRecord;
  score_margin: ScoreMargin;
  tag_stats: TagStats;
  per_role_breakdown: PlayerH2HRoleRow[];
  per_map_breakdown: H2HMapRow[];
  detail_list: PlayerH2HDetailRow[];
  margin_series: H2HSeries;
  cumulative_wl_series: H2HSeries;
}

/** `round_ref`: a Round with its Teams. */
export interface RoundRef {
  id: number;
  round_number: number;
  match_id: number | null;
  date_played: string;
  team_red: TeamRef;
  team_blue: TeamRef;
}

/** One stat across the two Rounds; `delta` is `b - a`, null when a side is missing. */
export interface StatDelta {
  a: number | null;
  b: number | null;
  delta: number | null;
}

/** One Player's stat deltas; the `*_a` / `*_b` fields are null when it missed that Round. */
export interface PlayerStatDeltaRow {
  player_id: number;
  name: string;
  role_a: Role | null;
  role_b: Role | null;
  side_a: Side | null;
  side_b: Side | null;
  stats: Record<string, StatDelta>;
  /** `stats` in `stat_keys` order. */
  cells: StatDelta[];
}

/** One shared Team's cumulative points, as `[tick, points]` pairs per Round. */
export interface PointsSeries {
  team_id: number;
  team_name: string;
  a: [number, number][];
  b: [number, number][];
}

/** `GET rounds/compare/?round_a=&round_b=`. */
export interface RoundComparison {
  round_a: RoundRef;
  round_b: RoundRef;
  stat_keys: string[];
  team_ids: number[];
  points_series: PointsSeries[];
  deltas: PlayerStatDeltaRow[];
}

/** One scope toggle: `freeform` or `t<tournament id>`. */
export interface HistoryScopeOption {
  key: string;
  label: string;
  selected: boolean;
}

/** The record tiles over the in-scope games. */
export interface TeamHistoryStats {
  total_matches: number;
  match_wins: number;
  match_losses: number;
  match_ties: number;
  total_rounds: number;
  round_wins: number;
  round_losses: number;
  round_ties: number;
}

/** One Role column of the roster table. */
export interface HistoryRoleColumn {
  role: Role;
  label: string;
}

/** One Role cell of a roster row; the averages are null when `games` is 0. */
export interface HistoryRoleCell {
  role: Role;
  games: number;
  avg_mvp: number | null;
  avg_score: number | null;
}

/** One roster row: members first, then Mercenaries; most games first, then by name. */
export interface HistoryRosterRow {
  player: TeamRef;
  games_played: number;
  is_merc: boolean;
  roles: HistoryRoleCell[];
}

/** One Match row, from the Team's point of view. A null winner is a `tie`. */
export interface HistoryMatchRow {
  id: number;
  opponent: TeamRef;
  result: 'won' | 'lost' | 'tie';
  team_points: number;
  opponent_points: number;
  team_rounds_won: number;
  opponent_rounds_won: number;
  date_played: string;
  is_completed: boolean;
}

/** One standalone Round row, from the Team's point of view. A null winner is a `tie`. */
export interface HistorySingleRoundRow {
  id: number;
  opponent: TeamRef;
  result: 'won' | 'lost' | 'tie';
  team_points: number;
  opponent_points: number;
  date_played: string;
}

/** `GET teams/{id}/history/`: the **Team match history**, the Account's own games only. */
export interface TeamHistory {
  team: TeamRef;
  scope_options: HistoryScopeOption[];
  show_scope_filter: boolean;
  has_any_games: boolean;
  stats: TeamHistoryStats;
  role_columns: HistoryRoleColumn[];
  roster: HistoryRosterRow[];
  /** Every opponent in scope, by name. */
  opponents: TeamRef[];
  /** Newest first. */
  matches: HistoryMatchRow[];
  /** Newest first. */
  single_rounds: HistorySingleRoundRow[];
}
