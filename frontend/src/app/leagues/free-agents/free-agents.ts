import { Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed, toObservable } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Params, Router, RouterLink } from '@angular/router';
import { MatButton } from '@angular/material/button';
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
import { Page } from '../../api/api-client';
import { LeaguePlayersService } from '../../api/league-players.service';
import { PlayerRatingRow } from '../../api/league-stats.models';
import { apiMessage } from '../../shared/api-message';
import { countOf } from '../../shared/count-of';
import { GENERIC_ERROR_MESSAGE } from '../../shared/form-errors';
import { LeagueContext } from '../stats-shared/league-context';
import { PlayerCell } from '../stats-shared/player-cell';
import {
  RATING_BIO_HEADERS,
  RATING_SORT_COLUMNS,
  RATING_STAT_KEYS,
  orDash,
  potentialText,
  truncateChars,
} from '../stats-shared/player-columns';
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
  withSort,
} from '../stats-shared/stats-query';

const CONFIG: PagedStatsQueryConfig = {
  sortKeys: RATING_SORT_COLUMNS.map((c) => c.key),
  defaultSort: 'team',
  defaultDir: 'asc',
  firstDir: 'asc',
  paged: true,
};

/** `free_agents.html` keeps MMR and Rank as "-" placeholders after the bio (STAT-PROXY-01). */
const BIO_HEADERS = [
  ...RATING_BIO_HEADERS,
  { key: 'mmr', label: 'MMR', full: 'MMR' },
  { key: 'rank', label: 'Rank', full: 'Rank' },
] as const;

/**
 * `/leagues/:id/players/free-agents` (spec #428 Free Agents, `leagues/free_agents.html`): the
 * displayed Season's free-agent pool with live ratings, in Player Ratings' columns, sorted and
 * paged on the server. URL state is the stats kit's (`?sort=&dir=&page=&per_page=`); no Team or
 * Season picker. It waits for the League nav and sends nothing without a displayed Season (I1);
 * `injectStatsScreen` loads at once and has no Retry, so the load is wired here from the kit's
 * pure helpers (I2: `recoverFrom`, else the error box with Retry).
 */
@Component({
  selector: 'app-free-agents',
  imports: [RouterLink, MatButton, PlayerCell, PerPagePicker, StatsPaginator],
  templateUrl: './free-agents.html',
  styleUrl: './free-agents.scss',
})
export class FreeAgents {
  private readonly players = inject(LeaguePlayersService);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  protected readonly nav = inject(LeagueContext).nav;
  protected readonly sortColumns = RATING_SORT_COLUMNS;
  protected readonly statKeys = RATING_STAT_KEYS;
  protected readonly bioHeaders = BIO_HEADERS;

  protected readonly query = signal(parseStatsQuery(this.route.snapshot.queryParamMap, CONFIG));
  protected readonly result = signal<Page<PlayerRatingRow> | null>(null);
  protected readonly loadError = signal<string | null>(null);
  private readonly reloads = new Subject<void>();

  /** I1: the nav has loaded and the League has no Season. */
  protected readonly noSeason = computed(() => {
    const nav = this.nav();
    return nav !== null && nav.displayed_season === null;
  });

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
            : this.players.freeAgents(id, statsApiParams(q, CONFIG)).pipe(
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
        takeUntilDestroyed(inject(DestroyRef)),
      )
      .subscribe((page) => this.result.set(page));
  }

  /** I2: re-sends the same request. */
  protected retry(): void {
    this.reloads.next();
  }

  protected sortParams(key: string): Params {
    return this.params(withSort(this.query(), key, CONFIG));
  }

  protected arrow(key: string): string {
    return key === this.query().sort ? sortArrow(this.query().dir) : '';
  }

  protected pickPerPage(perPage: number): void {
    this.navigate(withPerPage(this.query(), perPage), false);
  }

  protected pickPage(page: number): void {
    this.navigate(withPage(this.query(), page), false);
  }

  protected count(n: number): string {
    return countOf(n, 'free agent');
  }

  protected short(value: string, n = 14): string {
    return truncateChars(value, n);
  }

  protected roles(row: PlayerRatingRow): string {
    return row.preferred_roles.join(', ');
  }

  protected potential(row: PlayerRatingRow): string {
    return potentialText(row.potential);
  }

  /** `default:"-"`: a missing number or an empty string shows "-". */
  protected orDash(value: number | string | null): string {
    return orDash(value);
  }

  private params(next: StatsQuery): Params {
    return toQueryParams(next, CONFIG);
  }

  private navigate(next: StatsQuery, replaceUrl: boolean): void {
    void this.router.navigate([], {
      relativeTo: this.route,
      queryParams: this.params(next),
      replaceUrl,
    });
  }
}
