import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { environment } from '../../../../environments/environment';
import { authInterceptor } from '../../../auth/auth.interceptor';
import { PlaybackMap as PlaybackMapData, PlaybackSnapshot } from '../../../api/rounds.models';
import { POLL_TIMERS } from '../../../shared/testing/poll-timers';
import { playbackMap, playbackMapPlayer, playbackSnapshot } from '../../testing/playback-fixtures';
import { LOCKED_RING, PlaybackMap, readPalette } from './playback-map';
import { MARKER_FOOTPRINT } from './playback-markers';

const BASE = environment.apiBaseUrl;

describe('PlaybackMap (beta, contract only)', () => {
  let fixture: ComponentFixture<PlaybackMap>;
  let http: HttpTestingController;
  // jsdom has no object URLs: stub the pair and restore it after each test.
  const url = URL as unknown as Record<string, unknown>;
  const before = { create: url['createObjectURL'], revoke: url['revokeObjectURL'] };
  let create: ReturnType<typeof vi.fn>;
  let revoke: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    create = vi.fn(() => 'blob:arena-7');
    revoke = vi.fn();
    url['createObjectURL'] = create;
    url['revokeObjectURL'] = revoke;
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(withInterceptors([authInterceptor])),
        provideHttpClientTesting(),
      ],
    });
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    http.verify();
    url['createObjectURL'] = before.create;
    url['revokeObjectURL'] = before.revoke;
  });

  const IMAGE_URL = `${BASE}/maps/7/processed-image/`;
  const ZONE_URL = `${BASE}/maps/7/zone-config/`;
  const png = (): Blob => new Blob([new Uint8Array([137, 80, 78, 71])], { type: 'image/png' });
  const zoneConfig = () => ({
    data: {
      zone_size: 40,
      zones: null,
      wall_meta: {},
      elevation: null,
      red_spawn: [],
      blue_spawn: [],
      bases: [{ type: 'red', x_px: 100, y_px: 120 }],
    },
    meta: {},
  });
  /** Answers the zone-config request the first open sends beside the image request. */
  const answerZoneConfig = (): void => http.expectOne(ZONE_URL).flush(zoneConfig());

  async function render(
    map: PlaybackMapData | null,
    cursor = 0,
    snapshots: ReadonlyMap<number, readonly PlaybackSnapshot[]> = new Map(),
  ): Promise<void> {
    fixture = TestBed.createComponent(PlaybackMap);
    fixture.componentRef.setInput('map', map);
    fixture.componentRef.setInput('cursor', cursor);
    fixture.componentRef.setInput('snapshots', snapshots);
    fixture.componentRef.setInput('respawnTicks', 16);
    fixture.componentRef.setInput('events', []);
    await fixture.whenStable();
  }

  const host = (): HTMLElement => fixture.nativeElement as HTMLElement;
  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const toggle = (): HTMLButtonElement | null =>
    host().querySelector<HTMLButtonElement>('.pm-toggle');

  it('is collapsed behind a "Show map (beta)" toggle, with no panel and no request', async () => {
    await render(playbackMap());
    expect(text(toggle())).toBe('Show map (beta)');
    expect(toggle()?.getAttribute('aria-expanded')).toBe('false');
    expect(host().querySelector('.pm-panel')).toBeNull();
    expect(host().querySelector('.pm-beta')).toBeNull();
    http.expectNone(() => true);
  });

  async function openMap(): Promise<void> {
    toggle()?.click();
    await fixture.whenStable();
  }

  it('the toggle shows the panel with a Beta chip, and hides it again', async () => {
    await render(playbackMap());
    await openMap();
    // The image and zone-config requests belong to the next tests; release them here.
    http.match(IMAGE_URL);
    http.match(ZONE_URL);
    expect(toggle()?.getAttribute('aria-expanded')).toBe('true');
    expect(text(toggle())).toBe('Hide map (beta)');
    expect(host().querySelector('.pm-panel')).not.toBeNull();
    expect(text(host().querySelector('.pm-beta'))).toBe('Beta');

    await openMap();
    expect(host().querySelector('.pm-panel')).toBeNull();
  });

  it("fetches the arena's processed image through getRaw on first open only, shown from an object URL", async () => {
    await render(playbackMap());
    await openMap();
    const req = http.expectOne(IMAGE_URL);
    expect(req.request.method).toBe('GET');
    expect(req.request.responseType).toBe('blob');
    const blob = png();
    req.flush(blob);
    answerZoneConfig();
    await fixture.whenStable();
    expect(create).toHaveBeenCalledWith(blob);
    expect(host().querySelector('.pm-panel img')?.getAttribute('src')).toBe('blob:arena-7');
    expect(host().querySelector('.pm-panel canvas')).not.toBeNull();

    await openMap();
    await openMap();
    http.expectNone(IMAGE_URL);
    http.expectNone(ZONE_URL);
    expect(host().querySelector('.pm-panel img')?.getAttribute('src')).toBe('blob:arena-7');
  });

  it("fetches the map's zone config (for the bases) once, on first open, beside the image", async () => {
    await render(playbackMap());
    http.expectNone(ZONE_URL);
    await openMap();
    const req = http.expectOne(ZONE_URL);
    expect(req.request.method).toBe('GET');
    req.flush(zoneConfig());
    http.expectOne(IMAGE_URL).flush(png());
    await fixture.whenStable();
    expect(host().querySelector('.pm-panel canvas')).not.toBeNull();
  });

  it('a failed zone-config fetch still shows the map, with no notice', async () => {
    await render(playbackMap());
    await openMap();
    http.expectOne(ZONE_URL).flush(null, { status: 500, statusText: 'Server Error' });
    http.expectOne(IMAGE_URL).flush(png());
    await fixture.whenStable();
    expect(host().querySelector('.pm-panel img')?.getAttribute('src')).toBe('blob:arena-7');
    expect(host().querySelector('.pm-panel canvas')).not.toBeNull();
    expect(host().querySelector('.pm-notice')).toBeNull();
  });

  it('revokes the object URL on destroy', async () => {
    await render(playbackMap());
    await openMap();
    http.expectOne(IMAGE_URL).flush(png());
    answerZoneConfig();
    await fixture.whenStable();
    expect(revoke).not.toHaveBeenCalled();
    fixture.destroy();
    expect(revoke).toHaveBeenCalledWith('blob:arena-7');
  });

  const NOTICE = 'No map — playback map unavailable';

  it('shows the "No map" notice for a map-less Round, and requests nothing', async () => {
    await render(null);
    await openMap();
    http.expectNone(() => true);
    expect(text(host().querySelector('.pm-notice'))).toBe(NOTICE);
    expect(host().querySelector('.pm-panel canvas')).toBeNull();
  });

  it('shows the "No map" notice when the image fetch fails', async () => {
    await render(playbackMap());
    await openMap();
    http.expectOne(IMAGE_URL).flush(png(), { status: 404, statusText: 'Not Found' });
    answerZoneConfig();
    await fixture.whenStable();
    expect(text(host().querySelector('.pm-notice'))).toBe(NOTICE);
    expect(host().querySelector('.pm-panel canvas')).toBeNull();
    expect(create).not.toHaveBeenCalled();
  });

  describe('frame loop (RV-07b)', () => {
    // A Scout in rapid fire from tick 10 until tick 20.
    const scoutMap = playbackMap({
      players: [playbackMapPlayer({ role: 'scout', special_cost: 10, max_missiles: 0 })],
    });
    const rapidFire = new Map([
      [
        101,
        [
          playbackSnapshot({ tick: 0 }),
          playbackSnapshot({ tick: 10, rapid_fire: true }),
          playbackSnapshot({ tick: 20 }),
        ],
      ],
    ]);
    // Another spec in this worker may have left its own matchMedia: put back whatever was there.
    let matchMediaBefore: PropertyDescriptor | undefined;

    beforeEach(() => {
      matchMediaBefore = Object.getOwnPropertyDescriptor(window, 'matchMedia');
    });

    afterEach(() => {
      vi.useRealTimers();
      if (matchMediaBefore) {
        Object.defineProperty(window, 'matchMedia', matchMediaBefore);
      } else {
        delete (window as Partial<Window>).matchMedia;
      }
    });

    /** Renders the map at tick 0 (nothing running) and opens it, with its fetches answered. */
    async function renderOpen(): Promise<void> {
      await render(scoutMap, 0, rapidFire);
      await openMap();
      http.expectOne(IMAGE_URL).flush(png());
      answerZoneConfig();
      await fixture.whenStable();
    }

    /** Fakes the interval timers and rAF (`POLL_TIMERS`), after the first render. */
    const fakeFrames = (): void => {
      vi.useFakeTimers({ toFake: POLL_TIMERS });
    };

    async function seek(tick: number): Promise<void> {
      fixture.componentRef.setInput('cursor', tick);
      await fixture.whenStable();
    }

    /**
     * The frames still requested after three frames have run and the page has settled. Angular's
     * scheduler and the button ripple take one-shot frames; only the map's loop asks again.
     */
    async function pendingFrames(): Promise<number> {
      for (let i = 0; i < 3; i++) {
        vi.advanceTimersToNextFrame();
      }
      await fixture.whenStable();
      return vi.getTimerCount();
    }

    it('requests frames while the cursor sits in a rapid-fire run, and stops once it leaves', async () => {
      await renderOpen();
      fakeFrames();
      await seek(12);
      expect(await pendingFrames()).toBe(1);
      await seek(25);
      expect(await pendingFrames()).toBe(0);
    });

    it('requests none while no special runs at the cursor', async () => {
      await renderOpen();
      fakeFrames();
      await seek(5);
      expect(await pendingFrames()).toBe(0);
    });

    it('requests none while the map is closed', async () => {
      await render(scoutMap, 0, rapidFire);
      fakeFrames();
      await seek(12);
      expect(await pendingFrames()).toBe(0);
    });

    it('stops when the map closes, or the component is destroyed', async () => {
      await renderOpen();
      fakeFrames();
      await seek(12);
      expect(await pendingFrames()).toBe(1);
      await openMap();
      expect(await pendingFrames()).toBe(0);
      await openMap();
      expect(await pendingFrames()).toBe(1);
      fixture.destroy();
      vi.advanceTimersToNextFrame();
      expect(vi.getTimerCount()).toBe(0);
    });

    it('requests none under prefers-reduced-motion: reduce', async () => {
      Object.defineProperty(window, 'matchMedia', {
        configurable: true,
        writable: true,
        value: (query: string) => ({
          matches: query === '(prefers-reduced-motion: reduce)',
          media: query,
          addEventListener: () => undefined,
          removeEventListener: () => undefined,
          addListener: () => undefined,
          removeListener: () => undefined,
        }),
      });
      await renderOpen();
      fakeFrames();
      await seek(12);
      expect(await pendingFrames()).toBe(0);
    });
  });
});

describe('overlays round a marker (RV-08 retro)', () => {
  it("draws the locked reticle outside the marker's footprint, where the arcs cannot hide it", () => {
    expect(LOCKED_RING).toBeGreaterThan(MARKER_FOOTPRINT);
  });
});

describe('readPalette', () => {
  it('greys a deactivated marker with --lf-map-marker-off, not the theme-dependent muted text', () => {
    const el = document.createElement('canvas');
    el.style.setProperty('--lf-map-marker-off', '#6b7280');
    el.style.setProperty('--lf-text-muted', '#e0e0e0');
    document.body.append(el);
    try {
      expect(readPalette(el).muted).toBe('#6b7280');
    } finally {
      el.remove();
    }
  });

  /** `readPalette` on a canvas carrying `tokens`. */
  function paletteWith(tokens: Record<string, string>): ReturnType<typeof readPalette> {
    const el = document.createElement('canvas');
    for (const [name, value] of Object.entries(tokens)) {
      el.style.setProperty(name, value);
    }
    document.body.append(el);
    try {
      return readPalette(el);
    } finally {
      el.remove();
    }
  }

  it('reads a gauge token written as rgb(), space- or comma-separated, as its channels', () => {
    const palette = paletteWith({
      '--lf-gauge-full': 'rgb(30 158 62)',
      '--lf-gauge-mid': 'rgb(227, 160, 8)',
    });
    expect(palette.gaugeFull).toEqual([30, 158, 62]);
    expect(palette.gaugeMid).toEqual([227, 160, 8]);
  });

  it("falls back to a gauge token's default, not black, when its value cannot be read", () => {
    const palette = paletteWith({ '--lf-gauge-empty': 'not-a-colour' });
    expect(palette.gaugeEmpty).toEqual([127, 16, 16]);
    expect(paletteWith({}).gaugeFull).toEqual([30, 158, 62]);
  });

  it("paints a running special's stripes with --lf-special-stripe", () => {
    const palette = paletteWith({
      '--lf-special-stripe': '#ffffff',
      '--lf-map-base-outline': 'rgb(1 2 3)',
    });
    expect(palette.stripe).toBe('#ffffff');
  });

  it('draws a missile Lock in --lf-ev-locking, apart from the missile colour (RV-08)', () => {
    const palette = paletteWith({ '--lf-ev-locking': '#b4235a', '--lf-ev-missile': '#a8480a' });
    expect(palette.locking).toBe('#b4235a');
  });
});
