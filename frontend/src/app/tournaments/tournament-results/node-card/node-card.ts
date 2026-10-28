import { Component, input } from '@angular/core';
import { RouterLink } from '@angular/router';
import { BracketNode } from '../../../api/tournaments.models';

/**
 * One Bracket node card (`_tournament_round.html`): both seeds and Teams, then "Bye", or the
 * Series (BoN, the Series score, one line per played Match), and the winner.
 */
@Component({
  selector: 'app-node-card',
  imports: [RouterLink],
  templateUrl: './node-card.html',
  styleUrl: './node-card.scss',
})
export class NodeCard {
  readonly node = input.required<BracketNode>();
}
