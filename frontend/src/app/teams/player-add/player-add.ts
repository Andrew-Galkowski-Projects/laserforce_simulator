import { Component, inject, signal } from '@angular/core';
import { Router } from '@angular/router';
import { PlayerInput } from '../../api/players.models';
import { Team } from '../../api/teams.models';
import { TeamsService } from '../../api/teams.service';
import { injectNotify } from '../shared/notify';
import { PlayerForm } from '../shared/player-form';
import { loadRouteTeam } from '../shared/route-team';
import { TeamNotFound } from '../shared/team-not-found';

/** `/teams/:id/players/new`: add a Player to the Team, then land on its detail page. */
@Component({
  selector: 'app-player-add',
  imports: [PlayerForm, TeamNotFound],
  styleUrl: '../shared/team-page.scss',
  template: `
    @if (notFound()) {
      <app-team-not-found />
    } @else if (loadError(); as message) {
      <p role="alert">{{ message }}</p>
    } @else if (team(); as team) {
      <h1>Add Player to {{ team.name }}</h1>
      <app-player-form
        submitLabel="Add Player"
        [cancelLink]="'/teams/' + team.id"
        [busy]="busy()"
        [error]="error()"
        (submitted)="save(team, $event)"
      />
    }
  `,
})
export class PlayerAdd {
  private readonly teams = inject(TeamsService);
  private readonly router = inject(Router);
  private readonly notify = injectNotify();
  private readonly load = loadRouteTeam();

  protected readonly team = this.load.team;
  protected readonly notFound = this.load.notFound;
  protected readonly loadError = this.load.loadError;
  protected readonly busy = signal(false);
  protected readonly error = signal<unknown>(null);

  protected save(team: Team, input: PlayerInput): void {
    this.busy.set(true);
    this.teams.addPlayer(team.id, input).subscribe({
      next: (player) => {
        this.notify(`Player "${player.name}" added to ${team.name}!`);
        void this.router.navigate(['/teams', team.id]);
      },
      error: (error: unknown) => {
        this.busy.set(false);
        this.error.set(error);
      },
    });
  }
}
