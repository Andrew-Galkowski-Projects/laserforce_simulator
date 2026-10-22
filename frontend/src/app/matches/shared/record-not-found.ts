import { Component, input } from '@angular/core';
import { MatAnchor } from '@angular/material/button';
import { RouterLink } from '@angular/router';

/** What a detail stub shows on a 404 (a missing Match / Round, or another Account's). */
@Component({
  selector: 'app-record-not-found',
  imports: [MatAnchor, RouterLink],
  template: `
    <h1>{{ noun() }} not found</h1>
    <p><a mat-stroked-button routerLink="/matches">&larr; Back to Matches</a></p>
  `,
})
export class RecordNotFound {
  /** "Match" or "Round". */
  readonly noun = input.required<string>();
}
