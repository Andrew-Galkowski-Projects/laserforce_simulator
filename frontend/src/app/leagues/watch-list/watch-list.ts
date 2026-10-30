import { Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed, toObservable } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Params, Router, RouterLink } from '@angular/router';
import { MatButton } from '@angular/material/button';
import { MatDialog } from '@angular/material/dialog';
import {
  EMPTY,
  Subject,
  catchError,
  combineLatest,
  distinctUntilChanged,
  filter,
  map,
  startWith,
  switchMap,
  tap,
} from 'rxjs';
import { Page, PageMeta } from '../../api/api-client';
import { LeaguePlayersService } from '../../api/league-players.service';
import {
  PlayerStatRow,
  ScopedMeta,
  SeasonParam,
  SeasonScope,
  StatRate,
} from '../../api/league-stats.models';
import { apiMessage } from '../../shared/api-message';
import { ConfirmDialog, ConfirmDialogData } from '../../shared/confirm-dialog';
import { countOf } from '../../shared/count-of';
import { GENERIC_ERROR_MESSAGE } from '../../shared/form-errors';
import { injectNotify } from '../../shared/notify';
import { LeagueContext } from '../stats-shared/league-context';
import { PlayerCell } from '../stats-shared/player-cell';
import { STAT_COLUMNS, StatColumn, statCell } from '../stats-shared/player-columns';
import { RATE_OPTIONS, RatePicker } from '../stats-shared/rate-picker';
import { SeasonPicker } from '../stats-shared/season-picker';
import { PerPagePicker, StatsPaginator } from '../stats-shared/stats-paginator';
import {
  PagedStatsQueryConfig,
  StatsQuery,
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
} from '../stats-shared/stats-query';
import { WatchState } from '../watch/watch-state';

type Loaded = Page<PlayerStatRow, PageMeta & ScopedMeta>;

/** `WatchListParamsForm`: Player Stats' sort keys and rates, no Team. */
const CONFIG: PagedStatsQueryConfig<StatRate> = {
  sortKeys: ['name', 'team', 'games', ...STAT_COLUMNS.map((c) => c.key)],
  defaultSort: 'points_scored',
  defaultDir: 'desc',
  firstDir: 'desc',
  rates: RATE_OPTIONS.map(([value]) => value),
  paged: true,
};

/** The Season picker with no Season (I1): Career only, nothing selected. */
const NO_SEASON_SCOPE: SeasonScope = { selected: null, options: [] };

const REMOVE_ALL_DIALOG: ConfirmDialogData = {
  title: 'Remove All',
  message: 'Remove every player from your watch list for this League?',
  confirm: 'Remove All',
};

/**
 * `/leagues/:id/players/watch-list` (spec #428 Watch List, `leagues/watch_list.html`): the
 * Account's watched Players as Player Stats rows, over a Season or the League career, as totals
 * or rates, sorted and paged on the server. The Season and Rate pickers always show; Remove All
 * asks in a `ConfirmDialog`, then `WatchState.clearAll()` and the empty-list state. Un-starring a
 * row (the Player cell's flag) leaves it in place until the next load. It waits for the League
 * nav and sends nothing without a displayed Season (I1); `injectStatsScreen` loads at once and has
 * no Retry, so the load is wired here from the kit's pure helpers (I2).
 */
@Component({
  selector: 'app-watch-list',
  imports: [
    RouterLink,
    MatButton,
    SeasonPicker,
    RatePicker,
    PerPagePicker,
    StatsPaginator,
    PlayerCell,
  ],
  templateUrl: './watch-list.html',
  styleUrl: './watch-list.scss',
})
export class WatchList {
  private readonly players = inject(LeaguePlayersService);
  private readonly watch = inject(WatchState);
  private readonly dialog = inject(MatDialog);
  private readonly notify = injectNotify();
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly destroyRef = inject(DestroyRef);
  protected readonly nav = inject(LeagueContext).nav;
  protected readonly columns = STAT_COLUMNS;

  protected readonly query = signal(parseStatsQuery(this.route.snapshot.queryParamMap, CONFIG));
  protected readonly result = signal<Loaded | null>(null);
  protected readonly loadError = signal<string | null>(null);
  /** Remove All succeeded: the list is empty until the next load. */
  protected readonly cleared = signal(false);
  private readonly reloads = new Subject<void>();

  /** I1: the nav has loaded and the League has no Season. */
  protected readonly noSeason = computed(() => {
    const nav = this.nav();
    return nav !== null && nav.displayed_season === null;
  });

  /** The Season picker's scope: the response's, or Career alone with no Season. */
  protected readonly scope = computed<SeasonScope | null>(() =>
    this.noSeason() ? NO_SEASON_SCOPE : (this.result()?.meta.season_scope ?? null),
  );

  constructor() {
    const id$ = (this.route.parent ?? this.route).paramMap.pipe(map((p) => Number(p.get('id'))));
    const query$ = this.route.queryParamMap.pipe(map((p) => parseStatsQuery(p, CONFIG)));
    const hasSeason$ = toObservable(this.nav).pipe(
      filter((nav) => nav !== null),
      map((nav) => nav.displayed_season !== null),
      distinctUntilChanged(),
    );
    combineLatest([id$, query$, hasSeason$, this.reloads.pipe(startWith(undefined))])
      .pipe(
        tap(([, q]) => {
          this.query.set(q);
          this.loadError.set(null);
        }),
        switchMap(([id, q, hasSeason]) =>
          !hasSeason
            ? EMPTY
            : this.players.watchList(id, statsApiParams(q, CONFIG)).pipe(
                catchError((error: unknown) => {
                  const next = recoverFrom(q, error, CONFIG);
                  if (
                    next &&
                    JSON.stringify(this.params(next)) !== JSON.stringify(this.params(q))
                  ) {
                    this.navigate(next, true);
                  } else {
                    this.loadError.set(apiMessage(error, GENERIC_ERROR_MESSAGE));
                  }
                  return EMPTY;
                }),
              ),
        ),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((loaded) => {
        this.cleared.set(false);
        this.result.set(loaded);
      });
  }

  /** I2: re-sends the same request. */
  protected retry(): void {
    this.reloads.next();
  }

  /** Asks first; on yes empties the list (`WatchState.clearAll`), else sends nothing. */
  protected removeAll(): void {
    this.dialog
      .open<ConfirmDialog, ConfirmDialogData, boolean>(ConfirmDialog, { data: REMOVE_ALL_DIALOG })
      .afterClosed()
      .pipe(
        filter((yes) => yes === true),
        switchMap(() => this.watch.clearAll()),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe({
        next: () => this.cleared.set(true),
        error: (error: unknown) => this.notify(apiMessage(error, GENERIC_ERROR_MESSAGE)),
      });
  }

  protected sortParams(key: string): Params {
    return this.params(withSort(this.query(), key, CONFIG));
  }

  protected arrow(key: string): string {
    return key === this.query().sort ? sortArrow(this.query().dir) : '';
  }

  protected pickSeason(season: SeasonParam | null): void {
    this.navigate(withSeason(this.query(), season), false);
  }

  protected pickRate(rate: StatRate): void {
    this.navigate(withRate(this.query(), rate), false);
  }

  protected pickPerPage(perPage: number): void {
    this.navigate(withPerPage(this.query(), perPage), false);
  }

  protected pickPage(page: number): void {
    this.navigate(withPage(this.query(), page), false);
  }

  protected cell(row: PlayerStatRow, column: StatColumn): string {
    return statCell(row.stats, column);
  }

  protected count(n: number): string {
    return countOf(n, 'player');
  }

  private params(next: StatsQuery<StatRate>): Params {
    return toQueryParams(next, CONFIG);
  }

  private navigate(next: StatsQuery<StatRate>, replaceUrl: boolean): void {
    void this.router.navigate([], {
      relativeTo: this.route,
      queryParams: this.params(next),
      replaceUrl,
    });
  }
}
