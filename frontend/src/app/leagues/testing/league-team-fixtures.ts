// League Team screen spec fixtures (ANG-06-16, spec #428 F9): one builder per
// `api/league-team.models.ts` type, every required field filled, each a real-shaped payload that
// `overrides` adjusts. Imported only by *.spec.ts files. Envelopes: `withTeamOptions` for the three
// team actions (`meta.team_options`), `pagedEnvelope` (league-stats-fixtures) for history players.
import { TeamRef } from '../../api/analytics.models';
import {
  CompletedScheduleRow,
  MemberNightAppearance,
  RosterRow,
  ScheduleRow,
  TeamHistory,
  TeamHistoryOverall,
  TeamHistoryPlayerRow,
  TeamHistorySeason,
  TeamRoster,
  TeamSchedule,
} from '../../api/league-team.models';
import { teamOptions } from './league-stats-fixtures';

/** A bench row: Ace (25) of Red Raiders (3). `rosterRow({ role: 'commander' })` is a starter. */
export function rosterRow(overrides: Partial<RosterRow> = {}): RosterRow {
  return {
    player: { id: 25, name: 'Ace', team: { id: 3, name: 'Red Raiders' } },
    watched: false,
    overall_rating: 72.44,
    potential: 80.56,
    age: 24,
    height: '5\'11"',
    home_site: 'Laserforce Ventura',
    started_playing_age: 15,
    total_games: 120,
    ...overrides,
  };
}

/** Red Raiders (3): Ace starting at commander, Bolt (26) on the bench. */
export function teamRoster(overrides: Partial<TeamRoster> = {}): TeamRoster {
  return {
    team: { id: 3, name: 'Red Raiders' },
    starters: [rosterRow({ role: 'commander' })],
    bench: [rosterRow({ player: { id: 26, name: 'Bolt', team: { id: 3, name: 'Red Raiders' } } })],
    ...overrides,
  };
}

/** Matchday 2, Round 1: Red Raiders (red) v Blue Bolts (blue), unplayed. */
export function scheduleRow(overrides: Partial<ScheduleRow> = {}): ScheduleRow {
  return {
    matchday: 2,
    round_number: 1,
    date: '2026-10-12',
    red_team_id: 3,
    red_team_name: 'Red Raiders',
    blue_team_id: 5,
    blue_team_name: 'Blue Bolts',
    ...overrides,
  };
}

/** Matchday 1, Round 1, played as Round 70: Red Raiders won 9120 - 8011. */
export function completedScheduleRow(
  overrides: Partial<CompletedScheduleRow> = {},
): CompletedScheduleRow {
  return {
    ...scheduleRow({ matchday: 1, date: '2026-10-05' }),
    game_round_id: 70,
    red_score: 9120,
    blue_score: 8011,
    outcome: 'W',
    ...overrides,
  };
}

/** A played member-night game (Match 90, Rounds 91 and 92) that Ace appeared in. */
export function memberNightAppearance(
  overrides: Partial<MemberNightAppearance> = {},
): MemberNightAppearance {
  return {
    match_id: 90,
    team_a_name: 'MN Ventura G1 A',
    team_b_name: 'MN Ventura G1 B',
    is_completed: true,
    red_points: 15000,
    blue_points: 14200,
    winner_name: 'MN Ventura G1 A',
    rounds: [
      { id: 91, round_number: 1 },
      { id: 92, round_number: 2 },
    ],
    players: ['Ace'],
    ...overrides,
  };
}

/** Red Raiders' schedule in Metro League's Season 1: one row of each kind. */
export function teamSchedule(overrides: Partial<TeamSchedule> = {}): TeamSchedule {
  return {
    league: { id: 1, name: 'Metro League' },
    season: { id: 11, name: 'Season 1' },
    team: { id: 3, name: 'Red Raiders' },
    upcoming: [scheduleRow()],
    completed: [completedScheduleRow()],
    member_night_appearances: [memberNightAppearance()],
    ...overrides,
  };
}

export function teamHistoryOverall(
  overrides: Partial<TeamHistoryOverall> = {},
): TeamHistoryOverall {
  return { wins: 12, losses: 6, ties: 2, playoff_appearances: 2, championships: 1, ...overrides };
}

/** Season 1 (2026): 7-1-0, ranked 1st. */
export function teamHistorySeason(overrides: Partial<TeamHistorySeason> = {}): TeamHistorySeason {
  return {
    season: { id: 11, name: 'Season 1' },
    year: 2026,
    wins: 7,
    losses: 1,
    ties: 0,
    rank: 1,
    ...overrides,
  };
}

/** Red Raiders' history: Season 1 then Season 0, year descending. */
export function teamHistory(overrides: Partial<TeamHistory> = {}): TeamHistory {
  return {
    team: { id: 3, name: 'Red Raiders' },
    overall: teamHistoryOverall(),
    seasons: [
      teamHistorySeason(),
      teamHistorySeason({
        season: { id: 10, name: 'Season 0' },
        year: 2025,
        wins: 5,
        losses: 5,
        ties: 2,
        rank: 4,
      }),
    ],
    ...overrides,
  };
}

/** Ace, still on the Team, last played in 2026. */
export function teamHistoryPlayerRow(
  overrides: Partial<TeamHistoryPlayerRow> = {},
): TeamHistoryPlayerRow {
  return {
    player: { id: 25, name: 'Ace', team: { id: 3, name: 'Red Raiders' } },
    watched: false,
    on_team: true,
    games_played: 16,
    last_season_year: 2026,
    stats: {
      points_scored: 145000,
      tags_made: 650,
      times_tagged: 340,
      missiles_landed: 12,
      resupplies_given: 0,
      specials_used: 30,
    },
    ...overrides,
  };
}

/** A team action's envelope: `data` plus `meta.team_options` (B2), two Teams by default. */
export function withTeamOptions<T>(data: T, teams: TeamRef[] = teamOptions()) {
  return { data, meta: { team_options: teams } };
}
