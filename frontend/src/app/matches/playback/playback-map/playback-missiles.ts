// The Playback map's missile Locks (RV-08): pure, so the canvas painting in playback-map.ts
// only draws what these return. A Lock is read from the timeline alone (spec #580).
import { Side, TimelineEvent } from '../../../api/rounds.models';
import { LINE_TICKS } from './playback-overlays';

/** The engine's lock length: a lock started at tick T resolves at T + LOCK_TICKS. */
export const LOCK_TICKS = 3;

/** One lock from its `locking` event to the tick it ended, and how it ended. */
export interface LockSpan {
  shooterId: number;
  targetId: number;
  /** The shooter's Side. */
  side: Side;
  /** The `locking` tick. */
  start: number;
  /** `start + LOCK_TICKS`, or the tick the shooter cut it short. */
  end: number;
  /** `result`: a hit or a dodge paired with it at full length; `lost`: anything else. */
  ending: 'result' | 'lost';
}

type Point = readonly [number, number];

/** A lock building at the cursor, between where both players stand at the cursor. */
export interface BuildingLock {
  side: Side;
  /** Image px. */
  from: Point;
  /** Image px. */
  to: Point;
  /** How far the lock has built, 0..1 over `LOCK_TICKS`. */
  progress: number;
}

/** How a lock ended: a missile hit, a dodge, or no missile. */
export type LockMarkKind = 'hit' | 'dodge' | 'lost';

export interface LockMark {
  kind: LockMarkKind;
  side: Side;
  /** Image px. */
  at: Point;
  /** Fade only, 0..1. */
  alpha: number;
  tick: number;
}

export interface LockOverlays {
  locks: BuildingLock[];
  marks: LockMark[];
}

/** The locks building and the lock marks showing at `cursor` (ticks, may be fractional). */
export function lockOverlaysAt(
  spans: readonly LockSpan[],
  events: readonly TimelineEvent[],
  cursor: number,
  positionAt: (playerId: number, tick: number) => Point | null,
): LockOverlays {
  const locks: BuildingLock[] = [];
  for (const span of spans) {
    if (cursor < span.start || cursor >= span.end) {
      continue;
    }
    const from = positionAt(span.shooterId, cursor);
    const to = positionAt(span.targetId, cursor);
    // Two players on one spot have no line between them.
    if (!from || !to || (from[0] === to[0] && from[1] === to[1])) {
      continue;
    }
    locks.push({ side: span.side, from, to, progress: (cursor - span.start) / LOCK_TICKS });
  }
  const marks: LockMark[] = [];
  const mark = (kind: LockMarkKind, side: Side, id: number, tick: number): void => {
    const age = cursor - tick;
    const at = age >= 0 && age <= LINE_TICKS ? positionAt(id, tick) : null;
    if (at) {
      marks.push({ kind, side, at, alpha: 1 - age / (LINE_TICKS + 0.5), tick });
    }
  };
  for (const span of spans) {
    if (span.ending === 'lost') {
      mark('lost', span.side, span.targetId, span.end);
    }
  }
  // Hits and dodges come from the result events, not the spans, so a lock the map already
  // cut still shows its result.
  for (const event of events) {
    if (
      event.event_type === 'missiled' &&
      event.metadata['result'] === 'hit' &&
      event.target_id !== null
    ) {
      mark('hit', event.actor_side, event.target_id, event.tick);
    } else if (event.event_type === 'missile_dodge' && event.target_side !== null) {
      // The dodger is the actor; the mark takes the shooter's (the target's) Side.
      mark('dodge', event.target_side, event.actor_id, event.tick);
    }
  }
  return { locks, marks };
}

/** Every lock in the round, in event order. */
export function lockSpans(events: readonly TimelineEvent[]): LockSpan[] {
  const spans: LockSpan[] = [];
  for (const event of events) {
    if (event.event_type !== 'locking' || event.target_id === null) {
      continue;
    }
    const shooterId = event.actor_id;
    const targetId = event.target_id;
    const start = event.tick;
    const full = start + LOCK_TICKS;
    // One Lock at a time: the shooter's next lock cuts this one short. At exactly T+3 the
    // engine has already resolved the lock (locks resolve before actions in a tick).
    const cut = events
      .filter((e) => e.actor_id === shooterId && cutsLock(e) && e.tick > start && e.tick < full)
      .reduce((first, e) => Math.min(first, e.tick), Infinity);
    if (cut !== Infinity) {
      spans.push({ shooterId, targetId, side: event.actor_side, start, end: cut, ending: 'lost' });
      continue;
    }
    const end = full;
    // A dodge's actor is the dodger (the target) and its target the shooter.
    const resolved = events.some(
      (e) =>
        e.tick === end &&
        ((e.event_type === 'missiled' && e.actor_id === shooterId && e.target_id === targetId) ||
          (e.event_type === 'missile_dodge' &&
            e.actor_id === targetId &&
            e.target_id === shooterId)),
    );
    spans.push({
      shooterId,
      targetId,
      side: event.actor_side,
      start,
      end,
      ending: resolved ? 'result' : 'lost',
    });
  }
  return spans;
}

/**
 * Whether the shooter acting this way ends their lock: a new lock, or pulling the trigger
 * early (a tag, a miss, or an active base capture, which carries `shots_remaining`; a
 * round-end award does not).
 */
function cutsLock(event: TimelineEvent): boolean {
  switch (event.event_type) {
    case 'locking':
    case 'tag':
    case 'miss':
      return true;
    case 'base_capture':
      return 'shots_remaining' in event.metadata;
    default:
      return false;
  }
}
