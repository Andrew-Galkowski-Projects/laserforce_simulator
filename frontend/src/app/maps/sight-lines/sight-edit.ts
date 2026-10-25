// The Sight Lines mode's pure edits (no Angular, no DOM), ported from the Django editor's
// `renderSightLines`, `handleZoneSightClick`, `bulkToggleSightLines`, `handleBaseSightClick`,
// `handleHeavySpotClick`, `bulkToggleHeavySpots` and the Save Sight Lines batching.
//
// A high wall (type 0) is the only Cell the Sight Lines views treat as a wall: it cannot be
// selected, toggled, given a Base sight or a Strong spot, and a drag rectangle skips it.
import { BaseType, CellKey, CellPair, SightLinesInput, ZoneType } from '../../api/maps.models';
import { Cell, cellKey, cellsInRect, keyCells } from '../map-editor/grid';

/** `"r,c"` -> the keys it sees. */
export type SightData = Record<CellKey, CellKey[]>;

/** Whether `cell` is a high wall (off-grid Cells count as walls). */
export function isSightWall(zones: readonly (readonly ZoneType[])[], [r, c]: Cell): boolean {
  const type = zones[r]?.[c];
  return type === undefined || type === 0;
}

/**
 * The selected Cell's visible Cells (its Sight lines) and blocked Cells (every other Cell that
 * is not a high wall), as the Zone view colours them.
 */
export function sightOf(
  sightData: SightData,
  zones: readonly (readonly ZoneType[])[],
  selected: Cell,
): { visible: Set<CellKey>; blocked: Set<CellKey> } {
  const self = cellKey(selected);
  const visible = new Set(sightData[self] ?? []);
  const blocked = new Set<CellKey>();
  zones.forEach((row, r) =>
    row.forEach((_type, c) => {
      const key = cellKey([r, c]);
      if (key !== self && !visible.has(key) && !isSightWall(zones, [r, c])) blocked.add(key);
    }),
  );
  return { visible, blocked };
}

/**
 * Zone view's click on `target` with `selected` selected: unlinks the pair both ways when the
 * selection sees `target`, else links it both ways. A high wall or the selection itself leaves
 * `sightData` as it is (the same object).
 */
export function toggleSight(
  sightData: SightData,
  zones: readonly (readonly ZoneType[])[],
  selected: Cell,
  target: Cell,
): SightData {
  const self = cellKey(selected);
  const other = cellKey(target);
  if (other === self || isSightWall(zones, target)) return sightData;
  const next = { ...sightData };
  if ((sightData[self] ?? []).includes(other)) unlink(next, self, other);
  else link(next, self, other);
  return next;
}

/** The keys of the drag rectangle with corners `a` and `b`, high walls skipped. */
export function rectKeys(zones: readonly (readonly ZoneType[])[], a: Cell, b: Cell): Set<CellKey> {
  return new Set(
    cellsInRect(a, b)
      .filter((cell) => !isSightWall(zones, cell))
      .map(cellKey),
  );
}

/**
 * Zone view's Bulk Edit release: when the selection sees any of the dragged `keys`, unlinks
 * those (both ways); else links every one of them (both ways). The selection's own key is
 * ignored; a drag of nothing else leaves `sightData` as it is.
 */
export function bulkToggleSight(
  sightData: SightData,
  selected: Cell,
  keys: Iterable<CellKey>,
): SightData {
  const self = cellKey(selected);
  const targets = [...keys].filter((k) => k !== self);
  if (!targets.length) return sightData;
  const seen = sightData[self] ?? [];
  const linked = targets.filter((k) => seen.includes(k));
  const next = { ...sightData };
  if (linked.length) linked.forEach((k) => unlink(next, self, k));
  else targets.forEach((k) => link(next, self, k));
  return next;
}

/** The most `sight_data` keys one Save Sight Lines request carries (the request size limit). */
export const SIGHT_BATCH_SIZE = 100;

/**
 * Save Sight Lines' requests, in order: `sight_data` in batches of 100 keys (at least one
 * batch), the Base sights in the first only. The first is sent as a PUT (replace), the rest as
 * PATCHes (merge).
 */
export function sightBatches(
  zoneSize: number,
  sightData: SightData,
  baseSights: Record<BaseType, CellPair[]>,
): SightLinesInput[] {
  const entries = Object.entries(sightData);
  const count = Math.max(1, Math.ceil(entries.length / SIGHT_BATCH_SIZE));
  return Array.from({ length: count }, (_v, i) => ({
    zone_size: zoneSize,
    sight_data: Object.fromEntries(entries.slice(i * SIGHT_BATCH_SIZE, (i + 1) * SIGHT_BATCH_SIZE)),
    base_sights: i === 0 ? baseSights : {},
  }));
}

/** A Base view's click: adds `cell` to the Cells that can tag Base `type`, or removes it. */
export function toggleBaseSight(
  baseSights: Record<BaseType, CellPair[]>,
  zones: readonly (readonly ZoneType[])[],
  type: BaseType,
  cell: Cell,
): Record<BaseType, CellPair[]> {
  if (isSightWall(zones, cell)) return baseSights;
  return { ...baseSights, [type]: toggleIn(baseSights[type] ?? [], cell) };
}

/** Heavy Strong spots view's click: adds `cell` as a Strong spot, or removes it. */
export function toggleStrongSpot(
  spots: CellPair[],
  zones: readonly (readonly ZoneType[])[],
  cell: Cell,
): CellPair[] {
  return isSightWall(zones, cell) ? spots : toggleIn(spots, cell);
}

/**
 * Heavy Strong spots view's Bulk Edit release: when any dragged Cell is a Strong spot, removes
 * those; else adds every dragged Cell. An empty drag leaves `spots` as it is.
 */
export function bulkToggleStrongSpots(spots: CellPair[], keys: Iterable<CellKey>): CellPair[] {
  const dragged = new Set(keys);
  if (!dragged.size) return spots;
  const on = spots.filter((cell) => dragged.has(cellKey(cell)));
  if (on.length) return spots.filter((cell) => !dragged.has(cellKey(cell)));
  return [...spots, ...keyCells(dragged)];
}

/** `cells` without `cell` when it holds it, else with `cell` appended. */
function toggleIn(cells: CellPair[], cell: Cell): CellPair[] {
  const key = cellKey(cell);
  const rest = cells.filter((c) => cellKey(c) !== key);
  return rest.length < cells.length ? rest : [...cells, [cell[0], cell[1]]];
}

/** Adds `a` <-> `b` to `data` (a copy the caller owns), never doubling a key. */
function link(data: SightData, a: CellKey, b: CellKey): void {
  for (const [from, to] of [
    [a, b],
    [b, a],
  ]) {
    const seen = data[from] ?? [];
    if (!seen.includes(to)) data[from] = [...seen, to];
  }
}

/** Removes `a` <-> `b` from `data` (a copy the caller owns), dropping emptied Cells. */
function unlink(data: SightData, a: CellKey, b: CellKey): void {
  for (const [from, to] of [
    [a, b],
    [b, a],
  ]) {
    const seen = data[from];
    if (!seen) continue;
    const rest = seen.filter((k) => k !== to);
    if (rest.length) data[from] = rest;
    else delete data[from];
  }
}
