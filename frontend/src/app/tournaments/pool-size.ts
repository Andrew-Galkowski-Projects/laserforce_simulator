/** A drawable Player pool: whole Teams of 6, at least 4 of them. */
const PLAYERS_PER_TEAM = 6;
const MIN_POOL = 24;

/** The Django page's draw rule: a pool size that is not a multiple of 6, or under 24. */
export function poolSizeInvalid(size: number): boolean {
  return size % PLAYERS_PER_TEAM !== 0 || size < MIN_POOL;
}
