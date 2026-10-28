import { Component, computed, inject, input, output, signal } from '@angular/core';
import { FormControl, FormRecord, ReactiveFormsModule } from '@angular/forms';
import { MatButton } from '@angular/material/button';
import { MatDialog } from '@angular/material/dialog';
import { MatError, MatFormField } from '@angular/material/form-field';
import { MatInput } from '@angular/material/input';
import { RouterLink } from '@angular/router';
import { filter } from 'rxjs';
import { TournamentDetail } from '../../api/tournaments.models';
import { TournamentsService } from '../../api/tournaments.service';
import { apiMessage } from '../../shared/api-message';
import { ConfirmDialog, ConfirmDialogData } from '../../shared/confirm-dialog';
import {
  GENERIC_ERROR_MESSAGE,
  REQUIRED_MESSAGE,
  applyApiErrors,
  clearServerErrors,
} from '../../shared/form-errors';
import { injectNotify } from '../../shared/notify';

/** A seed input's control name: `seed_<team id>`, as the Django form names it. */
const seedKey = (teamId: number): string => `seed_${teamId}`;

const LOCK_DIALOG: ConfirmDialogData = {
  title: 'Lock & Build Bracket',
  message:
    'Locking builds the Bracket and starts the Tournament. The Seeding and the Player pool ' +
    'can no longer be changed.',
  confirm: 'Lock',
};

/**
 * The Tournament detail's setup section (ANG-06-13b-1): the Seeding form (preset Tournaments) and
 * Lock & Build Bracket. Both show only in setup and outside a League; it decides its own
 * visibility. Every successful write emits `changed`, so the host reloads the detail.
 */
@Component({
  selector: 'app-tournament-setup',
  imports: [MatButton, MatError, MatFormField, MatInput, ReactiveFormsModule, RouterLink],
  templateUrl: './tournament-setup.html',
  styleUrl: './tournament-setup.scss',
})
export class TournamentSetup {
  private readonly tournaments = inject(TournamentsService);
  private readonly dialog = inject(MatDialog);
  private readonly notify = injectNotify();

  readonly detail = input.required<TournamentDetail>();
  /** Emitted after every successful write, so the host reloads the detail. */
  readonly changed = output<void>();

  /** Setup controls: in setup, and not run by a League. */
  protected readonly editable = computed(
    () => this.detail().state === 'setup' && !this.detail().embedded,
  );

  protected readonly showSeeding = computed(
    () =>
      this.editable() &&
      this.detail().team_assembly === 'preset' &&
      this.detail().participants.length > 0,
  );

  /**
   * One `seed_<team id>` control per participant, rebuilt from each loaded detail. The rows keep
   * the detail's order: the server sends participants by seed.
   */
  protected readonly seedForm = computed(() => {
    const controls: Record<string, FormControl<number | null>> = {};
    for (const participant of this.detail().participants) {
      controls[seedKey(participant.team.id)] = new FormControl<number | null>(participant.seed);
    }
    return new FormRecord(controls);
  });

  protected readonly seedKey = seedKey;
  protected readonly requiredMessage = REQUIRED_MESSAGE;
  protected readonly saving = signal(false);
  protected readonly seedErrors = signal<string[]>([]);

  protected saveSeeds(): void {
    const form = this.seedForm();
    if (this.saving()) return;
    this.seedErrors.set([]);
    clearServerErrors(form);
    if (form.invalid) {
      form.markAllAsTouched();
      return;
    }
    const seeds: Record<number, number> = {};
    for (const participant of this.detail().participants) {
      seeds[participant.team.id] = form.controls[seedKey(participant.team.id)].value as number;
    }
    this.saving.set(true);
    this.tournaments.saveSeeds(this.detail().id, seeds).subscribe({
      next: () => {
        this.saving.set(false);
        this.notify('Seeding saved.');
        this.changed.emit();
      },
      error: (error: unknown) => {
        this.saving.set(false);
        this.seedErrors.set(applyApiErrors(form, error));
      },
    });
  }

  protected readonly locking = signal(false);
  protected readonly lockError = signal<string | null>(null);

  /** Asks first; Cancel sends nothing. */
  protected lock(): void {
    if (this.locking()) return;
    this.dialog
      .open<ConfirmDialog, ConfirmDialogData, boolean>(ConfirmDialog, { data: LOCK_DIALOG })
      .afterClosed()
      .pipe(filter((yes) => yes === true))
      .subscribe(() => this.sendLock());
  }

  private sendLock(): void {
    this.locking.set(true);
    this.lockError.set(null);
    this.tournaments.lock(this.detail().id).subscribe({
      next: () => {
        this.locking.set(false);
        this.notify('Bracket locked.');
        this.changed.emit();
      },
      error: (error: unknown) => {
        this.locking.set(false);
        this.lockError.set(apiMessage(error, GENERIC_ERROR_MESSAGE));
      },
    });
  }
}
