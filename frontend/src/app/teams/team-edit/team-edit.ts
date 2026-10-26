import { Component, inject, signal } from '@angular/core';
import { Router } from '@angular/router';
import { TeamsService } from '../../api/teams.service';
import { injectNotify } from '../../shared/notify';
import { loadRouteTeam } from '../shared/route-team';
import { TeamNameForm } from '../shared/team-name-form';
import { TeamNotFound } from '../shared/team-not-found';

/** `/teams/:id/edit`: rename a Team, then land on its detail page. */
@Component({
  selector: 'app-team-edit',
  imports: [TeamNameForm, TeamNotFound],
  styleUrl: '../shared/team-page.scss',
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
  private readonly notify = injectNotify();
  private readonly load = loadRouteTeam();

  protected readonly team = this.load.team;
  protected readonly notFound = this.load.notFound;
  protected readonly loadError = this.load.loadError;
  protected readonly busy = signal(false);
  protected readonly error = signal<unknown>(null);

  protected save(id: number, name: string): void {
    this.busy.set(true);
    this.teams.update(id, { name }).subscribe({
      next: (team) => {
        this.notify(`Team "${team.name}" updated successfully!`);
        void this.router.navigate(['/teams', team.id]);
      },
      error: (error: unknown) => {
        this.busy.set(false);
        this.error.set(error);
      },
    });
  }
}
