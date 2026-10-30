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
import { LeagueNav } from '../../api/leagues.models';
import { TeamHistory, TeamHistoryPlayerRow } from '../../api/league-team.models';
import { signIn } from '../../api/testing/auth-fixtures';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';
import { leagueNav } from '../testing/league-fixtures';
import { failure, pagedEnvelope } from '../testing/league-stats-fixtures';
import {
  teamHistory,
  teamHistoryOverall,
  teamHistoryPlayerRow,
  teamHistorySeason,
  withTeamOptions,
} from '../testing/league-team-fixtures';
import { LeagueTeamHistory } from './team-history';

const BASE = environment.apiBaseUrl;
const PATH = '/leagues/1/team/history';
const historyUrl = (team: number): string => `${BASE}/leagues/1/teams/${team}/history/`;
const playersUrl = (team: number): string => `${BASE}/leagues/1/teams/${team}/history/players/`;
const NO_SEASON = 'No Season in this League yet — start a Season to view team history.';
const NO_TEAM = "No team available to display in this League's current Season.";
const LEGEND =
  'Players currently on this team are highlighted in green. ' +
  'Players now on another team are highlighted in blue.';

describe('Team History (/leagues/:id/team/history, spec #428)', () => {
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
    page().querySelector<HTMLElement>(`app-league-team-history ${selector}`);
  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const url = (): string => TestBed.inject(Router).url;
  const stable = (): Promise<void> => harness.fixture.whenStable();

  /** Every param the request sent. */
  function sent(req: TestRequest): Record<string, string> {
    const params = req.request.params;
    return Object.fromEntries(params.keys().map((k) => [k, params.get(k) ?? '']));
  }

  async function answerHistory(team = 3, data: TeamHistory = teamHistory()): Promise<void> {
    http.expectOne(historyUrl(team)).flush(withTeamOptions(data));
    await stable();
  }

  async function answerPlayers(
    team = 3,
    rows: TeamHistoryPlayerRow[] = [teamHistoryPlayerRow()],
    meta: { page?: number; page_size?: number; total_pages?: number; count?: number } = {},
  ): Promise<Record<string, string>> {
    const req = http.expectOne((r) => r.url === playersUrl(team));
    const params = sent(req);
    req.flush(pagedEnvelope(rows, meta.count ?? rows.length, meta));
    await stable();
    return params;
  }

  /** Opens `path` and answers the layout's nav (no screen request answered). */
  async function openNav(path = PATH, nav: LeagueNav = leagueNav()): Promise<void> {
    await harness.navigateByUrl(path);
    http.expectOne(`${BASE}/leagues/1/nav/`).flush({ data: nav, meta: {} });
    await stable();
  }

  /** Opens `path`, answers the nav and both screen requests; returns the players params. */
  async function open(
    path = PATH,
    team = 3,
    data: TeamHistory = teamHistory(),
    rows?: TeamHistoryPlayerRow[],
  ): Promise<Record<string, string>> {
    await openNav(path);
    await answerHistory(team, data);
    return answerPlayers(team, rows);
  }

  it("loads the nav's Team: its history and its Players, name ascending, page 1 of 10", async () => {
    expect(await open()).toEqual({ ordering: 'name', page: '1', page_size: '10' });
    expect(harness.fixture.debugElement.query(By.directive(LeagueTeamHistory))).not.toBeNull();
  });

  it("heads the page with the Team's name", async () => {
    await open();
    expect(text(q('h1'))).toBe('Red Raiders — History');
  });

  it('shows the Overall record, playoff appearances and championships', async () => {
    await open(
      PATH,
      3,
      teamHistory({
        overall: teamHistoryOverall({
          wins: 12,
          losses: 6,
          ties: 2,
          playoff_appearances: 3,
          championships: 1,
        }),
      }),
    );
    const overall = q('#team-history-overall');
    expect(text(overall?.querySelector('h2'))).toBe('Overall');
    const pairs = [...(overall?.querySelectorAll('dt') ?? [])].map(
      (dt) => `${text(dt)}: ${text(dt.nextElementSibling)}`,
    );
    expect(pairs).toEqual([
      'Record (round W-L-T): 12-6-2',
      'Playoff appearances: 3',
      'Championships: 1',
    ]);
  });

  describe('Seasons', () => {
    const seasonHeaders = (): string[] =>
      [...page().querySelectorAll('#team-history-seasons-table th')].map((th) => text(th));
    const seasonRows = (): string[][] =>
      [...page().querySelectorAll('#team-history-seasons-table tbody tr')].map((tr) =>
        [...tr.querySelectorAll('td')].map((td) => text(td)),
      );
    const seasonHeader = (label: string): HTMLAnchorElement =>
      [...page().querySelectorAll<HTMLAnchorElement>('#team-history-seasons-table th a')].find(
        (a) => text(a).startsWith(label),
      )!;

    /** Three Seasons out of order, one with no year and no rank. */
    const threeSeasons = (): TeamHistory =>
      teamHistory({
        seasons: [
          teamHistorySeason({ season: { id: 10, name: 'S0' }, year: 2025, wins: 5, rank: 4 }),
          teamHistorySeason({ season: { id: 12, name: 'S2' }, year: null, wins: 9, rank: null }),
          teamHistorySeason({ season: { id: 11, name: 'S1' }, year: 2026, wins: 7, rank: 1 }),
        ],
      });

    it('has Year | Record (W-L-T) | Final rank, Year descending by default, "—" when unknown', async () => {
      await open(PATH, 3, threeSeasons());
      expect(text(q('#team-history-seasons h2'))).toBe('Seasons');
      expect(seasonHeaders()).toEqual(['Year ↓', 'Record (W-L-T)', 'Final rank']);
      // Python's reverse sort: an unknown year leads a descending sort.
      expect(seasonRows()).toEqual([
        ['—', '9-1-0', '—'],
        ['2026', '7-1-0', '1'],
        ['2025', '5-1-0', '4'],
      ]);
    });

    it('reads "No Seasons enrolled." with no Seasons', async () => {
      await open(PATH, 3, teamHistory({ seasons: [] }));
      expect(seasonRows()).toEqual([['No Seasons enrolled.']]);
    });

    it('a new header sorts ascending client-side, in the URL, with no request', async () => {
      await open(PATH, 3, threeSeasons());
      seasonHeader('Final rank').click();
      await stable();
      expect(url()).toBe(`${PATH}?seasons_sort=rank&seasons_dir=asc`);
      http.expectNone((r) => r.url.includes('/teams/'));
      expect(seasonHeaders()).toEqual(['Year', 'Record (W-L-T)', 'Final rank ↑']);
      expect(seasonRows().map((r) => r[2])).toEqual(['1', '4', '—']);
    });

    it('sorts by wins', async () => {
      await open(`${PATH}?seasons_sort=wins&seasons_dir=desc`, 3, threeSeasons());
      expect(seasonRows().map((r) => r[1])).toEqual(['9-1-0', '7-1-0', '5-1-0']);
    });

    it('clicking the active header flips the direction', async () => {
      await open(PATH, 3, threeSeasons());
      seasonHeader('Year').click();
      await stable();
      expect(url()).toBe(`${PATH}?seasons_sort=year&seasons_dir=asc`);
      expect(seasonRows().map((r) => r[0])).toEqual(['2025', '2026', '—']);
      seasonHeader('Year').click();
      await stable();
      expect(url()).toBe(PATH); // the default, left out of the URL
      expect(seasonRows().map((r) => r[0])).toEqual(['—', '2026', '2025']);
    });

    it('ignores a bad seasons_sort / seasons_dir (Year descending)', async () => {
      await open(`${PATH}?seasons_sort=BOGUS&seasons_dir=NOPE`, 3, threeSeasons());
      expect(seasonHeaders()[0]).toBe('Year ↓');
    });
  });

  describe('Players', () => {
    const playerHeaders = (): string[] =>
      [...page().querySelectorAll('#team-history-players-table th')].map((th) => text(th));
    const playerHeader = (label: string): HTMLAnchorElement =>
      [...page().querySelectorAll<HTMLAnchorElement>('#team-history-players-table th a')].find(
        (a) => text(a).startsWith(label),
      )!;
    const playerRow = (id: number): HTMLElement | null => q(`#team-history-player-row-${id}`);
    const cells = (id: number): string[] =>
      [...(playerRow(id)?.querySelectorAll('td') ?? [])].map((td) => text(td));
    const former = (): TeamHistoryPlayerRow =>
      teamHistoryPlayerRow({
        player: { id: 30, name: 'Comet', team: { id: 5, name: 'Blue Bolts' } },
        on_team: false,
        watched: true,
        last_season_year: null,
      });

    it('has the heading, the legend and the nine columns, Name the active ascending sort', async () => {
      await open();
      expect(text(q('#team-history-players h2'))).toBe('Players');
      expect(text(q('#team-history-players-legend'))).toBe(LEGEND);
      expect(playerHeaders()).toEqual([
        'Name ↑',
        'Games',
        'Points',
        'Tags',
        'Times tagged',
        'Missiles',
        'Resupplies',
        'Specials',
        'Last season',
      ]);
    });

    it('shows each row from its fields, "—" for an unknown last season', async () => {
      await open(PATH, 3, teamHistory(), [teamHistoryPlayerRow(), former()]);
      expect(cells(25)).toEqual([
        '☆ Ace (current)',
        '16',
        '145000',
        '650',
        '340',
        '12',
        '0',
        '30',
        '2026',
      ]);
      expect(cells(30)[8]).toBe('—');
    });

    it('puts the Watch flag and the League player link in the name cell (app-player-cell)', async () => {
      await open(PATH, 3, teamHistory(), [teamHistoryPlayerRow(), former()]);
      const link = playerRow(30)?.querySelector('td.name app-player-cell a');
      expect(link?.getAttribute('href')).toBe('/leagues/1/players/30');
      const flag = playerRow(30)?.querySelector('td.name app-player-cell button');
      expect(flag?.getAttribute('aria-pressed')).toBe('true');
    });

    it('marks a current Player and a former one from on_team', async () => {
      await open(PATH, 3, teamHistory(), [teamHistoryPlayerRow(), former()]);
      expect(playerRow(25)?.classList).toContain('team-history-player--current');
      expect(playerRow(25)?.classList).not.toContain('team-history-player--former');
      expect(playerRow(30)?.classList).toContain('team-history-player--former');
      expect(playerRow(30)?.classList).not.toContain('team-history-player--current');
    });

    it('names the mark in text too, not by colour alone (story 27): a hidden (current) / (former)', async () => {
      await open(PATH, 3, teamHistory(), [teamHistoryPlayerRow(), former()]);
      const status = (id: number): HTMLElement | null | undefined =>
        playerRow(id)?.querySelector<HTMLElement>('td.name .team-history__status');
      expect(text(status(25))).toBe('(current)');
      expect(text(status(30))).toBe('(former)');
      expect(cells(25)[0]).toBe('☆ Ace (current)');

      // The spec runs from `frontend/` (`npm run test:ci`) or from the repo root.
      const scss = 'app/leagues/team-history/team-history.scss';
      const file = [
        join(process.cwd(), 'src', scss),
        join(process.cwd(), 'frontend', 'src', scss),
      ].find((path) => existsSync(path)) as string;
      const rule = /\.team-history__status\s*\{([^}]*)\}/.exec(compile(file).css)?.[1] ?? '';
      expect(rule).toContain('position: absolute');
      expect(rule).toContain('clip: rect(0, 0, 0, 0)');
    });

    it('reads "No players have appeared for this team yet." with no Players', async () => {
      await open(PATH, 3, teamHistory(), []);
      const rows = [...page().querySelectorAll('#team-history-players-table tbody tr')];
      expect(rows.map((tr) => text(tr))).toEqual(['No players have appeared for this team yet.']);
      expect(rows[0].querySelector('td')?.getAttribute('colspan')).toBe('9');
    });

    it('a new header sorts ascending on the server, back to page 1', async () => {
      await open(`${PATH}?page=2`);
      playerHeader('Points').click();
      await stable();
      expect(url()).toBe(`${PATH}?sort=points_scored&dir=asc`);
      expect(await answerPlayers()).toEqual({
        ordering: 'points_scored',
        page: '1',
        page_size: '10',
      });
      http.expectNone(historyUrl(3)); // the Seasons and Overall are not reloaded
      expect(playerHeaders()[2]).toBe('Points ↑');
      expect(playerHeaders()[0]).toBe('Name');
    });

    it('the active header flips to descending', async () => {
      await open(`${PATH}?sort=points_scored&dir=asc`);
      playerHeader('Points').click();
      await stable();
      expect(url()).toBe(`${PATH}?sort=points_scored&dir=desc`);
      expect((await answerPlayers())['ordering']).toBe('-points_scored');
      expect(playerHeaders()[2]).toBe('Points ↓');
    });

    it('a per-page pick (STATS_PAGE_SIZES) goes back to page 1', async () => {
      await open(`${PATH}?page=3`);
      const select = q('#stats-per-page-select') as HTMLSelectElement;
      expect([...select.options].map((o) => o.value)).toEqual(['10', '25', '50', '100']);
      select.value = '25';
      select.dispatchEvent(new Event('change'));
      await stable();
      expect(url()).toBe(`${PATH}?per_page=25`);
      expect(await answerPlayers()).toEqual({ ordering: 'name', page: '1', page_size: '25' });
    });

    it('pages with the kit paginator', async () => {
      await openNav();
      await answerHistory();
      await answerPlayers(3, [teamHistoryPlayerRow()], { total_pages: 3, count: 25 });
      expect(text(q('.stats-pager__page'))).toBe('Page 1 of 3');
      [...(q('.stats-pager')?.querySelectorAll('button') ?? [])]
        .find((b) => text(b) === 'Next')
        ?.click();
      await stable();
      expect(url()).toBe(`${PATH}?page=2`);
      expect(await answerPlayers()).toEqual({ ordering: 'name', page: '2', page_size: '10' });
    });

    it('a 404 past page 1 goes back to page 1', async () => {
      await openNav(`${PATH}?page=9`);
      await answerHistory();
      http.expectOne((r) => r.url === playersUrl(3)).flush(...failure(404, 'Invalid page.'));
      await stable();
      expect(url()).toBe(PATH);
      expect(await answerPlayers()).toEqual({ ordering: 'name', page: '1', page_size: '10' });
      expect(q('#team-history-empty-notice')).toBeNull();
    });
  });

  describe('Team picker', () => {
    const picker = (): HTMLSelectElement =>
      q('app-team-picker select#stats-team-select') as HTMLSelectElement;

    it('is the kit app-team-picker in a filters row below the h1, not beside it', async () => {
      await open();
      expect(q('h1 ~ .stats-filters app-team-picker')).not.toBeNull();
      // Its own row right under the h1, not a header row beside it.
      expect(q('h1')?.nextElementSibling?.classList).toContain('stats-filters');
    });

    it("lists meta.team_options by name, the shown Team selected, with no 'All'", async () => {
      await open();
      expect(text(q('label[for="stats-team-select"]'))).toBe('Team:');
      expect([...picker().options].map((o) => `${o.value}:${o.text}`)).toEqual([
        '5:Blue Bolts',
        '3:Red Raiders',
      ]);
      expect(picker().value).toBe('3');
    });

    it('sets ?team=, resets the Players page to 1 and keeps both sorts', async () => {
      await open(`${PATH}?seasons_sort=rank&seasons_dir=asc&sort=tags_made&dir=desc&page=2`);
      picker().value = '5';
      picker().dispatchEvent(new Event('change'));
      await stable();
      expect(url()).toBe(
        `${PATH}?team=5&sort=tags_made&dir=desc&seasons_sort=rank&seasons_dir=asc`,
      );
      await answerHistory(5, teamHistory({ team: { id: 5, name: 'Blue Bolts' } }));
      expect(await answerPlayers(5)).toEqual({
        ordering: '-tags_made',
        page: '1',
        page_size: '10',
      });
      expect(text(q('h1'))).toBe('Blue Bolts — History');
    });
  });

  describe('URL state (I3)', () => {
    it('a Seasons sort keeps the Players sort, page and per-page', async () => {
      await open(`${PATH}?sort=points_scored&dir=desc&page=2&per_page=25`);
      [...page().querySelectorAll<HTMLAnchorElement>('#team-history-seasons-table th a')]
        .find((a) => text(a).startsWith('Final rank'))!
        .click();
      await stable();
      expect(url()).toBe(
        `${PATH}?sort=points_scored&dir=desc&page=2&per_page=25&seasons_sort=rank&seasons_dir=asc`,
      );
      http.expectNone((r) => r.url.includes('/teams/'));
    });

    it('a Players sort keeps the Seasons sort', async () => {
      await open(`${PATH}?seasons_sort=wins&seasons_dir=asc`);
      [...page().querySelectorAll<HTMLAnchorElement>('#team-history-players-table th a')]
        .find((a) => text(a).startsWith('Games'))!
        .click();
      await stable();
      expect(url()).toBe(`${PATH}?sort=games_played&dir=asc&seasons_sort=wins&seasons_dir=asc`);
      await answerPlayers();
      expect(text(page().querySelector('#team-history-seasons-table th:nth-child(2)'))).toBe(
        'Record (W-L-T) ↑',
      );
    });
  });

  it('puts both tables in the scrolling box (I6)', async () => {
    await open();
    for (const id of ['team-history-seasons-table', 'team-history-players-table']) {
      expect(q(`#${id}`)?.parentElement?.classList).toContain('stats-scroll');
      expect(q(`#${id}`)?.classList).toContain('stats-table');
    }
  });

  describe('empty states (I1)', () => {
    /** Everything but the h1 and the notice is absent. */
    function onlyNotice(): void {
      expect(q('app-team-picker')).toBeNull();
      expect(q('#team-history-overall')).toBeNull();
      expect(q('#team-history-seasons')).toBeNull();
      expect(q('#team-history-players')).toBeNull();
      expect(q('[role="alert"]')).toBeNull();
    }

    it('with no displayed Season: the League heading, the notice, and no request', async () => {
      // A Team would resolve (?team= and the nav's): the missing Season alone stops the loads.
      await openNav(`${PATH}?team=3`, leagueNav({ displayed_season: null }));
      http.expectNone((r) => r.url.includes('/teams/'));
      expect(text(q('h1'))).toBe('Metro League — History');
      expect(text(q('#team-history-empty-notice'))).toBe(NO_SEASON);
      expect(q('#team-history-empty-notice')?.getAttribute('role')).toBe('status');
      onlyNotice();
    });

    it('with a Season but no Team to resolve: the no-team notice, and no request', async () => {
      await openNav(PATH, leagueNav({ schedule_team: null }));
      http.expectNone((r) => r.url.includes('/teams/'));
      expect(text(q('h1'))).toBe('Metro League — History');
      expect(text(q('#team-history-empty-notice'))).toBe(NO_TEAM);
      onlyNotice();
    });

    it('a 404 on the history (a stale ?team=) shows the no-team notice', async () => {
      await openNav(`${PATH}?team=99`);
      http.expectOne(historyUrl(99)).flush(...failure(404, 'Not found.'));
      await answerPlayers(99);
      expect(text(q('h1'))).toBe('Metro League — History');
      expect(text(q('#team-history-empty-notice'))).toBe(NO_TEAM);
      onlyNotice();
    });

    it('a 404 on the Players shows the no-team notice', async () => {
      await openNav(`${PATH}?team=99`);
      await answerHistory(99);
      http.expectOne((r) => r.url === playersUrl(99)).flush(...failure(404, 'Not found.'));
      await stable();
      expect(text(q('h1'))).toBe('Metro League — History');
      expect(text(q('#team-history-empty-notice'))).toBe(NO_TEAM);
      onlyNotice();
    });
  });

  describe('load errors (I2)', () => {
    it('another history failure shows the error box, and Retry sends it again', async () => {
      await openNav();
      http.expectOne(historyUrl(3)).flush(...failure(500, 'Database is down.'));
      await answerPlayers();
      const alert = q('[role="alert"]');
      expect(text(alert?.querySelector('span'))).toBe('Database is down.');
      expect(q('#team-history-overall')).toBeNull();
      expect(q('#team-history-players')).toBeNull();

      [...(alert?.querySelectorAll('button') ?? [])].find((b) => text(b) === 'Retry')?.click();
      await stable();
      await answerHistory();
      await answerPlayers();
      expect(q('[role="alert"]')).toBeNull();
      expect(text(q('h1'))).toBe('Red Raiders — History');
    });

    it('a Players failure shows the error box, and Retry sends it again', async () => {
      await openNav();
      await answerHistory();
      http.expectOne((r) => r.url === playersUrl(3)).flush(...failure(503, 'Try later.'));
      await stable();
      const alert = q('[role="alert"]');
      expect(text(alert?.querySelector('span'))).toBe('Try later.');

      [...(alert?.querySelectorAll('button') ?? [])].find((b) => text(b) === 'Retry')?.click();
      await stable();
      await answerHistory();
      expect(await answerPlayers()).toEqual({ ordering: 'name', page: '1', page_size: '10' });
      expect(q('[role="alert"]')).toBeNull();
      expect(q('#team-history-players')).not.toBeNull();
    });
  });
});
