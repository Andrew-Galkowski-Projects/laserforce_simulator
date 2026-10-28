import { Component, DestroyRef, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatAnchor } from '@angular/material/button';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { Subject, combineLatest, startWith, switchMap } from 'rxjs';
import {
  TEAM_ASSEMBLY_LABELS,
  TOURNAMENT_STATE_LABELS,
  TournamentDetail as TournamentDetailData,
  TournamentFormat,
} from '../../api/tournaments.models';
import { TournamentsService } from '../../api/tournaments.service';
import { notFoundOrError } from '../../shared/not-found-or-error';
import { ParticipantImport } from '../participant-import/participant-import';
import { TournamentPlay } from '../tournament-play/tournament-play';
import { TournamentPool } from '../tournament-pool/tournament-pool';
import { TournamentResults } from '../tournament-results/tournament-results';
import { TournamentSetup } from '../tournament-setup/tournament-setup';

/** `_tournament_stage` values the stage badge names; any other stage reads "Setup", as in Django. */
const STAGE_LABELS: Readonly<Record<string, string>> = {
  seeding: 'Seeding stage',
  finals: 'Finals stage',
  swiss: 'Swiss stage',
  completed: 'Completed',
};

/** The formats whose stages the header badges: RR->DE and Swiss. */
const STAGED_FORMATS: readonly TournamentFormat[] = ['round_robin_double_elim', 'swiss'];

/**
 * `/tournaments/:id`: the Tournament detail host (ANG-06-13a). It loads the detail and shows the
 * header; each section is a child component that takes the detail and emits `changed` when it
 * wrote something, which reloads the detail.
 */
@Component({
  selector: 'app-tournament-detail',
  imports: [
    MatAnchor,
    ParticipantImport,
    RouterLink,
    TournamentPlay,
    TournamentPool,
    TournamentResults,
    TournamentSetup,
  ],
  templateUrl: './tournament-detail.html',
  styleUrl: './tournament-detail.scss',
})
export class TournamentDetail {
  protected readonly detail = signal<TournamentDetailData | null>(null);
  protected readonly notFound = signal(false);
  protected readonly loadError = signal<string | null>(null);
  protected readonly stateLabels = TOURNAMENT_STATE_LABELS;
  protected readonly assemblyLabels = TEAM_ASSEMBLY_LABELS;

  private readonly reloads = new Subject<void>();

  constructor() {
    const tournaments = inject(TournamentsService);
    combineLatest([inject(ActivatedRoute).paramMap, this.reloads.pipe(startWith(undefined))])
      .pipe(
        switchMap(([params]) =>
          tournaments
            .get(Number(params.get('id')))
            .pipe(notFoundOrError<TournamentDetailData>(this.notFound, this.loadError)),
        ),
        takeUntilDestroyed(inject(DestroyRef)),
      )
      .subscribe((loaded) => this.detail.set(loaded));
  }

  /** The RR->DE / Swiss stage badge's label, or null for a format without stages. */
  protected stageLabel(detail: TournamentDetailData): string | null {
    if (!STAGED_FORMATS.includes(detail.format)) {
      return null;
    }
    return STAGE_LABELS[detail.stage] ?? 'Setup';
  }

  /** `GET tournaments/{id}/` again: a child section's `changed` output calls this. */
  protected reload(): void {
    this.reloads.next();
  }
}
