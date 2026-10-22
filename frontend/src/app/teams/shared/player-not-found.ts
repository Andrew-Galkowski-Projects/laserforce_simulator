import { Component, input } from '@angular/core';
import { RouterLink } from '@angular/router';

/**
 * The state a `/teams/:id/players/:playerId...` screen shows on a 404 (a missing or another
 * Account's Player) or for a Player on another Team.
 */
@Component({
  selector: 'app-player-not-found',
  imports: [RouterLink],
  template: `
    <h1>Player not found</h1>
    <p><a [routerLink]="['/teams', teamId()]">&larr; Back to Team</a></p>
  `,
})
export class PlayerNotFound {
  /** The route's `:id`: the Team to go back to. */
  readonly teamId = input.required<number>();
}
