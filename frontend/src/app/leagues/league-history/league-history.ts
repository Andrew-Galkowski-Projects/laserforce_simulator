import { Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatButton } from '@angular/material/button';
import { ActivatedRoute, RouterLink } from '@angular/router';
import {
  BehaviorSubject,
  Subject,
  combineLatest,
  forkJoin,
  map,
  startWith,
  switchMap,
  tap,
} from 'rxjs';
import { Page } from '../../api/api-client';
import { AwardWinner, HistoryRow, League } from '../../api/leagues.models';
import { LeaguesService } from '../../api/leagues.service';
import { AuthService } from '../../auth/auth.service';
import { notFoundOrError } from '../../shared/not-found-or-error';
import { SoonBadge } from '../soon-badge/soon-badge';

/** `history.html`'s "Per page" choices; the first is the default (always sent). */
export const HISTORY_PAGE_SIZES = [10, 25, 50, 100] as const;

/** Which History page is shown (`page` is 1-based). */
interface PageQuery {
  page: number;
  size: number;
}

/** What one load fetches: the League (for the heading) and one History page. */
interface Loaded {
  league: League;
  history: Page<HistoryRow>;
}

/**
 * `/leagues/:id/history` (spec #409 F11, `leagues/history.html`): every Season's champion,
 * podium and MVPs, paged at 10 / 25 / 50 / 100. The in-progress Season leads page 1 only (the
 * API's accepted behaviour). Season and Awards links are Soon (I1).
 */
@Component({
  selector: 'app-league-history',
  imports: [MatButton, RouterLink, SoonBadge],
  templateUrl: './league-history.html',
  styleUrl: './league-history.scss',
})
export class LeagueHistory {
  private readonly auth = inject(AuthService);
  private readonly query = new BehaviorSubject<PageQuery>({
    page: 1,
    size: HISTORY_PAGE_SIZES[0],
  });
  private readonly reloads = new Subject<void>();

  protected readonly loaded = signal<Loaded | null>(null);
  protected readonly notFound = signal(false);
  protected readonly loadError = signal<string | null>(null);
  protected readonly pageSizes = HISTORY_PAGE_SIZES;
  protected readonly pageSize = signal<number>(HISTORY_PAGE_SIZES[0]);
  /** I4: the signed-in Account's Self-insert Player id. */
  protected readonly selfId = computed(() => this.auth.currentUser()?.self_insert?.id ?? null);

  constructor() {
    const leagues = inject(LeaguesService);
    // `:id` is on the parent `LeagueLayout` route.
    const route = inject(ActivatedRoute);
    const id$ = (route.parent ?? route).paramMap.pipe(map((p) => Number(p.get('id'))));
    combineLatest([id$, this.query, this.reloads.pipe(startWith(undefined))])
      .pipe(
        tap(() => {
          this.notFound.set(false);
          this.loadError.set(null);
        }),
        switchMap(([id, { page, size }]) =>
          forkJoin({ league: leagues.get(id), history: leagues.history(id, page, size) }).pipe(
            notFoundOrError<Loaded>(this.notFound, this.loadError),
          ),
        ),
        takeUntilDestroyed(inject(DestroyRef)),
      )
      .subscribe((loaded) => this.loaded.set(loaded));
  }

  protected retry(): void {
    this.reloads.next();
  }

  protected isSelf(winner: AwardWinner): boolean {
    return winner.player_id === this.selfId();
  }

  /** A new page size starts again at page 1. */
  protected changeSize(value: string): void {
    const size = Number(value);
    this.pageSize.set(size);
    this.query.next({ page: 1, size });
  }

  protected goTo(page: number): void {
    this.query.next({ page, size: this.query.value.size });
  }
}
