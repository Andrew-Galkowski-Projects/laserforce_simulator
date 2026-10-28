import { RoundNamer, roundNumberName, singleEliminationRoundName } from './round-names';

const names = (namer: RoundNamer, bracketRounds: number[]): string[] =>
  bracketRounds.map((round, i) => namer(i, bracketRounds.length, round));

describe('singleEliminationRoundName', () => {
  it('names a 2-round field Semifinals, Final', () => {
    expect(names(singleEliminationRoundName, [1, 2])).toEqual(['Semifinals', 'Final']);
  });

  it('names a 4-round field Round 1, Quarterfinals, Semifinals, Final', () => {
    expect(names(singleEliminationRoundName, [1, 2, 3, 4])).toEqual([
      'Round 1',
      'Quarterfinals',
      'Semifinals',
      'Final',
    ]);
  });

  it('names a lone round Final', () => {
    expect(names(singleEliminationRoundName, [1])).toEqual(['Final']);
  });
});

describe('roundNumberName', () => {
  it('names every column "Round N" by its bracket_round', () => {
    expect(names(roundNumberName, [1, 2, 3])).toEqual(['Round 1', 'Round 2', 'Round 3']);
  });
});
