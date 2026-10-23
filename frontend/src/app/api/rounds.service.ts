import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { ApiClient, Page } from './api-client';
import {
  GameEvent,
  GameEventListParams,
  GameRound,
  GameRoundListItem,
  RoundInput,
  RoundListParams,
} from './rounds.models';

/** `/api/rounds/`: list, detail, create, the report PDF and the event log. No edit or delete. */
@Injectable({ providedIn: 'root' })
export class RoundsService {
  private readonly api = inject(ApiClient);

  list(params?: RoundListParams): Observable<Page<GameRoundListItem>> {
    return this.api.list<GameRoundListItem>('rounds/', params);
  }

  get(id: number): Observable<GameRound> {
    return this.api.get<GameRound>(`rounds/${id}/`);
  }

  /** Simulates the Round synchronously; resolves to the full Round (201). */
  create(body: RoundInput): Observable<GameRound> {
    return this.api.post<GameRound>('rounds/', body);
  }

  /** The Round report PDF (raw, not enveloped; the server upgrades the Round to full fidelity). */
  report(id: number): Observable<Blob> {
    return this.api.getRaw(`rounds/${id}/report/`);
  }

  /** The Round's event log in `(timestamp, id)` order, filtered and paged. */
  events(roundId: number, params?: GameEventListParams): Observable<Page<GameEvent>> {
    return this.api.list<GameEvent>(`rounds/${roundId}/events/`, params);
  }
}
