import { Component, model } from '@angular/core';
import { MatButton } from '@angular/material/button';
import { MatFormField, MatLabel } from '@angular/material/form-field';
import { MatInput } from '@angular/material/input';
import { MatOption, MatSelect } from '@angular/material/select';
import { PhaseType } from '../../../api/leagues.models';
import { RrdeCombo, SeriesLength, TournamentFormat } from '../../../api/tournaments.models';
import { Choice } from '../../league-create/difficulty';
import { ComposerTournamentMode, PhaseRow, PhaseScheduleFormat, newPhaseRow } from '../phase-wire';

interface ModeChoice extends Choice<ComposerTournamentMode> {
  disabled: boolean;
}

type SeriesKey = 'finalSl' | 'semiSl' | 'quarterSl' | 'earlierSl';

/** The block's selects, labelled as `create_advanced.html`'s `aria-label`s and options (:221-349). */
const TYPE_CHOICES: readonly Choice<PhaseType>[] = [
  { value: 'round_robin', label: 'Round-robin' },
  { value: 'tournament', label: 'Tournament' },
  { value: 'member_night', label: 'Member night' },
];

const SCHEDULE_CHOICES: readonly Choice<PhaseScheduleFormat>[] = [
  { value: 'single_round_robin', label: 'Single round-robin' },
  { value: 'double_round_robin', label: 'Double round-robin' },
];

const MODE_CHOICES: readonly ModeChoice[] = [
  { value: 'standings', label: 'Season-ending (from standings)', disabled: false },
  { value: 'strength', label: 'Mid-season (by team strength)', disabled: false },
  { value: 'unseeded', label: 'Mid-season (random seed)', disabled: false },
  { value: 'random_draw', label: 'Mid-season (drawn pool) — coming soon', disabled: true },
];

const FORMAT_CHOICES: readonly Choice<TournamentFormat>[] = [
  { value: 'single_elimination', label: 'Single elimination' },
  { value: 'double_elimination', label: 'Double elimination' },
  { value: 'round_robin', label: 'Round robin' },
  { value: 'round_robin_double_elim', label: 'Round robin → Double elimination' },
  { value: 'swiss', label: 'Swiss' },
];

const SERIES_FIELDS: readonly Choice<SeriesKey>[] = [
  { value: 'finalSl', label: 'Final series length' },
  { value: 'semiSl', label: 'Semifinal series length' },
  { value: 'quarterSl', label: 'Quarterfinal series length' },
  { value: 'earlierSl', label: 'Earlier series length' },
];

const SERIES_CHOICES: readonly SeriesLength[] = [1, 3, 5];

const COMBO_CHOICES: readonly RrdeCombo[] = ['4/0', '4/2', '8/0', '8/4', '16/0', '16/8'];

/** A number input's value: an emptied one is null, as the template's `value === ""` check. */
function numberOrNull(event: Event): number | null {
  const raw = (event.target as HTMLInputElement).value;
  return raw === '' ? null : Number(raw);
}

/**
 * The Season phases composer (`create_advanced.html` :200-509): one block per phase, in order.
 * It edits `rows`; the parent serialises them with `phaseWire` on submit. A block keeps its
 * hidden sub-config values, as the template's hidden selects do.
 */
@Component({
  selector: 'app-phase-composer',
  imports: [MatButton, MatFormField, MatInput, MatLabel, MatOption, MatSelect],
  styleUrl: './phase-composer.scss',
  templateUrl: './phase-composer.html',
})
export class PhaseComposer {
  readonly rows = model.required<PhaseRow[]>();

  protected readonly typeChoices = TYPE_CHOICES;
  protected readonly scheduleChoices = SCHEDULE_CHOICES;
  protected readonly modeChoices = MODE_CHOICES;
  protected readonly formatChoices = FORMAT_CHOICES;
  protected readonly seriesFields = SERIES_FIELDS;
  protected readonly seriesChoices = SERIES_CHOICES;
  protected readonly comboChoices = COMBO_CHOICES;
  protected readonly numberOrNull = numberOrNull;

  /** `applyFormat()` (:369-384): series lengths for SE / DE / RR -> DE. */
  protected showSeries(row: PhaseRow): boolean {
    return row.format !== 'round_robin' && row.format !== 'swiss';
  }

  protected add(): void {
    this.rows.update((rows) => [...rows, newPhaseRow()]);
  }

  protected remove(index: number): void {
    this.rows.update((rows) => rows.filter((_, i) => i !== index));
  }

  protected set(index: number, patch: Partial<PhaseRow>): void {
    this.rows.update((rows) => rows.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  }

  protected setSeries(index: number, key: SeriesKey, value: SeriesLength): void {
    this.set(index, { [key]: value });
  }
}
