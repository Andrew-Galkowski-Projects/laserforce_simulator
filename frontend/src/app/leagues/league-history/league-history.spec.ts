import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import {
  HttpTestingController,
  TestRequest,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { environment } from '../../../environments/environment';
import { HistoryRow } from '../../api/leagues.models';
import { SELF_INSERT, USER, signIn } from '../../api/testing/auth-fixtures';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';
import { User } from '../../auth/user';
import {
  awardWinner,
  historyRow,
  league,
  leagueNav,
  pageEnvelope,
} from '../testing/league-fixtures';

const BASE = environment.apiBaseUrl;
const HISTORY = `${BASE}/leagues/1/history/`;
const EMPTY = 'No Seasons yet — start a new Season to populate the history table.';

describe('League History (/leagues/:id/history, spec #409 F11)', () => {
  let http: HttpTestingController;
  let harness: RouterTestingHarness;

  async function setUp(user: User = USER): Promise<void> {
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
  }

  afterEach(() => http.verify());

  const page = (): HTMLElement => harness.routeNativeElement as HTMLElement;
  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';

  /** The layout's nav load, answered (its spec covers it). */
  function flushNav(): void {
    http.expectOne(`${BASE}/leagues/1/nav/`).flush({ data: leagueNav(), meta: {} });
  }

  function flushLeague(): void {
    http.expectOne(`${BASE}/leagues/1/`).flush({ data: league(), meta: {} });
  }

  function historyRequest(): TestRequest {
    return http.expectOne((req) => req.url === HISTORY);
  }

  type Meta = Parameters<typeof pageEnvelope>[1];

  async function answer(rows: HistoryRow[], meta: Meta = {}): Promise<void> {
    flushLeague();
    historyRequest().flush(pageEnvelope(rows, { page_size: 10, ...meta }));
    await harness.fixture.whenStable();
  }

  async function open(rows: HistoryRow[], meta: Meta = {}, user: User = USER): Promise<void> {
    await setUp(user);
    await harness.navigateByUrl('/leagues/1/history');
    flushNav();
    await answer(rows, meta);
  }

  const bodyRows = (): HTMLTableRowElement[] => [
    ...page().querySelectorAll<HTMLTableRowElement>('#league-history-table tbody tr'),
  ];
  const cells = (row: HTMLTableRowElement): string[] =>
    [...row.querySelectorAll('td')].map((td) => text(td));

  it('asks for page 1 with page_size 10, sent explicitly', async () => {
    await setUp();
    await harness.navigateByUrl('/leagues/1/history');
    flushNav();
    flushLeague();
    const req = historyRequest();
    expect(req.request.method).toBe('GET');
    expect(req.request.params.get('page')).toBe('1');
    expect(req.request.params.get('page_size')).toBe('10');
    req.flush(pageEnvelope([]));
    await harness.fixture.whenStable();
  });

  it("heads the page with the League's name, as history.html", async () => {
    await open([historyRow()]);
    expect(text(page().querySelector('h1'))).toBe('Metro League — History');
  });

  it('has the history.html columns, and a completed row in them', async () => {
    await open([historyRow()]);
    const headers = [...page().querySelectorAll('#league-history-table th')].map((th) => text(th));
    expect(headers).toEqual([
      'Season',
      'Start date',
      'Teams enrolled',
      'Matches played',
      'Champion',
      'Runner-Up',
      'Tournament Champion',
      '1st place',
      '2nd place',
      '3rd place',
      'Season MVP',
      'Finals MVP',
      '',
    ]);
    expect(cells(bodyRows()[0])).toEqual([
      'Season 1',
      '2026-09-01',
      '8',
      '28',
      'Red Raiders',
      'Blue Bolts',
      '—',
      'Red Raiders',
      'Blue Bolts',
      'Green Giants',
      'Ace',
      'Bolt',
      'Awards',
    ]);
  });

  it('shows each row in the order the API sent, the in-progress one first with its badge', async () => {
    const live = historyRow({
      season_id: 11,
      season_name: 'Season 2',
      is_in_progress: true,
      champion: null,
      season_mvp: null,
      finals_mvp: null,
    });
    await open([live, historyRow()]);
    const [first, second] = bodyRows();
    expect(first.id).toBe('league-history-in-progress-row');
    expect(cells(first)[0]).toBe('Season 2');
    expect(text(first.querySelector('.league-history__badge'))).toBe('In progress');
    expect(cells(first)[4]).toBe('In progress');
    expect(cells(first).slice(10, 12)).toEqual(['—', '—']);
    expect(second.id).toBe('league-history-row-10');
    expect(second.querySelector('.league-history__badge')).toBeNull();
  });

  it('shows a dash for an empty podium slot and a missing champion', async () => {
    await open([
      historyRow({
        champion: null,
        runner_up: null,
        top_three: [{ id: 3, name: 'A' }, null, null],
      }),
    ]);
    expect(cells(bodyRows()[0]).slice(4, 10)).toEqual(['—', '—', '—', 'A', '—', '—']);
  });

  describe('MVPs (I4)', () => {
    const mvpLinks = (): [string, string | null][] =>
      [...bodyRows()[0].querySelectorAll('td[id*="-mvp-"] a')].map((a) => [
        text(a),
        a.getAttribute('href'),
      ]);

    it('link each MVP to /players/:id/stats', async () => {
      await open([historyRow()]);
      expect(mvpLinks()).toEqual([
        ['Ace', '/players/25/stats'],
        ['Bolt', '/players/26/stats'],
      ]);
      expect(page().querySelector('.league-history__you')).toBeNull();
    });

    it("mark the signed-in Account's Self-insert with You", async () => {
      const self = { ...SELF_INSERT, id: 26 };
      await open(
        [historyRow({ season_mvp: awardWinner({ player_id: 25 }) })],
        {},
        { ...USER, self_insert: self },
      );
      expect(text(page().querySelector('#league-history-season-mvp-10'))).toBe('Ace');
      expect(text(page().querySelector('#league-history-finals-mvp-10'))).toBe('Bolt You');
    });
  });

  it("links each row's Season name to its Season dashboard and Awards to its awards", async () => {
    const live = historyRow({ season_id: 11, season_name: 'Season 2', is_in_progress: true });
    await open([live, historyRow()]);
    const links = bodyRows().map((row) =>
      [...row.querySelectorAll('a')]
        .filter((a) => !a.closest('td[id*="-mvp-"]'))
        .map((a) => [text(a), a.getAttribute('href')]),
    );
    expect(links).toEqual([
      [
        ['Season 2', '/leagues/1/seasons/11'],
        ['Awards', '/leagues/1/seasons/11/awards'],
      ],
      [
        ['Season 1', '/leagues/1/seasons/10'],
        ['Awards', '/leagues/1/seasons/10/awards'],
      ],
    ]);
    expect(page().querySelector('app-soon-badge, .soon')).toBeNull();
  });

  it('shows the Django empty notice, and no table or pager, when there are no Seasons', async () => {
    await open([]);
    expect(text(page().querySelector('#league-history-empty-notice'))).toBe(EMPTY);
    expect(page().querySelector('#league-history-table')).toBeNull();
    expect(page().querySelector('#league-history-per-page-select')).toBeNull();
  });

  it('shows no empty notice while Seasons are listed', async () => {
    await open([historyRow()]);
    expect(page().textContent).not.toContain(EMPTY);
  });

  describe('paging', () => {
    const select = (): HTMLSelectElement =>
      page().querySelector<HTMLSelectElement>('#league-history-per-page-select')!;
    const button = (label: string): HTMLButtonElement | undefined =>
      [...page().querySelectorAll<HTMLButtonElement>('#league-history-pagination button')].find(
        (b) => text(b) === label,
      );

    it('offers 10 / 25 / 50 / 100 per page, 10 chosen', async () => {
      await open([historyRow()]);
      expect([...select().options].map((o) => o.value)).toEqual(['10', '25', '50', '100']);
      expect(select().value).toBe('10');
    });

    it('shows no Prev / Next when every Season fits on one page', async () => {
      await open([historyRow()]);
      expect(page().querySelector('#league-history-pagination')).toBeNull();
    });

    it('disables Previous on page 1 and Next on the last page; Next and Previous fetch', async () => {
      await open([historyRow()], { page: 1, total_pages: 2, count: 11 });
      expect(button('Previous')?.disabled).toBe(true);
      expect(button('Next')?.disabled).toBe(false);
      expect(text(page().querySelector('.league-history__page'))).toBe('Page 1 of 2');

      button('Next')?.click();
      await harness.fixture.whenStable();
      flushLeague();
      let req = historyRequest();
      expect(req.request.params.get('page')).toBe('2');
      expect(req.request.params.get('page_size')).toBe('10');
      req.flush(
        pageEnvelope([historyRow({ season_id: 1, season_name: 'Season 0' })], {
          page: 2,
          page_size: 10,
          total_pages: 2,
          count: 11,
        }),
      );
      await harness.fixture.whenStable();
      expect(cells(bodyRows()[0])[0]).toBe('Season 0');
      expect(button('Previous')?.disabled).toBe(false);
      expect(button('Next')?.disabled).toBe(true);

      button('Previous')?.click();
      await harness.fixture.whenStable();
      flushLeague();
      req = historyRequest();
      expect(req.request.params.get('page')).toBe('1');
      req.flush(
        pageEnvelope([historyRow()], { page: 1, page_size: 10, total_pages: 2, count: 11 }),
      );
      await harness.fixture.whenStable();
    });

    it('changing the size goes back to page 1 with the new page_size', async () => {
      await open([historyRow()], { page: 1, total_pages: 3, count: 21 });
      button('Next')?.click();
      await harness.fixture.whenStable();
      await answer([historyRow()], { page: 2, total_pages: 3, count: 21 });

      select().value = '25';
      select().dispatchEvent(new Event('change'));
      await harness.fixture.whenStable();
      flushLeague();
      const req = historyRequest();
      expect(req.request.params.get('page')).toBe('1');
      expect(req.request.params.get('page_size')).toBe('25');
      req.flush(
        pageEnvelope([historyRow()], { page: 1, page_size: 25, total_pages: 1, count: 21 }),
      );
      await harness.fixture.whenStable();
      expect(select().value).toBe('25');
    });
  });

  describe('load errors (I2)', () => {
    function fail(status: number, message: string) {
      return [
        { errors: [{ code: 'x', message, field: null }], meta: {} },
        { status, statusText: 'Error' },
      ] as const;
    }

    it('shows not-found for a 404, with no table', async () => {
      await setUp();
      await harness.navigateByUrl('/leagues/1/history');
      flushNav();
      flushLeague();
      historyRequest().flush(...fail(404, 'Not found.'));
      await harness.fixture.whenStable();
      expect(text(page().querySelector('app-league-history h1'))).toBe('League not found');
      expect(page().querySelector('#league-history-table')).toBeNull();
      expect(page().querySelector('app-league-history [role="alert"]')).toBeNull();
    });

    it('shows another failure in the error box, and Retry loads again', async () => {
      await setUp();
      await harness.navigateByUrl('/leagues/1/history');
      flushNav();
      flushLeague();
      historyRequest().flush(...fail(500, 'Database is down.'));
      await harness.fixture.whenStable();
      const alert = page().querySelector('app-league-history [role="alert"]');
      expect(text(alert?.querySelector('span'))).toBe('Database is down.');
      expect(page().querySelector('#league-history-table')).toBeNull();

      [...(alert?.querySelectorAll('button') ?? [])].find((b) => text(b) === 'Retry')?.click();
      await harness.fixture.whenStable();
      await answer([historyRow()]);
      expect(page().querySelector('app-league-history [role="alert"]')).toBeNull();
      expect(bodyRows()).toHaveLength(1);
    });
  });
});
