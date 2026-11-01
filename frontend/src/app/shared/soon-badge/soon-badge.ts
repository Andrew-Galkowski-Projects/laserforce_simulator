import { Component, input } from '@angular/core';

/**
 * Spec #409 I1: a target that isn't ported yet (ANG-06-15..20). It renders as a disabled
 * `<span>` with a "Soon" badge, never an `<a>`, so there is no dead link. The League nav and the
 * Shell's Tools / Help menus (spec #460) use it.
 */
@Component({
  selector: 'app-soon-badge',
  template: `<span class="soon" aria-disabled="true"
    >{{ label() }} <span class="soon__badge">Soon</span></span
  >`,
  styleUrl: './soon-badge.scss',
})
export class SoonBadge {
  readonly label = input.required<string>();
}
