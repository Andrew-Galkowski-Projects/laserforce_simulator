import { Component, DestroyRef, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatAnchor, MatButton } from '@angular/material/button';
import { RouterLink } from '@angular/router';
import { EMPTY, Subject, catchError, startWith, switchMap, tap } from 'rxjs';
import {
  SEASON_MODE_LABELS,
  SeasonDashboard as Dashboard,
  SeasonMode,
} from '../../../api/leagues.models';
import { SeasonsService } from '../../../api/seasons.service';
import { apiMessage } from '../../../shared/api-message';
import { GENERIC_ERROR_MESSAGE } from '../../../shared/form-errors';
import { DashboardCards } from '../../dashboard-cards/dashboard-cards';
import { SeasonContext } from '../season-context';

/**
 * `/leagues/:id/seasons/:seasonId` (spec #444): the Season dashboard, ported from
 * `seasons/dashboard.html`. Its body cards are the shared `DashboardCards`, with no managed Team
 * (Django passes none here). No play controls (ANG-06-19).
 */
@Component({
  selector: 'app-season-dashboard',
  imports: [DashboardCards, MatAnchor, MatButton, RouterLink],
  templateUrl: './season-dashboard.html',
  styleUrl: './season-dashboard.scss',
})
export class SeasonDashboard {
  private readonly seasonId = inject(SeasonContext).seasonId;
  protected readonly dash = signal<Dashboard | null>(null);
  protected readonly loadError = signal<string | null>(null);

  private readonly reloads = new Subject<void>();

  constructor() {
    const seasons = inject(SeasonsService);
    this.reloads
      .pipe(
        startWith(undefined),
        tap(() => this.loadError.set(null)),
        switchMap(() =>
          seasons.dashboard(this.seasonId() as number).pipe(
            catchError((failure: unknown) => {
              this.loadError.set(apiMessage(failure, GENERIC_ERROR_MESSAGE));
              return EMPTY;
            }),
          ),
        ),
        takeUntilDestroyed(inject(DestroyRef)),
      )
      .subscribe((loaded) => this.dash.set(loaded));
  }

  /** I2: re-sends only the dashboard GET. */
  protected retry(): void {
    this.reloads.next();
  }

  protected modeLabel(mode: SeasonMode): string {
    return SEASON_MODE_LABELS[mode];
  }

  /** `/leagues/:id/seasons/:seasonId/<screen>`. */
  protected seasonPath(d: Dashboard, screen: string): (string | number)[] {
    return ['/leagues', d.league.id, 'seasons', d.season.id, screen];
  }
}
