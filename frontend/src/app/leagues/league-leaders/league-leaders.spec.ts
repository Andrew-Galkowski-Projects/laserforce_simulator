import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { compile } from 'sass';
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
import { LeaderBoards, SeasonScope } from '../../api/league-stats.models';
import { signIn } from '../../api/testing/auth-fixtures';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';
import {
  badParam,
  failure,
  leaderBoardRow,
  leaderBoards,
  scopedEnvelope,
  seasonScope,
} from '../testing/league-stats-fixtures';
import { leagueNav } from '../testing/league-fixtures';
import { LeagueLeaders } from './league-leaders';

const BASE = environment.apiBaseUrl;
const LEADERS = `${BASE}/leagues/1/leaders/`;
const PATH = '/leagues/1/stats/league-leaders';
const NO_SEASON = 'No Season in this League yet — start a Season to populate the leaderboards.';

describe('League Leaders (/leagues/:id/stats/league-leaders, spec #417)', () => {
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
    page().querySelector<HTMLElement>(`app-league-leaders ${selector}`);
  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const url = (): string => TestBed.inject(Router).url;

  function leadersRequest(): TestRequest {
    return http.expectOne((req) => req.url === LEADERS);
  }

  function sent(req: TestRequest): Record<string, string> {
    const params = req.request.params;
    return Object.fromEntries(params.keys().map((k) => [k, params.get(k) ?? '']));
  }

  async function answer(
    boards: LeaderBoards = leaderBoards(),
    scope: SeasonScope = seasonScope(),
  ): Promise<Record<string, string>> {
    const req = leadersRequest();
    const params = sent(req);
    req.flush(scopedEnvelope(boards, { season_scope: scope }));
    await harness.fixture.whenStable();
    return params;
  }

  /** Opens `path`, answers the layout's nav, and returns the leaders request's params. */
  async function open(
    path = PATH,
    boards?: LeaderBoards,
    scope?: SeasonScope,
  ): Promise<Record<string, string>> {
    await harness.navigateByUrl(path);
    http.expectOne(`${BASE}/leagues/1/nav/`).flush({ data: leagueNav(), meta: {} });
    return answer(boards, scope);
  }

  const headers = (board: string): string[] =>
    [...page().querySelectorAll(`#${board} th`)].map((th) => text(th));
  const rows = (board: string): string[][] =>
    [...page().querySelectorAll(`#${board} tbody tr`)].map((tr) =>
      [...tr.querySelectorAll('td')].map((td) => text(td)),
    );
  const header = (board: string, index: number): HTMLButtonElement =>
    page().querySelectorAll<HTMLButtonElement>(`#${board} th button`)[index];
  const seasonSelect = (): HTMLSelectElement =>
    page().querySelector<HTMLSelectElement>('#stats-season-select')!;

  async function click(button: HTMLButtonElement): Promise<void> {
    button.click();
    await harness.fixture.whenStable();
  }

  it('asks for the displayed Season with no other param by default', async () => {
    expect(await open()).toEqual({});
    expect(harness.fixture.debugElement.query(By.directive(LeagueLeaders))).not.toBeNull();
  });

  it('is headed "League Leaders"', async () => {
    await open();
    expect(text(q('h1'))).toBe('League Leaders');
  });

  it('shows the four boards in template order, each headed by its title and value', async () => {
    await open();
    expect([...page().querySelectorAll('app-league-leaders table')].map((t) => t.id)).toEqual([
      'leaders-avg-tags',
      'leaders-avg-score',
      'leaders-fewest-tagged',
      'leaders-tag-ratio',
    ]);
    expect(headers('leaders-avg-tags')).toEqual(['Average Tags', 'Tags']);
    expect(headers('leaders-avg-score')).toEqual(['Average Score', 'Score']);
    expect(headers('leaders-fewest-tagged')).toEqual(['Fewest Times Tagged', 'Tagged']);
    expect(headers('leaders-tag-ratio')).toEqual(['Tag Ratio', 'Ratio']);
  });

  it('shows a row as "rank. Player, Team" and its value to 1 place (Tag Ratio: 2)', async () => {
    await open();
    expect(rows('leaders-avg-tags')).toEqual([['1. Ace, Red Raiders', '41.3']]);
    expect(rows('leaders-avg-score')).toEqual([['1. Ace, Red Raiders', '9120.5']]);
    expect(rows('leaders-fewest-tagged')).toEqual([['1. Ace, Red Raiders', '12.4']]);
    expect(rows('leaders-tag-ratio')).toEqual([['1. Ace, Red Raiders', '1.80']]);
    const cells = [...page().querySelectorAll('#leaders-avg-tags tbody td')];
    expect(cells.map((td) => td.className)).toEqual(['name', 'num']);
  });

  it('renders the Player through the shared Player cell (I4)', async () => {
    await open(PATH, leaderBoards({ avg_tags: [leaderBoardRow({ watched: true })] }));
    const cell = page().querySelector('#leaders-avg-tags tbody app-player-cell');
    expect(cell?.querySelector('a')?.getAttribute('href')).toBe('/players/25/stats');
    expect(cell?.querySelector('[title="Watched"]')).not.toBeNull();
  });

  describe('sorting one board on the client', () => {
    const three = (): LeaderBoards =>
      leaderBoards({
        avg_tags: [
          leaderBoardRow({
            rank: 1,
            value: 41,
            player_id: 25,
            player_name: 'Cal',
            player: { id: 25, name: 'Cal', team: null },
          }),
          leaderBoardRow({
            rank: 2,
            value: 30,
            player_id: 26,
            player_name: 'Ace',
            player: { id: 26, name: 'Ace', team: null },
          }),
          leaderBoardRow({
            rank: 3,
            value: 20,
            player_id: 27,
            player_name: 'Bea',
            player: { id: 27, name: 'Bea', team: null },
          }),
        ],
        avg_score: [
          leaderBoardRow({ rank: 1, value: 9000, player_id: 25 }),
          leaderBoardRow({ rank: 2, value: 8000, player_id: 26 }),
        ],
      });
    const ranks = (board: string): string[] => rows(board).map((r) => r[0].split('.')[0]);

    it('a value header sorts its board ascending, then toggles, with no request or URL change', async () => {
      await open(PATH, three());
      await click(header('leaders-avg-tags', 1));
      expect(ranks('leaders-avg-tags')).toEqual(['3', '2', '1']);
      expect(headers('leaders-avg-tags')).toEqual(['Average Tags', 'Tags ↑']);
      await click(header('leaders-avg-tags', 1));
      expect(ranks('leaders-avg-tags')).toEqual(['1', '2', '3']);
      expect(headers('leaders-avg-tags')).toEqual(['Average Tags', 'Tags ↓']);
      http.expectNone((req) => req.url === LEADERS);
      expect(url()).toBe(PATH);
    });

    it('the Player header sorts by name; a new header starts ascending', async () => {
      await open(PATH, three());
      await click(header('leaders-avg-tags', 1));
      await click(header('leaders-avg-tags', 1));
      await click(header('leaders-avg-tags', 0));
      expect(rows('leaders-avg-tags').map((r) => r[0])).toEqual([
        '2. Ace, Red Raiders',
        '3. Bea, Red Raiders',
        '1. Cal, Red Raiders',
      ]);
      expect(headers('leaders-avg-tags')).toEqual(['Average Tags ↑', 'Tags']);
      await click(header('leaders-avg-tags', 0));
      expect(ranks('leaders-avg-tags')).toEqual(['1', '3', '2']);
      expect(headers('leaders-avg-tags')).toEqual(['Average Tags ↓', 'Tags']);
    });

    it('leaves the other boards in rank order', async () => {
      await open(PATH, three());
      await click(header('leaders-avg-tags', 1));
      expect(ranks('leaders-avg-score')).toEqual(['1', '2']);
      expect(headers('leaders-avg-score')).toEqual(['Average Score', 'Score']);
    });

    it('breaks a tie on the Player id, reversed with the direction (the Django sort)', async () => {
      await open(
        PATH,
        leaderBoards({
          tag_ratio: [
            leaderBoardRow({ rank: 1, value: 1.5, player_id: 30 }),
            leaderBoardRow({ rank: 2, value: 1.5, player_id: 26 }),
          ],
        }),
      );
      await click(header('leaders-tag-ratio', 1));
      expect(ranks('leaders-tag-ratio')).toEqual(['2', '1']);
      await click(header('leaders-tag-ratio', 1));
      expect(ranks('leaders-tag-ratio')).toEqual(['1', '2']);
    });
  });

  describe('subheading: the selected scope', () => {
    it('names the selected Season, not the displayed one', async () => {
      await open(
        `${PATH}?season=10`,
        leaderBoards(),
        seasonScope({ selected: { id: 10, name: 'Season 0' } }),
      );
      expect(text(q('#leaders-scope'))).toBe('Season 0');
    });

    it('says "Career" for the career scope', async () => {
      await open(`${PATH}?season=career`, leaderBoards(), seasonScope({ selected: 'career' }));
      expect(text(q('#leaders-scope'))).toBe('Career');
    });
  });

  describe('Season picker (I1)', () => {
    it('lists the Seasons as "name (year)" plus Career, on the selected scope', async () => {
      await open();
      expect([...seasonSelect().options].map((o) => text(o))).toEqual([
        'Season 1 (2026)',
        'Season 0 (2025)',
        'Career',
      ]);
      expect(seasonSelect().value).toBe('11');
    });

    it('reads the Season from the URL and sends it', async () => {
      expect(await open(`${PATH}?season=career`)).toEqual({ season: 'career' });
    });

    it('a Season change puts the Season in the URL and reloads it', async () => {
      await open();
      const navigate = vi.spyOn(TestBed.inject(Router), 'navigate');
      seasonSelect().value = '10';
      seasonSelect().dispatchEvent(new Event('change'));
      await harness.fixture.whenStable();
      expect(url()).toBe(`${PATH}?season=10`);
      expect(navigate.mock.calls.map(([, extras]) => extras?.replaceUrl)).toEqual([false]);
      expect(await answer()).toEqual({ season: '10' });
    });

    it('a new Season supersedes the request still in flight', async () => {
      await open();
      const pick = async (value: string): Promise<void> => {
        seasonSelect().value = value;
        seasonSelect().dispatchEvent(new Event('change'));
        await harness.fixture.whenStable();
      };
      await pick('10');
      const stale = leadersRequest();
      await pick('career');
      expect(stale.cancelled).toBe(true);
      const fresh = leaderBoardRow({ player_id: 26, player: { id: 26, name: 'Bea', team: null } });
      expect(
        await answer(leaderBoards({ avg_tags: [fresh] }), seasonScope({ selected: 'career' })),
      ).toEqual({ season: 'career' });
      expect(text(q('#leaders-scope'))).toBe('Career');
      expect(rows('leaders-avg-tags')[0][0]).toBe('1. Bea, Red Raiders');
    });

    it('a Season change drops a hand-written team param (the screen has no Team filter)', async () => {
      expect(await open(`${PATH}?team=5`)).toEqual({});
      seasonSelect().value = 'career';
      seasonSelect().dispatchEvent(new Event('change'));
      await harness.fixture.whenStable();
      expect(url()).toBe(`${PATH}?season=career`);
      expect(await answer()).toEqual({ season: 'career' });
    });
  });

  it('drops a stale season the API rejects, replacing the URL, and reloads (I2)', async () => {
    await harness.navigateByUrl(`${PATH}?season=99`);
    http.expectOne(`${BASE}/leagues/1/nav/`).flush({ data: leagueNav(), meta: {} });
    const navigate = vi.spyOn(TestBed.inject(Router), 'navigate');
    leadersRequest().flush(...badParam('season'));
    await harness.fixture.whenStable();
    expect(url()).toBe(PATH);
    expect(navigate.mock.calls.map(([, extras]) => extras?.replaceUrl)).toEqual([true]);
    expect(await answer()).toEqual({});
    expect(q('[role="alert"]')).toBeNull();
    expect(rows('leaders-avg-tags')[0][0]).toBe('1. Ace, Red Raiders');
  });

  describe('empty states (I3)', () => {
    it('shows the no-Season notice, and no boards, for a League with no Season', async () => {
      await open(
        PATH,
        leaderBoards({ avg_tags: [], avg_score: [], fewest_tagged: [], tag_ratio: [] }),
        seasonScope({ selected: null, options: [] }),
      );
      expect(text(q('#leaders-empty-notice'))).toBe(NO_SEASON);
      expect(q('table')).toBeNull();
      expect(q('#leaders-scope')).toBeNull();
    });

    it('shows "No data." on an empty board, the others keeping their rows', async () => {
      await open(PATH, leaderBoards({ fewest_tagged: [] }));
      expect(rows('leaders-fewest-tagged')).toEqual([['No data.']]);
      expect(page().querySelector('#leaders-fewest-tagged tbody td')?.getAttribute('colspan')).toBe(
        '2',
      );
      expect(rows('leaders-avg-tags')).toEqual([['1. Ace, Red Raiders', '41.3']]);
      expect(q('#leaders-empty-notice')).toBeNull();
    });
  });

  it("shows any other failure in the error box with the server's message", async () => {
    await harness.navigateByUrl(PATH);
    http.expectOne(`${BASE}/leagues/1/nav/`).flush({ data: leagueNav(), meta: {} });
    leadersRequest().flush(...failure(500, 'Database is down.'));
    await harness.fixture.whenStable();
    expect(text(q('[role="alert"]'))).toBe('Database is down.');
    expect(q('table')).toBeNull();
    expect(url()).toBe(PATH);
  });

  it('lays the boards out two to a row, stacking below a 45.5rem content width (I5)', () => {
    // The spec runs from `frontend/` (`npm run test:ci`) or from the repo root.
    const scss = 'app/leagues/league-leaders/league-leaders.scss';
    const file = [
      join(process.cwd(), 'src', scss),
      join(process.cwd(), 'frontend', 'src', scss),
    ].find((path) => existsSync(path)) as string;
    const rule = /\.leaders-boards\s*\{([^}]*)\}/.exec(compile(file).css)?.[1] ?? '';
    expect(rule).toContain('display: grid');
    expect(rule).toContain('gap: 1.5rem');
    // Each track is at least half the row (so never three) and at least 22rem (so two need
    // 2 x 22rem + the gap): one column on a narrower container, whatever the viewport.
    expect(rule).toContain(
      'grid-template-columns: repeat(auto-fit, minmax(max(50% - 0.75rem, min(100%, 22rem)), 1fr))',
    );
  });

  it("shows the layout's not-found state for another Account's League (I7)", async () => {
    await harness.navigateByUrl(PATH);
    http.expectOne(`${BASE}/leagues/1/nav/`).flush(...failure(404, 'Not found.'));
    http.match((req) => req.url === LEADERS);
    await harness.fixture.whenStable();
    expect(text(page().querySelector('h1'))).toBe('League not found');
    expect(page().querySelector('app-league-leaders')).toBeNull();
  });
});
