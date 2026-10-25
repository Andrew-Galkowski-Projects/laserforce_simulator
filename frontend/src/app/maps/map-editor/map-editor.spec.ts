import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { MATERIAL_ANIMATIONS } from '@angular/material/core';
import { By } from '@angular/platform-browser';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { ArenaMap, MapZones, ZoneConfig } from '../../api/maps.models';
import { signIn } from '../../api/testing/auth-fixtures';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';
import { stubCanvasContext } from '../../matches/testing/fake-canvas';
import { HeatmapMode } from '../heatmap/heatmap';
import { MapList } from '../map-list/map-list';
import { SightLinesMode } from '../sight-lines/sight-lines';
import {
  answerEditorLoads,
  answerGridLoads,
  editorMap,
  expectMapRequest,
  flushData,
  flushError,
  mapZones,
  releaseModeLoads,
  zoneConfig,
} from '../testing/editor-fixtures';
import { releaseMapsLoads } from '../testing/maps-loads';
import { ZonesBasesMode } from '../zones-bases/zones-bases';
import { CellPointer, MapCanvas, MapFrame, MapLayer } from './map-canvas';
import { MapEditor } from './map-editor';
import { MapEditorStore } from './map-editor.store';

const png = (): Blob => new Blob([new Uint8Array([137, 80, 78, 71])], { type: 'image/png' });

describe('Map editor shell', () => {
  let http: HttpTestingController;
  let harness: RouterTestingHarness;
  let restoreCanvas: () => void;
  // jsdom has no object URLs: stub the pair and restore it after each test.
  const url = URL as unknown as Record<string, unknown>;
  const before = { create: url['createObjectURL'], revoke: url['revokeObjectURL'] };
  let create: ReturnType<typeof vi.fn>;
  let revoke: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    let n = 0;
    create = vi.fn(() => `blob:image-${++n}`);
    revoke = vi.fn();
    url['createObjectURL'] = create;
    url['revokeObjectURL'] = revoke;
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
    document.querySelector('.cdk-overlay-container')?.remove();
    releaseMapsLoads();
    releaseModeLoads();
    http.verify();
    restoreCanvas();
    url['createObjectURL'] = before.create;
    url['revokeObjectURL'] = before.revoke;
    delete (window as Partial<Window>).matchMedia;
    document.documentElement.style.removeProperty('--lf-map-floor');
  });

  const page = (): HTMLElement => harness.routeNativeElement as HTMLElement;
  const text = (el: Element | null | undefined = page()): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const routerUrl = (): string => TestBed.inject(Router).url;
  const stable = (): Promise<void> => harness.fixture.whenStable();
  const editor = () => harness.fixture.debugElement.query(By.directive(MapEditor));
  const store = (): MapEditorStore => editor().injector.get(MapEditorStore);
  const mapCanvas = (): MapCanvas => editor().injector.get(MapCanvas);
  const select = (): HTMLSelectElement =>
    page().querySelector<HTMLSelectElement>('select#zone-size') as HTMLSelectElement;
  const button = (label: string): HTMLButtonElement | undefined =>
    [...page().querySelectorAll('button')].find((b) => text(b) === label);
  const overlay = (): HTMLCanvasElement =>
    page().querySelector<HTMLCanvasElement>('canvas.me-overlay') as HTMLCanvasElement;
  const heatCanvas = (): HTMLCanvasElement =>
    page().querySelector<HTMLCanvasElement>('canvas.me-heatmap') as HTMLCanvasElement;
  const dialog = (): HTMLElement | null => document.querySelector('mat-dialog-container');
  const dialogButton = (label: string): HTMLButtonElement | undefined =>
    [...(dialog()?.querySelectorAll('button') ?? [])].find((b) => text(b) === label);

  /** Answers the processed and original image GETs (`null`: a 500). */
  function answerImages(id: number, processed: Blob | null = png(), original: Blob | null = png()) {
    const reply = (path: string, blob: Blob | null) => {
      const req = expectMapRequest(id, path);
      if (blob) req.flush(blob);
      else req.flush(new Blob(), { status: 500, statusText: 'Server Error' });
    };
    reply('processed-image/', processed);
    reply('image/', original);
  }

  /** Opens `/maps/<id>/editor` with the map, config and grid loads answered. */
  async function open(
    map: ArenaMap = editorMap(),
    config: ZoneConfig = zoneConfig(),
    zones?: MapZones,
  ): Promise<void> {
    await signIn();
    harness = await RouterTestingHarness.create();
    const nav = harness.navigateByUrl(`/maps/${map.id}/editor`);
    await stable();
    answerEditorLoads(map, config, zones);
    await nav;
    await stable();
  }

  /** Opens the default map and answers its images too. */
  async function openReady(): Promise<void> {
    await open();
    answerImages(4);
    await stable();
  }

  describe('header and states', () => {
    it("shows the map's name and a link back to the map list", async () => {
      await openReady();
      expect(text(page().querySelector('h1'))).toBe('Warehouse');
      const back = [...page().querySelectorAll('a')].find((a) => text(a) === 'Back to Maps');
      expect(back?.getAttribute('href')).toBe('/maps');
    });

    it('shows not-found for an unknown map id', async () => {
      await signIn();
      harness = await RouterTestingHarness.create();
      const nav = harness.navigateByUrl('/maps/99/editor');
      await stable();
      flushError(expectMapRequest(99, ''), 404, 'not_found', 'Not found.');
      http.match(() => true);
      await nav;
      await stable();
      expect(text()).toContain('Map not found.');
      expect(select()).toBeNull();
    });

    it('shows a load failure as an error', async () => {
      await signIn();
      harness = await RouterTestingHarness.create();
      const nav = harness.navigateByUrl('/maps/4/editor');
      await stable();
      flushError(expectMapRequest(4, ''), 500, 'server_error', 'Boom.');
      http.match(() => true);
      await nav;
      await stable();
      expect(text(page().querySelector('[role="alert"]'))).toBe('Could not load the map. Boom.');
    });

    it('shows a failed grid load as an error', async () => {
      await signIn();
      harness = await RouterTestingHarness.create();
      const nav = harness.navigateByUrl('/maps/4/editor');
      await stable();
      flushData(expectMapRequest(4, ''), editorMap());
      flushData(expectMapRequest(4, 'zone-config/'), zoneConfig());
      flushError(expectMapRequest(4, 'zones/'), 500, 'server_error', 'Boom.');
      http.match((r) => r.url.endsWith('/spawn-cells/'));
      await nav;
      await stable();
      answerImages(4);
      await stable();
      expect(text(page().querySelector('[role="alert"]'))).toContain(
        'Could not load the Cell grid.',
      );
    });
  });

  describe('Zone-size picker', () => {
    it('offers exactly the Django options and opens at the confirmed Zone size', async () => {
      await openReady();
      const options = [...select().options];
      expect(options.map((o) => o.value)).toEqual(['20', '30', '40', '50', '60', '80', '100']);
      expect(options.map((o) => text(o))).toEqual([
        '20px',
        '30px',
        '40px',
        '50px',
        '60px',
        '80px',
        '100px',
      ]);
      expect(select().value).toBe('40');
    });

    it('opens at 50 px without a confirmed config', async () => {
      await open(
        editorMap({ has_confirmed_config: false, confirmed_zone_size: null }),
        zoneConfig({ zone_size: null, zones: null, bases: [] }),
      );
      answerImages(4);
      await stable();
      expect(select().value).toBe('50');
    });

    function pick(size: string): void {
      select().value = size;
      select().dispatchEvent(new Event('change'));
    }

    it('reloads the grid at the new size without asking when nothing is unsaved', async () => {
      await openReady();
      pick('20');
      await stable();
      expect(dialog()).toBeNull();
      answerGridLoads(4, mapZones(20));
      await stable();
      expect(store().zoneSize()).toBe(20);
      expect(overlay().width).toBe(160);
    });

    it('asks first while dirty; Cancel keeps the old size and loads nothing', async () => {
      await openReady();
      store().setDirty('zoneConfig', true);
      pick('20');
      await stable();
      expect(text(dialog())).toContain('Discard unsaved changes?');
      dialogButton('Cancel')?.click();
      await stable();
      expect(dialog()).toBeNull();
      http.expectNone((r) => r.url.endsWith('/zones/'));
      expect(select().value).toBe('40');
      expect(store().zoneSize()).toBe(40);
      expect(store().dirty()).toBe(true);
    });

    it('asks first while dirty; Discard reloads the grid at the new size', async () => {
      await openReady();
      store().setDirty('sightLines', true);
      pick('60');
      await stable();
      dialogButton('Discard')?.click();
      await stable();
      answerGridLoads(4, mapZones(60));
      await stable();
      expect(store().zoneSize()).toBe(60);
      expect(store().dirty()).toBe(false);
    });
  });

  describe('leaving the editor', () => {
    it('leaves without asking when nothing is unsaved', async () => {
      await openReady();
      await harness.navigateByUrl('/maps');
      expect(dialog()).toBeNull();
      expect(routerUrl()).toBe('/maps');
    });

    /** Lets a navigation reach its guard's dialog (whenStable would wait for the navigation). */
    async function untilDialog(): Promise<void> {
      await vi.waitFor(() => {
        TestBed.tick();
        expect(dialog()).not.toBeNull();
      });
    }

    it('asks first while dirty, and stays on Cancel', async () => {
      await openReady();
      store().setDirty('strongSpots', true);
      const nav = harness.navigateByUrl('/maps');
      await untilDialog();
      expect(text(dialog())).toContain('Discard unsaved changes?');
      dialogButton('Cancel')?.click();
      await nav;
      expect(routerUrl()).toBe('/maps/4/editor');
      expect(editor()).not.toBeNull();
    });

    it('asks first while dirty, and leaves on Discard', async () => {
      await openReady();
      store().setDirty('zoneConfig', true);
      const nav = harness.navigateByUrl('/maps');
      await untilDialog();
      dialogButton('Discard')?.click();
      await nav;
      expect(routerUrl()).toBe('/maps');
      expect(harness.fixture.debugElement.query(By.directive(MapList))).not.toBeNull();
    });
  });

  describe('mode toggle', () => {
    const routed = () => ({
      zones: harness.fixture.debugElement.query(By.directive(ZonesBasesMode)) !== null,
      sight: harness.fixture.debugElement.query(By.directive(SightLinesMode)) !== null,
      heat: harness.fixture.debugElement.query(By.directive(HeatmapMode)) !== null,
    });

    it('opens in Zones & Bases and routes each toggle to its mode component', async () => {
      await openReady();
      const group = page().querySelector('[role="group"][aria-label="Editor mode"]');
      expect([...(group?.querySelectorAll('button') ?? [])].map((b) => text(b))).toEqual([
        'Zones & Bases',
        'Sight Lines',
        'Heatmap',
      ]);
      expect(routed()).toEqual({ zones: true, sight: false, heat: false });
      expect(button('Zones & Bases')?.getAttribute('aria-pressed')).toBe('true');

      button('Sight Lines')?.click();
      await stable();
      expect(routed()).toEqual({ zones: false, sight: true, heat: false });
      expect(button('Sight Lines')?.getAttribute('aria-pressed')).toBe('true');
      expect(button('Zones & Bases')?.getAttribute('aria-pressed')).toBe('false');

      button('Heatmap')?.click();
      await stable();
      expect(routed()).toEqual({ zones: false, sight: false, heat: true });
      expect(store().mode()).toBe('heatmap');
    });

    it('shows the heatmap canvas, not the overlay, only in Heatmap mode', async () => {
      await openReady();
      expect(heatCanvas().hidden).toBe(true);
      expect(overlay().hidden).toBe(false);
      button('Heatmap')?.click();
      await stable();
      expect(heatCanvas().hidden).toBe(false);
      expect(overlay().hidden).toBe(true);
      button('Zones & Bases')?.click();
      await stable();
      expect(heatCanvas().hidden).toBe(true);
      expect(overlay().hidden).toBe(false);
    });
  });

  describe('images', () => {
    it('shows the processed and the original image from Blob object URLs', async () => {
      await openReady();
      const processed = page().querySelector<HTMLImageElement>('img.me-processed');
      const original = page().querySelector<HTMLImageElement>('img.me-original');
      expect(processed?.getAttribute('src')).toBe('blob:image-1');
      expect(original?.getAttribute('src')).toBe('blob:image-2');
      expect(original?.getAttribute('alt')).toBe('Warehouse (original)');
    });

    it('revokes both object URLs when the editor is left', async () => {
      await openReady();
      expect(revoke).not.toHaveBeenCalled();
      await harness.navigateByUrl('/maps');
      expect(revoke.mock.calls.map((c) => c[0]).sort()).toEqual(['blob:image-1', 'blob:image-2']);
    });

    it('shows a failed processed image as an error', async () => {
      await open();
      answerImages(4, null, png());
      await stable();
      expect(text(page().querySelector('[role="alert"]'))).toContain(
        'Could not load the map image.',
      );
      expect(page().querySelector('img.me-processed')).toBeNull();
    });

    it('fetches no original image for a map without one', async () => {
      await open(editorMap({ image: null }));
      expectMapRequest(4, 'processed-image/').flush(png());
      await stable();
      http.expectNone((r) => r.url.endsWith('/image/'));
      expect(text()).toContain('No original image.');
    });
  });

  it('has a legend for every Cell type and overlay', async () => {
    await openReady();
    const items = [...page().querySelectorAll('.me-legend li')].map((li) => text(li));
    expect(items).toEqual([
      'High wall',
      'Floor',
      'Low wall',
      'Windowed wall',
      'Red spawn',
      'Blue spawn',
      'Elevation',
    ]);
  });

  describe('canvas host', () => {
    const nextFrame = (): Promise<void> =>
      new Promise((resolve) => requestAnimationFrame(() => resolve()));

    /** Runs change detection and lets every scheduled redraw land (twice, to drain chains). */
    async function settleFrames(): Promise<void> {
      for (let i = 0; i < 2; i++) {
        TestBed.tick();
        await nextFrame();
      }
    }

    /** Registers `layer` as the active mode's (as a mode component does on creation). */
    function register(layer: MapLayer): () => void {
      return mapCanvas().use(layer);
    }

    it('sizes both canvases to the image (the grid) and scales them by CSS', async () => {
      await openReady();
      expect([overlay().width, overlay().height]).toEqual([160, 120]);
      expect([heatCanvas().width, heatCanvas().height]).toEqual([160, 120]);
    });

    it('forwards pointer events with the Cell under the pointer (null off the grid)', async () => {
      await openReady();
      const seen: CellPointer[] = [];
      register({ pointer: (p) => seen.push(p) });
      // Drawn at half size: 80 x 60 on screen for the 160 x 120 image.
      overlay().getBoundingClientRect = () =>
        ({ left: 10, top: 20, width: 80, height: 60, right: 90, bottom: 80 }) as DOMRect;
      const fire = (type: string, x: number, y: number) =>
        overlay().dispatchEvent(new MouseEvent(type, { clientX: x, clientY: y, bubbles: true }));
      fire('pointerdown', 35, 45); // (25, 25) * 2 = (50, 50) px -> Cell [1, 1]
      fire('pointermove', 89, 79); // (158, 118) px -> Cell [2, 3]
      fire('pointerup', 95, 45); // off the image -> null
      fire('click', 15, 25); // Cell [0, 0]
      expect(seen.map((p) => [p.type, p.cell])).toEqual([
        ['down', [1, 1]],
        ['move', [2, 3]],
        ['up', null],
        ['click', [0, 0]],
      ]);
    });

    it('stops forwarding once the layer is unregistered', async () => {
      await openReady();
      const seen: CellPointer[] = [];
      const unregister = register({ pointer: (p) => seen.push(p) });
      unregister();
      overlay().dispatchEvent(new MouseEvent('click', { bubbles: true }));
      expect(seen).toEqual([]);
    });

    it("hands a layer's draw the grid geometry and coalesces redraws to one frame", async () => {
      await openReady();
      const frames: MapFrame[] = [];
      register({ draw: (f) => frames.push(f) });
      await settleFrames();
      expect(frames.length).toBeGreaterThan(0);
      expect(frames[0]).toMatchObject({ rows: 3, cols: 4, cellSize: 40, width: 160, height: 120 });

      frames.length = 0;
      // A drag: several edits, each followed by change detection, before the next frame.
      store().editZones((s) => ({ ...s, wallMeta: { '0,1': { facing: 'N' } } }));
      TestBed.tick();
      store().editZones((s) => ({ ...s, wallMeta: { '0,2': { facing: 'S' } } }));
      TestBed.tick();
      mapCanvas().requestRedraw();
      TestBed.tick();
      expect(frames.length).toBe(0);
      await nextFrame();
      expect(frames.length).toBe(1);
    });

    it('redraws when a mode-local signal its layer reads through deps changes', async () => {
      await openReady();
      const { signal } = await import('@angular/core');
      const selected = signal(0);
      let draws = 0;
      register({ draw: () => draws++, deps: () => selected() });
      await settleFrames();
      draws = 0;
      selected.set(1);
      await settleFrames();
      expect(draws).toBe(1);
    });

    it('redraws on a store edit alone, handing the edited Cell types', async () => {
      await openReady();
      const frames: MapFrame[] = [];
      register({ draw: (f) => frames.push(f) });
      await settleFrames();
      frames.length = 0;
      store().editZones((s) => ({ ...s, zones: s.zones.map((row) => row.map(() => 5 as const)) }));
      await settleFrames();
      expect(frames.length).toBe(1);
      expect(frames[0].zones[0][0]).toBe(5);
    });

    it('redraws on requestRedraw alone', async () => {
      await openReady();
      let draws = 0;
      register({ draw: () => draws++ });
      await settleFrames();
      draws = 0;
      mapCanvas().requestRedraw();
      await settleFrames();
      expect(draws).toBe(1);
    });

    it('draws the heatmap canvas through drawHeatmap', async () => {
      await openReady();
      const heat: MapFrame[] = [];
      register({ drawHeatmap: (f) => heat.push(f) });
      await settleFrames();
      expect(heat.length).toBeGreaterThan(0);
      expect(heat.every((f) => f.ctx.canvas === heatCanvas())).toBe(true);
    });

    it('re-reads the --lf-map-* tokens and redraws when the colour scheme flips', async () => {
      const listeners: (() => void)[] = [];
      Object.defineProperty(window, 'matchMedia', {
        configurable: true,
        writable: true,
        value: (query: string) => ({
          matches: false,
          media: query,
          addListener: () => undefined,
          removeListener: () => undefined,
          addEventListener: (_: string, fn: () => void) => {
            if (query.includes('prefers-color-scheme')) listeners.push(fn);
          },
          removeEventListener: () => undefined,
        }),
      });
      await openReady();
      const frames: MapFrame[] = [];
      register({ draw: (f) => frames.push(f) });
      await settleFrames();
      frames.length = 0;
      document.documentElement.style.setProperty('--lf-map-floor', 'rgb(1 2 3 / 0.5)');
      listeners.forEach((fn) => fn());
      await settleFrames();
      expect(frames.length).toBe(1);
      expect(frames[0].palette.floor).toBe('rgb(1 2 3 / 0.5)');
    });
  });
});
