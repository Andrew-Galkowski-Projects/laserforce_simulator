import { BracketType, PlayNextResult } from '../../api/tournaments.models';
import { playedMessage } from './played-message';

/** A play-next answer: Round 1, Match 1, Red Raiders 1–0 Blue Bolts, no champion. */
function result(
  node: Partial<PlayNextResult['node']> = {},
  champion: PlayNextResult['champion'] = null,
): PlayNextResult {
  return {
    node: {
      id: 40,
      bracket_type: 'winners' as BracketType,
      bracket_round: 1,
      position: 0,
      team_a: { id: 11, name: 'Red Raiders' },
      team_b: { id: 12, name: 'Blue Bolts' },
      seed_a: 1,
      seed_b: 2,
      is_bye: false,
      wins_a: 1,
      wins_b: 0,
      series_length: 1,
      winner: { id: 11, name: 'Red Raiders' },
      games: [],
      ...node,
    },
    match_ids: [70],
    state: champion ? 'completed' : 'active',
    champion,
  };
}

describe('playedMessage', () => {
  it('names a Winners bracket node by its Bracket round and 1-based Match number', () => {
    expect(playedMessage(result({ bracket_round: 3, position: 1, wins_a: 2, wins_b: 1 }))).toBe(
      'Played Round 3, Match 2: Red Raiders 2–1 Blue Bolts.',
    );
  });

  it.each<BracketType>(['round_robin', 'swiss'])('names a %s node as a plain round', (type) => {
    expect(playedMessage(result({ bracket_type: type, bracket_round: 4, position: 2 }))).toBe(
      'Played Round 4, Match 3: Red Raiders 1–0 Blue Bolts.',
    );
  });

  it('prefixes a Losers bracket node with "Losers"', () => {
    expect(playedMessage(result({ bracket_type: 'losers', bracket_round: 2, position: 0 }))).toBe(
      'Played Losers Round 2, Match 1: Red Raiders 1–0 Blue Bolts.',
    );
  });

  it('names the Grand final without a round or Match number', () => {
    expect(
      playedMessage(result({ bracket_type: 'grand_final', bracket_round: 1, position: 0 })),
    ).toBe('Played Grand Final: Red Raiders 1–0 Blue Bolts.');
  });

  it('shows "—" for a missing Team', () => {
    expect(playedMessage(result({ team_a: null }))).toBe(
      'Played Round 1, Match 1: — 1–0 Blue Bolts.',
    );
    expect(playedMessage(result({ team_b: null }))).toBe(
      'Played Round 1, Match 1: Red Raiders 1–0 —.',
    );
  });

  it('adds the champion when the Match ended the Tournament', () => {
    expect(
      playedMessage(result({ bracket_type: 'grand_final' }, { id: 11, name: 'Red Raiders' })),
    ).toBe('Played Grand Final: Red Raiders 1–0 Blue Bolts. Red Raiders win the Tournament!');
  });
});
