// League finance spec fixtures (ANG-06-17, spec #439): one builder per
// `api/league-finances.models.ts` type, every required field filled, each a real-shaped payload
// that `overrides` adjusts. Imported only by *.spec.ts files. The Team Finances envelope is
// `withTeamOptions` (league-team-fixtures).
import {
  FinanceLine,
  LeagueFinanceRow,
  LeagueFinances,
  TeamFinances,
  TeamFinancesHeader,
  TeamFinancesUpdate,
} from '../../api/league-finances.models';

/** Red Raiders (3) with a settled Season 1 snapshot. */
export function leagueFinanceRow(overrides: Partial<LeagueFinanceRow> = {}): LeagueFinanceRow {
  return {
    team: { id: 3, name: 'Red Raiders' },
    cash: 120.04,
    revenue: 250.25,
    profit: -7.24,
    payroll: 180,
    ...overrides,
  };
}

/** Finances on: Blue Bolts (5) not settled yet, then Red Raiders (3) settled, by name. */
export function leagueFinances(overrides: Partial<LeagueFinances> = {}): LeagueFinances {
  return {
    finance_enabled: true,
    rows: [
      leagueFinanceRow({
        team: { id: 5, name: 'Blue Bolts' },
        cash: 95.5,
        revenue: null,
        profit: null,
        payroll: null,
      }),
      leagueFinanceRow(),
    ],
    ...overrides,
  };
}

/** Season 1's (10) settled snapshot. */
export function financeLine(overrides: Partial<FinanceLine> = {}): FinanceLine {
  return {
    season: { id: 10, name: 'Season 1' },
    ticket: 80,
    national_tv: 60,
    local_tv: 40,
    sponsor: 50,
    merch: 20.25,
    payroll: 180,
    scouting_cost: 10,
    coaching_cost: 10,
    facilities_cost: 12,
    health_cost: 8,
    luxury_tax: 0,
    min_payroll_penalty: 0,
    revenue: 250.25,
    expenses: 257.49,
    profit: -7.24,
    hype: 0.4567,
    ...overrides,
  };
}

/** The finance-off / no-Season header for Red Raiders (3). */
export function teamFinancesHeader(
  overrides: Partial<TeamFinancesHeader> = {},
): TeamFinancesHeader {
  return {
    finance_enabled: false,
    team: { id: 3, name: 'Red Raiders' },
    editable: false,
    ...overrides,
  };
}

/** The full payload for the managed Red Raiders (3): editable, one settled Season. */
export function teamFinances(overrides: Partial<TeamFinances> = {}): TeamFinances {
  return {
    finance_enabled: true,
    team: { id: 3, name: 'Red Raiders' },
    editable: true,
    budgets: { scouting: 34, coaching: 34, facilities: 50, health: 20 },
    budget_costs: { scouting: 10, coaching: 10, facilities: 14.75, health: 6.5 },
    ticket_price: 12.5,
    cash: 120.04,
    injury_policy: 'auto_sub',
    live_payroll: 182.35,
    live_luxury_tax: 0,
    live_min_payroll_penalty: 4.5,
    record: { wins: 3, losses: 1, games: 4 },
    current_hype: 0.4567,
    roster_salaries: [
      { player: { id: 25, name: 'Ace', team: { id: 3, name: 'Red Raiders' } }, salary: 95.25 },
      { player: { id: 26, name: 'Bolt', team: { id: 3, name: 'Red Raiders' } }, salary: 87.1 },
    ],
    availability: [
      {
        player: { id: 26, name: 'Bolt', team: { id: 3, name: 'Red Raiders' } },
        games_remaining: 2,
      },
    ],
    history: [financeLine()],
    max_level: 100,
    default_level: 34,
    ...overrides,
  };
}

/** A Save's body: every field changed from `teamFinances()`. */
export function teamFinancesUpdate(
  overrides: Partial<TeamFinancesUpdate> = {},
): TeamFinancesUpdate {
  return {
    budget_scouting: 40,
    budget_coaching: 34,
    budget_facilities: 50,
    budget_health: 20,
    injury_policy: 'play_hurt',
    ticket_price: 12.5,
    ...overrides,
  };
}
