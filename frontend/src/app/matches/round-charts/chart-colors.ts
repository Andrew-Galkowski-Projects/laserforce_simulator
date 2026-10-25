// Chart colours from the `--lf-*` tokens (ADR-0043: no colour literal outside _tokens.scss).
// A canvas cannot read a CSS variable, so each colour is resolved to a concrete value.
import { Side } from '../../api/rounds.models';
import { ChartPlayer } from './event-charts';

/** The lightest share of the Side colour a player's shade keeps (the last player on a Side). */
const MIN_SHARE = 55;

/** A token's current value on the document root, trimmed (`''` when it is unset). */
export function readToken(name: string, root: Element = document.documentElement): string {
  return getComputedStyle(root).getPropertyValue(name).trim();
}

/**
 * The `index`-th of `count` shades of `base`: the first is `base` itself, the rest mix in more
 * of `mix` (the text colour, so a shade darkens in light mode and lightens in dark mode).
 */
export function shade(base: string, mix: string, index: number, count: number): string {
  const share = count > 1 ? Math.round(100 - ((100 - MIN_SHARE) * index) / (count - 1)) : 100;
  return `color-mix(in oklch, ${base} ${share}%, ${mix})`;
}

/** Every player's colour by id: a shade of their Side's colour, by their order on that Side. */
export function playerColors(
  players: readonly ChartPlayer[],
  tokens: Record<Side, string> & { mix: string },
): Map<number, string> {
  const colors = new Map<number, string>();
  for (const side of ['red', 'blue'] as const) {
    const onSide = players.filter((p) => p.side === side);
    onSide.forEach((p, i) => colors.set(p.id, shade(tokens[side], tokens.mix, i, onSide.length)));
  }
  return colors;
}

/**
 * `css` as the browser computes it (e.g. a `color-mix()` resolved to `oklch(...)`), through a
 * throwaway probe inside `host`; `css` itself when the browser cannot compute it.
 */
export function resolveColor(css: string, host: Element): string {
  const probe = document.createElement('span');
  probe.style.color = css;
  host.appendChild(probe);
  const computed = probe.style.color ? getComputedStyle(probe).color : '';
  probe.remove();
  return computed || css;
}
