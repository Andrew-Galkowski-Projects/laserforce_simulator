import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import {
  HttpTestingController,
  TestRequest,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { By } from '@angular/platform-browser';
import { provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { environment } from '../../../environments/environment';
import { LeagueFinances as LeagueFinancesData } from '../../api/league-finances.models';
import { LeagueNav } from '../../api/leagues.models';
import { signIn } from '../../api/testing/auth-fixtures';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';
import { leagueFinanceRow, leagueFinances } from '../testing/league-finances-fixtures';
import { leagueNav } from '../testing/league-fixtures';
import { failure } from '../testing/league-stats-fixtures';
import { LeagueFinances } from './league-finances';

const BASE = environment.apiBaseUrl;
const PATH = '/leagues/1/finances';
const FINANCES_URL = `${BASE}/leagues/1/finances/`;
const DISABLED = 'Finances are disabled for this League.';
const NO_SEASON = 'No Season in this League yet — start a Season to view finances.';

describe('League Finances (/leagues/:id/finances, spec #439)', () => {
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
    page().querySelector<HTMLElement>(`app-league-finances ${selector}`);
  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const financeRequests = (): TestRequest[] => http.match((req) => req.url.endsWith('/finances/'));

  async function navigate(nav: LeagueNav = leagueNav()): Promise<void> {
    await harness.navigateByUrl(PATH);
    http.expectOne(`${BASE}/leagues/1/nav/`).flush({ data: nav, meta: {} });
    await harness.fixture.whenStable();
  }

  async function answer(data: LeagueFinancesData = leagueFinances()): Promise<TestRequest> {
    const req = http.expectOne(FINANCES_URL);
    req.flush({ data, meta: {} });
    await harness.fixture.whenStable();
    return req;
  }

  async function open(data: LeagueFinancesData = leagueFinances()): Promise<void> {
    await navigate();
    await answer(data);
  }

  const headers = (): string[] =>
    [...page().querySelectorAll('#league-finances-table th')].map((th) => text(th));
  const cells = (team: number): string[] =>
    [...page().querySelectorAll(`#league-finances-row-${team} td`)].map((td) => text(td));

  it('asks for GET leagues/{id}/finances/ once the nav says finances are on', async () => {
    await navigate();
    const req = await answer();
    expect(req.request.method).toBe('GET');
    expect(harness.fixture.debugElement.query(By.directive(LeagueFinances))).not.toBeNull();
  });

  it('sends nothing while the nav is still loading', async () => {
    await harness.navigateByUrl(PATH);
    expect(financeRequests()).toEqual([]);
    expect(text(q('[role="status"]'))).toBe('');
    http.expectOne(`${BASE}/leagues/1/nav/`).flush({ data: leagueNav(), meta: {} });
    await harness.fixture.whenStable();
    await answer();
  });

  describe('loaded', () => {
    it('heads the page "<League> — Finances", the Season name on a muted line under it', async () => {
      await open();
      expect(text(q('h1'))).toBe('Metro League — Finances');
      expect(text(q('#league-finances-season'))).toBe('Season 1');
    });

    it('has the columns Team, Revenue, Profit, Cash, Payroll, the money ones right-aligned', async () => {
      await open();
      expect(headers()).toEqual(['Team', 'Revenue', 'Profit', 'Cash', 'Payroll']);
      const numeric = [...page().querySelectorAll('#league-finances-table th')].map((th) =>
        th.classList.contains('num'),
      );
      expect(numeric).toEqual([false, true, true, true, true]);
      for (const td of page().querySelectorAll('#league-finances-row-3 td:not(:first-child)')) {
        expect(td.classList).toContain('num');
      }
    });

    it('shows a settled Team by name with every money figure as moneyK ($7.2k, -$7.2k)', async () => {
      await open();
      expect(cells(3)).toEqual(['Red Raiders', '$250.3k', '-$7.2k', '$120.0k', '$180.0k']);
    });

    it('shows — for Revenue, Profit and Payroll of an unsettled Team, but still its Cash', async () => {
      await open();
      expect(cells(5)).toEqual(['Blue Bolts', '—', '—', '$95.5k', '—']);
    });

    it('lists the rows in the order the API sends them (by name)', async () => {
      await open();
      const ids = [...page().querySelectorAll('#league-finances-table tbody tr')].map(
        (tr) => tr.id,
      );
      expect(ids).toEqual(['league-finances-row-5', 'league-finances-row-3']);
    });

    it('a loss that rounds to zero carries no sign', async () => {
      await open(leagueFinances({ rows: [leagueFinanceRow({ profit: -0.04 })] }));
      expect(cells(3)[2]).toBe('$0.0k');
    });

    it('says "No enrolled teams." in one row when the displayed Season has no Teams', async () => {
      await open(leagueFinances({ rows: [] }));
      const rows = page().querySelectorAll('#league-finances-table tbody tr');
      expect(rows).toHaveLength(1);
      expect(text(rows[0])).toBe('No enrolled teams.');
      expect(rows[0].querySelector('td')?.getAttribute('colspan')).toBe('5');
    });

    it('sits the table in a scrolling box', async () => {
      await open();
      expect(q('#league-finances-table')?.parentElement?.classList).toContain('stats-scroll');
    });
  });

  describe('I1: empty states, in order', () => {
    it('finances off in the nav: heads "<League> — Finances", says so, and sends no request', async () => {
      await navigate(leagueNav({ finance_enabled: false }));
      expect(financeRequests()).toEqual([]);
      expect(text(q('h1'))).toBe('Metro League — Finances');
      expect(text(q('#league-finances-disabled-notice'))).toBe(DISABLED);
      expect(q('#league-finances-table')).toBeNull();
    });

    it('finances off wins over no Season', async () => {
      await navigate(leagueNav({ finance_enabled: false, displayed_season: null }));
      expect(financeRequests()).toEqual([]);
      expect(text(q('#league-finances-disabled-notice'))).toBe(DISABLED);
      expect(q('#league-finances-empty-notice')).toBeNull();
    });

    it('no displayed Season: heads "<League> — Finances", says so, and sends no request', async () => {
      await navigate(leagueNav({ displayed_season: null, schedule_team: null }));
      expect(financeRequests()).toEqual([]);
      expect(text(q('h1'))).toBe('Metro League — Finances');
      expect(text(q('#league-finances-empty-notice'))).toBe(NO_SEASON);
      expect(q('#league-finances-table')).toBeNull();
    });

    it('a loaded finance_enabled: false also shows the disabled notice', async () => {
      await open({ finance_enabled: false, rows: [] });
      expect(text(q('h1'))).toBe('Metro League — Finances');
      expect(text(q('#league-finances-disabled-notice'))).toBe(DISABLED);
      expect(q('#league-finances-table')).toBeNull();
      expect(q('#league-finances-season')).toBeNull();
    });
  });

  describe('I2: a failed load', () => {
    it('shows the error box with the API message; Retry re-sends the request', async () => {
      await navigate();
      http.expectOne(FINANCES_URL).flush(...failure(500, 'Database is down.'));
      await harness.fixture.whenStable();
      const box = q('.stats-error');
      expect(box?.getAttribute('role')).toBe('alert');
      expect(text(box?.querySelector('span'))).toBe('Database is down.');
      expect(text(q('h1'))).toBe('Metro League — Finances');
      expect(q('#league-finances-table')).toBeNull();

      (box?.querySelector('button') as HTMLButtonElement).click();
      await harness.fixture.whenStable();
      await answer();
      expect(q('.stats-error')).toBeNull();
      expect(cells(3)[0]).toBe('Red Raiders');
    });

    it('a dropped connection shows the network message and Retry', async () => {
      await navigate();
      http.expectOne(FINANCES_URL).error(new ProgressEvent('error'));
      await harness.fixture.whenStable();
      expect(text(q('.stats-error span'))).toBe('Could not reach the server.');
      expect(text(q('.stats-error button'))).toBe('Retry');
    });
  });
});
