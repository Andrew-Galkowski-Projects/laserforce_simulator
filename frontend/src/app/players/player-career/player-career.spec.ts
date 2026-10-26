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
import { CareerRole, CareerStatRow, CareerStats, StatBenchmark } from '../../api/players.models';
import { SELF_INSERT, USER, signIn } from '../../api/testing/auth-fixtures';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';
import { stubCanvasContext } from '../../matches/testing/fake-canvas';
import { LineChart } from '../../shared/line-chart';
import { PlayerCareer } from './player-career';

const BASE = environment.apiBaseUrl;
const CAREER_URL = `${BASE}/players/7/career/`;

function benchmark(fields: Partial<StatBenchmark> = {}): StatBenchmark {
  return {
    benchmark_mean: 10,
    benchmark_median: 9,
    delta_mean: 1.5,
    delta_median: -0.25,
    percentile: 62,
    qualified: true,
    n: 4,
    ...fields,
  };
}

function statRow(fields: Partial<CareerStatRow> = {}): CareerStatRow {
  return {
    key: 'points_scored',
    label: 'Points',
    player_value: 11.5,
    benchmark: benchmark(),
    ...fields,
  };
}

function careerRole(fields: Partial<CareerRole> = {}): CareerRole {
  return {
    role: 'commander',
    games: 3,
    avg_points: 4000,
    tag_ratio: 1,
    avg_survival_ticks: 1000,
    avg_accuracy_pct: 50,
    avg_sp_earned: 30,
    stat_rows: [statRow()],
    ...fields,
  };
}

function career(fields: Partial<CareerStats> = {}): CareerStats {
  return {
    player: { id: 7, name: 'Ace', team: { id: 3, name: 'Comets' } },
    total_rounds: 12,
    has_rounds: true,
    career: {
      games: 12,
      avg_points: 4522.26,
      tag_ratio: 0.956,
      avg_survival_ticks: 1239,
      avg_accuracy_pct: 54.6,
      avg_sp_earned: 37.44,
    },
    per_role: [],
    per_role_with_benchmarks: [careerRole()],
    trend: [
      [1, 2502],
      [2, 3511.5],
      [3, 4522],
    ],
    min_rounds: 5,
    display: 'mean',
    stat_keys: ['points_scored'],
    ...fields,
  };
}

describe('Career stats (/players/:id/stats)', () => {
  let http: HttpTestingController;
  let harness: RouterTestingHarness;
  let restoreCanvas: () => void;

  async function setUp(user = USER): Promise<void> {
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

  beforeEach(() => {
    restoreCanvas = stubCanvasContext();
  });

  afterEach(() => {
    http.verify({ ignoreCancelled: true });
    restoreCanvas();
  });

  const page = (): HTMLElement => harness.routeNativeElement as HTMLElement;
  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const routerUrl = (): string => TestBed.inject(Router).url;
  const careerRequest = (): TestRequest => http.expectOne((r) => r.url === CAREER_URL);
  const rowCells = (selector: string): string[] =>
    [...page().querySelectorAll(`${selector} tbody tr:first-child td`)].map((td) => text(td));

  /** Opens `url` and answers its career GET with `body`; returns that request. */
  async function open(
    body: CareerStats = career(),
    url = '/players/7/stats',
    user = USER,
  ): Promise<TestRequest> {
    await setUp(user);
    await harness.navigateByUrl(url);
    const req = careerRequest();
    req.flush({ data: body, meta: {} });
    await harness.fixture.whenStable();
    return req;
  }

  /** The per-role table's first row cells, Stat to n. */
  const statCells = (role = 'commander'): string[] => rowCells(`#career-role-${role}`);

  describe('header', () => {
    it('routes to PlayerCareer and asks for the career with the server defaults', async () => {
      const req = await open();
      expect(harness.fixture.debugElement.query(By.directive(PlayerCareer))).not.toBeNull();
      expect(req.request.params.keys()).toEqual([]);
    });

    it('shows the name, "— <Team>", the total rounds and the Role benchmarks link', async () => {
      await open();
      expect(text(page().querySelector('h1'))).toBe('Ace — Comets');
      expect(text(page())).toContain('Total rounds: 12');
      const link = [...page().querySelectorAll('a')].find((a) => text(a) === 'Role benchmarks');
      expect(link?.getAttribute('href')).toBe('/players/benchmarks');
    });

    it('leaves out the Team part for a Player with no Team', async () => {
      await open(career({ player: { id: 7, name: 'Ace', team: null } }));
      expect(text(page().querySelector('h1'))).toBe('Ace');
    });

    it('marks your own Self-insert with a "You" badge after the name', async () => {
      const self = { ...SELF_INSERT, id: 7 };
      await open(career(), '/players/7/stats', { ...USER, self_insert: self });
      const badge = page().querySelector('h1 .badge--you');
      expect(text(badge)).toBe('You');
      expect(text(page().querySelector('h1'))).toBe('Ace You — Comets');
    });

    it('shows no badge on another Player', async () => {
      await open(career(), '/players/7/stats', { ...USER, self_insert: SELF_INSERT });
      expect(page().querySelector('.badge--you')).toBeNull();
    });
  });

  describe('a Player with no rounds', () => {
    it('says "No rounds played yet." and shows no form, table or chart', async () => {
      await open(
        career({ has_rounds: false, total_rounds: 0, per_role_with_benchmarks: [], trend: [] }),
      );
      expect(text(page())).toContain('Total rounds: 0');
      expect(text(page())).toContain('No rounds played yet.');
      expect(page().querySelector('form')).toBeNull();
      expect(page().querySelector('table')).toBeNull();
      expect(harness.fixture.debugElement.query(By.directive(LineChart))).toBeNull();
    });

    it('does not say it for a Player with rounds', async () => {
      await open();
      expect(text(page())).not.toContain('No rounds played yet.');
    });
  });

  describe('career totals', () => {
    it('formats the six metrics as the Django page does', async () => {
      await open();
      const head = [...page().querySelectorAll('#career-totals th')].map((th) => text(th));
      expect(head).toEqual([
        'Games',
        'Avg points',
        'Tag ratio',
        'Avg survival',
        'Avg accuracy',
        'Avg SP earned',
      ]);
      expect(rowCells('#career-totals')).toEqual(['12', '4522.3', '0.96', '620s', '55%', '37.4']);
    });
  });

  describe('per-role tables', () => {
    it('titles each role "<Role> — N game(s)" with the seven columns', async () => {
      await open(
        career({
          per_role_with_benchmarks: [careerRole(), careerRole({ role: 'medic', games: 1 })],
        }),
      );
      const headings = [...page().querySelectorAll('.career-role h3')].map((h) => text(h));
      expect(headings).toEqual(['Commander — 3 games', 'Medic — 1 game']);
      const head = [...page().querySelectorAll('#career-role-commander th')].map((th) => text(th));
      expect(head).toEqual(['Stat', 'Player value', 'Mean', 'Median', 'Δ', 'Percentile', 'n']);
    });

    it('shows the stat, mean and median to 2 decimals, Δ vs the mean, percentile and n', async () => {
      await open();
      expect(statCells()).toEqual(['Points', '11.5', '10.00', '9.00', '(+1.50)', 'p62', 'n = 4']);
    });

    it('shows Δ against the median when the page shows the median', async () => {
      await open(career({ display: 'median' }));
      expect(statCells()[4]).toBe('(-0.25)');
    });

    it('says how many rounds a Player needs when they do not qualify', async () => {
      await open(career({ min_rounds: 3, per_role_with_benchmarks: [notQualified()] }));
      expect(statCells()[4]).toBe('— (need 3+ rounds)');

      function notQualified(): CareerRole {
        return careerRole({ stat_rows: [statRow({ benchmark: benchmark({ qualified: false }) })] });
      }
    });

    it('shows five "—" cells for a stat with no benchmark', async () => {
      await open(
        career({
          per_role_with_benchmarks: [careerRole({ stat_rows: [statRow({ benchmark: null })] })],
        }),
      );
      expect(statCells()).toEqual(['Points', '11.5', '—', '—', '—', '—', '—']);
    });

    it('shows "—" for a missing mean or median and a blank percentile', async () => {
      const empty = benchmark({ benchmark_mean: null, benchmark_median: null, percentile: null });
      await open(
        career({
          per_role_with_benchmarks: [careerRole({ stat_rows: [statRow({ benchmark: empty })] })],
        }),
      );
      expect(statCells()).toEqual(['Points', '11.5', '—', '—', '(+1.50)', '', 'n = 4']);
    });

    it.each([
      ['avg_points', 4522.26, '4522.3'],
      ['tag_ratio', 0.956, '0.96'],
      ['avg_survival_ticks', 1239, '620s'],
      ['avg_accuracy_pct', 54.6, '55%'],
      ['avg_sp_earned', 37.44, '37.4'],
      ['mvp', 1.234, '1.23'],
      ['tags_landed', 12.34, '12.3'],
    ])('formats a %s player value as %s', async (key, value, shown) => {
      await open(
        career({
          per_role_with_benchmarks: [
            careerRole({ stat_rows: [statRow({ key, player_value: value, benchmark: null })] }),
          ],
        }),
      );
      expect(statCells()[1]).toBe(shown);
    });

    it('shows the accuracy benchmarks as whole percentages', async () => {
      const row = statRow({
        key: 'avg_accuracy_pct',
        benchmark: benchmark({ benchmark_mean: 51.4, benchmark_median: 48.6 }),
      });
      await open(career({ per_role_with_benchmarks: [careerRole({ stat_rows: [row] })] }));
      expect(statCells().slice(2, 4)).toEqual(['51%', '49%']);
    });
  });

  describe('filters in the URL', () => {
    const input = (): HTMLInputElement =>
      page().querySelector('input[name="threshold"]') as HTMLInputElement;
    const select = (): HTMLSelectElement =>
      page().querySelector('select[name="display"]') as HTMLSelectElement;

    async function apply(threshold: string, display: string): Promise<void> {
      input().value = threshold;
      input().dispatchEvent(new Event('input'));
      select().value = display;
      select().dispatchEvent(new Event('change'));
      (page().querySelector('form') as HTMLFormElement).dispatchEvent(new Event('submit'));
      await harness.fixture.whenStable();
    }

    it('sends the URL threshold and display, and shows the values the server used', async () => {
      const req = await open(
        career({ min_rounds: 3, display: 'median' }),
        '/players/7/stats?threshold=3&display=median',
      );
      expect(req.request.params.get('threshold')).toBe('3');
      expect(req.request.params.get('display')).toBe('median');
      expect(input().value).toBe('3');
      expect(select().value).toBe('median');
    });

    it('drops a threshold or display the API would refuse, so the server defaults apply', async () => {
      const req = await open(career(), '/players/7/stats?threshold=-2&display=mode');
      expect(req.request.params.keys()).toEqual([]);
    });

    it('carries min="0" and required on the threshold', async () => {
      await open();
      expect(input().getAttribute('min')).toBe('0');
      expect(input().required).toBe(true);
    });

    it('Apply writes the filters to the URL and reloads the career with them', async () => {
      await open();
      await apply('2', 'median');
      expect(routerUrl()).toBe('/players/7/stats?threshold=2&display=median');
      const req = careerRequest();
      expect(req.request.params.get('threshold')).toBe('2');
      expect(req.request.params.get('display')).toBe('median');
      req.flush({ data: career({ min_rounds: 2, display: 'median' }), meta: {} });
      await harness.fixture.whenStable();
      expect(statCells()[4]).toBe('(-0.25)');
    });

    it('refuses a negative or blank threshold in the form', async () => {
      await open();
      await apply('-1', 'mean');
      expect(routerUrl()).toBe('/players/7/stats');
      expect(text(page().querySelector('mat-error'))).toBe('Enter 0 or more.');
      await apply('', 'mean');
      expect(routerUrl()).toBe('/players/7/stats');
    });
  });

  describe('the points trend chart', () => {
    it('plots the rolling-10 average by round number as a dashed line in token colours', async () => {
      document.documentElement.style.setProperty('--lf-accent', 'rgb(1, 2, 3)');
      document.documentElement.style.setProperty('--lf-font-body', 'Test Mono');
      try {
        await open();
      } finally {
        document.documentElement.style.removeProperty('--lf-accent');
        document.documentElement.style.removeProperty('--lf-font-body');
      }
      const chart = harness.fixture.debugElement.query(By.directive(LineChart))
        .componentInstance as LineChart;
      expect(chart.xAxis()).toBe('count');
      expect(chart.xTitle()).toBe('Round number');
      expect(chart.yTitle()).toBe('Avg points (rolling 10)');
      expect(chart.maxSec()).toBe(3);
      expect(chart.theme().font).toBe('Test Mono');
      expect(chart.datasets()).toEqual([
        {
          label: 'Avg points (rolling 10)',
          color: 'rgb(1, 2, 3)',
          dashed: true,
          pointRadius: 2,
          points: [
            { sec: 1, value: 2502 },
            { sec: 2, value: 3511.5 },
            { sec: 3, value: 4522 },
          ],
        },
      ]);
    });
  });

  describe('load failures', () => {
    it('says "Player not found." with a link to /players on a 404', async () => {
      await setUp();
      await harness.navigateByUrl('/players/7/stats');
      careerRequest().flush(
        { errors: [{ code: 'not_found', message: 'Not found.', field: null }], meta: {} },
        { status: 404, statusText: 'Not Found' },
      );
      await harness.fixture.whenStable();
      expect(text(page())).toContain('Player not found.');
      const back = [...page().querySelectorAll('a')].find((a) => text(a) === '← Back to Players');
      expect(back?.getAttribute('href')).toBe('/players');
      expect(page().querySelector('table')).toBeNull();
    });

    it('shows any other failure in an alert', async () => {
      await setUp();
      await harness.navigateByUrl('/players/7/stats');
      careerRequest().flush(
        { errors: [{ code: 'server_error', message: 'Database down.', field: null }], meta: {} },
        { status: 500, statusText: 'Server Error' },
      );
      await harness.fixture.whenStable();
      expect(text(page().querySelector('[role="alert"]'))).toBe('Database down.');
      expect(text(page())).not.toContain('Player not found.');
    });
  });

  describe('wide tables', () => {
    it('scroll sideways inside their own container', async () => {
      await open();
      const tables = [...page().querySelectorAll('table')];
      expect(tables.length).toBe(2);
      for (const table of tables) {
        expect(getComputedStyle(table.parentElement as HTMLElement).overflowX).toBe('auto');
      }
    });
  });
});
