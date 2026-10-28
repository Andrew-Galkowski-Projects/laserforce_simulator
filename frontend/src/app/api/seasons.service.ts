import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { ApiClient } from './api-client';
import { SeasonDashboard } from './leagues.models';

/** `/api/seasons/` (ANG-01d-2): the Season writes the League screens call. */
@Injectable({ providedIn: 'root' })
export class SeasonsService {
  private readonly api = inject(ApiClient);

  /**
   * Start a draft Season: 200 with its dashboard (`round_count_total` = Rounds scheduled). A
   * Season that is not a draft is a 409 `season_not_draft`.
   */
  start(seasonId: number): Observable<SeasonDashboard> {
    return this.api.post<SeasonDashboard>(`seasons/${seasonId}/start/`, {});
  }
}
