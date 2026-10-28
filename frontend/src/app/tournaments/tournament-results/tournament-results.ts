import { Component, input } from '@angular/core';
import { TournamentDetail } from '../../api/tournaments.models';

/**
 * The Tournament detail's read-only results (ANG-06-13b-3): the Bracket(s), the Round robin
 * crosstable and the Standings, per format. A stub until that ticket lands.
 */
@Component({
  selector: 'app-tournament-results',
  template: '',
})
export class TournamentResults {
  readonly detail = input.required<TournamentDetail>();
}
