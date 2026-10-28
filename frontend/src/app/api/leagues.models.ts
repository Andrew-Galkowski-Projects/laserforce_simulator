// League and Season shapes (ANG-06-14), snake_case exactly as `/api/leagues/` and
// `/api/seasons/` send them: `LeagueSerializer`, `SeasonSerializer`, `league_api_views`
// (`_dashboard_payload`, `_history_item`, `new_team_candidates`, `create`), `LeaderRow`,
// `AwardWinner` and `StandingsRow`. No mapping layer.
import { ListParams } from './api-client';
import { TeamRef } from './analytics.models';
import { SeasonRef, SeriesLength, TournamentFormat } from './tournaments.models';

export type { SeasonRef } from './tournaments.models';

/** `League.MODE_CHOICES` keys; only `league` is a career League (Delete, firing). */
export type LeagueMode = 'sandbox' | 'league' | 'multiplayer';

export const LEAGUE_MODE_LABELS: Readonly<Record<LeagueMode, string>> = {
  sandbox: 'Sandbox',
  league: 'League',
  multiplayer: 'Multiplayer',
};

/** `League.STATE_CHOICES` keys. */
export type LeagueState = 'active' | 'archived';

/** `League.STATE_CHOICES` labels, for the State badge. */
export const LEAGUE_STATE_LABELS: Readonly<Record<LeagueState, string>> = {
  active: 'Active',
  archived: 'Archived',
};

/** `League.VISIBILITY_CHOICES` keys (dormant: nothing reads it). */
export type LeagueVisibility = 'closed' | 'open';

/** `Season.STATE_CHOICES` keys. */
export type SeasonState = 'draft' | 'active' | 'completed';

export const SEASON_STATE_LABELS: Readonly<Record<SeasonState, string>> = {
  draft: 'Draft',
  active: 'Active',
  completed: 'Completed',
};

/** The dashboard's `season_mode` (`season_mode_for`): `none` when the League has no Season. */
export type SeasonMode = 'none' | SeasonState;

export const SEASON_MODE_LABELS: Readonly<Record<SeasonMode, string>> = {
  none: 'No Season',
  ...SEASON_STATE_LABELS,
};

/** `Season.SCHEDULE_FORMAT_CHOICES` keys. */
export type ScheduleFormat = 'single_round_robin';

/** `Season.MAP_MODE_CHOICES` keys. */
export type MapMode =
  'none' | 'single' | 'random_per_round' | 'rotate_by_matchday' | 'rotate_by_conference';

export const MAP_MODE_LABELS: Readonly<Record<MapMode, string>> = {
  none: '3-zone fallback',
  single: 'Single map',
  random_per_round: 'Random per Round',
  rotate_by_matchday: 'Rotate by matchday',
  rotate_by_conference: 'Rotate by Conference',
};

/** `CreateLeagueForm.DIFFICULTY_CHOICES` keys: which generated Team the Manager gets. */
export type Difficulty = 'easy' | 'medium' | 'hard';

export const DIFFICULTY_LABELS: Readonly<Record<Difficulty, string>> = {
  easy: 'Easy',
  medium: 'Medium',
  hard: 'Hard',
};

/** `CreateLeagueForm.LEAGUE_SPREAD_CHOICES` keys (Advanced create only). */
export type LeagueSpread = 'even' | 'tiered' | 'steep';

export const LEAGUE_SPREAD_LABELS: Readonly<Record<LeagueSpread, string>> = {
  even: 'Even',
  tiered: 'Tiered',
  steep: 'Steep',
};

/** `{id, name}`: a League (`league_ref`). */
export interface LeagueRef {
  id: number;
  name: string;
}

/** `LeagueSerializer`: a row of `GET leagues/` and `GET leagues/{id}/`. */
export interface League {
  id: number;
  name: string;
  mode: LeagueMode;
  state: LeagueState;
  visibility: LeagueVisibility;
  finance_enabled: boolean;
  challenge_fired_luxury_tax: boolean;
  /** The Account id; null for an Unmanaged row. */
  manager: number | null;
  current_team: TeamRef | null;
  /** The free-agent pool Team's id. */
  free_agent_pool: number | null;
  active_season: SeasonRef | null;
  /** ISO 8601 datetime. */
  created_at: string;
}

/** `LeagueFilterSet` plus `ordering` (name, created_at, id; default `-id`). */
export interface LeagueListParams extends ListParams {
  state?: LeagueState;
  mode?: LeagueMode;
  ordering?: string;
}

/** `GET leagues/templates/`: one curated create-League preset (`LeagueTemplate`). */
export interface LeagueTemplate {
  key: string;
  label: string;
  num_teams: number;
  /** A phase-composer wire string, e.g. `round_robin,tournament`. */
  phases: string;
  finance_enabled: boolean;
  challenge_fired_luxury_tax: boolean;
  mean: number;
  std_dev: number;
  map_mode: MapMode;
}

/** `POST leagues/` on the template path (`LeagueTemplateForm` + name and difficulty). */
export interface LeagueTemplateCreateInput {
  template: string;
  league_name: string;
  difficulty?: Difficulty;
}

/**
 * `POST leagues/` on the Advanced path: `CreateLeagueForm` fields. An absent optional field
 * takes the Form's default.
 */
export interface LeagueAdvancedCreateInput {
  league_name: string;
  manager_team_name?: string;
  difficulty?: Difficulty;
  league_spread?: LeagueSpread;
  season_name: string;
  /** `YYYY-MM-DD`. */
  start_date: string;
  num_teams: 4 | 8 | 12 | 16;
  schedule_format?: ScheduleFormat;
  mean: number;
  std_dev: number;
  map_mode: MapMode;
  /** ArenaMap ids. */
  map_pool?: number[];
  /** Ordered ArenaMap ids joined by commas. */
  map_rotation?: string;
  finance_enabled?: boolean;
  challenge_fired_luxury_tax?: boolean;
  number_of_conferences?: 0 | 2 | 3 | 4;
  visibility?: LeagueVisibility;
  /** The phase-composer wire string. */
  phases?: string;
}

export type LeagueCreateInput = LeagueTemplateCreateInput | LeagueAdvancedCreateInput;

/** The `POST leagues/` 201: the League plus its draft Season and Conference ids. */
export interface LeagueCreateResult extends League {
  draft_season: SeasonRef;
  conference_ids: number[];
}

/** `GET leagues/{id}/delete-summary/` (B2): what deleting the League removes. */
export interface DeleteSummary {
  seasons: number;
  matches: number;
  tournaments: number;
  teams: number;
  players: number;
}

/** `GET leagues/{id}/nav/` (B4): what the League nav needs. */
export interface LeagueNav {
  league: LeagueRef & { mode: LeagueMode };
  displayed_season: SeasonRef | null;
  /** The TEAM > Schedule target (`sidebar_schedule_team`). */
  schedule_team: TeamRef | null;
}

/** One dashboard standings-snippet row: 9 standings keys plus `team`. */
export interface StandingsSnippetRow {
  team_id: number;
  matches_played: number;
  wins: number;
  losses: number;
  ties: number;
  league_points: number;
  round_wins: number;
  total_score: number;
  rank: number;
  team: TeamRef | null;
}

/** The dashboard's next unplayed fixture. */
export interface NextFixture {
  matchday: number;
  round_number: number;
  team_a_id: number;
  team_a_name: string;
  team_b_id: number;
  team_b_name: string;
  /** `YYYY-MM-DD`. */
  date: string;
}

/** `LeaderRow`: one row of a top-3 leader card. */
export interface LeaderRow {
  player_id: number;
  player_name: string;
  role: string;
  team_id: number;
  team_name: string;
  value: number;
  games_played: number;
  rank: number;
}

/** UXP-11: the member night at the cursor, once its games are drawn. */
export interface MemberNightProgress {
  played: number;
  total: number;
}

/** `_dashboard_payload`: the body the League and Season dashboards share. */
export interface DashboardBody {
  displayed_season: SeasonRef | null;
  season_mode: SeasonMode;
  standings_snippet: StandingsSnippetRow[];
  next_fixture: NextFixture | null;
  round_count_completed: number;
  round_count_total: number;
  leaders_points: LeaderRow[];
  leaders_tags: LeaderRow[];
  leaders_ratio: LeaderRow[];
  map_config_label: string;
  playoff_tournament_id: number | null;
}

/** `GET leagues/{id}/dashboard/`: the shared body, the League, and the five B5 keys. */
export interface LeagueDashboard extends DashboardBody {
  league: LeagueRef;
  mode: LeagueMode;
  /** `league.current_team` in a career League, else null. */
  managed_team: TeamRef | null;
  has_owner_evaluations: boolean;
  member_night_progress: MemberNightProgress | null;
  /** A fired career Manager who must pick a new team before the next Season. */
  manager_awaiting_reassignment: boolean;
}

/** `GET seasons/{id}/dashboard/`, also the `POST seasons/{id}/start/` 200. */
export interface SeasonDashboard extends DashboardBody {
  season: SeasonRef;
  league: LeagueRef;
}

/** `AwardWinner`: a Season award's Player and the value that won it. */
export interface AwardWinner {
  player_id: number;
  player_name: string;
  role: string;
  team_id: number;
  team_name: string;
  value: number;
}

/** `_history_item`: one row of `GET leagues/{id}/history/` (in-progress first, page 1 only). */
export interface HistoryRow {
  season_id: number;
  season_name: string;
  /** The Django Season dashboard path. */
  season_url: string;
  /** `YYYY-MM-DD`. */
  start_date: string;
  teams_enrolled: number;
  matches_played: number;
  champion: TeamRef | null;
  runner_up: TeamRef | null;
  tournament_champion: TeamRef | null;
  /** Always three slots; a slot is null past the enrolled Teams. */
  top_three: (TeamRef | null)[];
  is_in_progress: boolean;
  season_mvp: AwardWinner | null;
  finals_mvp: AwardWinner | null;
}

/** A W / L / T streak: `["W", 3]`; `["", 0]` before any game. */
export type Streak = [kind: '' | 'W' | 'L' | 'T', length: number];

/** A (W, L, T) count. */
export type WinLossTie = [wins: number, losses: number, ties: number];

/** `StandingsRow`: the 17 keys in field order, plus `team`. */
export interface StandingsRow {
  team_id: number;
  matches_played: number;
  wins: number;
  losses: number;
  ties: number;
  league_points: number;
  round_wins: number;
  total_score: number;
  rank: number;
  match_streak: Streak;
  match_l5: WinLossTie;
  round_streak: Streak;
  round_l5: WinLossTie;
  red_wlt: WinLossTie;
  blue_wlt: WinLossTie;
  red_points_for: number;
  blue_points_for: number;
  team: TeamRef | null;
}

/** `GET leagues/{id}/new-team-candidates/`: the teams a fired Manager may take over. */
export interface NewTeamCandidates {
  latest_completed: SeasonRef;
  candidates: StandingsRow[];
}

/** `SeasonPhase.PHASE_TYPE_CHOICES` keys. */
export type PhaseType = 'round_robin' | 'tournament' | 'member_night';

/** `SeasonPhase.TOURNAMENT_MODE_CHOICES` keys (`worlds` is derived, never authored). */
export type TournamentMode = 'standings' | 'strength' | 'unseeded' | 'random_draw' | 'worlds';

/** `SeasonPhaseSerializer`. */
export interface SeasonPhase {
  id: number;
  ordinal: number;
  phase_type: PhaseType;
  /** Set on a round-robin phase, null on a tournament phase. */
  schedule_format: string | null;
  tournament_mode: TournamentMode;
  tournament_format: TournamentFormat;
  tournament_cut: number;
  final_series_length: SeriesLength;
  semifinal_series_length: SeriesLength;
  quarterfinal_series_length: SeriesLength;
  earlier_series_length: SeriesLength;
  wb_advancers: number;
  lb_advancers: number;
  swiss_rounds: number;
  /** The embedded Tournament's id, once built. */
  tournament: number | null;
}

/** `ConferenceSerializer`. */
export interface Conference {
  id: number;
  name: string;
  ordinal: number;
  team_ids: number[];
}

/** `SeasonSerializer`: the Season detail, and the next-season / reassign-team 201. */
export interface Season {
  id: number;
  league: LeagueRef;
  name: string;
  /** `YYYY-MM-DD`. */
  start_date: string;
  state: SeasonState;
  schedule_format: ScheduleFormat;
  map_mode: MapMode;
  champion_team: TeamRef | null;
  /** ISO 8601 datetime. */
  created_at: string;
  team_ids: number[];
  map_pool_ids: number[];
  starting_team_ids_json: number[] | null;
  starting_map_pool_ids_json: number[] | null;
  map_rotation_ids_json: number[] | null;
  starting_map_rotation_ids_json: number[] | null;
  phases: SeasonPhase[];
  conferences: Conference[];
}
