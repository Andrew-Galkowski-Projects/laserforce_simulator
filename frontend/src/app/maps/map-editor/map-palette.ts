// The editor canvas's colours, read from the `--lf-map-*` tokens at draw time (a canvas cannot
// read a CSS variable): the ANG-06g probe pattern. No colour literal lives here.
import { readToken } from '../../shared/read-token';

/** Every colour the editor canvas paints with, plus the body font for Cell labels. */
export interface MapPalette {
  floor: string;
  wallHigh: string;
  wallLow: string;
  wallWindowed: string;
  wallLabel: string;
  grid: string;
  elevation: string;
  elevationLabel: string;
  spawnRed: string;
  spawnBlue: string;
  /** The Side colours (`--lf-side-red` / `--lf-side-blue`): the red / blue Bases. */
  sideRed: string;
  sideBlue: string;
  baseNeutral: string;
  baseOutline: string;
  rampAnchor: string;
  losSelected: string;
  losVisible: string;
  losBlocked: string;
  losDrag: string;
  baseSight: string;
  highLos: string;
  highLosLow: string;
  strongSpot: string;
  heatLow: string;
  heatHigh: string;
  /** `--lf-font-body`, for canvas text. */
  font: string;
}

/** Each palette entry's token. */
export const MAP_PALETTE_TOKENS: Record<keyof MapPalette, string> = {
  floor: '--lf-map-floor',
  wallHigh: '--lf-map-wall-high',
  wallLow: '--lf-map-wall-low',
  wallWindowed: '--lf-map-wall-windowed',
  wallLabel: '--lf-map-wall-label',
  grid: '--lf-map-grid',
  elevation: '--lf-map-elevation',
  elevationLabel: '--lf-map-elevation-label',
  spawnRed: '--lf-map-spawn-red',
  spawnBlue: '--lf-map-spawn-blue',
  sideRed: '--lf-side-red',
  sideBlue: '--lf-side-blue',
  baseNeutral: '--lf-map-base-neutral',
  baseOutline: '--lf-map-base-outline',
  rampAnchor: '--lf-map-ramp-anchor',
  losSelected: '--lf-map-los-selected',
  losVisible: '--lf-map-los-visible',
  losBlocked: '--lf-map-los-blocked',
  losDrag: '--lf-map-los-drag',
  baseSight: '--lf-map-base-sight',
  highLos: '--lf-map-high-los',
  highLosLow: '--lf-map-high-los-low',
  strongSpot: '--lf-map-strong-spot',
  heatLow: '--lf-map-heat-low',
  heatHigh: '--lf-map-heat-high',
  font: '--lf-font-body',
};

/** The palette as the document root's tokens are now (light or dark). */
export function readMapPalette(root: Element = document.documentElement): MapPalette {
  const entries = Object.entries(MAP_PALETTE_TOKENS).map(([key, token]) => [
    key,
    readToken(token, root),
  ]);
  return Object.fromEntries(entries) as MapPalette;
}
