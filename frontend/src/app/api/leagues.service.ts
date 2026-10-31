import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { ApiClient, Page } from './api-client';
import {
  DeleteSummary,
  HistoryRow,
  League,
  LeagueCreateInput,
  LeagueCreateResult,
  LeagueDashboard,
  LeagueListParams,
  LeagueNav,
  LeagueTemplate,
  NewTeamCandidates,
  PlayControls,
  Season,
} from './leagues.models';

/**
 * `/api/leagues/` (ANG-01d-1, ANG-06-14): list, detail, templates, create, delete and its
 * summary, the dashboard, the nav, History, and the fired Manager's new-team flow.
 */
@Injectable({ providedIn: 'root' })
export class LeaguesService {
  private readonly api = inject(ApiClient);

  /** Newest first by default (`-id`). */
  list(params?: LeagueListParams): Observable<Page<League>> {
    return this.api.list<League>('leagues/', params);
  }

  get(id: number): Observable<League> {
    return this.api.get<League>(`leagues/${id}/`);
  }

  /** The five curated create presets (not paginated). */
  templates(): Observable<LeagueTemplate[]> {
    return this.api.get<LeagueTemplate[]>('leagues/templates/');
  }

  /** The template path (`template` set) or the Advanced path (`CreateLeagueForm` fields). */
  create(body: LeagueCreateInput): Observable<LeagueCreateResult> {
    return this.api.post<LeagueCreateResult>('leagues/', body);
  }

  /** Career Leagues only: another mode is a 409 `not_career_league`. */
  delete(id: number): Observable<void> {
    return this.api.delete(`leagues/${id}/`);
  }

  /** What `delete` would remove; a non-career League is a 409 `not_career_league`. */
  deleteSummary(id: number): Observable<DeleteSummary> {
    return this.api.get<DeleteSummary>(`leagues/${id}/delete-summary/`);
  }

  dashboard(id: number): Observable<LeagueDashboard> {
    return this.api.get<LeagueDashboard>(`leagues/${id}/dashboard/`);
  }

  nav(id: number): Observable<LeagueNav> {
    return this.api.get<LeagueNav>(`leagues/${id}/nav/`);
  }

  /** The Play menu's state for the League's Displayed Season (ANG-06-19). */
  playControls(leagueId: number): Observable<PlayControls> {
    return this.api.get<PlayControls>(`leagues/${leagueId}/play-controls/`);
  }

  /** One History page (`page` is 1-based); the in-progress Season leads page 1 only. */
  history(id: number, page: number, pageSize: number): Observable<Page<HistoryRow>> {
    return this.api.list<HistoryRow>(`leagues/${id}/history/`, { page, page_size: pageSize });
  }

  /** A fired career Manager's eligible teams; otherwise a 409. */
  newTeamCandidates(id: number): Observable<NewTeamCandidates> {
    return this.api.get<NewTeamCandidates>(`leagues/${id}/new-team-candidates/`);
  }

  /** Take over `teamId`, then roll the next Season (201 with it). */
  reassignTeam(id: number, teamId: number): Observable<Season> {
    return this.api.post<Season>(`leagues/${id}/reassign-team/`, { team_id: teamId });
  }

  /**
   * Roll the next Season and start it (UXP-18): 201 with an active Season, or a draft one when
   * the last Season had Conferences. A fired Manager is a 409 `manager_fired`.
   */
  nextSeason(id: number): Observable<Season> {
    return this.api.post<Season>(`leagues/${id}/next-season/`, {});
  }
}
