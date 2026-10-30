import { Component, DestroyRef, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatAnchor, MatButton } from '@angular/material/button';
import { Router, RouterLink } from '@angular/router';
import { finalize } from 'rxjs';
import { ApiError } from '../../../api/api-client';
import { LeaguesService } from '../../../api/leagues.service';
import { OwnerEvaluation as Evaluation } from '../../../api/seasons.models';
import { SeasonsService } from '../../../api/seasons.service';
import { fixed } from '../../../players/shared/benchmark-format';
import { apiMessage } from '../../../shared/api-message';
import { GENERIC_ERROR_MESSAGE } from '../../../shared/form-errors';
import { SeasonBreadcrumb } from '../season-breadcrumb/season-breadcrumb';
import { SeasonContext } from '../season-context';
import { injectSeasonLoad } from '../season-load';

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
  protected readonly context = inject(SeasonContext);
  private readonly leagues = inject(LeaguesService);
  private readonly seasons = inject(SeasonsService);
  private readonly router = inject(Router);
  private readonly destroyRef = inject(DestroyRef);

  private readonly load = injectSeasonLoad((id) => this.seasons.ownerEvaluation(id), {
    splitNotFound: true,
  });
  protected readonly evaluation = this.load.data;
  protected readonly notFound = this.load.notFound;
  protected readonly loadError = this.load.loadError;
  /** I3: Start Next Season in flight. */
  protected readonly writing = signal(false);
  /** I3: the last failed Start Next Season's message; the evaluation stays as it was. */
  protected readonly writeError = signal<string | null>(null);

  protected readonly fixed = fixed;

  /** I2: re-sends only the evaluation GET. */
  protected retry(): void {
    this.load.retry();
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
    const leagueId = this.context.requireLeagueId();
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
