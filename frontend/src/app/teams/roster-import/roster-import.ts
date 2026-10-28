import { Component, inject, signal } from '@angular/core';
import { MatAnchor, MatButton } from '@angular/material/button';
import { MatError } from '@angular/material/form-field';
import { RouterLink } from '@angular/router';
import { RosterImportResult } from '../../api/teams.models';
import { TeamsService } from '../../api/teams.service';
import { CsvImport } from '../../shared/csv-import';

/** `/teams/import`: Roster import (LG-00b) from a CSV, with the result shown in place. */
@Component({
  selector: 'app-roster-import',
  imports: [MatAnchor, MatButton, MatError, RouterLink],
  templateUrl: './roster-import.html',
  styleUrls: ['../shared/team-page.scss', './roster-import.scss'],
})
export class RosterImport {
  private readonly teams = inject(TeamsService);

  /** The file, template download, busy flag and error lists. */
  protected readonly csv = new CsvImport();
  /** The last successful import; while set, the result panel replaces the form. */
  protected readonly result = signal<RosterImportResult | null>(null);

  protected submit(): void {
    this.csv.upload(
      (file) => this.teams.importRoster(file),
      (result) => this.result.set(result),
    );
  }

  /** Back to the empty form: no file, no errors (the hidden input is recreated by the swap). */
  protected importAnother(): void {
    this.result.set(null);
    this.csv.reset();
  }
}
