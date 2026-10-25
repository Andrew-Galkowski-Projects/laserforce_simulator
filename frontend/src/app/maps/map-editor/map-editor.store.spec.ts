import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { firstValueFrom } from 'rxjs';
import { ZoneConfigInput } from '../../api/maps.models';
import { authInterceptor } from '../../auth/auth.interceptor';
import {
  SPAWNS,
  answerEditorLoads,
  answerGridLoads,
  editorMap,
  expectMapRequest,
  flushData,
  flushError,
  heatmap,
  mapZones,
  sightLines,
  zoneConfig,
} from '../testing/editor-fixtures';
import { DEFAULT_ZONE_SIZE, MapEditorStore, ZONE_SIZES } from './map-editor.store';

describe('MapEditorStore', () => {
  let store: MapEditorStore;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        MapEditorStore,
        provideHttpClient(withInterceptors([authInterceptor])),
        provideHttpClientTesting(),
      ],
    });
    store = TestBed.inject(MapEditorStore);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  /** Opens map 4 (confirmed at 40 px) with every load answered. */
  function openReady(): void {
    store.open(4);
    answerEditorLoads(
      editorMap(),
      zoneConfig(),
      mapZones(40, { wall_meta: { '1,1': { facing: 'E', height: 2 } }, elevation: [[0.5]] }),
    );
  }

  it('offers exactly the Django Zone sizes and defaults to 50', () => {
    expect(ZONE_SIZES).toEqual([20, 30, 40, 50, 60, 80, 100]);
    expect(DEFAULT_ZONE_SIZE).toBe(50);
  });

  describe('open', () => {
    it('loads the map, its config and the grid at the confirmed Zone size', () => {
      store.open(4);
      expect(store.status()).toBe('loading');
      flushData(expectMapRequest(4, ''), editorMap());
      flushData(expectMapRequest(4, 'zone-config/'), zoneConfig());
      expect(store.zoneSize()).toBe(40);
      answerGridLoads(
        4,
        mapZones(40, { wall_meta: { '1,1': { facing: 'E', height: 2 } }, elevation: [[0.5]] }),
      );

      expect(store.status()).toBe('ready');
      expect(store.map()?.name).toBe('Warehouse');
      expect(store.gridStatus()).toBe('ready');
      expect(store.grid()?.rows).toBe(3);
      const edit = store.zoneEdit();
      expect(edit?.zones).toEqual(mapZones().zones);
      expect(edit?.wallMeta).toEqual({ '1,1': { facing: 'E', height: 2 } });
      expect(edit?.elevation).toEqual([[0.5]]);
      expect(edit?.bases).toEqual({
        red: { x_px: 20, y_px: 60 },
        blue: { x_px: 140, y_px: 100 },
      });
      expect([...(edit?.redSpawn ?? [])]).toEqual(['1,0']);
      expect([...(edit?.blueSpawn ?? [])]).toEqual(['2,3']);
      expect(edit?.spawnEdited).toBe(false);
      expect(store.dirty()).toBe(false);
    });

    it('opens at 50 px when the map has no confirmed config', () => {
      store.open(4);
      answerEditorLoads(
        editorMap({ has_confirmed_config: false, confirmed_zone_size: null }),
        zoneConfig({ zone_size: null, zones: null, bases: [] }),
      );
      expect(store.zoneSize()).toBe(50);
      expect(store.zoneEdit()?.bases).toEqual({});
      expect(store.zoneEdit()?.wallMeta).toEqual({});
      expect(store.zoneEdit()?.elevation).toBeNull();
    });

    it('reports an unknown map as not-found and loads no grid', () => {
      store.open(99);
      flushError(expectMapRequest(99, ''), 404, 'not_found', 'Not found.');
      expect(store.status()).toBe('not-found');
      http.match(() => true);
      http.expectNone((r) => r.url.endsWith('/zones/'));
    });

    it('reports any other load failure as an error with the server message', () => {
      store.open(4);
      flushError(expectMapRequest(4, ''), 500, 'server_error', 'Boom.');
      http.match(() => true);
      expect(store.status()).toBe('error');
      expect(store.loadError()).toBe('Could not load the map. Boom.');
    });

    it('reports a failed grid load without failing the editor', () => {
      store.open(4);
      flushData(expectMapRequest(4, ''), editorMap());
      flushData(expectMapRequest(4, 'zone-config/'), zoneConfig());
      flushError(expectMapRequest(4, 'zones/'), 500, 'server_error', 'Boom.');
      http.match(() => true);
      expect(store.status()).toBe('ready');
      expect(store.gridStatus()).toBe('error');
      expect(store.zoneEdit()).toBeNull();
    });

    it('treats failed Spawn cells as none, as the Django editor does', () => {
      store.open(4);
      flushData(expectMapRequest(4, ''), editorMap());
      flushData(expectMapRequest(4, 'zone-config/'), zoneConfig());
      flushData(expectMapRequest(4, 'zones/'), mapZones(40));
      flushError(expectMapRequest(4, 'spawn-cells/'), 500, 'server_error', 'Boom.');
      expect(store.gridStatus()).toBe('ready');
      expect(store.zoneEdit()?.redSpawn.size).toBe(0);
    });
  });

  describe('Zones & Bases area', () => {
    beforeEach(openReady);

    it('marks the zone config dirty on an edit, without touching the loaded grid', () => {
      // Even an edit that writes in place cannot reach the loaded grid.
      store.editZones((s) => {
        s.zones[0][0] = 4;
        return { ...s };
      });
      expect(store.zoneEdit()?.zones[0][0]).toBe(4);
      expect(store.grid()?.zones[0][0]).toBe(0);
      expect(store.zoneConfigDirty()).toBe(true);
      expect(store.dirty()).toBe(true);
    });

    it('saves with PUT, then shows exactly what the server saved and is clean', async () => {
      store.editZones((s) => ({ ...s, bases: {} }));
      const body: ZoneConfigInput = { zone_size: 40, zones: mapZones().zones, bases: [] };
      const saved = firstValueFrom(store.saveZoneConfig(body));
      const req = expectMapRequest(4, 'zone-config/', 'PUT');
      expect(req.request.body).toEqual(body);
      const response = zoneConfig({
        wall_meta: { '0,1': { facing: 'W' } },
        elevation: [[1]],
        red_spawn: [[2, 2]],
        blue_spawn: [],
        bases: [{ type: 'neutral_1', x_px: 60, y_px: 60 }],
      });
      flushData(req, response);
      expect(await saved).toEqual(response);

      const edit = store.zoneEdit();
      expect(edit?.bases).toEqual({ neutral_1: { x_px: 60, y_px: 60 } });
      expect(edit?.wallMeta).toEqual({ '0,1': { facing: 'W' } });
      expect(edit?.elevation).toEqual([[1]]);
      expect([...(edit?.redSpawn ?? [])]).toEqual(['2,2']);
      expect(store.zoneConfigDirty()).toBe(false);
      expect(store.map()?.confirmed_zone_size).toBe(40);
    });

    it('stays dirty with the edits kept when the save fails', async () => {
      store.editZones((s) => ({ ...s, bases: {} }));
      const saved = firstValueFrom(store.saveZoneConfig({ zone_size: 40 })).catch(
        (e: unknown) => e,
      );
      flushError(expectMapRequest(4, 'zone-config/', 'PUT'), 400, 'invalid', 'Bad zones.');
      expect(((await saved) as Error).message).toBe('Bad zones.');
      expect(store.zoneConfigDirty()).toBe(true);
      expect(store.zoneEdit()?.bases).toEqual({});
    });
  });

  describe('Zone-size change', () => {
    beforeEach(openReady);

    it('reloads the grid at the new size and discards every unsaved edit', () => {
      store.editZones((s) => ({ ...s, bases: {}, wallMeta: {} }));
      store.setDirty('sightLines', true);
      store.setDirty('strongSpots', true);
      store.changeZoneSize(20);
      expect(store.zoneSize()).toBe(20);
      expect(store.dirty()).toBe(false);
      expect(store.gridStatus()).toBe('loading');
      answerGridLoads(4, mapZones(20), { red_spawn: [], blue_spawn: [] });
      expect(store.grid()?.cell_size).toBe(20);
      expect(store.zoneEdit()?.wallMeta).toEqual({});
      // Bases are pixel positions, so the last saved ones carry over to any size.
      expect(store.zoneEdit()?.bases).toEqual({
        red: { x_px: 20, y_px: 60 },
        blue: { x_px: 140, y_px: 100 },
      });
    });

    it('reloads the Sight-line and heatmap areas a mode had loaded, at the new size', () => {
      store.loadSightLines();
      flushData(expectMapRequest(4, 'sight-lines/'), sightLines());
      flushData(expectMapRequest(4, 'ranked-cells/'), { ranked_cells: [] });
      flushData(expectMapRequest(4, 'strong-spots/'), { cells: [] });
      store.loadHeatmap('red');
      flushData(expectMapRequest(4, 'heatmap/'), heatmap());

      store.changeZoneSize(60);
      answerGridLoads(4, mapZones(60), SPAWNS);
      for (const path of ['sight-lines/', 'ranked-cells/', 'strong-spots/']) {
        const req = expectMapRequest(4, path);
        expect(req.request.params.get('zone_size')).toBe('60');
        req.flush({ data: path === 'sight-lines/' ? sightLines(60) : {}, meta: {} });
      }
      const heat = expectMapRequest(4, 'heatmap/');
      expect(heat.request.params.get('zone_size')).toBe('60');
      expect(heat.request.params.get('team_color')).toBe('red');
      flushData(heat, heatmap({ zone_size: 60 }));
    });

    it('loads no area a mode never asked for', () => {
      store.changeZoneSize(60);
      answerGridLoads(4, mapZones(60), SPAWNS);
      http.expectNone((r) => /sight-lines|ranked-cells|strong-spots|heatmap/.test(r.url));
    });
  });

  describe('Sight Lines area', () => {
    beforeEach(openReady);

    function loadSight(sight = sightLines()): void {
      store.loadSightLines();
      expect(store.sightStatus()).toBe('loading');
      const data: Record<string, unknown> = {
        'sight-lines/': sight,
        'ranked-cells/': { ranked_cells: [[1, 1]] },
        'strong-spots/': { cells: [[1, 2]] },
      };
      for (const [path, body] of Object.entries(data)) {
        const req = expectMapRequest(4, path);
        expect(req.request.params.get('zone_size')).toBe('40');
        flushData(req, body);
      }
    }

    it('loads Sight lines, Base sights, ranked Cells and Strong spots for the Zone size', () => {
      loadSight();
      expect(store.sightStatus()).toBe('ready');
      expect(store.sight()?.sightData).toEqual({ '1,1': ['1,2'], '1,2': ['1,1'] });
      expect(store.sight()?.computed).toBe(true);
      expect(store.sight()?.baseSights.red).toEqual([[1, 1]]);
      expect(store.rankedCells()).toEqual([[1, 1]]);
      expect(store.strongSpots()).toEqual([[1, 2]]);
    });

    it('starts from empty Sight lines when they were never computed', () => {
      loadSight(sightLines(40, { sight_data: null }));
      expect(store.sight()?.sightData).toEqual({});
      expect(store.sight()?.computed).toBe(false);
    });

    it('reports a failed load', () => {
      store.loadSightLines();
      flushError(expectMapRequest(4, 'sight-lines/'), 500, 'server_error', 'Boom.');
      http.match(() => true);
      expect(store.sightStatus()).toBe('error');
    });

    it('marks Sight lines dirty on an edit and clean when a save is committed', () => {
      loadSight();
      store.editSightLines((s) => ({ ...s, sightData: {} }));
      expect(store.sightLinesDirty()).toBe(true);
      store.commitSightLines(sightLines(40, { sight_data: { '2,2': [] } }));
      expect(store.sight()?.sightData).toEqual({ '2,2': [] });
      expect(store.sightLinesDirty()).toBe(false);
    });

    it('takes a compute result as computed Sight lines, keeping the Base sights', () => {
      loadSight(sightLines(40, { sight_data: null }));
      store.commitComputed({ zone_size: 40, sight_data: { '0,1': [] } });
      expect(store.sight()?.sightData).toEqual({ '0,1': [] });
      expect(store.sight()?.computed).toBe(true);
      expect(store.sight()?.baseSights.red).toEqual([[1, 1]]);
    });

    it('reloads the ranking and Strong spots on request (after a compute)', () => {
      loadSight();
      store.loadRanking();
      flushData(expectMapRequest(4, 'ranked-cells/'), { ranked_cells: [[2, 2]] });
      flushData(expectMapRequest(4, 'strong-spots/'), { cells: [[2, 3]] });
      expect(store.rankedCells()).toEqual([[2, 2]]);
      expect(store.strongSpots()).toEqual([[2, 3]]);
    });

    it('saves Strong spots with PUT and is clean after; a failure stays dirty', async () => {
      loadSight();
      store.editStrongSpots(() => [[0, 1]]);
      expect(store.strongSpotsDirty()).toBe(true);
      const failed = firstValueFrom(store.saveStrongSpots()).catch((e: unknown) => e);
      flushError(expectMapRequest(4, 'strong-spots/', 'PUT'), 400, 'invalid', 'Bad.');
      await failed;
      expect(store.strongSpotsDirty()).toBe(true);

      const saved = firstValueFrom(store.saveStrongSpots());
      const req = expectMapRequest(4, 'strong-spots/', 'PUT');
      expect(req.request.body).toEqual({ zone_size: 40, cells: [[0, 1]] });
      flushData(req, { cells: [[0, 1]] });
      await saved;
      expect(store.strongSpotsDirty()).toBe(false);
      expect(store.strongSpots()).toEqual([[0, 1]]);
    });
  });

  describe('Heatmap area', () => {
    beforeEach(openReady);

    it('loads the heatmap for the Zone size, with team_color only for one Side', () => {
      store.loadHeatmap();
      expect(store.heatmapStatus()).toBe('loading');
      const both = expectMapRequest(4, 'heatmap/');
      expect(both.request.params.get('zone_size')).toBe('40');
      expect(both.request.params.has('team_color')).toBe(false);
      flushData(both, heatmap());
      expect(store.heatmap()?.round_count).toBe(2);
      expect(store.heatmapSide()).toBeNull();

      store.loadHeatmap('blue');
      expect(store.heatmapSide()).toBe('blue');
      const blue = expectMapRequest(4, 'heatmap/');
      expect(blue.request.params.get('team_color')).toBe('blue');
      flushData(blue, heatmap({ round_count: 1 }));
      expect(store.heatmap()?.round_count).toBe(1);
    });

    it('reports a failed heatmap load', () => {
      store.loadHeatmap();
      flushError(expectMapRequest(4, 'heatmap/'), 500, 'server_error', 'Boom.');
      expect(store.heatmapStatus()).toBe('error');
      expect(store.heatmap()).toBeNull();
    });
  });

  describe('dirty flags and mode', () => {
    beforeEach(openReady);

    it.each(['zoneConfig', 'sightLines', 'strongSpots'] as const)(
      'is dirty while the %s area is',
      (area) => {
        store.setDirty(area, true);
        expect(store.dirty()).toBe(true);
        store.setDirty(area, false);
        expect(store.dirty()).toBe(false);
      },
    );

    it('starts in Zones & Bases mode and switches', () => {
      expect(store.mode()).toBe('zones');
      store.setMode('heatmap');
      expect(store.mode()).toBe('heatmap');
    });
  });
});
