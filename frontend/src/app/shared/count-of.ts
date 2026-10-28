/** "1 player", "2 players", "0 players": `noun` takes a plain `s` unless the count is 1. */
export function countOf(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? '' : 's'}`;
}
