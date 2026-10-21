// Roster helpers shared by the Teams screens (ANG-06a): slot order, role
// labels and the active / bench split, all derived from the six `slot_*` ids.
import { Role } from '../../api/players.models';
import { TeamBase } from '../../api/teams.models';

/** The six role-slot keys of a Team. */
export type SlotKey =
  'slot_commander' | 'slot_heavy' | 'slot_scout_1' | 'slot_scout_2' | 'slot_medic' | 'slot_ammo';

/** The slots in display order (CMD, HVY, SCT 1, SCT 2, MED, AMO) with their labels. */
export const SLOTS: readonly { key: SlotKey; label: string }[] = [
  { key: 'slot_commander', label: 'CMD' },
  { key: 'slot_heavy', label: 'HVY' },
  { key: 'slot_scout_1', label: 'SCT 1' },
  { key: 'slot_scout_2', label: 'SCT 2' },
  { key: 'slot_medic', label: 'MED' },
  { key: 'slot_ammo', label: 'AMO' },
];

/** Short role labels (ADR-0043: roles stay CMD / HVY / SCT / AMO / MED). */
export const ROLE_LABELS: Readonly<Record<Role, string>> = {
  commander: 'CMD',
  heavy: 'HVY',
  scout: 'SCT',
  medic: 'MED',
  ammo: 'AMO',
};

/** Player ids in a slot, in slot order (empty slots skipped). */
export function slottedPlayerIds(team: TeamBase): number[] {
  return SLOTS.map((s) => team[s.key]).filter((id): id is number => id !== null);
}

/** The players in no slot, in their original order. */
export function benchPlayers<P extends { id: number }>(team: TeamBase, players: P[]): P[] {
  const slotted = new Set(slottedPlayerIds(team));
  return players.filter((p) => !slotted.has(p.id));
}
