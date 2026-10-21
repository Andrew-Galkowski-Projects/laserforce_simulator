import { Component, ViewEncapsulation, inject, input } from '@angular/core';
import { MatTabsModule } from '@angular/material/tabs';
import { MatToolbarModule } from '@angular/material/toolbar';
import { RouterLink } from '@angular/router';
import { SAMPLE_FIXTURE, SAMPLE_SCREENS, SampleScreen } from '../fixture/sample-fixture';
import { RoundScreenC } from './round-screen';
import { TeamsScreenC } from './teams-screen';
import { TimelineScreenC } from './timeline-screen';

const SCREEN_LABELS: Record<SampleScreen, string> = {
  teams: 'Teams',
  round: 'Round detail',
  timeline: 'Timeline',
};

/**
 * Layout C -- Clean Material. Stock Angular Material 3: a toolbar shell with a tab nav bar,
 * cards, `mat-table` + `matSort`, and a `mat-list` timeline. Encapsulation is off so the one
 * stylesheet (scoped under `.lc-shell`) can style the sub-components and Material internals.
 */
@Component({
  selector: 'app-layout-c',
  imports: [
    MatToolbarModule,
    MatTabsModule,
    RouterLink,
    TeamsScreenC,
    RoundScreenC,
    TimelineScreenC,
  ],
  templateUrl: './layout-c.html',
  styleUrl: './layout-c.scss',
  encapsulation: ViewEncapsulation.None,
})
export class LayoutC {
  readonly screen = input<SampleScreen>('round');

  protected readonly fixture = inject(SAMPLE_FIXTURE);
  protected readonly screens = SAMPLE_SCREENS;
  protected readonly labels = SCREEN_LABELS;
}
