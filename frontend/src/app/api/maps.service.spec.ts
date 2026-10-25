import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../environments/environment';
import { authInterceptor } from '../auth/auth.interceptor';
import { ApiError } from './api-client';
import {
  ArenaMap,
  ComputedSightLines,
  MapHeatmap,
  MapZones,
  SightLines,
  SightLinesInput,
  SpawnCells,
  StrongSpotsInput,
  ZoneConfig,
  ZoneConfigInput,
} from './maps.models';
import { MapsService } from './maps.service';

const BASE = environment.apiBaseUrl;

const MAP: ArenaMap = {
  id: 2,
  name: 'Warehouse',
  image: 'http://localhost:8000/media/maps/warehouse.png',
  is_default: false,
  img_width: 800,
  img_height: 600,
  created_at: '2026-10-21T12:00:00Z',
  has_confirmed_config: true,
  confirmed_zone_size: 40,
};

describe('MapsService', () => {
  let maps: MapsService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(withInterceptors([authInterceptor])),
        provideHttpClientTesting(),
      ],
    });
    maps = TestBed.inject(MapsService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('lists Maps with filters and ordering', async () => {
    const result = firstValueFrom(
      maps.list({ has_confirmed_config: true, is_default: false, ordering: '-created_at' }),
    );
    const req = http.expectOne((r) => r.url === `${BASE}/maps/`);
    expect(req.request.method).toBe('GET');
    expect(req.request.params.get('has_confirmed_config')).toBe('true');
    expect(req.request.params.get('is_default')).toBe('false');
    expect(req.request.params.get('ordering')).toBe('-created_at');
    const meta = { page: 1, page_size: 20, count: 1, total_pages: 1 };
    req.flush({ data: [MAP], meta });
    expect(await result).toEqual({ items: [MAP], meta });
  });

  it('gets one Map', async () => {
    const result = firstValueFrom(maps.get(2));
    const req = http.expectOne(`${BASE}/maps/2/`);
    expect(req.request.method).toBe('GET');
    req.flush({ data: MAP, meta: {} });
    expect(await result).toEqual(MAP);
  });

  it("fetches a Map's processed image as a raw Blob", async () => {
    const result = firstValueFrom(maps.processedImage(7));
    const req = http.expectOne(`${BASE}/maps/7/processed-image/`);
    expect(req.request.method).toBe('GET');
    expect(req.request.responseType).toBe('blob');
    const png = new Blob([new Uint8Array([137, 80, 78, 71])], { type: 'image/png' });
    req.flush(png);
    expect(await result).toBe(png);
  });

  it("fetches a Map's original image as a raw Blob", async () => {
    const result = firstValueFrom(maps.image(7));
    const req = http.expectOne(`${BASE}/maps/7/image/`);
    expect(req.request.method).toBe('GET');
    expect(req.request.responseType).toBe('blob');
    const jpeg = new Blob([new Uint8Array([255, 216, 255])], { type: 'image/jpeg' });
    req.flush(jpeg);
    expect(await result).toBe(jpeg);
  });

  describe('editor actions', () => {
    const ZONES: MapZones = {
      rows: 2,
      cols: 3,
      cell_size: 40,
      img_width: 120,
      img_height: 80,
      zones: [
        [0, 1, 4],
        [5, 1, 1],
      ],
      blocked_edges: { '0,1,e': true },
      blocked_edges_grid: [
        [
          { e: false, s: false },
          { e: true, s: false },
          { e: false, s: false },
        ],
        [
          { e: false, s: false },
          { e: false, s: false },
          { e: false, s: false },
        ],
      ],
      wall_meta: { '1,0': { facing: 'N' } },
      elevation: [
        [0, 0, 0],
        [0, 1.5, 0],
      ],
    };
    const CONFIG: ZoneConfig = {
      zone_size: 40,
      zones: ZONES.zones,
      wall_meta: { '1,0': { facing: 'N', height: 2 } },
      elevation: null,
      red_spawn: [[1, 1]],
      blue_spawn: [],
      spawn_user_edited: true,
      bases: [{ type: 'red', x_px: 60, y_px: 20 }],
    };
    const SIGHT: SightLines = {
      zone_size: 40,
      sight_data: { '0,1': ['1,1'], '1,1': ['0,1'] },
      base_sights: {
        red: [[1, 1]],
        blue: [],
        neutral_1: [],
        neutral_2: [],
        neutral_3: [],
        neutral_4: [],
      },
    };

    it('GETs the Cell grid for a Zone size', async () => {
      const result = firstValueFrom(maps.zones(3, 40));
      const req = http.expectOne((r) => r.url === `${BASE}/maps/3/zones/`);
      expect(req.request.method).toBe('GET');
      expect(req.request.params.get('zone_size')).toBe('40');
      req.flush({ data: ZONES, meta: {} });
      expect(await result).toEqual(ZONES);
    });

    it('GETs the confirmed zone config', async () => {
      const result = firstValueFrom(maps.zoneConfig(3));
      const req = http.expectOne(`${BASE}/maps/3/zone-config/`);
      expect(req.request.method).toBe('GET');
      expect(req.request.params.keys()).toEqual([]);
      req.flush({ data: CONFIG, meta: {} });
      expect(await result).toEqual(CONFIG);
    });

    it('PUTs a zone config and resolves to the saved state', async () => {
      const body: ZoneConfigInput = {
        zone_size: 40,
        zones: ZONES.zones,
        bases: [{ type: 'blue', x_px: 20, y_px: 60 }],
        wall_meta: { '1,0': { facing: 'S' } },
        red_spawn: [],
        blue_spawn: [[0, 1]],
      };
      const result = firstValueFrom(maps.replaceZoneConfig(3, body));
      const req = http.expectOne(`${BASE}/maps/3/zone-config/`);
      expect(req.request.method).toBe('PUT');
      expect(req.request.body).toEqual(body);
      req.flush({ data: CONFIG, meta: {} });
      expect(await result).toEqual(CONFIG);
    });

    it('GETs the stored Sight lines for a Zone size', async () => {
      const result = firstValueFrom(maps.sightLines(3, 40));
      const req = http.expectOne((r) => r.url === `${BASE}/maps/3/sight-lines/`);
      expect(req.request.method).toBe('GET');
      expect(req.request.params.get('zone_size')).toBe('40');
      req.flush({ data: SIGHT, meta: {} });
      expect(await result).toEqual(SIGHT);
    });

    it('PUTs (replace) and PATCHes (merge) a Sight-line batch', async () => {
      const body: SightLinesInput = {
        zone_size: 40,
        sight_data: { '0,1': ['1,1'] },
        base_sights: { red: [[1, 1]] },
      };
      const replaced = firstValueFrom(maps.replaceSightLines(3, body));
      const put = http.expectOne(`${BASE}/maps/3/sight-lines/`);
      expect(put.request.method).toBe('PUT');
      expect(put.request.body).toEqual(body);
      put.flush({ data: SIGHT, meta: {} });
      expect(await replaced).toEqual(SIGHT);

      const merged = firstValueFrom(maps.mergeSightLines(3, { ...body, base_sights: {} }));
      const patch = http.expectOne(`${BASE}/maps/3/sight-lines/`);
      expect(patch.request.method).toBe('PATCH');
      expect(patch.request.body).toEqual({ ...body, base_sights: {} });
      patch.flush({ data: SIGHT, meta: {} });
      expect(await merged).toEqual(SIGHT);
    });

    it('POSTs a Sight-line compute with the Zone size (and edited spawns)', async () => {
      const computed: ComputedSightLines = { zone_size: 40, sight_data: SIGHT.sight_data ?? {} };
      const result = firstValueFrom(maps.computeSightLines(3, 40));
      const req = http.expectOne(`${BASE}/maps/3/sight-lines/compute/`);
      expect(req.request.method).toBe('POST');
      expect(req.request.body).toEqual({ zone_size: 40 });
      req.flush({ data: computed, meta: {} });
      expect(await result).toEqual(computed);

      void firstValueFrom(maps.computeSightLines(3, 40, { red_spawn: [[1, 1]], blue_spawn: [] }));
      const withSpawns = http.expectOne(`${BASE}/maps/3/sight-lines/compute/`);
      expect(withSpawns.request.body).toEqual({
        zone_size: 40,
        red_spawn: [[1, 1]],
        blue_spawn: [],
      });
      withSpawns.flush({ data: computed, meta: {} });
    });

    it('GETs the ranked Cells for a Zone size', async () => {
      const result = firstValueFrom(maps.rankedCells(3, 40));
      const req = http.expectOne((r) => r.url === `${BASE}/maps/3/ranked-cells/`);
      expect(req.request.method).toBe('GET');
      expect(req.request.params.get('zone_size')).toBe('40');
      req.flush({ data: { ranked_cells: [[1, 1]] }, meta: {} });
      expect(await result).toEqual({ ranked_cells: [[1, 1]] });
    });

    it('GETs and PUTs the Heavy Strong spots', async () => {
      const got = firstValueFrom(maps.strongSpots(3, 40));
      const get = http.expectOne((r) => r.url === `${BASE}/maps/3/strong-spots/`);
      expect(get.request.method).toBe('GET');
      expect(get.request.params.get('zone_size')).toBe('40');
      get.flush({ data: { cells: [[0, 1]] }, meta: {} });
      expect(await got).toEqual({ cells: [[0, 1]] });

      const body: StrongSpotsInput = { zone_size: 40, cells: [[1, 1]] };
      const saved = firstValueFrom(maps.replaceStrongSpots(3, body));
      const put = http.expectOne(`${BASE}/maps/3/strong-spots/`);
      expect(put.request.method).toBe('PUT');
      expect(put.request.body).toEqual(body);
      put.flush({ data: { cells: [[1, 1]] }, meta: {} });
      expect(await saved).toEqual({ cells: [[1, 1]] });
    });

    it('GETs the Spawn cells (no params: they belong to the confirmed config)', async () => {
      const result = firstValueFrom(maps.spawnCells(3));
      const req = http.expectOne(`${BASE}/maps/3/spawn-cells/`);
      expect(req.request.method).toBe('GET');
      expect(req.request.params.keys()).toEqual([]);
      const spawns: SpawnCells = { red_spawn: [[1, 1]], blue_spawn: [[0, 2]] };
      req.flush({ data: spawns, meta: {} });
      expect(await result).toEqual(spawns);
    });

    it('GETs the heatmap, adding team_color only for one Side', async () => {
      const heat: MapHeatmap = {
        cell_occupancy: { '1,1': 12 },
        zone_size: 40,
        rows: 2,
        cols: 3,
        round_count: 4,
      };
      const both = firstValueFrom(maps.heatmap(3, 40));
      const req = http.expectOne((r) => r.url === `${BASE}/maps/3/heatmap/`);
      expect(req.request.method).toBe('GET');
      expect(req.request.params.get('zone_size')).toBe('40');
      expect(req.request.params.has('team_color')).toBe(false);
      req.flush({ data: heat, meta: {} });
      expect(await both).toEqual(heat);

      void firstValueFrom(maps.heatmap(3, 40, 'blue'));
      const blue = http.expectOne((r) => r.url === `${BASE}/maps/3/heatmap/`);
      expect(blue.request.params.get('team_color')).toBe('blue');
      blue.flush({ data: heat, meta: {} });
    });
  });

  it('surfaces an unknown Map as ApiError 404', async () => {
    const result = firstValueFrom(maps.get(99)).catch((e: unknown) => e);
    http
      .expectOne(`${BASE}/maps/99/`)
      .flush(
        { errors: [{ code: 'not_found', message: 'Not found.', field: null }], meta: {} },
        { status: 404, statusText: 'Not Found' },
      );
    const err = (await result) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(404);
  });

  it('uploads a Map as multipart FormData (name + image), not JSON', async () => {
    const image = new File([new Uint8Array([137, 80, 78, 71])], 'warehouse.png', {
      type: 'image/png',
    });
    const result = firstValueFrom(maps.upload({ name: 'Warehouse', image }));
    const req = http.expectOne(`${BASE}/maps/`);
    expect(req.request.method).toBe('POST');
    const body = req.request.body as unknown;
    expect(body).toBeInstanceOf(FormData);
    const form = body as FormData;
    expect(form.get('name')).toBe('Warehouse');
    const sent = form.get('image') as File;
    expect(sent).toBeInstanceOf(File);
    expect(sent.name).toBe('warehouse.png');
    expect(req.request.headers.has('Content-Type')).toBe(false);
    expect(req.request.detectContentTypeHeader()).toBeNull();
    req.flush({ data: MAP, meta: {} }, { status: 201, statusText: 'Created' });
    expect(await result).toEqual(MAP);
  });

  it('deletes a Map (204 resolves to undefined)', async () => {
    const result = firstValueFrom(maps.delete(2));
    const req = http.expectOne(`${BASE}/maps/2/`);
    expect(req.request.method).toBe('DELETE');
    req.flush(null, { status: 204, statusText: 'No Content' });
    expect(await result).toBeUndefined();
  });

  it('reports a Map still used by a Season as ApiError 409', async () => {
    const result = firstValueFrom(maps.delete(2)).catch((e: unknown) => e);
    http.expectOne(`${BASE}/maps/2/`).flush(
      {
        errors: [
          {
            code: 'map_in_use',
            message: 'This map is used by a Season that is not completed.',
            field: null,
          },
        ],
        meta: {},
      },
      { status: 409, statusText: 'Conflict' },
    );
    const err = (await result) as ApiError;
    expect(err.status).toBe(409);
    expect(err.errors[0].code).toBe('map_in_use');
  });
});
