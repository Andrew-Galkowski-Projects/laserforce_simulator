import { PlayNextResult } from '../../api/tournaments.models';

/**
 * The notice after a Play Next Match, as Django's `_played_message` words it:
 * "Played Round 1, Match 2: A 2–0 B." plus " {champion} win the Tournament!" when that Match ended
 * the Tournament.
 */
export function playedMessage(result: PlayNextResult): string {
  const { node, champion } = result;
  const label =
    node.bracket_type === 'grand_final'
      ? 'Grand Final'
      : `${node.bracket_type === 'losers' ? 'Losers ' : ''}Round ${node.bracket_round}, Match ${node.position + 1}`;
  const nameA = node.team_a?.name ?? '—';
  const nameB = node.team_b?.name ?? '—';
  const text = `Played ${label}: ${nameA} ${node.wins_a}–${node.wins_b} ${nameB}.`;
  return champion ? `${text} ${champion.name} win the Tournament!` : text;
}
