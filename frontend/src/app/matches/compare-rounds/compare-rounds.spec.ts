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
import {
  PlayerStatDeltaRow,
  PointsSeries,
  RoundComparison,
  RoundRef,
  StatDelta,
} from '../../api/analytics.models';
import { GameRoundListItem } from '../../api/rounds.models';
import { SELF_INSERT, USER, signIn } from '../../api/testing/auth-fixtures';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';
import { User } from '../../auth/user';
import { LineChart } from '../../shared/line-chart';
import { RecordNotFound } from '../shared/record-not-found';
import { stubCanvasContext } from '../testing/fake-canvas';
import { roundRow } from '../testing/match-fixtures';
import { CompareRounds } from './compare-rounds';

const BASE = environment.apiBaseUrl;
const LIST_URL = `${BASE}/rounds/`;
const COMPARE_URL = `${BASE}/rounds/compare/`;

/** The picker's Rounds, newest first as the server orders them. */
const ROUNDS: GameRoundListItem[] = [
  roundRow({ id: 12, team_red_name: 'Alpha', team_blue_name: 'Bravo' }),
  roundRow({ id: 11, team_red_name: 'Bravo', team_blue_name: 'Alpha' }),
  roundRow({ id: 3, team_red_name: 'Comets', team_blue_name: 'Alpha' }),
];

function roundRef(id: number): RoundRef {
  return {
    id,
    round_number: 1,
    match_id: null,
    date_played: '2026-10-05T14:30:00Z',
    team_red: { id: 1, name: 'Alpha' },
    team_blue: { id: 2, name: 'Bravo' },
  };
}

const cell = (a: number | null, b: number | null): StatDelta => ({
  a,
  b,
  delta: a === null || b === null ? null : b - a,
});

function deltaRow(fields: Partial<PlayerStatDeltaRow> = {}): PlayerStatDeltaRow {
  const cells = fields.cells ?? [cell(100, 140), cell(2, 1)];
  return {
    player_id: 20,
    name: 'Ace',
    role_a: 'commander',
    role_b: 'heavy',
    side_a: 'red',
    side_b: 'blue',
    stats: { points_scored: cells[0], tags_made: cells[1] },
    cells,
    ...fields,
  };
}

function series(fields: Partial<PointsSeries> = {}): PointsSeries {
  return {
    team_id: 1,
    team_name: 'Alpha',
    a: [
      [0, 0],
      [120, 300],
    ],
    b: [
      [0, 0],
      [240, 500],
    ],
    ...fields,
  };
}

function comparison(fields: Partial<RoundComparison> = {}): RoundComparison {
  return {
    round_a: roundRef(12),
    round_b: roundRef(11),
    stat_keys: ['points_scored', 'tags_made'],
    team_ids: [1, 2],
    points_series: [series()],
    deltas: [deltaRow()],
    ...fields,
  };
}

const failure = (status: number, message: string) => ({
  body: { errors: [{ code: 'x', message, field: null }], meta: {} },
  opts: { status, statusText: 'Failed' },
});

describe('Compare Rounds (/rounds/compare)', () => {
  let http: HttpTestingController;
  let harness: RouterTestingHarness;
  let restoreCanvas: () => void;

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
  const listRequest = (): TestRequest => http.expectOne((r) => r.url === LIST_URL);
  const compareRequest = (): TestRequest => http.expectOne((r) => r.url === COMPARE_URL);
  const select = (name: 'round_a' | 'round_b'): HTMLSelectElement =>
    page().querySelector(`select[name="${name}"]`) as HTMLSelectElement;
  const options = (name: 'round_a' | 'round_b'): string[] =>
    [...select(name).options].map((o) => text(o));
  const alertBox = (): HTMLElement | null => page().querySelector('[role="alert"]');
  const table = (): HTMLTableElement | null => page().querySelector('table');

  /** Opens `url`, answers the Round list with `rounds`, and the comparison (if asked) with `body`. */
  async function open(
    url = '/rounds/compare?round_a=12&round_b=11',
    body: RoundComparison = comparison(),
    user: User = USER,
    rounds: GameRoundListItem[] = ROUNDS,
  ): Promise<void> {
    await setUp(user);
    await harness.navigateByUrl(url);
    listRequest().flush({
      data: rounds,
      meta: { page: 1, page_size: 100, count: rounds.length, total_pages: 1 },
    });
    const asked = http.match((r) => r.url === COMPARE_URL);
    for (const req of asked) req.flush({ data: body, meta: {} });
    await harness.fixture.whenStable();
  }

  /** Opens `url`, answers only the Round list, and checks no comparison was asked for. */
  async function openWithoutComparison(url: string): Promise<void> {
    await setUp();
    await harness.navigateByUrl(url);
    listRequest().flush({
      data: ROUNDS,
      meta: { page: 1, page_size: 100, count: ROUNDS.length, total_pages: 1 },
    });
    await harness.fixture.whenStable();
    http.expectNone((r) => r.url === COMPARE_URL);
  }

  /** Opens `url` and fails its comparison with `status` / `message`. */
  async function openFailing(status: number, message: string): Promise<void> {
    await setUp();
    await harness.navigateByUrl('/rounds/compare?round_a=12&round_b=11');
    listRequest().flush({
      data: ROUNDS,
      meta: { page: 1, page_size: 100, count: ROUNDS.length, total_pages: 1 },
    });
    const { body, opts } = failure(status, message);
    compareRequest().flush(body, opts);
    await harness.fixture.whenStable();
  }

  async function pick(a: string, b: string): Promise<void> {
    for (const [name, value] of [
      ['round_a', a],
      ['round_b', b],
    ] as const) {
      select(name).value = value;
      select(name).dispatchEvent(new Event('change'));
    }
    (page().querySelector('form') as HTMLFormElement).dispatchEvent(new Event('submit'));
    await harness.fixture.whenStable();
  }

  describe('picker', () => {
    it('routes to CompareRounds with the heading and the Django intro', async () => {
      await open('/rounds/compare');
      expect(harness.fixture.debugElement.query(By.directive(CompareRounds))).not.toBeNull();
      expect(text(page().querySelector('h1'))).toBe('Compare Rounds');
      expect(text(page())).toContain(
        'Pick two rounds that share at least one team to see a per-player stat delta and a ' +
          'points-over-time overlay.',
      );
      const button = page().querySelector('form button[type="submit"]');
      expect(text(button)).toBe('Compare');
    });

    it('lists every Round newest first through listAll (ordering=-id, page_size=100)', async () => {
      await setUp();
      await harness.navigateByUrl('/rounds/compare');
      const first = listRequest();
      expect(first.request.params.get('ordering')).toBe('-id');
      expect(first.request.params.get('page_size')).toBe('100');
      expect(first.request.params.get('page')).toBe('1');
      first.flush({
        data: ROUNDS.slice(0, 2),
        meta: { page: 1, page_size: 100, count: 3, total_pages: 2 },
      });
      const second = listRequest();
      expect(second.request.params.get('page')).toBe('2');
      expect(second.request.params.get('ordering')).toBe('-id');
      second.flush({
        data: ROUNDS.slice(2),
        meta: { page: 2, page_size: 100, count: 3, total_pages: 2 },
      });
      await harness.fixture.whenStable();
      const expected = [
        '-- Select a round --',
        '#12 - Alpha vs Bravo',
        '#11 - Bravo vs Alpha',
        '#3 - Comets vs Alpha',
      ];
      expect(options('round_a')).toEqual(expected);
      expect(options('round_b')).toEqual(expected);
    });

    it('keeps a one-sided pick selected and asks for no comparison', async () => {
      await openWithoutComparison('/rounds/compare?round_a=12');
      expect(select('round_a').value).toBe('12');
      expect(select('round_b').value).toBe('');
      expect(table()).toBeNull();
    });
  });

  describe('INV-1 picks in the URL', () => {
    it('builds the form from the URL and fetches the comparison when both ids are present', async () => {
      await setUp();
      await harness.navigateByUrl('/rounds/compare?round_a=12&round_b=11');
      listRequest().flush({
        data: ROUNDS,
        meta: { page: 1, page_size: 100, count: 3, total_pages: 1 },
      });
      const req = compareRequest();
      expect(req.request.params.get('round_a')).toBe('12');
      expect(req.request.params.get('round_b')).toBe('11');
      req.flush({ data: comparison(), meta: {} });
      await harness.fixture.whenStable();
      expect(select('round_a').value).toBe('12');
      expect(select('round_b').value).toBe('11');
    });

    it.each([
      ['a non-integer', 'round_a=abc&round_b=11', '', '11'],
      ['zero', 'round_a=0&round_b=11', '', '11'],
      ['a negative id', 'round_a=12&round_b=-3', '12', ''],
      ['a decimal', 'round_a=1.5&round_b=11', '', '11'],
    ])('drops %s and asks for nothing', async (_, query, a, b) => {
      await openWithoutComparison(`/rounds/compare?${query}`);
      expect(select('round_a').value).toBe(a);
      expect(select('round_b').value).toBe(b);
    });

    it('Compare navigates with the picks; the URL change loads the comparison', async () => {
      await open('/rounds/compare');
      await pick('12', '3');
      expect(routerUrl()).toBe('/rounds/compare?round_a=12&round_b=3');
      const req = compareRequest();
      expect(req.request.params.get('round_b')).toBe('3');
      req.flush({ data: comparison(), meta: {} });
      await harness.fixture.whenStable();
      expect(table()).not.toBeNull();
    });

    it('Compare with one pick puts only that pick in the URL and asks for nothing', async () => {
      await open('/rounds/compare');
      await pick('', '11');
      expect(routerUrl()).toBe('/rounds/compare?round_b=11');
      http.expectNone((r) => r.url === COMPARE_URL);
      expect(select('round_b').value).toBe('11');
    });
  });

  describe('INV-2 error box', () => {
    it.each([
      'Pick two different rounds to compare.',
      'These rounds share no team, so there is nothing to compare.',
    ])('shows the 400 "%s" above the picker, keeps the picks, shows no results', async (msg) => {
      await openFailing(400, msg);
      const box = alertBox();
      expect(text(box)).toBe(msg);
      const form = page().querySelector('form') as HTMLFormElement;
      expect(box?.compareDocumentPosition(form)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
      expect(select('round_a').value).toBe('12');
      expect(select('round_b').value).toBe('11');
      expect(table()).toBeNull();
      expect(harness.fixture.debugElement.query(By.directive(LineChart))).toBeNull();
    });

    it('clears the error when a new comparison loads', async () => {
      await openFailing(400, 'Pick two different rounds to compare.');
      await pick('12', '3');
      compareRequest().flush({ data: comparison(), meta: {} });
      await harness.fixture.whenStable();
      expect(alertBox()).toBeNull();
      expect(table()).not.toBeNull();
    });
  });

  describe('INV-3 not found', () => {
    it('shows "Round not found" on a 404 and no results', async () => {
      await openFailing(404, 'Not found.');
      expect(harness.fixture.debugElement.query(By.directive(RecordNotFound))).not.toBeNull();
      expect(text(page().querySelector('h1'))).toBe('Round not found');
      expect(table()).toBeNull();
      expect(text(page())).not.toContain('Not found.');
    });
  });

  describe('INV-4 load error', () => {
    it('shows the server message of any other comparison failure', async () => {
      await openFailing(500, 'Database down.');
      expect(text(alertBox())).toBe('Database down.');
      expect(table()).toBeNull();
      expect(harness.fixture.debugElement.query(By.directive(RecordNotFound))).toBeNull();
    });

    it('shows the server message when the Round list fails', async () => {
      await setUp();
      await harness.navigateByUrl('/rounds/compare');
      const { body, opts } = failure(500, 'List down.');
      listRequest().flush(body, opts);
      await harness.fixture.whenStable();
      expect(text(alertBox())).toBe('List down.');
    });
  });

  describe('Player Stat Deltas', () => {
    const headers = (): string[] => [...page().querySelectorAll('thead th')].map((th) => text(th));
    const rows = (): HTMLTableRowElement[] => [...page().querySelectorAll('tbody tr')] as never;
    const cells = (row: Element | undefined): HTMLTableCellElement[] =>
      [...(row?.querySelectorAll('td') ?? [])] as HTMLTableCellElement[];

    it('has the heading, the Django subtitle and one column per stat key', async () => {
      await open();
      const section = page().querySelector('[aria-label="Player Stat Deltas"]');
      expect(text(section?.querySelector('h2'))).toBe('Player Stat Deltas');
      expect(text(section)).toContain(
        'Each cell shows Round A → Round B with the change (B − A). Green = up, red = down.',
      );
      expect(headers()).toEqual([
        'Player',
        'Side A',
        'Role A',
        'Side B',
        'Role B',
        'points_scored',
        'tags_made',
      ]);
    });

    it('shows the Sides as R / B tags in Side colour and the Roles as short labels', async () => {
      await open();
      const [, sideA, roleA, sideB, roleB] = cells(rows()[0]);
      expect(text(sideA)).toBe('R');
      expect(sideA.querySelector('[data-lf-side="red"]')).not.toBeNull();
      expect(text(roleA)).toBe('CMD');
      expect(text(sideB)).toBe('B');
      expect(sideB.querySelector('[data-lf-side="blue"]')).not.toBeNull();
      expect(text(roleB)).toBe('HVY');
    });

    it('shows "—" for the Side and Role of a Round the Player missed', async () => {
      await open(
        undefined,
        comparison({
          deltas: [
            deltaRow({ side_b: null, role_b: null, cells: [cell(100, null), cell(2, null)] }),
          ],
        }),
      );
      const [, , , sideB, roleB] = cells(rows()[0]);
      expect(text(sideB)).toBe('—');
      expect(sideB.querySelector('[data-lf-side]')).toBeNull();
      expect(text(roleB)).toBe('—');
    });

    it('shows each cell as "a → b" with the signed change underneath', async () => {
      await open();
      const stat = cells(rows()[0]).slice(5);
      expect(stat.map((td) => text(td))).toEqual(['100 → 140 +40', '2 → 1 -1']);
      expect(text(stat[0].querySelector('.delta__change'))).toBe('+40');
    });

    it('shows "–" for a missing side with no change line, and "—" when both are missing', async () => {
      await open(
        undefined,
        comparison({ deltas: [deltaRow({ cells: [cell(null, 5), cell(null, null)] })] }),
      );
      const stat = cells(rows()[0]).slice(5);
      expect(stat.map((td) => text(td))).toEqual(['– → 5', '—']);
      expect(stat[0].querySelector('.delta__change')).toBeNull();
    });

    it('shows a zero change as "0"', async () => {
      await open(
        undefined,
        comparison({ deltas: [deltaRow({ cells: [cell(5, 5), cell(1, 1)] })] }),
      );
      expect(text(cells(rows()[0])[5])).toBe('5 → 5 0');
    });

    it('rounds a fractional stat (MVP) to two decimals', async () => {
      await open(
        undefined,
        comparison({
          deltas: [
            deltaRow({ cells: [{ a: 1.1, b: 1.4, delta: 0.30000000000000004 }, cell(1, 1)] }),
          ],
        }),
      );
      expect(text(cells(rows()[0])[5])).toBe('1.10 → 1.40 +0.30');
    });

    it('marks a cell up, down or muted by its change, and leaves an unchanged one plain', async () => {
      await open(
        undefined,
        comparison({
          stat_keys: ['a', 'b', 'c', 'd'],
          deltas: [deltaRow({ cells: [cell(1, 3), cell(3, 1), cell(null, 2), cell(2, 2)] })],
        }),
      );
      const stat = cells(rows()[0]).slice(5);
      const marks = stat.map((td) =>
        ['delta--up', 'delta--down', 'delta--muted'].filter((c) => td.classList.contains(c)),
      );
      expect(marks).toEqual([['delta--up'], ['delta--down'], ['delta--muted'], []]);
    });

    it('sorts the rows by name', async () => {
      await open(
        undefined,
        comparison({
          deltas: [
            deltaRow({ player_id: 1, name: 'Zed' }),
            deltaRow({ player_id: 2, name: 'Ace' }),
            deltaRow({ player_id: 3, name: 'Max' }),
          ],
        }),
      );
      expect(rows().map((r) => text(cells(r)[0]))).toEqual(['Ace', 'Max', 'Zed']);
    });

    it('sorts the rows by name ignoring case', async () => {
      await open(
        undefined,
        comparison({
          deltas: [
            deltaRow({ player_id: 1, name: 'Zed' }),
            deltaRow({ player_id: 2, name: 'alice' }),
          ],
        }),
      );
      expect(rows().map((r) => text(cells(r)[0]))).toEqual(['alice', 'Zed']);
    });

    it('marks your own Self-insert with the "You" badge, and no one else (INV-6)', async () => {
      const self = { ...SELF_INSERT, id: 2 };
      await open(
        undefined,
        comparison({
          deltas: [
            deltaRow({ player_id: 1, name: 'Ace' }),
            deltaRow({ player_id: 2, name: 'Dan' }),
          ],
        }),
        { ...USER, self_insert: self },
      );
      const [ace, dan] = rows().map((r) => cells(r)[0]);
      expect(ace.querySelector('.badge--you')).toBeNull();
      const badge = dan.querySelector('.badge--you');
      expect(text(badge)).toBe('You');
      expect(badge?.classList.contains('badge')).toBe(true);
      expect(text(dan)).toBe('Dan You');
    });

    it('scrolls the wide table sideways inside its own container', async () => {
      await open();
      expect(getComputedStyle(table()?.parentElement as HTMLElement).overflowX).toBe('auto');
    });
  });

  describe('Points Over Time', () => {
    const chart = (): LineChart =>
      harness.fixture.debugElement.query(By.directive(LineChart)).componentInstance as LineChart;

    it('has the heading and the Django note', async () => {
      await open();
      const section = page().querySelector('[aria-label="Points Over Time"]');
      expect(text(section?.querySelector('h2'))).toBe('Points Over Time');
      expect(text(section)).toContain(
        'Solid = Round A, dashed = Round B. One color pair per shared team.',
      );
    });

    it('draws "{team} (A)" solid and "{team} (B)" dashed per shared Team in the chart tokens', async () => {
      const root = document.documentElement.style;
      root.setProperty('--lf-chart-1', 'rgb(1, 1, 1)');
      root.setProperty('--lf-chart-2', 'rgb(2, 2, 2)');
      root.setProperty('--lf-font-body', 'Test Mono');
      try {
        await open(
          undefined,
          comparison({
            points_series: [
              series(),
              series({
                team_id: 2,
                team_name: 'Bravo',
                a: [[10, 100]],
                b: [[1800, 900]],
              }),
            ],
          }),
        );
      } finally {
        root.removeProperty('--lf-chart-1');
        root.removeProperty('--lf-chart-2');
        root.removeProperty('--lf-font-body');
      }
      expect(chart().xAxis()).toBe('clock');
      expect(chart().yTitle()).toBe('Cumulative points');
      expect(chart().theme().font).toBe('Test Mono');
      // Ticks are half-seconds: the last tick, 1800, is 900 s.
      expect(chart().maxSec()).toBe(900);
      expect(chart().datasets()).toEqual([
        {
          label: 'Alpha (A)',
          color: 'rgb(1, 1, 1)',
          points: [
            { sec: 0, value: 0 },
            { sec: 60, value: 300 },
          ],
        },
        {
          label: 'Alpha (B)',
          color: 'rgb(1, 1, 1)',
          dashed: true,
          points: [
            { sec: 0, value: 0 },
            { sec: 120, value: 500 },
          ],
        },
        { label: 'Bravo (A)', color: 'rgb(2, 2, 2)', points: [{ sec: 5, value: 100 }] },
        {
          label: 'Bravo (B)',
          color: 'rgb(2, 2, 2)',
          dashed: true,
          points: [{ sec: 900, value: 900 }],
        },
      ]);
    });
  });
});
