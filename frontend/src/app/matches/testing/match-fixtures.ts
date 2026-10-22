// Matches spec fixtures. Imported only by *.spec.ts files.
import { Match } from '../../api/matches.models';
import { GameRound, GameRoundListItem } from '../../api/rounds.models';

/** A completed friendly Match, Alpha (red) beating Bravo (blue) 7500 - 6600, 1 - 1 in rounds. */
export function matchRow(overrides: Partial<Match> = {}): Match {
  return {
    id: 7,
    round_ids: [21, 22],
    match_type: 'friendly',
    date_played: '2026-10-05T14:30:00Z',
    red_round1_points: 4000,
    blue_round1_points: 3000,
    red_round1_eliminated: false,
    blue_round1_eliminated: false,
    round1_eliminated_at: 1801,
    red_round2_points: 3500,
    blue_round2_points: 3600,
    red_round2_eliminated: false,
    blue_round2_eliminated: false,
    round2_eliminated_at: 1801,
    red_bonus_points: 0,
    blue_bonus_points: 0,
    leg: 1,
    is_completed: true,
    team_red: 3,
    team_blue: 4,
    winner: 3,
    team_red_name: 'Alpha',
    team_blue_name: 'Bravo',
    winner_name: 'Alpha',
    red_total_points: 7500,
    blue_total_points: 6600,
    red_rounds_won: 1,
    blue_rounds_won: 1,
    season: null,
    season_phase: null,
    conference: null,
    manager: 1,
    ...overrides,
  };
}

/** A Single Round (no Match), Alpha (red) beating Bravo (blue) 4200 - 3100. */
export function roundRow(overrides: Partial<GameRoundListItem> = {}): GameRoundListItem {
  return {
    id: 9,
    match: null,
    round_number: 1,
    team_red: 3,
    team_blue: 4,
    date_played: '2026-10-05T14:30:00Z',
    red_points: 4200,
    blue_points: 3100,
    red_team_eliminated: false,
    blue_team_eliminated: false,
    eliminated_at: 1801,
    winner: 3,
    is_completed: true,
    team_red_name: 'Alpha',
    team_blue_name: 'Bravo',
    winner_name: 'Alpha',
    ...overrides,
  };
}

/** `roundRow` as the detail serializer sends it (with player states, empty here). */
export function roundDetail(overrides: Partial<GameRound> = {}): GameRound {
  return { ...roundRow(overrides), player_states: [], ...overrides };
}

/** The `{data, meta}` envelope of one list page. */
export function pageEnvelope<T>(items: T[], page = 1, totalPages = 1, count = items.length) {
  return { data: items, meta: { page, page_size: 20, count, total_pages: totalPages } };
}
