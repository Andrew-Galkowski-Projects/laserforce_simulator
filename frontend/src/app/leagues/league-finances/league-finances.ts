import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed, toObservable } from '@angular/core/rxjs-interop';
import { MatButton } from '@angular/material/button';
import { Observable, catchError, combineLatest, map, of, startWith, switchMap } from 'rxjs';
import { LeagueFinanceRow } from '../../api/league-finances.models';
import { LeagueFinancesService } from '../../api/league-finances.service';
import { SeasonRef } from '../../api/tournaments.models';
import { apiMessage } from '../../shared/api-message';
import { GENERIC_ERROR_MESSAGE } from '../../shared/form-errors';
import { moneyK } from '../../shared/money-k';
import { LeagueContext } from '../stats-shared/league-context';

/** What the screen shows. */
type View =
  /** The nav or the finances are still loading. */
  | { kind: 'loading' }
  /** I1: finances are off (from the nav: no request is sent; or from the response). */
  | { kind: 'disabled' }
  /** I1: the League has no displayed Season (no request is sent). */
  | { kind: 'noSeason' }
  /** I2: the load failed; Retry re-sends the request. */
  | { kind: 'error'; message: string }
  | { kind: 'loaded'; season: SeasonRef; rows: LeagueFinanceRow[] };

/** The request the nav calls for, or the state that replaces it. */
type Request = { leagueId: number; season: SeasonRef } | View;

/**
 * `/leagues/:id/finances` (spec #439 League Finances, `leagues/league_finances.html`): every
 * enrolled Team's Revenue, Profit, Cash and Payroll in the displayed Season, money as `moneyK`.
 * A Team without a settled snapshot shows `—` for Revenue, Profit and Payroll. States in I1 order:
 * the nav loading, finances off, no Season (neither sends a request), then the load.
 */
@Component({
  selector: 'app-league-finances',
  imports: [MatButton],
  templateUrl: './league-finances.html',
  styleUrl: './league-finances.scss',
})
export class LeagueFinances {
  private readonly finances = inject(LeagueFinancesService);
  private readonly context = inject(LeagueContext);
  protected readonly nav = this.context.nav;
  protected readonly moneyK = moneyK;

  private readonly reloads = signal(0);

  private readonly request = computed((): Request => {
    const nav = this.nav();
    const leagueId = this.context.leagueId();
    if (nav === null || leagueId === null) return { kind: 'loading' };
    if (!nav.finance_enabled) return { kind: 'disabled' };
    if (nav.displayed_season === null) return { kind: 'noSeason' };
    return { leagueId, season: nav.displayed_season };
  });

  protected readonly view = signal<View>({ kind: 'loading' });

  constructor() {
    combineLatest([toObservable(this.request), toObservable(this.reloads)])
      .pipe(
        switchMap(([req]) => ('kind' in req ? of(req) : this.load(req.leagueId, req.season))),
        takeUntilDestroyed(),
      )
      .subscribe((view) => this.view.set(view));
  }

  private load(leagueId: number, season: SeasonRef): Observable<View> {
    return this.finances.league(leagueId).pipe(
      map((data): View =>
        data.finance_enabled ? { kind: 'loaded', season, rows: data.rows } : { kind: 'disabled' },
      ),
      catchError((error: unknown) =>
        of<View>({ kind: 'error', message: apiMessage(error, GENERIC_ERROR_MESSAGE) }),
      ),
      startWith<View>({ kind: 'loading' }),
    );
  }

  protected retry(): void {
    this.reloads.update((n) => n + 1);
  }
}
