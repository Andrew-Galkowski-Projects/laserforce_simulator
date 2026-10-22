import { DatePipe } from '@angular/common';
import { Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatAnchor } from '@angular/material/button';
import { MatPaginator, PageEvent } from '@angular/material/paginator';
import { RouterLink } from '@angular/router';
import { Page } from '../../api/api-client';
import { TeamListItem } from '../../api/teams.models';
import { TeamsService } from '../../api/teams.service';
import { benchPlayers, slottedPlayerIds } from '../shared/roster';

/** Teams per page; a paginator appears only when there are more. */
const PAGE_SIZE = 100;

/** What one Team card shows. */
interface TeamCard {
  team: TeamListItem;
  active: number;
  bench: number;
}

/** `/teams`: one card per Team the manager can see (Free Agents excluded by the API). */
@Component({
  selector: 'app-team-list',
  imports: [DatePipe, MatAnchor, MatPaginator, RouterLink],
  templateUrl: './team-list.html',
  styleUrl: './team-list.scss',
})
export class TeamList {
  private readonly teams = inject(TeamsService);
  private readonly destroyRef = inject(DestroyRef);

  protected readonly page = signal<Page<TeamListItem> | null>(null);

  protected readonly cards = computed<TeamCard[]>(() =>
    (this.page()?.items ?? []).map((team) => ({
      team,
      active: slottedPlayerIds(team).length,
      bench: benchPlayers(team, team.players).length,
    })),
  );

  constructor() {
    this.load();
  }

  protected changePage(event: PageEvent): void {
    this.load(event.pageIndex + 1);
  }

  /** `page` is 1-based; the first page is fetched without one. */
  private load(page?: number): void {
    this.teams
      .list({ page, page_size: PAGE_SIZE, ordering: 'name' })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe((page) => this.page.set(page));
  }
}
