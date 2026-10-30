/**
 * An amount held in thousands of dollars as `$7.2k` / `-$7.2k`, the Django `money_k` filter
 * (`teams/templatetags/team_extras.py`): one decimal, rounded half-up (away from zero), the sign
 * before the `$` (never `$-7.2k`), and no sign on a value that rounds to zero (`-0.04` =>
 * `$0.0k`). Every money figure on the finance screens goes through it.
 */
export function moneyK(value: number): string {
  // `Math.round` on the magnitude rounds a half away from zero, as ROUND_HALF_UP does.
  const tenths = Math.round(Math.abs(value) * 10);
  const sign = value < 0 && tenths > 0 ? '-' : '';
  return `${sign}$${(tenths / 10).toFixed(1)}k`;
}
