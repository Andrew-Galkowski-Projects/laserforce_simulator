import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { ApiClient, Page } from './api-client';
import { Match, MatchInput, MatchListParams } from './matches.models';

/** `/api/matches/`: list, detail and create. The API has no Match edit or delete. */
@Injectable({ providedIn: 'root' })
export class MatchesService {
  private readonly api = inject(ApiClient);

  list(params?: MatchListParams): Observable<Page<Match>> {
    return this.api.list<Match>('matches/', params);
  }

  get(id: number): Observable<Match> {
    return this.api.get<Match>(`matches/${id}/`);
  }

  /** Simulates both Rounds synchronously; resolves to the finished Match (201). */
  create(body: MatchInput): Observable<Match> {
    return this.api.post<Match>('matches/', body);
  }
}
