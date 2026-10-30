import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { ApiClient, WithMeta } from './api-client';
import {
  LeagueFinances,
  TeamFinances,
  TeamFinancesHeader,
  TeamFinancesUpdate,
} from './league-finances.models';
import { TeamOptionsMeta } from './league-stats.models';

/**
 * The League finance screens (ANG-06-17, spec #439): `/api/leagues/{id}/finances/` and
 * `/api/leagues/{id}/teams/{team_id}/finances/`. `leagueId` is the League, `teamId` the Team. A
 * Team not enrolled in the displayed Season, a League with no Season, or another Account's League
 * is an `ApiError` 404. The Team GET keeps `meta.team_options` (the Team picker); `ApiClient` has
 * no PATCH-with-meta, so `updateTeam` returns the payload alone and the screen keeps the options
 * it holds from the GET. A PATCH 409 (`finance_disabled` / `team_not_editable`) or 400 (the
 * `TeamFinancesForm` field name) is an `ApiError` too.
 */
@Injectable({ providedIn: 'root' })
export class LeagueFinancesService {
  private readonly api = inject(ApiClient);

  league(leagueId: number): Observable<LeagueFinances> {
    return this.api.get<LeagueFinances>(`leagues/${leagueId}/finances/`);
  }

  team(
    leagueId: number,
    teamId: number,
  ): Observable<WithMeta<TeamFinancesHeader | TeamFinances, TeamOptionsMeta>> {
    return this.api.getWithMeta<TeamFinancesHeader | TeamFinances, TeamOptionsMeta>(
      this.teamPath(leagueId, teamId),
    );
  }

  updateTeam(
    leagueId: number,
    teamId: number,
    body: TeamFinancesUpdate,
  ): Observable<TeamFinancesHeader | TeamFinances> {
    return this.api.patch<TeamFinancesHeader | TeamFinances>(this.teamPath(leagueId, teamId), body);
  }

  private teamPath(leagueId: number, teamId: number): string {
    return `leagues/${leagueId}/teams/${teamId}/finances/`;
  }
}
