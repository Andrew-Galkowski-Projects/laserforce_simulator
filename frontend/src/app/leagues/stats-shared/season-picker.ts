import { Component, computed, input, output } from '@angular/core';
import { SeasonOption, SeasonParam, SeasonScope } from '../../api/league-stats.models';

/**
 * `<app-season-picker>`: the League stats Season picker (spec #417), a labelled native
 * `<select id="stats-season-select">` ("Season:").
 *
 * - `[scope]` (required): the response's `meta.season_scope`. Its options list newest first as
 *   "name (year)", or the name alone when `year` is null; `scope.selected` is the chosen option.
 * - `[career]` (default true): add the trailing "Career" option. Playoffs passes false.
 * - `(seasonChange)`: the chosen Season id (a number), `'career'`, or null for an empty value.
 *   Feed it to `withSeason` (`stats-query.ts`), which also clears `team` and resets the page.
 *
 * It sits in a `.stats-filters` row (`_stats-page.scss`), which gives it room (I5).
 */
@Component({
  selector: 'app-season-picker',
  host: { class: 'stats-picker stats-picker--wide' },
  template: `
    <label for="stats-season-select">Season:</label>
    <select id="stats-season-select" (change)="pick($event)">
      @for (option of scope().options; track option.id) {
        <option [value]="option.id" [selected]="option.id === selectedValue()">
          {{ label(option) }}
        </option>
      }
      @if (career()) {
        <option value="career" [selected]="selectedValue() === 'career'">Career</option>
      }
    </select>
  `,
  styleUrl: './stats-picker.scss',
})
export class SeasonPicker {
  readonly scope = input.required<SeasonScope>();
  readonly career = input(true);
  readonly seasonChange = output<SeasonParam | null>();

  protected readonly selectedValue = computed<number | 'career' | null>(() => {
    const selected = this.scope().selected;
    return selected === 'career' ? 'career' : (selected?.id ?? null);
  });

  protected label(option: SeasonOption): string {
    return option.year === null ? option.name : `${option.name} (${option.year})`;
  }

  protected pick(event: Event): void {
    const value = (event.target as HTMLSelectElement).value;
    if (value === 'career') this.seasonChange.emit('career');
    else this.seasonChange.emit(value === '' ? null : Number(value));
  }
}
