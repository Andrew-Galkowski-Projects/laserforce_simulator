import { Component, input, output } from '@angular/core';
import { StatRate } from '../../api/league-stats.models';

/** `player_stats.py` `_RATE_OPTIONS`: the `?rate=` values and their labels, Totals first. */
export const RATE_OPTIONS: readonly (readonly [StatRate, string])[] = [
  ['total', 'Totals'],
  ['per_game', 'Per Game'],
  ['per_10', 'Per 10 min'],
];

/**
 * `<app-rate-picker>`: the "Rate:" native `<select id="stats-rate-select">` (Player Stats, Watch
 * List). `[rate]` is the `StatsQuery.rate`; `(rateChange)` emits the new value for `withRate`
 * (`screen.pickRate($event)`). A screen's config takes `rates: RATE_OPTIONS.map(([v]) => v)`.
 */
@Component({
  selector: 'app-rate-picker',
  host: { class: 'stats-picker' },
  template: `
    <label for="stats-rate-select">Rate:</label>
    <select id="stats-rate-select" (change)="pick($event)">
      @for (option of options; track option[0]) {
        <option [value]="option[0]" [selected]="option[0] === rate()">{{ option[1] }}</option>
      }
    </select>
  `,
  styleUrl: './stats-picker.scss',
})
export class RatePicker {
  readonly rate = input<string | null>(null);
  readonly rateChange = output<StatRate>();
  protected readonly options = RATE_OPTIONS;

  protected pick(event: Event): void {
    this.rateChange.emit((event.target as HTMLSelectElement).value as StatRate);
  }
}
