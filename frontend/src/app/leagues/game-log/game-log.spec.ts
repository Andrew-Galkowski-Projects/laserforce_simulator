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
import { TeamRef } from '../../api/analytics.models';
import { GameLogRow, SeasonScope } from '../../api/league-stats.models';
import { signIn } from '../../api/testing/auth-fixtures';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';
import {
  badParam,
  failure,
  gameLogRow,
  scopedEnvelope,
  seasonScope,
  teamOptions,
} from '../testing/league-stats-fixtures';
import { leagueNav } from '../testing/league-fixtures';
import { GameLog } from './game-log';
import { releasePlayControls } from '../testing/leagues-loads';

const BASE = environment.apiBaseUrl;
const LOG = `${BASE}/leagues/1/game-log/`;
const NO_SEASON = 'No Season in this League yet — start a Season to populate the game log.';
const NO_GAMES = 'No games played yet in this Season.';

describe('Game Log (/leagues/:id/stats/game-log, spec #417)', () => {
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

  afterEach(() => {
    releasePlayControls();
    http.verify();
  });

  const page = (): HTMLElement => harness.routeNativeElement as HTMLElement;
  const q = (selector: string): HTMLElement | null =>
    page().querySelector<HTMLElement>(`app-game-log ${selector}`);
  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const url = (): string => TestBed.inject(Router).url;

  function logRequest(): TestRequest {
    return http.expectOne((req) => req.url === LOG);
  }

  function sent(req: TestRequest): Record<string, string> {
    const params = req.request.params;
    return Object.fromEntries(params.keys().map((k) => [k, params.get(k) ?? '']));
  }

  async function answer(
    rows: GameLogRow[] = [gameLogRow()],
    scope: SeasonScope = seasonScope(),
    teams: TeamRef[] = teamOptions(),
  ): Promise<Record<string, string>> {
    const req = logRequest();
    const params = sent(req);
    req.flush(scopedEnvelope(rows, { season_scope: scope, team_options: teams }));
    await harness.fixture.whenStable();
    return params;
  }

  async function open(
    path = '/leagues/1/stats/game-log',
    rows?: GameLogRow[],
    scope?: SeasonScope,
  ): Promise<Record<string, string>> {
    await harness.navigateByUrl(path);
    http.expectOne(`${BASE}/leagues/1/nav/`).flush({ data: leagueNav(), meta: {} });
    return answer(rows, scope);
  }

  const headers = (): string[] =>
    [...page().querySelectorAll('#game-log-table th')].map((th) => text(th));
  const header = (label: string): HTMLAnchorElement =>
    [...page().querySelectorAll<HTMLAnchorElement>('#game-log-table th a')].find((a) =>
      text(a).startsWith(label),
    )!;
  const cells = (id: number): HTMLElement[] => [
    ...page().querySelectorAll<HTMLElement>(`#game-log-row-${id} td`),
  ];
  const seasonSelect = (): HTMLSelectElement =>
    page().querySelector<HTMLSelectElement>('#stats-season-select')!;
  const teamSelect = (): HTMLSelectElement | null =>
    page().querySelector<HTMLSelectElement>('#stats-team-select');

  async function click(link: HTMLAnchorElement): Promise<void> {
    link.click();
    await harness.fixture.whenStable();
  }

  async function choose(select: HTMLSelectElement, value: string): Promise<void> {
    select.value = value;
    select.dispatchEvent(new Event('change'));
    await harness.fixture.whenStable();
  }

  it('asks for the displayed Season by date ascending by default', async () => {
    expect(await open()).toEqual({ ordering: 'date_played' });
    expect(harness.fixture.debugElement.query(By.directive(GameLog))).not.toBeNull();
  });

  it("heads the page with the League's name", async () => {
    await open();
    expect(text(q('h1'))).toBe('Metro League — Game Log');
  });

  it('has the game_log.html columns, Date marked as the active ascending sort', async () => {
    await open();
    expect(headers()).toEqual(['Matchday', 'Date ↑', 'Red', 'Blue', 'Score', 'Winner']);
  });

  it('shows a row: matchday, date, the Teams, red–blue score and winner', async () => {
    await open();
    expect(cells(70).map((td) => text(td))).toEqual([
      '1',
      '2026-10-05',
      'Red Raiders',
      'Blue Bolts',
      '9120–8011',
      'Red Raiders',
    ]);
    expect(cells(70)[0].classList.contains('num')).toBe(true);
    expect(cells(70)[4].classList.contains('center')).toBe(true);
  });

  it('shows the date in UTC, as the server does', async () => {
    await open('/leagues/1/stats/game-log', [
      // 02:00Z is still the 5th anywhere west of UTC-2 (the dev machines' zone).
      gameLogRow({ round: { ...gameLogRow().round, date_played: '2026-10-06T02:00:00Z' } }),
    ]);
    expect(text(cells(70)[1])).toBe('2026-10-06');
  });

  it('links Red, Blue and Score to the Round', async () => {
    await open();
    const hrefs = cells(70).map((td) => td.querySelector('a')?.getAttribute('href') ?? null);
    expect(hrefs).toEqual([null, null, '/rounds/70', '/rounds/70', '/rounds/70', null]);
  });

  it('shows "—" for a tie and for a missing Team', async () => {
    const row = gameLogRow({ winner: null });
    const missing = { ...row.round, team_blue: null as unknown as TeamRef };
    await open('/leagues/1/stats/game-log', [{ ...row, round: missing }]);
    expect(text(cells(70)[3])).toBe('—');
    expect(text(cells(70)[5])).toBe('—');
  });

  describe('sorting (I1)', () => {
    it('a new header sorts ascending, in the URL and the request', async () => {
      await open();
      await click(header('Score'));
      expect(url()).toBe('/leagues/1/stats/game-log?sort=score&dir=asc');
      expect(await answer()).toEqual({ ordering: 'score' });
      expect(headers()).toEqual(['Matchday', 'Date', 'Red', 'Blue', 'Score ↑', 'Winner']);
    });

    it('the active header toggles to descending', async () => {
      await open();
      await click(header('Date'));
      expect(url()).toBe('/leagues/1/stats/game-log?sort=date_played&dir=desc');
      expect(await answer()).toEqual({ ordering: '-date_played' });
      expect(headers()[1]).toBe('Date ↓');
    });

    it('every header is a sort link', async () => {
      await open();
      for (const key of ['matchday', 'team_red', 'team_blue', 'winner']) {
        const link = [...page().querySelectorAll<HTMLAnchorElement>('#game-log-table th a')].find(
          (a) => a.getAttribute('href')?.includes(`sort=${key}&`),
        );
        expect(link, key).toBeDefined();
      }
    });
  });

  describe('Team picker', () => {
    it('lists "All teams" plus meta.team_options', async () => {
      await open();
      expect([...teamSelect()!.options].map((o) => text(o))).toEqual([
        'All teams',
        'Blue Bolts',
        'Red Raiders',
      ]);
    });

    it('a Team change narrows the request, keeping the Season and sort', async () => {
      await open('/leagues/1/stats/game-log?season=10&sort=score&dir=desc');
      await choose(teamSelect()!, '5');
      expect(url()).toBe('/leagues/1/stats/game-log?season=10&team=5&sort=score&dir=desc');
      expect(await answer()).toEqual({ season: '10', team_id: '5', ordering: '-score' });
      expect(teamSelect()!.value).toBe('5');
    });

    it('starts on the Team the URL names', async () => {
      expect(await open('/leagues/1/stats/game-log?team=5')).toEqual({
        team_id: '5',
        ordering: 'date_played',
      });
      expect(teamSelect()!.value).toBe('5');
    });

    it('a Season change clears the Team', async () => {
      await open('/leagues/1/stats/game-log?team=5');
      await choose(seasonSelect(), 'career');
      expect(url()).toBe('/leagues/1/stats/game-log?season=career');
      expect(await answer()).toEqual({ season: 'career', ordering: 'date_played' });
    });

    it('offers Career on the Season picker', async () => {
      await open();
      expect([...seasonSelect().options].map((o) => text(o))).toEqual([
        'Season 1 (2026)',
        'Season 0 (2025)',
        'Career',
      ]);
    });
  });

  describe('lenient fallback (I2)', () => {
    async function reject(path: string, field: string): Promise<void> {
      await harness.navigateByUrl(path);
      http.expectOne(`${BASE}/leagues/1/nav/`).flush({ data: leagueNav(), meta: {} });
      logRequest().flush(...badParam(field));
      await harness.fixture.whenStable();
    }

    it('drops a stale season, replacing the URL, and reloads', async () => {
      await harness.navigateByUrl('/leagues/1/stats/game-log?season=99');
      http.expectOne(`${BASE}/leagues/1/nav/`).flush({ data: leagueNav(), meta: {} });
      const navigate = vi.spyOn(TestBed.inject(Router), 'navigate');
      logRequest().flush(...badParam('season'));
      await harness.fixture.whenStable();
      expect(url()).toBe('/leagues/1/stats/game-log');
      expect(navigate.mock.calls.map(([, extras]) => extras?.replaceUrl)).toEqual([true]);
      expect(await answer()).toEqual({ ordering: 'date_played' });
      expect(q('[role="alert"]')).toBeNull();
    });

    it('drops a Team the API rejects', async () => {
      await reject('/leagues/1/stats/game-log?team=99&sort=score&dir=asc', 'team_id');
      expect(url()).toBe('/leagues/1/stats/game-log?sort=score&dir=asc');
      expect(await answer()).toEqual({ ordering: 'score' });
    });

    it('drops an ordering the API rejects', async () => {
      await reject('/leagues/1/stats/game-log?team=5&sort=score&dir=desc', 'ordering');
      expect(url()).toBe('/leagues/1/stats/game-log?team=5');
      expect(await answer()).toEqual({ team_id: '5', ordering: 'date_played' });
    });
  });

  describe('empty states (I3)', () => {
    it('shows the no-Season notice, and no Team picker or table', async () => {
      await open('/leagues/1/stats/game-log', [], seasonScope({ selected: null, options: [] }));
      expect(text(q('#game-log-empty-notice'))).toBe(NO_SEASON);
      expect(q('#game-log-table')).toBeNull();
      expect(teamSelect()).toBeNull();
    });

    it('shows the no-games notice for a Season with no Rounds', async () => {
      await open('/leagues/1/stats/game-log', []);
      expect(text(q('#game-log-empty-notice'))).toBe(NO_GAMES);
      expect(q('#game-log-table')).toBeNull();
      expect(teamSelect()).not.toBeNull();
    });

    it('shows no notice while there are rows', async () => {
      await open();
      expect(q('#game-log-empty-notice')).toBeNull();
    });
  });

  it("shows any other failure in the error box with the server's message", async () => {
    await harness.navigateByUrl('/leagues/1/stats/game-log');
    http.expectOne(`${BASE}/leagues/1/nav/`).flush({ data: leagueNav(), meta: {} });
    logRequest().flush(...failure(500, 'Database is down.'));
    await harness.fixture.whenStable();
    expect(text(q('[role="alert"]'))).toBe('Database is down.');
    expect(q('#game-log-table')).toBeNull();
  });

  it("shows the layout's not-found state for another Account's League (I7)", async () => {
    await harness.navigateByUrl('/leagues/1/stats/game-log');
    http.expectOne(`${BASE}/leagues/1/nav/`).flush(...failure(404, 'Not found.'));
    http.match((req) => req.url === LOG);
    await harness.fixture.whenStable();
    expect(text(page().querySelector('h1'))).toBe('League not found');
    expect(page().querySelector('app-game-log')).toBeNull();
  });
});
