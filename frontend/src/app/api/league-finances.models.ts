// League finance shapes (ANG-06-17, spec #439), snake_case exactly as `LeagueScreensMixin` sends
// them: `league_finances` (`GET leagues/{id}/finances/`) and `team_finances_payload`
// (`GET` / `PATCH leagues/{id}/teams/{team_id}/finances/`, with `meta.team_options`). Money is in
// thousands of dollars (`moneyK` renders it). No mapping layer.
import { PlayerRef, TeamRef } from './analytics.models';
import { SeasonRef } from './tournaments.models';

/** One League Finances row; `revenue` / `profit` / `payroll` are null without a snapshot. */
export interface LeagueFinanceRow {
  team: TeamRef;
  cash: number;
  revenue: number | null;
  profit: number | null;
  payroll: number | null;
}

/** `GET leagues/{id}/finances/` `data`; finances off (or no Season) => `rows: []`. */
export interface LeagueFinances {
  finance_enabled: boolean;
  rows: LeagueFinanceRow[];
}

/** One settled Season's `TeamSeasonFinance` snapshot. */
export interface FinanceLine {
  season: SeasonRef;
  ticket: number;
  national_tv: number;
  local_tv: number;
  sponsor: number;
  merch: number;
  payroll: number;
  scouting_cost: number;
  coaching_cost: number;
  facilities_cost: number;
  health_cost: number;
  luxury_tax: number;
  min_payroll_penalty: number;
  revenue: number;
  expenses: number;
  profit: number;
  hype: number;
}

/** The four Budget levels (or, as `budget_costs`, their cost per Season in thousands). */
export interface BudgetLevels {
  scouting: number;
  coaching: number;
  facilities: number;
  health: number;
}

/** `Team.injury_policy`: how an injured starter is resolved at fixture time. */
export type InjuryPolicy = 'auto_sub' | 'play_hurt';

/** The Injury policy select's labels, in the Django form's order. */
export const INJURY_POLICY_LABELS: Readonly<Record<InjuryPolicy, string>> = {
  auto_sub: 'Auto-substitute',
  play_hurt: 'Play hurt',
};

/** The Team Finances payload with finances off or no Season: these three keys only. */
export interface TeamFinancesHeader {
  finance_enabled: boolean;
  team: TeamRef;
  editable: boolean;
}

/** The full Team Finances payload (finances on and a Season). */
export interface TeamFinances extends TeamFinancesHeader {
  budgets: BudgetLevels;
  budget_costs: BudgetLevels;
  ticket_price: number;
  cash: number;
  injury_policy: InjuryPolicy;
  live_payroll: number;
  live_luxury_tax: number;
  live_min_payroll_penalty: number;
  record: { wins: number; losses: number; games: number };
  current_hype: number;
  roster_salaries: { player: PlayerRef; salary: number }[];
  availability: { player: PlayerRef; games_remaining: number }[];
  /** Settled Seasons, newest first. */
  history: FinanceLine[];
  /** `finance.MAX_LEVEL`: a Budget level's upper bound (B2). */
  max_level: number;
  /** `finance.DEFAULT_LEVEL`: the neutral Budget level (B2). */
  default_level: number;
}

/** The Team Finances `PATCH` body (`TeamFinancesForm`'s fields): Save sends all six. */
export interface TeamFinancesUpdate {
  budget_scouting: number;
  budget_coaching: number;
  budget_facilities: number;
  budget_health: number;
  injury_policy: InjuryPolicy;
  ticket_price: number;
}
