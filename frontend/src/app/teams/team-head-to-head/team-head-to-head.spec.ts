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
import { TeamHeadToHead as TeamH2HData } from '../../api/analytics.models';
import { TeamListItem } from '../../api/teams.models';
import { USER, signIn } from '../../api/testing/auth-fixtures';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';
import { User } from '../../auth/user';
import { stubCanvasContext } from '../../matches/testing/fake-canvas';
import { LineChart } from '../../shared/line-chart';
import { TeamHeadToHead } from './team-head-to-head';

const BASE = environment.apiBaseUrl;
const TEAMS_URL = `${BASE}/teams/`;
const H2H_URL = `${BASE}/teams/head-to-head/`;

function team(id: number, name: string): TeamListItem {
  return {
    id,
    name,
    players: [],
  } as unknown as TeamListItem;
}

const TEAMS = [team(1, 'Comets'), team(2, 'Rockets')];

function h2h(fields: Partial<TeamH2HData> = {}): TeamH2HData {
  return {
    team_a: { id: 1, name: 'Comets' },
    team_b: { id: 2, name: 'Rockets' },
    provenance: 'all',
    date_from: null,
    date_to: null,
    match_record: { wins: 1, losses: 0, ties: 0, n: 1 },
    round_record: { wins: 2, losses: 1, ties: 0, n: 3 },
    score_margin: { mean_margin: 1234.56, n: 3 },
    avg_survivors: { team_a_avg: 2.5, team_b_avg: 1.333, n: 3 },
    top_impactful: {
      team_a: { player_id: 9, name: 'Dan', mvp_total: 12.34, games: 3 },
      team_b: { player_id: 11, name: 'Eve', mvp_total: 5, games: 1 },
    },
    per_map_breakdown: [
      {
        arena_map_id: 4,
        arena_map_name: 'Arena',
        games: 2,
        wins: 1,
        losses: 1,
        ties: 0,
        mean_margin: 250.25,
      },
      {
        arena_map_id: null,
        arena_map_name: 'No map (3-zone)',
        games: 1,
        wins: 1,
        losses: 0,
        ties: 0,
        mean_margin: -3,
      },
    ],
    detail_list: [
      {
        kind: 'match',
        date_played: '2026-10-02T14:05:00',
        label: 'Match',
        team_a_score: 9000,
        team_b_score: 8000,
        score_str: '9000 - 8000',
        winner_name: 'Comets',
        detail_url: '/matches/match/77/',
        match_id: 77,
        round_id: null,
        is_simulated: true,
      },
      {
        kind: 'round',
        date_played: '2026-10-01T09:30:00',
        label: 'Single Round',
        team_a_score: 4000,
        team_b_score: 4000,
        score_str: '4000 - 4000',
        winner_name: '—',
        detail_url: '/matches/round/55/',
        match_id: null,
        round_id: 55,
        is_simulated: true,
      },
    ],
    margin_series: [
      [1, 100],
      [2, -50],
      [3, 200],
    ],
    cumulative_wl_series: [
      [1, 1],
      [2, 0],
      [3, 1],
    ],
    ...fields,
  };
}

const NOT_FOUND = {
  body: { errors: [{ code: 'not_found', message: 'Not found.', field: null }], meta: {} },
  init: { status: 404, statusText: 'Not Found' },
};

describe('Team head-to-head (/teams/head-to-head)', () => {
  let http: HttpTestingController;
  let harness: RouterTestingHarness;
  let restoreCanvas: () => void;

  beforeEach(() => {
    restoreCanvas = stubCanvasContext();
  });

  afterEach(() => {
    http.verify({ ignoreCancelled: true });
    restoreCanvas();
  });

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

  const page = (): HTMLElement => harness.routeNativeElement as HTMLElement;
  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const pageText = (): string => text(page());
  const routerUrl = (): string => TestBed.inject(Router).url;
  const teamsRequest = (): TestRequest => http.expectOne((r) => r.url === TEAMS_URL);
  const h2hRequests = (): TestRequest[] => http.match((r) => r.url === H2H_URL);
  const select = (name: string): HTMLSelectElement =>
    page().querySelector(`select[name="${name}"]`) as HTMLSelectElement;
  const input = (name: string): HTMLInputElement =>
    page().querySelector(`input[name="${name}"]`) as HTMLInputElement;
  const options = (name: string): string[] => [...select(name).options].map((o) => text(o));
  const chart = (title: string): LineChart | undefined =>
    harness.fixture.debugElement
      .queryAll(By.directive(LineChart))
      .map((d) => d.componentInstance as LineChart)
      .find((c) => c.title() === title);
  const card = (label: string): string => {
    const found = [...page().querySelectorAll('.h2h-card')].find((c) =>
      text(c.querySelector('.h2h-card__label')).startsWith(label),
    );
    // Label, value and note are stacked blocks: read them as separate words.
    return ['label', 'value', 'note']
      .map((part) => text(found?.querySelector(`.h2h-card__${part}`)))
      .filter((part) => part !== '')
      .join(' ');
  };
  const tableRows = (selector: string): string[][] =>
    [...page().querySelectorAll(`${selector} tbody tr`)].map((tr) =>
      [...tr.querySelectorAll('td')].map((td) => text(td)),
    );
  const tableHead = (selector: string): string[] =>
    [...page().querySelectorAll(`${selector} th`)].map((th) => text(th));

  /** Opens `url`, answers the Teams picker with `teams`, and returns. */
  async function openPicker(
    url = '/teams/head-to-head',
    teams = TEAMS,
    user = USER,
  ): Promise<void> {
    await setUp(user);
    await harness.navigateByUrl(url);
    teamsRequest().flush({
      data: teams,
      meta: { page: 1, page_size: 100, count: teams.length, total_pages: 1 },
    });
    await harness.fixture.whenStable();
  }

  /** Opens a comparison of Teams 1 and 2, answering the H2H GET with `body`. */
  async function openResults(
    body: TeamH2HData = h2h(),
    url = '/teams/head-to-head?team_a=1&team_b=2',
    user = USER,
  ): Promise<TestRequest> {
    await openPicker(url, TEAMS, user);
    const [req] = h2hRequests();
    req.flush({ data: body, meta: {} });
    await harness.fixture.whenStable();
    return req;
  }

  describe('the picker', () => {
    it('routes to TeamHeadToHead with the heading and the Django intro text', async () => {
      await openPicker();
      expect(harness.fixture.debugElement.query(By.directive(TeamHeadToHead))).not.toBeNull();
      expect(text(page().querySelector('h1'))).toBe('Head-to-Head Record');
      expect(pageText()).toContain(
        'Pick two teams to see their full head-to-head history — record, score margin, ' +
          'survivor averages, most impactful players, per-map breakdown, and the chronological ' +
          'match/round list.',
      );
    });

    it('fills Team A and Team B from every page of the Team list, by name', async () => {
      await setUp();
      await harness.navigateByUrl('/teams/head-to-head');
      const first = teamsRequest();
      expect(first.request.params.get('ordering')).toBe('name');
      expect(first.request.params.get('page_size')).toBe('100');
      first.flush({
        data: [team(1, 'Comets')],
        meta: { page: 1, page_size: 100, count: 2, total_pages: 2 },
      });
      const second = teamsRequest();
      expect(second.request.params.get('page')).toBe('2');
      second.flush({
        data: [team(2, 'Rockets')],
        meta: { page: 2, page_size: 100, count: 2, total_pages: 2 },
      });
      await harness.fixture.whenStable();
      for (const name of ['team_a', 'team_b']) {
        expect(options(name)).toEqual(['-- Select a team --', 'Comets', 'Rockets']);
      }
    });

    it('has Provenance (All / Real only / Simulated only), From / To dates and Compare', async () => {
      await openPicker();
      expect(options('provenance')).toEqual(['All', 'Real only', 'Simulated only']);
      expect(input('from').type).toBe('date');
      expect(input('to').type).toBe('date');
      const labels = [...page().querySelectorAll('form label, form mat-label')].map((l) => text(l));
      expect(labels).toEqual(
        expect.arrayContaining(['Team A', 'Team B', 'Provenance', 'From', 'To']),
      );
      expect(text(page().querySelector('form button[type="submit"]'))).toBe('Compare');
    });
  });

  describe('INV-1 picks in the URL', () => {
    async function submit(): Promise<void> {
      (page().querySelector('form') as HTMLFormElement).dispatchEvent(new Event('submit'));
      await harness.fixture.whenStable();
    }

    function choose(name: string, value: string): void {
      const el = page().querySelector(`[name="${name}"]`) as HTMLInputElement | HTMLSelectElement;
      el.value = value;
      el.dispatchEvent(new Event(el instanceof HTMLSelectElement ? 'change' : 'input'));
    }

    it('fills the form from the URL and asks for the record with those params', async () => {
      const req = await openResults(
        h2h(),
        '/teams/head-to-head?team_a=1&team_b=2&provenance=real&from=2026-01-01&to=2026-12-31',
      );
      expect(req.request.method).toBe('GET');
      expect(req.request.params.get('team_a')).toBe('1');
      expect(req.request.params.get('team_b')).toBe('2');
      expect(req.request.params.get('provenance')).toBe('real');
      expect(req.request.params.get('from')).toBe('2026-01-01');
      expect(req.request.params.get('to')).toBe('2026-12-31');
      expect(select('team_a').value).toBe('1');
      expect(select('team_b').value).toBe('2');
      expect(select('provenance').value).toBe('real');
      expect(input('from').value).toBe('2026-01-01');
      expect(input('to').value).toBe('2026-12-31');
    });

    it('sends only the two ids when the URL has no filters', async () => {
      const req = await openResults();
      expect(req.request.params.keys().sort()).toEqual(['team_a', 'team_b']);
      expect(select('provenance').value).toBe('all');
    });

    it('drops an unknown provenance and malformed dates, so the server defaults apply', async () => {
      const req = await openResults(
        h2h(),
        '/teams/head-to-head?team_a=1&team_b=2&provenance=fake&from=2026-02-30&to=yesterday',
      );
      expect(req.request.params.keys().sort()).toEqual(['team_a', 'team_b']);
      expect(input('from').value).toBe('');
      expect(input('to').value).toBe('');
    });

    it.each([['abc'], ['0'], ['-3'], ['1.5']])(
      'drops a team id of "%s" and does not fetch',
      async (bad) => {
        await openPicker(`/teams/head-to-head?team_a=${bad}&team_b=2`);
        expect(h2hRequests()).toEqual([]);
        expect(select('team_a').value).toBe('');
        expect(select('team_b').value).toBe('2');
      },
    );

    it('does not fetch with only one team picked (a preselected Team A)', async () => {
      await openPicker('/teams/head-to-head?team_a=1');
      expect(h2hRequests()).toEqual([]);
      expect(select('team_a').value).toBe('1');
      expect(page().querySelector('h2')).toBeNull();
    });

    it('Compare writes the picks to the URL, which loads the record', async () => {
      await openPicker();
      choose('team_a', '1');
      choose('team_b', '2');
      choose('provenance', 'sim');
      choose('from', '2026-03-01');
      await submit();
      expect(routerUrl()).toBe(
        '/teams/head-to-head?team_a=1&team_b=2&provenance=sim&from=2026-03-01',
      );
      const [req] = h2hRequests();
      expect(req.request.params.get('provenance')).toBe('sim');
      req.flush({ data: h2h(), meta: {} });
      await harness.fixture.whenStable();
      expect(text(page().querySelector('h2'))).toBe('Comets vs Rockets');
    });

    it('Compare itself never calls the API: an unchanged URL sends nothing new', async () => {
      await openResults();
      await submit();
      expect(h2hRequests()).toEqual([]);
    });
  });

  describe('results: heading, notice and cards', () => {
    const EMPTY_RECORD = { wins: 0, losses: 0, ties: 0, n: 0 };

    it('heads the results "{A} vs {B}"', async () => {
      await openResults();
      expect(text(page().querySelector('h2'))).toBe('Comets vs Rockets');
    });

    it('says no games were found when both records are empty', async () => {
      await openResults(h2h({ match_record: EMPTY_RECORD, round_record: EMPTY_RECORD }));
      expect(pageText()).toContain(
        'No head-to-head games found between these two teams for the selected filters.',
      );
    });

    it('does not say it when only Rounds were played', async () => {
      await openResults(h2h({ match_record: EMPTY_RECORD }));
      expect(pageText()).not.toContain('No head-to-head games found');
    });

    it('shows the Match and Round records with their counts', async () => {
      await openResults();
      expect(card('Match record (W-L-T)')).toBe('Match record (W-L-T) 1-0-0 1 match');
      expect(card('Round record (W-L-T)')).toBe('Round record (W-L-T) 2-1-0 3 rounds');
    });

    it('pluralises the counts: "2 matches", "1 round"', async () => {
      await openResults(
        h2h({
          match_record: { wins: 1, losses: 1, ties: 0, n: 2 },
          round_record: { wins: 1, losses: 0, ties: 0, n: 1 },
          score_margin: { mean_margin: 0, n: 1 },
        }),
      );
      expect(card('Match record')).toContain('2 matches');
      expect(card('Round record')).toContain('1 round');
      expect(card('Round record')).not.toContain('1 rounds');
      expect(card('Avg score margin')).toBe('Avg score margin (A − B) 0.0 over 1 round');
    });

    it('shows the score margin to 1 decimal over N rounds', async () => {
      await openResults();
      expect(card('Avg score margin (A − B)')).toBe(
        'Avg score margin (A − B) 1234.6 over 3 rounds',
      );
    });

    it("shows each Team's average survivors to 2 decimals", async () => {
      await openResults();
      expect(card('Avg survivors — Comets')).toBe('Avg survivors — Comets 2.50');
      expect(card('Avg survivors — Rockets')).toBe('Avg survivors — Rockets 1.33');
    });

    it('shows each Team\'s most impactful Player as "MVP x over N round(s)"', async () => {
      await openResults();
      expect(card('Top impactful — Comets')).toBe(
        'Top impactful — Comets Dan MVP 12.3 over 3 rounds',
      );
      expect(card('Top impactful — Rockets')).toBe(
        'Top impactful — Rockets Eve MVP 5.0 over 1 round',
      );
    });

    it('shows "—" for a Team with no impactful Player', async () => {
      await openResults(h2h({ top_impactful: { team_a: null, team_b: null } }));
      expect(card('Top impactful — Comets')).toBe('Top impactful — Comets —');
      expect(card('Top impactful — Rockets')).toBe('Top impactful — Rockets —');
    });

    it('INV-6: marks your Self-insert with the You badge', async () => {
      const self = { id: 9, name: 'Dan' } as unknown as NonNullable<User['self_insert']>;
      await openResults(h2h(), undefined, { ...USER, self_insert: self });
      const badges = [...page().querySelectorAll('.badge--you')];
      expect(badges.length).toBe(1);
      expect(badges[0].classList.contains('badge')).toBe(true);
      expect(card('Top impactful — Comets')).toBe(
        'Top impactful — Comets Dan You MVP 12.3 over 3 rounds',
      );
    });

    it('INV-6: shows no badge without a Self-insert in the results', async () => {
      await openResults();
      expect(page().querySelector('.badge--you')).toBeNull();
    });
  });

  describe('results: per-map breakdown', () => {
    it('lists Map, Games, W, L, T and the mean margin to 1 decimal', async () => {
      await openResults();
      expect(pageText()).toContain('Per-map breakdown');
      expect(tableHead('.h2h-map-table')).toEqual(['Map', 'Games', 'W', 'L', 'T', 'Mean margin']);
      expect(tableRows('.h2h-map-table')).toEqual([
        ['Arena', '2', '1', '1', '0', '250.3'],
        ['No map (3-zone)', '1', '1', '0', '0', '-3.0'],
      ]);
    });

    it('says "No per-map data." when there is none', async () => {
      await openResults(h2h({ per_map_breakdown: [] }));
      expect(page().querySelector('.h2h-map-table')).toBeNull();
      expect(pageText()).toContain('No per-map data.');
    });
  });

  describe('results: all H2H games', () => {
    it('lists Date, Type, Score, Winner and a View link per game', async () => {
      await openResults();
      expect(pageText()).toContain('All H2H games');
      expect(tableHead('.h2h-games')).toEqual(['Date', 'Type', 'Score (A − B)', 'Winner', '']);
      expect(tableRows('.h2h-games')).toEqual([
        ['2026-10-02 14:05', 'Match', '9000 - 8000', 'Comets', 'View'],
        ['2026-10-01 09:30', 'Single Round', '4000 - 4000', '—', 'View'],
      ]);
    });

    it('INV-5: View opens the Angular Match or Round page by id, never detail_url', async () => {
      await openResults();
      const hrefs = [...page().querySelectorAll('.h2h-games tbody a')].map((a) =>
        a.getAttribute('href'),
      );
      expect(hrefs).toEqual(['/matches/77', '/rounds/55']);
    });

    it('says "No games to list." when there are none', async () => {
      await openResults(h2h({ detail_list: [] }));
      expect(page().querySelector('.h2h-games')).toBeNull();
      expect(pageText()).toContain('No games to list.');
    });
  });

  describe('results: charts', () => {
    it('plots the margin per round with a dashed zero line on a count axis', async () => {
      document.documentElement.style.setProperty('--lf-accent', 'rgb(1, 2, 3)');
      document.documentElement.style.setProperty('--lf-text-muted', 'rgb(4, 5, 6)');
      try {
        await openResults();
      } finally {
        document.documentElement.style.removeProperty('--lf-accent');
        document.documentElement.style.removeProperty('--lf-text-muted');
      }
      expect(pageText()).toContain('Margin per round (A − B)');
      const margin = chart('Margin per round (A − B)') as LineChart;
      expect(margin.xAxis()).toBe('count');
      expect(margin.xTitle()).toBe('Round #');
      expect(margin.yTitle()).toBe('Margin');
      expect(margin.maxSec()).toBe(3);
      expect(margin.datasets()).toEqual([
        {
          label: 'Margin (A − B)',
          color: 'rgb(1, 2, 3)',
          pointRadius: 3,
          points: [
            { sec: 1, value: 100 },
            { sec: 2, value: -50 },
            { sec: 3, value: 200 },
          ],
        },
        {
          label: 'Zero',
          color: 'rgb(4, 5, 6)',
          dashed: true,
          points: [
            { sec: 1, value: 0 },
            { sec: 3, value: 0 },
          ],
        },
      ]);
    });

    it('plots the cumulative round W − L', async () => {
      await openResults();
      expect(pageText()).toContain('Cumulative round W − L (A − B)');
      const cumulative = chart('Cumulative round W − L (A − B)') as LineChart;
      expect(cumulative.xAxis()).toBe('count');
      expect(cumulative.xTitle()).toBe('Round #');
      expect(cumulative.yTitle()).toBe('Cumulative W − L');
      expect(cumulative.datasets().map((d) => d.label)).toEqual(['Cumulative (A wins − B wins)']);
      expect(cumulative.datasets()[0].points).toEqual([
        { sec: 1, value: 1 },
        { sec: 2, value: 0 },
        { sec: 3, value: 1 },
      ]);
    });

    it('draws no chart when the series are empty', async () => {
      await openResults(h2h({ margin_series: [], cumulative_wl_series: [] }));
      expect(harness.fixture.debugElement.query(By.directive(LineChart))).toBeNull();
      expect(pageText()).not.toContain('Margin per round');
    });
  });

  describe('layout', () => {
    it('scrolls each wide table inside its own container', async () => {
      await openResults();
      const tables = [...page().querySelectorAll('table')];
      expect(tables.length).toBe(2);
      for (const table of tables) {
        expect(getComputedStyle(table.parentElement as HTMLElement).overflowX).toBe('auto');
      }
    });
  });

  describe('INV-2..4 failures', () => {
    async function failWith(status: number, message: string, code = 'invalid'): Promise<void> {
      await openPicker('/teams/head-to-head?team_a=1&team_b=1');
      const [req] = h2hRequests();
      req.flush(
        { errors: [{ code, message, field: null }], meta: {} },
        { status, statusText: 'Error' },
      );
      await harness.fixture.whenStable();
    }

    it('INV-2: a 400 shows its message in the error box above the picker, picks kept', async () => {
      await failWith(400, 'Pick two different teams to compare.');
      const box = page().querySelector('[role="alert"]');
      expect(text(box)).toBe('Pick two different teams to compare.');
      const form = page().querySelector('form') as HTMLFormElement;
      expect(box?.compareDocumentPosition(form)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
      expect(select('team_a').value).toBe('1');
      expect(select('team_b').value).toBe('1');
      expect(page().querySelector('h2')).toBeNull();
    });

    it('INV-2: a later comparison clears the error', async () => {
      await failWith(400, 'Pick two different teams to compare.');
      await harness.navigateByUrl('/teams/head-to-head?team_a=1&team_b=2');
      h2hRequests()[0].flush({ data: h2h(), meta: {} });
      await harness.fixture.whenStable();
      expect(page().querySelector('[role="alert"]')).toBeNull();
      expect(text(page().querySelector('h2'))).toBe('Comets vs Rockets');
    });

    it('INV-3: a 404 shows "Team not found" and no results', async () => {
      await openPicker('/teams/head-to-head?team_a=1&team_b=99');
      h2hRequests()[0].flush(NOT_FOUND.body, NOT_FOUND.init);
      await harness.fixture.whenStable();
      expect(text(page().querySelector('h1'))).toBe('Team not found');
      expect(page().querySelector('h2')).toBeNull();
      expect(pageText()).not.toContain('Not found.');
    });

    it('INV-4: any other analytics failure shows the server message', async () => {
      await failWith(500, 'Database down.', 'server_error');
      expect(text(page().querySelector('[role="alert"]'))).toBe('Database down.');
      expect(page().querySelector('h2')).toBeNull();
    });

    it('INV-4: a failing Team list shows the server message', async () => {
      await setUp();
      await harness.navigateByUrl('/teams/head-to-head');
      teamsRequest().flush(
        { errors: [{ code: 'server_error', message: 'Teams down.', field: null }], meta: {} },
        { status: 500, statusText: 'Server Error' },
      );
      await harness.fixture.whenStable();
      expect(text(page().querySelector('[role="alert"]'))).toBe('Teams down.');
    });
  });
});
