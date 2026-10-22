import { Component, input } from '@angular/core';
import { Result } from './result';

/** A Match or Round result as a chip: the winner's name, "Tie", or a muted "In progress". */
@Component({
  selector: 'app-result-chip',
  template: `<span class="result-chip" [class.result-chip--muted]="result().muted">{{
    result().label
  }}</span>`,
  styles: `
    .result-chip {
      display: inline-block;
      padding: 0.1rem 0.6rem;
      border: 1px solid var(--lf-accent);
      border-radius: 999px;
      background: color-mix(in srgb, var(--lf-accent) 15%, transparent);
      color: var(--lf-text);
      font-size: 0.85rem;
      line-height: 1.5;
      white-space: nowrap;
    }

    .result-chip--muted {
      border-color: var(--lf-border);
      background: none;
      color: var(--lf-text-muted);
      font-style: italic;
    }
  `,
})
export class ResultChip {
  readonly result = input.required<Result>();
}
