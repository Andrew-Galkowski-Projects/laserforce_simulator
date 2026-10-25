// The Zones & Bases mode's edits (pure: no Angular, no DOM), ported from the Django map editor's
// inline script (`handleZonesModeClick`, `applyBrushToZoneSelection`, the Elevation helpers and
// Save Configuration). Each function returns a new `ZoneEdit` and never mutates its input.
import { BASE_TYPES, BaseType, Facing, ZoneConfigInput } from '../../api/maps.models';
import {
  Cell,
  baseCell,
  cellKey,
  cellPixel,
  cellsInRect,
  inGrid,
  keyCells,
} from '../map-editor/grid';
import { Bases, ZoneEdit } from '../map-editor/map-editor.store';

/**
 * Places Base `type` on `cell` (moving it if it stood elsewhere); clicking the Cell it already
 * stands on removes it.
 */
export function placeBase(state: ZoneEdit, type: BaseType, cell: Cell, cellSize: number): ZoneEdit {
  const bases = { ...state.bases };
  const placed = bases[type];
  if (placed) {
    const [r, c] = baseCell(placed, cellSize);
    if (r === cell[0] && c === cell[1]) {
      delete bases[type];
      return { ...state, bases };
    }
  }
  bases[type] = cellPixel(cell, cellSize);
  return { ...state, bases };
}

/** Clear All Bases. */
export function clearBases(state: ZoneEdit): ZoneEdit {
  return { ...state, bases: {} };
}

/** A wall brush's Cell type: high wall, floor, low wall, windowed wall. */
export type WallType = 0 | 1 | 4 | 5;

/**
 * Paints `type` on every Cell of `cells` (off-grid Cells skipped). A windowed wall's `wall_meta`
 * becomes `{facing}`; any other type removes the Cell's entry. Bases are untouched.
 */
export function paintWall(
  state: ZoneEdit,
  cells: readonly Cell[],
  type: WallType,
  facing: Facing,
): ZoneEdit {
  const { rows, cols } = shape(state);
  const zones = state.zones.map((row) => [...row]);
  const wallMeta = { ...state.wallMeta };
  for (const cell of cells) {
    if (!inGrid(cell, rows, cols)) continue;
    zones[cell[0]][cell[1]] = type;
    const key = cellKey(cell);
    if (type === 5) wallMeta[key] = { facing };
    else delete wallMeta[key];
  }
  return { ...state, zones, wallMeta };
}

/** Paints Elevation `value` on every Cell of `cells` (off-grid Cells skipped). */
export function paintElevation(state: ZoneEdit, cells: readonly Cell[], value: number): ZoneEdit {
  const { rows, cols } = shape(state);
  const elevation = elevationGrid(state);
  for (const cell of cells) {
    if (inGrid(cell, rows, cols)) elevation[cell[0]][cell[1]] = value;
  }
  return { ...state, elevation };
}

/**
 * The Ramp tool: fills the rectangle between Cells `a` and `b` by projecting each Cell onto the
 * line from `a` to `b`, interpolating between their Elevations, rounded to 2 dp.
 */
export function rampElevation(state: ZoneEdit, a: Cell, b: Cell): ZoneEdit {
  const { rows, cols } = shape(state);
  const elevation = elevationGrid(state);
  const start = inGrid(a, rows, cols) ? elevation[a[0]][a[1]] : 0;
  const end = inGrid(b, rows, cols) ? elevation[b[0]][b[1]] : 0;
  const [dr, dc] = [b[0] - a[0], b[1] - a[1]];
  const distSq = dr * dr + dc * dc;
  for (const cell of cellsInRect(a, b)) {
    if (!inGrid(cell, rows, cols)) continue;
    const [r, c] = cell;
    const t =
      distSq === 0 ? 1 : Math.max(0, Math.min(1, ((r - a[0]) * dr + (c - a[1]) * dc) / distSq));
    elevation[r][c] = parseFloat((start + (end - start) * t).toFixed(2));
  }
  return { ...state, elevation };
}

/** A Spawn brush: paint a Side's Spawn cells, or erase both. */
export type SpawnBrush = 'red' | 'blue' | 'erase';

/**
 * A Spawn brush click on `cell`: Erase removes it from both Sides; a Side's brush takes it from
 * the other Side and toggles it in its own. Marks the spawns edited.
 */
export function clickSpawn(state: ZoneEdit, cell: Cell, brush: SpawnBrush): ZoneEdit {
  const key = cellKey(cell);
  const red = new Set(state.redSpawn);
  const blue = new Set(state.blueSpawn);
  if (brush === 'erase') {
    red.delete(key);
    blue.delete(key);
  } else {
    const [own, other] = brush === 'red' ? [red, blue] : [blue, red];
    other.delete(key);
    if (own.has(key)) own.delete(key);
    else own.add(key);
  }
  return { ...state, redSpawn: red, blueSpawn: blue, spawnEdited: true };
}

/**
 * A Bulk Edit rectangle with a Spawn brush: Erase removes every Cell from both Sides; a Side's
 * brush adds every Cell but high walls to its Side (no toggle). Marks the spawns edited.
 */
export function paintSpawns(state: ZoneEdit, cells: readonly Cell[], brush: SpawnBrush): ZoneEdit {
  const red = new Set(state.redSpawn);
  const blue = new Set(state.blueSpawn);
  for (const cell of cells) {
    const key = cellKey(cell);
    if (brush === 'erase') {
      red.delete(key);
      blue.delete(key);
    } else if (state.zones[cell[0]]?.[cell[1]] !== 0) {
      const [own, other] = brush === 'red' ? [red, blue] : [blue, red];
      other.delete(key);
      own.add(key);
    }
  }
  return { ...state, redSpawn: red, blueSpawn: blue, spawnEdited: true };
}

/**
 * The Save Configuration body: the Zone size, Cell types and Bases always; `wall_meta` only when
 * non-empty; Elevation whenever there is a grid (loaded or painted); the Spawn cells only once
 * edited (unedited spawns keep being auto-generated).
 */
export function saveBody(state: ZoneEdit, zoneSize: number): ZoneConfigInput {
  const body: ZoneConfigInput = {
    zone_size: zoneSize,
    zones: state.zones,
    bases: BASE_TYPES.flatMap((type) => {
      const pos = state.bases[type];
      return pos ? [{ type, x_px: pos.x_px, y_px: pos.y_px }] : [];
    }),
  };
  if (Object.keys(state.wallMeta).length) body.wall_meta = state.wallMeta;
  if (state.elevation) body.elevation = state.elevation;
  if (state.spawnEdited) {
    body.red_spawn = keyCells(state.redSpawn);
    body.blue_spawn = keyCells(state.blueSpawn);
  }
  return body;
}

/** What Save Configuration shows after a save: success, or UXP-10's missing-team-Base warning. */
export interface SaveMessage {
  kind: 'success' | 'warning';
  text: string;
}

/** The message for a save at `zoneSize` with `bases` placed (the template's wording). */
export function saveMessage(zoneSize: number, bases: Bases): SaveMessage {
  const count = Object.keys(bases).length;
  const missing = (['red', 'blue'] as const).filter((side) => !bases[side]);
  if (!missing.length) {
    return {
      kind: 'success',
      text: `Configuration saved! (${zoneSize}px zones, ${count} base(s))`,
    };
  }
  const place = missing.length === 2 ? 'a red and a blue base' : `a ${missing[0]} base`;
  return {
    kind: 'warning',
    text:
      `Configuration saved (${zoneSize}px zones, ${count} base(s)) - place ${place} before using ` +
      "this map in a match. It won't be offered in match, round or League map pickers until then.",
  };
}

/** A copy of the area's Elevation grid, or all zeros when it has none or another shape. */
function elevationGrid(state: ZoneEdit): number[][] {
  const { rows, cols } = shape(state);
  const current = state.elevation;
  if (current && current.length === rows && current.every((row) => row.length === cols)) {
    return current.map((row) => [...row]);
  }
  return Array.from({ length: rows }, () => new Array<number>(cols).fill(0));
}

/** The area's grid size, from its Cell types. */
function shape(state: ZoneEdit): { rows: number; cols: number } {
  return { rows: state.zones.length, cols: state.zones[0]?.length ?? 0 };
}
