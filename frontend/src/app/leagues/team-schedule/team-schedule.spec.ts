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
import { TeamSchedule } from '../../api/league-team.models';
import { LeagueNav } from '../../api/leagues.models';
import { signIn } from '../../api/testing/auth-fixtures';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';
import { leagueNav } from '../testing/league-fixtures';
import { failure } from '../testing/league-stats-fixtures';
import {
  memberNightAppearance,
  teamSchedule,
  withTeamOptions,
} from '../testing/league-team-fixtures';
import { LeagueTeamSchedule } from './team-schedule';

const BASE = environment.apiBaseUrl;
const PATH = '/leagues/1/team/schedule';
const scheduleUrl = (team: number): string => `${BASE}/leagues/1/teams/${team}/schedule/`;

describe('Team Schedule (/leagues/:id/team/schedule, spec #428)', () => {
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
    page().querySelector<HTMLElement>(`app-league-team-schedule ${selector}`);
  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';

  async function navigate(path = PATH, nav: LeagueNav = leagueNav()): Promise<void> {
    await harness.navigateByUrl(path);
    http.expectOne(`${BASE}/leagues/1/nav/`).flush({ data: nav, meta: {} });
    await harness.fixture.whenStable();
  }

  async function answer(team = 3, data: TeamSchedule = teamSchedule()): Promise<TestRequest> {
    const req = http.expectOne(scheduleUrl(team));
    req.flush(withTeamOptions(data));
    await harness.fixture.whenStable();
    return req;
  }

  async function open(path = PATH, data: TeamSchedule = teamSchedule()): Promise<void> {
    await navigate(path);
    await answer(3, data);
  }

  it("asks for the nav's schedule Team when no ?team= is given", async () => {
    await navigate();
    const req = await answer(3);
    expect(req.request.method).toBe('GET');
    expect(harness.fixture.debugElement.query(By.directive(LeagueTeamSchedule))).not.toBeNull();
  });

  it('heads the page "<Team> — Schedule", the Season name on a muted line under it', async () => {
    await open();
    expect(text(q('h1'))).toBe('Red Raiders — Schedule');
    expect(text(q('#team-schedule-season'))).toBe('Season 1');
  });

  const headers = (table: string): string[] =>
    [...page().querySelectorAll(`#${table} th`)].map((th) => text(th));
  const cells = (row: string): string[] =>
    [...page().querySelectorAll(`#${row} td`)].map((td) => text(td));

  describe('Upcoming Games', () => {
    it('lists Matchday "<matchday> · R<round>", Date and Match "(R) <red> VS (B) <blue>"', async () => {
      await open();
      expect(text(q('#team-schedule-upcoming-section h2'))).toBe('Upcoming Games');
      expect(headers('team-schedule-upcoming-list')).toEqual(['Matchday', 'Date', 'Match']);
      expect(cells('team-schedule-upcoming-row-2-1')).toEqual([
        '2 · R1',
        '2026-10-12',
        '(R) Red Raiders VS (B) Blue Bolts',
      ]);
    });

    it('shows "No upcoming games" in place of the table when there are none', async () => {
      await open(PATH, teamSchedule({ upcoming: [] }));
      expect(text(q('#team-schedule-upcoming-empty'))).toBe('No upcoming games');
      expect(q('#team-schedule-upcoming-list')).toBeNull();
    });
  });

  describe('Completed Games', () => {
    it('lists Matchday, Date, Match, Score "<red> — <blue>", Result and a Round link', async () => {
      await open();
      expect(text(q('#team-schedule-completed-section h2'))).toBe('Completed Games');
      expect(headers('team-schedule-completed-list')).toEqual([
        'Matchday',
        'Date',
        'Match',
        'Score',
        'Result',
        '',
      ]);
      expect(cells('team-schedule-completed-row-70')).toEqual([
        '1 · R1',
        '2026-10-05',
        '(R) Red Raiders VS (B) Blue Bolts',
        '9120 — 8011',
        'W',
        'View Round detail',
      ]);
    });

    it('links "View Round detail" to the Angular Round detail /rounds/<game_round_id>', async () => {
      await open();
      const link = q('#team-schedule-completed-row-70 a') as HTMLAnchorElement;
      expect(text(link)).toBe('View Round detail');
      expect(link.getAttribute('href')).toBe('/rounds/70');
    });

    it('shows "No completed games" in place of the table when there are none', async () => {
      await open(PATH, teamSchedule({ completed: [] }));
      expect(text(q('#team-schedule-completed-empty'))).toBe('No completed games');
      expect(q('#team-schedule-completed-list')).toBeNull();
    });
  });

  describe('Member Night Appearances', () => {
    it('lists Game, Result "<red> — <blue> (<winner>)", Your players and R<n> links', async () => {
      await open(
        PATH,
        teamSchedule({
          member_night_appearances: [memberNightAppearance({ players: ['Ace', 'Bolt'] })],
        }),
      );
      expect(text(q('#team-schedule-member-nights h2'))).toBe('Member Night Appearances');
      expect(headers('team-schedule-member-night-list')).toEqual([
        'Game',
        'Result',
        'Your players',
        '',
      ]);
      expect(cells('team-schedule-member-night-row-90')).toEqual([
        'MN Ventura G1 A VS MN Ventura G1 B',
        '15000 — 14200 (MN Ventura G1 A)',
        'Ace, Bolt',
        'R1 · R2',
      ]);
    });

    it('links each R<n> to /rounds/<id>', async () => {
      await open();
      const links = [
        ...page().querySelectorAll<HTMLAnchorElement>('#team-schedule-member-night-row-90 a'),
      ];
      expect(links.map((a) => [text(a), a.getAttribute('href')])).toEqual([
        ['R1', '/rounds/91'],
        ['R2', '/rounds/92'],
      ]);
    });

    it('shows "Not played" as the Result of an unplayed game', async () => {
      await open(
        PATH,
        teamSchedule({
          member_night_appearances: [
            memberNightAppearance({
              is_completed: false,
              red_points: 0,
              blue_points: 0,
              winner_name: null,
            }),
          ],
        }),
      );
      expect(cells('team-schedule-member-night-row-90')[1]).toBe('Not played');
    });

    it('is absent when the Team has no member-night appearances', async () => {
      await open(PATH, teamSchedule({ member_night_appearances: [] }));
      expect(q('#team-schedule-member-nights')).toBeNull();
      expect(page().textContent).not.toContain('Member Night Appearances');
    });
  });

  describe('empty states and errors', () => {
    const NO_SEASON = 'No Season in this League yet — start a Season to view the team schedule.';
    const NO_TEAM = "No team available to display in this League's current Season.";
    const scheduleRequests = () => http.match((req) => req.url.includes('/schedule/'));

    it('I1: with no displayed Season, heads "<League> — Schedule", says so, and sends no request', async () => {
      await navigate(PATH, leagueNav({ displayed_season: null, schedule_team: null }));
      expect(scheduleRequests()).toEqual([]);
      expect(text(q('h1'))).toBe('Metro League — Schedule');
      expect(text(q('#team-schedule-empty-notice'))).toBe(NO_SEASON);
      expect(q('app-team-picker')).toBeNull();
    });

    it('I1: sends no request with no Season even when ?team= is given', async () => {
      await navigate(`${PATH}?team=3`, leagueNav({ displayed_season: null }));
      expect(scheduleRequests()).toEqual([]);
      expect(text(q('#team-schedule-empty-notice'))).toBe(NO_SEASON);
    });

    it('with no resolvable Team, says no team is available and sends no request', async () => {
      await navigate(PATH, leagueNav({ schedule_team: null }));
      expect(scheduleRequests()).toEqual([]);
      expect(text(q('h1'))).toBe('Metro League — Schedule');
      expect(text(q('#team-schedule-empty-notice'))).toBe(NO_TEAM);
      expect(q('#team-schedule-upcoming-section')).toBeNull();
    });

    it('a 404 on ?team= shows the no-team state', async () => {
      await navigate(`${PATH}?team=99`);
      http.expectOne(scheduleUrl(99)).flush(...failure(404, 'Not found.'));
      await harness.fixture.whenStable();
      expect(text(q('h1'))).toBe('Metro League — Schedule');
      expect(text(q('#team-schedule-empty-notice'))).toBe(NO_TEAM);
      expect(q('[role="alert"]')).toBeNull();
    });

    it('I2: any other failure shows the error box; Retry re-sends the request', async () => {
      await navigate();
      http.expectOne(scheduleUrl(3)).flush(...failure(500, 'Database is down.'));
      await harness.fixture.whenStable();
      const box = q('.stats-error');
      expect(box?.getAttribute('role')).toBe('alert');
      expect(text(box?.querySelector('span'))).toBe('Database is down.');
      expect(q('#team-schedule-upcoming-section')).toBeNull();

      (box?.querySelector('button') as HTMLButtonElement).click();
      await harness.fixture.whenStable();
      await answer(3);
      expect(q('.stats-error')).toBeNull();
      expect(text(q('h1'))).toBe('Red Raiders — Schedule');
    });
  });

  describe('I6: wide tables', () => {
    it('sit in a scrolling box', async () => {
      await open();
      for (const id of [
        'team-schedule-upcoming-list',
        'team-schedule-completed-list',
        'team-schedule-member-night-list',
      ]) {
        expect(q(`#${id}`)?.parentElement?.classList).toContain('stats-scroll');
      }
    });
  });

  describe('Team picker', () => {
    const picker = (): HTMLSelectElement =>
      q('app-team-picker select#stats-team-select') as HTMLSelectElement;
    const options = (): string[] => [...picker().options].map((o) => text(o));

    it('is the kit app-team-picker in a filters row below the h1', async () => {
      await open();
      expect(q('h1 ~ .stats-filters app-team-picker')).not.toBeNull();
    });

    it("asks for ?team='s Team", async () => {
      await navigate(`${PATH}?team=5`);
      await answer(5, teamSchedule({ team: { id: 5, name: 'Blue Bolts' } }));
      expect(text(q('h1'))).toBe('Blue Bolts — Schedule');
    });

    it('lists meta.team_options by name, labelled Team:, with the shown Team selected', async () => {
      await open();
      expect(text(q('label[for="stats-team-select"]'))).toBe('Team:');
      expect(options()).toEqual(['Blue Bolts', 'Red Raiders']);
      expect(picker().selectedOptions[0].textContent?.trim()).toBe('Red Raiders');
    });

    it('sets ?team= and loads the picked Team', async () => {
      await open();
      picker().value = '5';
      picker().dispatchEvent(new Event('change'));
      await harness.fixture.whenStable();
      await answer(5, teamSchedule({ team: { id: 5, name: 'Blue Bolts' } }));
      expect(TestBed.inject(Router).url).toBe(`${PATH}?team=5`);
      expect(text(q('h1'))).toBe('Blue Bolts — Schedule');
    });
  });
});
