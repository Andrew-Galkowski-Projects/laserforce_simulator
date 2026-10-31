import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { ApiClient } from './api-client';
import { Season, SeasonDashboard } from './leagues.models';
import {
  ConferenceWrite,
  ConferencesState,
  LivePlayResult,
  MemberNightState,
  OwnerEvaluation,
  SeasonAwards,
  SeasonPlayJobRef,
  SeasonPlayJobStatus,
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

  // --- ANG-06-19: the Play menu (each refused with a 409 `play_in_progress` during a run) ---

  /** Play the next matchday; the dashboard after it. */
  playWeek(id: number): Observable<SeasonDashboard> {
    return this.api.post<SeasonDashboard>(`seasons/${id}/play-week/`, {});
  }

  /** Play one playoff bracket node; the dashboard after it. */
  playSingleRound(id: number): Observable<SeasonDashboard> {
    return this.api.post<SeasonDashboard>(`seasons/${id}/play-single-round/`, {});
  }

  /** Start a background run of the next 8 matchdays. */
  playTwoMonths(id: number): Observable<SeasonPlayJobRef> {
    return this.api.post<SeasonPlayJobRef>(`seasons/${id}/play-two-months/`, {});
  }

  /** Start a background run of the rest of the regular season. */
  playUntilEnd(id: number): Observable<SeasonPlayJobRef> {
    return this.api.post<SeasonPlayJobRef>(`seasons/${id}/play-until-end/`, {});
  }

  /** Start a background drain of the playoffs. */
  playPlayoffs(id: number): Observable<SeasonPlayJobRef> {
    return this.api.post<SeasonPlayJobRef>(`seasons/${id}/play-playoffs/`, {});
  }

  /** One Week (Live): play the manager's game now, the rest of the week in the background. */
  playLive(id: number): Observable<LivePlayResult> {
    return this.api.post<LivePlayResult>(`seasons/${id}/play-live/`, {});
  }

  /** Play one member-night game for the manager to watch (`job` is always null). */
  memberNightPlayLive(id: number): Observable<LivePlayResult> {
    return this.api.post<LivePlayResult>(`seasons/${id}/member-night/play-live/`, {});
  }

  /** Ask the Active play run to stop after its current game (a no-op with none). */
  playCancel(id: number): Observable<{ cancel_requested: boolean }> {
    return this.api.post<{ cancel_requested: boolean }>(`seasons/${id}/play-cancel/`, {});
  }

  playJob(id: number, jobId: string): Observable<SeasonPlayJobStatus> {
    return this.api.get<SeasonPlayJobStatus>(`seasons/${id}/play-jobs/${jobId}/`);
  }

  // --- ANG-06-19b: member nights ---

  /** The member-night phase's viable Sites and the games drawn so far. */
  memberNight(id: number): Observable<MemberNightState> {
    return this.api.get<MemberNightState>(`seasons/${id}/member-night/`);
  }

  /** Draw games for the chosen Sites (at least one); the dashboard after it. */
  setUpMemberNight(id: number, sites: string[]): Observable<SeasonDashboard> {
    return this.api.post<SeasonDashboard>(`seasons/${id}/member-night/`, { sites });
  }

  /** Play one member-night game; the dashboard after it. */
  memberNightPlaySingle(id: number): Observable<SeasonDashboard> {
    return this.api.post<SeasonDashboard>(`seasons/${id}/member-night/play-single/`, {});
  }

  /** Start a background run of every unplayed member-night game. */
  memberNightPlayAll(id: number): Observable<SeasonPlayJobRef> {
    return this.api.post<SeasonPlayJobRef>(`seasons/${id}/member-night/play/`, {});
  }
}
