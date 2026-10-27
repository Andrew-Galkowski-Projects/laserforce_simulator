// Tournament shapes, snake_case exactly as matches/tournament_services (`tournament_fields`,
// `build_tournament_detail`, `pool_entry_payload`) and `TournamentCreateForm` /
// `PoolGenerateForm` send / bind them (no mapping layer).
import { ListParams } from './api-client';
import { PlayerRef, TeamRef } from './analytics.models';

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

/**
 * `build_tournament_detail`: `GET tournaments/{id}/`, the create and participant-import
 * responses. `brackets`, `rr_standings`, `swiss_standings` and `cut_labels` belong to ANG-06-13b,
 * which narrows them.
 */
export interface TournamentDetail extends Tournament {
  participants: TournamentParticipant[];
  pool_size: number;
  is_drawn: boolean;
  /** `_tournament_stage`: setup, seeding, finals, completed, swiss or the format key. */
  stage: string;
  brackets: unknown;
  rr_standings: unknown;
  swiss_standings: unknown;
  cut_labels: unknown;
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
  mean: number;
  std_dev: number;
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
