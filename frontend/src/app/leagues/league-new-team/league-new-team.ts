import { Component, DestroyRef, inject, signal } from '@angular/core';
import { takeUntilDestroyed, toSignal } from '@angular/core/rxjs-interop';
import { MatButton } from '@angular/material/button';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { Subject, combineLatest, map, startWith, switchMap, tap } from 'rxjs';
import { NewTeamCandidates } from '../../api/leagues.models';
import { LeaguesService } from '../../api/leagues.service';
import { apiMessage } from '../../shared/api-message';
import { GENERIC_ERROR_MESSAGE } from '../../shared/form-errors';
import { notFoundOrError } from '../../shared/not-found-or-error';

/**
 * `/leagues/:id/new-team` (spec #409 F12, `leagues/new_team.html`): a fired career Manager picks
 * one of the eligible teams (first row checked) and takes it over, landing on the dashboard. A
 * 409 (not fired, no completed Season, not a career League) shows its message with a link back.
 */
@Component({
  selector: 'app-league-new-team',
  imports: [MatButton, RouterLink],
  templateUrl: './league-new-team.html',
  styleUrl: './league-new-team.scss',
})
export class LeagueNewTeam {
  private readonly leagues = inject(LeaguesService);
  private readonly router = inject(Router);
  private readonly destroyRef = inject(DestroyRef);
  private readonly reloads = new Subject<void>();

  protected readonly loaded = signal<NewTeamCandidates | null>(null);
  protected readonly notFound = signal(false);
  protected readonly loadError = signal<string | null>(null);
  /** The checked radio's team id. */
  protected readonly chosen = signal<number | null>(null);
  protected readonly busy = signal(false);
  protected readonly submitError = signal<string | null>(null);

  // `:id` is on the parent `LeagueLayout` route.
  private readonly route = inject(ActivatedRoute);
  protected readonly leagueId = toSignal(
    (this.route.parent ?? this.route).paramMap.pipe(map((p) => Number(p.get('id')))),
    { initialValue: 0 },
  );

  constructor() {
    combineLatest([
      (this.route.parent ?? this.route).paramMap,
      this.reloads.pipe(startWith(undefined)),
    ])
      .pipe(
        tap(() => {
          this.notFound.set(false);
          this.loadError.set(null);
        }),
        switchMap(([params]) =>
          this.leagues
            .newTeamCandidates(Number(params.get('id')))
            .pipe(notFoundOrError<NewTeamCandidates>(this.notFound, this.loadError)),
        ),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((loaded) => {
        this.loaded.set(loaded);
        this.chosen.set(loaded.candidates[0]?.team_id ?? null);
      });
  }

  protected retry(): void {
    this.reloads.next();
  }

  protected takeOver(): void {
    const teamId = this.chosen();
    if (teamId === null) return;
    const id = this.leagueId();
    this.busy.set(true);
    this.submitError.set(null);
    this.leagues
      .reassignTeam(id, teamId)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: () => {
          this.busy.set(false);
          void this.router.navigate(['/leagues', id]);
        },
        error: (error: unknown) => {
          this.busy.set(false);
          this.submitError.set(apiMessage(error, GENERIC_ERROR_MESSAGE));
        },
      });
  }
}
