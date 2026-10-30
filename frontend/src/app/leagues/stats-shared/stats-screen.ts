import { DestroyRef, Signal, inject, signal } from '@angular/core';
import { takeUntilDestroyed, toObservable } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Params, Router } from '@angular/router';
import {
  EMPTY,
  Observable,
  Subject,
  catchError,
  combineLatest,
  distinctUntilChanged,
  filter,
  map,
  of,
  startWith,
  switchMap,
  tap,
} from 'rxjs';
import { SeasonParam } from '../../api/league-stats.models';
import { apiMessage } from '../../shared/api-message';
import { GENERIC_ERROR_MESSAGE } from '../../shared/form-errors';
import { LeagueContext } from './league-context';
import {
  PagedStatsApiParams,
  PagedStatsQueryConfig,
  StatsApiParams,
  StatsQuery,
  StatsQueryConfig,
  parseStatsQuery,
  recoverFrom,
  sortArrow,
  statsApiParams,
  toQueryParams,
  withPage,
  withPerPage,
  withRate,
  withSeason,
  withSort,
  withTeam,
} from './stats-query';

/** What `injectStatsScreen` gives a screen. */
export interface StatsScreen<R, Rate extends string = string> {
  /** The view state the URL holds now. */
  readonly query: Signal<StatsQuery<Rate>>;
  /** The last successful load; null before the first. */
  readonly result: Signal<R | null>;
  /**
   * The error box's message (I2: a failure `recoverFrom` can't fix); null otherwise. Check it
   * before `result`: the last result stays set behind it.
   */
  readonly loadError: Signal<string | null>;
  /** `next` as query params, for a `[routerLink]="[]" [queryParams]` link. */
  params(next: StatsQuery<Rate>): Params;
  /** Navigate to `next` (a picker or pager event); the load follows the URL. */
  go(next: StatsQuery<Rate>): void;
  /** A sort header link's query params: `withSort` on the current query. */
  sortParams(key: string): Params;
  /** A sort header's arrow: ' ↑' / ' ↓' on the active sort, '' elsewhere. */
  arrow(key: string): string;
  /** The pickers' and pager's events: each `go(withX(query(), value))`. */
  pickSeason(season: SeasonParam | null): void;
  pickTeam(team: number | null): void;
  pickRate(rate: Rate): void;
  pickPerPage(perPage: number): void;
  pickPage(page: number): void;
  /** I2's Retry: re-sends the current request. */
  retry(): void;
}

/** `injectStatsScreen`'s opt-ins; leaving them out keeps the ANG-06-15 behaviour. */
export interface StatsScreenOptions {
  /**
   * Wait for the League nav (`LeagueContext`) and send nothing while its `displayed_season` is
   * null (I1: the screen shows its no-Season notice instead). Each new request also clears
   * `loadError`, so Retry or a picker hides the error box while it loads (Free Agents, Watch
   * List). Off by default: the screen loads at once and keeps the error box until a success.
   */
  waitForSeason?: boolean;
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
 * `loadError` to the server's message. A `PagedStatsQueryConfig` types `load`'s params as
 * `PagedStatsApiParams` (`page` / `page_size` required). The template binds a sort header to
 * `screen.sortParams(key)` / `screen.arrow(key)`, a picker to `screen.pickSeason($event)`, and
 * so on, and the error box's Retry to `screen.retry()`. `{ waitForSeason: true }` gates the
 * load on the League nav (see `StatsScreenOptions`).
 */
export function injectStatsScreen<R, Rate extends string = string>(
  config: PagedStatsQueryConfig<Rate>,
  load: (leagueId: number, params: PagedStatsApiParams<Rate>) => Observable<R>,
  options?: StatsScreenOptions,
): StatsScreen<R, Rate>;
export function injectStatsScreen<R, Rate extends string = string>(
  config: StatsQueryConfig<Rate>,
  load: (leagueId: number, params: StatsApiParams<Rate>) => Observable<R>,
  options?: StatsScreenOptions,
): StatsScreen<R, Rate>;
export function injectStatsScreen<R, Rate extends string>(
  config: StatsQueryConfig<Rate>,
  pagedOrNotLoad:
    | ((leagueId: number, params: PagedStatsApiParams<Rate>) => Observable<R>)
    | ((leagueId: number, params: StatsApiParams<Rate>) => Observable<R>),
  options: StatsScreenOptions = {},
): StatsScreen<R, Rate> {
  // The overloads pair a paged `load` only with a `PagedStatsQueryConfig`, whose
  // `statsApiParams` always carry `page` and `page_size`: each `load` gets the params it takes.
  const load = pagedOrNotLoad as (leagueId: number, params: StatsApiParams<Rate>) => Observable<R>;
  const route = inject(ActivatedRoute);
  const router = inject(Router);
  const query = signal(parseStatsQuery(route.snapshot.queryParamMap, config));
  const result = signal<R | null>(null);
  const loadError = signal<string | null>(null);

  const params = (next: StatsQuery<Rate>): Params => toQueryParams(next, config);
  const navigate = (next: StatsQuery<Rate>, replaceUrl: boolean): void => {
    void router.navigate([], { relativeTo: route, queryParams: params(next), replaceUrl });
  };
  const go = (next: StatsQuery<Rate>): void => navigate(next, false);

  const gated = options.waitForSeason === true;
  const reloads = new Subject<void>();
  const id$ = (route.parent ?? route).paramMap.pipe(map((p) => Number(p.get('id'))));
  const query$ = route.queryParamMap.pipe(map((p) => parseStatsQuery(p, config)));
  // Ungated: always open. Gated: nothing until the nav loads, then whether it has a Season.
  const open$ = gated
    ? toObservable(inject(LeagueContext).nav).pipe(
        filter((nav) => nav !== null),
        map((nav) => nav.displayed_season !== null),
        distinctUntilChanged(),
      )
    : of(true);
  combineLatest([id$, query$, open$, reloads.pipe(startWith(undefined))])
    .pipe(
      tap(([, q]) => {
        query.set(q);
        if (gated) loadError.set(null);
      }),
      switchMap(([id, q, open]) =>
        !open
          ? EMPTY
          : load(id, statsApiParams(q, config)).pipe(
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
    go,
    sortParams: (key) => params(withSort(query(), key, config)),
    arrow: (key) => (key === query().sort ? sortArrow(query().dir) : ''),
    pickSeason: (season) => go(withSeason(query(), season)),
    pickTeam: (team) => go(withTeam(query(), team)),
    pickRate: (rate) => go(withRate(query(), rate)),
    pickPerPage: (perPage) => go(withPerPage(query(), perPage)),
    pickPage: (page) => go(withPage(query(), page)),
    retry: () => reloads.next(),
  };
}
