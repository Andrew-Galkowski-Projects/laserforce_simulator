import { Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatAnchor, MatButton } from '@angular/material/button';
import { ActivatedRoute, RouterLink, RouterOutlet } from '@angular/router';
import { Subject, combineLatest, distinctUntilChanged, map, startWith, switchMap, tap } from 'rxjs';
import { Season } from '../../../api/leagues.models';
import { SeasonsService } from '../../../api/seasons.service';
import { notFoundOrError } from '../../../shared/not-found-or-error';
import { SeasonContext } from '../season-context';

/**
 * `/leagues/:id/seasons/:seasonId/...` (spec #444): loads the Season once per `:seasonId` and
 * renders the routed Season screen only once it has loaded and belongs to the route's League.
 * A 404, or another League's Season, shows "Season not found" (I1); any other failure the error
 * box with Retry, which re-sends only the Season GET (I2).
 */
@Component({
  selector: 'app-season-layout',
  imports: [MatAnchor, MatButton, RouterLink, RouterOutlet],
  templateUrl: './season-layout.html',
  styleUrl: './season-layout.scss',
  providers: [SeasonContext],
})
export class SeasonLayout {
  private readonly context = inject(SeasonContext);
  protected readonly leagueId = this.context.leagueId;
  protected readonly notFound = signal(false);
  protected readonly loadError = signal<string | null>(null);
  /** I1: the loaded Season belongs to another League than the route's `:id`. */
  protected readonly mismatch = computed(() => {
    const season = this.context.season();
    return season !== null && season.league.id !== this.leagueId();
  });
  /** The Season has loaded and is this League's: the screen may render. */
  protected readonly ready = computed(() => this.context.season() !== null && !this.mismatch());

  private readonly reloads = new Subject<void>();

  constructor() {
    const seasons = inject(SeasonsService);
    const seasonIds = inject(ActivatedRoute).paramMap.pipe(
      map((params) => Number(params.get('seasonId'))),
      distinctUntilChanged(),
    );
    combineLatest([seasonIds, this.reloads.pipe(startWith(undefined))])
      .pipe(
        tap(([seasonId]) => {
          this.context.seasonId.set(seasonId);
          this.context.season.set(null);
          this.notFound.set(false);
          this.loadError.set(null);
        }),
        switchMap(([seasonId]) =>
          seasons.get(seasonId).pipe(notFoundOrError<Season>(this.notFound, this.loadError)),
        ),
        takeUntilDestroyed(inject(DestroyRef)),
      )
      .subscribe((loaded) => this.context.season.set(loaded));
  }

  protected retry(): void {
    this.reloads.next();
  }
}
