import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { ApiClient } from './api-client';
import {
  PlayerH2HParams,
  PlayerHeadToHead,
  RoundComparison,
  TeamH2HParams,
  TeamHeadToHead,
  TeamHistory,
} from './analytics.models';

/**
 * The analytics reads (ANG-06-9): team and player head-to-head, compare rounds and Team match
 * history. A 400 (e.g. "Pick two different teams to compare.") arrives as an `ApiError`.
 */
@Injectable({ providedIn: 'root' })
export class AnalyticsService {
  private readonly api = inject(ApiClient);

  teamHeadToHead(params: TeamH2HParams): Observable<TeamHeadToHead> {
    return this.api.get<TeamHeadToHead>('teams/head-to-head/', params);
  }

  playerHeadToHead(params: PlayerH2HParams): Observable<PlayerHeadToHead> {
    return this.api.get<PlayerHeadToHead>('players/head-to-head/', params);
  }

  compareRounds(roundA: number, roundB: number): Observable<RoundComparison> {
    return this.api.get<RoundComparison>('rounds/compare/', { round_a: roundA, round_b: roundB });
  }

  /**
   * `GET teams/{id}/history/`. `scopes` null sends no `scope` (every scope); `[]` sends a bare
   * `scope=` (no scope: Overall off); otherwise one `scope` per key.
   */
  teamHistory(teamId: number, scopes: string[] | null): Observable<TeamHistory> {
    const params = scopes === null ? {} : { scope: scopes.length > 0 ? scopes : '' };
    return this.api.get<TeamHistory>(`teams/${teamId}/history/`, params);
  }
}
