import { Component, DestroyRef, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatAnchor, MatButton } from '@angular/material/button';
import { Router, RouterLink } from '@angular/router';
import { Subject, finalize, startWith, switchMap, tap } from 'rxjs';
import { ApiError } from '../../../api/api-client';
import { LeaguesService } from '../../../api/leagues.service';
import { OwnerEvaluation as Evaluation } from '../../../api/seasons.models';
import { SeasonsService } from '../../../api/seasons.service';
import { fixed } from '../../../players/shared/benchmark-format';
import { apiMessage } from '../../../shared/api-message';
import { GENERIC_ERROR_MESSAGE } from '../../../shared/form-errors';
import { notFoundOrError } from '../../../shared/not-found-or-error';
import { SeasonBreadcrumb } from '../season-breadcrumb/season-breadcrumb';
import { SeasonContext } from '../season-context';

/**
 * `/leagues/:id/seasons/:seasonId/owner-evaluation` (spec #444): the Owner's Annual Performance
 * Evaluation, ported from `seasons/owner_evaluation.html`, with "Start Next Season" (or "Choose
 * New Team" for a fired Manager not yet reassigned). The `OwnerEvaluation` model shares this
 * class's name, so it is imported as `Evaluation`.
 */
@Component({
  selector: 'app-owner-evaluation',
  imports: [MatAnchor, MatButton, RouterLink, SeasonBreadcrumb],
  templateUrl: './owner-evaluation.html',
  styleUrl: './owner-evaluation.scss',
})
export class OwnerEvaluation {
  private readonly ctx = inject(SeasonContext);
  private readonly leagues = inject(LeaguesService);
  private readonly router = inject(Router);
  private readonly destroyRef = inject(DestroyRef);

  protected readonly evaluation = signal<Evaluation | null>(null);
  protected readonly notFound = signal(false);
  protected readonly loadError = signal<string | null>(null);
  /** I3: Start Next Season in flight. */
  protected readonly writing = signal(false);
  /** I3: the last failed Start Next Season's message; the evaluation stays as it was. */
  protected readonly writeError = signal<string | null>(null);

  protected readonly leagueId = this.ctx.leagueId;
  protected readonly seasonId = this.ctx.seasonId;
  protected readonly fixed = fixed;

  private readonly reloads = new Subject<void>();

  constructor() {
    const seasons = inject(SeasonsService);
    this.reloads
      .pipe(
        startWith(undefined),
        tap(() => {
          this.notFound.set(false);
          this.loadError.set(null);
        }),
        switchMap(() =>
          seasons
            .ownerEvaluation(this.seasonId() as number)
            .pipe(notFoundOrError<Evaluation>(this.notFound, this.loadError)),
        ),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((loaded) => this.evaluation.set(loaded));
  }

  /** I2: re-sends only the evaluation GET. */
  protected retry(): void {
    this.reloads.next();
  }

  /** Django's `text-uppercase` badge: `hot_seat` shows as `HOT_SEAT`. */
  protected verdictLabel(e: Evaluation): string {
    return e.verdict.toUpperCase();
  }

  /**
   * Start Next Season (I3: one at a time): a 201 opens the new Season's dashboard; a 409
   * `manager_fired` goes to the Team picker; any other failure shows its message.
   */
  protected startNextSeason(): void {
    if (this.writing()) return;
    const leagueId = this.leagueId() as number;
    this.writing.set(true);
    this.writeError.set(null);
    this.leagues
      .nextSeason(leagueId)
      .pipe(
        finalize(() => this.writing.set(false)),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe({
        next: (started) => void this.router.navigate(['/leagues', leagueId, 'seasons', started.id]),
        error: (failure: unknown) => {
          if (failure instanceof ApiError && failure.errors[0]?.code === 'manager_fired') {
            void this.router.navigate(['/leagues', leagueId, 'new-team']);
          } else {
            this.writeError.set(apiMessage(failure, GENERIC_ERROR_MESSAGE));
          }
        },
      });
  }
}
