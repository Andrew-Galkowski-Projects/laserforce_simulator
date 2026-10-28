/**
 * Names a Bracket round column: `index` is its place among the `count` columns, `bracketRound`
 * its `bracket_round`.
 */
export type RoundNamer = (index: number, count: number, bracketRound: number) => string;

/** Every group but a single elimination's: "Round N" (`_tournament_round.html`). */
export const roundNumberName: RoundNamer = (_index, _count, bracketRound) =>
  `Round ${bracketRound}`;

const NAMES_FROM_END = ['Final', 'Semifinals', 'Quarterfinals'];

/**
 * A single elimination's columns, named from the end: Final, Semifinals, Quarterfinals, then
 * "Round N" (UXP-17, the create form's series-length stage names).
 */
export const singleEliminationRoundName: RoundNamer = (index, count, bracketRound) =>
  NAMES_FROM_END[count - 1 - index] ?? roundNumberName(index, count, bracketRound);
