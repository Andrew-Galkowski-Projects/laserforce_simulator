// Leagues spec fixtures: one builder per `api/leagues.models.ts` type, each a real-shaped
// payload that `overrides` adjusts. Imported only by *.spec.ts files.
import {
  AwardWinner,
  Conference,
  DashboardBody,
  DeleteSummary,
  HistoryRow,
  LeaderRow,
  League,
  LeagueCreateResult,
  LeagueDashboard,
  LeagueNav,
  LeagueTemplate,
  NewTeamCandidates,
  NextFixture,
  Season,
  SeasonDashboard,
  SeasonPhase,
  StandingsRow,
  StandingsSnippetRow,
} from '../../api/leagues.models';

/** A career League (mode `league`), active, managing Team 3 in Season 1. */
export function league(overrides: Partial<League> = {}): League {
  return {
    id: 1,
    name: 'Metro League',
    mode: 'league',
    state: 'active',
    visibility: 'closed',
    finance_enabled: false,
    challenge_fired_luxury_tax: false,
    manager: 1,
    current_team: { id: 3, name: 'Red Raiders' },
    free_agent_pool: 40,
    active_season: { id: 11, name: 'Season 1' },
    created_at: '2026-10-27T09:15:00Z',
    ...overrides,
  };
}

/** `8_team_classic`. */
export function leagueTemplate(overrides: Partial<LeagueTemplate> = {}): LeagueTemplate {
  return {
    key: '8_team_classic',
    label: '8-Team Classic',
    num_teams: 8,
    phases: 'round_robin,tournament',
    finance_enabled: false,
    challenge_fired_luxury_tax: false,
    mean: 50,
    std_dev: 15,
    map_mode: 'none',
    ...overrides,
  };
}

/** The create 201: `league()` with its draft Season and no Conferences. */
export function leagueCreateResult(
  overrides: Partial<LeagueCreateResult> = {},
): LeagueCreateResult {
  return {
    ...league({ active_season: { id: 11, name: 'Season 1' } }),
    draft_season: { id: 11, name: 'Season 1' },
    conference_ids: [],
    ...overrides,
  };
}

export function deleteSummary(overrides: Partial<DeleteSummary> = {}): DeleteSummary {
  return { seasons: 2, matches: 56, tournaments: 1, teams: 9, players: 72, ...overrides };
}

export function leagueNav(overrides: Partial<LeagueNav> = {}): LeagueNav {
  return {
    league: { id: 1, name: 'Metro League', mode: 'league' },
    displayed_season: { id: 11, name: 'Season 1' },
    schedule_team: { id: 3, name: 'Red Raiders' },
    finance_enabled: true,
    ...overrides,
  };
}

export function standingsSnippetRow(
  overrides: Partial<StandingsSnippetRow> = {},
): StandingsSnippetRow {
  return {
    team_id: 3,
    matches_played: 4,
    wins: 3,
    losses: 1,
    ties: 0,
    league_points: 9,
    round_wins: 6,
    total_score: 48210,
    rank: 1,
    team: { id: 3, name: 'Red Raiders' },
    ...overrides,
  };
}

export function nextFixture(overrides: Partial<NextFixture> = {}): NextFixture {
  return {
    matchday: 5,
    round_number: 1,
    team_a_id: 3,
    team_a_name: 'Red Raiders',
    team_b_id: 5,
    team_b_name: 'Blue Bolts',
    date: '2026-11-24',
    ...overrides,
  };
}

export function leaderRow(overrides: Partial<LeaderRow> = {}): LeaderRow {
  return {
    player_id: 25,
    player_name: 'Ace',
    role: 'commander',
    team_id: 3,
    team_name: 'Red Raiders',
    value: 9120.5,
    games_played: 8,
    rank: 1,
    ...overrides,
  };
}

function dashboardBody(): DashboardBody {
  return {
    displayed_season: { id: 11, name: 'Season 1' },
    season_mode: 'active',
    standings_snippet: [standingsSnippetRow()],
    next_fixture: nextFixture(),
    round_count_completed: 8,
    round_count_total: 56,
    leaders_points: [leaderRow()],
    leaders_tags: [leaderRow({ value: 41.25 })],
    leaders_ratio: [leaderRow({ value: 1.8 })],
    map_config_label: 'Map: 3-zone fallback (no map)',
    playoff_tournament_id: null,
  };
}

/** An active career League's dashboard, its Manager not fired. */
export function leagueDashboard(overrides: Partial<LeagueDashboard> = {}): LeagueDashboard {
  return {
    league: { id: 1, name: 'Metro League' },
    ...dashboardBody(),
    mode: 'league',
    managed_team: { id: 3, name: 'Red Raiders' },
    has_owner_evaluations: false,
    member_night_progress: null,
    manager_awaiting_reassignment: false,
    ...overrides,
  };
}

/** Season 11's dashboard, as `POST seasons/11/start/` returns it. */
export function seasonDashboard(overrides: Partial<SeasonDashboard> = {}): SeasonDashboard {
  return {
    season: { id: 11, name: 'Season 1' },
    league: { id: 1, name: 'Metro League' },
    ...dashboardBody(),
    ...overrides,
  };
}

export function awardWinner(overrides: Partial<AwardWinner> = {}): AwardWinner {
  return {
    player_id: 25,
    player_name: 'Ace',
    role: 'commander',
    team_id: 3,
    team_name: 'Red Raiders',
    value: 1234.5,
    ...overrides,
  };
}

/** A completed Season's history row, won by Red Raiders. */
export function historyRow(overrides: Partial<HistoryRow> = {}): HistoryRow {
  return {
    season_id: 10,
    season_name: 'Season 1',
    season_url: '/seasons/10/',
    start_date: '2026-09-01',
    teams_enrolled: 8,
    matches_played: 28,
    champion: { id: 3, name: 'Red Raiders' },
    runner_up: { id: 5, name: 'Blue Bolts' },
    tournament_champion: null,
    top_three: [
      { id: 3, name: 'Red Raiders' },
      { id: 5, name: 'Blue Bolts' },
      { id: 7, name: 'Green Giants' },
    ],
    is_in_progress: false,
    season_mvp: awardWinner(),
    finals_mvp: awardWinner({ player_id: 26, player_name: 'Bolt', value: 980 }),
    ...overrides,
  };
}

export function standingsRow(overrides: Partial<StandingsRow> = {}): StandingsRow {
  return {
    team_id: 7,
    matches_played: 7,
    wins: 1,
    losses: 6,
    ties: 0,
    league_points: 3,
    round_wins: 3,
    total_score: 70120,
    rank: 8,
    match_streak: ['L', 3],
    match_l5: [1, 4, 0],
    round_streak: ['L', 2],
    round_l5: [1, 4, 0],
    red_wlt: [1, 3, 0],
    blue_wlt: [2, 8, 0],
    red_points_for: 35100,
    blue_points_for: 35020,
    team: { id: 7, name: 'Green Giants' },
    ...overrides,
  };
}

export function newTeamCandidates(overrides: Partial<NewTeamCandidates> = {}): NewTeamCandidates {
  return {
    latest_completed: { id: 10, name: 'Season 1' },
    candidates: [
      standingsRow(),
      standingsRow({ team_id: 8, rank: 7, wins: 2, losses: 5, team: { id: 8, name: 'Gold' } }),
    ],
    ...overrides,
  };
}

export function seasonPhase(overrides: Partial<SeasonPhase> = {}): SeasonPhase {
  return {
    id: 21,
    ordinal: 1,
    phase_type: 'round_robin',
    schedule_format: 'single_round_robin',
    tournament_mode: 'standings',
    tournament_format: 'single_elimination',
    tournament_cut: 0,
    final_series_length: 1,
    semifinal_series_length: 1,
    quarterfinal_series_length: 1,
    earlier_series_length: 1,
    wb_advancers: 0,
    lb_advancers: 0,
    swiss_rounds: 0,
    tournament: null,
    ...overrides,
  };
}

export function conference(overrides: Partial<Conference> = {}): Conference {
  return { id: 31, name: 'East', ordinal: 1, team_ids: [3, 5], ...overrides };
}

/** Season 12, the draft `POST leagues/1/next-season/` rolls. */
export function season(overrides: Partial<Season> = {}): Season {
  return {
    id: 12,
    league: { id: 1, name: 'Metro League' },
    name: 'Season 2',
    start_date: '2026-12-01',
    state: 'draft',
    schedule_format: 'single_round_robin',
    map_mode: 'none',
    champion_team: null,
    created_at: '2026-10-28T10:00:00Z',
    team_ids: [3, 5, 7, 8],
    map_pool_ids: [],
    starting_team_ids_json: null,
    starting_map_pool_ids_json: null,
    map_rotation_ids_json: null,
    starting_map_rotation_ids_json: null,
    phases: [seasonPhase()],
    conferences: [],
    ...overrides,
  };
}

/** A `Page<T>` envelope as the API sends it (one page unless told otherwise). */
export function pageEnvelope<T>(
  items: T[],
  meta: { page?: number; page_size?: number; count?: number; total_pages?: number } = {},
) {
  return {
    data: items,
    meta: { page: 1, page_size: 20, count: items.length, total_pages: 1, ...meta },
  };
}
