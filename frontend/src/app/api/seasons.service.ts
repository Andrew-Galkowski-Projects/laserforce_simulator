import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { ApiClient } from './api-client';
import { Season, SeasonDashboard } from './leagues.models';
import {
  ConferenceWrite,
  ConferencesState,
  OwnerEvaluation,
  SeasonAwards,
  SeasonSchedule,
  SeasonStandings,
  StandingsParams,
} from './seasons.models';

/**
 * `/api/seasons/` (ANG-01d-1 / 01d-2): Start Season (ANG-06-14) and one read per Season screen
 * (ANG-06-18). Another Account's Season is a 404 `not_found` on every one.
 */
@Injectable({ providedIn: 'root' })
export class SeasonsService {
  private readonly api = inject(ApiClient);

  /** The Season detail (`SeasonSerializer`). */
  get(id: number): Observable<Season> {
    return this.api.get<Season>(`seasons/${id}/`);
  }

  /** The Season dashboard body. */
  dashboard(id: number): Observable<SeasonDashboard> {
    return this.api.get<SeasonDashboard>(`seasons/${id}/dashboard/`);
  }

  /** Per-Conference standings; an absent `sort` / `dir` takes `rank` / `asc` (bad: 400). */
  standings(id: number, params: StandingsParams): Observable<SeasonStandings> {
    return this.api.get<SeasonStandings>(`seasons/${id}/standings/`, {
      sort: params.sort,
      dir: params.dir,
    });
  }

  schedule(id: number): Observable<SeasonSchedule> {
    return this.api.get<SeasonSchedule>(`seasons/${id}/schedule/`);
  }

  awards(id: number): Observable<SeasonAwards> {
    return this.api.get<SeasonAwards>(`seasons/${id}/awards/`);
  }

  /** 404 `not_found` when the Season has no owner evaluation (not completed, not career). */
  ownerEvaluation(id: number): Observable<OwnerEvaluation> {
    return this.api.get<OwnerEvaluation>(`seasons/${id}/owner-evaluation/`);
  }

  conferences(id: number): Observable<ConferencesState> {
    return this.api.get<ConferencesState>(`seasons/${id}/conferences/`);
  }

  /**
   * Full replace of the partition (`[]` clears it); the saved state back. A started Season is a
   * 409 `season_not_draft`; an invalid partition a 400 listing every reason.
   */
  replaceConferences(
    id: number,
    body: { conferences: ConferenceWrite[] },
  ): Observable<ConferencesState> {
    return this.api.put<ConferencesState>(`seasons/${id}/conferences/`, body);
  }

  /**
   * Start a draft Season: 200 with its dashboard (`round_count_total` = Rounds scheduled). A
   * Season that is not a draft is a 409 `season_not_draft`.
   */
  start(seasonId: number): Observable<SeasonDashboard> {
    return this.api.post<SeasonDashboard>(`seasons/${seasonId}/start/`, {});
  }
}
