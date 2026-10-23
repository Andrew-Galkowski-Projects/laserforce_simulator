// The nine **Event groups** the Event log filters and colours by (GLOSSARY, ANG-06e).
import { TimelineEventType } from '../../api/rounds.models';

/** One Event group: its label, its `--lf-ev-*` colour token and the event types it covers. */
export interface EventGroup {
  key: string;
  label: string;
  token: string;
  types: readonly TimelineEventType[];
}

/** The groups in display order. Every one of the 15 timeline types is in exactly one. */
export const EVENT_GROUPS: readonly EventGroup[] = [
  { key: 'tag', label: 'Tag', token: '--lf-ev-tag', types: ['tag'] },
  {
    key: 'elimination',
    label: 'Elimination',
    token: '--lf-ev-elimination',
    types: ['elimination', 'team_elimination'],
  },
  { key: 'miss', label: 'Miss', token: '--lf-ev-miss', types: ['miss'] },
  {
    key: 'special',
    label: 'Special',
    token: '--lf-ev-special',
    types: ['special', 'nuke_cancelled'],
  },
  {
    key: 'missile',
    label: 'Missile',
    token: '--lf-ev-missile',
    types: ['missiled', 'missile_dodge'],
  },
  { key: 'locking', label: 'Locking', token: '--lf-ev-locking', types: ['locking'] },
  {
    key: 'resupply',
    label: 'Resupply',
    token: '--lf-ev-resupply',
    types: ['resupply_ammo', 'resupply_lives', 'combo_resupply', 'medic_reset'],
  },
  { key: 'base', label: 'Base capture', token: '--lf-ev-base', types: ['base_capture'] },
  { key: 'movement', label: 'Movement', token: '--lf-ev-movement', types: ['movement'] },
];

/** The groups that start unchecked on the Event log: only Movement. */
export const HIDDEN_BY_DEFAULT: readonly string[] = ['movement'];

const BY_TYPE = new Map<string, EventGroup>(
  EVENT_GROUPS.flatMap((group) => group.types.map((type) => [type, group] as const)),
);

/** The Event group of an event type (Tag for a type the table doesn't know). */
export function eventGroupOf(type: TimelineEventType): EventGroup {
  return BY_TYPE.get(type) ?? EVENT_GROUPS[0];
}
