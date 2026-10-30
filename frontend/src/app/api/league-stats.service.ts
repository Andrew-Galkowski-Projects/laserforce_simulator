import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { ApiClient, Page, PageMeta, WithMeta } from './api-client';
import {
  GameLogParams,
  GameLogRow,
  LeaderBoards,
  LeadersParams,
  PlayerRatingRow,
  PlayerRatingsParams,
  PlayerStatRow,
  PlayerStatsParams,
  PlayoffsParams,
  PlayoffsScreen,
  PowerRankingRow,
  PowerRankingsParams,
  ScopedMeta,
  StatisticalFeats,
  StatisticalFeatsParams,
  TeamOptionsMeta,
  TeamStatRow,
  TeamStatsParams,
} from './league-stats.models';

/**
 * The League stats screens (ANG-06-15, spec #417): one method per `/api/leagues/{id}/...` screen
 * action. `id` is the League id; params go out as given (undefined ones dropped). A bad param is
 * an `ApiError` 400 whose error `field` names it (I2).
 */
@Injectable({ providedIn: 'root' })
export class LeagueStatsService {
  private readonly api = inject(ApiClient);

  leaders(id: number, params: LeadersParams): Observable<WithMeta<LeaderBoards, ScopedMeta>> {
    return this.api.getWithMeta<LeaderBoards, ScopedMeta>(`leagues/${id}/leaders/`, params);
  }

  playerStats(
    id: number,
    params: PlayerStatsParams,
  ): Observable<Page<PlayerStatRow, PageMeta & ScopedMeta & TeamOptionsMeta>> {
    return this.api.list<PlayerStatRow, PageMeta & ScopedMeta & TeamOptionsMeta>(
      `leagues/${id}/player-stats/`,
      params,
    );
  }

  playerRatings(
    id: number,
    params: PlayerRatingsParams,
  ): Observable<Page<PlayerRatingRow, PageMeta & TeamOptionsMeta>> {
    return this.api.list<PlayerRatingRow, PageMeta & TeamOptionsMeta>(
      `leagues/${id}/player-ratings/`,
      params,
    );
  }

  teamStats(id: number, params: TeamStatsParams): Observable<WithMeta<TeamStatRow[], ScopedMeta>> {
    return this.api.getWithMeta<TeamStatRow[], ScopedMeta>(`leagues/${id}/team-stats/`, params);
  }

  /** Paged, but `data` is an object (`feats` is the page; `team_feats` is every Team feat). */
  statisticalFeats(
    id: number,
    params: StatisticalFeatsParams,
  ): Observable<WithMeta<StatisticalFeats, PageMeta & ScopedMeta & TeamOptionsMeta>> {
    return this.api.getWithMeta<StatisticalFeats, PageMeta & ScopedMeta & TeamOptionsMeta>(
      `leagues/${id}/statistical-feats/`,
      params,
    );
  }

  gameLog(
    id: number,
    params: GameLogParams,
  ): Observable<WithMeta<GameLogRow[], ScopedMeta & TeamOptionsMeta>> {
    return this.api.getWithMeta<GameLogRow[], ScopedMeta & TeamOptionsMeta>(
      `leagues/${id}/game-log/`,
      params,
    );
  }

  powerRankings(
    id: number,
    params: PowerRankingsParams,
  ): Observable<WithMeta<PowerRankingRow[], ScopedMeta>> {
    return this.api.getWithMeta<PowerRankingRow[], ScopedMeta>(
      `leagues/${id}/power-rankings/`,
      params,
    );
  }

  /** No Career: `season=career` is a 400. */
  playoffs(id: number, params: PlayoffsParams): Observable<WithMeta<PlayoffsScreen, ScopedMeta>> {
    return this.api.getWithMeta<PlayoffsScreen, ScopedMeta>(`leagues/${id}/playoffs/`, params);
  }
}
