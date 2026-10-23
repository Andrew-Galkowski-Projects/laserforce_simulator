// Round timeline (Event log) spec fixtures (ANG-06e). Imported only by *.spec.ts files.
import {
  EventSummary,
  Highlight,
  RoundTimeline,
  TimelineEvent,
  TimelinePlayer,
} from '../../api/rounds.models';

/** Ace (red commander) tags Bolt (blue heavy) at tick 120 (1:00) for 100 points. */
export function timelineEvent(overrides: Partial<TimelineEvent> = {}): TimelineEvent {
  return {
    tick: 120,
    event_type: 'tag',
    points: 100,
    description: 'Ace tagged Bolt',
    actor_id: 101,
    actor_name: 'Ace',
    actor_side: 'red',
    target_id: 201,
    target_name: 'Bolt',
    target_side: 'blue',
    metadata: {},
    ...overrides,
  };
}

/** A red commander, Ace (id 101). */
export function timelinePlayer(overrides: Partial<TimelinePlayer> = {}): TimelinePlayer {
  return { id: 101, name: 'Ace', side: 'red', role: 'commander', ...overrides };
}

/** Blue's first elimination at tick 240 (2:00). */
export function highlight(overrides: Partial<Highlight> = {}): Highlight {
  return {
    kind: 'first_elimination',
    tick: 240,
    team: 'blue',
    actor: 'Bolt',
    target: 'Ace',
    points: null,
    label: 'Bolt eliminated Ace',
    ...overrides,
  };
}

/** An all-zero summary; override the counts a spec asserts. */
export function eventSummary(overrides: Partial<EventSummary> = {}): EventSummary {
  return {
    total_events: 0,
    tags: 0,
    moves: 0,
    missiles: 0,
    misses: 0,
    eliminations: 0,
    resupplies: 0,
    specials: 0,
    ...overrides,
  };
}

/** The four players `timeline()` uses: Ace + Cleo red, Bolt + Dax blue. */
export const TIMELINE_PLAYERS: TimelinePlayer[] = [
  timelinePlayer(),
  timelinePlayer({ id: 201, name: 'Bolt', side: 'blue', role: 'heavy' }),
  timelinePlayer({ id: 102, name: 'Cleo', side: 'red', role: 'medic' }),
  timelinePlayer({ id: 202, name: 'Dax', side: 'blue', role: 'ammo' }),
];

/**
 * A Round timeline: the four `TIMELINE_PLAYERS`, one tag, no highlights and a
 * summary counting that tag. Pass `events` / `highlights` / `summary` to override.
 */
export function timeline(overrides: Partial<RoundTimeline> = {}): RoundTimeline {
  return {
    events: [timelineEvent()],
    players: TIMELINE_PLAYERS,
    highlights: [],
    summary: eventSummary({ total_events: 1, tags: 1 }),
    ...overrides,
  };
}
