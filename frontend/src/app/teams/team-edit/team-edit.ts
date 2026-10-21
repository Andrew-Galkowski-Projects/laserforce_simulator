import { Component, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatSnackBar } from '@angular/material/snack-bar';
import { ActivatedRoute, Router } from '@angular/router';
import { switchMap } from 'rxjs';
import { ApiError } from '../../api/api-client';
import { Team } from '../../api/teams.models';
import { TeamsService } from '../../api/teams.service';
import { TeamNotFound } from '../shared/team-not-found';
import { TeamNameForm } from '../team-create/team-name-form';

/** `/teams/:id/edit`: rename a Team, then land on its detail page. */
@Component({
  selector: 'app-team-edit',
  imports: [TeamNameForm, TeamNotFound],
  styleUrl: '../team-create/team-page.scss',
  template: `
    @if (notFound()) {
      <app-team-not-found />
    } @else if (loadError(); as message) {
      <p role="alert">{{ message }}</p>
    } @else if (team(); as team) {
      <h1>Edit Team</h1>
      <p class="team-page__sub">{{ team.name }}</p>
      <app-team-name-form
        submitLabel="Save"
        [cancelLink]="'/teams/' + team.id"
        [initialName]="team.name"
        [busy]="busy()"
        [error]="error()"
        (submitted)="save(team.id, $event)"
      />
    }
  `,
})
export class TeamEdit {
  private readonly teams = inject(TeamsService);
  private readonly router = inject(Router);
  private readonly snackBar = inject(MatSnackBar);

  protected readonly team = signal<Team | null>(null);
  protected readonly notFound = signal(false);
  protected readonly loadError = signal<string | null>(null);
  protected readonly busy = signal(false);
  protected readonly error = signal<unknown>(null);

  constructor() {
    inject(ActivatedRoute)
      .paramMap.pipe(
        switchMap((params) => this.teams.get(Number(params.get('id')))),
        takeUntilDestroyed(),
      )
      .subscribe({
        next: (team) => this.team.set(team),
        error: (error: unknown) => {
          if (error instanceof ApiError && error.status === 404) {
            this.notFound.set(true);
          } else {
            this.loadError.set(error instanceof Error ? error.message : 'Could not load the Team.');
          }
        },
      });
  }

  protected save(id: number, name: string): void {
    this.busy.set(true);
    this.teams.update(id, { name }).subscribe({
      next: (team) => {
        this.snackBar.open(`Team "${team.name}" updated successfully!`, 'Close', {
          duration: 5000,
        });
        void this.router.navigate(['/teams', team.id]);
      },
      error: (error: unknown) => {
        this.busy.set(false);
        this.error.set(error);
      },
    });
  }
}
