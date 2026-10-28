import { NgTemplateOutlet } from '@angular/common';
import { Component, computed, input } from '@angular/core';
import {
  BracketGroup,
  BracketRound,
  BracketType,
  TournamentDetail,
} from '../../api/tournaments.models';
import { Bracket } from './bracket/bracket';
import { Crosstable } from './crosstable/crosstable';
import { NodeCard } from './node-card/node-card';
import { singleEliminationRoundName } from './round-names';
import { StandingsTable } from './standings-table/standings-table';

/** The elimination tree's sub-brackets, in render order, with their headings. */
const ELIMINATION_HEADINGS: readonly (readonly [BracketType, string])[] = [
  ['winners', 'Winners Bracket'],
  ['losers', 'Losers Bracket'],
  ['grand_final', 'Grand Final'],
];

/** One elimination sub-bracket to render under its heading. */
interface EliminationSection {
  heading: string;
  rounds: BracketRound[];
}

/**
 * The Tournament detail's read-only results (ANG-06-13b-3), laid out per format as the Django
 * page's branches do:
 * - single elimination: "Bracket", its columns named Final / Semifinals / Quarterfinals / Round N;
 * - double elimination: "Bracket" with Winners Bracket, Losers Bracket and Grand Final;
 * - Round robin: "Crosstable" and "Standings";
 * - RR->DE: those two (the Standings with the Cut column), then "Finals Bracket" once it exists;
 * - Swiss: "Swiss Rounds", a "Round N" group of node cards each, and "Standings".
 */
@Component({
  selector: 'app-tournament-results',
  imports: [Bracket, Crosstable, NgTemplateOutlet, NodeCard, StandingsTable],
  templateUrl: './tournament-results.html',
  styleUrl: './tournament-results.scss',
})
export class TournamentResults {
  readonly detail = input.required<TournamentDetail>();

  protected readonly singleEliminationRoundName = singleEliminationRoundName;

  private readonly groups = computed(
    () =>
      new Map<BracketType, BracketGroup>(this.detail().brackets.map((g) => [g.bracket_type, g])),
  );

  /** A single elimination's whole tree (the `winners` group). */
  protected readonly winnersRounds = computed(() => this.groups().get('winners')?.rounds ?? []);

  /** The double elimination (or RR->DE finals) sub-brackets that exist. */
  protected readonly eliminationSections = computed<EliminationSection[]>(() =>
    ELIMINATION_HEADINGS.flatMap(([type, heading]) => {
      const group = this.groups().get(type);
      return group ? [{ heading, rounds: group.rounds }] : [];
    }),
  );

  protected readonly swissRounds = computed(() => this.groups().get('swiss')?.rounds ?? []);
}
