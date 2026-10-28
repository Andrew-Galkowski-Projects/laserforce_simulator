// Spec #409 F6: the one League delete flow, shared by the Leagues list and the dashboard.
import { inject, signal } from '@angular/core';
import { MatDialog } from '@angular/material/dialog';
import { Router } from '@angular/router';
import { filter, finalize, switchMap } from 'rxjs';
import { DeleteSummary, LeagueMode, LeagueRef } from '../api/leagues.models';
import { LeaguesService } from '../api/leagues.service';
import { apiMessage } from '../shared/api-message';
import { ConfirmDialog, ConfirmDialogData } from '../shared/confirm-dialog';
import { GENERIC_ERROR_MESSAGE } from '../shared/form-errors';
import { injectNotify } from '../shared/notify';

/** Delete is offered only on a career League (the server refuses the rest with a 409). */
export function canDeleteLeague(league: { mode: LeagueMode }): boolean {
  return league.mode === 'league';
}

/** The summary's five counts, in the Django confirm page's order. */
function summaryLines(summary: DeleteSummary): string[] {
  return [
    `Seasons: ${summary.seasons}`,
    `Matches: ${summary.matches}`,
    `Tournaments: ${summary.tournaments}`,
    `Teams: ${summary.teams}`,
    `Players: ${summary.players}`,
  ];
}

/**
 * The League delete flow. Create it as a component field (it injects); bind the Delete button's
 * `disabled` to `busy()` and show `error()` in the screen's error box.
 *
 * `run` fetches the delete summary, asks in a `ConfirmDialog` with the five counts, and on
 * "Delete permanently" sends the DELETE, says `League "<name>" deleted.` and navigates to
 * `/leagues`. Cancel sends nothing.
 */
export class LeagueDelete {
  private readonly leagues = inject(LeaguesService);
  private readonly dialog = inject(MatDialog);
  private readonly router = inject(Router);
  private readonly notify = injectNotify();

  /** True from the click until the flow ends (cancelled, failed or deleted). */
  readonly busy = signal(false);
  /** The last failure's message (I3), cleared when a new run starts. */
  readonly error = signal<string | null>(null);

  /** `deleted` runs after the navigation, e.g. for the list to reload itself. */
  run(league: LeagueRef, deleted?: () => void): void {
    if (this.busy()) return;
    this.busy.set(true);
    this.error.set(null);
    this.leagues
      .deleteSummary(league.id)
      .pipe(
        switchMap((summary) => {
          const data: ConfirmDialogData = {
            title: `Delete "${league.name}"?`,
            message:
              'This action is permanent and cannot be undone. Deleting this League will ' +
              'remove all of the data it owns. This will permanently delete:',
            details: summaryLines(summary),
            confirm: 'Delete permanently',
          };
          return this.dialog
            .open<ConfirmDialog, ConfirmDialogData, boolean>(ConfirmDialog, { data })
            .afterClosed();
        }),
        filter((yes) => yes === true),
        switchMap(() => this.leagues.delete(league.id)),
        finalize(() => this.busy.set(false)),
      )
      .subscribe({
        next: () => {
          this.notify(`League "${league.name}" deleted.`);
          void this.router.navigate(['/leagues']).then(() => deleted?.());
        },
        error: (failure: unknown) => this.error.set(apiMessage(failure, GENERIC_ERROR_MESSAGE)),
      });
  }
}
