// Text formats the head-to-head screens share (Django's `pluralize` and the W-L-T line).
import { WinLossRecord } from '../../api/analytics.models';

/** `n word` with Django's `pluralize`: "1 round", "0 rounds", "2 matches" (`suffix` "es"). */
export function plural(n: number, word: string, suffix = 's'): string {
  return `${n} ${word}${n === 1 ? '' : suffix}`;
}

/** A record as "W-L-T". */
export function record(r: WinLossRecord): string {
  return `${r.wins}-${r.losses}-${r.ties}`;
}
