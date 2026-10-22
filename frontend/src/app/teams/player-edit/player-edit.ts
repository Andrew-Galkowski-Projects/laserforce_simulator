import { Component, inject, signal } from '@angular/core';
import { Router } from '@angular/router';
import { Player, PlayerInput } from '../../api/players.models';
import { PlayersService } from '../../api/players.service';
import { injectNotify } from '../shared/notify';
import { PlayerForm } from '../shared/player-form';
import { PlayerNotFound } from '../shared/player-not-found';
import { loadRoutePlayer } from '../shared/route-player';

/**
 * `/teams/:id/players/:playerId/edit`: edit a Player, then land on its Team's detail page.
 * A 404 (a missing or another Account's Player) or a Player on another Team is "Player not found".
 */
@Component({
  selector: 'app-player-edit',
  imports: [PlayerForm, PlayerNotFound],
  styleUrl: '../shared/team-page.scss',
  template: `
    @if (notFound()) {
      <app-player-not-found [teamId]="teamId()" />
    } @else if (loadError(); as message) {
      <p role="alert">{{ message }}</p>
    } @else if (player(); as player) {
      <h1>Edit {{ player.name }}</h1>
      <app-player-form
        submitLabel="Save Changes"
        [cancelLink]="'/teams/' + teamId()"
        [initial]="player"
        [busy]="busy()"
        [error]="error()"
        (submitted)="save(player, $event)"
      />
    }
  `,
})
export class PlayerEdit {
  private readonly players = inject(PlayersService);
  private readonly router = inject(Router);
  private readonly notify = injectNotify();
  private readonly load = loadRoutePlayer();

  protected readonly teamId = this.load.teamId;
  protected readonly player = this.load.player;
  protected readonly notFound = this.load.notFound;
  protected readonly loadError = this.load.loadError;
  protected readonly busy = signal(false);
  protected readonly error = signal<unknown>(null);

  protected save(player: Player, input: PlayerInput): void {
    this.busy.set(true);
    this.players.update(player.id, input).subscribe({
      next: (saved) => {
        this.notify(`Player "${saved.name}" updated!`);
        void this.router.navigate(['/teams', this.teamId()]);
      },
      error: (error: unknown) => {
        this.busy.set(false);
        this.error.set(error);
      },
    });
  }
}
