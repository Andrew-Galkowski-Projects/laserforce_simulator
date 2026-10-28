import { Component, input, output } from '@angular/core';
import { TournamentDetail } from '../../api/tournaments.models';

/**
 * The Tournament detail's play section (ANG-06-13b-2): Play Next Match and Play All with progress.
 * A stub until that ticket lands; it decides its own visibility.
 */
@Component({
  selector: 'app-tournament-play',
  template: '',
})
export class TournamentPlay {
  readonly detail = input.required<TournamentDetail>();
  /** Emitted after every Match played, so the host reloads the detail. */
  readonly changed = output<void>();
}
