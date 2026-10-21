import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { ApiClient, Page } from './api-client';
import { Player, PlayerInput, PlayerListParams } from './players.models';

/**
 * `/api/players/`: list, detail, edit and delete. The API has no standalone
 * Player create: add one through `TeamsService.addPlayer`.
 */
@Injectable({ providedIn: 'root' })
export class PlayersService {
  private readonly api = inject(ApiClient);

  list(params?: PlayerListParams): Observable<Page<Player>> {
    return this.api.list<Player>('players/', params);
  }

  get(id: number): Observable<Player> {
    return this.api.get<Player>(`players/${id}/`);
  }

  /** PATCH: omitted fields keep their current values. */
  update(id: number, body: PlayerInput): Observable<Player> {
    return this.api.patch<Player>(`players/${id}/`, body);
  }

  delete(id: number): Observable<void> {
    return this.api.delete(`players/${id}/`);
  }
}
