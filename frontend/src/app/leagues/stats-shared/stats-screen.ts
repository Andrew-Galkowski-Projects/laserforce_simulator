import { DestroyRef, Signal, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Params, Router } from '@angular/router';
import { EMPTY, Observable, catchError, combineLatest, map, switchMap, tap } from 'rxjs';
import { apiMessage } from '../../shared/api-message';
import { GENERIC_ERROR_MESSAGE } from '../../shared/form-errors';
import {
  StatsApiParams,
  StatsQuery,
  StatsQueryConfig,
  parseStatsQuery,
  recoverFrom,
  statsApiParams,
  toQueryParams,
} from './stats-query';

/** What `injectStatsScreen` gives a screen. */
export interface StatsScreen<R> {
  /** The view state the URL holds now. */
  readonly query: Signal<StatsQuery>;
  /** The last successful load; null before the first. */
  readonly result: Signal<R | null>;
  /**
   * The error box's message (I2: a failure `recoverFrom` can't fix); null otherwise. Check it
   * before `result`: the last result stays set behind it.
   */
  readonly loadError: Signal<string | null>;
  /** `next` as query params, for a `[routerLink]="[]" [queryParams]` link (a sort header). */
  params(next: StatsQuery): Params;
  /** Navigate to `next` (a picker or pager event); the load follows the URL. */
  go(next: StatsQuery): void;
}

/**
 * Wires one League stats screen to its URL (spec #417 I1, I2). Call it in a field initializer:
 *
 *   private readonly stats = inject(LeagueStatsService);
 *   protected readonly screen = injectStatsScreen(CONFIG, (id, params) =>
 *     this.stats.teamStats(id, params));
 *
 * It reads `:id` from the parent `LeagueLayout` route and the `StatsQuery` from the query
 * string, calls `load` with `statsApiParams` on every change (`switchMap`: a new request
 * supersedes the one in flight), and on a failure either navigates to `recoverFrom`'s state
 * with `replaceUrl` (a 400 on season / team_id / rate / ordering, or a 404 past page 1) or sets
 * `loadError` to the server's message. A sort link uses `params(withSort(query(), key, CONFIG))`;
 * a picker calls `go(withSeason(query(), value))`, and so on.
 */
export function injectStatsScreen<R>(
  config: StatsQueryConfig,
  load: (leagueId: number, params: StatsApiParams) => Observable<R>,
): StatsScreen<R> {
  const route = inject(ActivatedRoute);
  const router = inject(Router);
  const query = signal(parseStatsQuery(route.snapshot.queryParamMap, config));
  const result = signal<R | null>(null);
  const loadError = signal<string | null>(null);

  const params = (next: StatsQuery): Params => toQueryParams(next, config);
  const navigate = (next: StatsQuery, replaceUrl: boolean): void => {
    void router.navigate([], { relativeTo: route, queryParams: params(next), replaceUrl });
  };

  const id$ = (route.parent ?? route).paramMap.pipe(map((p) => Number(p.get('id'))));
  const query$ = route.queryParamMap.pipe(map((p) => parseStatsQuery(p, config)));
  combineLatest([id$, query$])
    .pipe(
      tap(([, q]) => query.set(q)),
      switchMap(([id, q]) =>
        load(id, statsApiParams(q, config)).pipe(
          catchError((error: unknown) => {
            const next = recoverFrom(q, error, config);
            // A state that writes the same URL would never reload: show the error instead.
            if (next && JSON.stringify(params(next)) !== JSON.stringify(params(q))) {
              navigate(next, true);
            } else {
              loadError.set(apiMessage(error, GENERIC_ERROR_MESSAGE));
            }
            return EMPTY;
          }),
        ),
      ),
      takeUntilDestroyed(inject(DestroyRef)),
    )
    .subscribe((loaded) => {
      loadError.set(null);
      result.set(loaded);
    });

  return {
    query: query.asReadonly(),
    result: result.asReadonly(),
    loadError: loadError.asReadonly(),
    params,
    go: (next) => navigate(next, false),
  };
}
