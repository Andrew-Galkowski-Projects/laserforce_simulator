import {
  Component,
  DestroyRef,
  ElementRef,
  afterRenderEffect,
  inject,
  signal,
  viewChild,
} from '@angular/core';
import { takeUntilDestroyed, toSignal } from '@angular/core/rxjs-interop';
import { MatAnchor, MatButton } from '@angular/material/button';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { EMPTY, Subject, catchError, startWith, switchMap, tap } from 'rxjs';
import { SeasonSchedule as Schedule } from '../../../api/seasons.models';
import { SeasonsService } from '../../../api/seasons.service';
import { apiMessage } from '../../../shared/api-message';
import { GENERIC_ERROR_MESSAGE } from '../../../shared/form-errors';
import { SeasonBreadcrumb } from '../season-breadcrumb/season-breadcrumb';
import { SeasonContext } from '../season-context';

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
  private readonly context = inject(SeasonContext);
  protected readonly schedule = signal<Schedule | null>(null);
  protected readonly loadError = signal<string | null>(null);

  private readonly reloads = new Subject<void>();
  private readonly fragment = toSignal(inject(ActivatedRoute).fragment, { initialValue: null });
  private readonly memberNights = viewChild<ElementRef<HTMLElement>>('memberNights');

  constructor() {
    const seasons = inject(SeasonsService);
    this.reloads
      .pipe(
        startWith(undefined),
        tap(() => this.loadError.set(null)),
        switchMap(() =>
          seasons.schedule(this.context.seasonId() as number).pipe(
            catchError((failure: unknown) => {
              this.loadError.set(apiMessage(failure, GENERIC_ERROR_MESSAGE));
              return EMPTY;
            }),
          ),
        ),
        takeUntilDestroyed(inject(DestroyRef)),
      )
      .subscribe((loaded) => this.schedule.set(loaded));

    afterRenderEffect(() => {
      const section = this.memberNights();
      if (section && this.fragment() === MEMBER_NIGHTS) {
        section.nativeElement.scrollIntoView();
      }
    });
  }

  /** `/leagues/:id/seasons/:seasonId/<screen>`. */
  protected seasonPath(screen: string): (string | number | null)[] {
    return ['/leagues', this.context.leagueId(), 'seasons', this.context.seasonId(), screen];
  }

  /** I2: re-sends only the schedule GET. */
  protected retry(): void {
    this.reloads.next();
  }
}
