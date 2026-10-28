import { Component, DestroyRef, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatAnchor, MatButton } from '@angular/material/button';
import { MatTableModule } from '@angular/material/table';
import { RouterLink } from '@angular/router';
import { forkJoin } from 'rxjs';
import { LEAGUE_STATE_LABELS, League, LeagueState } from '../../api/leagues.models';
import { LeaguesService } from '../../api/leagues.service';
import { listAll } from '../../api/list-all';
import { apiMessage } from '../../shared/api-message';
import { GENERIC_ERROR_MESSAGE } from '../../shared/form-errors';
import { LeagueDelete, canDeleteLeague } from '../league-delete';

/** One table of the list: Active, then Archived, as on the Django page. */
interface LeagueGroup {
  state: LeagueState;
  heading: string;
  leagues: League[];
}

/**
 * `/leagues` (spec #409 F7): every active and every archived League the Manager owns, in two
 * tables (a table with no Leagues is left out), with Delete on career Leagues.
 */
@Component({
  selector: 'app-league-list',
  imports: [MatAnchor, MatButton, MatTableModule, RouterLink],
  templateUrl: './league-list.html',
  styleUrl: './league-list.scss',
})
export class LeagueList {
  private readonly leagues = inject(LeaguesService);
  private readonly destroyRef = inject(DestroyRef);

  /** Null until both lists arrive. */
  protected readonly groups = signal<LeagueGroup[] | null>(null);
  protected readonly loadError = signal<string | null>(null);
  protected readonly columns = ['name', 'state'];
  protected readonly canDelete = canDeleteLeague;
  protected readonly del = new LeagueDelete();

  constructor() {
    this.load();
  }

  protected load(): void {
    this.loadError.set(null);
    forkJoin([this.all('active'), this.all('archived')])
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: ([active, archived]) =>
          this.groups.set([
            { state: 'active', heading: 'Active', leagues: active },
            { state: 'archived', heading: 'Archived', leagues: archived },
          ]),
        error: (error: unknown) => {
          this.groups.set(null);
          this.loadError.set(apiMessage(error, GENERIC_ERROR_MESSAGE));
        },
      });
  }

  protected stateLabel(league: League): string {
    return LEAGUE_STATE_LABELS[league.state];
  }

  protected remove(league: League): void {
    this.del.run(league, () => this.load());
  }

  protected isEmpty(groups: LeagueGroup[]): boolean {
    return groups.every((group) => group.leagues.length === 0);
  }

  private all(state: LeagueState) {
    return listAll((page) => this.leagues.list({ ...page, state }));
  }
}
