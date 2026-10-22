import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { ApiClient, Page } from './api-client';
import { Player, PlayerInput } from './players.models';
import { Team, TeamInput, TeamListItem, TeamListParams, TeamSlotsInput } from './teams.models';

/** `/api/teams/`: list, detail, create, edit, assign slots and add-player. The API has no Team delete. */
@Injectable({ providedIn: 'root' })
export class TeamsService {
  private readonly api = inject(ApiClient);

  list(params?: TeamListParams): Observable<Page<TeamListItem>> {
    return this.api.list<TeamListItem>('teams/', params);
  }

  get(id: number): Observable<Team> {
    return this.api.get<Team>(`teams/${id}/`);
  }

  create(body: TeamInput): Observable<Team> {
    return this.api.post<Team>('teams/', body);
  }

  update(id: number, body: Partial<TeamInput>): Observable<Team> {
    return this.api.patch<Team>(`teams/${id}/`, body);
  }

  /** Full replace of the six role slots (`PUT teams/{id}/slots/`). */
  setSlots(id: number, body: TeamSlotsInput): Observable<Team> {
    return this.api.put<Team>(`teams/${id}/slots/`, body);
  }

  addPlayer(teamId: number, body: PlayerInput): Observable<Player> {
    return this.api.post<Player>(`teams/${teamId}/players/`, body);
  }
}
