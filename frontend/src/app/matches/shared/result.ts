// The Winner rule, shared by Match History rows and the detail stubs (spec #289).
import { Match } from '../../api/matches.models';
import { GameRoundListItem } from '../../api/rounds.models';

/** What a result chip shows: the winner's name, "Tie", or a muted "In progress". */
export interface Result {
  label: string;
  /** True for "In progress": shown muted, since there is no result yet. */
  muted: boolean;
}

const IN_PROGRESS: Result = { label: 'In progress', muted: true };

function winnerOrTie(winnerName: string | null): Result {
  return { label: winnerName ?? 'Tie', muted: false };
}

/**
 * An unfinished Match (`is_completed === false`, e.g. a League Match after round 1) is
 * "In progress", never "Tie"; otherwise the winner's name, or "Tie" when there is none.
 */
export function matchResult(match: Pick<Match, 'is_completed' | 'winner_name'>): Result {
  return match.is_completed ? winnerOrTie(match.winner_name) : IN_PROGRESS;
}

/** A Round shows the winner's name, or "Tie" when there is none. */
export function roundResult(round: Pick<GameRoundListItem, 'winner_name'>): Result {
  return winnerOrTie(round.winner_name);
}
