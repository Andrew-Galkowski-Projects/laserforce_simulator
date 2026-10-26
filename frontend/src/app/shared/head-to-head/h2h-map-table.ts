import { Component, input } from '@angular/core';
import { H2HMapRow } from '../../api/analytics.models';
import { fixed } from '../../players/shared/benchmark-format';

/** The "Per-map breakdown" section of Team and Player head-to-head; a null map is "No map (3-zone)". */
@Component({
  selector: 'app-h2h-map-table',
  template: `
    <h3>Per-map breakdown</h3>
    @if (rows().length > 0) {
      <div class="h2h-scroll">
        <table class="h2h-table h2h-map-table">
          <thead>
            <tr>
              <th>Map</th>
              <th>Games</th>
              <th>W</th>
              <th>L</th>
              <th>T</th>
              <th>Mean margin</th>
            </tr>
          </thead>
          <tbody>
            @for (row of rows(); track $index) {
              <tr>
                <td>{{ row.arena_map_name }}</td>
                <td>{{ row.games }}</td>
                <td>{{ row.wins }}</td>
                <td>{{ row.losses }}</td>
                <td>{{ row.ties }}</td>
                <td>{{ fixed(row.mean_margin, 1) }}</td>
              </tr>
            }
          </tbody>
        </table>
      </div>
    } @else {
      <p class="h2h-empty">No per-map data.</p>
    }
  `,
  styleUrl: './h2h-section.scss',
})
export class H2HMapTable {
  readonly rows = input.required<readonly H2HMapRow[]>();
  protected readonly fixed = fixed;
}
