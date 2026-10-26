// The head-to-head screens' URL filters (spec #377 INV-1), shared by Team and Player
// head-to-head. Each reader drops a value the API's strict param Form would refuse, so the
// server default applies instead of a 400.
import { Provenance } from '../../api/analytics.models';
import { Role } from '../../api/players.models';

/** Anything with `get(name)`: a `ParamMap`. */
export interface QueryReader {
  get(name: string): string | null;
}

/** The Provenance select, in the Django order. */
export const PROVENANCE_OPTIONS: readonly { value: Provenance; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'real', label: 'Real only' },
  { value: 'sim', label: 'Simulated only' },
];

/** A whole number >= 1, or undefined. */
export function queryId(query: QueryReader, name: string): number | undefined {
  const raw = query.get(name);
  return raw !== null && /^[1-9]\d*$/.test(raw) ? Number(raw) : undefined;
}

/** `all`, `real` or `sim`, or undefined. */
export function queryProvenance(query: QueryReader): Provenance | undefined {
  const raw = query.get('provenance');
  return PROVENANCE_OPTIONS.find((o) => o.value === raw)?.value;
}

/** A real calendar date as `YYYY-MM-DD`, or undefined. */
export function queryDate(query: QueryReader, name: string): string | undefined {
  const raw = query.get(name);
  if (raw === null || !/^\d{4}-\d{2}-\d{2}$/.test(raw)) return undefined;
  const [y, m, d] = raw.split('-').map(Number);
  const date = new Date(Date.UTC(y, m - 1, d));
  const real =
    date.getUTCFullYear() === y && date.getUTCMonth() === m - 1 && date.getUTCDate() === d;
  return real ? raw : undefined;
}

/** One of the five Roles, or undefined. */
export function queryRole(query: QueryReader, roles: readonly Role[]): Role | undefined {
  const raw = query.get('role');
  return roles.find((r) => r === raw);
}

/** A form value as a query param: blank becomes null, which the router leaves out. */
export function paramOrNull(value: string): string | null {
  return value === '' ? null : value;
}
