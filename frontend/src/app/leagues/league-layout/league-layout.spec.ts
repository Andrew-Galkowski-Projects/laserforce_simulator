import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import {
  HttpTestingController,
  TestRequest,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { environment } from '../../../environments/environment';
import { LeagueNav } from '../../api/leagues.models';
import { signIn } from '../../api/testing/auth-fixtures';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';
import { leagueNav } from '../testing/league-fixtures';
import { watchResult } from '../testing/league-players-fixtures';
import {
  pagedEnvelope,
  playerRatingRow,
  playerStatRow,
  seasonScope,
} from '../testing/league-stats-fixtures';

const BASE = environment.apiBaseUrl;

/** jsdom has no `matchMedia`; this one reports a viewport `width` px wide. */
function viewport(width: number): void {
  const matches = (query: string): boolean => {
    const min = /min-width:\s*(\d+)px/.exec(query);
    return !min || width >= Number(min[1]);
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

describe('League layout (spec #409 F4 / F5)', () => {
  let http: HttpTestingController;
  let harness: RouterTestingHarness;

  async function setUp(width: number): Promise<void> {
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
  }

  afterEach(() => {
    // The routed child screens' own loads are their specs' business.
    http.match((req) => !req.url.endsWith('/nav/'));
    http.verify();
    delete (window as Partial<Window>).matchMedia;
  });

  const page = (): HTMLElement => harness.routeNativeElement as HTMLElement;
  const nav = (): HTMLElement | null => page().querySelector('nav.league-nav');
  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const routerUrl = (): string => TestBed.inject(Router).url;

  function navRequest(id = 1): TestRequest {
    return http.expectOne(`${BASE}/leagues/${id}/nav/`);
  }

  async function open(url = '/leagues/1', body: LeagueNav = leagueNav(), width = 1280) {
    await setUp(width);
    await harness.navigateByUrl(url);
    navRequest().flush({ data: body, meta: {} });
    await harness.fixture.whenStable();
  }

  function fail(status: number, message: string) {
    return [
      { errors: [{ code: 'x', message, field: null }], meta: {} },
      { status, statusText: 'Error' },
    ] as const;
  }

  it("loads the League's nav with GET leagues/{id}/nav/", async () => {
    await setUp(1280);
    await harness.navigateByUrl('/leagues/1/history');
    const req = navRequest();
    expect(req.request.method).toBe('GET');
    req.flush({ data: leagueNav(), meta: {} });
    await harness.fixture.whenStable();
  });

  it('shows the League name above its nav', async () => {
    await open();
    expect(text(nav()?.querySelector('.league-nav__name'))).toBe('Metro League');
  });

  it('groups the 23 entries under LEAGUE / TEAM / PLAYERS / STATS, in order', async () => {
    await open();
    const items = [...(nav()?.querySelectorAll('h2, li') ?? [])].map((el) =>
      el.tagName === 'H2' ? `# ${text(el)}` : text(el).replace(/ Soon$/, ''),
    );
    expect(items).toEqual([
      'Dashboard',
      '# LEAGUE',
      'Standings',
      'Schedule',
      'Playoffs',
      'Finances',
      'History',
      'Power Rankings',
      '# TEAM',
      'Roster',
      'Schedule',
      'Finances',
      'History',
      '# PLAYERS',
      'Free Agents',
      'Trade',
      'Trading Block',
      'Prospects',
      'Watch List',
      'Hall of Fame',
      '# STATS',
      'Game Log',
      'League Leaders',
      'Player Ratings',
      'Player Stats',
      'Team Stats',
      'Statistical Feats',
    ]);
  });

  it('links Dashboard, History, the stats and team / player screens; every other entry is a Soon span (I1)', async () => {
    await open();
    const links = [...(nav()?.querySelectorAll('a') ?? [])];
    expect(links.map((a) => [text(a), a.getAttribute('href')])).toEqual([
      ['Dashboard', '/leagues/1'],
      ['Playoffs', '/leagues/1/playoffs'],
      ['History', '/leagues/1/history'],
      ['Power Rankings', '/leagues/1/power-rankings'],
      ['Roster', '/leagues/1/team/roster'],
      ['Schedule', '/leagues/1/team/schedule'],
      ['History', '/leagues/1/team/history'],
      ['Free Agents', '/leagues/1/players/free-agents'],
      ['Watch List', '/leagues/1/players/watch-list'],
      ['Game Log', '/leagues/1/stats/game-log'],
      ['League Leaders', '/leagues/1/stats/league-leaders'],
      ['Player Ratings', '/leagues/1/stats/player-ratings'],
      ['Player Stats', '/leagues/1/stats/player-stats'],
      ['Team Stats', '/leagues/1/stats/team-stats'],
      ['Statistical Feats', '/leagues/1/stats/statistical-feats'],
    ]);
    const soon = [...(nav()?.querySelectorAll('span.soon') ?? [])];
    expect(soon).toHaveLength(8);
    expect(soon.every((s) => text(s).endsWith('Soon'))).toBe(true);
  });

  it('marks the Dashboard entry active on the dashboard, and only it', async () => {
    await open('/leagues/1');
    const active = [...(nav()?.querySelectorAll('[aria-current="page"]') ?? [])];
    expect(active.map((a) => text(a))).toEqual(['Dashboard']);
    expect(active[0].classList).toContain('league-nav__link--active');
  });

  it('marks the History entry active on History, and only it', async () => {
    await open('/leagues/1/history');
    const active = [...(nav()?.querySelectorAll('[aria-current="page"]') ?? [])];
    expect(active.map((a) => text(a))).toEqual(['History']);
  });

  it('opens History from its entry', async () => {
    await open('/leagues/1');
    const history = [...(nav()?.querySelectorAll('a') ?? [])].find((a) => text(a) === 'History');
    history?.click();
    await harness.fixture.whenStable();
    expect(routerUrl()).toBe('/leagues/1/history');
  });

  it('shows the nav beside the page at 768px and wider, with no League menu toggle', async () => {
    await open('/leagues/1', leagueNav(), 768);
    expect(nav()).not.toBeNull();
    expect(page().querySelector('.league-layout')?.classList).toContain('league-layout--wide');
    expect(page().querySelector('button.league-layout__toggle')).toBeNull();
  });

  it('collapses the nav behind a "League menu" toggle below 768px', async () => {
    await open('/leagues/1', leagueNav(), 767);
    const toggle = page().querySelector<HTMLButtonElement>('button.league-layout__toggle');
    expect(text(toggle)).toBe('League menu');
    expect(toggle?.getAttribute('aria-expanded')).toBe('false');
    expect(nav()).toBeNull();

    toggle?.click();
    await harness.fixture.whenStable();
    expect(toggle?.getAttribute('aria-expanded')).toBe('true');
    expect(nav()).not.toBeNull();

    toggle?.click();
    await harness.fixture.whenStable();
    expect(nav()).toBeNull();
  });

  it("shows not-found for a 404 (another Account's League), with no routed screen", async () => {
    await setUp(1280);
    await harness.navigateByUrl('/leagues/9');
    http.expectOne(`${BASE}/leagues/9/nav/`).flush(...fail(404, 'Not found.'));
    await harness.fixture.whenStable();
    expect(text(page().querySelector('h1'))).toBe('League not found');
    const back = [...page().querySelectorAll('a')].find((a) => text(a).includes('Back to Leagues'));
    expect(back?.getAttribute('href')).toBe('/leagues');
    expect(nav()).toBeNull();
    expect(page().querySelector('app-league-dashboard')).toBeNull();
    expect(page().querySelector('[role="alert"]')).toBeNull();
  });

  it('shows another load failure in the error box, and Retry loads the nav again (I2)', async () => {
    await setUp(1280);
    await harness.navigateByUrl('/leagues/1');
    navRequest().flush(...fail(500, 'Database is down.'));
    await harness.fixture.whenStable();
    const alert = page().querySelector('[role="alert"]');
    expect(text(alert)).toContain('Database is down.');
    expect(nav()).toBeNull();
    expect(text(page().querySelector('h1'))).not.toBe('League not found');

    const retry = [...page().querySelectorAll('button')].find((b) => text(b) === 'Retry');
    expect(retry).toBeDefined();
    retry?.click();
    await harness.fixture.whenStable();
    navRequest().flush({ data: leagueNav(), meta: {} });
    await harness.fixture.whenStable();
    expect(page().querySelector('[role="alert"]')).toBeNull();
    expect(text(nav()?.querySelector('.league-nav__name'))).toBe('Metro League');
  });

  it('shares one WatchState across its screens: a ★ toggled on one stays on the next (F3)', async () => {
    await open('/leagues/1/stats/player-stats');
    http
      .expectOne((req) => req.url === `${BASE}/leagues/1/player-stats/`)
      .flush(
        pagedEnvelope([playerStatRow({ watched: false })], 1, {
          season_scope: seasonScope(),
          team_options: [],
        }),
      );
    await harness.fixture.whenStable();
    const flag = (): HTMLButtonElement =>
      page().querySelector<HTMLButtonElement>('app-player-cell button')!;
    flag().click();
    await harness.fixture.whenStable();
    const put = http.expectOne(`${BASE}/leagues/1/watch-list/25/`);
    expect(put.request.method).toBe('PUT');
    put.flush({ data: watchResult(true), meta: {} });
    await harness.fixture.whenStable();
    expect(flag().getAttribute('aria-pressed')).toBe('true');

    await harness.navigateByUrl('/leagues/1/stats/player-ratings');
    http
      .expectOne((req) => req.url === `${BASE}/leagues/1/player-ratings/`)
      .flush(pagedEnvelope([playerRatingRow({ watched: false })], 1, { team_options: [] }));
    await harness.fixture.whenStable();
    expect(flag().getAttribute('aria-pressed')).toBe('true');
  });

  it('loads the new League when the :id changes', async () => {
    await open('/leagues/1');
    await harness.navigateByUrl('/leagues/2');
    http
      .expectOne(`${BASE}/leagues/2/nav/`)
      .flush({ data: leagueNav({ league: { id: 2, name: 'Rival', mode: 'sandbox' } }), meta: {} });
    await harness.fixture.whenStable();
    expect(text(nav()?.querySelector('.league-nav__name'))).toBe('Rival');
  });
});
