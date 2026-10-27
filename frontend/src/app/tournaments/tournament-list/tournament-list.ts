import { DatePipe } from '@angular/common';
import { Component, DestroyRef, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatAnchor } from '@angular/material/button';
import { MatPaginator, PageEvent } from '@angular/material/paginator';
import { MatTableModule } from '@angular/material/table';
import { RouterLink } from '@angular/router';
import { Page } from '../../api/api-client';
import { TOURNAMENT_STATE_LABELS, Tournament } from '../../api/tournaments.models';
import { TournamentsService } from '../../api/tournaments.service';

/** Rows per page; a paginator appears only when there are more. */
const PAGE_SIZE = 20;

/**
 * `/tournaments`: every Tournament the Manager can see, newest first, in-League ones included
 * (as on the Django page). No filters and no sorting.
 */
@Component({
  selector: 'app-tournament-list',
  imports: [DatePipe, MatAnchor, MatPaginator, MatTableModule, RouterLink],
  templateUrl: './tournament-list.html',
  styleUrl: './tournament-list.scss',
})
export class TournamentList {
  private readonly tournaments = inject(TournamentsService);
  private readonly destroyRef = inject(DestroyRef);

  protected readonly page = signal<Page<Tournament> | null>(null);
  protected readonly columns = ['name', 'format', 'state', 'created'];
  constructor() {
    this.load();
  }

  protected stateLabel(tournament: Tournament): string {
    return TOURNAMENT_STATE_LABELS[tournament.state];
  }

  protected changePage(event: PageEvent): void {
    this.load(event.pageIndex + 1);
  }

  /** `page` is 1-based; the first page is fetched without one. */
  private load(page?: number): void {
    this.tournaments
      .list({ page, page_size: PAGE_SIZE, ordering: '-id' })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe((result) => this.page.set(result));
  }
}
