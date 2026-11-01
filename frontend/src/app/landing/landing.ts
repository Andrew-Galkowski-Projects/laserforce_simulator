import { Component, DestroyRef, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatButton } from '@angular/material/button';
import { RouterLink } from '@angular/router';
import { APP_TITLE } from '../app-title';
import { listAll } from '../api/list-all';
import { LEAGUE_STATE_LABELS, League } from '../api/leagues.models';
import { LeaguesService } from '../api/leagues.service';
import { apiMessage } from '../shared/api-message';
import { GENERIC_ERROR_MESSAGE } from '../shared/form-errors';

/**
 * `/` (spec #460, ports `core/landing.html`): the mode picker, then an In Progress card per
 * active League (every page, newest first), hidden when there is none. A failed load shows the
 * error and Retry; the mode cards render regardless.
 */
@Component({
  imports: [MatButton, RouterLink],
  selector: 'app-landing',
  styleUrl: './landing.scss',
  templateUrl: './landing.html',
})
export class Landing {
  private readonly leagues = inject(LeaguesService);
  private readonly destroyRef = inject(DestroyRef);

  protected readonly title = APP_TITLE;
  protected readonly inProgress = signal<League[]>([]);
  protected readonly loadError = signal<string | null>(null);

  constructor() {
    this.load();
  }

  protected load(): void {
    this.loadError.set(null);
    listAll((page) => this.leagues.list({ ...page, state: 'active' }))
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (leagues) => this.inProgress.set(leagues),
        error: (error: unknown) => {
          this.inProgress.set([]);
          this.loadError.set(apiMessage(error, GENERIC_ERROR_MESSAGE));
        },
      });
  }

  protected stateLabel(league: League): string {
    return LEAGUE_STATE_LABELS[league.state];
  }
}
