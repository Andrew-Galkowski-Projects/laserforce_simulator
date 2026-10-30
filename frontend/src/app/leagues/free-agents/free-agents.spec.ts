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
import { By } from '@angular/platform-browser';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { environment } from '../../../environments/environment';
import { PlayerRatingRow } from '../../api/league-stats.models';
import { LeagueNav } from '../../api/leagues.models';
import { signIn } from '../../api/testing/auth-fixtures';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';
import { leagueNav } from '../testing/league-fixtures';
import {
  badParam,
  failure,
  pagedEnvelope,
  playerRatingRow,
} from '../testing/league-stats-fixtures';
import { FreeAgents } from './free-agents';

const BASE = environment.apiBaseUrl;
const AGENTS = `${BASE}/leagues/1/free-agents/`;
const PATH = '/leagues/1/players/free-agents';
const DEFAULT = { ordering: 'team', page: '1', page_size: '10' };
const NO_SEASON = 'No Season in this League yet — start a Season to view free agents.';
const NO_AGENTS = "No free agents available in this League's current Season.";

/** `RATING_SORT_KEYS_DISPLAY` (league_views.py): key, abbreviation, full name. */
const SORTABLE: [string, string, string][] = [
  ['name', 'Name', 'Name'],
  ['team', 'Team', 'Team'],
  ['preferred_roles', 'Roles', 'Preferred Roles'],
  ['overall_rating', 'Ovr', 'Overall'],
  ['player_awareness', 'PlAw', 'Player Awareness'],
  ['game_awareness', 'GmAw', 'Game Awareness'],
  ['resource_awareness', 'RsAw', 'Resource Awareness'],
  ['decision_making', 'Dec', 'Decision Making'],
  ['positioning', 'Pos', 'Positioning'],
  ['stamina', 'Sta', 'Stamina'],
  ['speed', 'Spd', 'Speed'],
  ['flexibility', 'Flx', 'Flexibility'],
  ['adaptability', 'Adp', 'Adaptability'],
  ['communication', 'Comm', 'Communication'],
  ['teamwork', 'Team', 'Teamwork'],
  ['offensive_synergy', 'OffSyn', 'Offensive Synergy'],
  ['defensive_synergy', 'DefSyn', 'Defensive Synergy'],
  ['midfield_synergy', 'MidSyn', 'Midfield Synergy'],
  ['resupply_synergy', 'RsupSyn', 'Resupply Synergy'],
  ['resupply_efficiency', 'RsupEff', 'Resupply Efficiency'],
  ['accuracy', 'Acc', 'Accuracy'],
  ['survival', 'Surv', 'Survival'],
  ['special_usage', 'SpcUse', 'Special Usage'],
  ['potential', 'Pot', 'Potential'],
];

/** A free agent has no Team: the pool is the Players on no enrolled Team. */
function agent(overrides: Partial<PlayerRatingRow> = {}): PlayerRatingRow {
  return playerRatingRow({ player: { id: 25, name: 'Ace', team: null }, ...overrides });
}

describe('Free Agents (/leagues/:id/players/free-agents, spec #428)', () => {
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
    page().querySelector<HTMLElement>(`app-free-agents ${selector}`);
  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const url = (): string => TestBed.inject(Router).url;

  function agentsRequest(): TestRequest {
    return http.expectOne((req) => req.url === AGENTS);
  }

  function sent(req: TestRequest): Record<string, string> {
    const params = req.request.params;
    return Object.fromEntries(params.keys().map((k) => [k, params.get(k) ?? '']));
  }

  interface Answer {
    rows?: PlayerRatingRow[];
    count?: number;
    page?: number;
    totalPages?: number;
  }

  async function answer(a: Answer = {}): Promise<Record<string, string>> {
    const rows = a.rows ?? [agent()];
    const req = agentsRequest();
    const params = sent(req);
    req.flush(
      pagedEnvelope(rows, a.count ?? rows.length, {
        page: a.page ?? 1,
        total_pages: a.totalPages ?? 1,
      }),
    );
    await harness.fixture.whenStable();
    return params;
  }

  async function navigate(path: string, nav: LeagueNav = leagueNav()): Promise<void> {
    await harness.navigateByUrl(path);
    http.expectOne(`${BASE}/leagues/1/nav/`).flush({ data: nav, meta: {} });
    await harness.fixture.whenStable();
  }

  async function open(path = PATH, a: Answer = {}): Promise<Record<string, string>> {
    await navigate(path);
    return answer(a);
  }

  const headerCells = (): HTMLTableCellElement[] => [
    ...page().querySelectorAll<HTMLTableCellElement>('#free-agents-table th'),
  ];
  const headers = (): string[] => headerCells().map((th) => text(th));
  const header = (key: string): HTMLAnchorElement =>
    page().querySelector<HTMLAnchorElement>(`#free-agents-th-${key} a`)!;
  const row = (id = 25): HTMLTableCellElement[] => [
    ...page().querySelectorAll<HTMLTableCellElement>(`#free-agents-row-${id} td`),
  ];
  const cells = (id = 25): string[] => row(id).map((td) => text(td));
  const select = (id: string): HTMLSelectElement =>
    page().querySelector<HTMLSelectElement>(`#${id}`)!;

  async function change(id: string, value: string): Promise<void> {
    select(id).value = value;
    select(id).dispatchEvent(new Event('change'));
    await harness.fixture.whenStable();
  }

  async function click(el: HTMLElement): Promise<void> {
    el.click();
    await harness.fixture.whenStable();
  }

  const button = (label: string): HTMLButtonElement =>
    [...page().querySelectorAll<HTMLButtonElement>('app-free-agents button')].find(
      (b) => text(b) === label,
    )!;

  it("asks for page 1 of 10 in the server's default order (team)", async () => {
    expect(await open()).toEqual(DEFAULT);
    expect(harness.fixture.debugElement.query(By.directive(FreeAgents))).not.toBeNull();
  });

  it('is headed "Free Agents"', async () => {
    await open();
    expect(text(q('h1'))).toBe('Free Agents');
  });

  describe('columns', () => {
    it('has the sortable rating headers, then Age, Site, Ht, GP, Start, MMR and Rank', async () => {
      await open();
      expect(headers()).toEqual([
        ...SORTABLE.map(([key, label]) => (key === 'team' ? 'Team ↑' : label)),
        'Age',
        'Site',
        'Ht',
        'GP',
        'Start',
        'MMR',
        'Rank',
      ]);
    });

    it('gives each sortable header its full name as the title', async () => {
      await open();
      expect(SORTABLE.map(([key]) => header(key).title)).toEqual(SORTABLE.map(([, , f]) => f));
    });

    it('gives the bio headers their full names as the title, and no sort link', async () => {
      await open();
      const bio = headerCells().slice(SORTABLE.length);
      expect(bio.map((th) => th.title)).toEqual([
        'Age',
        'Home Site',
        'Height',
        'Games Played',
        'Started Playing Age',
        'MMR',
        'Rank',
      ]);
      expect(bio.every((th) => th.querySelector('a') === null)).toBe(true);
    });
  });

  describe('a row', () => {
    it("shows the template's cells and formats, MMR and Rank as '-'", async () => {
      await open();
      expect(cells()).toEqual([
        '★ Ace',
        '—',
        'commander, he…',
        '72.4',
        '70',
        '75',
        '66',
        '71',
        '69',
        '80',
        '77',
        '64',
        '70',
        '72',
        '74',
        '68',
        '63',
        '65',
        '60',
        '58',
        '68',
        '73',
        '70',
        '80.5',
        '24',
        'Laserforc…',
        '5\'11"',
        '120',
        '15',
        '-',
        '-',
      ]);
    });

    it('uses the Player cell: the ★ flag and the League player link (I4)', async () => {
      await open(PATH, { rows: [agent({ watched: true })] });
      const cell = row()[0];
      expect(cell.querySelector('app-player-cell')).not.toBeNull();
      expect(cell.querySelector('a')!.getAttribute('href')).toBe('/leagues/1/players/25');
      expect(cell.querySelector('button')!.getAttribute('aria-pressed')).toBe('true');
    });

    it('links a Team, should a row carry one', async () => {
      await open(PATH, { rows: [playerRatingRow()] });
      const link = row()[1].querySelector('a')!;
      expect(link.getAttribute('href')).toBe('/teams/3');
      expect(text(link)).toBe('Red Raiders');
    });

    it('shows "—" for a missing Pot and "-" for a missing Age, Site, Ht and Start', async () => {
      await open(PATH, {
        rows: [
          agent({
            potential: null,
            age: null,
            home_site: '',
            height: '',
            started_playing_age: null,
          }),
        ],
      });
      expect(cells().slice(23)).toEqual(['—', '-', '-', '-', '120', '-', '-', '-']);
    });
  });

  describe('sorting (I3)', () => {
    it('a new header sorts ascending and goes back to page 1', async () => {
      await open(`${PATH}?page=2`, { count: 30, page: 2, totalPages: 3 });
      await click(header('overall_rating'));
      expect(url()).toBe(`${PATH}?sort=overall_rating&dir=asc`);
      expect(await answer()).toEqual({ ...DEFAULT, ordering: 'overall_rating' });
      expect(headers()[3]).toBe('Ovr ↑');
      expect(headers()[1]).toBe('Team');
    });

    it('the active header toggles to descending', async () => {
      await open();
      await click(header('team'));
      expect(url()).toBe(`${PATH}?sort=team&dir=desc`);
      expect(await answer()).toEqual({ ...DEFAULT, ordering: '-team' });
      expect(headers()[1]).toBe('Team ↓');
    });

    it('reads the sort and page from the URL', async () => {
      expect(await open(`${PATH}?sort=speed&dir=desc&page=2&per_page=25`, { page: 2 })).toEqual({
        ordering: '-speed',
        page: '2',
        page_size: '25',
      });
    });

    it('has no Team or Season picker', async () => {
      await open();
      expect(q('#stats-team-select')).toBeNull();
      expect(q('#stats-season-select')).toBeNull();
    });
  });

  describe('count and paging', () => {
    it('counts "N free agent(s)"', async () => {
      await open(PATH, { count: 31, totalPages: 4 });
      expect(text(q('#free-agents-count'))).toBe('31 free agents');
    });

    it('says "1 free agent" for one', async () => {
      await open();
      expect(text(q('#free-agents-count'))).toBe('1 free agent');
    });

    it('shows Page X of N and Next loads the next page', async () => {
      await open(PATH, { count: 31, totalPages: 4 });
      expect(text(q('.stats-pager__page'))).toBe('Page 1 of 4');
      await click(button('Next'));
      expect(url()).toBe(`${PATH}?page=2`);
      expect(await answer({ count: 31, page: 2, totalPages: 4 })).toEqual({
        ...DEFAULT,
        page: '2',
      });
      expect(text(q('.stats-pager__page'))).toBe('Page 2 of 4');
    });

    it('a per-page change (10 / 25 / 50 / 100) goes back to page 1', async () => {
      await open(`${PATH}?sort=speed&dir=asc&page=3`, { count: 31, page: 3, totalPages: 4 });
      expect([...select('stats-per-page-select').options].map((o) => o.value)).toEqual([
        '10',
        '25',
        '50',
        '100',
      ]);
      await change('stats-per-page-select', '25');
      expect(url()).toBe(`${PATH}?sort=speed&dir=asc&per_page=25`);
      expect(await answer()).toEqual({ ordering: 'speed', page: '1', page_size: '25' });
    });
  });

  describe('empty states (I1, I5)', () => {
    async function expectHidden(): Promise<void> {
      expect(q('#free-agents-count')).toBeNull();
      expect(q('#stats-per-page-select')).toBeNull();
      expect(q('#free-agents-table')).toBeNull();
      expect(q('.stats-footer')).toBeNull();
    }

    it('with no displayed Season: the notice, the h1, and no request (I1)', async () => {
      await navigate(PATH, leagueNav({ displayed_season: null }));
      http.expectNone((req) => req.url === AGENTS);
      expect(text(q('h1'))).toBe('Free Agents');
      expect(text(q('#free-agents-empty-notice'))).toBe(NO_SEASON);
      await expectHidden();
    });

    it('sends nothing before the League nav answers', async () => {
      await harness.navigateByUrl(PATH);
      await harness.fixture.whenStable();
      http.expectNone((req) => req.url === AGENTS);
      expect(text(q('h1'))).toBe('Free Agents');
      http.expectOne(`${BASE}/leagues/1/nav/`).flush({ data: leagueNav(), meta: {} });
      await harness.fixture.whenStable();
      expect(await answer()).toEqual(DEFAULT);
    });

    it('with an empty pool: the no-free-agents notice', async () => {
      await open(PATH, { rows: [] });
      expect(text(q('h1'))).toBe('Free Agents');
      expect(text(q('#free-agents-empty-notice'))).toBe(NO_AGENTS);
      await expectHidden();
    });

    it('shows no notice while there are rows', async () => {
      await open();
      expect(q('#free-agents-empty-notice')).toBeNull();
    });
  });

  describe('load failures (I2)', () => {
    it("shows the server's message with Retry, which re-sends the same request", async () => {
      await navigate(`${PATH}?sort=speed&dir=asc`);
      agentsRequest().flush(...failure(500, 'Database is down.'));
      await harness.fixture.whenStable();
      expect(text(q('[role="alert"]'))).toContain('Database is down.');
      expect(text(q('h1'))).toBe('Free Agents');
      expect(q('#free-agents-table')).toBeNull();
      await click(button('Retry'));
      expect(await answer()).toEqual({ ...DEFAULT, ordering: 'speed' });
      expect(q('[role="alert"]')).toBeNull();
      expect(cells()[0]).toBe('★ Ace');
    });

    it('drops an ordering the API rejects, replacing the URL', async () => {
      await navigate(`${PATH}?sort=speed&dir=desc`);
      const nav = vi.spyOn(TestBed.inject(Router), 'navigate');
      agentsRequest().flush(...badParam('ordering'));
      await harness.fixture.whenStable();
      expect(url()).toBe(PATH);
      expect(nav.mock.calls.map(([, extras]) => extras?.replaceUrl)).toEqual([true]);
      expect(await answer()).toEqual(DEFAULT);
    });

    it('goes back to page 1 on a 404 past the last page', async () => {
      await navigate(`${PATH}?page=9`);
      agentsRequest().flush(...failure(404, 'Invalid page.'));
      await harness.fixture.whenStable();
      expect(url()).toBe(PATH);
      expect(await answer()).toEqual(DEFAULT);
      expect(q('[role="alert"]')).toBeNull();
    });
  });

  it('I6: the table scrolls sideways inside its own box (768px and 1280px)', async () => {
    await open();
    expect(q('#free-agents-table')?.parentElement?.classList).toContain('stats-scroll');
    // The spec runs from `frontend/` (`npm run test:ci`) or from the repo root.
    const scss = 'app/leagues/free-agents/free-agents.scss';
    const file = [
      join(process.cwd(), 'src', scss),
      join(process.cwd(), 'frontend', 'src', scss),
    ].find((path) => existsSync(path)) as string;
    const rule = /\.stats-scroll\s*\{([^}]*)\}/.exec(compile(file).css)?.[1] ?? '';
    expect(rule).toContain('max-width: 100%');
    expect(rule).toContain('overflow-x: auto');
  });
});
