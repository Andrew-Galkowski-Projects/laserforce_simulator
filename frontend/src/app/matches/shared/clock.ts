/** Ticks per second (1 tick = 0.5 s, as `GameEvent.formatted_timestamp`). */
export const TICKS_PER_SECOND = 2;

/**
 * A tick count as round-clock text: `m:ss` by default, `mm:ss` with `pad` (the minutes
 * zero-padded to two digits).
 */
export function tickClock(ticks: number, { pad = false }: { pad?: boolean } = {}): string {
  const seconds = Math.floor(ticks / TICKS_PER_SECOND);
  const minutes = String(Math.floor(seconds / 60));
  return `${pad ? minutes.padStart(2, '0') : minutes}:${String(seconds % 60).padStart(2, '0')}`;
}
