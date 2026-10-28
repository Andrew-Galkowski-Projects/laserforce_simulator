// Tournament results fixtures, per format. Imported only by *.spec.ts files.
import {
  BracketGroup,
  BracketNode,
  BracketRound,
  BracketType,
  CrosstableLeg,
  CrosstableRow,
  TournamentStandingsRow,
} from '../../api/tournaments.models';

export const RED = { id: 11, name: 'Red Raiders' };
export const BLUE = { id: 12, name: 'Blue Bolts' };
export const GREEN = { id: 13, name: 'Green Giants' };
export const GOLD = { id: 14, name: 'Gold Guns' };

/** A `node_payload`: an unplayed Bo1 between Red Raiders [1] and Blue Bolts [2]. */
export function bracketNode(overrides: Partial<BracketNode> = {}): BracketNode {
  return {
    position: 0,
    team_a: RED,
    team_b: BLUE,
    seed_a: 1,
    seed_b: 2,
    is_bye: false,
    wins_a: 0,
    wins_b: 0,
    series_length: 1,
    winner: null,
    games: [],
    ...overrides,
  };
}

/** `count` rounds numbered from `first`, one node each. */
export function bracketRounds(count: number, first = 1): BracketRound[] {
  return Array.from({ length: count }, (_, i) => ({
    bracket_round: first + i,
    nodes: [bracketNode({ position: 0 })],
  }));
}

export function bracketGroup(bracket_type: BracketType, rounds: BracketRound[]): BracketGroup {
  return { bracket_type, rounds };
}

/** The three elimination groups of a double elimination (or RR->DE finals) tree. */
export function eliminationGroups(): BracketGroup[] {
  return [
    bracketGroup('winners', bracketRounds(2)),
    bracketGroup('losers', bracketRounds(2)),
    bracketGroup('grand_final', bracketRounds(1, 3)),
  ];
}

/** A `StandingsRow` + team ref. */
export function standingsRow(
  overrides: Partial<TournamentStandingsRow> = {},
): TournamentStandingsRow {
  return {
    team_id: RED.id,
    rank: 1,
    matches_played: 2,
    wins: 2,
    losses: 0,
    ties: 0,
    league_points: 12,
    round_wins: 4,
    total_score: 51000,
    team: RED,
    ...overrides,
  };
}

/** A played crosstable leg (Match 70, 30000-21000 total, 6-0 Match score). */
export function playedLeg(overrides: Partial<CrosstableLeg> = {}): CrosstableLeg {
  return {
    team_score: 30000,
    opp_score: 21000,
    match_team: 6,
    match_opp: 0,
    played: true,
    match_id: 70,
    ...overrides,
  };
}

export const UNPLAYED_LEG: CrosstableLeg = {
  team_score: null,
  opp_score: null,
  match_team: null,
  match_opp: null,
  played: false,
  match_id: null,
};

/**
 * A two-Team crosstable: Red Raiders' leg 1 against Blue Bolts is played (Match 70), and Blue
 * Bolts' leg 2 against Red Raiders is not.
 */
export function twoTeamCrosstable(): CrosstableRow[] {
  return [
    {
      team: RED,
      cells: [null, { opponent_team_id: BLUE.id, leg1: playedLeg(), leg2: null }],
    },
    {
      team: BLUE,
      cells: [{ opponent_team_id: RED.id, leg1: null, leg2: UNPLAYED_LEG }, null],
    },
  ];
}
