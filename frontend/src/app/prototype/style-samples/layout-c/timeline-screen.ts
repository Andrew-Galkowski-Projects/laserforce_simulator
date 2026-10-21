import { Component, inject } from '@angular/core';
import { MatCardModule } from '@angular/material/card';
import { MatListModule } from '@angular/material/list';
import { SAMPLE_FIXTURE, tickToClock } from '../fixture/sample-fixture';

/** Layout C Timeline: a static `mat-list`, one line per event, a dot per event type. */
@Component({
  selector: 'app-lc-timeline',
  imports: [MatCardModule, MatListModule],
  template: `
    <h1 class="lc-h1">Timeline</h1>
    <p class="lc-muted">{{ fixture.events.length }} events around the first elimination</p>
    <mat-card appearance="outlined" class="lc-timeline-card">
      <mat-list class="lc-timeline">
        @for (ev of fixture.events; track $index) {
          <mat-list-item class="lc-ev">
            <span
              matListItemIcon
              class="lc-ev__dot"
              [style.--ev]="'var(--lf-ev-' + ev.type + ')'"
              [title]="ev.type"
            ></span>
            <span matListItemTitle class="lc-ev__title">
              <span class="lc-ev__actor" [attr.data-lf-side]="ev.actorSide"
                ><span class="lc-side-tag lc-side-tag--sm">{{
                  ev.actorSide === 'red' ? 'R' : 'B'
                }}</span>
                {{ ev.actorName }}</span
              >
              @if (ev.targetName) {
                <span class="lc-ev__arrow" aria-label="to">&rarr;</span>
                <span class="lc-ev__actor" [attr.data-lf-side]="ev.targetSide"
                  ><span class="lc-side-tag lc-side-tag--sm">{{
                    ev.targetSide === 'red' ? 'R' : 'B'
                  }}</span>
                  {{ ev.targetName }}</span
                >
              }
            </span>
            <span matListItemLine class="lc-ev__meta">
              <span class="lc-num">{{ clock(ev.tick) }}</span> &middot;
              <span class="lc-ev__type" [style.--ev]="'var(--lf-ev-' + ev.type + ')'">{{
                ev.type
              }}</span>
              &middot; {{ ev.description }}
            </span>
            <span matListItemMeta class="lc-ev__pts lc-num">
              @if (ev.points) {
                {{ ev.points > 0 ? '+' : '' }}{{ ev.points }}
              }
            </span>
          </mat-list-item>
        }
      </mat-list>
    </mat-card>
  `,
})
export class TimelineScreenC {
  protected readonly fixture = inject(SAMPLE_FIXTURE);
  protected readonly clock = tickToClock;
}
