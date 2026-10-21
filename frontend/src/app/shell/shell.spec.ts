import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { environment } from '../../environments/environment';
import { releaseTeamsLoads, signIn } from '../api/testing/auth-fixtures';
import { routes } from '../app.routes';
import { authInterceptor } from '../auth/auth.interceptor';

const BASE = environment.apiBaseUrl;

/** jsdom has no `matchMedia`; this one reports a viewport `width` px wide. */
function viewport(width: number): void {
  const matches = (query: string): boolean => {
    const min = /min-width:\s*(\d+)px/.exec(query);
    const max = /max-width:\s*([\d.]+)px/.exec(query);
    return (!min || width >= Number(min[1])) && (!max || width <= Number(max[1]));
  };
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: (query: string) => ({
      matches: matches(query),
      media: query,
      onchange: null,
      addListener: () => undefined,
      removeListener: () => undefined,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      dispatchEvent: () => false,
    }),
  });
}

describe('App shell (layout B)', () => {
  let http: HttpTestingController;
  let harness: RouterTestingHarness;

  async function open(url: string, width = 1280): Promise<void> {
    viewport(width);
    TestBed.configureTestingModule({
      providers: [
        provideRouter(routes),
        provideHttpClient(withInterceptors([authInterceptor])),
        provideHttpClientTesting(),
      ],
    });
    http = TestBed.inject(HttpTestingController);
    await signIn();
    harness = await RouterTestingHarness.create();
    await harness.navigateByUrl(url);
  }

  afterEach(() => {
    releaseTeamsLoads();
    http.verify();
    delete (window as Partial<Window>).matchMedia;
  });

  const shell = (): HTMLElement => harness.fixture.nativeElement as HTMLElement;
  const header = (): HTMLElement | null => shell().querySelector('header');
  const sidenav = (): HTMLElement | null => shell().querySelector('mat-sidenav');
  const routerUrl = (): string => TestBed.inject(Router).url;

  function button(label: string): HTMLButtonElement | undefined {
    return [...shell().querySelectorAll('button')].find(
      (b) => b.textContent?.trim() === label || b.getAttribute('aria-label') === label,
    );
  }

  it('shows the app title and the signed-in email in the header', async () => {
    await open('/teams');
    expect(header()?.textContent).toContain('Laserforce Simulator');
    expect(header()?.textContent).toContain('ann@example.com');
  });

  it('does not use the Material 2 `color` input on the header toolbar', async () => {
    // Under the Material 3 `mat.theme()` mixin `color="primary"` is a no-op; the colours come
    // from `mat.toolbar-overrides` in shell.scss instead.
    await open('/teams');
    const toolbar = header()?.querySelector('mat-toolbar');
    expect(toolbar).toBeTruthy();
    expect(toolbar?.hasAttribute('color')).toBe(false);
  });

  it('signs out through token/logout/ and lands on /login', async () => {
    await open('/teams');
    button('Sign out')?.click();
    const req = http.expectOne(`${BASE}/token/logout/`);
    expect(req.request.method).toBe('POST');
    req.flush(null, { status: 204, statusText: 'No Content' });
    await harness.fixture.whenStable();
    expect(routerUrl()).toBe('/login');
    expect(shell().textContent).not.toContain('ann@example.com');
  });

  it('has a Teams entry in the sidebar that opens /teams', async () => {
    await open('/teams/3');
    const link = [...(sidenav()?.querySelectorAll('a') ?? [])].find(
      (a) => a.textContent?.trim() === 'Teams',
    );
    expect(link).toBeDefined();
    link?.click();
    await harness.fixture.whenStable();
    expect(routerUrl()).toBe('/teams');
  });

  it('keeps the sidebar open beside the page at 768px and wider, with no menu toggle', async () => {
    await open('/teams', 768);
    expect(sidenav()?.classList).toContain('mat-drawer-side');
    expect(sidenav()?.classList).toContain('mat-drawer-opened');
    expect(button('Toggle navigation')).toBeUndefined();
  });

  it('turns the sidebar into a closed drawer below 768px, opened by the header toggle', async () => {
    await open('/teams', 767);
    expect(sidenav()?.classList).toContain('mat-drawer-over');
    expect(sidenav()?.classList).not.toContain('mat-drawer-opened');
    button('Toggle navigation')?.click();
    await harness.fixture.whenStable();
    expect(sidenav()?.classList).toContain('mat-drawer-opened');
  });
});
