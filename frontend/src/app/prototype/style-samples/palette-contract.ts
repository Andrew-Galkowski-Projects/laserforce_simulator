/**
 * ANG-04b token contract (spec #246). Every palette class `.lf-palette-{a..d}` defines exactly
 * these custom properties, and layouts read colour, fonts, radius and glow from nothing else.
 *
 * Usage notes for layouts:
 * - Side: put `data-lf-side="red"` / `data-lf-side="blue"` on every team container. It sets
 *   `--side`, `--side-text`, `--side-bg` and `--side-dim` to that Side's tokens; nothing inside
 *   picks red or blue itself (see `_sides.scss`).
 * - Glow: `--lf-glow` is a shadow *geometry* (`0 0 12px` in palette D, `0 0 0` elsewhere). Use it
 *   with a colour token: `box-shadow: var(--lf-glow) var(--side);` or
 *   `text-shadow: var(--lf-glow) var(--lf-accent);`.
 * - Event types: `--lf-ev-<type>` where `<type>` is a `SampleEventType` from the fixture.
 * - Derived tints are fine through `color-mix(in srgb, var(--side) 20%, transparent)`.
 */
export const LF_TOKENS = [
  '--lf-bg',
  '--lf-surface',
  '--lf-surface-alt',
  '--lf-text',
  '--lf-text-muted',
  '--lf-border',
  '--lf-accent',
  '--lf-side-red',
  '--lf-side-red-text',
  '--lf-side-red-bg',
  '--lf-side-red-dim',
  '--lf-side-blue',
  '--lf-side-blue-text',
  '--lf-side-blue-bg',
  '--lf-side-blue-dim',
  '--lf-ev-elimination',
  '--lf-ev-missile',
  '--lf-ev-special',
  '--lf-ev-nuke',
  '--lf-ev-miss',
  '--lf-ev-resupply',
  '--lf-ev-tag',
  '--lf-font-body',
  '--lf-font-heading',
  '--lf-font-numeric',
  '--lf-radius',
  '--lf-glow',
] as const;

/** The Material system roles every palette must also drive, so Material components follow it. */
export const MAT_SYS_TOKENS = [
  '--mat-sys-primary',
  '--mat-sys-on-primary',
  '--mat-sys-surface',
  '--mat-sys-on-surface',
  '--mat-sys-surface-container',
  '--mat-sys-outline-variant',
] as const;

export const PALETTE_KEYS = ['a', 'b', 'c', 'd'] as const;
export type PaletteKey = (typeof PALETTE_KEYS)[number];

export const PALETTE_LABELS: Record<PaletteKey, string> = {
  a: 'A Dense dashboard',
  b: 'B LFstats-like',
  c: 'C Clean Material',
  d: 'D Arena neon',
};
