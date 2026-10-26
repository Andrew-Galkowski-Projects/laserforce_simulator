import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import {
  HttpTestingController,
  TestRequest,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { HarnessLoader } from '@angular/cdk/testing';
import { TestbedHarnessEnvironment } from '@angular/cdk/testing/testbed';
import { MATERIAL_ANIMATIONS } from '@angular/material/core';
import { MatFormFieldHarness } from '@angular/material/form-field/testing';
import { MatSelectHarness } from '@angular/material/select/testing';
import { By } from '@angular/platform-browser';
import { provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { environment } from '../../../environments/environment';
import { RoundHeatmap as RoundHeatmapData } from '../../api/rounds.models';
import { signIn } from '../../api/testing/auth-fixtures';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';
import { heatColour } from '../../maps/heatmap/heat-cells';
import { RoundDetail } from '../round-detail/round-detail';
import { stubCanvasContext } from '../testing/fake-canvas';
import { roundDetail } from '../testing/match-fixtures';
import { RoundHeatmap } from './round-heatmap';

const BASE = environment.apiBaseUrl;
const IMAGE_URL = `${BASE}/maps/7/processed-image/`;
const ZONE = 40;

/** Ace (red CMD), Cleo (red SCT), Bolt (blue CMD), Dax (blue SCT), in API roster order. */
function heatmapData(overrides: Partial<RoundHeatmapData> = {}): RoundHeatmapData {
  return {
    round_id: 9,
    has_map: true,
    arena_map: { id: 7, name: 'Warehouse' },
    zone_size: ZONE,
    processed_image_url: '/maps/7/processed/',
    cell_occupancy_json: {
      '1': { '0,0': 4, '1,2': 1 },
      '3': { '0,0': 4 },
      '2': { '2,1': 9 },
      '4': { '2,3': 2 },
    },
    player_roster: [
      { id: 1, name: 'Ace', role: 'commander', team_color: 'red' },
      { id: 3, name: 'Cleo', role: 'scout', team_color: 'red' },
      { id: 2, name: 'Bolt', role: 'commander', team_color: 'blue' },
      { id: 4, name: 'Dax', role: 'scout', team_color: 'blue' },
    ],
    ...overrides,
  };
}

/** One recorded `fillRect`, with the fill style and alpha set when it ran. */
interface Fill {
  rect: number[];
  style: unknown;
  alpha: unknown;
}

const png = (): Blob => new Blob([new Uint8Array([137, 80, 78, 71])], { type: 'image/png' });
const failure = (message: string) =>
  [
    { errors: [{ code: 'server_error', message, field: null }], meta: {} },
    { status: 500, statusText: 'Server Error' },
  ] as const;

describe('Movement heatmap (/rounds/:id/heatmap)', () => {
  let http: HttpTestingController;
  let harness: RouterTestingHarness;
  let loader: HarnessLoader;
  let restoreCanvas: () => void;
  let calls: ('clear' | Fill)[];
  // jsdom has no object URLs: stub the pair and restore it after each test.
  const url = URL as unknown as Record<string, unknown>;
  const before = { create: url['createObjectURL'], revoke: url['revokeObjectURL'] };
  let create: ReturnType<typeof vi.fn>;
  let revoke: ReturnType<typeof vi.fn>;

  beforeEach(async () => {
    create = vi.fn(() => 'blob:arena-7');
    revoke = vi.fn();
    url['createObjectURL'] = create;
    url['revokeObjectURL'] = revoke;
    restoreCanvas = stubCanvasContext();
    // Record clears and fills on the (no-op) context, with the style and alpha at fill time.
    calls = [];
    const proto = HTMLCanvasElement.prototype;
    const stubbed = proto.getContext;
    proto.getContext = function (this: HTMLCanvasElement, ...args: unknown[]) {
      const ctx = (stubbed as (...a: unknown[]) => CanvasRenderingContext2D).apply(this, args);
      return new Proxy(ctx, {
        get: (target, prop) => {
          if (prop === 'clearRect') return () => calls.push('clear');
          if (prop === 'fillRect')
            return (...rect: number[]) =>
              calls.push({ rect, style: target.fillStyle, alpha: target.globalAlpha });
          return Reflect.get(target, prop);
        },
      });
    } as typeof stubbed;
    document.documentElement.style.setProperty('--lf-map-heat-low', 'rgb(1, 1, 1)');
    document.documentElement.style.setProperty('--lf-map-heat-high', 'rgb(2, 2, 2)');

    TestBed.configureTestingModule({
      providers: [
        provideRouter(routes),
        provideHttpClient(withInterceptors([authInterceptor])),
        provideHttpClientTesting(),
        { provide: MATERIAL_ANIMATIONS, useValue: { animationsDisabled: true } },
      ],
    });
    http = TestBed.inject(HttpTestingController);
    await signIn();
    harness = await RouterTestingHarness.create();
    loader = TestbedHarnessEnvironment.documentRootLoader(harness.fixture);
  });

  afterEach(() => {
    http.verify({ ignoreCancelled: true });
    restoreCanvas();
    url['createObjectURL'] = before.create;
    url['revokeObjectURL'] = before.revoke;
    document.documentElement.style.removeProperty('--lf-map-heat-low');
    document.documentElement.style.removeProperty('--lf-map-heat-high');
  });

  const page = (): HTMLElement => harness.routeNativeElement as HTMLElement;
  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const link = (label: string): HTMLAnchorElement | undefined =>
    [...page().querySelectorAll('a')].find((a) => text(a).includes(label));
  const stable = (): Promise<void> => harness.fixture.whenStable();
  const alert = (): string => text(page().querySelector('[role="alert"]'));

  /** Opens `/rounds/9/heatmap` and answers the Round GET. */
  async function openRound(): Promise<void> {
    await harness.navigateByUrl('/rounds/9/heatmap');
    http
      .expectOne(`${BASE}/rounds/9/`)
      .flush({ data: roundDetail({ id: 9, team_red_name: 'Comets', team_blue_name: 'Vipers' }) });
    await stable();
  }

  const heatmapRequest = (): TestRequest => http.expectOne(`${BASE}/rounds/9/heatmap/`);

  /** Opens the screen and answers the Round and heatmap GETs (not the map image). */
  async function open(data: RoundHeatmapData = heatmapData()): Promise<void> {
    await openRound();
    heatmapRequest().flush({ data, meta: {} });
    await stable();
  }

  /** Answers the image GET and "loads" the <img> at 400 x 200. */
  async function showImage(): Promise<void> {
    http.expectOne(IMAGE_URL).flush(png());
    await stable();
    const img = page().querySelector('img');
    if (!img) throw new Error('no <img>');
    Object.defineProperty(img, 'naturalWidth', { configurable: true, value: 400 });
    Object.defineProperty(img, 'naturalHeight', { configurable: true, value: 200 });
    img.dispatchEvent(new Event('load'));
    await stable();
  }

  /** The fills of the last paint (after its clear). */
  const lastFills = (): Fill[] => calls.slice(calls.lastIndexOf('clear') + 1) as Fill[];
  const filledRects = (): number[][] =>
    lastFills()
      .map((f) => f.rect)
      .sort((a, b) => a[1] - b[1] || a[0] - b[0]);

  const field = (label: string): Promise<MatFormFieldHarness> =>
    loader.getHarness(MatFormFieldHarness.with({ floatingLabelText: label }));
  async function select(label: string): Promise<MatSelectHarness> {
    const control = await (await field(label)).getControl(MatSelectHarness);
    if (!control) throw new Error(`no ${label} select`);
    return control;
  }
  async function optionTexts(label: string): Promise<string[]> {
    const control = await select(label);
    await control.open();
    const texts = await Promise.all((await control.getOptions()).map((o) => o.getText()));
    await control.close();
    return texts;
  }
  async function pick(label: string, option: string): Promise<void> {
    const control = await select(label);
    await control.open();
    await control.clickOptions({ text: option });
    await stable();
  }

  describe('page head', () => {
    it('routes :id/heatmap to the Movement heatmap with a "Heatmap" kicker and the teams', async () => {
      await open(heatmapData({ has_map: false, arena_map: null }));
      const de = harness.fixture.debugElement;
      expect(de.query(By.directive(RoundHeatmap))).not.toBeNull();
      expect(de.query(By.directive(RoundDetail))).toBeNull();
      expect(text(page().querySelector('.rh-kicker'))).toBe('Heatmap');
      expect(text(page().querySelector('h1'))).toBe('Comets vs Vipers');
    });

    it('links back to the Round, and to its Event log and Missile log', async () => {
      await open(heatmapData({ has_map: false, arena_map: null }));
      expect(link('Back to Round')?.getAttribute('href')).toBe('/rounds/9');
      expect(link('Event log')?.getAttribute('href')).toBe('/rounds/9/events');
      expect(link('Missile log')?.getAttribute('href')).toBe('/rounds/9/missile-log');
    });
  });

  it('says it is loading until the heatmap arrives', async () => {
    await openRound();
    const req = heatmapRequest();
    expect(text(page())).toContain('Loading the heatmap…');
    req.flush({ data: heatmapData({ has_map: false, arena_map: null }), meta: {} });
    await stable();
    expect(text(page())).not.toContain('Loading the heatmap…');
  });

  it('shows "No map — heatmap unavailable." and no image or canvas for a map-less Round', async () => {
    await open(heatmapData({ has_map: false, arena_map: null, zone_size: null }));
    expect(text(page())).toContain('No map — heatmap unavailable.');
    expect(page().querySelector('img')).toBeNull();
    expect(page().querySelector('canvas')).toBeNull();
    http.expectNone(() => true);
  });

  describe('with a map', () => {
    it('fetches the processed image for arena_map.id as a Blob, shown from an object URL', async () => {
      await open();
      const req = http.expectOne(IMAGE_URL);
      expect(req.request.responseType).toBe('blob');
      const blob = png();
      req.flush(blob);
      await stable();
      expect(create).toHaveBeenCalledWith(blob);
      expect(page().querySelector('img')?.getAttribute('src')).toBe('blob:arena-7');
      expect(page().querySelector('canvas')).not.toBeNull();
    });

    it('revokes the object URL when the screen is left', async () => {
      await open();
      await showImage();
      expect(revoke).not.toHaveBeenCalled();
      await harness.navigateByUrl('/rounds/9');
      http.expectOne(`${BASE}/rounds/9/`).flush({ data: roundDetail({ id: 9 }) });
      await stable();
      expect(revoke).toHaveBeenCalledWith('blob:arena-7');
    });

    it('sizes the canvas to the image’s natural size', async () => {
      await open();
      await showImage();
      const canvas = page().querySelector('canvas') as HTMLCanvasElement;
      expect([canvas.width, canvas.height]).toEqual([400, 200]);
    });

    it('fills each hot Cell of every player at zone_size, in the heat tokens', async () => {
      await open();
      await showImage();
      expect(filledRects()).toEqual([
        [0, 0, ZONE, ZONE],
        [2 * ZONE, ZONE, ZONE, ZONE],
        [ZONE, 2 * ZONE, ZONE, ZONE],
        [3 * ZONE, 2 * ZONE, ZONE, ZONE],
      ]);
      // The hottest Cell (Bolt's 9 at 2,1): t = 1.
      const hottest = lastFills().find((f) => f.rect[0] === ZONE && f.rect[1] === 2 * ZONE);
      expect(hottest?.style).toBe(heatColour(1, 'rgb(1, 1, 1)', 'rgb(2, 2, 2)'));
      expect(hottest?.alpha).toBeCloseTo(0.9);
    });

    it('redraws for the Player picked, with no request', async () => {
      await open();
      await showImage();
      await pick('Player', 'Ace (red commander)');
      http.expectNone(() => true);
      expect(filledRects()).toEqual([
        [0, 0, ZONE, ZONE],
        [2 * ZONE, ZONE, ZONE, ZONE],
      ]);
    });

    it('combines the Role and Team pickers', async () => {
      await open();
      await showImage();
      await pick('Team', 'Blue');
      expect(filledRects()).toEqual([
        [ZONE, 2 * ZONE, ZONE, ZONE],
        [3 * ZONE, 2 * ZONE, ZONE, ZONE],
      ]);
      await pick('Role', 'Scout');
      http.expectNone(() => true);
      expect(filledRects()).toEqual([[3 * ZONE, 2 * ZONE, ZONE, ZONE]]);
    });

    it('shows the bare map, the canvas empty, when the pickers match nothing', async () => {
      await open();
      await showImage();
      await pick('Player', 'Ace (red commander)');
      await pick('Team', 'Blue');
      expect(calls.at(-1)).toBe('clear');
      expect(page().querySelector('img')).not.toBeNull();
    });

    it('draws nothing over the bare map when zone_size is null', async () => {
      await open(heatmapData({ zone_size: null }));
      await showImage();
      expect(calls.filter((c) => c !== 'clear')).toEqual([]);
      expect(page().querySelector('img')).not.toBeNull();
      expect(page().querySelector('canvas')).not.toBeNull();
    });
  });

  describe('pickers', () => {
    it('lists All players, then each player as "<name> (<team_color> <role>)" in roster order', async () => {
      await open();
      http.expectOne(IMAGE_URL).flush(png());
      await stable();
      expect(await optionTexts('Player')).toEqual([
        'All players',
        'Ace (red commander)',
        'Cleo (red scout)',
        'Bolt (blue commander)',
        'Dax (blue scout)',
      ]);
      expect(await (await select('Player')).getValueText()).toBe('All players');
    });

    it('lists All and the five roles, and Both, Red and Blue', async () => {
      await open();
      http.expectOne(IMAGE_URL).flush(png());
      await stable();
      expect(await optionTexts('Role')).toEqual([
        'All',
        'Commander',
        'Heavy',
        'Scout',
        'Ammo',
        'Medic',
      ]);
      expect(await optionTexts('Team')).toEqual(['Both', 'Red', 'Blue']);
      expect(await (await select('Role')).getValueText()).toBe('All');
      expect(await (await select('Team')).getValueText()).toBe('Both');
    });

    it('gives the Player picker the wide (name picker) slot', async () => {
      await open();
      http.expectOne(IMAGE_URL).flush(png());
      await stable();
      const player = await (await field('Player')).host();
      expect(await player.hasClass('rh-picker__wide')).toBe(true);
    });
  });

  describe('errors', () => {
    it('shows Round not found on a 404, and asks for no heatmap', async () => {
      await harness.navigateByUrl('/rounds/9/heatmap');
      http
        .expectOne(`${BASE}/rounds/9/`)
        .flush(
          { errors: [{ code: 'not_found', message: 'Not found.', field: null }], meta: {} },
          { status: 404, statusText: 'Not Found' },
        );
      await stable();
      expect(text(page())).toContain('Round not found');
      http.expectNone(`${BASE}/rounds/9/heatmap/`);
    });

    it('shows "Could not load the Round." when the Round fails otherwise', async () => {
      await harness.navigateByUrl('/rounds/9/heatmap');
      http.expectOne(`${BASE}/rounds/9/`).flush(...failure('Boom.'));
      await stable();
      expect(alert()).toContain('Could not load the Round.');
      http.expectNone(`${BASE}/rounds/9/heatmap/`);
    });

    it('shows the server message when the heatmap fails to load', async () => {
      await openRound();
      heatmapRequest().flush(...failure('Replay failed.'));
      await stable();
      expect(alert()).toBe('Replay failed.');
      expect(text(page().querySelector('h1'))).toBe('Comets vs Vipers');
      expect(text(page())).not.toContain('Loading');
    });

    it('shows the server message when the map image fails to load', async () => {
      await open();
      // A Blob request's error body is a Blob, not an envelope: the ApiError's own message.
      http.expectOne(IMAGE_URL).flush(new Blob([]), { status: 503, statusText: 'Unavailable' });
      await stable();
      expect(alert()).toBe('The server responded with 503.');
      expect(page().querySelector('img')).toBeNull();
      expect(page().querySelector('canvas')).toBeNull();
    });
  });
});
