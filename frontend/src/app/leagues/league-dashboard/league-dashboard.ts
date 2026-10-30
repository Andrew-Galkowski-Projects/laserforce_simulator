import { Component, DestroyRef, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatAnchor, MatButton } from '@angular/material/button';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { Observable, Subject, combineLatest, finalize, startWith, switchMap, tap } from 'rxjs';
import { ApiError } from '../../api/api-client';
import {
  LeagueDashboard as Dashboard,
  SEASON_MODE_LABELS,
  SeasonMode,
} from '../../api/leagues.models';
import { LeaguesService } from '../../api/leagues.service';
import { SeasonsService } from '../../api/seasons.service';
import { apiMessage } from '../../shared/api-message';
import { countOf } from '../../shared/count-of';
import { GENERIC_ERROR_MESSAGE } from '../../shared/form-errors';
import { notFoundOrError } from '../../shared/not-found-or-error';
import { injectNotify } from '../../shared/notify';
import { DashboardCards, DashboardLink } from '../dashboard-cards/dashboard-cards';
import { LeagueDelete, canDeleteLeague } from '../league-delete';

/**
 * `/leagues/:id` (spec #409 F10): the League dashboard, ported from `leagues/dashboard.html`.
 * Its body cards are the shared `DashboardCards` (spec #444), also on the Season dashboard.
 */
@Component({
  selector: 'app-league-dashboard',
  imports: [DashboardCards, MatAnchor, MatButton, RouterLink],
  templateUrl: './league-dashboard.html',
  styleUrl: './league-dashboard.scss',
})
export class LeagueDashboard {
  private readonly leagues = inject(LeaguesService);
  private readonly seasons = inject(SeasonsService);
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);
  private readonly destroyRef = inject(DestroyRef);
  private readonly notify = injectNotify();

  protected readonly dash = signal<Dashboard | null>(null);
  protected readonly notFound = signal(false);
  protected readonly loadError = signal<string | null>(null);
  /** I3: Start Season / Start next Season in flight. */
  protected readonly writing = signal(false);
  /** I3: the last failed write's message; the dashboard stays as it was. */
  protected readonly writeError = signal<string | null>(null);
  protected readonly del = new LeagueDelete();
  protected readonly canDelete = canDeleteLeague;

  private readonly reloads = new Subject<void>();

  constructor() {
    combineLatest([this.route.paramMap, this.reloads.pipe(startWith(undefined))])
      .pipe(
        tap(() => {
          this.notFound.set(false);
          this.loadError.set(null);
        }),
        switchMap(([params]) =>
          this.leagues
            .dashboard(Number(params.get('id')))
            .pipe(notFoundOrError<Dashboard>(this.notFound, this.loadError)),
        ),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((loaded) => this.dash.set(loaded));
  }

  protected retry(): void {
    this.reloads.next();
  }

  /** Start Season: says how many Rounds were scheduled (`league_views.start_season`), reloads. */
  protected start(seasonId: number): void {
    this.write(this.seasons.start(seasonId), (started) => {
      const rounds = countOf(started.round_count_total, 'Round');
      this.notify(`${started.season.name} started — ${rounds} scheduled.`);
    });
  }

  /** Start next Season (UXP-18 auto-start), then reload; a fired Manager goes to the picker. */
  protected nextSeason(dash: Dashboard): void {
    this.write(
      this.leagues.nextSeason(dash.league.id),
      () => undefined,
      (failure) => {
        if (!(failure instanceof ApiError) || failure.errors[0]?.code !== 'manager_fired') {
          return false;
        }
        void this.router.navigate(['new-team'], { relativeTo: this.route });
        return true;
      },
    );
  }

  protected remove(dash: Dashboard): void {
    this.writeError.set(null);
    this.del.run(dash.league);
  }

  /**
   * I3: one write at a time. On success `done` runs and the dashboard reloads; a failure that
   * `handled` does not take shows in the error box, the data kept as it was.
   */
  private write<T>(
    request: Observable<T>,
    done: (result: T) => void,
    handled: (failure: unknown) => boolean = () => false,
  ): void {
    if (this.writing()) return;
    this.writing.set(true);
    this.writeError.set(null);
    this.del.error.set(null);
    request
      .pipe(
        finalize(() => this.writing.set(false)),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe({
        next: (result) => {
          done(result);
          this.reloads.next();
        },
        error: (failure: unknown) => {
          if (!handled(failure)) this.writeError.set(apiMessage(failure, GENERIC_ERROR_MESSAGE));
        },
      });
  }

  /** Member-night "View results": the displayed Season's Schedule, at its member nights. */
  protected resultsLink(dash: Dashboard): DashboardLink | null {
    const season = dash.displayed_season;
    if (season === null) return null;
    return {
      commands: ['/leagues', dash.league.id, 'seasons', season.id, 'schedule'],
      fragment: 'member-nights',
    };
  }

  protected modeLabel(mode: SeasonMode): string {
    return SEASON_MODE_LABELS[mode];
  }
}
