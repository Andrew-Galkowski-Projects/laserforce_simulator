import { Component, inject, signal } from '@angular/core';
import { MatAnchor, MatButton } from '@angular/material/button';
import { MatError } from '@angular/material/form-field';
import { RouterLink } from '@angular/router';
import { RosterImportResult } from '../../api/teams.models';
import { TeamsService } from '../../api/teams.service';
import { saveBlob } from '../../shared/save-blob';
import { GENERIC_ERROR_MESSAGE } from '../../shared/form-errors';
import { classifyImportErrors } from './import-errors';

/** The filename the browser saves the server's template CSV under. */
const TEMPLATE_FILENAME = 'roster_template.csv';

function messageOf(error: unknown): string {
  return error instanceof Error && error.message ? error.message : GENERIC_ERROR_MESSAGE;
}

/** `/teams/import`: Roster import (LG-00b) from a CSV, with the result shown in place. */
@Component({
  selector: 'app-roster-import',
  imports: [MatAnchor, MatButton, MatError, RouterLink],
  templateUrl: './roster-import.html',
  styleUrls: ['../shared/team-page.scss', './roster-import.scss'],
})
export class RosterImport {
  private readonly teams = inject(TeamsService);

  /** The CSV picked through the hidden input; Import needs one. */
  protected readonly file = signal<File | null>(null);
  /** True while the import request is in flight. */
  protected readonly busy = signal(false);
  /** The last successful import; while set, the result panel replaces the form. */
  protected readonly result = signal<RosterImportResult | null>(null);
  /** Messages for the alert line: a failed template download or an unowned import error. */
  protected readonly alertErrors = signal<string[]>([]);
  /** `csv_file` errors, shown on the file control. */
  protected readonly fileErrors = signal<string[]>([]);
  /** Row errors, formatted ("Row N (column): ..."), listed under the picker in server order. */
  protected readonly rowErrors = signal<string[]>([]);

  /** Fetches the server's template CSV (behind the JWT, so not a plain link) and saves it. */
  protected downloadTemplate(): void {
    this.alertErrors.set([]);
    this.teams.importTemplate().subscribe({
      next: (blob) => saveBlob(blob, TEMPLATE_FILENAME),
      error: (error: unknown) => this.alertErrors.set([messageOf(error)]),
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
    this.teams.importRoster(file).subscribe({
      next: (result) => {
        this.busy.set(false);
        this.result.set(result);
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

  /** Back to the empty form: no file, no errors (the hidden input is recreated by the swap). */
  protected importAnother(): void {
    this.result.set(null);
    this.file.set(null);
    this.clearErrors();
  }

  private clearErrors(): void {
    this.alertErrors.set([]);
    this.fileErrors.set([]);
    this.rowErrors.set([]);
  }
}
