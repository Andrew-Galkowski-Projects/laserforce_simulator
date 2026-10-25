import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { MATERIAL_ANIMATIONS } from '@angular/material/core';
import { By } from '@angular/platform-browser';
import { provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { CellPair, SightLines } from '../../api/maps.models';
import { signIn } from '../../api/testing/auth-fixtures';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';
import { stubCanvasContext } from '../../matches/testing/fake-canvas';
import { Cell } from '../map-editor/grid';
import { MapCanvas } from '../map-editor/map-canvas';
import { MapEditor } from '../map-editor/map-editor';
import { MAP_PALETTE_TOKENS, MapPalette } from '../map-editor/map-palette';
import { MapEditorStore } from '../map-editor/map-editor.store';
import {
  answerEditorLoads,
  editorMap,
  expectMapRequest,
  flushData,
  sightLines,
} from '../testing/editor-fixtures';
import { releaseMapsLoads } from '../testing/maps-loads';

// The editor's default map: 3 x 4 Cells at 40 px over a 160 x 120 image; [0, 0] is a high wall.
describe('Sight Lines mode', () => {
  let http: HttpTestingController;
  let harness: RouterTestingHarness;
  let restoreCanvas: () => void;
  const url = URL as unknown as Record<string, unknown>;
  const before = { create: url['createObjectURL'], revoke: url['revokeObjectURL'] };

  beforeEach(() => {
    url['createObjectURL'] = vi.fn(() => 'blob:image');
    url['revokeObjectURL'] = vi.fn();
    restoreCanvas = stubCanvasContext();
    TestBed.configureTestingModule({
      providers: [
        provideRouter(routes),
        provideHttpClient(withInterceptors([authInterceptor])),
        provideHttpClientTesting(),
        { provide: MATERIAL_ANIMATIONS, useValue: { animationsDisabled: true } },
      ],
    });
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    releaseMapsLoads();
    http.verify();
    restoreCanvas();
    url['createObjectURL'] = before.create;
    url['revokeObjectURL'] = before.revoke;
  });

  const page = (): HTMLElement => harness.routeNativeElement as HTMLElement;
  const text = (el: Element | null | undefined = page()): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const stable = (): Promise<void> => harness.fixture.whenStable();
  const store = (): MapEditorStore =>
    harness.fixture.debugElement.query(By.directive(MapEditor)).injector.get(MapEditorStore);
  const button = (label: string): HTMLButtonElement | undefined =>
    [...page().querySelectorAll('button')].find((b) => text(b) === label);
  const viewSelect = (): HTMLSelectElement =>
    page().querySelector<HTMLSelectElement>('select#sight-view') as HTMLSelectElement;
  const overlay = (): HTMLCanvasElement =>
    page().querySelector<HTMLCanvasElement>('canvas.me-overlay') as HTMLCanvasElement;
  const alert = (): string => text(page().querySelector('.sl-alert'));
  const hint = (): string => text(page().querySelector('.sl-hint'));

  /** Answers the Sight Lines mode's three GETs at `size`. */
  function answerSightLoads(
    sight: SightLines = sightLines(),
    ranked: CellPair[] = [],
    spots: CellPair[] = [],
    size = 40,
  ): void {
    for (const path of ['sight-lines/', 'ranked-cells/', 'strong-spots/']) {
      const req = expectMapRequest(4, path);
      expect(req.request.params.get('zone_size')).toBe(String(size));
      flushData(
        req,
        path === 'sight-lines/'
          ? sight
          : path === 'ranked-cells/'
            ? { ranked_cells: ranked }
            : { cells: spots },
      );
    }
  }

  /** Opens the editor and switches to Sight Lines, answering its loads. */
  async function openSight(sight: SightLines = sightLines(), spots: CellPair[] = []) {
    await signIn();
    harness = await RouterTestingHarness.create();
    const nav = harness.navigateByUrl('/maps/4/editor');
    await stable();
    answerEditorLoads(editorMap());
    await nav;
    await stable();
    for (const path of ['processed-image/', 'image/']) {
      expectMapRequest(4, path).flush(new Blob([new Uint8Array([137, 80, 78, 71])]));
    }
    await stable();
    button('Sight Lines')?.click();
    await stable();
    answerSightLoads(sight, [], spots);
    await stable();
    // The canvas drawn at its natural size: client px are image px.
    overlay().getBoundingClientRect = () =>
      ({ left: 0, top: 0, width: 160, height: 120, right: 160, bottom: 120 }) as DOMRect;
  }

  /** The client point at the centre of `cell`. */
  const centre = ([r, c]: Cell) => ({ clientX: c * 40 + 20, clientY: r * 40 + 20 });
  const fire = (type: string, cell: Cell) =>
    overlay().dispatchEvent(new MouseEvent(type, { ...centre(cell), bubbles: true }));

  async function clickCell(cell: Cell): Promise<void> {
    fire('pointerdown', cell);
    fire('pointerup', cell);
    fire('click', cell);
    await stable();
  }

  async function dragCells(from: Cell, to: Cell): Promise<void> {
    fire('pointerdown', from);
    fire('pointermove', to);
    fire('pointerup', to);
    fire('click', to);
    await stable();
  }

  async function pickView(value: string): Promise<void> {
    viewSelect().value = value;
    viewSelect().dispatchEvent(new Event('change'));
    await stable();
  }

  describe('view picker', () => {
    it('loads the Sight lines, ranking and Strong spots and offers every view', async () => {
      await openSight();
      expect([...viewSelect().options].map((o) => [o.value, text(o)])).toEqual([
        ['zone', 'Zone (click map)'],
        ['red', 'Red Base'],
        ['blue', 'Blue Base'],
        ['neutral_1', 'Neutral 1'],
        ['neutral_2', 'Neutral 2'],
        ['neutral_3', 'Neutral 3'],
        ['neutral_4', 'Neutral 4'],
        ['high_los', 'High-LOS Heatmap'],
        ['heavy_spots', 'Heavy Strong Spots'],
      ]);
      expect(viewSelect().value).toBe('zone');
      expect(store().sightStatus()).toBe('ready');
    });

    it('keeps unsaved edits across a mode toggle (no reload on re-entry)', async () => {
      await openSight();
      await clickCell([1, 1]);
      await clickCell([2, 2]);
      button('Zones & Bases')?.click();
      await stable();
      button('Sight Lines')?.click();
      await stable();
      http.expectNone((r) => r.url.endsWith('/sight-lines/'));
      expect(store().sightLinesDirty()).toBe(true);
      expect(store().sight()?.sightData['2,2']).toEqual(['1,1']);
    });

    it('shows a loading message, then a load failure as an error', async () => {
      await signIn();
      harness = await RouterTestingHarness.create();
      const nav = harness.navigateByUrl('/maps/4/editor');
      await stable();
      answerEditorLoads(editorMap());
      await nav;
      await stable();
      button('Sight Lines')?.click();
      await stable();
      expect(text(page().querySelector('app-sight-lines-mode'))).toContain('Loading sight lines…');
      expect(button('Save Sight Lines')?.disabled).toBe(true);
      expectMapRequest(4, 'sight-lines/').flush(
        { errors: [{ code: 'server_error', message: 'Boom.', field: null }], meta: {} },
        { status: 500, statusText: 'Server Error' },
      );
      await stable();
      const error = page().querySelector('app-sight-lines-mode [role="alert"]');
      expect(text(error)).toBe('Could not load the sight lines.');
    });

    it('retries a failed load when the mode is left and entered again', async () => {
      await signIn();
      harness = await RouterTestingHarness.create();
      const nav = harness.navigateByUrl('/maps/4/editor');
      await stable();
      answerEditorLoads(editorMap());
      await nav;
      await stable();
      button('Sight Lines')?.click();
      await stable();
      expectMapRequest(4, 'sight-lines/').flush(
        { errors: [{ code: 'server_error', message: 'Boom.', field: null }], meta: {} },
        { status: 500, statusText: 'Server Error' },
      );
      await stable();
      expect(store().sightStatus()).toBe('error');
      // The failure cancelled the first load's other two GETs.
      const siblings = http.match(
        (r) => r.url.endsWith('/ranked-cells/') || r.url.endsWith('/strong-spots/'),
      );
      expect(siblings.map((r) => r.cancelled)).toEqual([true, true]);
      button('Zones & Bases')?.click();
      await stable();
      button('Sight Lines')?.click();
      await stable();
      answerSightLoads();
      await stable();
      expect(store().sightStatus()).toBe('ready');
      expect(page().querySelector('app-sight-lines-mode [role="alert"]')).toBeNull();
    });
  });

  /** Clicks Save Sight Lines and returns the PUT it sends. */
  async function saveSight() {
    button('Save Sight Lines')?.click();
    await stable();
    return expectMapRequest(4, 'sight-lines/', 'PUT');
  }

  describe('Zone view', () => {
    it('selects a Cell, then toggles its LOS link with another both ways', async () => {
      // Fixture: [1, 1] <-> [1, 2] linked.
      await openSight();
      await clickCell([1, 1]); // select
      await clickCell([2, 2]); // link [1, 1] <-> [2, 2]
      await clickCell([1, 2]); // unlink [1, 1] <-> [1, 2]
      await clickCell([0, 0]); // a high wall: ignored
      expect(store().sightLinesDirty()).toBe(true);

      const req = await saveSight();
      expect(req.request.body).toEqual({
        zone_size: 40,
        sight_data: { '1,1': ['2,2'], '2,2': ['1,1'] },
        base_sights: sightLines().base_sights,
      });
      expect(alert()).toBe(''); // one batch: no progress message
      expect(button('Save Sight Lines')?.disabled).toBe(true);
      await clickCell([2, 3]); // the canvas edits nothing while a save is in flight
      expect(store().sight()?.sightData['2,3']).toBeUndefined();
      flushData(req, sightLines(40, { sight_data: { '1,1': ['2,2'], '2,2': ['1,1'] } }));
      await stable();
      expect(alert()).toBe('Sight lines saved!');
      expect(store().sightLinesDirty()).toBe(false);
    });

    it('clicking the selected Cell deselects it, so the next click selects instead', async () => {
      await openSight();
      await clickCell([1, 1]); // select
      await clickCell([1, 1]); // deselect
      await clickCell([2, 2]); // select, no toggle
      expect(store().sightLinesDirty()).toBe(false);
      expect(store().sight()?.sightData).toEqual(sightLines().sight_data);
    });

    it('never selects a high wall', async () => {
      await openSight();
      await clickCell([0, 0]); // a wall: nothing selected
      await clickCell([2, 2]); // select
      await clickCell([1, 1]); // toggles against [2, 2]
      expect(store().sight()?.sightData['2,2']).toEqual(['1,1']);
    });

    it('with Bulk Edit on, a drag toggles every non-wall Cell of the rectangle both ways', async () => {
      await openSight(sightLines(40, { sight_data: {} }));
      button('Bulk Edit: Off')?.click();
      await stable();
      expect(button('Bulk Edit: On')).toBeDefined();
      expect(overlay().style.cursor).toBe('crosshair');
      await clickCell([2, 2]); // select (a click without a move is no drag)
      await dragCells([0, 0], [1, 1]); // the wall [0, 0] is skipped; the trailing click swallowed

      const req = await saveSight();
      expect(req.request.body.sight_data).toEqual({
        '2,2': ['0,1', '1,0', '1,1'],
        '0,1': ['2,2'],
        '1,0': ['2,2'],
        '1,1': ['2,2'],
      });
      flushData(req, sightLines());
    });

    it('with Bulk Edit on, a drag over linked Cells unlinks only those', async () => {
      // Fixture: [1, 1] <-> [1, 2] linked.
      await openSight();
      button('Bulk Edit: Off')?.click();
      await stable();
      await clickCell([1, 1]);
      await dragCells([1, 2], [2, 3]);
      expect(store().sight()?.sightData).toEqual({});
    });

    it('with Bulk Edit on but nothing selected, a drag edits nothing', async () => {
      await openSight();
      button('Bulk Edit: Off')?.click();
      await stable();
      await dragCells([1, 2], [2, 3]); // no rectangle: its click selects [2, 3]
      expect(store().sightLinesDirty()).toBe(false);
      await clickCell([1, 1]); // toggles against [2, 3]
      expect(store().sight()?.sightData['2,3']).toEqual(['1,1']);
    });

    it('with Bulk Edit off, a drag is no rectangle: only its click lands', async () => {
      await openSight(sightLines(40, { sight_data: {} }));
      await clickCell([1, 1]);
      await dragCells([1, 2], [2, 3]); // the click on [2, 3] toggles that one link
      expect(store().sight()?.sightData).toEqual({ '1,1': ['2,3'], '2,3': ['1,1'] });
    });
  });

  describe('Base views', () => {
    it("toggles a Cell in the Base's tagging Cells on click, skipping high walls", async () => {
      await openSight();
      await pickView('blue');
      expect(hint()).toBe('Showing cells that can tag Blue Base. Click to add/remove.');
      await clickCell([2, 2]); // add
      await clickCell([0, 0]); // a high wall: ignored
      await pickView('red');
      await clickCell([1, 1]); // remove the fixture's only red Cell
      await clickCell([0, 1]); // add

      const req = await saveSight();
      expect(req.request.body.base_sights).toEqual({
        ...sightLines().base_sights,
        red: [[0, 1]],
        blue: [[2, 2]],
      });
      expect(req.request.body.sight_data).toEqual(sightLines().sight_data);
      flushData(req, sightLines());
    });

    it('turns Bulk Edit off on entry, and a drag there is no rectangle', async () => {
      await openSight();
      button('Bulk Edit: Off')?.click();
      await stable();
      await pickView('neutral_2');
      expect(button('Bulk Edit: Off')).toBeDefined();
      button('Bulk Edit: Off')?.click();
      await stable();
      await dragCells([1, 2], [2, 3]); // only the click on [2, 3] lands
      expect(store().sight()?.baseSights.neutral_2).toEqual([[2, 3]]);
    });
  });

  describe('High LOS view', () => {
    it('is read-only', async () => {
      await openSight();
      await pickView('high_los');
      expect(hint()).toBe('Heat map: gold = highest LOS count, teal = lowest. Read-only.');
      await clickCell([1, 1]);
      expect(store().dirty()).toBe(false);
    });
  });

  describe('Heavy Strong spots view', () => {
    it('toggles by click and by Bulk Edit rectangle, and saves through its own button', async () => {
      await openSight(sightLines(), [[2, 3]]);
      await pickView('heavy_spots');
      expect(hint()).toBe(
        'Click cells to add/remove Heavy strong-spot candidates. Enable Bulk Edit to drag-select a rectangle.',
      );
      expect(button('Save Sight Lines')).toBeUndefined();
      await clickCell([0, 0]); // a high wall: ignored
      await clickCell([2, 3]); // remove
      await clickCell([1, 3]); // add
      button('Bulk Edit: Off')?.click();
      await stable();
      await dragCells([0, 0], [1, 1]); // adds the rectangle's non-wall Cells
      expect(store().strongSpotsDirty()).toBe(true);
      expect(store().sightLinesDirty()).toBe(false);

      button('Save Heavy Strong Spots')?.click();
      await stable();
      const req = expectMapRequest(4, 'strong-spots/', 'PUT');
      expect(req.request.body).toEqual({
        zone_size: 40,
        cells: [
          [1, 3],
          [0, 1],
          [1, 0],
          [1, 1],
        ],
      });
      // The server's answer is what is stored (and counted).
      flushData(req, { cells: [[1, 3]] });
      await stable();
      expect(alert()).toBe('Heavy strong spots saved! (1 cells)');
      expect(store().strongSpots()).toEqual([[1, 3]]);
      expect(store().strongSpotsDirty()).toBe(false);
    });

    it('a rectangle over a Strong spot removes the Strong spots in it', async () => {
      await openSight(sightLines(), [
        [1, 1],
        [2, 3],
      ]);
      await pickView('heavy_spots');
      button('Bulk Edit: Off')?.click();
      await stable();
      await dragCells([1, 0], [2, 1]);
      expect(store().strongSpots()).toEqual([[2, 3]]);
    });

    it('a failed save shows the server message and stays dirty', async () => {
      await openSight();
      await pickView('heavy_spots');
      await clickCell([1, 1]);
      button('Save Heavy Strong Spots')?.click();
      await stable();
      expectMapRequest(4, 'strong-spots/', 'PUT').flush(
        { errors: [{ code: 'invalid', message: 'Bad cells.', field: null }], meta: {} },
        { status: 400, statusText: 'Bad Request' },
      );
      await stable();
      expect(alert()).toBe('Error: Bad cells.');
      expect(store().strongSpotsDirty()).toBe(true);
    });
  });

  describe('Compute Sight Lines', () => {
    const busy = (): Element | null => page().querySelector('[role="progressbar"]');

    it('runs the server computation with a busy indicator and shows the result', async () => {
      await openSight();
      await clickCell([1, 1]); // a selection, cleared by the result
      button('Compute Sight Lines')?.click();
      await stable();
      const req = expectMapRequest(4, 'sight-lines/compute/', 'POST');
      expect(req.request.body).toEqual({ zone_size: 40 });
      expect(busy()).not.toBeNull();
      expect(button('Compute Sight Lines')?.disabled).toBe(true);
      expect(hint()).toBe('Computing… this may take a moment for large maps.');

      flushData(req, { zone_size: 40, sight_data: { '2,2': ['2,3'], '2,3': ['2,2'] } });
      await stable();
      expect(busy()).toBeNull();
      expect(button('Compute Sight Lines')?.disabled).toBe(false);
      expect(store().sight()?.sightData).toEqual({ '2,2': ['2,3'], '2,3': ['2,2'] });
      expect(hint()).toBe(
        'Done! Click a zone cell to see its sight lines. Enable Bulk Edit to drag-select.',
      );
      // The server re-derives the ranking and Strong spots: both reload.
      flushData(expectMapRequest(4, 'ranked-cells/'), { ranked_cells: [[2, 2]] });
      flushData(expectMapRequest(4, 'strong-spots/'), { cells: [[2, 2]] });
      await stable();
      expect(store().rankedCells()).toEqual([[2, 2]]);

      await clickCell([2, 2]); // selects afresh: the old selection was cleared
      expect(store().sightLinesDirty()).toBe(false);
      await pickView('red'); // a view change replaces the compute's notice with its hint
      expect(hint()).toBe('Showing cells that can tag Red Base. Click to add/remove.');
    });

    it('sends unsaved Spawn edits so the server keeps them', async () => {
      await openSight();
      store().editZones((s) => ({
        ...s,
        redSpawn: new Set(['1,0']),
        blueSpawn: new Set(['2,3', '2,2']),
        spawnEdited: true,
      }));
      button('Compute Sight Lines')?.click();
      await stable();
      const req = expectMapRequest(4, 'sight-lines/compute/', 'POST');
      expect(req.request.body).toEqual({
        zone_size: 40,
        red_spawn: [[1, 0]],
        blue_spawn: [
          [2, 3],
          [2, 2],
        ],
      });
      flushData(req, { zone_size: 40, sight_data: {} });
    });

    it("shows a failure's server message and keeps the Sight lines", async () => {
      await openSight();
      button('Compute Sight Lines')?.click();
      await stable();
      expectMapRequest(4, 'sight-lines/compute/', 'POST').flush(
        { errors: [{ code: 'invalid', message: 'No zone data.', field: null }], meta: {} },
        { status: 400, statusText: 'Bad Request' },
      );
      await stable();
      expect(alert()).toBe('Error: No zone data.');
      expect(hint()).toContain('Click a zone cell');
      expect(busy()).toBeNull();
      expect(button('Compute Sight Lines')?.disabled).toBe(false);
      expect(store().sight()?.sightData).toEqual(sightLines().sight_data);
    });

    it('is disabled in the Heavy Strong spots view', async () => {
      await openSight();
      await pickView('heavy_spots');
      expect(button('Compute Sight Lines')?.disabled).toBe(true);
    });
  });

  describe('overlay', () => {
    /** Runs the mode's draw on a recording context; returns the colours each Cell fill used. */
    function paintedColours(): Set<string> {
      const layer = harness.fixture.debugElement
        .query(By.directive(MapEditor))
        .injector.get(MapCanvas)
        .layer();
      const fills: string[] = [];
      const ctx = { fillStyle: '', globalAlpha: 1 } as Record<string, unknown>;
      const record = new Proxy(ctx, {
        get: (target, prop) =>
          prop in target
            ? target[prop as string]
            : prop === 'fillRect'
              ? () => fills.push(String(target['fillStyle']))
              : () => undefined,
        set: (target, prop, value) => {
          target[prop as string] = value;
          return true;
        },
      });
      // Each palette entry is its own name, so a fill names the colour it used.
      const palette = Object.fromEntries(
        Object.keys(MAP_PALETTE_TOKENS).map((k) => [k, k]),
      ) as unknown as MapPalette;
      layer?.draw?.({
        ctx: record as unknown as CanvasRenderingContext2D,
        rows: 3,
        cols: 4,
        cellSize: 40,
        width: 160,
        height: 120,
        zones: store().zoneEdit()!.zones,
        palette,
      });
      return new Set(fills);
    }

    it("paints each view's own overlay", async () => {
      await openSight(sightLines(), [[2, 2]]);
      expect(paintedColours().has('losSelected')).toBe(false);
      await clickCell([1, 1]);
      expect([...paintedColours()]).toEqual(
        expect.arrayContaining(['losSelected', 'losVisible', 'losBlocked']),
      );
      await pickView('red');
      expect([...paintedColours()]).toEqual(expect.arrayContaining(['baseSight', 'spawnRed']));
      await pickView('heavy_spots');
      expect(paintedColours().has('strongSpot')).toBe(true);
      store().loadRanking();
      flushData(expectMapRequest(4, 'ranked-cells/'), {
        ranked_cells: [
          [1, 1],
          [2, 2],
        ],
      });
      flushData(expectMapRequest(4, 'strong-spots/'), { cells: [] });
      await pickView('high_los');
      expect([...paintedColours()]).toEqual(expect.arrayContaining(['highLos', 'highLosLow']));
    });
  });

  describe('hints', () => {
    it('explains the Zone view, or that the Sight lines were never computed', async () => {
      await openSight();
      expect(hint()).toBe(
        'Click a zone cell to view its sight lines. Enable Bulk Edit to drag-select multiple cells.',
      );
      await pickView('red');
      await pickView('zone');
      expect(hint()).toContain('Click a zone cell');
    });

    it('says when nothing is computed yet for the Zone size', async () => {
      await openSight(sightLines(40, { sight_data: null }));
      expect(hint()).toBe("Not computed yet for this zone size. Click 'Compute Sight Lines'.");
    });

    it('switching views clears the Zone selection', async () => {
      await openSight();
      await clickCell([1, 1]); // select
      await pickView('red');
      await pickView('zone');
      await clickCell([2, 2]); // selects again rather than toggling against [1, 1]
      expect(store().sightLinesDirty()).toBe(false);
    });
  });

  describe('Save Sight Lines', () => {
    /** 250 Sight-line entries: three batches. */
    const big = () =>
      sightLines(40, {
        sight_data: Object.fromEntries(
          Array.from({ length: 250 }, (_v, i) => [`9,${i}`, [`8,${i}`]]),
        ),
      });

    it('sends 100-key batches in order, PUT first and PATCH after, with progress', async () => {
      await openSight(big());
      await clickCell([1, 1]);
      await clickCell([2, 2]); // an edit: dirty
      const put = await saveSight();
      expect(Object.keys(put.request.body.sight_data)).toHaveLength(100);
      expect(put.request.body.base_sights).toEqual(sightLines().base_sights);
      expect(alert()).toBe('Saving... (1/3)');
      flushData(put, big());
      await stable();

      const second = expectMapRequest(4, 'sight-lines/', 'PATCH');
      expect(Object.keys(second.request.body.sight_data)[0]).toBe('9,100');
      expect(second.request.body.base_sights).toEqual({});
      expect(alert()).toBe('Saving... (2/3)');
      flushData(second, big());
      await stable();
      expect(store().sightLinesDirty()).toBe(true);

      const third = expectMapRequest(4, 'sight-lines/', 'PATCH');
      // 50 of the fixture's entries, plus the two the edit added.
      expect(Object.keys(third.request.body.sight_data)).toHaveLength(52);
      flushData(third, sightLines(40, { sight_data: { '1,1': ['2,2'] } }));
      await stable();
      expect(alert()).toBe('Sight lines saved!');
      expect(store().sightLinesDirty()).toBe(false);
      expect(store().sight()?.sightData).toEqual({ '1,1': ['2,2'] });
    });

    it('reports a failed batch as partial, stays dirty, and a retry resends from the start', async () => {
      await openSight(big());
      await clickCell([1, 1]);
      await clickCell([2, 2]);
      flushData(await saveSight(), big());
      await stable();
      const second = expectMapRequest(4, 'sight-lines/', 'PATCH');
      second.flush(
        { errors: [{ code: 'invalid', message: 'Too large.', field: null }], meta: {} },
        { status: 400, statusText: 'Bad Request' },
      );
      await stable();
      expect(alert()).toBe('Saved 1 of 3 batches — retry. Too large.');
      expect(store().sightLinesDirty()).toBe(true);
      http.expectNone((r) => r.method === 'PATCH');

      const retry = await saveSight();
      expect(Object.keys(retry.request.body.sight_data)[0]).toBe('9,0');
      flushData(retry, big());
      await stable();
      flushData(expectMapRequest(4, 'sight-lines/', 'PATCH'), big());
      await stable();
      flushData(expectMapRequest(4, 'sight-lines/', 'PATCH'), big());
      await stable();
      expect(alert()).toBe('Sight lines saved!');
    });

    it('a single failed batch saved none of them', async () => {
      await openSight();
      const put = await saveSight();
      put.flush(
        { errors: [{ code: 'invalid', message: 'Bad data.', field: null }], meta: {} },
        { status: 400, statusText: 'Bad Request' },
      );
      await stable();
      expect(alert()).toBe('Saved 0 of 1 batches — retry. Bad data.');
    });
  });
});
