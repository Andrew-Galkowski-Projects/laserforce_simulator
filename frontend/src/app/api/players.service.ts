import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { ApiClient, Page } from './api-client';
import {
  CareerParams,
  CareerStats,
  Player,
  PlayerInput,
  PlayerListParams,
  RoleBenchmarks,
} from './players.models';

/**
 * `/api/players/`: list, detail, edit, delete, career and role benchmarks. The API has no standalone
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

  /** The career averages, per-role benchmark tables and points trend (HX-01 / HX-02). */
  career(id: number, params?: CareerParams): Observable<CareerStats> {
    return this.api.get<CareerStats>(`players/${id}/career/`, params);
  }

  /** The per-role benchmark distributions over the caller's own and Unmanaged Rounds. */
  benchmarks(params?: CareerParams): Observable<RoleBenchmarks> {
    return this.api.get<RoleBenchmarks>('players/benchmarks/', params);
  }
}
