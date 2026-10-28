// The CSV import mechanics the roster-CSV screens share (Import Roster, a Tournament's Import
// Participants and its pool's Import players): the picked file, the template download, the busy
// flag and the three error lists. Each screen keeps its own template, upload call and success
// message.
import { inject, signal } from '@angular/core';
import { Observable } from 'rxjs';
import { TeamsService } from '../api/teams.service';
import { apiMessage } from './api-message';
import { GENERIC_ERROR_MESSAGE } from './form-errors';
import { classifyImportErrors } from './import-errors';
import { saveBlob } from './save-blob';

/** The filename the browser saves the server's roster template CSV under. */
export const TEMPLATE_FILENAME = 'roster_template.csv';

/**
 * One import card's state. Create it in an injection context (a component field):
 * `protected readonly csv = new CsvImport();`.
 */
export class CsvImport {
  private readonly teams = inject(TeamsService);

  /** The CSV picked through the hidden input; the import needs one. */
  readonly file = signal<File | null>(null);
  /** True while the upload is in flight. */
  readonly busy = signal(false);
  /** The alert line: a failed template download, or an import error no control owns. */
  readonly alertErrors = signal<string[]>([]);
  /** `csv_file` errors, shown on the file control. */
  readonly fileErrors = signal<string[]>([]);
  /** Row errors, formatted ("Row N (column): ..."), in server order. */
  readonly rowErrors = signal<string[]>([]);

  /** The hidden input's `change`: keep the File and clear the input so the same name re-fires. */
  choose(event: Event): void {
    const input = event.target as HTMLInputElement;
    this.file.set(input.files?.[0] ?? null);
    input.value = '';
  }

  /** Fetches the server's template CSV (behind the JWT, so not a plain link) and saves it. */
  downloadTemplate(): void {
    this.alertErrors.set([]);
    this.teams.importTemplate().subscribe({
      next: (blob) => saveBlob(blob, TEMPLATE_FILENAME),
      error: (error: unknown) => this.alertErrors.set([apiMessage(error, GENERIC_ERROR_MESSAGE)]),
    });
  }

  /**
   * Sends the picked file through `send` (nothing without a file, or while one is in flight).
   * A success clears the file and hands the result to `done`; a failure sorts its errors into
   * the three lists and keeps the file.
   */
  upload<T>(send: (file: File) => Observable<T>, done: (result: T) => void): void {
    const file = this.file();
    if (!file || this.busy()) return;
    this.clearErrors();
    this.busy.set(true);
    send(file).subscribe({
      next: (result) => {
        this.busy.set(false);
        this.file.set(null);
        done(result);
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

  /** Back to the empty card: no file, no errors. */
  reset(): void {
    this.file.set(null);
    this.clearErrors();
  }

  private clearErrors(): void {
    this.alertErrors.set([]);
    this.fileErrors.set([]);
    this.rowErrors.set([]);
  }
}
