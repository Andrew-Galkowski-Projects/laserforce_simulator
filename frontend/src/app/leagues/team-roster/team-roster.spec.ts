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
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { environment } from '../../../environments/environment';
import { TeamRoster } from '../../api/league-team.models';
import { LeagueNav } from '../../api/leagues.models';
import { signIn } from '../../api/testing/auth-fixtures';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';
import { leagueNav } from '../testing/league-fixtures';
import { failure } from '../testing/league-stats-fixtures';
import { rosterRow, teamRoster, withTeamOptions } from '../testing/league-team-fixtures';

const BASE = environment.apiBaseUrl;
const PATH = '/leagues/1/team/roster';
const rosterUrl = (team: number): string => `${BASE}/leagues/1/teams/${team}/roster/`;

describe('Team Roster (/leagues/:id/team/roster, spec #428)', () => {
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
    page().querySelector<HTMLElement>(`app-league-team-roster ${selector}`);
  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const url = (): string => TestBed.inject(Router).url;

  async function navigate(path: string, nav: LeagueNav = leagueNav()): Promise<void> {
    await harness.navigateByUrl(path);
    http.expectOne(`${BASE}/leagues/1/nav/`).flush({ data: nav, meta: {} });
    await harness.fixture.whenStable();
  }

  function rosterRequest(team = 3): TestRequest {
    return http.expectOne((req) => req.url === rosterUrl(team));
  }

  async function answer(team = 3, data: TeamRoster = teamRoster()): Promise<void> {
    rosterRequest(team).flush(withTeamOptions(data));
    await harness.fixture.whenStable();
  }

  /** Opens `path`, answers the nav and the roster request for `team`. */
  async function open(path = PATH, data: TeamRoster = teamRoster(), team = 3): Promise<void> {
    await navigate(path);
    await answer(team, data);
  }

  it("opens on the manager's current Team: the h1 is '<Team> — Roster'", async () => {
    await open();
    expect(text(q('h1'))).toBe('Red Raiders — Roster');
    expect(url()).toBe(PATH);
  });

  const headers = (table: string): string[] =>
    [...page().querySelectorAll(`#${table} thead th`)].map((th) => text(th));
  const rows = (table: string): string[][] =>
    [...page().querySelectorAll(`#${table} tbody tr`)].map((tr) =>
      [...tr.querySelectorAll('td')].map((td) => text(td)),
    );

  describe('Starting Six', () => {
    it('has the h2 and the Django columns in order', async () => {
      await open();
      expect(text(q('#roster-starting-heading'))).toBe('Starting Six');
      expect(headers('roster-starting-table')).toEqual([
        'Role',
        'Name',
        'Age',
        'Home Site',
        'Height',
        'Games',
        'Started',
        'MMR',
        'Rank',
        'Overall',
        'Potential',
      ]);
    });

    it('shows a starter: title-cased role, the Player cell, bio, MMR / Rank "-", 1 dp ratings', async () => {
      await open();
      expect(rows('roster-starting-table')).toEqual([
        [
          'Commander',
          '☆ Ace',
          '24',
          'Laserforce Ventura',
          '5\'11"',
          '120',
          '15',
          '-',
          '-',
          '72.4',
          '80.6',
        ],
      ]);
      const link = q('#roster-starting-table app-player-cell a');
      expect(link?.getAttribute('href')).toBe('/leagues/1/players/25');
    });

    it('shows "-" for a missing bio value and "—" for no Potential', async () => {
      await open(
        PATH,
        teamRoster({
          starters: [
            rosterRow({
              role: 'scout',
              age: null,
              home_site: '',
              height: '',
              started_playing_age: null,
              potential: null,
              overall_rating: 60,
            }),
          ],
        }),
      );
      expect(rows('roster-starting-table')).toEqual([
        ['Scout', '☆ Ace', '-', '-', '-', '120', '-', '-', '-', '60.0', '—'],
      ]);
    });

    it('reads "No starting players assigned." across every column when empty', async () => {
      await open(PATH, teamRoster({ starters: [] }));
      expect(rows('roster-starting-table')).toEqual([['No starting players assigned.']]);
      expect(q('#roster-starting-table tbody td')?.getAttribute('colspan')).toBe('11');
    });
  });

  /** An empty state shows its notice and hides the picker and both tables. */
  function expectEmptyState(notice: string): void {
    expect(text(q('h1'))).toBe('Metro League — Roster');
    expect(text(q('#roster-empty-notice'))).toBe(notice);
    expect(q('#roster-empty-notice')?.getAttribute('role')).toBe('status');
    expect(q('app-team-picker')).toBeNull();
    expect(q('#roster-starting-table')).toBeNull();
    expect(q('#roster-bench-table')).toBeNull();
  }

  it('I1: with no displayed Season, shows the no-Season notice and sends no roster request', async () => {
    // `schedule_team` is left set: the screen must not load a roster without a Season.
    await navigate(PATH, leagueNav({ displayed_season: null }));
    expectEmptyState('No Season in this League yet — start a Season to view team rosters.');
    http.expectNone((req) => req.url.includes('/roster/'));
  });

  const NO_TEAM = "No team available to display in this League's current Season.";

  it('with a Season but no Team to show, shows the no-team notice and sends no request', async () => {
    await navigate(PATH, leagueNav({ schedule_team: null }));
    expectEmptyState(NO_TEAM);
  });

  it('a 404 on a stale ?team= shows the no-team notice', async () => {
    await navigate(`${PATH}?team=99`);
    rosterRequest(99).flush(...failure(404, 'Not found.'));
    await harness.fixture.whenStable();
    expectEmptyState(NO_TEAM);
    expect(q('[role="alert"]')).toBeNull();
  });

  it("I2: any other failure shows the server's message with Retry, which re-sends the request", async () => {
    await navigate(PATH);
    rosterRequest().flush(...failure(500, 'Database is down.'));
    await harness.fixture.whenStable();
    expect(text(q('[role="alert"] span'))).toBe('Database is down.');
    expect(q('#roster-starting-table')).toBeNull();
    expect(q('#roster-empty-notice')).toBeNull();

    const retry = [...page().querySelectorAll<HTMLButtonElement>('[role="alert"] button')].find(
      (b) => text(b) === 'Retry',
    );
    retry!.click();
    await harness.fixture.whenStable();
    await answer();
    expect(q('[role="alert"]')).toBeNull();
    expect(text(q('h1'))).toBe('Red Raiders — Roster');
  });

  describe('I4: Watch flag', () => {
    const flag = (table: string, id: number): HTMLButtonElement =>
      page().querySelector<HTMLButtonElement>(`#${table}-row-${id} app-watch-flag button`)!;

    it("seeds each flag from the row's watched", async () => {
      const bolt = { id: 26, name: 'Bolt', team: { id: 3, name: 'Red Raiders' } };
      await open(PATH, teamRoster({ bench: [rosterRow({ player: bolt, watched: true })] }));
      expect(flag('roster-starting', 25).getAttribute('aria-pressed')).toBe('false');
      expect(flag('roster-bench', 26).getAttribute('aria-pressed')).toBe('true');
      expect(flag('roster-bench', 26).getAttribute('aria-label')).toBe('Stop watching Bolt');
    });

    it('toggling a ★ re-colours every flag for that Player on the page', async () => {
      // Ace in both tables stands in for two cells of one Player on the page.
      await open(PATH, teamRoster({ bench: [rosterRow()] }));
      flag('roster-starting', 25).click();
      await harness.fixture.whenStable();
      const put = http.expectOne(`${BASE}/leagues/1/watch-list/25/`);
      expect(put.request.method).toBe('PUT');
      put.flush({ data: { player: rosterRow().player, watched: true }, meta: {} });
      await harness.fixture.whenStable();
      expect(flag('roster-starting', 25).getAttribute('aria-pressed')).toBe('true');
      expect(flag('roster-bench', 25).getAttribute('aria-pressed')).toBe('true');
    });
  });

  it('I6: each table scrolls sideways inside its own box (so it fits at 768px)', async () => {
    await open();
    for (const id of ['roster-starting-table', 'roster-bench-table']) {
      expect(q(`#${id}`)?.parentElement?.classList).toContain('stats-scroll');
    }
    // The spec runs from `frontend/` (`npm run test:ci`) or from the repo root.
    const scss = 'app/leagues/team-roster/team-roster.scss';
    const file = [
      join(process.cwd(), 'src', scss),
      join(process.cwd(), 'frontend', 'src', scss),
    ].find((path) => existsSync(path)) as string;
    const rule = /\.stats-scroll\s*\{([^}]*)\}/.exec(compile(file).css)?.[1] ?? '';
    expect(rule).toContain('max-width: 100%');
    expect(rule).toContain('overflow-x: auto');
  });

  describe('Team picker', () => {
    const picker = (): HTMLSelectElement =>
      q('app-team-picker select#stats-team-select') as HTMLSelectElement;
    const options = (): [string, string][] => [...picker().options].map((o) => [o.value, text(o)]);

    it('is the kit app-team-picker in a filters row below the h1', async () => {
      await open();
      expect(q('h1 ~ .stats-filters app-team-picker')).not.toBeNull();
    });

    it("lists meta.team_options by name, labelled 'Team:', with the shown Team selected", async () => {
      await open();
      expect(text(q('label[for="stats-team-select"]'))).toBe('Team:');
      expect(options()).toEqual([
        ['5', 'Blue Bolts'],
        ['3', 'Red Raiders'],
      ]);
      expect(picker().value).toBe('3');
    });

    it('sets ?team= on a change and loads that Team', async () => {
      await open();
      picker().value = '5';
      picker().dispatchEvent(new Event('change'));
      await harness.fixture.whenStable();
      expect(url()).toBe(`${PATH}?team=5`);
      await answer(5, teamRoster({ team: { id: 5, name: 'Blue Bolts' } }));
      expect(text(q('h1'))).toBe('Blue Bolts — Roster');
      expect(picker().value).toBe('5');
    });

    it('opens on ?team= over the current Team', async () => {
      await open(`${PATH}?team=5`, teamRoster({ team: { id: 5, name: 'Blue Bolts' } }), 5);
      expect(text(q('h1'))).toBe('Blue Bolts — Roster');
      expect(picker().value).toBe('5');
    });
  });

  describe('Bench', () => {
    it('has the h2 and the Starting Six columns minus Role', async () => {
      await open();
      expect(text(q('#roster-bench-heading'))).toBe('Bench');
      expect(headers('roster-bench-table')).toEqual([
        'Name',
        'Age',
        'Home Site',
        'Height',
        'Games',
        'Started',
        'MMR',
        'Rank',
        'Overall',
        'Potential',
      ]);
    });

    it('shows a bench Player without a role', async () => {
      await open();
      expect(rows('roster-bench-table')).toEqual([
        ['☆ Bolt', '24', 'Laserforce Ventura', '5\'11"', '120', '15', '-', '-', '72.4', '80.6'],
      ]);
      const link = q('#roster-bench-table app-player-cell a');
      expect(link?.getAttribute('href')).toBe('/leagues/1/players/26');
    });

    it('reads "No bench players." across every column when empty', async () => {
      await open(PATH, teamRoster({ bench: [] }));
      expect(rows('roster-bench-table')).toEqual([['No bench players.']]);
      expect(q('#roster-bench-table tbody td')?.getAttribute('colspan')).toBe('10');
      expect(rows('roster-starting-table')).toHaveLength(1);
    });
  });
});
