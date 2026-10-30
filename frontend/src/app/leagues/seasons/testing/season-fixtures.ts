// Season screen spec fixtures (ANG-06-18, spec #444): one builder per `api/seasons.models.ts`
// type plus the routed Season, each a real-shaped payload that `overrides` adjusts. Every
// builder describes Season 11 "Season 1" of League 1 "Metro League", the Season the
// `/leagues/1/seasons/11/...` routes load. Imported only by *.spec.ts files.
import { Season } from '../../../api/leagues.models';
import {
  ConferenceState,
  ConferencesState,
  MemberNightGame,
  OwnerEvaluation,
  ScheduleFixture,
  SeasonAwards,
  SeasonSchedule,
  SeasonStandings,
} from '../../../api/seasons.models';
import { awardWinner, season as leagueSeason, standingsRow } from '../../testing/league-fixtures';

/** The League and Season every `seasons/testing` builder and route uses. */
export const LEAGUE_ID = 1;
export const SEASON_ID = 11;

/** `/leagues/1/seasons/11` plus `path` (`''` for the dashboard). */
export function seasonUrl(path = ''): string {
  return `/leagues/${LEAGUE_ID}/seasons/${SEASON_ID}${path ? `/${path}` : ''}`;
}

/**
 * `GET seasons/11/`: Season 11 "Season 1" of League 1, **active**, no champion. What
 * `SeasonLayout` loads before any Season screen renders (`releaseSeasonLayout` flushes it).
 */
export function season(overrides: Partial<Season> = {}): Season {
  return leagueSeason({
    id: SEASON_ID,
    name: 'Season 1',
    state: 'active',
    start_date: '2026-09-01',
    starting_team_ids_json: [3, 5, 7, 8],
    ...overrides,
  });
}

export function seasonStandings(overrides: Partial<SeasonStandings> = {}): SeasonStandings {
  return {
    season: { id: SEASON_ID, name: 'Season 1' },
    is_draft_preview: false,
    sort: 'rank',
    dir: 'asc',
    groups: [{ conference: null, name: null, rows: [standingsRow()] }],
    ...overrides,
  };
}

/** Matchday 1, Round 1: Red Raiders vs Blue Bolts, played 41000-38000 as Round 501. */
export function scheduleFixture(overrides: Partial<ScheduleFixture> = {}): ScheduleFixture {
  return {
    matchday: 1,
    round_number: 1,
    team_a_id: 3,
    team_b_id: 5,
    team_a: { id: 3, name: 'Red Raiders' },
    team_b: { id: 5, name: 'Blue Bolts' },
    played: true,
    game_round_id: 501,
    red_score: 41000,
    blue_score: 38000,
    date: '2026-09-01',
    ...overrides,
  };
}

/** A completed member-night game, won by its A side, with two Rounds. */
export function memberNightGame(overrides: Partial<MemberNightGame> = {}): MemberNightGame {
  return {
    match_id: 900,
    team_a_name: 'MN Arena G1 A',
    team_b_name: 'MN Arena G1 B',
    is_completed: true,
    red_points: 2,
    blue_points: 0,
    winner_name: 'MN Arena G1 A',
    rounds: [
      { id: 901, round_number: 1 },
      { id: 902, round_number: 2 },
    ],
    ...overrides,
  };
}

export function seasonSchedule(overrides: Partial<SeasonSchedule> = {}): SeasonSchedule {
  return {
    season: { id: SEASON_ID, name: 'Season 1' },
    matchdays: [{ matchday: 1, date: '2026-09-01', fixtures: [scheduleFixture()] }],
    member_night_games: [],
    ...overrides,
  };
}

export function seasonAwards(overrides: Partial<SeasonAwards> = {}): SeasonAwards {
  return {
    season: { id: SEASON_ID, name: 'Season 1' },
    has_rounds: true,
    most_points: awardWinner(),
    best_accuracy: awardWinner({ value: 61.25 }),
    kd_by_role: {
      commander: awardWinner({ value: 2.5 }),
      heavy: null,
      scout: null,
      medic: null,
      ammo: null,
    },
    best_medic: null,
    most_efficient_nuke: null,
    season_mvp: awardWinner({ value: 88.4 }),
    finals_mvp: null,
    ...overrides,
  };
}

/** A retained career Manager of Red Raiders. */
export function ownerEvaluation(overrides: Partial<OwnerEvaluation> = {}): OwnerEvaluation {
  return {
    season: { id: SEASON_ID, name: 'Season 1' },
    league: { id: LEAGUE_ID, name: 'Metro League' },
    team_managed: { id: 3, name: 'Red Raiders' },
    wins_delta: 0.125,
    playoffs_delta: 0.05,
    money_delta: 1.5,
    wins_total: 0.625,
    playoffs_total: 0.3,
    money_total: 4.5,
    overall_mood: 5.425,
    verdict: 'retained',
    hot_seat_level: 0,
    fired_reason: '',
    is_fired: false,
    reassigned: false,
    created_at: '2026-10-30T12:00:00Z',
    ...overrides,
  };
}

/** Conference "East" holding Teams 3 and 5, no rotation, not yet snapshotted. */
export function conferenceState(overrides: Partial<ConferenceState> = {}): ConferenceState {
  return {
    id: 31,
    name: 'East',
    ordinal: 1,
    team_ids: [3, 5],
    map_rotation_ids: [],
    starting_team_ids: null,
    starting_map_rotation_ids: null,
    ...overrides,
  };
}

/** A draft Season's editable partition: East (3, 5); Teams 7 and 8 unassigned. */
export function conferencesState(overrides: Partial<ConferencesState> = {}): ConferencesState {
  return {
    season: { id: SEASON_ID, name: 'Season 1' },
    editable: true,
    map_mode: 'none',
    teams: [
      { id: 5, name: 'Blue Bolts' },
      { id: 8, name: 'Gold' },
      { id: 7, name: 'Green Giants' },
      { id: 3, name: 'Red Raiders' },
    ],
    conferences: [conferenceState()],
    unassigned_team_ids: [7, 8],
    ...overrides,
  };
}
