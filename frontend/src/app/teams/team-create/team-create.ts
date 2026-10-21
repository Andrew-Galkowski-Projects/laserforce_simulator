import { Component, inject, signal } from '@angular/core';
import { MatSnackBar } from '@angular/material/snack-bar';
import { Router } from '@angular/router';
import { TeamsService } from '../../api/teams.service';
import { TeamNameForm } from './team-name-form';

/** `/teams/new`: create a Team, then land on its detail page. */
@Component({
  selector: 'app-team-create',
  imports: [TeamNameForm],
  styleUrl: './team-page.scss',
  template: `
    <h1>Create Team</h1>
    <app-team-name-form
      submitLabel="Create Team"
      cancelLink="/teams"
      [busy]="busy()"
      [error]="error()"
      (submitted)="create($event)"
    />
  `,
})
export class TeamCreate {
  private readonly teams = inject(TeamsService);
  private readonly router = inject(Router);
  private readonly snackBar = inject(MatSnackBar);

  protected readonly busy = signal(false);
  protected readonly error = signal<unknown>(null);

  protected create(name: string): void {
    this.busy.set(true);
    this.teams.create({ name }).subscribe({
      next: (team) => {
        this.snackBar.open(`Team "${team.name}" created successfully!`, 'Close', {
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
