/**
 * The `--lf-*` token contract (ADR-0043). `src/styles/_tokens.scss` defines every one of these on
 * `:root`, light by default and dark under `@media (prefers-color-scheme: dark)`. Components read
 * colour, fonts, radius and glow from nothing else: never a hex value.
 *
 * - Side: put `data-lf-side="red"` / `data-lf-side="blue"` on every team container. It points
 *   `--side`, `--side-text`, `--side-bg` and `--side-dim` at that Side's tokens (`_sides.scss`);
 *   nothing inside picks red or blue itself.
 * - Glow: `--lf-glow` is a shadow geometry. Use it with a colour token:
 *   `box-shadow: var(--lf-glow) var(--side);`.
 * - Event types: `--lf-ev-<type>`, one per event type of the round log, and one per **Event
 *   group** of the Event log (`matches/shared/event-groups.ts`).
 * - Derived tints are fine: `color-mix(in srgb, var(--side) 20%, transparent)`.
 */
export const LF_COLOUR_TOKENS = [
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
  '--lf-ev-locking',
  '--lf-ev-base',
  '--lf-ev-movement',
] as const;

/** Fonts and shape: the same in light and dark, so they are declared once on `:root`. */
export const LF_SHARED_TOKENS = [
  '--lf-font-body',
  '--lf-font-heading',
  '--lf-font-numeric',
  '--lf-radius',
  '--lf-glow',
] as const;

/** The full contract: every token a component may read. */
export const LF_TOKENS = [...LF_COLOUR_TOKENS, ...LF_SHARED_TOKENS] as const;

/** The aliases a `[data-lf-side]` container sets (see `_sides.scss`). */
export const SIDE_ALIASES = ['--side', '--side-text', '--side-bg', '--side-dim'] as const;
