import { Component, input } from '@angular/core';
import { BracketRound } from '../../../api/tournaments.models';
import { NodeCard } from '../node-card/node-card';
import { RoundNamer, roundNumberName } from '../round-names';

/**
 * One sub-bracket as columns of Bracket rounds, each a column of node cards. The columns scroll
 * sideways inside the bracket, so a wide tree never widens the page.
 */
@Component({
  selector: 'app-bracket',
  imports: [NodeCard],
  templateUrl: './bracket.html',
  styleUrl: './bracket.scss',
})
export class Bracket {
  readonly rounds = input.required<BracketRound[]>();
  /** Names each column; "Round N" unless the group says otherwise. */
  readonly roundName = input<RoundNamer>(roundNumberName);
  /**
   * The column headings' level: 4 under a sub-bracket's h3 (Winners / Losers / Grand Final), 3
   * when the bracket sits straight under an h2 (single elimination), so no level is skipped.
   */
  readonly headingLevel = input<3 | 4>(4);
}
