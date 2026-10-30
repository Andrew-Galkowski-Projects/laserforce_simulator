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
import { TeamRef } from '../../api/analytics.models';
import { PlayerRatingRow } from '../../api/league-stats.models';
import { LeagueNav } from '../../api/leagues.models';
import { signIn } from '../../api/testing/auth-fixtures';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';
import {
  badParam,
  failure,
  pagedEnvelope,
  playerRatingRow,
  teamOptions,
} from '../testing/league-stats-fixtures';
import { leagueNav } from '../testing/league-fixtures';
import { PlayerRatings } from './player-ratings';

const BASE = environment.apiBaseUrl;
const RATINGS = `${BASE}/leagues/1/player-ratings/`;
const PATH = '/leagues/1/stats/player-ratings';
const NO_SEASON = 'No Season in this League yet — start a Season to view player ratings.';
const NO_PLAYERS = "No players on enrolled teams in this League's current Season.";

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

describe('Player Ratings (/leagues/:id/stats/player-ratings, spec #417)', () => {
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
    page().querySelector<HTMLElement>(`app-player-ratings ${selector}`);
  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const url = (): string => TestBed.inject(Router).url;

  function ratingsRequest(): TestRequest {
    return http.expectOne((req) => req.url === RATINGS);
  }

  /** Every param the request sent. */
  function sent(req: TestRequest): Record<string, string> {
    const params = req.request.params;
    return Object.fromEntries(params.keys().map((k) => [k, params.get(k) ?? '']));
  }

  interface Answer {
    rows?: PlayerRatingRow[];
    count?: number;
    page?: number;
    totalPages?: number;
    teams?: TeamRef[];
  }

  async function answer(a: Answer = {}): Promise<Record<string, string>> {
    const rows = a.rows ?? [playerRatingRow()];
    const req = ratingsRequest();
    const params = sent(req);
    req.flush(
      pagedEnvelope(rows, a.count ?? rows.length, {
        page: a.page ?? 1,
        total_pages: a.totalPages ?? 1,
        team_options: a.teams ?? teamOptions(),
      }),
    );
    await harness.fixture.whenStable();
    return params;
  }

  async function navigate(path: string, nav: LeagueNav = leagueNav()): Promise<void> {
    await harness.navigateByUrl(path);
    http.expectOne(`${BASE}/leagues/1/nav/`).flush({ data: nav, meta: {} });
  }

  /** Opens `path`, answers the layout's nav, and returns the first ratings request's params. */
  async function open(path = PATH, a: Answer = {}): Promise<Record<string, string>> {
    await navigate(path);
    return answer(a);
  }

  const headerCells = (): HTMLTableCellElement[] => [
    ...page().querySelectorAll<HTMLTableCellElement>('#player-ratings-table th'),
  ];
  const headers = (): string[] => headerCells().map((th) => text(th));
  const header = (key: string): HTMLAnchorElement =>
    page().querySelector<HTMLAnchorElement>(`#player-ratings-th-${key} a`)!;
  const row = (id = 25): HTMLTableCellElement[] => [
    ...page().querySelectorAll<HTMLTableCellElement>(`#player-ratings-row-${id} td`),
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

  const pagerButton = (label: string): HTMLButtonElement =>
    [...page().querySelectorAll<HTMLButtonElement>('app-player-ratings .stats-pager button')].find(
      (b) => text(b) === label,
    )!;

  it('asks for page 1 of 10, sorted by Team ascending, with no season param', async () => {
    expect(await open()).toEqual({ ordering: 'team', page: '1', page_size: '10' });
    expect(harness.fixture.debugElement.query(By.directive(PlayerRatings))).not.toBeNull();
  });

  it('heads the page "Player Ratings", as the template does (no League name)', async () => {
    await open();
    expect(text(q('h1'))).toBe('Player Ratings');
  });

  describe('columns', () => {
    it('has the sortable rating headers, then Age, Site, Ht, GP and Start; no MMR or Rank', async () => {
      await open();
      expect(headers()).toEqual([
        ...SORTABLE.map(([key, label]) => (key === 'team' ? 'Team ↑' : label)),
        'Age',
        'Site',
        'Ht',
        'GP',
        'Start',
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
      ]);
      expect(bio.map((th) => th.querySelector('a'))).toEqual([null, null, null, null, null]);
    });
  });

  describe('a row', () => {
    it("shows the template's cells and formats", async () => {
      await open();
      expect(cells()).toEqual([
        '★ Ace',
        'Red Raiders',
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
      ]);
    });

    it('right-aligns the numbers and keeps the Player and Team names whole', async () => {
      await open();
      const classes = row().map((td) =>
        td.classList.contains('num') ? 'num' : td.classList.contains('name') ? 'name' : '',
      );
      expect(classes).toEqual([
        'name',
        'name',
        '',
        ...Array(21).fill('num'),
        'num',
        '',
        '',
        'num',
        'num',
      ]);
    });

    it('uses the Player cell: the name links to the League player page (I4)', async () => {
      await open('/leagues/1/stats/player-ratings', { rows: [playerRatingRow({ watched: true })] });
      const cell = row()[0];
      expect(cell.querySelector('app-player-cell')).not.toBeNull();
      expect(cell.querySelector('a')!.getAttribute('href')).toBe('/leagues/1/players/25');
      expect(cell.querySelector('button')!.getAttribute('aria-pressed')).toBe('true');
    });

    it('links the Team to its page, its full name in the title', async () => {
      await open();
      const link = row()[1].querySelector('a')!;
      expect(link.getAttribute('href')).toBe('/teams/3');
      expect(link.title).toBe('Red Raiders');
    });

    it('shortens a long Team name to 14 characters, the full name in the title', async () => {
      const team = { id: 3, name: 'The Laser Lords of Ventura' };
      await open(PATH, {
        rows: [playerRatingRow({ player: { id: 25, name: 'Ace', team } })],
      });
      const link = row()[1].querySelector('a')!;
      expect(text(link)).toBe('The Laser Lor…');
      expect(link.title).toBe('The Laser Lords of Ventura');
    });

    it('keeps a 14-character Team name and Roles whole', async () => {
      const team = { id: 3, name: 'Fourteen Chars' };
      await open(PATH, {
        rows: [
          playerRatingRow({
            player: { id: 25, name: 'Ace', team },
            preferred_roles: ['ammo', 'medic.'],
          }),
        ],
      });
      expect(cells().slice(1, 3)).toEqual(['Fourteen Chars', 'ammo, medic.']);
    });

    it('cuts a 15-character Team name', async () => {
      const team = { id: 3, name: 'Fifteen Charsxy' };
      await open(PATH, { rows: [playerRatingRow({ player: { id: 25, name: 'Ace', team } })] });
      expect(cells()[1]).toBe('Fifteen Chars…');
    });

    it('puts the whole Roles list in the title', async () => {
      await open();
      expect(row()[2].title).toBe('commander, heavy');
    });

    it('shows "—" for a Player with no Team', async () => {
      await open(PATH, {
        rows: [playerRatingRow({ player: { id: 25, name: 'Ace', team: null } })],
      });
      expect(cells()[1]).toBe('—');
      expect(row()[1].querySelector('a')).toBeNull();
    });

    it('shows Ovr and Pot to 1 decimal place', async () => {
      await open(PATH, { rows: [playerRatingRow({ overall_rating: 70, potential: 81.26 })] });
      expect(cells()[3]).toBe('70.0');
      expect(cells()[23]).toBe('81.3');
    });

    it('shows "—" for a missing Pot and "-" for a missing Age, Site, Ht and Start', async () => {
      await open(PATH, {
        rows: [
          playerRatingRow({
            potential: null,
            age: null,
            home_site: '',
            height: '',
            started_playing_age: null,
          }),
        ],
      });
      expect(cells().slice(23)).toEqual(['—', '-', '-', '-', '120', '-']);
    });

    it('puts the whole Site in the title', async () => {
      await open();
      expect(row()[25].title).toBe('Laserforce Ventura');
    });
  });

  describe('sorting (I1)', () => {
    it('a new header sorts ascending and goes back to page 1, in the URL and the request', async () => {
      await open(`${PATH}?page=2`, { count: 30, page: 2, totalPages: 3 });
      await click(header('overall_rating'));
      expect(url()).toBe(`${PATH}?sort=overall_rating&dir=asc`);
      expect(await answer()).toEqual({ ordering: 'overall_rating', page: '1', page_size: '10' });
      expect(headers()[3]).toBe('Ovr ↑');
      expect(headers()[1]).toBe('Team');
    });

    it('the active header toggles to descending', async () => {
      await open(`${PATH}?sort=overall_rating&dir=asc`);
      await click(header('overall_rating'));
      expect(url()).toBe(`${PATH}?sort=overall_rating&dir=desc`);
      expect(await answer()).toEqual({ ordering: '-overall_rating', page: '1', page_size: '10' });
      expect(headers()[3]).toBe('Ovr ↓');
    });

    it('the default Team header toggles to descending', async () => {
      await open();
      await click(header('team'));
      expect(url()).toBe(`${PATH}?sort=team&dir=desc`);
      expect(await answer()).toEqual({ ordering: '-team', page: '1', page_size: '10' });
    });

    it('takes the default for a hand-edited sort it does not know', async () => {
      expect(await open(`${PATH}?sort=mmr&dir=up`)).toEqual({
        ordering: 'team',
        page: '1',
        page_size: '10',
      });
    });
  });

  describe('Team picker', () => {
    it('offers All Teams plus the enrolled Teams, on the URL Team', async () => {
      await open(`${PATH}?team=3`);
      expect([...select('stats-team-select').options].map((o) => text(o))).toEqual([
        'All Teams',
        'Blue Bolts',
        'Red Raiders',
      ]);
      expect(select('stats-team-select').value).toBe('3');
    });

    it('sends the Team as team_id', async () => {
      expect(await open(`${PATH}?team=5`)).toEqual({
        team_id: '5',
        ordering: 'team',
        page: '1',
        page_size: '10',
      });
    });

    it('a Team change keeps the sort and goes back to page 1', async () => {
      await open(`${PATH}?sort=speed&dir=desc&page=2`, { count: 30, page: 2, totalPages: 3 });
      const navigate = vi.spyOn(TestBed.inject(Router), 'navigate');
      await change('stats-team-select', '5');
      expect(url()).toBe(`${PATH}?team=5&sort=speed&dir=desc`);
      expect(navigate.mock.calls.map(([, extras]) => extras?.replaceUrl)).toEqual([false]);
      expect(await answer()).toEqual({
        team_id: '5',
        ordering: '-speed',
        page: '1',
        page_size: '10',
      });
    });

    it('All Teams drops the Team', async () => {
      await open(`${PATH}?team=5`);
      await change('stats-team-select', '');
      expect(url()).toBe(PATH);
      expect(await answer()).toEqual({ ordering: 'team', page: '1', page_size: '10' });
    });

    it('has no Season picker', async () => {
      await open();
      expect(q('#stats-season-select')).toBeNull();
    });
  });

  describe('count and paging', () => {
    it('shows the player count', async () => {
      await open(PATH, { count: 31, totalPages: 4 });
      expect(text(q('#player-ratings-count'))).toBe('31 players');
    });

    it('says "1 player" for one', async () => {
      await open();
      expect(text(q('#player-ratings-count'))).toBe('1 player');
    });

    it('puts the count above the table, outside the paging footer', async () => {
      await open(PATH, { count: 31, totalPages: 4 });
      const count = q('#player-ratings-count')!;
      const table = q('#player-ratings-table')!;
      expect(count.compareDocumentPosition(table) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      expect(count.closest('.stats-footer')).toBeNull();
      expect(q('.stats-footer #stats-per-page-select')).not.toBeNull();
      expect(q('.stats-footer .stats-pager')).not.toBeNull();
    });

    it('shows Page X of N and Next loads the next page', async () => {
      await open(PATH, { count: 31, totalPages: 4 });
      expect(text(q('.stats-pager__page'))).toBe('Page 1 of 4');
      expect(pagerButton('Previous').disabled).toBe(true);
      await click(pagerButton('Next'));
      expect(url()).toBe(`${PATH}?page=2`);
      expect(await answer({ count: 31, page: 2, totalPages: 4 })).toEqual({
        ordering: 'team',
        page: '2',
        page_size: '10',
      });
      expect(text(q('.stats-pager__page'))).toBe('Page 2 of 4');
    });

    it('hides the pager on a single page', async () => {
      await open();
      expect(q('.stats-pager')).toBeNull();
    });

    it('a per-page change goes back to page 1', async () => {
      await open(`${PATH}?team=3&page=3`, { count: 31, page: 3, totalPages: 4 });
      expect(select('stats-per-page-select').value).toBe('10');
      await change('stats-per-page-select', '25');
      expect(url()).toBe(`${PATH}?team=3&per_page=25`);
      expect(await answer()).toEqual({
        team_id: '3',
        ordering: 'team',
        page: '1',
        page_size: '25',
      });
    });
  });

  describe('lenient fallback (I2)', () => {
    it('drops a stale team the API rejects, replacing the URL, and reloads', async () => {
      await navigate(`${PATH}?team=99&sort=speed&dir=asc`);
      const navigateSpy = vi.spyOn(TestBed.inject(Router), 'navigate');
      ratingsRequest().flush(...badParam('team_id'));
      await harness.fixture.whenStable();
      expect(url()).toBe(`${PATH}?sort=speed&dir=asc`);
      expect(navigateSpy.mock.calls.map(([, extras]) => extras?.replaceUrl)).toEqual([true]);
      expect(await answer()).toEqual({ ordering: 'speed', page: '1', page_size: '10' });
      expect(q('[role="alert"]')).toBeNull();
      expect(cells()[0]).toBe('★ Ace');
    });

    it('drops an ordering the API rejects', async () => {
      await navigate(`${PATH}?team=3&sort=speed&dir=desc`);
      ratingsRequest().flush(...badParam('ordering'));
      await harness.fixture.whenStable();
      expect(url()).toBe(`${PATH}?team=3`);
      expect(await answer()).toEqual({
        team_id: '3',
        ordering: 'team',
        page: '1',
        page_size: '10',
      });
    });

    it('goes back to page 1 on a 404 past the last page', async () => {
      await navigate(`${PATH}?page=9`);
      ratingsRequest().flush(...failure(404, 'Invalid page.'));
      await harness.fixture.whenStable();
      expect(url()).toBe(PATH);
      expect(await answer()).toEqual({ ordering: 'team', page: '1', page_size: '10' });
      expect(q('[role="alert"]')).toBeNull();
    });
  });

  describe('empty states (I3)', () => {
    it("shows the no-Season notice when the League nav's displayed Season is null", async () => {
      await navigate(PATH, leagueNav({ displayed_season: null }));
      await answer({ rows: [], teams: [] });
      expect(text(q('#player-ratings-empty-notice'))).toBe(NO_SEASON);
      expect(q('#player-ratings-table')).toBeNull();
      expect(q('#stats-team-select')).toBeNull();
    });

    it('shows no table before the League nav says whether there is a Season', async () => {
      await harness.navigateByUrl(PATH);
      await answer();
      expect(q('#player-ratings-table')).toBeNull();
      http.expectOne(`${BASE}/leagues/1/nav/`).flush({
        data: leagueNav({ displayed_season: null }),
        meta: {},
      });
      await harness.fixture.whenStable();
      expect(text(q('#player-ratings-empty-notice'))).toBe(NO_SEASON);
      expect(q('#player-ratings-table')).toBeNull();
    });

    it('shows the no-players notice for an empty result', async () => {
      await open(PATH, { rows: [] });
      expect(text(q('#player-ratings-empty-notice'))).toBe(NO_PLAYERS);
      expect(q('#player-ratings-table')).toBeNull();
      expect(q('#player-ratings-count')).toBeNull();
    });

    it('shows no notice while there are rows', async () => {
      await open();
      expect(q('#player-ratings-empty-notice')).toBeNull();
    });
  });

  it("shows any other failure in the error box with the server's message", async () => {
    await navigate(PATH);
    ratingsRequest().flush(...failure(500, 'Database is down.'));
    await harness.fixture.whenStable();
    expect(text(q('[role="alert"]'))).toBe('Database is down.');
    expect(q('#player-ratings-table')).toBeNull();
    expect(url()).toBe(PATH);
  });

  it("shows the layout's not-found state for another Account's League (I7)", async () => {
    await harness.navigateByUrl(PATH);
    http.expectOne(`${BASE}/leagues/1/nav/`).flush(...failure(404, 'Not found.'));
    http.match((req) => req.url === RATINGS);
    await harness.fixture.whenStable();
    expect(text(page().querySelector('h1'))).toBe('League not found');
    expect(page().querySelector('app-player-ratings')).toBeNull();
  });
});
