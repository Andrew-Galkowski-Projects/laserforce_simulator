import { Component, input, output } from '@angular/core';
import { TeamRef } from '../../api/analytics.models';

/**
 * `<app-team-picker>`: the League stats Team picker (spec #417), a labelled native
 * `<select id="stats-team-select">` ("Team:").
 *
 * - `[options]` (required): the response's `meta.team_options` (already by name).
 * - `[selected]`: the `StatsQuery.team` (null: All).
 * - `[allLabel]` (default "All Teams"): Game Log passes "All teams".
 * - `(teamChange)`: a Team id, or null for All. Feed it to `withTeam` (`stats-query.ts`).
 *
 * It sits in a `.stats-filters` row (`_stats-page.scss`), which gives it room (I5).
 */
@Component({
  selector: 'app-team-picker',
  host: { class: 'stats-picker stats-picker--wide' },
  template: `
    <label for="stats-team-select">Team:</label>
    <select id="stats-team-select" (change)="pick($event)">
      <option value="" [selected]="selected() === null">{{ allLabel() }}</option>
      @for (team of options(); track team.id) {
        <option [value]="team.id" [selected]="team.id === selected()">{{ team.name }}</option>
      }
    </select>
  `,
  styleUrl: './stats-picker.scss',
})
export class TeamPicker {
  readonly options = input.required<TeamRef[]>();
  readonly selected = input<number | null>(null);
  readonly allLabel = input('All Teams');
  readonly teamChange = output<number | null>();

  protected pick(event: Event): void {
    const value = (event.target as HTMLSelectElement).value;
    this.teamChange.emit(value === '' ? null : Number(value));
  }
}
