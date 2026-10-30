import { ParamMap } from '@angular/router';
import { LeagueNav } from '../../api/leagues.models';
import { StatsQueryConfig, parseStatsQuery } from './stats-query';

/** Only `?team=`: `parseStatsQuery`'s lenient Team reader, nothing else. */
const TEAM_ONLY: StatsQueryConfig = {
  sortKeys: [],
  defaultSort: '',
  defaultDir: 'asc',
  firstDir: 'asc',
  team: true,
};

/**
 * `?team=` as a Team id, or null when it is missing or not a positive integer. For a League team
 * screen with no other URL state (Roster, Team Schedule); a screen with a sort or paging reads
 * `parseStatsQuery(...).team` instead (Team History).
 *
 *   const team = toSignal(route.queryParamMap.pipe(map(teamQueryParam)), { initialValue: null });
 */
export function teamQueryParam(params: ParamMap): number | null {
  return parseStatsQuery(params, TEAM_ONLY).team;
}

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
