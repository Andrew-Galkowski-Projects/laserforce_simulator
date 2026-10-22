import { Component } from '@angular/core';
import { RouterLink } from '@angular/router';

/** The state any `/teams/:id...` screen shows on a 404 (missing or another Account's Team). */
@Component({
  selector: 'app-team-not-found',
  imports: [RouterLink],
  template: `
    <h1>Team not found</h1>
    <p><a routerLink="/teams">&larr; Back to Teams</a></p>
  `,
})
export class TeamNotFound {}
