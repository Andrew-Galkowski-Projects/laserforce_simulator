import { Component, input, output } from '@angular/core';
import { TournamentDetail } from '../../api/tournaments.models';

/**
 * The Tournament detail's setup section (ANG-06-13b-1): Seeding, Lock & Build Bracket, and the
 * Player-pool draw. A stub until that ticket lands; it decides its own visibility.
 */
@Component({
  selector: 'app-tournament-setup',
  template: '',
})
export class TournamentSetup {
  readonly detail = input.required<TournamentDetail>();
  /** Emitted after every successful write, so the host reloads the detail. */
  readonly changed = output<void>();
}
