// ArenaMap shapes, snake_case exactly as core/serializers.py and core/forms.py
// send / bind them (no mapping layer). Maps are shared reference data: every
// Account sees, uploads and deletes every map (ADR-0038).
import { ListParams } from './api-client';

/** `ArenaMapSerializer`: list rows, `GET maps/{id}/` and the upload response. */
export interface ArenaMap {
  id: number;
  name: string;
  /** Absolute URL of the uploaded image (null without a request context). */
  image: string | null;
  is_default: boolean;
  img_width: number | null;
  img_height: number | null;
  /** ISO 8601 datetime. */
  created_at: string;
  /** True when the map has a confirmed zone config (usable for simulation). */
  has_confirmed_config: boolean;
  /** The latest confirmed config's Zone size in px; null exactly when `has_confirmed_config` is false. */
  confirmed_zone_size: number | null;
}

/** `MapUploadForm` fields, sent as multipart `name` + `image`. */
export interface MapUploadInput {
  /** At most 100 characters. */
  name: string;
  image: File;
}

// --- Editor actions (ANG-01c endpoints; field names from core/services.py) ---------------------

/** A Cell as the API sends it: `[row, col]`. */
export type CellPair = [row: number, col: number];

/** A Cell's `"r,c"` key, as `sight_data`, `wall_meta` and `cell_occupancy` key them. */
export type CellKey = string;

/**
 * A Cell type in the `zones` grid: 0 high wall, 1 floor, 4 low wall, 5 windowed wall
 * (2 / 3 are legacy red / blue zones, drawn as before but never produced).
 */
export type ZoneType = 0 | 1 | 2 | 3 | 4 | 5;

/** A windowed wall's aperture direction. */
export type Facing = 'N' | 'S' | 'E' | 'W';

/** One `wall_meta` entry: a windowed wall's `facing`, and a `height` set only through JSON. */
export interface WallMetaEntry {
  facing?: Facing;
  height?: number;
}

/** `wall_meta`, keyed `"r,c"`. */
export type WallMeta = Record<CellKey, WallMetaEntry>;

/** The six Base types, in the editor's button order. */
export const BASE_TYPES = [
  'red',
  'blue',
  'neutral_1',
  'neutral_2',
  'neutral_3',
  'neutral_4',
] as const;
export type BaseType = (typeof BASE_TYPES)[number];

/** A `MapBaseConfig`: the Base's centre in image pixels. */
export interface MapBase {
  type: BaseType;
  x_px: number;
  y_px: number;
}

/** `blocked_edges_grid[r][c]`: is the edge to the east / south Cell blocked. */
export interface BlockedEdges {
  e: boolean;
  s: boolean;
}

/** `GET maps/{id}/zones/?zone_size=`: `detect_zones_with_overlay`. */
export interface MapZones {
  rows: number;
  cols: number;
  /** The Zone size in px. */
  cell_size: number;
  img_width: number;
  img_height: number;
  zones: ZoneType[][];
  /** `"r,c,e"` / `"r,c,s"` -> true (legacy form). */
  blocked_edges: Record<string, boolean>;
  blocked_edges_grid: BlockedEdges[][];
  /** Present when the confirmed config has this Zone size and stores them. */
  wall_meta?: WallMeta;
  elevation?: number[][];
}

/** `GET maps/{id}/zone-config/` and the PUT response: `get_zone_config`. */
export interface ZoneConfig {
  /** null when the map has no confirmed config. */
  zone_size: number | null;
  zones: ZoneType[][] | null;
  wall_meta: WallMeta;
  elevation: number[][] | null;
  red_spawn: CellPair[];
  blue_spawn: CellPair[];
  spawn_user_edited: boolean;
  bases: MapBase[];
}

/**
 * `PUT maps/{id}/zone-config/` (`ZoneConfigForm`). Absent `zones` auto-detects, absent `bases`
 * deletes every Base, absent `wall_meta` drops it, absent `elevation` / spawns carry forward.
 */
export interface ZoneConfigInput {
  zone_size: number;
  zones?: ZoneType[][];
  wall_meta?: WallMeta;
  elevation?: number[][];
  red_spawn?: CellPair[];
  blue_spawn?: CellPair[];
  bases?: MapBase[];
}

/** `GET maps/{id}/sight-lines/?zone_size=` and the PUT / PATCH response. */
export interface SightLines {
  zone_size: number;
  /** `"r,c"` -> the keys it can see; null when never computed for this Zone size. */
  sight_data: Record<CellKey, CellKey[]> | null;
  /** Every Base type -> the Cells that can tag it. */
  base_sights: Record<BaseType, CellPair[]>;
}

/** `PUT` (replace, the first batch) / `PATCH` (merge, the rest) `maps/{id}/sight-lines/`. */
export interface SightLinesInput {
  zone_size: number;
  sight_data: Record<CellKey, CellKey[]>;
  /** Upserted per Base type; other types untouched. */
  base_sights: Partial<Record<BaseType, CellPair[]>>;
}

/** `POST maps/{id}/sight-lines/compute/`'s optional unsaved Spawn edits. */
export interface ComputeSpawns {
  red_spawn?: CellPair[];
  blue_spawn?: CellPair[];
}

/** `POST maps/{id}/sight-lines/compute/` response. */
export interface ComputedSightLines {
  sight_data: Record<CellKey, CellKey[]>;
  zone_size: number;
}

/** `GET maps/{id}/ranked-cells/?zone_size=`: high to low LOS count (`[]` when not computed). */
export interface RankedCells {
  ranked_cells: CellPair[];
}

/** `GET` / `PUT maps/{id}/strong-spots/`: the Heavy Strong spots. */
export interface StrongSpots {
  cells: CellPair[];
}

/** `PUT maps/{id}/strong-spots/` (`StrongSpotsForm`). */
export interface StrongSpotsInput {
  zone_size: number;
  cells: CellPair[];
}

/** `GET maps/{id}/spawn-cells/`: from the confirmed config (per config, not per Zone size). */
export interface SpawnCells {
  red_spawn: CellPair[];
  blue_spawn: CellPair[];
}

/** `GET maps/{id}/heatmap/?zone_size=&team_color=`: `aggregate_map_heatmap`. */
export interface MapHeatmap {
  /** `"r,c"` -> ticks, summed over the requester's visible rounds (zero sums dropped). */
  cell_occupancy: Record<CellKey, number>;
  zone_size: number;
  /** From the confirmed config at this Zone size; 0 without one. */
  rows: number;
  cols: number;
  round_count: number;
}

/** `ArenaMapFilterSet` plus `ordering` (`name`, `created_at`, `id`; `-` descends). */
export interface MapListParams extends ListParams {
  is_default?: boolean;
  has_confirmed_config?: boolean;
  /** A confirmed config plus a red and a blue base: the maps a simulation accepts (ANG-06-14). */
  simulation_ready?: boolean;
  ordering?: string;
}
