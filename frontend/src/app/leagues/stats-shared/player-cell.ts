import { Component, computed, inject, input } from '@angular/core';
import { RouterLink } from '@angular/router';
import { PlayerRef } from '../../api/analytics.models';
import { AuthService } from '../../auth/auth.service';
import { WatchFlag } from '../watch/watch-flag';
import { LeagueContext } from './league-context';

/**
 * `<app-player-cell>`: a League table row's Player name (spec #417 I4, spec #428 F5 / I4). Put it
 * inside the row's name cell:
 * `<td class="name"><app-player-cell [player]="row.player" [watched]="row.watched" /></td>`.
 *
 * - `[player]` (required): the row's `player` ref; the name links to the League player page
 *   `/leagues/:id/players/:playerId` (`:id` from `LeagueContext.leagueId`).
 * - `[watched]` (default false): the row's seed for the `app-watch-flag` ★ toggle before the name.
 * - The signed-in Account's Self-insert gets the "You" badge after the name.
 *
 * Its text reads "★ <name>" (plus " You"): the flag's ★ is the button's text.
 */
@Component({
  selector: 'app-player-cell',
  imports: [RouterLink, WatchFlag],
  template: `
    <app-watch-flag [player]="player()" [watched]="watched()" />&ngsp;
    <a [routerLink]="['/leagues', leagueId(), 'players', player().id]">{{ player().name }}</a>
    @if (isSelf()) {
      &ngsp;<span class="player-cell__you">You</span>
    }
  `,
  styleUrl: './player-cell.scss',
})
export class PlayerCell {
  readonly player = input.required<PlayerRef>();
  readonly watched = input(false);

  protected readonly leagueId = inject(LeagueContext).leagueId;
  private readonly auth = inject(AuthService);
  protected readonly isSelf = computed(
    () => this.player().id === (this.auth.currentUser()?.self_insert?.id ?? null),
  );
}
