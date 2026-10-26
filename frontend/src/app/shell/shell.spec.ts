import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { environment } from '../../environments/environment';
import { SELF_INSERT, USER, signIn } from '../api/testing/auth-fixtures';
import { User } from '../auth/user';
import { releaseMatchesLoads } from '../matches/testing/matches-loads';
import { releaseMapsLoads } from '../maps/testing/maps-loads';
import { releasePlayersLoads } from '../players/testing/players-loads';
import { releaseTeamsLoads } from '../teams/testing/teams-loads';
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

  async function open(url: string, width = 1280, user: User = USER): Promise<void> {
    viewport(width);
    TestBed.configureTestingModule({
      providers: [
        provideRouter(routes),
        provideHttpClient(withInterceptors([authInterceptor])),
        provideHttpClientTesting(),
      ],
    });
    http = TestBed.inject(HttpTestingController);
    await signIn(user);
    harness = await RouterTestingHarness.create();
    await harness.navigateByUrl(url);
  }

  afterEach(() => {
    releaseTeamsLoads();
    releasePlayersLoads();
    releaseMatchesLoads();
    releaseMapsLoads();
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

  /** The open account menu's items (Material renders them in the overlay, outside the shell). */
  function menuItems(): HTMLElement[] {
    return [...document.querySelectorAll<HTMLElement>('.mat-mdc-menu-panel [mat-menu-item]')];
  }

  function menuItem(label: string): HTMLElement | undefined {
    return menuItems().find((item) => item.textContent?.trim() === label);
  }

  async function openAccountMenu(): Promise<void> {
    button('Account')?.click();
    await harness.fixture.whenStable();
  }

  const LINKED: User = { ...USER, self_insert: SELF_INSERT };

  it('puts the email on a labelled account-menu button, with the full email as its title', async () => {
    await open('/teams');
    const account = button('Account');
    expect(account?.textContent).toContain('ann@example.com');
    expect(account?.getAttribute('title')).toBe('ann@example.com');
  });

  it('turns the account button into an icon below 600px', async () => {
    await open('/teams', 599);
    expect(button('Account')).toBeDefined();
    expect(header()?.textContent).not.toContain('ann@example.com');
  });

  it('offers Change password and Sign out, but no Self-insert items when none is linked', async () => {
    await open('/teams');
    await openAccountMenu();
    expect(menuItems().map((item) => item.textContent?.trim())).toEqual([
      'Change password',
      'Sign out',
    ]);
  });

  it('offers My player and Unlink Self-insert first when a Self-insert is linked', async () => {
    await open('/teams', 1280, LINKED);
    await openAccountMenu();
    expect(menuItems().map((item) => item.textContent?.trim())).toEqual([
      'My player',
      'Unlink Self-insert',
      'Change password',
      'Sign out',
    ]);
    // ANG-06-8 changed the target on purpose: My player now opens the Player's own detail page
    // (`/teams/:team/players/:id`), which ANG-06-8a added; it used to land on the Team.
    expect(menuItem('My player')?.getAttribute('href')).toBe('/teams/3/players/9');
  });

  it('opens player detail from My player', async () => {
    await open('/teams', 1280, LINKED);
    await openAccountMenu();
    menuItem('My player')?.click();
    await harness.fixture.whenStable();
    expect(routerUrl()).toBe('/teams/3/players/9');
  });

  it('opens Change password from the menu', async () => {
    await open('/teams');
    await openAccountMenu();
    menuItem('Change password')?.click();
    await harness.fixture.whenStable();
    expect(routerUrl()).toBe('/account/password');
  });

  it('unlinks the Self-insert from the menu and says so', async () => {
    await open('/teams', 1280, LINKED);
    await openAccountMenu();
    menuItem('Unlink Self-insert')?.click();
    const req = http.expectOne(`${BASE}/accounts/me/self-insert/`);
    expect(req.request.method).toBe('DELETE');
    req.flush(null, { status: 204, statusText: 'No Content' });
    await harness.fixture.whenStable();
    expect(document.body.textContent).toContain('Self-insert unlinked.');
    await openAccountMenu();
    expect(menuItem('My player')).toBeUndefined();
  });

  it('keeps the Self-insert and shows the generic message when unlinking fails', async () => {
    await open('/teams', 1280, LINKED);
    await openAccountMenu();
    menuItem('Unlink Self-insert')?.click();
    http
      .expectOne(`${BASE}/accounts/me/self-insert/`)
      .error(new ProgressEvent('error'), { status: 0 });
    await harness.fixture.whenStable();
    expect(document.body.textContent).toContain('Something went wrong. Please try again.');
    await openAccountMenu();
    expect(menuItem('My player')).toBeDefined();
  });

  it('signs out through token/logout/ and lands on /login', async () => {
    await open('/teams');
    // Sign out sits in the account menu (ANG-06 item 7), no longer on the toolbar itself.
    await openAccountMenu();
    menuItem('Sign out')?.click();
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

  it('has a Players entry right after Teams that opens /players', async () => {
    await open('/teams');
    const links = [...(sidenav()?.querySelectorAll('a') ?? [])];
    const labels = links.map((a) => a.textContent?.trim());
    expect(labels.indexOf('Players')).toBe(labels.indexOf('Teams') + 1);
    links.find((a) => a.textContent?.trim() === 'Players')?.click();
    await harness.fixture.whenStable();
    expect(routerUrl()).toBe('/players');
  });

  // ANG-06-8 put Players between Teams and Matches on purpose; the order stays one entry per area.
  it('has a Matches entry right after Players that opens /matches', async () => {
    await open('/teams');
    const links = [...(sidenav()?.querySelectorAll('a') ?? [])];
    const labels = links.map((a) => a.textContent?.trim());
    expect(labels.indexOf('Matches')).toBe(labels.indexOf('Players') + 1);
    links.find((a) => a.textContent?.trim() === 'Matches')?.click();
    await harness.fixture.whenStable();
    expect(routerUrl()).toBe('/matches');
  });

  it('has a Maps entry right after Matches that opens /maps', async () => {
    await open('/teams');
    const links = [...(sidenav()?.querySelectorAll('a') ?? [])];
    const labels = links.map((a) => a.textContent?.trim());
    expect(labels.indexOf('Maps')).toBe(labels.indexOf('Matches') + 1);
    links.find((a) => a.textContent?.trim() === 'Maps')?.click();
    await harness.fixture.whenStable();
    expect(routerUrl()).toBe('/maps');
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
