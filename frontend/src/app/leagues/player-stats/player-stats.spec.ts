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
import { PlayerStatRow, SeasonScope } from '../../api/league-stats.models';
import { signIn } from '../../api/testing/auth-fixtures';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';
import {
  badParam,
  failure,
  pagedEnvelope,
  playerStatRow,
  seasonScope,
  teamOptions,
} from '../testing/league-stats-fixtures';
import { leagueNav } from '../testing/league-fixtures';
import { PlayerStats } from './player-stats';
import { releasePlayControls } from '../testing/leagues-loads';

const BASE = environment.apiBaseUrl;
const STATS = `${BASE}/leagues/1/player-stats/`;
const PATH = '/leagues/1/stats/player-stats';
// `statsApiParams` always sends the rate (Totals by default), the ordering and the page.
const DEFAULT = { rate: 'total', ordering: '-points_scored', page: '1', page_size: '10' };
const NO_SEASON = 'No Season in this League yet — start a Season to view player stats.';
const NO_ROWS = "No player performance recorded in this League's current Season yet.";

interface Answer {
  rows?: PlayerStatRow[];
  count?: number;
  page?: number;
  totalPages?: number;
  scope?: SeasonScope;
  teams?: TeamRef[];
}

describe('Player Stats (/leagues/:id/stats/player-stats, spec #417)', () => {
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
    page().querySelector<HTMLElement>(`app-player-stats ${selector}`);
  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const url = (): string => TestBed.inject(Router).url;

  function statsRequest(): TestRequest {
    return http.expectOne((req) => req.url === STATS);
  }

  function sent(req: TestRequest): Record<string, string> {
    const params = req.request.params;
    return Object.fromEntries(params.keys().map((k) => [k, params.get(k) ?? '']));
  }

  async function answer(a: Answer = {}): Promise<Record<string, string>> {
    const rows = a.rows ?? [playerStatRow()];
    const req = statsRequest();
    const params = sent(req);
    req.flush(
      pagedEnvelope(rows, a.count ?? rows.length, {
        page: a.page ?? 1,
        total_pages: a.totalPages ?? 1,
        season_scope: a.scope ?? seasonScope(),
        team_options: a.teams ?? teamOptions(),
      }),
    );
    await harness.fixture.whenStable();
    return params;
  }

  async function navigate(path: string): Promise<void> {
    await harness.navigateByUrl(path);
    http.expectOne(`${BASE}/leagues/1/nav/`).flush({ data: leagueNav(), meta: {} });
  }

  /** Opens `path`, answers the layout's nav, and returns the first stats request's params. */
  async function open(path = PATH, a: Answer = {}): Promise<Record<string, string>> {
    await navigate(path);
    return answer(a);
  }

  async function settle(): Promise<void> {
    await harness.fixture.whenStable();
  }

  const headers = (): string[] =>
    [...page().querySelectorAll('#player-stats-table th')].map((th) => text(th));
  const header = (key: string): HTMLAnchorElement =>
    page().querySelector<HTMLAnchorElement>(`#player-stats-th-${key} a`)!;
  const cells = (id: number): string[] =>
    [...page().querySelectorAll(`#player-stats-row-${id} td`)].map((td) => text(td));
  const select = (id: string): HTMLSelectElement =>
    page().querySelector<HTMLSelectElement>(`#${id}`)!;

  async function pick(id: string, value: string): Promise<void> {
    select(id).value = value;
    select(id).dispatchEvent(new Event('change'));
    await settle();
  }

  async function click(el: HTMLElement): Promise<void> {
    el.click();
    await settle();
  }

  const button = (label: string): HTMLButtonElement =>
    [...page().querySelectorAll<HTMLButtonElement>('app-player-stats button')].find(
      (b) => text(b) === label,
    )!;

  it('asks for the displayed Season in Totals by Points descending, page 1 of 10', async () => {
    expect(await open()).toEqual(DEFAULT);
    expect(harness.fixture.debugElement.query(By.directive(PlayerStats))).not.toBeNull();
  });

  it('is headed "Player Stats"', async () => {
    await open();
    expect(text(q('h1'))).toBe('Player Stats');
  });

  it('has the 17 player_stats.html columns, Points marked as the active descending sort', async () => {
    await open();
    expect(headers()).toEqual([
      'Name',
      'Team',
      'GP',
      'Points ↓',
      'MVP',
      'Tags',
      'Tagged',
      'Tag Ratio',
      'Acc%',
      'Survival',
      'Lives',
      'Resup',
      'Missiles',
      'Specials',
      'Follow-up',
      'Reaction',
      'Combo Resup',
    ]);
  });

  it("shows a row in the template's formats, Survival with an s suffix", async () => {
    await open(PATH, {
      rows: [
        playerStatRow({
          games: 8,
          stats: {
            points_scored: 9120.5,
            mvp: 14.25,
            tags_made: 41.4,
            times_tagged: 22,
            tag_ratio: 1.864,
            accuracy: 42.36,
            survival: 612.6,
            final_lives: 6,
            resupplies_given: 0,
            missiles_landed: 3,
            specials_used: 2,
            follow_up_shots: 4,
            reaction_shots: 1,
            combo_resupply_count: 0.4,
          },
        }),
      ],
    });
    expect(cells(25)).toEqual([
      '☆ Ace',
      'Red Raiders',
      '8',
      '9121',
      '14.3',
      '41',
      '22',
      '1.86',
      '42.4',
      '613s',
      '6',
      '0',
      '3',
      '2',
      '4',
      '1',
      '0',
    ]);
    const numeric = [...page().querySelectorAll('#player-stats-row-25 td')].map((td) =>
      td.classList.contains('num'),
    );
    expect(numeric).toEqual([false, false, ...Array(15).fill(true)]);
  });

  it('puts the shared Player cell in the Name column, linking to the League player page (I4)', async () => {
    await open(PATH, { rows: [playerStatRow({ watched: true })] });
    const cell = q('#player-stats-row-25 td.name app-player-cell');
    expect(cell).not.toBeNull();
    expect(cell!.querySelector('a')!.getAttribute('href')).toBe('/leagues/1/players/25');
    expect(cell!.querySelector('button')!.getAttribute('aria-pressed')).toBe('true');
  });

  it('shows "—" for a Player with no Team (I6)', async () => {
    await open(PATH, { rows: [playerStatRow({ team: null })] });
    expect(cells(25)[1]).toBe('—');
  });

  it('shows the rows in the order the API sent', async () => {
    await open(PATH, {
      rows: [playerStatRow({ player: { id: 30, name: 'Bolt', team: null } }), playerStatRow()],
    });
    const ids = [...page().querySelectorAll('#player-stats-table tbody tr')].map((tr) => tr.id);
    expect(ids).toEqual(['player-stats-row-30', 'player-stats-row-25']);
  });

  it('counts the players as "N player(s)"', async () => {
    await open(PATH, { count: 1 });
    expect(text(q('#player-stats-count'))).toBe('1 player');
    await pick('stats-rate-select', 'per_game');
    await answer({ count: 37, totalPages: 4 });
    expect(text(q('#player-stats-count'))).toBe('37 players');
  });

  describe('sorting (I1)', () => {
    it('a new header sorts descending, in the URL and the request, from page 1', async () => {
      await open(`${PATH}?page=3`, { page: 3, totalPages: 4, count: 37 });
      await click(header('mvp'));
      expect(url()).toBe(`${PATH}?sort=mvp&dir=desc`);
      expect(await answer()).toEqual({ ...DEFAULT, ordering: '-mvp' });
      expect(headers()[4]).toBe('MVP ↓');
      expect(headers()[3]).toBe('Points');
    });

    it('the active header toggles to ascending', async () => {
      await open();
      await click(header('points_scored'));
      expect(url()).toBe(`${PATH}?sort=points_scored&dir=asc`);
      expect(await answer()).toEqual({ ...DEFAULT, ordering: 'points_scored' });
      expect(headers()[3]).toBe('Points ↑');
    });

    it('a new sort supersedes the request still in flight', async () => {
      await open();
      await click(header('mvp'));
      const stale = statsRequest();
      await click(header('accuracy'));
      expect(stale.cancelled).toBe(true);
      const fresh = playerStatRow({ player: { id: 30, name: 'Bolt', team: null } });
      expect(await answer({ rows: [fresh] })).toEqual({ ...DEFAULT, ordering: '-accuracy' });
      expect(q('#player-stats-row-30')).not.toBeNull();
      expect(q('#player-stats-row-25')).toBeNull();
    });

    it('sorts by Name, Team and GP too', async () => {
      await open();
      await click(header('name'));
      expect(await answer()).toEqual({ ...DEFAULT, ordering: '-name' });
      await click(header('team'));
      expect(await answer()).toEqual({ ...DEFAULT, ordering: '-team' });
      await click(header('games'));
      expect(await answer()).toEqual({ ...DEFAULT, ordering: '-games' });
    });

    it('reads every param from the URL', async () => {
      expect(
        await open(
          `${PATH}?season=career&team=5&rate=per_10&sort=survival&dir=asc&page=2&per_page=25`,
          { page: 2, totalPages: 3, count: 60 },
        ),
      ).toEqual({
        season: 'career',
        team_id: '5',
        rate: 'per_10',
        ordering: 'survival',
        page: '2',
        page_size: '25',
      });
    });

    it('takes the defaults for a hand-edited sort, rate or per-page it does not know', async () => {
      expect(await open(`${PATH}?sort=bogus&dir=up&rate=per_36&per_page=7`)).toEqual(DEFAULT);
    });
  });

  describe('pickers', () => {
    it('lists the rates as Totals / Per Game / Per 10 min, on the URL rate', async () => {
      await open(`${PATH}?rate=per_game`);
      expect([...select('stats-rate-select').options].map((o) => [o.value, text(o)])).toEqual([
        ['total', 'Totals'],
        ['per_game', 'Per Game'],
        ['per_10', 'Per 10 min'],
      ]);
      expect(select('stats-rate-select').value).toBe('per_game');
      expect(text(q('label[for="stats-rate-select"]'))).toBe('Rate:');
    });

    it('a rate change keeps the sort and Team and resets the page', async () => {
      await open(`${PATH}?team=5&sort=mvp&dir=asc&page=2`, { page: 2, totalPages: 3 });
      const nav = vi.spyOn(TestBed.inject(Router), 'navigate');
      await pick('stats-rate-select', 'per_10');
      expect(url()).toBe(`${PATH}?team=5&rate=per_10&sort=mvp&dir=asc`);
      expect(nav.mock.calls.map(([, extras]) => extras?.replaceUrl)).toEqual([false]);
      expect(await answer()).toEqual({
        team_id: '5',
        rate: 'per_10',
        ordering: 'mvp',
        page: '1',
        page_size: '10',
      });
    });

    it('lists "All Teams" plus meta.team_options, on the URL Team', async () => {
      await open(`${PATH}?team=3`);
      expect([...select('stats-team-select').options].map((o) => text(o))).toEqual([
        'All Teams',
        'Blue Bolts',
        'Red Raiders',
      ]);
      expect(select('stats-team-select').value).toBe('3');
    });

    it('a Team change resets the page and sends team_id', async () => {
      await open(`${PATH}?rate=per_game&page=2`, { page: 2, totalPages: 3 });
      await pick('stats-team-select', '5');
      expect(url()).toBe(`${PATH}?team=5&rate=per_game`);
      expect(await answer()).toEqual({ ...DEFAULT, rate: 'per_game', team_id: '5' });
    });

    it('a Season change clears the Team and the page, keeping the rate and sort', async () => {
      await open(`${PATH}?team=5&rate=per_game&sort=mvp&dir=desc&page=2`, {
        page: 2,
        totalPages: 3,
      });
      await pick('stats-season-select', 'career');
      expect(url()).toBe(`${PATH}?season=career&rate=per_game&sort=mvp&dir=desc`);
      expect(await answer()).toEqual({
        season: 'career',
        rate: 'per_game',
        ordering: '-mvp',
        page: '1',
        page_size: '10',
      });
    });

    it('lists the Seasons plus Career', async () => {
      await open();
      expect([...select('stats-season-select').options].map((o) => text(o))).toEqual([
        'Season 1 (2026)',
        'Season 0 (2025)',
        'Career',
      ]);
    });

    it('a per-page change resets the page', async () => {
      await open(`${PATH}?page=2`, { page: 2, totalPages: 3, count: 25 });
      await pick('stats-per-page-select', '50');
      expect(url()).toBe(`${PATH}?per_page=50`);
      expect(await answer()).toEqual({ ...DEFAULT, page_size: '50' });
    });
  });

  describe('paging', () => {
    it('shows "Page X of N" and goes to the next page', async () => {
      await open(PATH, { count: 25, totalPages: 3 });
      expect(text(q('.stats-pager__page'))).toBe('Page 1 of 3');
      await click(button('Next'));
      expect(url()).toBe(`${PATH}?page=2`);
      expect(await answer({ page: 2, totalPages: 3 })).toEqual({ ...DEFAULT, page: '2' });
      expect(text(q('.stats-pager__page'))).toBe('Page 2 of 3');
    });

    it('shows no pager on a single page', async () => {
      await open();
      expect(q('.stats-pager')).toBeNull();
    });
  });

  describe('lenient fallback (I2)', () => {
    async function rejectFirst(path: string, field: string): Promise<void> {
      await navigate(path);
      statsRequest().flush(...badParam(field));
      await settle();
    }

    it('drops a stale season, replacing the URL, and reloads', async () => {
      await navigate(`${PATH}?season=99&rate=per_game`);
      const nav = vi.spyOn(TestBed.inject(Router), 'navigate');
      statsRequest().flush(...badParam('season'));
      await settle();
      expect(url()).toBe(`${PATH}?rate=per_game`);
      expect(nav.mock.calls.map(([, extras]) => extras?.replaceUrl)).toEqual([true]);
      expect(await answer()).toEqual({ ...DEFAULT, rate: 'per_game' });
      expect(q('[role="alert"]')).toBeNull();
      expect(cells(25)[0]).toBe('☆ Ace');
    });

    it('drops a team the API rejects', async () => {
      await rejectFirst(`${PATH}?team=99&rate=per_game`, 'team_id');
      expect(url()).toBe(`${PATH}?rate=per_game`);
      expect(await answer()).toEqual({ ...DEFAULT, rate: 'per_game' });
    });

    it('drops a rate the API rejects', async () => {
      await rejectFirst(`${PATH}?team=5&rate=per_game`, 'rate');
      expect(url()).toBe(`${PATH}?team=5`);
      expect(await answer()).toEqual({ ...DEFAULT, team_id: '5' });
    });

    it('drops an ordering the API rejects', async () => {
      await rejectFirst(`${PATH}?sort=mvp&dir=asc&team=5`, 'ordering');
      expect(url()).toBe(`${PATH}?team=5`);
      expect(await answer()).toEqual({ ...DEFAULT, team_id: '5' });
    });

    it('goes back to page 1 on a 404 past it', async () => {
      await navigate(`${PATH}?page=9&rate=per_game`);
      statsRequest().flush(...failure(404, 'Invalid page.'));
      await settle();
      expect(url()).toBe(`${PATH}?rate=per_game`);
      expect(await answer()).toEqual({ ...DEFAULT, rate: 'per_game' });
    });
  });

  describe('empty states (I3)', () => {
    it('shows the no-Season notice, and no table, for a League with no Season', async () => {
      await open(PATH, {
        rows: [],
        scope: seasonScope({ selected: null, options: [] }),
        teams: [],
      });
      expect(text(q('#player-stats-empty-notice'))).toBe(NO_SEASON);
      expect(q('#player-stats-table')).toBeNull();
      expect(q('#player-stats-count')).toBeNull();
    });

    it('hides the Team picker, per-page picker and pager footer with no Season', async () => {
      await open(`${PATH}?team=5`, {
        rows: [],
        scope: seasonScope({ selected: null, options: [] }),
        teams: [],
      });
      expect(q('#stats-team-select')).toBeNull();
      expect(q('#stats-per-page-select')).toBeNull();
      expect(q('.stats-footer')).toBeNull();
      expect(q('#stats-season-select')).not.toBeNull();
      expect(q('#stats-rate-select')).not.toBeNull();
    });

    it('shows the no-performance notice for a Season with no rows', async () => {
      await open(PATH, { rows: [] });
      expect(text(q('#player-stats-empty-notice'))).toBe(NO_ROWS);
      expect(q('#player-stats-table')).toBeNull();
    });

    it('hides the Team picker and the paging footer with no rows and no Team filter', async () => {
      await open(PATH, { rows: [] });
      expect(q('#stats-team-select')).toBeNull();
      expect(q('#stats-per-page-select')).toBeNull();
      expect(q('.stats-footer')).toBeNull();
    });

    it('keeps the Team picker on an empty Team, so it can be cleared', async () => {
      await open(`${PATH}?team=5`, { rows: [] });
      expect(q('#stats-per-page-select')).toBeNull();
      expect(q('.stats-footer')).toBeNull();
      await pick('stats-team-select', '');
      expect(url()).toBe(PATH);
      expect(await answer()).toEqual(DEFAULT);
    });

    it('shows no notice while there are rows', async () => {
      await open();
      expect(q('#player-stats-empty-notice')).toBeNull();
    });
  });

  it("shows any other failure in the error box with the server's message", async () => {
    await navigate(PATH);
    statsRequest().flush(...failure(500, 'Database is down.'));
    await settle();
    expect(text(q('[role="alert"]'))).toBe('Database is down.');
    expect(q('#player-stats-table')).toBeNull();
    expect(url()).toBe(PATH);
  });

  it("shows the layout's not-found state for another Account's League (I7)", async () => {
    await harness.navigateByUrl(PATH);
    http.expectOne(`${BASE}/leagues/1/nav/`).flush(...failure(404, 'Not found.'));
    http.match((req) => req.url === STATS);
    await settle();
    expect(text(page().querySelector('h1'))).toBe('League not found');
    expect(page().querySelector('app-player-stats')).toBeNull();
  });
});
