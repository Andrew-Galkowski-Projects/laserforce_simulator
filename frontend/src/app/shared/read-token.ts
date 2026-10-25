// Reads a `--lf-*` token's value for code that cannot use a CSS variable (a canvas).

/** A token's current value on the document root, trimmed (`''` when it is unset). */
export function readToken(name: string, root: Element = document.documentElement): string {
  return getComputedStyle(root).getPropertyValue(name).trim();
}
