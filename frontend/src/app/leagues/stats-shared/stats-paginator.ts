import { Component, input, output } from '@angular/core';
import { MatButton } from '@angular/material/button';
import { STATS_PAGE_SIZES } from './stats-query';

/**
 * `<app-stats-paginator>`: Previous / "Page X of N" / Next in a `nav.stats-pager` (spec #417).
 * Hidden when `totalPages` is 1 or less.
 *
 * - `[page]`, `[totalPages]` (required): from the response's `meta.page` / `meta.total_pages`.
 * - `[label]`: the nav's `aria-label` (default "Pagination").
 * - `(pageChange)`: the page to go to. Feed it to `withPage` (`stats-query.ts`).
 */
@Component({
  selector: 'app-stats-paginator',
  imports: [MatButton],
  template: `
    @if (totalPages() > 1) {
      <nav class="stats-pager" [attr.aria-label]="label()">
        <button
          mat-stroked-button
          type="button"
          [disabled]="page() <= 1"
          (click)="pageChange.emit(page() - 1)"
        >
          Previous
        </button>
        <span class="stats-pager__page">Page {{ page() }} of {{ totalPages() }}</span>
        <button
          mat-stroked-button
          type="button"
          [disabled]="page() >= totalPages()"
          (click)="pageChange.emit(page() + 1)"
        >
          Next
        </button>
      </nav>
    }
  `,
  styleUrl: './stats-picker.scss',
})
export class StatsPaginator {
  readonly page = input.required<number>();
  readonly totalPages = input.required<number>();
  readonly label = input('Pagination');
  readonly pageChange = output<number>();
}

/**
 * `<app-per-page-picker>`: "Per page:" and a native `<select id="stats-per-page-select">` of
 * 10 / 25 / 50 / 100 (spec #417).
 *
 * - `[perPage]` (required): the `StatsQuery.perPage`.
 * - `(perPageChange)`: the new size. Feed it to `withPerPage` (`stats-query.ts`), which resets
 *   the page.
 */
@Component({
  selector: 'app-per-page-picker',
  host: { class: 'stats-picker' },
  template: `
    <label for="stats-per-page-select">Per page:</label>
    <select id="stats-per-page-select" (change)="pick($event)">
      @for (size of sizes; track size) {
        <option [value]="size" [selected]="size === perPage()">{{ size }}</option>
      }
    </select>
  `,
  styleUrl: './stats-picker.scss',
})
export class PerPagePicker {
  readonly perPage = input.required<number>();
  readonly perPageChange = output<number>();
  protected readonly sizes = STATS_PAGE_SIZES;

  protected pick(event: Event): void {
    this.perPageChange.emit(Number((event.target as HTMLSelectElement).value));
  }
}
