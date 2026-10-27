// Reads a `--lf-*` token's value for code that cannot use a CSS variable (a canvas).

/** A token's current value on the document root, trimmed (`''` when it is unset). */
export function readToken(name: string, root: Element = document.documentElement): string {
  return getComputedStyle(root).getPropertyValue(name).trim();
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
