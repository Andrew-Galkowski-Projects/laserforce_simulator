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
import { PowerRankingRow, SeasonScope } from '../../api/league-stats.models';
import { signIn } from '../../api/testing/auth-fixtures';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';
import {
  badParam,
  failure,
  powerRankingRow,
  scopedEnvelope,
  seasonScope,
} from '../testing/league-stats-fixtures';
import { leagueNav } from '../testing/league-fixtures';
import { PowerRankings } from './power-rankings';

const BASE = environment.apiBaseUrl;
const RANKINGS = `${BASE}/leagues/1/power-rankings/`;
const NO_SEASON = 'No Season in this League yet — start a Season to populate the power rankings.';
const EXPLAINER =
  'The power score sums three per-League min-max-normalized components: team mean overall ' +
  'rating, win%, and average per-round score differential. Highest score ranks #1.';

describe('Power Rankings (/leagues/:id/power-rankings, spec #417)', () => {
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
    page().querySelector<HTMLElement>(`app-power-rankings ${selector}`);
  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const url = (): string => TestBed.inject(Router).url;

  function rankingsRequest(): TestRequest {
    return http.expectOne((req) => req.url === RANKINGS);
  }

  function sent(req: TestRequest): Record<string, string> {
    const params = req.request.params;
    return Object.fromEntries(params.keys().map((k) => [k, params.get(k) ?? '']));
  }

  async function answer(
    rows: PowerRankingRow[] = [powerRankingRow()],
    scope: SeasonScope = seasonScope(),
  ): Promise<Record<string, string>> {
    const req = rankingsRequest();
    const params = sent(req);
    req.flush(scopedEnvelope(rows, { season_scope: scope }));
    await harness.fixture.whenStable();
    return params;
  }

  async function open(
    path = '/leagues/1/power-rankings',
    rows?: PowerRankingRow[],
    scope?: SeasonScope,
  ): Promise<Record<string, string>> {
    await harness.navigateByUrl(path);
    http.expectOne(`${BASE}/leagues/1/nav/`).flush({ data: leagueNav(), meta: {} });
    return answer(rows, scope);
  }

  const headers = (): string[] =>
    [...page().querySelectorAll('#power-rankings-table th')].map((th) => text(th));
  const header = (label: string): HTMLAnchorElement =>
    [...page().querySelectorAll<HTMLAnchorElement>('#power-rankings-table th a')].find((a) =>
      text(a).startsWith(label),
    )!;
  const cells = (id: number): HTMLElement[] => [
    ...page().querySelectorAll<HTMLElement>(`#power-rankings-row-${id} td`),
  ];
  const seasonSelect = (): HTMLSelectElement =>
    page().querySelector<HTMLSelectElement>('#stats-season-select')!;

  async function click(link: HTMLAnchorElement): Promise<void> {
    link.click();
    await harness.fixture.whenStable();
  }

  it('asks for the displayed Season by rank ascending by default', async () => {
    expect(await open()).toEqual({ ordering: 'rank' });
    expect(harness.fixture.debugElement.query(By.directive(PowerRankings))).not.toBeNull();
  });

  it("heads the page with the League's name, then the explainer", async () => {
    await open();
    expect(text(q('h1'))).toBe('Metro League — Power Rankings');
    expect(text(q('#power-rankings-explainer'))).toBe(EXPLAINER);
  });

  it('has the SORT_KEYS_DISPLAY columns, Rank marked as the active ascending sort', async () => {
    await open();
    expect(headers()).toEqual([
      'Rank ↑',
      'Team',
      'Mean rating',
      'Win %',
      'Avg score diff',
      'Power score',
    ]);
  });

  it("shows a row in the template's formats, the power score bold", async () => {
    await open('/leagues/1/power-rankings', [
      powerRankingRow({ mean_rating: 64.25, win_pct: 0.6667, avg_score_diff: -12.04 }),
    ]);
    const row = cells(3);
    expect(row.map((td) => text(td))).toEqual([
      '1',
      'Red Raiders',
      '64.3',
      '0.667',
      '-12.0',
      '2.813',
    ]);
    expect(row.map((td) => td.classList.contains('num'))).toEqual([
      true,
      false,
      true,
      true,
      true,
      true,
    ]);
    expect(row[5].classList.contains('strong')).toBe(true);
  });

  it('links the Team to its page', async () => {
    await open();
    expect(cells(3)[1].querySelector('a')?.getAttribute('href')).toBe('/teams/3');
  });

  describe('sorting (I1)', () => {
    it('a new header sorts ascending, in the URL and the request', async () => {
      await open();
      await click(header('Power score'));
      expect(url()).toBe('/leagues/1/power-rankings?sort=power_score&dir=asc');
      expect(await answer()).toEqual({ ordering: 'power_score' });
      expect(headers()[5]).toBe('Power score ↑');
      expect(headers()[0]).toBe('Rank');
    });

    it('the active header toggles to descending', async () => {
      await open();
      await click(header('Rank'));
      expect(url()).toBe('/leagues/1/power-rankings?sort=rank&dir=desc');
      expect(await answer()).toEqual({ ordering: '-rank' });
    });

    it('every header is a sort link', async () => {
      await open();
      const keys = [
        ...page().querySelectorAll<HTMLAnchorElement>('#power-rankings-table th a'),
      ].map((a) => /sort=(\w+)/.exec(a.getAttribute('href') ?? '')?.[1]);
      expect(keys).toEqual([
        'rank',
        'team',
        'mean_rating',
        'win_pct',
        'avg_score_diff',
        'power_score',
      ]);
    });
  });

  describe('Season picker', () => {
    it('offers Career, and a Season change keeps the sort', async () => {
      await open('/leagues/1/power-rankings?sort=win_pct&dir=desc');
      expect([...seasonSelect().options].map((o) => text(o))).toEqual([
        'Season 1 (2026)',
        'Season 0 (2025)',
        'Career',
      ]);
      seasonSelect().value = 'career';
      seasonSelect().dispatchEvent(new Event('change'));
      await harness.fixture.whenStable();
      expect(url()).toBe('/leagues/1/power-rankings?season=career&sort=win_pct&dir=desc');
      expect(await answer()).toEqual({ season: 'career', ordering: '-win_pct' });
    });
  });

  describe('lenient fallback (I2)', () => {
    it('drops a stale season, replacing the URL, and reloads', async () => {
      await harness.navigateByUrl('/leagues/1/power-rankings?season=99&sort=win_pct&dir=asc');
      http.expectOne(`${BASE}/leagues/1/nav/`).flush({ data: leagueNav(), meta: {} });
      const navigate = vi.spyOn(TestBed.inject(Router), 'navigate');
      rankingsRequest().flush(...badParam('season'));
      await harness.fixture.whenStable();
      expect(url()).toBe('/leagues/1/power-rankings?sort=win_pct&dir=asc');
      expect(navigate.mock.calls.map(([, extras]) => extras?.replaceUrl)).toEqual([true]);
      expect(await answer()).toEqual({ ordering: 'win_pct' });
      expect(q('[role="alert"]')).toBeNull();
    });

    it('drops an ordering the API rejects', async () => {
      await harness.navigateByUrl('/leagues/1/power-rankings?season=10&sort=win_pct&dir=desc');
      http.expectOne(`${BASE}/leagues/1/nav/`).flush({ data: leagueNav(), meta: {} });
      rankingsRequest().flush(...badParam('ordering'));
      await harness.fixture.whenStable();
      expect(url()).toBe('/leagues/1/power-rankings?season=10');
      expect(await answer()).toEqual({ season: '10', ordering: 'rank' });
    });

    it('ignores a ?team= the screen has no picker for', async () => {
      expect(await open('/leagues/1/power-rankings?team=5')).toEqual({ ordering: 'rank' });
    });
  });

  describe('empty states (I3)', () => {
    it('shows the no-Season notice, and no explainer or table', async () => {
      await open('/leagues/1/power-rankings', [], seasonScope({ selected: null, options: [] }));
      expect(text(q('#power-rankings-empty-notice'))).toBe(NO_SEASON);
      expect(q('#power-rankings-explainer')).toBeNull();
      expect(q('#power-rankings-table')).toBeNull();
    });

    it('shows no notice while there is a Season', async () => {
      await open();
      expect(q('#power-rankings-empty-notice')).toBeNull();
    });
  });

  it("shows any other failure in the error box with the server's message", async () => {
    await harness.navigateByUrl('/leagues/1/power-rankings');
    http.expectOne(`${BASE}/leagues/1/nav/`).flush({ data: leagueNav(), meta: {} });
    rankingsRequest().flush(...failure(500, 'Database is down.'));
    await harness.fixture.whenStable();
    expect(text(q('[role="alert"]'))).toBe('Database is down.');
    expect(q('#power-rankings-table')).toBeNull();
  });
});
