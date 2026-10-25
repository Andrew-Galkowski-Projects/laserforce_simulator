// The Cell grid's geometry, shared by every editor mode (pure: no Angular, no DOM). Ported from
// the Django map editor's inline script (`getCellFromClick`, `drawBases`, the drag rectangles).
import { CellKey, CellPair } from '../../api/maps.models';

/** A Cell: `[row, col]`, the API's own pair shape. */
export type Cell = CellPair;

/** The on-screen box of the canvas (`getBoundingClientRect()`), in CSS pixels. */
export interface ScreenRect {
  left: number;
  top: number;
  width: number;
  height: number;
}

/** The canvas's drawing-buffer size: the processed image's natural size. */
export interface CanvasSize {
  width: number;
  height: number;
}

/** A Base's position in image pixels. */
export interface PixelPoint {
  x_px: number;
  y_px: number;
}

/**
 * The Cell under a pointer at (`clientX`, `clientY`): the pointer is scaled from the canvas's
 * on-screen size to image pixels, then divided by the Zone size. Off-image pointers give
 * out-of-range (possibly negative) indices; check them with `inGrid`. `null` while the canvas
 * has no on-screen size.
 */
export function pointerToCell(
  clientX: number,
  clientY: number,
  rect: ScreenRect,
  canvas: CanvasSize,
  cellSize: number,
): Cell | null {
  if (rect.width <= 0 || rect.height <= 0 || cellSize <= 0) return null;
  const x = ((clientX - rect.left) * canvas.width) / rect.width;
  const y = ((clientY - rect.top) * canvas.height) / rect.height;
  return [Math.floor(y / cellSize), Math.floor(x / cellSize)];
}

/** Whether `cell` lies on a `rows` x `cols` grid. */
export function inGrid([row, col]: Cell, rows: number, cols: number): boolean {
  return row >= 0 && col >= 0 && row < rows && col < cols;
}

/** The Cell a Base stands on. */
export function baseCell(base: PixelPoint, cellSize: number): Cell {
  return [Math.floor(base.y_px / cellSize), Math.floor(base.x_px / cellSize)];
}

/** Where a Base placed on `cell` is stored: the Cell's centre, floored to whole pixels. */
export function cellPixel([row, col]: Cell, cellSize: number): PixelPoint {
  return { x_px: Math.floor((col + 0.5) * cellSize), y_px: Math.floor((row + 0.5) * cellSize) };
}

/** Every Cell of the rectangle with corners `a` and `b` (any corner order), row by row. */
export function cellsInRect(a: Cell, b: Cell): Cell[] {
  const cells: Cell[] = [];
  const [r0, r1] = [Math.min(a[0], b[0]), Math.max(a[0], b[0])];
  const [c0, c1] = [Math.min(a[1], b[1]), Math.max(a[1], b[1])];
  for (let r = r0; r <= r1; r++) {
    for (let c = c0; c <= c1; c++) cells.push([r, c]);
  }
  return cells;
}

/** A Cell's `"r,c"` key. */
export function cellKey([row, col]: Cell): CellKey {
  return `${row},${col}`;
}

/** The Cell of an `"r,c"` key; `null` for anything but two non-negative integers. */
export function parseCellKey(key: CellKey): Cell | null {
  const match = /^(\d+),(\d+)$/.exec(key);
  return match ? [Number(match[1]), Number(match[2])] : null;
}

/** The keys of `cells`, as a set. */
export function cellKeys(cells: Iterable<Cell>): Set<CellKey> {
  return new Set([...cells].map(cellKey));
}

/** The Cells of `keys`, in order; malformed keys are dropped. */
export function keyCells(keys: Iterable<CellKey>): Cell[] {
  return [...keys].map(parseCellKey).filter((cell): cell is Cell => cell !== null);
}
