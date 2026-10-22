import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { ApiClient, Page } from './api-client';
import { Player, PlayerInput } from './players.models';
import {
  GenerateInput,
  GenerationResult,
  RosterImportResult,
  Team,
  TeamInput,
  TeamListItem,
  TeamListParams,
  TeamSlotsInput,
} from './teams.models';

/**
 * `/api/teams/`: list, detail, create, edit, assign slots, add-player, and the bulk actions
 * (generate, roster import). The API has no Team delete.
 */
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

  /** LG-00 generation (`POST teams/generate/`): randomised Teams, or the free-agent pool. */
  generate(input: GenerateInput): Observable<GenerationResult> {
    return this.api.post<GenerationResult>('teams/generate/', input);
  }

  /** Roster import (`POST teams/import/`, multipart): the CSV under `csv_file`. */
  importRoster(file: File): Observable<RosterImportResult> {
    const form = new FormData();
    form.append('csv_file', file);
    return this.api.upload<RosterImportResult>('teams/import/', form);
  }

  /** The server's template CSV (`GET teams/import/template/`), raw: the caller saves it. */
  importTemplate(): Observable<Blob> {
    return this.api.getRaw('teams/import/template/');
  }
}
