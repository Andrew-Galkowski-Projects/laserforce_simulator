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
import { PlayRun } from '../play/play-run';
import { season } from '../seasons/testing/season-fixtures';
import { leagueNav, pageEnvelope, playControls, seasonDashboard } from '../testing/league-fixtures';
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

  it('links Dashboard, Standings, Schedule, History, Finances, the stats and team / player screens; every other entry is a Soon span (I1)', async () => {
    await open();
    const links = [...(nav()?.querySelectorAll('a') ?? [])];
    expect(links.map((a) => [text(a), a.getAttribute('href')])).toEqual([
      ['Dashboard', '/leagues/1'],
      ['Standings', '/leagues/1/seasons/11/standings'],
      ['Schedule', '/leagues/1/seasons/11/schedule'],
      ['Playoffs', '/leagues/1/playoffs'],
      ['Finances', '/leagues/1/finances'],
      ['History', '/leagues/1/history'],
      ['Power Rankings', '/leagues/1/power-rankings'],
      ['Roster', '/leagues/1/team/roster'],
      ['Schedule', '/leagues/1/team/schedule'],
      ['Finances', '/leagues/1/team/finances'],
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
    expect(soon).toHaveLength(4);
    expect(soon.every((s) => text(s).endsWith('Soon'))).toBe(true);
  });

  it('leaves LEAGUE Standings and Schedule out of the nav while the League has no displayed Season', async () => {
    await open('/leagues/1/history', leagueNav({ displayed_season: null }));
    const league = [...(nav()?.querySelectorAll('ul.league-nav__list') ?? [])][1];
    expect([...(league?.querySelectorAll('li') ?? [])].map((li) => text(li))).toEqual([
      'Playoffs',
      'Finances',
      'History',
      'Power Rankings',
    ]);
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

  // ANG-06-19 (spec #454): a finished play run reloads the open screen, as Django's
  // `window.location.reload()` did.
  it('re-sends the nav GET and re-creates the open screen when PlayRun.changed ticks', async () => {
    await open('/leagues/1/history');
    const historyUrl = `${BASE}/leagues/1/history/`;
    http.expectOne((req) => req.url === historyUrl).flush(pageEnvelope([]));
    http.expectOne(`${BASE}/leagues/1/play-controls/`).flush({ data: playControls(), meta: {} });
    await harness.fixture.whenStable();
    const before = page().querySelector('app-league-history');
    expect(before).not.toBeNull();

    TestBed.inject(PlayRun).run('play_two_months');
    http.expectOne(`${BASE}/seasons/11/play-two-months/`).flush({
      data: { job_id: 'job-1', season_id: 11, status_url: '' },
      meta: {},
    });
    http.expectOne(`${BASE}/seasons/11/play-jobs/job-1/`).flush({
      data: { status: 'complete', completed: 8, total: 8, cancelled: false, error: null },
      meta: {},
    });
    await harness.fixture.whenStable();

    navRequest().flush({ data: leagueNav(), meta: {} });
    http.expectOne((req) => req.url === historyUrl);
    http.expectOne(`${BASE}/leagues/1/play-controls/`);
    await harness.fixture.whenStable();
    const after = page().querySelector('app-league-history');
    expect(after).not.toBeNull();
    expect(after).not.toBe(before);
  });

  describe('a play action that lands on a screen (spec #454 fix A / B)', () => {
    const CONTROLS = `${BASE}/leagues/1/play-controls/`;
    const SEASON = `${BASE}/seasons/11/`;
    const DASHBOARD = `${BASE}/seasons/11/dashboard/`;
    const ROUND = `${BASE}/rounds/31/`;
    const count = (url: string): number => http.match((req) => req.url === url).length;

    function answerControls(body = playControls()): void {
      http.expectOne(CONTROLS).flush({ data: body, meta: {} });
    }

    /** Answers the reload a play change sends: the League nav and the controls. */
    async function answerReload(): Promise<void> {
      navRequest().flush({ data: leagueNav(), meta: {} });
      answerControls();
      await harness.fixture.whenStable();
    }

    /** Answers the Season layout's GET, so the Season screen under it is created. */
    async function answerSeason(): Promise<void> {
      http.expectOne(SEASON).flush({ data: season(), meta: {} });
      await harness.fixture.whenStable();
    }

    async function openHistory(controls = playControls()): Promise<void> {
      await open('/leagues/1/history');
      http.expectOne((req) => req.url === `${BASE}/leagues/1/history/`).flush(pageEnvelope([]));
      answerControls(controls);
      await harness.fixture.whenStable();
    }

    async function playWeek(): Promise<void> {
      TestBed.inject(PlayRun).run('play_week');
      http.expectOne(`${BASE}/seasons/11/play-week/`).flush({ data: seasonDashboard(), meta: {} });
      await harness.fixture.whenStable();
    }

    it('creates the Season dashboard once when the action lands there from another screen', async () => {
      await openHistory();
      await playWeek();
      await answerReload();
      expect(routerUrl()).toBe('/leagues/1/seasons/11');
      // One Season GET (expectOne) and one dashboard GET: the screen was not created twice.
      await answerSeason();
      expect(count(DASHBOARD)).toBe(1);
    });

    it('re-creates the Season dashboard when the action is played from it', async () => {
      await open('/leagues/1/seasons/11');
      await answerSeason();
      answerControls();
      expect(count(DASHBOARD)).toBe(1);
      const before = page().querySelector('app-season-dashboard');
      expect(before).not.toBeNull();

      await playWeek();
      await answerReload();
      await answerSeason();
      expect(count(DASHBOARD)).toBe(1);
      const after = page().querySelector('app-season-dashboard');
      expect(after).not.toBeNull();
      expect(after).not.toBe(before);
    });

    it('creates the Live screen once when a Live action lands there', async () => {
      await openHistory(playControls({ live_preview_available: true }));
      TestBed.inject(PlayRun).playLive('season');
      http.expectOne(`${BASE}/seasons/11/play-live/`).flush({
        data: { kind: 'rr', round_ids: [31], job: null },
        meta: {},
      });
      await harness.fixture.whenStable();
      await answerReload();
      await answerSeason();
      expect(count(ROUND)).toBe(1);
      expect(routerUrl()).toBe('/leagues/1/seasons/11/live?rounds=31');
    });

    it('keeps the Live screen when a Job finishes on it; the nav and controls still reload', async () => {
      await open('/leagues/1/seasons/11/live?rounds=31');
      await answerSeason();
      answerControls();
      expect(count(ROUND)).toBe(1);
      const before = page().querySelector('app-season-live');
      expect(before).not.toBeNull();

      TestBed.inject(PlayRun).run('play_two_months');
      http.expectOne(`${BASE}/seasons/11/play-two-months/`).flush({
        data: { job_id: 'job-1', season_id: 11, status_url: '' },
        meta: {},
      });
      http.expectOne(`${BASE}/seasons/11/play-jobs/job-1/`).flush({
        data: { status: 'complete', completed: 8, total: 8, cancelled: false, error: null },
        meta: {},
      });
      await harness.fixture.whenStable();
      await answerReload();
      expect(count(SEASON)).toBe(0);
      expect(count(ROUND)).toBe(0);
      expect(page().querySelector('app-season-live')).toBe(before);
    });
  });

  it('does not reload on its first look at PlayRun.changed', async () => {
    await open('/leagues/1/history');
    await harness.fixture.whenStable();
    http.expectNone(`${BASE}/leagues/1/nav/`);
    expect(http.match((req) => req.url === `${BASE}/leagues/1/history/`).length).toBe(1);
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
