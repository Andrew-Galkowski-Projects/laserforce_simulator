// Season screen shapes (ANG-06-18, spec #444), snake_case exactly as `/api/seasons/<id>/...`
// sends them (`SeasonViewSet` in `league_api_views.py`). `Season`, `SeasonDashboard`,
// `StandingsRow`, `AwardWinner`, `SeasonRef`, `LeagueRef` and `MapMode` stay in
// `leagues.models.ts`; `TeamRef` in `analytics.models.ts`. No mapping layer.
import { TeamRef } from './analytics.models';
import { JobStatus } from './batch.models';
import {
  AwardWinner,
  LeagueRef,
  MapMode,
  MemberNightSite,
  SeasonRef,
  StandingsRow,
} from './leagues.models';

/** `{id, name}`: a Conference (`conference_ref`). */
export interface ConferenceRef {
  id: number;
  name: string;
}

/** `STANDINGS_SORT_KEYS`: the 17 `?sort=` values `StandingsParamsForm` accepts. */
export type StandingsSortKey =
  | 'rank'
  | 'team'
  | 'matches_played'
  | 'wins'
  | 'losses'
  | 'ties'
  | 'league_points'
  | 'round_wins'
  | 'total_score'
  | 'match_streak'
  | 'match_l5'
  | 'round_streak'
  | 'round_l5'
  | 'red_wlt'
  | 'blue_wlt'
  | 'red_points_for'
  | 'blue_points_for';

export type SortDir = 'asc' | 'desc';

/** `?sort=&dir=`; an absent key takes the server default (`rank` / `asc`). */
export interface StandingsParams {
  sort?: StandingsSortKey;
  dir?: SortDir;
}

/** One standings table: a Conference's, or the whole Season's (`conference` / `name` null). */
export interface StandingsGroup {
  conference: ConferenceRef | null;
  name: string | null;
  rows: StandingsRow[];
}

/** `GET seasons/{id}/standings/`. */
export interface SeasonStandings {
  season: SeasonRef;
  /** A draft Season, ranked by computed team rating. */
  is_draft_preview: boolean;
  sort: StandingsSortKey;
  dir: SortDir;
  groups: StandingsGroup[];
}

/** One `season_schedule` fixture, with played Rounds overlaid. */
export interface ScheduleFixture {
  matchday: number;
  round_number: number;
  team_a_id: number;
  team_b_id: number;
  /** Null when the Team no longer exists. */
  team_a: TeamRef | null;
  team_b: TeamRef | null;
  played: boolean;
  game_round_id: number | null;
  red_score: number | null;
  blue_score: number | null;
  /** `YYYY-MM-DD`. */
  date: string;
}

export interface ScheduleMatchday {
  matchday: number;
  /** `YYYY-MM-DD`. */
  date: string;
  fixtures: ScheduleFixture[];
}

/** A member-night game's Round link. */
export interface MemberNightRoundRef {
  id: number;
  round_number: number;
}

/** `_member_night_game_dict`: one drawn-Team member-night game. */
export interface MemberNightGame {
  match_id: number;
  team_a_name: string;
  team_b_name: string;
  is_completed: boolean;
  red_points: number | null;
  blue_points: number | null;
  winner_name: string | null;
  rounds: MemberNightRoundRef[];
}

/** One member-night phase's games (`_member_night_games_for_season`). */
export interface MemberNightSection {
  ordinal: number;
  games: MemberNightGame[];
}

/** `GET seasons/{id}/schedule/`. */
export interface SeasonSchedule {
  season: SeasonRef;
  matchdays: ScheduleMatchday[];
  member_night_games: MemberNightSection[];
}

/** `ROLE_KEYS`: the five Player roles, in the awards' K/D order. */
export type RoleKey = 'commander' | 'heavy' | 'scout' | 'medic' | 'ammo';

/** `GET seasons/{id}/awards/`: each award null when nobody qualified. */
export interface SeasonAwards {
  season: SeasonRef;
  has_rounds: boolean;
  most_points: AwardWinner | null;
  best_accuracy: AwardWinner | null;
  kd_by_role: Record<RoleKey, AwardWinner | null>;
  best_medic: AwardWinner | null;
  most_efficient_nuke: AwardWinner | null;
  season_mvp: AwardWinner | null;
  finals_mvp: AwardWinner | null;
}

/** `OwnerEvaluation.verdict` keys. */
export type OwnerVerdict = 'retained' | 'hot_seat' | 'fired';

/** `GET seasons/{id}/owner-evaluation/` (404 `not_found` when there is none). */
export interface OwnerEvaluation {
  season: SeasonRef;
  league: LeagueRef;
  team_managed: TeamRef | null;
  wins_delta: number;
  playoffs_delta: number;
  money_delta: number;
  wins_total: number;
  playoffs_total: number;
  money_total: number;
  overall_mood: number;
  verdict: OwnerVerdict;
  hot_seat_level: number;
  /** '' when not fired; `luxury_tax` for a luxury-tax firing. */
  fired_reason: string;
  is_fired: boolean;
  reassigned: boolean;
  /** ISO 8601 datetime. */
  created_at: string;
}

/** One Conference in `build_conferences_state`. */
export interface ConferenceState {
  id: number;
  name: string;
  ordinal: number;
  team_ids: number[];
  map_rotation_ids: number[];
  /** The activation snapshots; null until the Season starts. */
  starting_team_ids: number[] | null;
  starting_map_rotation_ids: number[] | null;
}

/** `GET` / `PUT seasons/{id}/conferences/`. */
export interface ConferencesState {
  season: SeasonRef;
  /** The Season is a draft. */
  editable: boolean;
  map_mode: MapMode;
  /** The enrolled Teams, by name. */
  teams: TeamRef[];
  conferences: ConferenceState[];
  unassigned_team_ids: number[];
}

/** A background Season run's Job (`_job_ref`): poll `status_url` (`playJob`). */
export interface SeasonPlayJobRef {
  job_id: string;
  season_id: number;
  status_url: string;
}

/**
 * `LivePlayResult.kind` (`league_play_services.py`): a round-robin game (a bye too), a playoff
 * node, or a member-night game.
 */
export type LivePlayKind = 'rr' | 'playoff' | 'member_night';

/**
 * `POST seasons/{id}/play-live/` and `member-night/play-live/`: the manager's game, played
 * now. `round_ids` is empty on a bye; `job` is the background run of the rest, if any.
 */
export interface LivePlayResult {
  kind: LivePlayKind;
  round_ids: number[];
  job: SeasonPlayJobRef | null;
}

/** `GET seasons/{id}/play-jobs/{job}/` (`build_play_job_status`). */
export interface SeasonPlayJobStatus {
  status: JobStatus;
  completed: number;
  total: number;
  cancelled: boolean;
  error: string | null;
}

/** One viable Site of `GET seasons/{id}/member-night/`, with the games drawn there. */
export interface MemberNightSiteState extends MemberNightSite {
  games: number;
}

/** `GET seasons/{id}/member-night/` (`build_member_night_state`). */
export interface MemberNightState {
  season: SeasonRef;
  phase_active: boolean;
  sites: MemberNightSiteState[];
  games_total: number;
  games_unplayed: number;
}

/** One Conference of the `PUT seasons/{id}/conferences/` body (`ConferencePartitionForm`). */
export interface ConferenceWrite {
  name: string;
  team_ids: number[];
  map_rotation_ids: number[];
}
