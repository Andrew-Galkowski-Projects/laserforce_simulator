// The Playback map's missile Locks (RV-08, MECH-21): pure, so the canvas painting in
// playback-map.ts only draws what these return. A Lock is read from the timeline alone: the
// engine writes its start (`locking`, carrying `metadata.lock_ticks`) and how it ended (ADR-0050).
import { Side, TimelineEvent } from '../../../api/rounds.models';
import { LINE_TICKS, Point, fadeAlpha } from './playback-overlays';

/** How a lock ended: a missile hit, a dodge, or no missile. */
export type LockMarkKind = 'hit' | 'dodge' | 'lost';

/** One lock from its `locking` event to the tick it ended, and how it ended. */
export interface LockSpan {
  shooterId: number;
  targetId: number;
  /** The shooter's Side. */
  side: Side;
  /** The `locking` tick. */
  start: number;
  /** The `locking` row's `metadata.lock_ticks`: how long the lock takes to launch. */
  lockTicks: number;
  /** The ending row's tick, or `start + lockTicks` for a lock still open at round end. */
  end: number;
  /**
   * `hit`: the shooter's `missiled` on the target; `dodge`: a `lock_cancelled` with reason
   * `dodge`; `lost`: a `lock_cancelled` with any other reason, or no row (round end).
   */
  ending: LockMarkKind;
}

/** A lock building at the cursor, between where both players stand at the cursor. */
export interface BuildingLock {
  side: Side;
  /** Image px. */
  from: Point;
  /** Image px. */
  to: Point;
  /** How far the lock has built, 0..1 over its `lockTicks`. */
  progress: number;
}

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
  cursor: number,
  positionAt: (playerId: number, tick: number) => Point | null,
): LockOverlays {
  const locks: BuildingLock[] = [];
  const marks: LockMark[] = [];
  for (const span of spans) {
    if (cursor >= span.start && cursor < span.end) {
      const from = positionAt(span.shooterId, cursor);
      const to = positionAt(span.targetId, cursor);
      // Two players on one spot have no line between them.
      if (from && to && (from[0] !== to[0] || from[1] !== to[1])) {
        const progress = (cursor - span.start) / span.lockTicks;
        locks.push({ side: span.side, from, to, progress });
      }
    }
    // Every ending marks the target where it stood when the lock ended; a dodge's dodger is the
    // target, and the mark takes the shooter's Side.
    const age = cursor - span.end;
    const at = age >= 0 && age <= LINE_TICKS ? positionAt(span.targetId, span.end) : null;
    if (at) {
      const alpha = fadeAlpha(age, LINE_TICKS);
      marks.push({ kind: span.ending, side: span.side, at, alpha, tick: span.end });
    }
  }
  return { locks, marks };
}

/** Every lock in the round, in event order (`events` in the timeline's log order). */
export function lockSpans(events: readonly TimelineEvent[]): LockSpan[] {
  const spans: LockSpan[] = [];
  events.forEach((event, index) => {
    if (event.event_type !== 'locking' || event.target_id === null) {
      return;
    }
    const shooterId = event.actor_id;
    const targetId = event.target_id;
    const start = event.tick;
    const lockTicks = Number(event.metadata['lock_ticks']);
    // Later in log order, not by tick: a retarget logs the old lock's cancel just before the
    // new `locking`, on the same tick.
    const ended = events
      .slice(index + 1)
      .find(
        (e) =>
          e.actor_id === shooterId &&
          ((e.event_type === 'missiled' && e.target_id === targetId) ||
            e.event_type === 'lock_cancelled'),
      );
    const span = { shooterId, targetId, side: event.actor_side, start, lockTicks };
    spans.push(
      ended
        ? { ...span, end: ended.tick, ending: endingOf(ended) }
        : { ...span, end: start + lockTicks, ending: 'lost' },
    );
  });
  return spans;
}

/** The ending a lock's `missiled` or `lock_cancelled` row gives it. */
function endingOf(row: TimelineEvent): LockMarkKind {
  if (row.event_type === 'missiled') {
    return 'hit';
  }
  return row.metadata['reason'] === 'dodge' ? 'dodge' : 'lost';
}
