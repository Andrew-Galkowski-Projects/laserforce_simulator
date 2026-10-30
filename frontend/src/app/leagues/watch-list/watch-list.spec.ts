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
import { MATERIAL_ANIMATIONS } from '@angular/material/core';
import { By } from '@angular/platform-browser';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { environment } from '../../../environments/environment';
import { PlayerStatRow, SeasonScope } from '../../api/league-stats.models';
import { LeagueNav } from '../../api/leagues.models';
import { signIn } from '../../api/testing/auth-fixtures';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';
import { leagueNav } from '../testing/league-fixtures';
import { watchResult } from '../testing/league-players-fixtures';
import {
  badParam,
  failure,
  pagedEnvelope,
  playerStatRow,
  seasonScope,
} from '../testing/league-stats-fixtures';
import { WatchList } from './watch-list';

const BASE = environment.apiBaseUrl;
const WATCH = `${BASE}/leagues/1/watch-list/`;
const PATH = '/leagues/1/players/watch-list';
// The kit always sends the rate (Totals by default), the ordering and the page.
const DEFAULT = { rate: 'total', ordering: '-points_scored', page: '1', page_size: '10' };
const HELP =
  'This watch list is saved to your account for this League, so it follows you across ' +
  'browsers; each League keeps its own list. Click the ★ flag on any player to add or remove them.';
const NO_SEASON = 'No Season in this League yet — start a Season to build a watch list.';
const EMPTY_LIST =
  'Your watch list is empty — open any player table and click the ★ flag to start tracking players.';

/** A watched row (the Watch List only holds watched Players). */
function watchedRow(overrides: Partial<PlayerStatRow> = {}): PlayerStatRow {
  return playerStatRow({ watched: true, ...overrides });
}

describe('Watch List (/leagues/:id/players/watch-list, spec #428)', () => {
  let http: HttpTestingController;
  let harness: RouterTestingHarness;

  beforeEach(async () => {
    TestBed.configureTestingModule({
      providers: [
        provideRouter(routes),
        provideHttpClient(withInterceptors([authInterceptor])),
        provideHttpClientTesting(),
        // jsdom fires no animationend, so a dialog would never finish closing.
        { provide: MATERIAL_ANIMATIONS, useValue: { animationsDisabled: true } },
      ],
    });
    http = TestBed.inject(HttpTestingController);
    await signIn();
    harness = await RouterTestingHarness.create();
  });

  afterEach(() => {
    document.querySelector('.cdk-overlay-container')?.remove();
    http.verify();
  });

  const page = (): HTMLElement => harness.routeNativeElement as HTMLElement;
  const q = (selector: string): HTMLElement | null =>
    page().querySelector<HTMLElement>(`app-watch-list ${selector}`);
  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const url = (): string => TestBed.inject(Router).url;

  function listRequest(): TestRequest {
    return http.expectOne((req) => req.url === WATCH && req.method === 'GET');
  }

  function sent(req: TestRequest): Record<string, string> {
    const params = req.request.params;
    return Object.fromEntries(params.keys().map((k) => [k, params.get(k) ?? '']));
  }

  interface Answer {
    rows?: PlayerStatRow[];
    count?: number;
    page?: number;
    totalPages?: number;
    scope?: SeasonScope;
  }

  async function answer(a: Answer = {}): Promise<Record<string, string>> {
    const rows = a.rows ?? [watchedRow()];
    const req = listRequest();
    const params = sent(req);
    req.flush(
      pagedEnvelope(rows, a.count ?? rows.length, {
        page: a.page ?? 1,
        total_pages: a.totalPages ?? 1,
        season_scope: a.scope ?? seasonScope(),
      }),
    );
    await settle();
    return params;
  }

  async function settle(): Promise<void> {
    await harness.fixture.whenStable();
  }

  async function navigate(path: string, nav: LeagueNav = leagueNav()): Promise<void> {
    await harness.navigateByUrl(path);
    http.expectOne(`${BASE}/leagues/1/nav/`).flush({ data: nav, meta: {} });
    await settle();
  }

  async function open(path = PATH, a: Answer = {}): Promise<Record<string, string>> {
    await navigate(path);
    return answer(a);
  }

  const headers = (): string[] =>
    [...page().querySelectorAll('#watch-list-table th')].map((th) => text(th));
  const header = (key: string): HTMLAnchorElement =>
    page().querySelector<HTMLAnchorElement>(`#watch-list-th-${key} a`)!;
  const cells = (id = 25): string[] =>
    [...page().querySelectorAll(`#watch-list-row-${id} td`)].map((td) => text(td));
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

  const button = (label: string): HTMLButtonElement | undefined =>
    [...page().querySelectorAll<HTMLButtonElement>('app-watch-list button')].find(
      (b) => text(b) === label,
    );

  it('asks for the displayed Season in Totals by Points descending, page 1 of 10', async () => {
    expect(await open()).toEqual(DEFAULT);
    expect(harness.fixture.debugElement.query(By.directive(WatchList))).not.toBeNull();
  });

  it('is headed "<League> — Watch List", then the help text', async () => {
    await open();
    expect(text(q('h1'))).toBe('Metro League — Watch List');
    expect(text(q('#watch-list-help'))).toBe(HELP);
  });

  it('has the Player Stats columns, Points marked as the active descending sort', async () => {
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

  it("shows a row in the template's formats, through the Player cell (I4)", async () => {
    await open();
    expect(cells()).toEqual([
      '★ Ace',
      'Red Raiders',
      '8',
      '9121',
      '14.2',
      '41',
      '22',
      '1.86',
      '0.4',
      '1s',
      '6',
      '0',
      '3',
      '2',
      '4',
      '1',
      '0',
    ]);
    const cell = q('#watch-list-row-25 td.name app-player-cell');
    expect(cell!.querySelector('a')!.getAttribute('href')).toBe('/leagues/1/players/25');
    expect(cell!.querySelector('button')!.getAttribute('aria-pressed')).toBe('true');
  });

  it('shows "—" for a Player with no Team', async () => {
    await open(PATH, { rows: [watchedRow({ team: null })] });
    expect(cells()[1]).toBe('—');
  });

  describe('count, Remove All and paging', () => {
    it('counts "N player(s)" beside a Remove All button', async () => {
      await open(PATH, { count: 37, totalPages: 4 });
      expect(text(q('#watch-list-count'))).toBe('37 players');
      expect(button('Remove All')).toBeDefined();
    });

    it('says "1 player" for one', async () => {
      await open();
      expect(text(q('#watch-list-count'))).toBe('1 player');
    });

    it('shows "Page X of N" and goes to the next page', async () => {
      await open(PATH, { count: 25, totalPages: 3 });
      expect(text(q('.stats-pager__page'))).toBe('Page 1 of 3');
      await click(button('Next')!);
      expect(url()).toBe(`${PATH}?page=2`);
      expect(await answer({ page: 2, totalPages: 3 })).toEqual({ ...DEFAULT, page: '2' });
      expect(text(q('.stats-pager__page'))).toBe('Page 2 of 3');
    });

    it('a per-page change resets the page', async () => {
      await open(`${PATH}?page=2`, { page: 2, totalPages: 3, count: 25 });
      await pick('stats-per-page-select', '50');
      expect(url()).toBe(`${PATH}?per_page=50`);
      expect(await answer()).toEqual({ ...DEFAULT, page_size: '50' });
    });
  });

  describe('sorting (I3)', () => {
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
  });

  describe('pickers', () => {
    it('lists the Seasons plus Career, and the rates', async () => {
      await open(`${PATH}?rate=per_game`);
      expect([...select('stats-season-select').options].map((o) => text(o))).toEqual([
        'Season 1 (2026)',
        'Season 0 (2025)',
        'Career',
      ]);
      expect([...select('stats-rate-select').options].map((o) => text(o))).toEqual([
        'Totals',
        'Per Game',
        'Per 10 min',
      ]);
      expect(select('stats-rate-select').value).toBe('per_game');
    });

    it('has no Team picker', async () => {
      await open();
      expect(q('#stats-team-select')).toBeNull();
    });

    it('a Season change keeps the rate and sort and resets the page', async () => {
      await open(`${PATH}?rate=per_game&sort=mvp&dir=desc&page=2`, { page: 2, totalPages: 3 });
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

    it('a rate change keeps the Season and resets the page', async () => {
      await open(`${PATH}?season=10&page=2`, { page: 2, totalPages: 3 });
      await pick('stats-rate-select', 'per_10');
      expect(url()).toBe(`${PATH}?season=10&rate=per_10`);
      expect(await answer()).toEqual({ ...DEFAULT, season: '10', rate: 'per_10' });
    });
  });

  describe('Remove All', () => {
    const dialog = (): HTMLElement | null => document.querySelector('mat-dialog-container');

    function dialogButton(label: string): HTMLButtonElement {
      const b = [...(dialog()?.querySelectorAll('button') ?? [])].find(
        (el) => el.textContent?.trim() === label,
      );
      if (!b) throw new Error(`no ${label} button in the dialog`);
      return b;
    }

    const clearRequests = (): TestRequest[] =>
      http.match((req) => req.url === WATCH && req.method === 'DELETE');

    it('asks first, and sends nothing on Cancel', async () => {
      await open();
      await click(button('Remove All')!);
      expect(text(dialog())).toContain('Remove every player from your watch list');
      expect(clearRequests()).toEqual([]);
      await click(dialogButton('Cancel'));
      expect(dialog()).toBeNull();
      expect(clearRequests()).toEqual([]);
      expect(cells()[0]).toBe('★ Ace');
    });

    it('on confirm empties the list, then shows the empty-list state', async () => {
      await open(`${PATH}?rate=per_game`);
      await click(button('Remove All')!);
      await click(dialogButton('Remove All'));
      const del = clearRequests();
      expect(del.length).toBe(1);
      del[0].flush(null, { status: 204, statusText: 'No Content' });
      await settle();
      expect(text(q('#watch-list-empty-notice'))).toBe(EMPTY_LIST);
      expect(q('#watch-list-table')).toBeNull();
      expect(q('#watch-list-count')).toBeNull();
      expect(button('Remove All')).toBeUndefined();
      expect(q('.stats-footer')).toBeNull();
      expect(q('#stats-season-select')).not.toBeNull();
      expect(q('#stats-rate-select')).not.toBeNull();
    });

    it('clears through WatchState, so every League table then reads the Players as unwatched', async () => {
      await open();
      await click(button('Remove All')!);
      await click(dialogButton('Remove All'));
      clearRequests()[0].flush(null, { status: 204, statusText: 'No Content' });
      await settle();
      // Player Stats, in the same League layout, still has a stale watched: true seed.
      await harness.navigateByUrl('/leagues/1/stats/player-stats');
      http
        .expectOne((req) => req.url === `${BASE}/leagues/1/player-stats/`)
        .flush(pagedEnvelope([watchedRow()], 1, { season_scope: seasonScope(), team_options: [] }));
      await settle();
      const flag = page().querySelector('#player-stats-row-25 app-player-cell button');
      expect(flag?.getAttribute('aria-pressed')).toBe('false');
    });

    it("keeps the table and shows the server's message when the clear fails", async () => {
      await open();
      await click(button('Remove All')!);
      await click(dialogButton('Remove All'));
      clearRequests()[0].flush(...failure(500, 'Database is down.'));
      await settle();
      expect(document.querySelector('mat-snack-bar-container')?.textContent).toContain(
        'Database is down.',
      );
      expect(cells()[0]).toBe('★ Ace');
    });
  });

  it('un-starring a row leaves it in place, re-coloured, until reload', async () => {
    await open();
    const flag = (): HTMLButtonElement =>
      q('#watch-list-row-25 app-player-cell button') as HTMLButtonElement;
    await click(flag());
    const del = http.expectOne(`${WATCH}25/`);
    expect(del.request.method).toBe('DELETE');
    del.flush(null, { status: 204, statusText: 'No Content' });
    await settle();
    expect(flag().getAttribute('aria-pressed')).toBe('false');
    expect(cells()[0]).toBe('★ Ace');
    expect(text(q('#watch-list-count'))).toBe('1 player');
    // Starring it again puts it back.
    await click(flag());
    http.expectOne(`${WATCH}25/`).flush({ data: watchResult(true), meta: {} });
    await settle();
    expect(flag().getAttribute('aria-pressed')).toBe('true');
  });

  describe('empty states (I1, I5)', () => {
    function expectHidden(): void {
      expect(q('#watch-list-count')).toBeNull();
      expect(button('Remove All')).toBeUndefined();
      expect(q('#stats-per-page-select')).toBeNull();
      expect(q('#watch-list-table')).toBeNull();
      expect(q('.stats-footer')).toBeNull();
    }

    it('with no displayed Season: the notice, the pickers, and no request (I1)', async () => {
      await navigate(PATH, leagueNav({ displayed_season: null }));
      http.expectNone((req) => req.url === WATCH);
      expect(text(q('h1'))).toBe('Metro League — Watch List');
      expect(text(q('#watch-list-help'))).toBe(HELP);
      expect(text(q('#watch-list-empty-notice'))).toBe(NO_SEASON);
      expect([...select('stats-season-select').options].map((o) => text(o))).toEqual(['Career']);
      expect(q('#stats-rate-select')).not.toBeNull();
      expectHidden();
    });

    it('with no displayed Season a rate change still sends nothing', async () => {
      await navigate(PATH, leagueNav({ displayed_season: null }));
      await pick('stats-rate-select', 'per_game');
      expect(url()).toBe(`${PATH}?rate=per_game`);
      http.expectNone((req) => req.url === WATCH);
      expect(text(q('#watch-list-empty-notice'))).toBe(NO_SEASON);
    });

    it('sends nothing before the League nav answers', async () => {
      await harness.navigateByUrl(PATH);
      await settle();
      http.expectNone((req) => req.url === WATCH);
      http.expectOne(`${BASE}/leagues/1/nav/`).flush({ data: leagueNav(), meta: {} });
      await settle();
      expect(await answer()).toEqual(DEFAULT);
    });

    it('with an empty list: the empty-list notice, keeping the pickers', async () => {
      await open(PATH, { rows: [] });
      expect(text(q('#watch-list-empty-notice'))).toBe(EMPTY_LIST);
      expect(q('#stats-season-select')).not.toBeNull();
      expect(q('#stats-rate-select')).not.toBeNull();
      expectHidden();
    });

    it('shows no notice while there are rows', async () => {
      await open();
      expect(q('#watch-list-empty-notice')).toBeNull();
    });
  });

  describe('load failures (I2)', () => {
    it("shows the server's message with Retry, which re-sends the same request", async () => {
      await navigate(`${PATH}?rate=per_game`);
      listRequest().flush(...failure(500, 'Database is down.'));
      await settle();
      expect(text(q('[role="alert"]'))).toContain('Database is down.');
      expect(text(q('h1'))).toBe('Metro League — Watch List');
      expect(q('#watch-list-table')).toBeNull();
      await click(button('Retry')!);
      expect(await answer()).toEqual({ ...DEFAULT, rate: 'per_game' });
      expect(q('[role="alert"]')).toBeNull();
      expect(cells()[0]).toBe('★ Ace');
    });

    it('drops a stale season the API rejects, replacing the URL', async () => {
      await navigate(`${PATH}?season=99&rate=per_game`);
      const nav = vi.spyOn(TestBed.inject(Router), 'navigate');
      listRequest().flush(...badParam('season'));
      await settle();
      expect(url()).toBe(`${PATH}?rate=per_game`);
      expect(nav.mock.calls.map(([, extras]) => extras?.replaceUrl)).toEqual([true]);
      expect(await answer()).toEqual({ ...DEFAULT, rate: 'per_game' });
    });

    it('goes back to page 1 on a 404 past it', async () => {
      await navigate(`${PATH}?page=9`);
      listRequest().flush(...failure(404, 'Invalid page.'));
      await settle();
      expect(url()).toBe(PATH);
      expect(await answer()).toEqual(DEFAULT);
    });
  });

  it('I6: the table scrolls sideways inside its own box (768px and 1280px)', async () => {
    await open();
    expect(q('#watch-list-table')?.parentElement?.classList).toContain('stats-scroll');
    // The spec runs from `frontend/` (`npm run test:ci`) or from the repo root.
    const scss = 'app/leagues/watch-list/watch-list.scss';
    const file = [
      join(process.cwd(), 'src', scss),
      join(process.cwd(), 'frontend', 'src', scss),
    ].find((path) => existsSync(path)) as string;
    const rule = /\.stats-scroll\s*\{([^}]*)\}/.exec(compile(file).css)?.[1] ?? '';
    expect(rule).toContain('max-width: 100%');
    expect(rule).toContain('overflow-x: auto');
  });
});
