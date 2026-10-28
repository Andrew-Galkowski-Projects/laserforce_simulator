import { Component, inject, input, output } from '@angular/core';
import { MatButton } from '@angular/material/button';
import { MatError } from '@angular/material/form-field';
import { TournamentDetail } from '../../api/tournaments.models';
import { TournamentsService } from '../../api/tournaments.service';
import { countOf } from '../../shared/count-of';
import { CsvImport } from '../../shared/csv-import';
import { injectNotify } from '../../shared/notify';

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
  private readonly notify = injectNotify();

  /** The Tournament the participants join. */
  readonly detail = input.required<TournamentDetail>();
  /** Emitted after a successful import, so the host reloads the detail. */
  readonly changed = output<void>();

  /** The file, template download, busy flag and error lists. */
  protected readonly csv = new CsvImport();

  protected submit(): void {
    const { id, participants } = this.detail();
    this.csv.upload(
      (file) => this.tournaments.importParticipants(id, file),
      (updated) => {
        const added = updated.participants.length - participants.length;
        this.notify(`Imported ${countOf(added, 'participant')}.`);
        this.changed.emit();
      },
    );
  }
}
