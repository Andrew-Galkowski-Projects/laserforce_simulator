import { Component, computed, inject, input } from '@angular/core';
import { RouterLink } from '@angular/router';
import { PlayerRef } from '../../api/analytics.models';
import { AuthService } from '../../auth/auth.service';

/**
 * `<app-player-cell>`: a League stats row's Player name (spec #417 I4). Put it inside the row's
 * name cell: `<td class="name"><app-player-cell [player]="row.player" [watched]="row.watched" /></td>`.
 *
 * - `[player]` (required): the row's `player` ref; the name links to `/players/:id/stats`.
 * - `[watched]` (default false): shows the read-only watched marker (a star, title "Watched")
 *   before the name. It never toggles: the watch-flag toggle is ANG-06-16.
 * - The signed-in Account's Self-insert gets the "You" badge after the name.
 */
@Component({
  selector: 'app-player-cell',
  imports: [RouterLink],
  template: `
    @if (watched()) {
      <span class="player-cell__watched" title="Watched" aria-label="Watched">&#9733;</span>&ngsp;
    }
    <a [routerLink]="['/players', player().id, 'stats']">{{ player().name }}</a>
    @if (isSelf()) {
      &ngsp;<span class="player-cell__you">You</span>
    }
  `,
  styleUrl: './player-cell.scss',
})
export class PlayerCell {
  readonly player = input.required<PlayerRef>();
  readonly watched = input(false);

  private readonly auth = inject(AuthService);
  protected readonly isSelf = computed(
    () => this.player().id === (this.auth.currentUser()?.self_insert?.id ?? null),
  );
}
