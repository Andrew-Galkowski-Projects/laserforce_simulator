// Team shapes, snake_case exactly as teams/serializers.py and teams/forms.py
// send / bind them (no mapping layer).
import { ListParams } from './api-client';
import { Player, PlayerInline } from './players.models';

/**
 * `TeamSlotForm` fields for `PUT teams/{id}/slots/`: Player ids (`null` clears a slot).
 * The endpoint is a full replace, so all six are always sent.
 */
export interface TeamSlotsInput {
  slot_commander: number | null;
  slot_heavy: number | null;
  slot_scout_1: number | null;
  slot_scout_2: number | null;
  slot_medic: number | null;
  slot_ammo: number | null;
}

/**
 * The fields `TeamListSerializer` and `TeamSerializer` share. The six slots (from
 * `TeamSlotsInput`) are Player ids.
 */
export interface TeamBase extends TeamSlotsInput {
  id: number;
  name: string;
  /** ISO 8601 datetime. */
  created_date: string;
  wins: number;
  losses: number;
  /** `Team.is_valid_roster`: the server's SM5 roster verdict. */
  is_valid_roster: boolean;
  /** `Team.roster_errors`: human-readable problems, `[]` when valid. */
  roster_errors: string[];
}

/** `TeamListSerializer`: a row of `GET teams/`. */
export interface TeamListItem extends TeamBase {
  players: PlayerInline[];
}

/** `TeamSerializer`: `GET teams/{id}/` and every Team write's response. */
export interface Team extends TeamBase {
  players: Player[];
}

/** `TeamForm` fields (create; PATCH sends any subset). */
export interface TeamInput {
  name: string;
}

/**
 * `GenerateLeagueForm` fields for `POST teams/generate/`. The two selects travel as strings
 * (`'0'`, `'2'`..`'20'` or the marker `'random_2_10'`; `'6'`..`'9'` or `'random_team'` in team
 * mode, `'12'`..`'100'` or `'random_pool'` in pool mode) and the two stats as numbers, exactly as
 * the Django form binds them, so the server's cross-field rule stays the only validator.
 */
export interface GenerateInput {
  num_teams: string;
  players_per_team: string;
  mean: number;
  std_dev: number;
}

/** The `generate` action's response (`teams/api_views.py`): Teams as `TeamListSerializer` rows. */
export interface GenerationResult {
  created_teams: TeamListItem[];
  free_agent_count: number;
}

/** The `import_roster` action's response (`teams/api_views.py`): Teams as `TeamListSerializer` rows. */
export interface RosterImportResult {
  created_teams: TeamListItem[];
  appended_teams: TeamListItem[];
  player_count: number;
  row_count: number;
}

/** `TeamFilterSet` plus `ordering` (name, created_date, wins, losses, id; `-` descends). */
export interface TeamListParams extends ListParams {
  name?: string;
  include_free_agents?: boolean;
  ordering?: string;
}
