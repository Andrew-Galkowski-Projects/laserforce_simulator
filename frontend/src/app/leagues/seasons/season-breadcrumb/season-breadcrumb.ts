import { Component, inject } from '@angular/core';
import { RouterLink } from '@angular/router';
import { SeasonContext } from '../season-context';

/**
 * `<League> › <Season>` above a Season sub-screen's h1 (spec #444): the League name links to
 * `/leagues/:id`, the Season name to the Season dashboard. Reads the layout's `SeasonContext`,
 * so it renders nothing outside a loaded Season.
 */
@Component({
  selector: 'app-season-breadcrumb',
  imports: [RouterLink],
  template: `
    @if (context.season(); as season) {
      <nav class="season-breadcrumb" aria-label="Breadcrumb">
        <a [routerLink]="['/leagues', season.league.id]">{{ season.league.name }}</a>
        <span class="season-breadcrumb__sep" aria-hidden="true">{{ ' › ' }}</span>
        <a [routerLink]="context.link()">{{ season.name }}</a>
      </nav>
    }
  `,
  styles: `
    // ADR-0043: --lf-* tokens only.
    .season-breadcrumb {
      margin-bottom: 0.5rem;
      color: var(--lf-text-muted);
      font-size: 0.875rem;
      overflow-wrap: anywhere;
    }

    a {
      color: var(--lf-accent);
    }

    .season-breadcrumb__sep {
      white-space: pre;
    }
  `,
})
export class SeasonBreadcrumb {
  protected readonly context = inject(SeasonContext);
}
