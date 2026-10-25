import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { MATERIAL_ANIMATIONS } from '@angular/material/core';
import { By } from '@angular/platform-browser';
import { RouterTestingHarness } from '@angular/router/testing';
import { provideRouter } from '@angular/router';
import { TestRequest } from '@angular/common/http/testing';
import { MapHeatmap } from '../../api/maps.models';
import { signIn } from '../../api/testing/auth-fixtures';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';
import { stubCanvasContext } from '../../matches/testing/fake-canvas';
import { MapCanvas } from '../map-editor/map-canvas';
import { MapEditor } from '../map-editor/map-editor';
import { MapEditorStore } from '../map-editor/map-editor.store';
import {
  answerEditorLoads,
  answerGridLoads,
  expectMapRequest,
  flushData,
  heatmap,
  mapZones,
  releaseModeLoads,
} from '../testing/editor-fixtures';

const png = (): Blob => new Blob([new Uint8Array([137, 80, 78, 71])], { type: 'image/png' });

describe('Heatmap mode', () => {
  let http: HttpTestingController;
  let harness: RouterTestingHarness;
  let restoreCanvas: () => void;
  // jsdom has no object URLs: stub the pair and restore it after each test.
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
  const mode = (): HTMLElement => page().querySelector('app-heatmap-mode') as HTMLElement;

  /** The one pending heatmap GET. */
  const heatmapRequest = (): TestRequest => expectMapRequest(4, 'heatmap/');

  /** Opens the editor (map, config, grid and images answered) and switches to Heatmap. */
  async function openHeatmap(): Promise<void> {
    await signIn();
    harness = await RouterTestingHarness.create();
    const nav = harness.navigateByUrl('/maps/4/editor');
    await stable();
    answerEditorLoads();
    await nav;
    await stable();
    expectMapRequest(4, 'processed-image/').flush(png());
    expectMapRequest(4, 'image/').flush(png());
    await stable();
    button('Heatmap')?.click();
    await stable();
  }

  async function answer(req: TestRequest, data: MapHeatmap = heatmap()): Promise<void> {
    flushData(req, data);
    await stable();
  }

  it('loads the heatmap for the Zone size on entry, both Sides, and shows the round count', async () => {
    await openHeatmap();
    const req = heatmapRequest();
    expect(req.request.params.get('zone_size')).toBe('40');
    expect(req.request.params.has('team_color')).toBe(false);
    await answer(req, heatmap({ round_count: 7 }));
    expect(text(mode())).toContain('Rounds aggregated: 7');
    expect(store().heatmapStatus()).toBe('ready');
  });

  it('shows a notice, not a round count, when no rounds were played at the Zone size', async () => {
    await openHeatmap();
    await answer(heatmapRequest(), heatmap({ cell_occupancy: {}, round_count: 0 }));
    expect(text(mode())).toContain('No rounds have been played at this Zone size (40px) yet.');
    expect(text(mode())).not.toContain('Rounds aggregated');
  });

  it('shows a load failure as an error', async () => {
    await openHeatmap();
    heatmapRequest().flush(
      { errors: [{ code: 'server_error', message: 'Boom.', field: null }], meta: {} },
      { status: 500, statusText: 'Server Error' },
    );
    await stable();
    expect(text(mode().querySelector('[role="alert"]'))).toBe('Could not load the heatmap.');
    expect(text(mode())).not.toContain('Rounds aggregated');
  });

  it('says it is loading until the heatmap arrives', async () => {
    await openHeatmap();
    const req = heatmapRequest();
    expect(text(mode())).toContain('Loading the heatmap...');
    await answer(req);
    expect(text(mode())).not.toContain('Loading');
  });

  it('reloads at the new Zone size, keeping the Side, when the Zone size changes', async () => {
    await openHeatmap();
    await answer(heatmapRequest());
    const side = page().querySelector<HTMLSelectElement>('select#heatmap-side')!;
    side.value = 'red';
    side.dispatchEvent(new Event('change'));
    await stable();
    await answer(heatmapRequest());

    const zoneSize = page().querySelector<HTMLSelectElement>('select#zone-size')!;
    zoneSize.value = '20';
    zoneSize.dispatchEvent(new Event('change'));
    await stable();
    answerGridLoads(4, mapZones(20));
    const req = heatmapRequest();
    expect(req.request.params.get('zone_size')).toBe('20');
    expect(req.request.params.get('team_color')).toBe('red');
    await answer(req, heatmap({ cell_occupancy: {}, zone_size: 20, round_count: 0 }));
    expect(text(mode())).toContain('No rounds have been played at this Zone size (20px) yet.');
  });

  it('paints the heatmap canvas, shown only in this mode, the other overlays unaffected', async () => {
    await openHeatmap();
    await answer(heatmapRequest());
    const canvases = () => ({
      heat: page().querySelector<HTMLCanvasElement>('canvas.me-heatmap')!.hidden,
      overlay: page().querySelector<HTMLCanvasElement>('canvas.me-overlay')!.hidden,
    });
    const layer = () =>
      harness.fixture.debugElement.query(By.directive(MapEditor)).injector.get(MapCanvas).layer();
    expect(canvases()).toEqual({ heat: false, overlay: true });
    expect(layer()?.drawHeatmap).toBeTypeOf('function');
    expect(layer()?.draw).toBeUndefined();

    button('Sight Lines')?.click();
    await stable();
    releaseModeLoads();
    await stable();
    expect(canvases()).toEqual({ heat: true, overlay: false });
    expect(layer()?.drawHeatmap).toBeUndefined();

    button('Zones & Bases')?.click();
    await stable();
    expect(canvases()).toEqual({ heat: true, overlay: false });
  });

  describe('heatmap canvas', () => {
    let calls: string[];
    let restoreRecorder: () => void;

    /** Records `clear` / `fill` on the heatmap canvas's (no-op) context. */
    beforeEach(() => {
      calls = [];
      const proto = HTMLCanvasElement.prototype;
      const stubbed = proto.getContext;
      proto.getContext = function (this: HTMLCanvasElement, ...args: unknown[]) {
        const ctx = (stubbed as (...a: unknown[]) => CanvasRenderingContext2D).apply(this, args);
        if (!this.classList.contains('me-heatmap')) return ctx;
        return new Proxy(ctx, {
          get: (target, prop) => {
            if (prop === 'clearRect') return () => calls.push('clear');
            if (prop === 'fillRect') return () => calls.push('fill');
            return Reflect.get(target, prop);
          },
        });
      } as typeof stubbed;
      restoreRecorder = () => (proto.getContext = stubbed);
    });

    afterEach(() => restoreRecorder());

    /** The fills of the last paint (after its clear). */
    const lastPaintFills = (): number => calls.length - 1 - calls.lastIndexOf('clear');
    const frames = async (): Promise<void> => {
      for (let i = 0; i < 2; i++) {
        TestBed.tick();
        await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
      }
    };

    it('fills one Cell per hot Cell, and none once a reload has no rounds', async () => {
      await openHeatmap();
      await answer(
        heatmapRequest(),
        heatmap({ cell_occupancy: { '1,1': 30, '0,2': 5, '2,0': 0 } }),
      );
      await frames();
      expect(calls).toContain('clear');
      expect(lastPaintFills()).toBe(2);

      const side = page().querySelector<HTMLSelectElement>('select#heatmap-side')!;
      side.value = 'blue';
      side.dispatchEvent(new Event('change'));
      await stable();
      await answer(heatmapRequest(), heatmap({ cell_occupancy: {}, round_count: 0 }));
      await frames();
      expect(lastPaintFills()).toBe(0);
    });
  });

  describe('Side filter', () => {
    const side = (): HTMLSelectElement =>
      page().querySelector<HTMLSelectElement>('select#heatmap-side') as HTMLSelectElement;
    const pick = async (value: string): Promise<void> => {
      side().value = value;
      side().dispatchEvent(new Event('change'));
      await stable();
    };

    it('offers Both, Red and Blue, Both selected', async () => {
      await openHeatmap();
      await answer(heatmapRequest());
      expect([...side().options].map((o) => [o.value, text(o)])).toEqual([
        ['', 'Both'],
        ['red', 'Red'],
        ['blue', 'Blue'],
      ]);
      expect(side().value).toBe('');
      expect(text(page().querySelector('label[for="heatmap-side"]'))).toBe('Side');
    });

    it('reloads with team_color for Red / Blue, and without it for Both', async () => {
      await openHeatmap();
      await answer(heatmapRequest());

      await pick('red');
      let req = heatmapRequest();
      // The last Side's count is not shown as this one's while it loads.
      expect(text(mode())).not.toContain('Rounds aggregated');
      expect(text(mode())).toContain('Loading the heatmap...');
      expect(req.request.params.get('team_color')).toBe('red');
      expect(req.request.params.get('zone_size')).toBe('40');
      await answer(req, heatmap({ round_count: 3 }));
      expect(text(mode())).toContain('Rounds aggregated: 3');

      await pick('blue');
      req = heatmapRequest();
      expect(req.request.params.get('team_color')).toBe('blue');
      await answer(req);

      await pick('');
      req = heatmapRequest();
      expect(req.request.params.has('team_color')).toBe(false);
      await answer(req);
    });

    it('keeps the Side when the mode is left and re-entered', async () => {
      await openHeatmap();
      await answer(heatmapRequest());
      await pick('blue');
      await answer(heatmapRequest());
      button('Zones & Bases')?.click();
      await stable();
      button('Heatmap')?.click();
      await stable();
      const req = heatmapRequest();
      expect(req.request.params.get('team_color')).toBe('blue');
      await answer(req);
      expect(side().value).toBe('blue');
    });
  });
});
