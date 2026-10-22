/**
 * Which setup the shared form runs, from the route's `data.kind`: `/matches/new` is `'match'`
 * (two Rounds, with a Match type), `/rounds/new` is `'round'` (a Single Round).
 */
export type SetupKind = 'match' | 'round';
