import { Component, inject, input, output, signal } from '@angular/core';
import { MatButton } from '@angular/material/button';
import { MatError } from '@angular/material/form-field';
import { TeamsService } from '../../api/teams.service';
import { TournamentDetail } from '../../api/tournaments.models';
import { TournamentsService } from '../../api/tournaments.service';
import { apiMessage } from '../../shared/api-message';
import { GENERIC_ERROR_MESSAGE } from '../../shared/form-errors';
import { classifyImportErrors } from '../../shared/import-errors';
import { injectNotify } from '../../shared/notify';
import { saveBlob } from '../../shared/save-blob';

/** The filename the browser saves the server's roster template CSV under. */
const TEMPLATE_FILENAME = 'roster_template.csv';

/**
 * The Tournament detail's "Import Participants (CSV)" card: a roster CSV whose new Teams join a
 * preset Tournament's field. The host shows it only in setup, for preset, when not embedded.
 */
@Component({
  selector: 'app-participant-import',
  imports: [MatButton, MatError],
  templateUrl: './participant-import.html',
  styleUrl: './participant-import.scss',
})
export class ParticipantImport {
  private readonly tournaments = inject(TournamentsService);
  private readonly teams = inject(TeamsService);
  private readonly notify = injectNotify();

  /** The Tournament the participants join. */
  readonly detail = input.required<TournamentDetail>();
  /** Emitted after a successful import, so the host reloads the detail. */
  readonly changed = output<void>();

  /** The CSV picked through the hidden input; Import needs one. */
  protected readonly file = signal<File | null>(null);
  /** True while the import request is in flight. */
  protected readonly busy = signal(false);
  /** A failed template download, or an import error no control owns. */
  protected readonly alertErrors = signal<string[]>([]);
  /** `csv_file` errors, shown on the file control. */
  protected readonly fileErrors = signal<string[]>([]);
  /** Row errors, formatted ("Row N (column): ..."), in server order. */
  protected readonly rowErrors = signal<string[]>([]);

  /** Fetches the server's template CSV (behind the JWT, so not a plain link) and saves it. */
  protected downloadTemplate(): void {
    this.alertErrors.set([]);
    this.teams.importTemplate().subscribe({
      next: (blob) => saveBlob(blob, TEMPLATE_FILENAME),
      error: (error: unknown) => this.alertErrors.set([apiMessage(error, GENERIC_ERROR_MESSAGE)]),
    });
  }

  /** The hidden input's `change`: keep the File and clear the input so the same name re-fires. */
  protected choose(event: Event): void {
    const input = event.target as HTMLInputElement;
    this.file.set(input.files?.[0] ?? null);
    input.value = '';
  }

  protected submit(): void {
    const file = this.file();
    if (!file || this.busy()) return;
    this.clearErrors();
    this.busy.set(true);
    const before = this.detail().participants.length;
    this.tournaments.importParticipants(this.detail().id, file).subscribe({
      next: (updated) => {
        this.busy.set(false);
        this.file.set(null);
        this.notify(`Imported ${updated.participants.length - before} participants.`);
        this.changed.emit();
      },
      error: (error: unknown) => {
        this.busy.set(false);
        const errors = classifyImportErrors(error);
        this.fileErrors.set(errors.file);
        this.rowErrors.set(errors.rows);
        this.alertErrors.set(errors.alert);
      },
    });
  }

  private clearErrors(): void {
    this.alertErrors.set([]);
    this.fileErrors.set([]);
    this.rowErrors.set([]);
  }
}
