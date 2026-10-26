// Number formats shared by Career stats and Role Benchmarks (Django's `floatformat`).

/** The em dash Django shows for a missing value. */
export const DASH = '—';

/** `value` to `dp` decimals, like `floatformat:dp` (never "-0"). */
export function fixed(value: number, dp: number): string {
  const shown = value.toFixed(dp);
  return Number(shown) === 0 ? (0).toFixed(dp) : shown;
}

/** `fixed`, or the dash for a missing value. */
export function fixedOrDash(value: number | null, dp: number): string {
  return value === null ? DASH : fixed(value, dp);
}

/** `Commander` from `commander` (Django's `|title`). */
export function capitalise(word: string): string {
  return word.charAt(0).toUpperCase() + word.slice(1);
}
