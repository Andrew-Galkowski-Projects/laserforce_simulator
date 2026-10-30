import { LeagueNav } from '../../api/leagues.models';

/**
 * The Team a League team screen shows (spec #428 F8; Roster, Team Schedule, Team History):
 * `?team=` (the `StatsQuery.team`), else the nav's `schedule_team` (the Team the Account manages,
 * the TEAM nav's target), else null. Null with a displayed Season is the screen's "No team
 * available to display in this League's current Season." state; send no request then.
 *
 *   const teamId = resolveTeamParam(screenQuery().team, inject(LeagueContext).nav());
 */
export function resolveTeamParam(team: number | null, nav: LeagueNav | null): number | null {
  return team ?? nav?.schedule_team?.id ?? null;
}
