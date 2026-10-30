import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import {
  HttpTestingController,
  TestRequest,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { By } from '@angular/platform-browser';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { environment } from '../../../environments/environment';
import { SeasonScope, TeamStatRow } from '../../api/league-stats.models';
import { signIn } from '../../api/testing/auth-fixtures';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';
import {
  badParam,
  failure,
  scopedEnvelope,
  seasonOption,
  seasonScope,
  teamStatRow,
} from '../testing/league-stats-fixtures';
import { leagueNav } from '../testing/league-fixtures';
import { TeamStats } from './team-stats';

const BASE = environment.apiBaseUrl;
const STATS = `${BASE}/leagues/1/team-stats/`;
const NO_SEASON = 'No Season in this League yet — start a Season to populate team stats.';
const NO_TEAMS = 'No teams enrolled in this Season yet.';

describe('Team Stats (/leagues/:id/stats/team-stats, spec #417)', () => {
  let http: HttpTestingController;
  let harness: RouterTestingHarness;

  beforeEach(async () => {
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
  });

  afterEach(() => http.verify());

  const page = (): HTMLElement => harness.routeNativeElement as HTMLElement;
  const q = (selector: string): HTMLElement | null =>
    page().querySelector<HTMLElement>(`app-team-stats ${selector}`);
  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const url = (): string => TestBed.inject(Router).url;

  function statsRequest(): TestRequest {
    return http.expectOne((req) => req.url === STATS);
  }

  /** Every param the request sent. */
  function sent(req: TestRequest): Record<string, string> {
    const params = req.request.params;
    return Object.fromEntries(params.keys().map((k) => [k, params.get(k) ?? '']));
  }

  async function answer(
    rows: TeamStatRow[] = [teamStatRow()],
    scope: SeasonScope = seasonScope(),
  ): Promise<Record<string, string>> {
    const req = statsRequest();
    const params = sent(req);
    req.flush(scopedEnvelope(rows, { season_scope: scope }));
    await harness.fixture.whenStable();
    return params;
  }

  /** Opens `path`, answers the layout's nav, and returns the first stats request's params. */
  async function open(
    path = '/leagues/1/stats/team-stats',
    rows?: TeamStatRow[],
    scope?: SeasonScope,
  ): Promise<Record<string, string>> {
    await harness.navigateByUrl(path);
    http.expectOne(`${BASE}/leagues/1/nav/`).flush({ data: leagueNav(), meta: {} });
    return answer(rows, scope);
  }

  const headers = (): string[] =>
    [...page().querySelectorAll('#team-stats-table th')].map((th) => text(th));
  const header = (label: string): HTMLAnchorElement =>
    [...page().querySelectorAll<HTMLAnchorElement>('#team-stats-table th a')].find((a) =>
      text(a).startsWith(label),
    )!;
  const cells = (id: number): string[] =>
    [...page().querySelectorAll(`#team-stats-row-${id} td`)].map((td) => text(td));
  const seasonSelect = (): HTMLSelectElement =>
    page().querySelector<HTMLSelectElement>('#stats-season-select')!;

  async function click(link: HTMLAnchorElement): Promise<void> {
    link.click();
    await harness.fixture.whenStable();
  }

  it('asks for the displayed Season sorted by Team ascending by default', async () => {
    expect(await open()).toEqual({ ordering: 'team' });
    expect(harness.fixture.debugElement.query(By.directive(TeamStats))).not.toBeNull();
  });

  it("heads the page with the League's name", async () => {
    await open();
    expect(text(q('h1'))).toBe('Metro League — Team Stats');
  });

  it('has the 13 team_stats.html columns, Team marked as the active ascending sort', async () => {
    await open();
    expect(headers()).toEqual([
      'Team ↑',
      'Avg Pts For',
      'Avg Pts Against',
      'Avg Margin',
      'Avg Survivors',
      'Tags Landed',
      'Times Tagged',
      'Base Captures',
      'Missiles Fired',
      'Missiles Hit',
      'Nukes Fired',
      'Nukes Landed',
      'Cancelled Nukes',
    ]);
  });

  it("shows a row in the template's formats: 1 decimal, survivors 2, counts whole", async () => {
    await open();
    expect(cells(3)).toEqual([
      'Red Raiders',
      '9120.5',
      '8011.0',
      '1109.4',
      '2.38',
      '412',
      '380',
      '6',
      '30',
      '21',
      '4',
      '4',
      '1',
    ]);
    const numeric = [...page().querySelectorAll('#team-stats-row-3 td')].map((td) =>
      td.classList.contains('num'),
    );
    expect(numeric).toEqual([false, ...Array(12).fill(true)]);
  });

  it('shows the rows in the order the API sent', async () => {
    await open('/leagues/1/stats/team-stats', [
      teamStatRow({ team_id: 5, team_name: 'Blue Bolts', team: { id: 5, name: 'Blue Bolts' } }),
      teamStatRow(),
    ]);
    const ids = [...page().querySelectorAll('#team-stats-table tbody tr')].map((tr) => tr.id);
    expect(ids).toEqual(['team-stats-row-5', 'team-stats-row-3']);
  });

  describe('sorting (I1)', () => {
    it('a new header sorts ascending, in the URL and the request', async () => {
      await open();
      await click(header('Avg Margin'));
      expect(url()).toBe('/leagues/1/stats/team-stats?sort=avg_margin&dir=asc');
      expect(await answer()).toEqual({ ordering: 'avg_margin' });
      expect(headers()[3]).toBe('Avg Margin ↑');
      expect(headers()[0]).toBe('Team');
    });

    it('the active header toggles to descending', async () => {
      await open('/leagues/1/stats/team-stats?sort=avg_margin&dir=asc');
      await click(header('Avg Margin'));
      expect(url()).toBe('/leagues/1/stats/team-stats?sort=avg_margin&dir=desc');
      expect(await answer()).toEqual({ ordering: '-avg_margin' });
      expect(headers()[3]).toBe('Avg Margin ↓');
    });

    it('reads the sort and Season from the URL', async () => {
      expect(
        await open('/leagues/1/stats/team-stats?season=career&sort=avg_survivors&dir=desc'),
      ).toEqual({ season: 'career', ordering: '-avg_survivors' });
    });

    it('takes the default for a hand-edited sort it does not know', async () => {
      expect(await open('/leagues/1/stats/team-stats?sort=bogus&dir=up')).toEqual({
        ordering: 'team',
      });
    });
  });

  describe('Season picker', () => {
    it('lists the Seasons as "name (year)" plus Career, on the resolved scope', async () => {
      await open(
        '/leagues/1/stats/team-stats',
        [teamStatRow()],
        seasonScope({
          selected: { id: 10, name: 'Season 0' },
          options: [seasonOption(), seasonOption({ id: 10, name: 'Season 0', year: null })],
        }),
      );
      expect([...seasonSelect().options].map((o) => text(o))).toEqual([
        'Season 1 (2026)',
        'Season 0',
        'Career',
      ]);
      expect(seasonSelect().value).toBe('10');
    });

    it('a Season change keeps the sort and reloads that Season', async () => {
      await open('/leagues/1/stats/team-stats?sort=avg_margin&dir=desc');
      const navigate = vi.spyOn(TestBed.inject(Router), 'navigate');
      seasonSelect().value = 'career';
      seasonSelect().dispatchEvent(new Event('change'));
      await harness.fixture.whenStable();
      expect(url()).toBe('/leagues/1/stats/team-stats?season=career&sort=avg_margin&dir=desc');
      // A picked Season is a new history entry; only an I2 fallback replaces the URL.
      expect(navigate.mock.calls.map(([, extras]) => extras?.replaceUrl)).toEqual([false]);
      expect(await answer()).toEqual({ season: 'career', ordering: '-avg_margin' });
    });
  });

  describe('lenient fallback (I2)', () => {
    it('drops a stale season the API rejects, replacing the URL, and reloads', async () => {
      await harness.navigateByUrl('/leagues/1/stats/team-stats?season=99&sort=avg_margin&dir=asc');
      http.expectOne(`${BASE}/leagues/1/nav/`).flush({ data: leagueNav(), meta: {} });
      const navigate = vi.spyOn(TestBed.inject(Router), 'navigate');
      statsRequest().flush(...badParam('season'));
      await harness.fixture.whenStable();
      expect(url()).toBe('/leagues/1/stats/team-stats?sort=avg_margin&dir=asc');
      expect(navigate.mock.calls.map(([, extras]) => extras?.replaceUrl)).toEqual([true]);
      expect(await answer()).toEqual({ ordering: 'avg_margin' });
      expect(q('[role="alert"]')).toBeNull();
      expect(cells(3)[0]).toBe('Red Raiders');
    });

    it('drops an ordering the API rejects', async () => {
      await harness.navigateByUrl('/leagues/1/stats/team-stats?sort=avg_margin&dir=desc');
      http.expectOne(`${BASE}/leagues/1/nav/`).flush({ data: leagueNav(), meta: {} });
      statsRequest().flush(...badParam('ordering'));
      await harness.fixture.whenStable();
      expect(url()).toBe('/leagues/1/stats/team-stats');
      expect(await answer()).toEqual({ ordering: 'team' });
    });
  });

  describe('empty states (I3)', () => {
    it('shows the no-Season notice, and no table, for a League with no Season', async () => {
      await open('/leagues/1/stats/team-stats', [], seasonScope({ selected: null, options: [] }));
      expect(text(q('#team-stats-empty-notice'))).toBe(NO_SEASON);
      expect(q('#team-stats-table')).toBeNull();
    });

    it('shows the no-teams notice for a Season with no rows', async () => {
      await open('/leagues/1/stats/team-stats', []);
      expect(text(q('#team-stats-empty-notice'))).toBe(NO_TEAMS);
      expect(q('#team-stats-table')).toBeNull();
    });

    it('shows no notice while there are rows', async () => {
      await open();
      expect(q('#team-stats-empty-notice')).toBeNull();
    });
  });

  it("shows any other failure in the error box with the server's message", async () => {
    await harness.navigateByUrl('/leagues/1/stats/team-stats');
    http.expectOne(`${BASE}/leagues/1/nav/`).flush({ data: leagueNav(), meta: {} });
    statsRequest().flush(...failure(500, 'Database is down.'));
    await harness.fixture.whenStable();
    expect(text(q('[role="alert"]'))).toBe('Database is down.');
    expect(q('#team-stats-table')).toBeNull();
    expect(url()).toBe('/leagues/1/stats/team-stats');
  });

  it("shows the layout's not-found state for another Account's League (I7)", async () => {
    await harness.navigateByUrl('/leagues/1/stats/team-stats');
    http.expectOne(`${BASE}/leagues/1/nav/`).flush(...failure(404, 'Not found.'));
    http.match((req) => req.url === STATS);
    await harness.fixture.whenStable();
    expect(text(page().querySelector('h1'))).toBe('League not found');
    expect(page().querySelector('app-team-stats')).toBeNull();
  });
});
