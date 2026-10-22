import { DatePipe, NgTemplateOutlet } from '@angular/common';
import { Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatAnchor } from '@angular/material/button';
import { MatPaginator, PageEvent } from '@angular/material/paginator';
import { MatTableModule } from '@angular/material/table';
import { RouterLink } from '@angular/router';
import { Page } from '../../api/api-client';
import { Match } from '../../api/matches.models';
import { MatchesService } from '../../api/matches.service';
import { GameRoundListItem } from '../../api/rounds.models';
import { RoundsService } from '../../api/rounds.service';
import { matchResult, roundResult } from '../shared/result';
import { ResultChip } from '../shared/result-chip';
import { LinkRow } from './link-row';

/** Rows per page in each section; a paginator appears only when there are more. */
const PAGE_SIZE = 20;

/** `/matches`: Match History, a Matches section and a Single Rounds section, paged apart. */
@Component({
  selector: 'app-match-list',
  imports: [
    DatePipe,
    LinkRow,
    MatAnchor,
    MatPaginator,
    MatTableModule,
    NgTemplateOutlet,
    ResultChip,
    RouterLink,
  ],
  templateUrl: './match-list.html',
  styleUrl: './match-list.scss',
})
export class MatchList {
  private readonly matches = inject(MatchesService);
  private readonly rounds = inject(RoundsService);
  private readonly destroyRef = inject(DestroyRef);

  protected readonly matchPage = signal<Page<Match> | null>(null);
  protected readonly roundPage = signal<Page<GameRoundListItem> | null>(null);

  /** Both sections loaded and both empty: one centred empty state replaces them. */
  protected readonly nothingPlayed = computed(
    () => this.matchPage()?.meta.count === 0 && this.roundPage()?.meta.count === 0,
  );

  protected readonly matchColumns = ['teams', 'winner', 'score', 'rounds', 'date'];
  protected readonly matchResult = matchResult;
  protected readonly roundColumns = ['teams', 'winner', 'score', 'date'];
  protected readonly roundResult = roundResult;

  constructor() {
    this.loadMatches();
    this.loadRounds();
  }

  protected pageMatches(event: PageEvent): void {
    this.loadMatches(event.pageIndex + 1);
  }

  protected pageRounds(event: PageEvent): void {
    this.loadRounds(event.pageIndex + 1);
  }

  /** `page` is 1-based; the first page is fetched without one. The server orders newest first. */
  private loadMatches(page?: number): void {
    this.matches
      .list({ page, page_size: PAGE_SIZE })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe((result) => this.matchPage.set(result));
  }

  private loadRounds(page?: number): void {
    this.rounds
      .list({ standalone: true, page, page_size: PAGE_SIZE })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe((result) => this.roundPage.set(result));
  }
}
