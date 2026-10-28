import { Component, input } from '@angular/core';

/**
 * Spec #409 I1: a target that isn't ported yet (ANG-06-15..20). It renders as a disabled
 * `<span>` with a "Soon" badge, never an `<a>`, so there is no dead link. The League nav, the
 * dashboard and History all use it.
 */
@Component({
  selector: 'app-soon-badge',
  template: `<span class="soon" aria-disabled="true"
    >{{ label() }} <span class="soon__badge">Soon</span></span
  >`,
  styles: `
    // ADR-0043: --lf-* tokens only.
    .soon {
      color: var(--lf-text-muted);
      cursor: default;
    }

    .soon__badge {
      margin-left: 0.15rem;
      padding: 0 0.35rem;
      border: 1px solid var(--lf-border);
      border-radius: var(--lf-radius);
      color: var(--lf-text-muted);
      font-size: 0.7rem;
      font-weight: 700;
      white-space: nowrap;
    }
  `,
})
export class SoonBadge {
  readonly label = input.required<string>();
}
