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
import { PlayerHeadToHead as PlayerH2HData } from '../../api/analytics.models';
import { Player } from '../../api/players.models';
import { SELF_INSERT, USER, signIn } from '../../api/testing/auth-fixtures';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';
import { User } from '../../auth/user';
import { stubCanvasContext } from '../../matches/testing/fake-canvas';
import { LineChart } from '../../shared/line-chart';
import { PlayerHeadToHead } from './player-head-to-head';

const BASE = environment.apiBaseUrl;
const PLAYERS_URL = `${BASE}/players/`;
const H2H_URL = `${BASE}/players/head-to-head/`;

function player(id: number, name: string, teamName: string): Player {
  return { ...SELF_INSERT, id, name, team_name: teamName };
}

const PLAYERS = [player(7, 'Ace', 'Comets'), player(8, 'Bo', 'Rockets'), player(9, 'Cy', '')];

function h2h(fields: Partial<PlayerH2HData> = {}): PlayerH2HData {
  return {
    player_a: { id: 7, name: 'Ace', team: { id: 1, name: 'Comets' } },
    player_b: { id: 8, name: 'Bo', team: { id: 2, name: 'Rockets' } },
    role: null,
    provenance: 'all',
    date_from: null,
    date_to: null,
    round_record: { wins: 2, losses: 1, ties: 0, n: 3 },
    score_margin: { mean_margin: -12.34, n: 3 },
    tag_stats: {
      avg_tags_a_to_b: 1.666,
      avg_tags_b_to_a: 0.5,
      total_tags_a_to_b: 5,
      total_tags_b_to_a: 2,
      n: 3,
    },
    per_role_breakdown: [
      {
        role: 'commander',
        games: 2,
        wins: 1,
        losses: 1,
        ties: 0,
        mean_margin: 10.25,
        avg_tags_a_to_b: 1.5,
        avg_tags_b_to_a: 0.333,
      },
    ],
    per_map_breakdown: [
      {
        arena_map_id: null,
        arena_map_name: 'No map (3-zone)',
        games: 3,
        wins: 2,
        losses: 1,
        ties: 0,
        mean_margin: -12.34,
      },
    ],
    detail_list: [
      {
        round_id: 55,
        date_played: '2026-10-02T14:05:00',
        role_a: 'commander',
        role_b: 'heavy',
        player_a_team_score: 9000,
        player_b_team_score: 8000,
        score_str: '9000 - 8000',
        tags_a_to_b: 3,
        tags_b_to_a: 1,
        winner_label: 'Player A',
        detail_url: '/matches/round/55/',
        is_simulated: true,
      },
    ],
    margin_series: [
      [1, 1000],
      [2, -200],
    ],
    cumulative_wl_series: [
      [1, 1],
      [2, 0],
    ],
    ...fields,
  };
}

describe('Player head-to-head (/players/head-to-head)', () => {
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
  const playersRequest = (): TestRequest => http.expectOne((r) => r.url === PLAYERS_URL);
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

  /** Opens `url` and answers the Players picker. */
  async function openPicker(url = '/players/head-to-head', user = USER): Promise<void> {
    await setUp(user);
    await harness.navigateByUrl(url);
    playersRequest().flush({
      data: PLAYERS,
      meta: { page: 1, page_size: 100, count: PLAYERS.length, total_pages: 1 },
    });
    await harness.fixture.whenStable();
  }

  /** Opens a comparison of Players 7 and 8, answering the H2H GET with `body`. */
  async function openResults(
    body: PlayerH2HData = h2h(),
    url = '/players/head-to-head?player_a=7&player_b=8',
    user = USER,
  ): Promise<TestRequest> {
    await openPicker(url, user);
    const [req] = h2hRequests();
    req.flush({ data: body, meta: {} });
    await harness.fixture.whenStable();
    return req;
  }

  async function submit(): Promise<void> {
    (page().querySelector('form') as HTMLFormElement).dispatchEvent(new Event('submit'));
    await harness.fixture.whenStable();
  }

  function choose(name: string, value: string): void {
    const el = page().querySelector(`[name="${name}"]`) as HTMLInputElement | HTMLSelectElement;
    el.value = value;
    el.dispatchEvent(new Event(el instanceof HTMLSelectElement ? 'change' : 'input'));
  }

  describe('the picker', () => {
    it('routes to PlayerHeadToHead with the heading and the Django intro text', async () => {
      await openPicker();
      expect(harness.fixture.debugElement.query(By.directive(PlayerHeadToHead))).not.toBeNull();
      expect(text(page().querySelector('h1'))).toBe('Player Head-to-Head Record');
      expect(pageText()).toContain(
        'Pick two players to see their full head-to-head history — round record, score ' +
          'margin, direct-tag exchange, per-role and per-map breakdowns, and the chronological ' +
          'round list.',
      );
    });

    it('fills Player A and B from every page of the Player list as "name (team)"', async () => {
      await setUp();
      await harness.navigateByUrl('/players/head-to-head');
      const first = playersRequest();
      expect(first.request.params.get('page_size')).toBe('100');
      first.flush({
        data: PLAYERS.slice(0, 2),
        meta: { page: 1, page_size: 100, count: 3, total_pages: 2 },
      });
      const second = playersRequest();
      expect(second.request.params.get('page')).toBe('2');
      second.flush({
        data: PLAYERS.slice(2),
        meta: { page: 2, page_size: 100, count: 3, total_pages: 2 },
      });
      await harness.fixture.whenStable();
      for (const name of ['player_a', 'player_b']) {
        expect(options(name)).toEqual([
          '-- Select a player --',
          'Ace (Comets)',
          'Bo (Rockets)',
          'Cy',
        ]);
      }
    });

    it('has Role (both), Provenance, From / To and Compare', async () => {
      await openPicker();
      expect(options('role')).toEqual(['Any', 'Commander', 'Heavy', 'Scout', 'Medic', 'Ammo']);
      expect([...select('role').options].map((o) => o.value)).toEqual([
        '',
        'commander',
        'heavy',
        'scout',
        'medic',
        'ammo',
      ]);
      expect(options('provenance')).toEqual(['All', 'Real only', 'Simulated only']);
      expect(input('from').type).toBe('date');
      expect(input('to').type).toBe('date');
      const labels = [...page().querySelectorAll('form mat-label')].map((l) => text(l));
      expect(labels).toEqual(['Player A', 'Player B', 'Role (both)', 'Provenance', 'From', 'To']);
      expect(text(page().querySelector('form button[type="submit"]'))).toBe('Compare');
    });
  });

  describe('INV-1 picks in the URL', () => {
    it('fills the form from the URL and asks for the record with those params', async () => {
      const req = await openResults(
        h2h(),
        '/players/head-to-head?player_a=7&player_b=8&role=scout&provenance=sim' +
          '&from=2026-01-01&to=2026-12-31',
      );
      expect(req.request.params.get('player_a')).toBe('7');
      expect(req.request.params.get('player_b')).toBe('8');
      expect(req.request.params.get('role')).toBe('scout');
      expect(req.request.params.get('provenance')).toBe('sim');
      expect(req.request.params.get('from')).toBe('2026-01-01');
      expect(req.request.params.get('to')).toBe('2026-12-31');
      expect(select('player_a').value).toBe('7');
      expect(select('player_b').value).toBe('8');
      expect(select('role').value).toBe('scout');
      expect(select('provenance').value).toBe('sim');
      expect(input('from').value).toBe('2026-01-01');
    });

    it('drops an unknown role or provenance and a malformed date', async () => {
      const req = await openResults(
        h2h(),
        '/players/head-to-head?player_a=7&player_b=8&role=pilot&provenance=x&from=2026-13-01',
      );
      expect(req.request.params.keys().sort()).toEqual(['player_a', 'player_b']);
      expect(select('role').value).toBe('');
      expect(select('provenance').value).toBe('all');
    });

    it.each([['x'], ['0'], ['-1']])('drops a player id of "%s" and does not fetch', async (bad) => {
      await openPicker(`/players/head-to-head?player_a=7&player_b=${bad}`);
      expect(h2hRequests()).toEqual([]);
      expect(select('player_b').value).toBe('');
    });

    it('preselects Player A alone without fetching (the career link)', async () => {
      await openPicker('/players/head-to-head?player_a=7');
      expect(h2hRequests()).toEqual([]);
      expect(select('player_a').value).toBe('7');
    });

    it('Compare writes the picks to the URL, which loads the record', async () => {
      await openPicker();
      choose('player_a', '7');
      choose('player_b', '8');
      choose('role', 'medic');
      choose('to', '2026-05-01');
      await submit();
      expect(routerUrl()).toBe(
        '/players/head-to-head?player_a=7&player_b=8&role=medic&to=2026-05-01',
      );
      const [req] = h2hRequests();
      expect(req.request.params.get('role')).toBe('medic');
      req.flush({ data: h2h(), meta: {} });
      await harness.fixture.whenStable();
      expect(text(page().querySelector('h2'))).toBe('Ace vs Bo');
    });
  });

  describe('results', () => {
    it('heads the results "{A} vs {B}"', async () => {
      await openResults();
      expect(text(page().querySelector('h2'))).toBe('Ace vs Bo');
    });

    it('INV-6: puts the You badge on your Self-insert in the heading', async () => {
      const self = { ...SELF_INSERT, id: 8 };
      await openResults(h2h(), undefined, { ...USER, self_insert: self });
      expect(text(page().querySelector('h2'))).toBe('Ace vs Bo You');
      const badges = page().querySelectorAll('.badge--you');
      expect(badges.length).toBe(1);
      expect(badges[0].classList.contains('badge')).toBe(true);
    });

    it('says no rounds were found for an empty record', async () => {
      await openResults(h2h({ round_record: { wins: 0, losses: 0, ties: 0, n: 0 } }));
      expect(pageText()).toContain(
        'No head-to-head rounds found between these two players for the selected filters.',
      );
    });

    it('does not say it when there are rounds', async () => {
      await openResults();
      expect(pageText()).not.toContain('No head-to-head rounds found');
    });

    it('shows the Round record, the margin and the tags each way', async () => {
      await openResults();
      expect(card('Round record (W-L-T)')).toBe('Round record (W-L-T) 2-1-0 3 rounds');
      expect(card('Avg score margin (A − B)')).toBe('Avg score margin (A − B) -12.3 over 3 rounds');
      expect(card('Tags A → B (avg / total)')).toBe('Tags A → B (avg / total) 1.67 / 5');
      expect(card('Tags B → A (avg / total)')).toBe('Tags B → A (avg / total) 0.50 / 2');
    });

    it("lists the per-role breakdown by Player A's role", async () => {
      await openResults();
      expect(pageText()).toContain("Per-role breakdown (Player A's role)");
      expect(tableHead('.h2h-role-table')).toEqual([
        'Role',
        'Games',
        'W',
        'L',
        'T',
        'Mean margin',
        'Avg tags A→B',
        'Avg tags B→A',
      ]);
      expect(tableRows('.h2h-role-table')).toEqual([
        ['Commander', '2', '1', '1', '0', '10.3', '1.50', '0.33'],
      ]);
    });

    it('says "No per-role data." when there is none', async () => {
      await openResults(h2h({ per_role_breakdown: [] }));
      expect(page().querySelector('.h2h-role-table')).toBeNull();
      expect(pageText()).toContain('No per-role data.');
    });

    it('lists the per-map breakdown', async () => {
      await openResults();
      expect(tableRows('.h2h-map-table')).toEqual([
        ['No map (3-zone)', '3', '2', '1', '0', '-12.3'],
      ]);
    });

    it('says "No per-map data." when there is none', async () => {
      await openResults(h2h({ per_map_breakdown: [] }));
      expect(pageText()).toContain('No per-map data.');
    });

    it('lists every H2H round with its roles, score, tags and winner', async () => {
      await openResults();
      expect(pageText()).toContain('All H2H rounds');
      expect(tableHead('.h2h-rounds')).toEqual([
        'Date',
        'Role A',
        'Role B',
        'Score (A − B)',
        'Tags A→B',
        'Tags B→A',
        'Winner',
        '',
      ]);
      expect(tableRows('.h2h-rounds')).toEqual([
        ['2026-10-02 14:05', 'Commander', 'Heavy', '9000 - 8000', '3', '1', 'Player A', 'View'],
      ]);
    });

    it('INV-5: View opens the Angular Round page by id, never detail_url', async () => {
      await openResults();
      const view = page().querySelector('.h2h-rounds tbody a');
      expect(view?.getAttribute('href')).toBe('/rounds/55');
    });

    it('says "No rounds to list." when there are none', async () => {
      await openResults(h2h({ detail_list: [] }));
      expect(page().querySelector('.h2h-rounds')).toBeNull();
      expect(pageText()).toContain('No rounds to list.');
    });

    it('draws the margin and cumulative charts by Round #', async () => {
      await openResults();
      const margin = chart('Margin per round (A − B)') as LineChart;
      expect(margin.xAxis()).toBe('count');
      expect(margin.xTitle()).toBe('Round #');
      expect(margin.yTitle()).toBe('Margin');
      expect(margin.maxSec()).toBe(2);
      expect(margin.datasets().map((d) => [d.label, d.dashed ?? false])).toEqual([
        ['Margin (A − B)', false],
        ['Zero', true],
      ]);
      const cumulative = chart('Cumulative round W − L (A − B)') as LineChart;
      expect(cumulative.yTitle()).toBe('Cumulative W − L');
      expect(cumulative.datasets()[0].label).toBe('Cumulative (A wins − B wins)');
    });

    it('draws no chart when the series are empty', async () => {
      await openResults(h2h({ margin_series: [], cumulative_wl_series: [] }));
      expect(harness.fixture.debugElement.query(By.directive(LineChart))).toBeNull();
    });

    it('scrolls each wide table inside its own container', async () => {
      await openResults();
      const tables = [...page().querySelectorAll('table')];
      expect(tables.length).toBe(3);
      for (const table of tables) {
        expect(getComputedStyle(table.parentElement as HTMLElement).overflowX).toBe('auto');
      }
    });
  });

  describe('INV-2..4 failures', () => {
    async function failWith(status: number, message: string, code = 'invalid'): Promise<void> {
      await openPicker('/players/head-to-head?player_a=7&player_b=7');
      h2hRequests()[0].flush(
        { errors: [{ code, message, field: null }], meta: {} },
        { status, statusText: 'Error' },
      );
      await harness.fixture.whenStable();
    }

    it('INV-2: a 400 shows its message above the picker and keeps the picks', async () => {
      await failWith(400, 'Pick two different players to compare.');
      const box = page().querySelector('[role="alert"]');
      expect(text(box)).toBe('Pick two different players to compare.');
      const form = page().querySelector('form') as HTMLFormElement;
      expect(box?.compareDocumentPosition(form)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
      expect(select('player_a').value).toBe('7');
      expect(select('player_b').value).toBe('7');
      expect(page().querySelector('h2')).toBeNull();
    });

    it('INV-3: a 404 shows "Player not found" and no results', async () => {
      await failWith(404, 'Not found.', 'not_found');
      expect(text(page().querySelector('h1'))).toBe('Player not found');
      const back = page().querySelector('a[href="/players"]');
      expect(back).not.toBeNull();
      expect(page().querySelector('h2')).toBeNull();
    });

    it('INV-4: any other analytics failure shows the server message', async () => {
      await failWith(500, 'Database down.', 'server_error');
      expect(text(page().querySelector('[role="alert"]'))).toBe('Database down.');
      expect(page().querySelector('h2')).toBeNull();
    });

    it('INV-4: a failing Player list shows the server message', async () => {
      await setUp();
      await harness.navigateByUrl('/players/head-to-head');
      playersRequest().flush(
        { errors: [{ code: 'server_error', message: 'Players down.', field: null }], meta: {} },
        { status: 500, statusText: 'Server Error' },
      );
      await harness.fixture.whenStable();
      expect(text(page().querySelector('[role="alert"]'))).toBe('Players down.');
    });
  });
});
