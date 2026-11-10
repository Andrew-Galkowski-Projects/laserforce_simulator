// The Playback map's Tag lines, capture bursts and base markers (RV-06): pure, so the canvas
// painting in playback-map.ts only draws what these return. The shapes came from the prototype.
import { BaseType, MapBase } from '../../../api/maps.models';
import { Side, TimelineEvent } from '../../../api/rounds.models';

export type TagLineKind = 'tag' | 'miss' | 'missile-hit' | 'resupply' | 'capture';

export interface TagLine {
  kind: TagLineKind;
  /** The acting player's Side. */
  side: Side;
  /** Image px, already offset. */
  from: readonly [number, number];
  /** Image px, already offset. */
  to: readonly [number, number];
  /** Fade only, 0..1. */
  alpha: number;
  /** The event's tick. */
  tick: number;
}

export interface CaptureBurst {
  side: Side;
  at: readonly [number, number];
  alpha: number;
  tick: number;
}

export interface Overlays {
  lines: TagLine[];
  bursts: CaptureBurst[];
}

export interface BaseMarker {
  type: BaseType;
  kind: 'red' | 'blue' | 'neutral';
  letter: 'R' | 'B' | 'N';
  x: number;
  y: number;
}

/** A line shows while `0 <= cursor - tick <= LINE_TICKS`. */
export const LINE_TICKS = 2;
/** A capture's lasers show while `0 <= cursor - tick <= BURST_TICKS`. */
export const BURST_TICKS = 4;
/** Each line sits this far to its acting player's right. */
export const LINE_OFFSET_PX = 4;

/** The Tag lines and capture bursts showing at `cursor` (ticks, may be fractional). */
export function overlaysAt(
  events: readonly TimelineEvent[],
  cursor: number,
  positionAt: (playerId: number, tick: number) => readonly [number, number] | null,
  bases: readonly MapBase[],
): Overlays {
  const lines: TagLine[] = [];
  const bursts: CaptureBurst[] = [];
  for (const event of events) {
    const age = cursor - event.tick;
    const kind = lineKind(event);
    if (age < 0 || age > BURST_TICKS || !kind) {
      continue;
    }
    const from = positionAt(event.actor_id, event.tick);
    let to: Point | null;
    if (kind === 'capture') {
      to = from && capturedBase(event, from, bases);
    } else {
      to = event.target_id === null ? null : positionAt(event.target_id, event.tick);
    }
    const ends = offsetEnds(from, to);
    if (!ends || !to) {
      continue;
    }
    const side = event.actor_side;
    if (age <= LINE_TICKS) {
      lines.push({ kind, side, ...ends, alpha: fadeAlpha(age, LINE_TICKS), tick: event.tick });
    }
    if (kind === 'capture') {
      bursts.push({ side, at: to, alpha: fadeAlpha(age, BURST_TICKS), tick: event.tick });
    }
  }
  return { lines, bursts };
}

/** A spot on the map image, in px. */
export type Point = readonly [number, number];

/** An overlay's opacity `age` ticks after its event, shown for `ticks`: 1 down to ~0.2. */
export function fadeAlpha(age: number, ticks: number): number {
  return 1 - age / (ticks + 0.5);
}

/** The `base_capture` event's `base_id`s (`matches/sim_helpers`): 15 is any neutral base. */
const RED_BASE_ID = 13;
const BLUE_BASE_ID = 14;
const NEUTRAL_BASE_ID = 15;

/**
 * Where a capture's base stands: `metadata.base_type` when present, else `base_id` 13 / 14 for
 * the red / blue base and 15 for the `neutral_*` base nearest the capturer at the event tick.
 */
function capturedBase(
  event: TimelineEvent,
  capturer: Point,
  bases: readonly MapBase[],
): Point | null {
  const type = event.metadata['base_type'];
  const id = event.metadata['base_id'];
  let base: MapBase | undefined;
  if (typeof type === 'string') {
    base = bases.find((b) => b.type === type);
  } else if (id === RED_BASE_ID || id === BLUE_BASE_ID) {
    const want = id === RED_BASE_ID ? 'red' : 'blue';
    base = bases.find((b) => b.type === want);
  } else if (id === NEUTRAL_BASE_ID) {
    const distance = (b: MapBase) => Math.hypot(b.x_px - capturer[0], b.y_px - capturer[1]);
    base = bases
      .filter((b) => baseKind(b.type) === 'neutral')
      .reduce<MapBase | undefined>(
        (best, b) => (!best || distance(b) < distance(best) ? b : best),
        undefined,
      );
  }
  return base ? [base.x_px, base.y_px] : null;
}

/**
 * Both ends shifted `LINE_OFFSET_PX` along the actor's right-hand normal `(-dy, dx) / length`
 * (image coordinates, y grows down); `null` for a missing end or a zero-length pair.
 */
function offsetEnds(from: Point | null, to: Point | null): { from: Point; to: Point } | null {
  if (!from || !to) {
    return null;
  }
  const dx = to[0] - from[0];
  const dy = to[1] - from[1];
  const length = Math.hypot(dx, dy);
  if (length === 0) {
    return null;
  }
  const ox = (-dy / length) * LINE_OFFSET_PX;
  const oy = (dx / length) * LINE_OFFSET_PX;
  return { from: [from[0] + ox, from[1] + oy], to: [to[0] + ox, to[1] + oy] };
}

const RESUPPLY_TYPES: ReadonlySet<string> = new Set([
  'resupply_ammo',
  'resupply_lives',
  'combo_resupply',
]);

/** The line an event draws, or `null` for one that draws none. */
function lineKind(event: TimelineEvent): TagLineKind | null {
  switch (event.event_type) {
    case 'tag':
      return 'tag';
    case 'miss':
      return 'miss';
    case 'missiled':
      // MECH-21 (ADR-0050): a launched missile always hits; a dodge is a `lock_cancelled`.
      return 'missile-hit';
    case 'base_capture':
      // Only an active capture draws: round-end awarded ones carry no extras.
      return 'shots_remaining' in event.metadata ? 'capture' : null;
    default:
      return RESUPPLY_TYPES.has(event.event_type) ? 'resupply' : null;
  }
}

/** A base's Side, or `neutral` for every `neutral_*` base. */
export function baseKind(type: BaseType): 'red' | 'blue' | 'neutral' {
  return type === 'red' || type === 'blue' ? type : 'neutral';
}

/** The bases to draw: a lettered diamond each, where the map editor placed them. */
export function baseMarkers(bases: readonly MapBase[]): BaseMarker[] {
  return bases.map((b) => {
    const kind = baseKind(b.type);
    const letter = kind === 'red' ? 'R' : kind === 'blue' ? 'B' : 'N';
    return { type: b.type, kind, letter, x: b.x_px, y: b.y_px };
  });
}
