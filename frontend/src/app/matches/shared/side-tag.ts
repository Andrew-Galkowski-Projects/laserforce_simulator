import { Side } from '../../api/rounds.models';

/** A Side's R / B badge, kept next to its colour for colour-blind users. */
export function sideTag(side: Side): 'R' | 'B' {
  return side === 'red' ? 'R' : 'B';
}
