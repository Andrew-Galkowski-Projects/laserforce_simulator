// The Movement heatmap's pure part, ported from the Django editor's `renderHeatmapOverlay`:
// occupancy -> per-Cell intensity. No Angular, no DOM.
import { CellKey } from '../../api/maps.models';
import { Cell, parseCellKey } from '../map-editor/grid';

/** One hot Cell: its intensity `t` (0..1] and the fill's alpha. */
export interface HeatCell {
  cell: Cell;
  t: number;
  alpha: number;
}

/**
 * Each Cell with positive occupancy, as `t = sqrt(min(1, value / max))` (the square root lifts
 * mid-traffic Cells next to one dominant hot Cell) and alpha `0.45 + 0.45 t`.
 */
export function heatCells(occupancy: Record<CellKey, number>): HeatCell[] {
  const values = Object.values(occupancy);
  const max = Math.max(0, ...values);
  const cells: HeatCell[] = [];
  for (const [key, value] of Object.entries(occupancy)) {
    // Also covers a max at or below zero: then every value is.
    if (!(value > 0)) continue;
    const cell = parseCellKey(key);
    if (!cell) continue;
    const t = Math.sqrt(Math.min(1, value / max));
    cells.push({ cell, t, alpha: 0.45 + 0.45 * t });
  }
  return cells;
}

/**
 * A Cell's colour at intensity `t`: `t` of the high token mixed into the low one (the template's
 * yellow-to-red hue ramp, from the `--lf-map-heat-*` tokens). The alpha is applied separately.
 */
export function heatColour(t: number, low: string, high: string): string {
  return `color-mix(in srgb, ${high} ${Math.round(t * 100)}%, ${low})`;
}
