import { Role } from '../../api/players.models';
import { ROLE_LABELS } from '../../teams/shared/roster';

/**
 * A role's short label (CMD / HVY / SCT / AMO / MED, ADR-0043), matched case-insensitively. An
 * unknown role falls back to its first three letters, upper-cased, rather than crashing.
 */
export function roleLabel(role: string): string {
  return ROLE_LABELS[role.toLowerCase() as Role] ?? role.slice(0, 3).toUpperCase();
}
