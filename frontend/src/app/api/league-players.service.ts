import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { ApiClient, Page, PageMeta } from './api-client';
import {
  FreeAgentsParams,
  LeaguePlayer,
  WatchListParams,
  WatchResult,
} from './league-players.models';
import { PlayerRatingRow, PlayerStatRow, ScopedMeta } from './league-stats.models';

/**
 * The League Player screens and the Watch flag (ANG-06-16, spec #428 F2). `id` is the League.
 * Only `WatchState` (leagues/watch/) calls `watch` / `unwatch` / `clearWatchList`; a screen never
 * does (I4). Another Account's League or Player is an `ApiError` 404.
 */
@Injectable({ providedIn: 'root' })
export class LeaguePlayersService {
  private readonly api = inject(ApiClient);

  /** The free-agent pool with live ratings (Player Ratings' row shape). */
  freeAgents(id: number, params: FreeAgentsParams): Observable<Page<PlayerRatingRow>> {
    return this.api.list<PlayerRatingRow>(`leagues/${id}/free-agents/`, params);
  }

  /**
   * The Account's watched Players as Player Stats rows (each `watched: true`), paged, with
   * `meta.season_scope` as Player Stats has it (no `team_options`: the Watch List has no Team).
   */
  watchList(
    id: number,
    params: WatchListParams,
  ): Observable<Page<PlayerStatRow, PageMeta & ScopedMeta>> {
    return this.api.list<PlayerStatRow, PageMeta & ScopedMeta>(`leagues/${id}/watch-list/`, params);
  }

  /** The League player page: lenient, a Player with no games here is a 200 with empty sections. */
  player(id: number, playerId: number): Observable<LeaguePlayer> {
    return this.api.get<LeaguePlayer>(`leagues/${id}/players/${playerId}/`);
  }

  /** Adds the Player to the Account's watch list for this League; idempotent. */
  watch(id: number, playerId: number): Observable<WatchResult> {
    return this.api.put<WatchResult>(`leagues/${id}/watch-list/${playerId}/`, {});
  }

  /** Takes the Player off the Account's watch list for this League; idempotent. */
  unwatch(id: number, playerId: number): Observable<void> {
    return this.api.delete(`leagues/${id}/watch-list/${playerId}/`);
  }

  /** Empties the Account's watch list for this League (Remove All). */
  clearWatchList(id: number): Observable<void> {
    return this.api.delete(`leagues/${id}/watch-list/`);
  }
}
