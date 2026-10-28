// Tournament shapes, snake_case exactly as matches/tournament_services (`tournament_fields`,
// `build_tournament_detail`, `pool_entry_payload`) and `TournamentCreateForm` /
// `PoolGenerateForm` send / bind them (no mapping layer).
import { ListParams } from './api-client';
import { PlayerRef, TeamRef } from './analytics.models';
import { JobStatus } from './batch.models';

/** `Tournament.FORMAT_CHOICES` keys. */
export type TournamentFormat =
  'single_elimination' | 'double_elimination' | 'round_robin' | 'round_robin_double_elim' | 'swiss';

/** `Tournament.STATE_CHOICES` keys. */
export type TournamentState = 'setup' | 'active' | 'completed';

/** `Tournament.STATE_CHOICES` labels, for the State badge. */
export const TOURNAMENT_STATE_LABELS: Readonly<Record<TournamentState, string>> = {
  setup: 'Setup',
  active: 'Active',
  completed: 'Completed',
};

/** `Tournament.TEAM_ASSEMBLY_CHOICES` keys: chosen Teams, or a Player-pool draw. */
export type TeamAssembly = 'preset' | 'player_pool';

/** `Tournament.TEAM_ASSEMBLY_CHOICES` labels: the create picker and the detail header. */
export const TEAM_ASSEMBLY_LABELS: Readonly<Record<TeamAssembly, string>> = {
  preset: 'Preset teams',
  player_pool: 'Player pool draw',
};

/** `Tournament.ROLE_ASSIGNMENT_CHOICES` keys (Player-pool draws only). */
export type RoleAssignmentMode = 'random' | 'per_tier';

/** A best-of-N series length (`_SERIES_LENGTH_CHOICES`). */
export type SeriesLength = 1 | 3 | 5;

/** `RRDE_COMBOS` values: `"<wb>/<lb>"` advancers for Round robin -> double elimination. */
export type RrdeCombo = '4/0' | '4/2' | '8/0' | '8/4' | '16/0' | '16/8';

/** `{id, name}`: a Season (`season_ref`). */
export interface SeasonRef {
  id: number;
  name: string;
}

/** `tournament_fields`: a row of `GET tournaments/`, and the top of the detail. */
export interface Tournament {
  id: number;
  name: string;
  format: TournamentFormat;
  /** The derived display label (`format_label`), e.g. "Random Draw" for a Player-pool RR->DE. */
  format_label: string;
  state: TournamentState;
  team_assembly: TeamAssembly;
  role_assignment_mode: RoleAssignmentMode;
  champion: TeamRef | null;
  /** True for an in-League Tournament, whose `season` is then set. */
  embedded: boolean;
  season: SeasonRef | null;
  final_series_length: SeriesLength;
  semifinal_series_length: SeriesLength;
  quarterfinal_series_length: SeriesLength;
  earlier_series_length: SeriesLength;
  wb_advancers: number;
  lb_advancers: number;
  /** 0 = auto until lock. */
  swiss_rounds: number;
  /** The Account id; null for an Unmanaged row. */
  manager: number | null;
  /** ISO 8601 datetime. */
  created_at: string;
}

/** One preset participant, by seed. */
export interface TournamentParticipant {
  team: TeamRef;
  seed: number;
}

/** `BracketNode.bracket_type`: the payload's bracket groups, in render order. */
export type BracketType = 'round_robin' | 'swiss' | 'winners' | 'losers' | 'grand_final';

/** One played Match of a node's Series: the 6-point Match score from slot A / B. */
export interface BracketGame {
  game_number: number;
  match_id: number;
  score_a: number;
  score_b: number;
}

/** `node_payload`: one Bracket node card. */
export interface BracketNode {
  position: number;
  team_a: TeamRef | null;
  team_b: TeamRef | null;
  seed_a: number | null;
  seed_b: number | null;
  is_bye: boolean;
  wins_a: number;
  wins_b: number;
  series_length: SeriesLength;
  winner: TeamRef | null;
  games: BracketGame[];
}

/** One Bracket round (a column of node cards). */
export interface BracketRound {
  bracket_round: number;
  nodes: BracketNode[];
}

/** One non-empty sub-bracket of `brackets`. */
export interface BracketGroup {
  bracket_type: BracketType;
  rounds: BracketRound[];
}

/**
 * A `StandingsRow` plus its `team` ref (`rr_standings` / `swiss_standings`). The server also sends
 * the streak, L5 and per-side keys; the detail screen doesn't show them, so they're left untyped.
 */
export interface TournamentStandingsRow {
  team_id: number;
  rank: number;
  matches_played: number;
  wins: number;
  losses: number;
  ties: number;
  league_points: number;
  round_wins: number;
  total_score: number;
  team: TeamRef | null;
}

/** An RR->DE seeding-stage cut marker: Winners bracket, Losers bracket or out. */
export type CutLabel = 'wb' | 'lb' | 'out';

/** One leg of a crosstable cell, from the row Team's side. Scores are null until played. */
export interface CrosstableLeg {
  /** Total points. */
  team_score: number | null;
  opp_score: number | null;
  /** The 6-point Match score. */
  match_team: number | null;
  match_opp: number | null;
  played: boolean;
  match_id: number | null;
}

/** A row Team's cell against one opponent: both legs of the double round robin. */
export interface CrosstableCell {
  opponent_team_id: number;
  leg1: CrosstableLeg | null;
  leg2: CrosstableLeg | null;
}

/** One crosstable row, in Standings order; `cells[i]` is null on the diagonal. */
export interface CrosstableRow {
  team: TeamRef | null;
  cells: (CrosstableCell | null)[];
}

/**
 * `build_tournament_detail`: `GET tournaments/{id}/`, the create, participant-import, seeds, lock
 * and draw responses.
 */
export interface TournamentDetail extends Tournament {
  participants: TournamentParticipant[];
  pool_size: number;
  is_drawn: boolean;
  /** `_tournament_stage`: setup, seeding, finals, completed, swiss or the format key. */
  stage: string;
  /** Non-empty sub-brackets only; `[]` before lock. */
  brackets: BracketGroup[];
  /** Round robin and RR->DE only, else `[]`. */
  rr_standings: TournamentStandingsRow[];
  /** Swiss only, else `[]`. */
  swiss_standings: TournamentStandingsRow[];
  /** Team id (as a string) -> cut marker; RR->DE seeding stage only, else `{}`. */
  cut_labels: Record<string, CutLabel>;
  /** Round robin and RR->DE with 2+ Teams, else `[]`. */
  rr_crosstable: CrosstableRow[];
  /** Active, with a Match ready to play. */
  can_play: boolean;
}

/** `POST tournaments/{id}/play-next/`: the node just played, with its identity. */
export interface PlayNextResult {
  node: BracketNode & { id: number; bracket_type: BracketType; bracket_round: number };
  match_ids: number[];
  state: TournamentState;
  /** Set when this node ended the Tournament. */
  champion: TeamRef | null;
}

/** `POST tournaments/{id}/play-all/`: the background run to poll. */
export interface PlayJobRef {
  job_id: string;
  tournament_id: number;
  status_url: string;
}

/** `GET tournaments/{id}/play-jobs/{job_id}/`: `build_play_status`. */
export interface PlayJobStatus {
  status: JobStatus;
  completed: number;
  total: number;
  error: string | null;
  tournament_id: number;
}

/** `pool_entry_payload`: a `GET tournaments/{id}/pool/` row and every pool-write entry. */
export interface PoolEntry {
  player: PlayerRef;
  tier: number | null;
  drawn_team: TeamRef | null;
}

/**
 * `TournamentCreateForm` fields for `POST tournaments/`. Only `name` is required: an absent
 * field takes the Form's default, so a screen leaves hidden fields out.
 */
export interface TournamentCreateInput {
  name: string;
  format?: TournamentFormat;
  team_assembly?: TeamAssembly;
  /** Team ids: the requester's own or Unmanaged valid-roster Teams. */
  teams?: number[];
  generate_count?: number;
  generate_ppt?: number;
  rrde_combo?: RrdeCombo;
  swiss_rounds?: number;
  role_assignment_mode?: RoleAssignmentMode;
  final_series_length?: SeriesLength;
  semifinal_series_length?: SeriesLength;
  quarterfinal_series_length?: SeriesLength;
  earlier_series_length?: SeriesLength;
}

/** `PoolGenerateForm` fields for `POST tournaments/{id}/pool/generate/`. */
export interface PoolGenerateInput {
  count: number;
  /** null (or absent) takes the Form's default (`required=False`). */
  mean: number | null;
  std_dev: number | null;
}

/**
 * `TournamentFilterSet` plus `ordering` (name, created_at, id; `-` descends; default `-id`).
 */
export interface TournamentListParams extends ListParams {
  state?: TournamentState;
  format?: TournamentFormat;
  team_assembly?: TeamAssembly;
  embedded?: boolean;
  ordering?: string;
}
