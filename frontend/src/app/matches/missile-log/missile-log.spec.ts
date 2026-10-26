import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { By, Title } from '@angular/platform-browser';
import { provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { environment } from '../../../environments/environment';
import { MissileLogRow, RoundMissileLog } from '../../api/rounds.models';
import { signIn } from '../../api/testing/auth-fixtures';
import { APP_TITLE } from '../../app-title';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';
import { RoundDetail } from '../round-detail/round-detail';
import { roundDetail } from '../testing/match-fixtures';
import { MissileLog } from './missile-log';

const BASE = environment.apiBaseUrl;

function missileRow(overrides: Partial<MissileLogRow> = {}): MissileLogRow {
  return {
    timestamp: 84,
    timestamp_mmss: '00:42',
    actor_role: 'scout',
    target_role: 'commander',
    result: 'hit',
    friendly_fire: false,
    description: 'Scout missiled Commander',
    points: 500,
    row_class: 'missile-row',
    ...overrides,
  };
}

function missileLog(overrides: Partial<RoundMissileLog> = {}): RoundMissileLog {
  return { round_id: 9, fired: 0, hit: 0, efficiency: 0, rows: [], ...overrides };
}

/** Three fired, two hit (one of them Friendly fire), one miss. */
function threeMissiles(): RoundMissileLog {
  return missileLog({
    fired: 3,
    hit: 2,
    efficiency: 66.66666666666667,
    rows: [
      missileRow(),
      missileRow({
        timestamp: 600,
        timestamp_mmss: '05:00',
        actor_role: 'commander',
        target_role: 'heavy',
        result: 'hit',
        friendly_fire: true,
        description: 'Commander missiled own Heavy',
        points: 0,
        row_class: 'missile-row friendly-fire',
      }),
      missileRow({
        timestamp: 1200,
        timestamp_mmss: '10:00',
        actor_role: 'commander',
        target_role: 'medic',
        result: 'miss',
        description: 'Commander missed Medic',
        points: 0,
      }),
    ],
  });
}

const notFound = {
  body: { errors: [{ code: 'not_found', message: 'Not found.', field: null }], meta: {} },
  init: { status: 404, statusText: 'Not Found' },
};

describe('Missile log (/rounds/:id/missile-log)', () => {
  let http: HttpTestingController;
  let harness: RouterTestingHarness;

  async function setUp(): Promise<void> {
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

  afterEach(() => http.verify({ ignoreCancelled: true }));

  const page = (): HTMLElement => harness.routeNativeElement as HTMLElement;
  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const link = (label: string): HTMLAnchorElement | undefined =>
    [...page().querySelectorAll('a')].find((a) => text(a).includes(label));
  const bodyRows = (): HTMLTableRowElement[] => [
    ...page().querySelectorAll<HTMLTableRowElement>('tbody tr'),
  ];
  const cells = (row: Element): string[] => [...row.querySelectorAll('td')].map((td) => text(td));
  const stats = (): string[][] =>
    [...page().querySelectorAll('.ml-stat')].map((s) => [
      text(s.querySelector('.ml-stat__label')),
      text(s.querySelector('.ml-stat__value')),
    ]);

  /** Opens `/rounds/9/missile-log` and answers the Round GET. */
  async function openRound(): Promise<void> {
    await setUp();
    await harness.navigateByUrl('/rounds/9/missile-log');
    http
      .expectOne(`${BASE}/rounds/9/`)
      .flush({ data: roundDetail({ id: 9, team_red_name: 'Comets', team_blue_name: 'Vipers' }) });
    await harness.fixture.whenStable();
  }

  /** Opens the screen and answers both GETs (the Round, then its Missile log). */
  async function open(log: RoundMissileLog = threeMissiles()): Promise<void> {
    await openRound();
    http.expectOne(`${BASE}/rounds/9/missile-log/`).flush({ data: log, meta: {} });
    await harness.fixture.whenStable();
  }

  describe('route and page head', () => {
    it('routes :id/missile-log to the Missile log (not Round detail)', async () => {
      await open();
      const de = harness.fixture.debugElement;
      expect(de.query(By.directive(MissileLog))).not.toBeNull();
      expect(de.query(By.directive(RoundDetail))).toBeNull();
    });

    it('heads the page "Missile log" over "<red> vs <blue>" and titles the tab', async () => {
      await open();
      expect(text(page().querySelector('.ml-kicker'))).toBe('Missile log');
      expect(text(page().querySelector('h1'))).toBe('Comets vs Vipers');
      expect(TestBed.inject(Title).getTitle()).toBe(`Missile Log - ${APP_TITLE}`);
    });

    it('links back to the Round and to its Event log', async () => {
      await open();
      expect(link('Back to Round')?.getAttribute('href')).toBe('/rounds/9');
      expect(link('Event log')?.getAttribute('href')).toBe('/rounds/9/events');
    });
  });

  describe('loading', () => {
    it('shows a loading state until the Missile log arrives', async () => {
      await openRound();
      const req = http.expectOne(`${BASE}/rounds/9/missile-log/`);
      try {
        expect(text(page())).toContain('Loading the Missile log');
        expect(page().querySelector('table')).toBeNull();
      } finally {
        req.flush({ data: missileLog(), meta: {} });
      }
      await harness.fixture.whenStable();
      expect(text(page())).not.toContain('Loading the Missile log');
      expect(page().querySelector('table')).not.toBeNull();
    });
  });

  describe('stats', () => {
    it('shows Fired, Hit and Efficiency to one decimal place with %', async () => {
      await open();
      expect(stats()).toEqual([
        ['Fired', '3'],
        ['Hit', '2'],
        ['Efficiency', '66.7%'],
      ]);
    });

    it('shows 0, 0 and 0.0% for a Round with no missiles', async () => {
      await open(missileLog());
      expect(stats()).toEqual([
        ['Fired', '0'],
        ['Hit', '0'],
        ['Efficiency', '0.0%'],
      ]);
    });
  });

  describe('table', () => {
    it('has the columns Time, Actor, Target, Result, Friendly fire, Description, Points', async () => {
      await open();
      expect([...page().querySelectorAll('thead th')].map((th) => text(th))).toEqual([
        'Time',
        'Actor',
        'Target',
        'Result',
        'Friendly fire',
        'Description',
        'Points',
      ]);
    });

    it('renders one row per Missiled event in order, roles as short labels', async () => {
      await open();
      expect(bodyRows().map(cells)).toEqual([
        ['00:42', 'SCT', 'CMD', 'hit', 'no', 'Scout missiled Commander', '500'],
        ['05:00', 'CMD', 'HVY', 'hit', 'yes', 'Commander missiled own Heavy', '0'],
        ['10:00', 'CMD', 'MED', 'miss', 'no', 'Commander missed Medic', '0'],
      ]);
    });

    it('marks exactly the Friendly-fire rows', async () => {
      await open();
      expect(bodyRows().map((r) => r.className.includes('friendly-fire'))).toEqual([
        false,
        true,
        false,
      ]);
    });

    it('says "No missiles fired this round." in one full-width row when there are none', async () => {
      await open(missileLog());
      const rows = bodyRows();
      expect(rows.length).toBe(1);
      const only = rows[0].querySelectorAll('td');
      expect(only.length).toBe(1);
      expect(only[0].getAttribute('colspan')).toBe('7');
      expect(text(only[0])).toBe('No missiles fired this round.');
    });
  });

  describe('load failures', () => {
    it('shows Round not found on a 404 (a missing or another Account’s Round)', async () => {
      await setUp();
      await harness.navigateByUrl('/rounds/9/missile-log');
      http.expectOne(`${BASE}/rounds/9/`).flush(notFound.body, notFound.init);
      await harness.fixture.whenStable();
      expect(text(page())).toContain('Round not found');
      expect(page().querySelector('table')).toBeNull();
      http.expectNone(`${BASE}/rounds/9/missile-log/`);
    });

    it('shows "Could not load the Round." when the Round load fails otherwise', async () => {
      await setUp();
      await harness.navigateByUrl('/rounds/9/missile-log');
      http.expectOne(`${BASE}/rounds/9/`).flush(null, { status: 500, statusText: 'Server Error' });
      await harness.fixture.whenStable();
      expect(text(page().querySelector('[role="alert"]'))).toContain('Could not load the Round.');
      expect(page().querySelector('table')).toBeNull();
    });

    it("shows a failed Missile log load's message in the alert box", async () => {
      await openRound();
      http.expectOne(`${BASE}/rounds/9/missile-log/`).flush(
        {
          errors: [{ code: 'server_error', message: 'Replay failed.', field: null }],
          meta: {},
        },
        { status: 500, statusText: 'Server Error' },
      );
      await harness.fixture.whenStable();
      expect(text(page().querySelector('[role="alert"]'))).toBe('Replay failed.');
      expect(text(page().querySelector('h1'))).toBe('Comets vs Vipers');
      expect(text(page())).not.toContain('Loading the Missile log');
      expect(page().querySelector('table')).toBeNull();
    });
  });
});
