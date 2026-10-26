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
/**
 * The map editor's canvas colours (ANG-06h): the Django editor's rgba literals as semantic
 * tokens. They carry their own alpha, since the canvas paints them over the processed image.
 * Bases use the Side tokens (`--lf-side-red` / `--lf-side-blue`) and `--lf-map-base-neutral`.
 */
export const LF_MAP_TOKENS = [
  // Cell types and the grid.
  '--lf-map-floor',
  '--lf-map-wall-high',
  '--lf-map-wall-low',
  '--lf-map-wall-windowed',
  /** A windowed wall's N / S / E / W letter. */
  '--lf-map-wall-label',
  '--lf-map-grid',
  // Elevation: the Cell fill and its value.
  '--lf-map-elevation',
  '--lf-map-elevation-label',
  // Spawn cells, Bases, the ramp tool's first Cell.
  '--lf-map-spawn-red',
  '--lf-map-spawn-blue',
  '--lf-map-base-neutral',
  '--lf-map-base-outline',
  '--lf-map-ramp-anchor',
  // Sight Lines: the selected Cell, what it sees / does not see, the drag rectangle.
  '--lf-map-los-selected',
  '--lf-map-los-visible',
  '--lf-map-los-blocked',
  '--lf-map-los-drag',
  '--lf-map-base-sight',
  /** The high-LOS ranking, from the top-ranked Cell to the last. */
  '--lf-map-high-los',
  '--lf-map-high-los-low',
  '--lf-map-strong-spot',
  /** The Movement heatmap ramp, opaque (the mode sets the alpha): cold to hot. */
  '--lf-map-heat-low',
  '--lf-map-heat-high',
] as const;

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
  // Player detail's Stat bars (ANG-06-8a), by tier: 80+, 60+, 40+, below 40. The value always
  // shows beside the bar, so the colour is never the only signal.
  '--lf-rating-elite',
  '--lf-rating-good',
  '--lf-rating-fair',
  '--lf-rating-poor',
  ...LF_MAP_TOKENS,
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
