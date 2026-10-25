// Map-editor spec fixtures (ANG-06h). Imported only by *.spec.ts files; the mode tickets may use
// them too.
import { TestBed } from '@angular/core/testing';
import { HttpTestingController, TestRequest } from '@angular/common/http/testing';
import { environment } from '../../../environments/environment';
import {
  ArenaMap,
  MapHeatmap,
  MapZones,
  SightLines,
  SpawnCells,
  ZoneConfig,
} from '../../api/maps.models';

export const MAPS_URL = `${environment.apiBaseUrl}/maps`;

/** A configured 3 x 4 map at 40 px (160 x 120 image). */
export function editorMap(overrides: Partial<ArenaMap> = {}): ArenaMap {
  return {
    id: 4,
    name: 'Warehouse',
    image: 'http://localhost:8000/media/maps/warehouse.png',
    is_default: false,
    img_width: 160,
    img_height: 120,
    created_at: '2026-10-21T12:00:00Z',
    has_confirmed_config: true,
    confirmed_zone_size: 40,
    ...overrides,
  };
}

/** The `zones/` grid for `zoneSize` over a 160 x 120 image. */
export function mapZones(zoneSize = 40, overrides: Partial<MapZones> = {}): MapZones {
  const rows = Math.ceil(120 / zoneSize);
  const cols = Math.ceil(160 / zoneSize);
  const zones = Array.from({ length: rows }, (_r, r) =>
    Array.from({ length: cols }, (_c, c) => (r === 0 && c === 0 ? 0 : 1) as 0 | 1),
  );
  return {
    rows,
    cols,
    cell_size: zoneSize,
    img_width: 160,
    img_height: 120,
    zones,
    blocked_edges: {},
    blocked_edges_grid: zones.map((row) => row.map(() => ({ e: false, s: false }))),
    ...overrides,
  };
}

export function zoneConfig(overrides: Partial<ZoneConfig> = {}): ZoneConfig {
  return {
    zone_size: 40,
    zones: mapZones().zones,
    wall_meta: {},
    elevation: null,
    red_spawn: [],
    blue_spawn: [],
    spawn_user_edited: false,
    bases: [
      { type: 'blue', x_px: 140, y_px: 100 },
      { type: 'red', x_px: 20, y_px: 60 },
    ],
    ...overrides,
  };
}

export const SPAWNS: SpawnCells = { red_spawn: [[1, 0]], blue_spawn: [[2, 3]] };

export function sightLines(zoneSize = 40, overrides: Partial<SightLines> = {}): SightLines {
  return {
    zone_size: zoneSize,
    sight_data: { '1,1': ['1,2'], '1,2': ['1,1'] },
    base_sights: {
      red: [[1, 1]],
      blue: [],
      neutral_1: [],
      neutral_2: [],
      neutral_3: [],
      neutral_4: [],
    },
    ...overrides,
  };
}

export function heatmap(overrides: Partial<MapHeatmap> = {}): MapHeatmap {
  return {
    cell_occupancy: { '1,1': 30 },
    zone_size: 40,
    rows: 3,
    cols: 4,
    round_count: 2,
    ...overrides,
  };
}

const http = (): HttpTestingController => TestBed.inject(HttpTestingController);

/** The one pending request to `maps/<id>/<path>` (path `''` for the detail). */
export function expectMapRequest(id: number, path: string, method = 'GET'): TestRequest {
  const url = `${MAPS_URL}/${id}/${path}`;
  return http().expectOne((r) => r.url === url && r.method === method);
}

/** Answers `req` with `data` in the envelope. */
export function flushData(req: TestRequest, data: unknown): void {
  req.flush({ data, meta: {} });
}

/** Answers `req` with an envelope error. */
export function flushError(req: TestRequest, status: number, code: string, message: string): void {
  req.flush(
    { errors: [{ code, message, field: null }], meta: {} },
    { status, statusText: String(status) },
  );
}

/**
 * Answers the editor's opening loads for `map`: the detail and zone-config, then the grid and the
 * Spawn cells at the confirmed Zone size (or 50).
 */
export function answerEditorLoads(
  map: ArenaMap = editorMap(),
  config: ZoneConfig = zoneConfig(),
  zones?: MapZones,
): void {
  flushData(expectMapRequest(map.id, ''), map);
  flushData(expectMapRequest(map.id, 'zone-config/'), config);
  answerGridLoads(map.id, zones ?? mapZones(map.confirmed_zone_size ?? 50));
}

/** Each mode's own GETs, answered by `releaseModeLoads` (path suffix -> data). */
const MODE_LOADS: [string, () => unknown][] = [
  ['/sight-lines/', () => sightLines()],
  ['/ranked-cells/', () => ({ ranked_cells: [] })],
  ['/strong-spots/', () => ({ cells: [] })],
  ['/heatmap/', () => heatmap()],
];

/**
 * Answers every pending Sight Lines / Heatmap mode GET. A spec that switches to a mode it is not
 * testing (the shell's mode toggle) calls this before `verify()`, so a mode that loads its data
 * on entry doesn't break it.
 */
export function releaseModeLoads(): void {
  for (const [suffix, data] of MODE_LOADS) {
    http()
      .match((r) => r.method === 'GET' && r.url.startsWith(MAPS_URL) && r.url.endsWith(suffix))
      .forEach((req) => {
        if (!req.cancelled) flushData(req, data());
      });
  }
}

/** Answers one grid load: `zones/?zone_size=` and `spawn-cells/`. */
export function answerGridLoads(id: number, zones: MapZones, spawns: SpawnCells = SPAWNS): void {
  const req = expectMapRequest(id, 'zones/');
  // No `expect` here: the app build compiles this file too (only *.spec.ts is excluded).
  const sent = req.request.params.get('zone_size');
  if (sent !== String(zones.cell_size)) {
    throw new Error(`zones/ asked for zone_size=${sent}, expected ${zones.cell_size}`);
  }
  flushData(req, zones);
  flushData(expectMapRequest(id, 'spawn-cells/'), spawns);
}
