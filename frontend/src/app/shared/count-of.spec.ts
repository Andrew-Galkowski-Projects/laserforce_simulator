import { countOf } from './count-of';

describe('countOf', () => {
  it('keeps the noun singular for one', () => {
    expect(countOf(1, 'player')).toBe('1 player');
  });

  it.each([
    [0, '0 players'],
    [2, '2 players'],
    [24, '24 players'],
  ])('pluralises %i', (count, expected) => {
    expect(countOf(count, 'player')).toBe(expected);
  });
});
