import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { environment } from '../../../../environments/environment';
import { authInterceptor } from '../../../auth/auth.interceptor';
import { PlaybackMap as PlaybackMapData } from '../../../api/rounds.models';
import { playbackMap } from '../../testing/playback-fixtures';
import { PlaybackMap, readPalette } from './playback-map';

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

  async function render(map: PlaybackMapData | null, cursor = 0): Promise<void> {
    fixture = TestBed.createComponent(PlaybackMap);
    fixture.componentRef.setInput('map', map);
    fixture.componentRef.setInput('cursor', cursor);
    fixture.componentRef.setInput('snapshots', new Map());
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
});
