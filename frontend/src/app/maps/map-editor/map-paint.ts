// Paint helpers shared by the editor modes, ported from the Django editor's `drawGrid`,
// `drawBases`, `drawElevationOverlay` and `drawSpawnCells`. Each paints onto `frame.ctx` with
// `frame.palette`; none clears the canvas (the host does before each draw).
import { BaseType, CellKey, ZoneType } from '../../api/maps.models';
import { Cell, PixelPoint, baseCell, inGrid, parseCellKey } from './grid';
import { MapFrame } from './map-canvas';
import { MapPalette } from './map-palette';

/** The fill of each Cell type (legacy 2 / 3 red / blue zones use the Spawn tints). */
export function cellColour(type: ZoneType, palette: MapPalette): string {
  switch (type) {
    case 0:
      return palette.wallHigh;
    case 2:
      return palette.spawnRed;
    case 3:
      return palette.spawnBlue;
    case 4:
      return palette.wallLow;
    case 5:
      return palette.wallWindowed;
    default:
      return palette.floor;
  }
}

/** Fills one Cell, `inset` px in from its edges. Off-grid Cells are skipped. */
export function fillCell(frame: MapFrame, [r, c]: Cell, colour: string, inset = 0): void {
  if (!inGrid([r, c], frame.rows, frame.cols)) return;
  const s = frame.cellSize;
  frame.ctx.fillStyle = colour;
  frame.ctx.fillRect(c * s + inset, r * s + inset, s - 2 * inset, s - 2 * inset);
}

/** Outlines one Cell (the selected Cell, a Base, the ramp's first Cell). */
export function strokeCell(frame: MapFrame, [r, c]: Cell, colour: string, width = 2): void {
  if (!inGrid([r, c], frame.rows, frame.cols)) return;
  const s = frame.cellSize;
  frame.ctx.strokeStyle = colour;
  frame.ctx.lineWidth = width;
  frame.ctx.strokeRect(c * s + 1, r * s + 1, s - 2, s - 2);
}

/** Writes `label` centred on a Cell, `ratio` of the Cell tall (at least `min` px). */
export function labelCell(
  frame: MapFrame,
  [r, c]: Cell,
  label: string,
  colour: string,
  { ratio = 0.28, min = 7, bold = true }: { ratio?: number; min?: number; bold?: boolean } = {},
): void {
  const s = frame.cellSize;
  const ctx = frame.ctx;
  ctx.font = `${bold ? '700 ' : ''}${Math.max(min, Math.floor(s * ratio))}px ${frame.palette.font}`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillStyle = colour;
  ctx.fillText(label, (c + 0.5) * s, (r + 0.5) * s);
}

/**
 * Fills every Cell by type (windowed walls get their facing letter from `wallMeta`). `colour`
 * overrides the fill, e.g. a faded one for the Sight Lines views.
 */
export function paintCells(
  frame: MapFrame,
  {
    colour = (type: ZoneType) => cellColour(type, frame.palette),
    wallMeta,
  }: {
    colour?: (type: ZoneType, cell: Cell) => string;
    wallMeta?: Record<CellKey, { facing?: string }>;
  } = {},
): void {
  for (let r = 0; r < frame.rows; r++) {
    for (let c = 0; c < frame.cols; c++) {
      const type = frame.zones[r]?.[c] ?? 1;
      fillCell(frame, [r, c], colour(type, [r, c]));
      const facing = type === 5 ? wallMeta?.[`${r},${c}`]?.facing : undefined;
      if (facing) labelCell(frame, [r, c], facing, frame.palette.wallLabel);
    }
  }
}

/** The grid lines between Cells. */
export function paintGridLines(frame: MapFrame): void {
  const { ctx, rows, cols, cellSize: s } = frame;
  ctx.strokeStyle = frame.palette.grid;
  ctx.lineWidth = 0.8;
  ctx.beginPath();
  for (let c = 0; c <= cols; c++) {
    ctx.moveTo(c * s, 0);
    ctx.lineTo(c * s, rows * s);
  }
  for (let r = 0; r <= rows; r++) {
    ctx.moveTo(0, r * s);
    ctx.lineTo(cols * s, r * s);
  }
  ctx.stroke();
}

/** A Base's fill: its Side colour, or the neutral colour. */
export function baseColour(type: BaseType, palette: MapPalette): string {
  if (type === 'red') return palette.sideRed;
  if (type === 'blue') return palette.sideBlue;
  return palette.baseNeutral;
}

/** A Base's badge: R / B for the team Bases (colour-blind safe), N1-N4 for the neutrals. */
export function baseLabel(type: BaseType): string {
  if (type === 'red') return 'R';
  if (type === 'blue') return 'B';
  return `N${type.slice('neutral_'.length)}`;
}

/** Every placed Base: its Cell filled and outlined, with its badge. */
export function paintBases(frame: MapFrame, bases: Partial<Record<BaseType, PixelPoint>>): void {
  for (const [type, pos] of Object.entries(bases) as [BaseType, PixelPoint][]) {
    const cell = baseCell(pos, frame.cellSize);
    if (!inGrid(cell, frame.rows, frame.cols)) continue;
    fillCell(frame, cell, baseColour(type, frame.palette));
    strokeCell(frame, cell, frame.palette.baseOutline);
    // Light text on the Side colours, dark text on the amber neutrals (as the Django editor).
    const text =
      type === 'red' || type === 'blue' ? frame.palette.baseOutline : frame.palette.wallLabel;
    labelCell(frame, cell, baseLabel(type), text, { ratio: 0.35, min: 9 });
  }
}

/** Non-zero Elevation: a fill whose strength grows with the value, and the value itself. */
export function paintElevation(frame: MapFrame, elevation: readonly (readonly number[])[]): void {
  const ctx = frame.ctx;
  for (let r = 0; r < frame.rows; r++) {
    for (let c = 0; c < frame.cols; c++) {
      const value = elevation[r]?.[c];
      if (!value || value <= 0) continue;
      ctx.globalAlpha = Math.min(1, (value / 3) * 0.93 + 0.07);
      fillCell(frame, [r, c], frame.palette.elevation);
      ctx.globalAlpha = 1;
      const label = value % 1 === 0 ? value.toFixed(0) : value.toFixed(1);
      labelCell(frame, [r, c], label, frame.palette.elevationLabel, {
        ratio: 0.22,
        min: 6,
        bold: false,
      });
    }
  }
}

/** Spawn cells, inset in their Side's tint. Malformed keys are skipped. */
export function paintSpawns(
  frame: MapFrame,
  red: Iterable<CellKey>,
  blue: Iterable<CellKey>,
): void {
  for (const [keys, colour] of [
    [red, frame.palette.spawnRed],
    [blue, frame.palette.spawnBlue],
  ] as const) {
    for (const key of keys) {
      const cell = parseCellKey(key);
      if (cell) fillCell(frame, cell, colour, 2);
    }
  }
}
