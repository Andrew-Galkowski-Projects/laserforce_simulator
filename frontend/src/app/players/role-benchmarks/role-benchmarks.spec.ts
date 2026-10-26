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
import { BenchmarkRow, RoleBenchmarks as RoleBenchmarksBody } from '../../api/players.models';
import { signIn } from '../../api/testing/auth-fixtures';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';
import { RoleBenchmarks } from './role-benchmarks';

const BENCHMARKS_URL = `${environment.apiBaseUrl}/players/benchmarks/`;

function row(fields: Partial<BenchmarkRow> = {}): BenchmarkRow {
  return {
    stat: 'points_scored',
    mean: 7661.9,
    median: 7267,
    p25: 6582.125,
    p75: 7322,
    p90: 9531.5,
    n: 4,
    ...fields,
  };
}

function body(fields: Partial<RoleBenchmarksBody> = {}): RoleBenchmarksBody {
  return {
    min_rounds: 5,
    display: 'mean',
    roles: ['commander', 'heavy', 'scout', 'medic', 'ammo'],
    stat_keys: ['points_scored', 'tags_landed'],
    benchmarks: {
      commander: [row(), row({ stat: 'tags_landed', mean: 12.345, n: 3 })],
      heavy: [row({ n: 2 })],
      scout: [row()],
      medic: [row()],
      ammo: [row()],
    },
    any_data: true,
    ...fields,
  };
}

const NO_DATA = {
  commander: [row({ mean: null, median: null, p25: null, p75: null, p90: null, n: 0 })],
};

describe('Role Benchmarks (/players/benchmarks)', () => {
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

  afterEach(() => {
    http.verify({ ignoreCancelled: true });
  });

  const page = (): HTMLElement => harness.routeNativeElement as HTMLElement;
  const screen = (): HTMLElement =>
    harness.fixture.debugElement.query(By.directive(RoleBenchmarks)).nativeElement as HTMLElement;
  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const routerUrl = (): string => TestBed.inject(Router).url;
  const request = (): TestRequest => http.expectOne((r) => r.url === BENCHMARKS_URL);
  const cells = (role: string, index = 0): string[] => {
    const rows = screen().querySelectorAll(`#benchmark-table-${role} tbody tr`);
    return [...(rows[index]?.querySelectorAll('td') ?? [])].map((td) => text(td));
  };
  const input = (): HTMLInputElement =>
    screen().querySelector('input[name="threshold"]') as HTMLInputElement;
  const select = (): HTMLSelectElement =>
    screen().querySelector('select[name="display"]') as HTMLSelectElement;

  /** Opens `url` and answers its benchmarks GET with `data`; returns that request. */
  async function open(
    data: RoleBenchmarksBody = body(),
    url = '/players/benchmarks',
  ): Promise<TestRequest> {
    await setUp();
    await harness.navigateByUrl(url);
    const req = request();
    req.flush({ data, meta: {} });
    await harness.fixture.whenStable();
    return req;
  }

  it('asks for the benchmarks with the server defaults and explains the page', async () => {
    const req = await open();
    expect(req.request.params.keys()).toEqual([]);
    expect(text(screen().querySelector('h1'))).toBe('Role Benchmarks');
    expect(text(screen())).toContain(
      "Per-role averages computed from every player's career-average when playing the role, " +
        'over your saved Rounds (plus any unclaimed ones). Filter the population to players ' +
        'with at least N rounds in that role.',
    );
  });

  it('shows one table per role, headed by the capitalised role', async () => {
    await open();
    const headings = [...screen().querySelectorAll('h2')].map((h) => text(h));
    expect(headings).toEqual(['Commander', 'Heavy', 'Scout', 'Medic', 'Ammo']);
    const head = [...screen().querySelectorAll('#benchmark-table-commander th')].map((th) =>
      text(th),
    );
    expect(head).toEqual(['Stat', 'Mean', 'Median', 'P25', 'P75', 'P90', 'N']);
  });

  it('shows each stat key with its values to 2 decimals and N', async () => {
    await open();
    expect(cells('commander')).toEqual([
      'points_scored',
      '7661.90',
      '7267.00',
      '6582.13',
      '7322.00',
      '9531.50',
      '4',
    ]);
    expect(cells('commander', 1)).toEqual([
      'tags_landed',
      '12.35',
      '7267.00',
      '6582.13',
      '7322.00',
      '9531.50',
      '3',
    ]);
    expect(cells('heavy')[6]).toBe('2');
  });

  it('shows "—" for a value the population cannot give', async () => {
    await open(body({ roles: ['commander'], benchmarks: NO_DATA, any_data: false }));
    expect(cells('commander')).toEqual(['points_scored', '—', '—', '—', '—', '—', '0']);
  });

  describe('no data', () => {
    it('says which threshold kept every Player out, pluralising Round', async () => {
      await open(
        body({ min_rounds: 5, roles: ['commander'], benchmarks: NO_DATA, any_data: false }),
      );
      expect(text(screen().querySelector('.bp-notice'))).toBe(
        'No benchmarks yet: a player needs at least 5 saved Rounds in a role to count. ' +
          'Lower Min rounds in role above to include players with fewer Rounds.',
      );
      expect(text(screen().querySelector('.bp-notice strong'))).toBe('Min rounds in role');
    });

    it('says "Round" for a threshold of 1', async () => {
      await open(
        body({ min_rounds: 1, roles: ['commander'], benchmarks: NO_DATA, any_data: false }),
      );
      expect(text(screen().querySelector('.bp-notice'))).toContain('at least 1 saved Round in a');
    });

    it('asks for saved Rounds when the threshold is 0', async () => {
      await open(
        body({ min_rounds: 0, roles: ['commander'], benchmarks: NO_DATA, any_data: false }),
      );
      expect(text(screen().querySelector('.bp-notice'))).toBe(
        'No saved Rounds yet. Play and save some Rounds first.',
      );
    });

    it('shows no notice when there is data', async () => {
      await open();
      expect(screen().querySelector('.bp-notice')).toBeNull();
    });
  });

  describe('filters in the URL', () => {
    async function apply(threshold: string, display: string): Promise<void> {
      input().value = threshold;
      input().dispatchEvent(new Event('input'));
      select().value = display;
      select().dispatchEvent(new Event('change'));
      (screen().querySelector('form') as HTMLFormElement).dispatchEvent(new Event('submit'));
      await harness.fixture.whenStable();
    }

    it('sends the URL threshold and display, and shows the values the server used', async () => {
      const req = await open(
        body({ min_rounds: 0, display: 'median' }),
        '/players/benchmarks?threshold=0&display=median',
      );
      expect(req.request.params.get('threshold')).toBe('0');
      expect(req.request.params.get('display')).toBe('median');
      expect(input().value).toBe('0');
      expect(select().value).toBe('median');
    });

    it('carries min="0" and required on the threshold', async () => {
      await open();
      expect(input().getAttribute('min')).toBe('0');
      expect(input().required).toBe(true);
    });

    it('Apply writes the filters to the URL and reloads the benchmarks with them', async () => {
      await open();
      await apply('2', 'median');
      expect(routerUrl()).toBe('/players/benchmarks?threshold=2&display=median');
      const req = request();
      expect(req.request.params.get('threshold')).toBe('2');
      expect(req.request.params.get('display')).toBe('median');
      req.flush({ data: body({ min_rounds: 2 }), meta: {} });
      await harness.fixture.whenStable();
      expect(input().value).toBe('2');
    });

    it('refuses a negative threshold in the form', async () => {
      await open();
      await apply('-1', 'mean');
      expect(routerUrl()).toBe('/players/benchmarks');
      expect(text(screen().querySelector('mat-error'))).toBe('Enter 0 or more.');
    });
  });

  it('shows a load failure in an alert', async () => {
    await setUp();
    await harness.navigateByUrl('/players/benchmarks');
    request().flush(
      { errors: [{ code: 'server_error', message: 'Database down.', field: null }], meta: {} },
      { status: 500, statusText: 'Server Error' },
    );
    await harness.fixture.whenStable();
    expect(text(page().querySelector('[role="alert"]'))).toBe('Database down.');
    expect(screen().querySelector('table')).toBeNull();
  });

  it('scrolls each wide table sideways inside its own container', async () => {
    await open();
    const tables = [...screen().querySelectorAll('table')];
    expect(tables.length).toBe(5);
    for (const table of tables) {
      expect(getComputedStyle(table.parentElement as HTMLElement).overflowX).toBe('auto');
    }
  });
});
