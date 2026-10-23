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
  RoundPlayback,
  RoundTimeline,
} from './rounds.models';

/**
 * `/api/rounds/`: list, detail, create, the report PDF, the paged event list, the
 * whole timeline and the Playback snapshots. No edit or delete.
 */
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

  /** The Round's whole **Event log** (movement included), unpaged; full fidelity server-side. */
  timeline(roundId: number): Observable<RoundTimeline> {
    return this.api.get<RoundTimeline>(`rounds/${roundId}/timeline/`);
  }

  /** The Round's **Playback snapshots** per player plus the beta map; full fidelity server-side. */
  playback(roundId: number): Observable<RoundPlayback> {
    return this.api.get<RoundPlayback>(`rounds/${roundId}/playback/`);
  }
}
