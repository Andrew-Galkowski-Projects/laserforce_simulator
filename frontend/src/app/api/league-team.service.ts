import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { ApiClient, Page, WithMeta } from './api-client';
import { TeamOptionsMeta } from './league-stats.models';
import {
  TeamHistory,
  TeamHistoryPlayerRow,
  TeamHistoryPlayersParams,
  TeamRoster,
  TeamSchedule,
} from './league-team.models';

/**
 * The League Team screens (ANG-06-16, spec #428 F1): `/api/leagues/{id}/teams/{team_id}/...`.
 * `id` is the League, `teamId` the Team. A Team not enrolled in the displayed Season (roster,
 * history) or not owned (schedule) is an `ApiError` 404; so is the schedule of a League with no
 * Season. Roster, schedule and history keep `meta.team_options` (the Team picker's options).
 */
@Injectable({ providedIn: 'root' })
export class LeagueTeamService {
  private readonly api = inject(ApiClient);

  roster(id: number, teamId: number): Observable<WithMeta<TeamRoster, TeamOptionsMeta>> {
    return this.api.getWithMeta<TeamRoster, TeamOptionsMeta>(this.path(id, teamId, 'roster/'));
  }

  schedule(id: number, teamId: number): Observable<WithMeta<TeamSchedule, TeamOptionsMeta>> {
    return this.api.getWithMeta<TeamSchedule, TeamOptionsMeta>(this.path(id, teamId, 'schedule/'));
  }

  history(id: number, teamId: number): Observable<WithMeta<TeamHistory, TeamOptionsMeta>> {
    return this.api.getWithMeta<TeamHistory, TeamOptionsMeta>(this.path(id, teamId, 'history/'));
  }

  historyPlayers(
    id: number,
    teamId: number,
    params: TeamHistoryPlayersParams,
  ): Observable<Page<TeamHistoryPlayerRow>> {
    return this.api.list<TeamHistoryPlayerRow>(this.path(id, teamId, 'history/players/'), params);
  }

  private path(id: number, teamId: number, screen: string): string {
    return `leagues/${id}/teams/${teamId}/${screen}`;
  }
}
