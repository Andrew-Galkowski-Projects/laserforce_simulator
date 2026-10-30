import { Component, computed, inject, input } from '@angular/core';
import { PlayerRef } from '../../api/analytics.models';
import { WatchState } from './watch-state';

/**
 * `<app-watch-flag [player] [watched]>`: the ★ toggle beside a Player's name on a League table
 * (spec #428 F4). `[watched]` is the row's seed; the shown state is `WatchState.watched`, so every
 * flag for one Player changes together. Clicking calls `WatchState.toggle`; the button is
 * disabled while that request is in flight. `app-player-cell` puts one before the name; the League
 * player page's h1 uses it directly (I4). Needs `LeagueLayout` (it provides `WatchState`).
 */
@Component({
  selector: 'app-watch-flag',
  template: `
    <button
      type="button"
      class="watch-flag"
      [class.watch-flag--on]="on()"
      [attr.aria-pressed]="on()"
      [attr.aria-label]="label()"
      [title]="label()"
      [disabled]="pending()"
      (click)="toggle()"
    >
      &#9733;
    </button>
  `,
  styleUrl: './watch-flag.scss',
})
export class WatchFlag {
  readonly player = input.required<PlayerRef>();
  readonly watched = input(false);

  private readonly state = inject(WatchState);
  protected readonly on = computed(() => this.state.watched(this.player().id, this.watched()));
  protected readonly pending = computed(() => this.state.pending(this.player().id));
  protected readonly label = computed(
    () => `${this.on() ? 'Stop watching' : 'Watch'} ${this.player().name}`,
  );

  protected toggle(): void {
    this.state.toggle(this.player().id, this.on());
  }
}
