import { Component, ElementRef, afterRenderEffect, inject, viewChild } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { MatAnchor, MatButton } from '@angular/material/button';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { SeasonsService } from '../../../api/seasons.service';
import { SeasonBreadcrumb } from '../season-breadcrumb/season-breadcrumb';
import { SeasonContext } from '../season-context';
import { injectSeasonLoad } from '../season-load';

/** The route fragment the dashboards' member-night "View results" link carries. */
const MEMBER_NIGHTS = 'member-nights';

/**
 * `/leagues/:id/seasons/:seasonId/schedule` (spec #444), ported from `seasons/schedule.html`:
 * each matchday's fixtures with their results, then the member-night games. The app has no
 * router anchor scrolling, so arriving with `#member-nights` scrolls that section into view here,
 * once it has rendered.
 */
@Component({
  selector: 'app-season-schedule',
  imports: [MatAnchor, MatButton, RouterLink, SeasonBreadcrumb],
  templateUrl: './season-schedule.html',
  styleUrl: './season-schedule.scss',
})
export class SeasonSchedule {
  protected readonly context = inject(SeasonContext);
  private readonly seasons = inject(SeasonsService);
  private readonly load = injectSeasonLoad((id) => this.seasons.schedule(id));
  protected readonly schedule = this.load.data;
  protected readonly loadError = this.load.loadError;

  private readonly fragment = toSignal(inject(ActivatedRoute).fragment, { initialValue: null });
  private readonly memberNights = viewChild<ElementRef<HTMLElement>>('memberNights');

  constructor() {
    afterRenderEffect(() => {
      const section = this.memberNights();
      if (section && this.fragment() === MEMBER_NIGHTS) {
        section.nativeElement.scrollIntoView();
      }
    });
  }

  /** I2: re-sends only the schedule GET. */
  protected retry(): void {
    this.load.retry();
  }
}
