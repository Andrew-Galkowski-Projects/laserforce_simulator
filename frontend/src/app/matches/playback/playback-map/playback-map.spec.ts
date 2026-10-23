import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { environment } from '../../../../environments/environment';
import { authInterceptor } from '../../../auth/auth.interceptor';
import { PlaybackMap as PlaybackMapData } from '../../../api/rounds.models';
import { playbackMap } from '../../testing/playback-fixtures';
import { TIMELINE_PLAYERS } from '../../testing/timeline-fixtures';
import { PlaybackMap } from './playback-map';

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
  const png = (): Blob => new Blob([new Uint8Array([137, 80, 78, 71])], { type: 'image/png' });

  async function render(map: PlaybackMapData | null, cursor = 0): Promise<void> {
    fixture = TestBed.createComponent(PlaybackMap);
    fixture.componentRef.setInput('map', map);
    fixture.componentRef.setInput('players', TIMELINE_PLAYERS);
    fixture.componentRef.setInput('cursor', cursor);
    fixture.componentRef.setInput('snapshots', new Map());
    fixture.componentRef.setInput('respawnTicks', 16);
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
    // The image request belongs to the next test; release it here.
    http.match(`${BASE}/maps/7/processed-image/`);
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
    await fixture.whenStable();
    expect(create).toHaveBeenCalledWith(blob);
    expect(host().querySelector('.pm-panel img')?.getAttribute('src')).toBe('blob:arena-7');
    expect(host().querySelector('.pm-panel canvas')).not.toBeNull();

    await openMap();
    await openMap();
    http.expectNone(IMAGE_URL);
    expect(host().querySelector('.pm-panel img')?.getAttribute('src')).toBe('blob:arena-7');
  });

  it('revokes the object URL on destroy', async () => {
    await render(playbackMap());
    await openMap();
    http.expectOne(IMAGE_URL).flush(png());
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
    await fixture.whenStable();
    expect(text(host().querySelector('.pm-notice'))).toBe(NOTICE);
    expect(host().querySelector('.pm-panel canvas')).toBeNull();
    expect(create).not.toHaveBeenCalled();
  });
});
