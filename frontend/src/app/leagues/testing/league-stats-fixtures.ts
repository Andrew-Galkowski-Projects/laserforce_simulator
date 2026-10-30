// League stats spec fixtures (ANG-06-15): one builder per `api/league-stats.models.ts` row /
// meta type, each a real-shaped payload that `overrides` adjusts, plus envelope helpers.
// Imported only by *.spec.ts files. The stats GETs all live under `leagues/`, so
// `releaseLeaguesLoads()` already releases them in routing / shell specs.
import { TeamRef } from '../../api/analytics.models';
import {
  FeatRow,
  GameLogRow,
  LeaderBoardRow,
  LeaderBoards,
  PlayerRatingRow,
  PlayerStatRow,
  PlayoffBracket,
  PlayoffsScreen,
  PowerRankingRow,
  SeasonOption,
  SeasonScope,
  StatisticalFeats,
  TeamFeat,
  TeamStatRow,
  WorldsQualifier,
} from '../../api/league-stats.models';
import { BracketNode } from '../../api/tournaments.models';

/** Season 11 "Season 1" (2026). */
export function seasonOption(overrides: Partial<SeasonOption> = {}): SeasonOption {
  return { id: 11, name: 'Season 1', year: 2026, ...overrides };
}

/**
 * `meta.season_scope`: Season 1 (displayed) and Season 0, newest first. `seasonScope({ selected:
 * null, options: [] })` is the no-Season League (I3).
 */
export function seasonScope(overrides: Partial<SeasonScope> = {}): SeasonScope {
  return {
    selected: { id: 11, name: 'Season 1' },
    options: [seasonOption(), seasonOption({ id: 10, name: 'Season 0', year: 2025 })],
    ...overrides,
  };
}

/** `meta.team_options`: two enrolled Teams, by name. */
export function teamOptions(): TeamRef[] {
  return [
    { id: 5, name: 'Blue Bolts' },
    { id: 3, name: 'Red Raiders' },
  ];
}

export function leaderBoardRow(overrides: Partial<LeaderBoardRow> = {}): LeaderBoardRow {
  return {
    player_id: 25,
    player_name: 'Ace',
    role: 'commander',
    team_id: 3,
    team_name: 'Red Raiders',
    value: 41.25,
    games_played: 8,
    rank: 1,
    player: { id: 25, name: 'Ace', team: { id: 3, name: 'Red Raiders' } },
    watched: false,
    ...overrides,
  };
}

export function leaderBoards(overrides: Partial<LeaderBoards> = {}): LeaderBoards {
  return {
    avg_tags: [leaderBoardRow()],
    avg_score: [leaderBoardRow({ value: 9120.5 })],
    fewest_tagged: [leaderBoardRow({ value: 12.4 })],
    tag_ratio: [leaderBoardRow({ value: 1.8 })],
    ...overrides,
  };
}

export function playerStatRow(overrides: Partial<PlayerStatRow> = {}): PlayerStatRow {
  return {
    player: { id: 25, name: 'Ace', team: { id: 3, name: 'Red Raiders' } },
    watched: false,
    team: { id: 3, name: 'Red Raiders' },
    role: 'commander',
    games: 8,
    stats: {
      points_scored: 9120.5,
      mvp: 14.2,
      tags_made: 41,
      times_tagged: 22,
      accuracy: 0.42,
      final_lives: 6,
      resupplies_given: 0,
      missiles_landed: 3,
      specials_used: 2,
      follow_up_shots: 4,
      reaction_shots: 1,
      combo_resupply_count: 0,
      tag_ratio: 1.86,
      survival: 0.75,
    },
    ...overrides,
  };
}

export function playerRatingRow(overrides: Partial<PlayerRatingRow> = {}): PlayerRatingRow {
  return {
    player: { id: 25, name: 'Ace', team: { id: 3, name: 'Red Raiders' } },
    watched: false,
    preferred_roles: ['commander', 'heavy'],
    overall_rating: 72.4,
    potential: 80.5,
    age: 24,
    height: '5\'11"',
    home_site: 'Laserforce Ventura',
    started_playing_age: 15,
    total_games: 120,
    stats: {
      player_awareness: 70,
      game_awareness: 75,
      resource_awareness: 66,
      decision_making: 71,
      positioning: 69,
      stamina: 80,
      speed: 77,
      flexibility: 64,
      adaptability: 70,
      communication: 72,
      teamwork: 74,
      offensive_synergy: 68,
      defensive_synergy: 63,
      midfield_synergy: 65,
      resupply_synergy: 60,
      resupply_efficiency: 58,
      accuracy: 68,
      survival: 73,
      special_usage: 70,
    },
    ...overrides,
  };
}

export function teamStatRow(overrides: Partial<TeamStatRow> = {}): TeamStatRow {
  return {
    team_id: 3,
    team_name: 'Red Raiders',
    rounds_played: 8,
    avg_points_for: 9120.46,
    avg_points_against: 8011.04,
    avg_margin: 1109.42,
    avg_survivors: 2.375,
    total_tags_landed: 412,
    total_times_tagged: 380,
    base_captures: 6,
    missiles_fired: 30,
    missiles_hit: 21,
    nukes_fired: 4,
    nukes_landed: 4,
    cancelled_nukes: 1,
    team: { id: 3, name: 'Red Raiders' },
    ...overrides,
  };
}

export function featRow(overrides: Partial<FeatRow> = {}): FeatRow {
  return {
    player_id: 25,
    player_name: 'Ace',
    role: 'commander',
    team_id: 3,
    team_name: 'Red Raiders',
    round_id: 70,
    opp_team_name: 'Blue Bolts',
    result: 'W',
    season_id: 11,
    season_name: 'Season 1',
    stats: {
      points_scored: 12400,
      mvp: 16.5,
      tags_made: 22,
      times_tagged: 9,
      accuracy: 0.48,
      final_lives: 4,
      resupplies_given: 0,
      missiles_landed: 2,
      specials_used: 1,
      follow_up_shots: 3,
      reaction_shots: 1,
      combo_resupply_count: 0,
      nuke_detonations: 0,
    },
    feats: [
      { kind: 'high_tags', label: 'Tags', is_season_best: false },
      { kind: 'high_points', label: 'Points', is_season_best: true },
    ],
    player: { id: 25, name: 'Ace', team: { id: 3, name: 'Red Raiders' } },
    watched: false,
    ...overrides,
  };
}

export function teamFeat(overrides: Partial<TeamFeat> = {}): TeamFeat {
  return {
    kind: 'comeback_win',
    label: 'Comeback win (won the match after losing round 1)',
    team_name: 'Red Raiders',
    team: { id: 3, name: 'Red Raiders' },
    round_id: 70,
    ...overrides,
  };
}

export function statisticalFeats(overrides: Partial<StatisticalFeats> = {}): StatisticalFeats {
  return { feats: [featRow()], team_feats: [teamFeat()], ...overrides };
}

export function gameLogRow(overrides: Partial<GameLogRow> = {}): GameLogRow {
  return {
    round: {
      id: 70,
      round_number: 1,
      match_id: 35,
      date_played: '2026-10-05T19:30:00Z',
      team_red: { id: 3, name: 'Red Raiders' },
      team_blue: { id: 5, name: 'Blue Bolts' },
    },
    red_points: 9120,
    blue_points: 8011,
    winner: { id: 3, name: 'Red Raiders' },
    ...overrides,
  };
}

export function powerRankingRow(overrides: Partial<PowerRankingRow> = {}): PowerRankingRow {
  return {
    rank: 1,
    team_id: 3,
    team_name: 'Red Raiders',
    mean_rating: 64.2,
    win_pct: 0.75,
    avg_score_diff: 1109.4,
    norm_rating: 1,
    norm_win_pct: 1,
    norm_score_diff: 0.8125,
    power_score: 2.8125,
    team: { id: 3, name: 'Red Raiders' },
    ...overrides,
  };
}

export function bracketNode(overrides: Partial<BracketNode> = {}): BracketNode {
  return {
    position: 0,
    team_a: { id: 3, name: 'Red Raiders' },
    team_b: { id: 5, name: 'Blue Bolts' },
    seed_a: 1,
    seed_b: 2,
    is_bye: false,
    wins_a: 1,
    wins_b: 0,
    series_length: 1,
    winner: { id: 3, name: 'Red Raiders' },
    games: [],
    ...overrides,
  };
}

/** A Season-wide bracket, its one Final played. */
export function playoffBracket(overrides: Partial<PlayoffBracket> = {}): PlayoffBracket {
  return {
    key: '2',
    name: 'Playoffs',
    stage: '',
    stage_label: '',
    conference: null,
    champion: null,
    pending: false,
    rounds: [{ bracket_round: 1, nodes: [bracketNode()] }],
    ...overrides,
  };
}

export function worldsQualifier(overrides: Partial<WorldsQualifier> = {}): WorldsQualifier {
  return {
    team_id: 3,
    team_name: 'Red Raiders',
    conference_id: 31,
    conference_name: 'East',
    tier: 1,
    provenance: 'conference_champion',
    matches_played: 7,
    league_points: 18,
    round_wins: 12,
    total_score: 64000,
    seed: 1,
    provenance_label: 'Conference champion',
    ...overrides,
  };
}

export function playoffsScreen(overrides: Partial<PlayoffsScreen> = {}): PlayoffsScreen {
  return {
    season: { id: 11, name: 'Season 1' },
    brackets: [playoffBracket()],
    worlds_qualifiers: [],
    ...overrides,
  };
}

/** A non-paged stats envelope: `data` plus `meta.season_scope` (and any other meta keys). */
export function scopedEnvelope<T>(
  data: T,
  meta: { season_scope?: SeasonScope; team_options?: TeamRef[] } = {},
) {
  return { data, meta: { season_scope: seasonScope(), ...meta } };
}

/** A paged stats envelope (one page unless told otherwise), `page_size` 10. */
export function pagedEnvelope<T>(
  data: T,
  count: number,
  meta: {
    page?: number;
    page_size?: number;
    total_pages?: number;
    season_scope?: SeasonScope;
    team_options?: TeamRef[];
  } = {},
) {
  return { data, meta: { page: 1, page_size: 10, count, total_pages: 1, ...meta } };
}

/** A 400 on one param (I2): flush with `req.flush(...badParam('season'))`. */
export function badParam(field: string, message = 'Select a valid choice.') {
  return [
    { errors: [{ code: 'invalid_choice', message, field }], meta: {} },
    { status: 400, statusText: 'Bad Request' },
  ] as const;
}

/** A failure with no field: flush with `req.flush(...failure(500, 'Database is down.'))`. */
export function failure(status: number, message: string) {
  return [
    { errors: [{ code: 'error', message, field: null }], meta: {} },
    { status, statusText: 'Error' },
  ] as const;
}
