import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import {
  HttpTestingController,
  TestRequest,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { MATERIAL_ANIMATIONS } from '@angular/material/core';
import { provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { environment } from '../../../../environments/environment';
import { SeasonSchedule } from '../../../api/seasons.models';
import { signIn } from '../../../api/testing/auth-fixtures';
import { routes } from '../../../app.routes';
import { authInterceptor } from '../../../auth/auth.interceptor';
import { failure } from '../../testing/league-stats-fixtures';
import {
  memberNightGame,
  scheduleFixture,
  seasonSchedule,
  seasonUrl,
} from '../testing/season-fixtures';
import { releaseSeasonLayout } from '../testing/season-loads';
import { releasePlayControls } from '../../testing/leagues-loads';

const BASE = environment.apiBaseUrl;

/** Two member-night phases: phase 1 one completed game, phase 2 one not yet played. */
function withMemberNights(): SeasonSchedule {
  return seasonSchedule({
    member_night_games: [
      { ordinal: 1, games: [memberNightGame()] },
      {
        ordinal: 2,
        games: [
          memberNightGame({
            match_id: 910,
            team_a_name: 'MN Arena G2 A',
            team_b_name: 'MN Arena G2 B',
            is_completed: false,
            red_points: null,
            blue_points: null,
            winner_name: null,
            rounds: [],
          }),
        ],
      },
    ],
  });
}

describe('Season Schedule (/leagues/:id/seasons/:seasonId/schedule, spec #444)', () => {
  let http: HttpTestingController;
  let harness: RouterTestingHarness;
  let scrolled: Element[];
  const realScrollIntoView = Element.prototype.scrollIntoView;

  beforeEach(() => {
    scrolled = [];
    // jsdom has no layout: record which element asked to be scrolled into view.
    Element.prototype.scrollIntoView = function (this: Element) {
      scrolled.push(this);
    };
  });

  afterEach(() => {
    Element.prototype.scrollIntoView = realScrollIntoView;
    http.match((req) => req.url.endsWith('/nav/'));
    releasePlayControls();
    http.verify();
  });

  async function setUp(): Promise<void> {
    TestBed.configureTestingModule({
      providers: [
        provideRouter(routes),
        provideHttpClient(withInterceptors([authInterceptor])),
        provideHttpClientTesting(),
        { provide: MATERIAL_ANIMATIONS, useValue: { animationsDisabled: true } },
      ],
    });
    http = TestBed.inject(HttpTestingController);
    await signIn();
    harness = await RouterTestingHarness.create();
  }

  const page = (): HTMLElement =>
    (harness.routeNativeElement as HTMLElement).querySelector('app-season-schedule') as HTMLElement;
  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const q = (selector: string): HTMLElement | null => page().querySelector<HTMLElement>(selector);
  const qa = (selector: string, root: Element = page()): HTMLElement[] => [
    ...root.querySelectorAll<HTMLElement>(selector),
  ];
  const linkIn = (el: Element | null, label: string): HTMLAnchorElement | undefined =>
    [...(el?.querySelectorAll('a') ?? [])].find((a) => text(a) === label);
  const cells = (row: Element): string[] => [...row.children].map(text);

  function scheduleRequest(): TestRequest {
    const req = http.expectOne(`${BASE}/seasons/11/schedule/`);
    expect(req.request.method).toBe('GET');
    return req;
  }

  /** Lands on Season 11's Schedule (with `#fragment` when given) and answers its GET. */
  async function open(body: SeasonSchedule = seasonSchedule(), fragment = ''): Promise<void> {
    await setUp();
    await harness.navigateByUrl(seasonUrl('schedule') + (fragment ? `#${fragment}` : ''));
    await releaseSeasonLayout(harness);
    scheduleRequest().flush({ data: body, meta: {} });
    await harness.fixture.whenStable();
  }

  describe('header', () => {
    it('shows the Season breadcrumb above the h1 "Schedule"', async () => {
      await open();
      const crumb = q('nav[aria-label="Breadcrumb"]');
      expect(text(crumb)).toBe('Metro League › Season 1');
      expect(text(q('h1'))).toBe('Schedule');
    });

    it('links "Standings" to the Season\'s Standings', async () => {
      await open();
      expect(linkIn(page(), 'Standings')?.getAttribute('href')).toBe(
        '/leagues/1/seasons/11/standings',
      );
    });
  });

  it('says no fixtures are scheduled when there are no matchdays (I6)', async () => {
    await open(seasonSchedule({ matchdays: [] }));
    expect(text(q('#season-schedule-empty'))).toBe(
      'No fixtures scheduled — at least 2 enrolled teams are required.',
    );
    expect(q('table')).toBeNull();
  });

  describe('matchdays', () => {
    it('heads each matchday "Matchday N" with a muted "— <date>"', async () => {
      await open(
        seasonSchedule({
          matchdays: [
            { matchday: 1, date: '2026-09-01', fixtures: [scheduleFixture()] },
            {
              matchday: 2,
              date: '2026-09-08',
              fixtures: [scheduleFixture({ matchday: 2, date: '2026-09-08' })],
            },
          ],
        }),
      );
      const headings = qa('.season-schedule__matchday h3');
      expect(headings.map(text)).toEqual(['Matchday 1 — 2026-09-01', 'Matchday 2 — 2026-09-08']);
      expect(text(headings[0].querySelector('.season-schedule__muted'))).toBe('— 2026-09-01');
    });

    it('has the columns "Round", "Home", "Away", "Result" (I6)', async () => {
      await open();
      expect(qa('.season-schedule__matchday th').map(text)).toEqual([
        'Round',
        'Home',
        'Away',
        'Result',
      ]);
    });

    it('shows a played fixture as R<n>, two Team links and the result linked to its Round (I5)', async () => {
      await open();
      const row = qa('.season-schedule__matchday tbody tr')[0];
      expect(cells(row)).toEqual(['R1', 'Red Raiders', 'Blue Bolts', '41000–38000']);
      expect(linkIn(row, 'Red Raiders')?.getAttribute('href')).toBe('/teams/3');
      expect(linkIn(row, 'Blue Bolts')?.getAttribute('href')).toBe('/teams/5');
      expect(linkIn(row, '41000–38000')?.getAttribute('href')).toBe('/rounds/501');
    });

    it('shows a played result as plain text when it has no Round', async () => {
      await open(
        seasonSchedule({
          matchdays: [
            {
              matchday: 1,
              date: '2026-09-01',
              fixtures: [scheduleFixture({ game_round_id: null })],
            },
          ],
        }),
      );
      const row = qa('.season-schedule__matchday tbody tr')[0];
      expect(cells(row)[3]).toBe('41000–38000');
      expect(row.children[3].querySelector('a')).toBeNull();
    });

    it('shows "Unplayed" for a fixture not yet played', async () => {
      await open(
        seasonSchedule({
          matchdays: [
            {
              matchday: 1,
              date: '2026-09-01',
              fixtures: [
                scheduleFixture({
                  round_number: 2,
                  played: false,
                  game_round_id: null,
                  red_score: null,
                  blue_score: null,
                }),
              ],
            },
          ],
        }),
      );
      const row = qa('.season-schedule__matchday tbody tr')[0];
      expect(cells(row)).toEqual(['R2', 'Red Raiders', 'Blue Bolts', 'Unplayed']);
      expect(row.children[3].querySelector('a')).toBeNull();
    });

    it('shows "(team <id>)" for a Team that no longer exists', async () => {
      await open(
        seasonSchedule({
          matchdays: [
            {
              matchday: 1,
              date: '2026-09-01',
              fixtures: [scheduleFixture({ team_a: null, team_b: null })],
            },
          ],
        }),
      );
      const row = qa('.season-schedule__matchday tbody tr')[0];
      expect(cells(row).slice(1, 3)).toEqual(['(team 3)', '(team 5)']);
      expect(row.children[1].querySelector('a')).toBeNull();
      expect(row.children[2].querySelector('a')).toBeNull();
    });
  });

  describe('member nights', () => {
    it('is not shown when the Season has no member-night games', async () => {
      await open(seasonSchedule({ member_night_games: [] }));
      expect(q('#member-nights')).toBeNull();
      expect(page().textContent).not.toContain('Member nights');
    });

    it('shows the section with its h2 and the not-counted line (I6)', async () => {
      await open(withMemberNights());
      const section = q('#member-nights');
      expect(text(section?.querySelector('h2'))).toBe('Member nights');
      expect(text(section?.querySelector('p'))).toBe(
        'Casual per-Site social games — not counted toward Standings.',
      );
    });

    it('heads each phase "Member night" with a muted "— phase <ordinal>"', async () => {
      await open(withMemberNights());
      const headings = qa('#member-nights h4');
      expect(headings.map(text)).toEqual(['Member night — phase 1', 'Member night — phase 2']);
      expect(text(headings[1].querySelector('.season-schedule__muted'))).toBe('— phase 2');
    });

    it('has the columns "Teams", "Result", "Rounds" (I6)', async () => {
      await open(withMemberNights());
      const firstTable = qa('#member-nights table')[0];
      expect(qa('th', firstTable).map(text)).toEqual(['Teams', 'Result', 'Rounds']);
    });

    it('shows a completed game: "A vs B", the score with the winner, and its Round links (I5)', async () => {
      await open(withMemberNights());
      const row = qa('#member-nights tbody tr')[0];
      expect(cells(row)).toEqual([
        'MN Arena G1 A vs MN Arena G1 B',
        '2–0 (MN Arena G1 A)',
        'R1 · R2',
      ]);
      expect(linkIn(row, 'R1')?.getAttribute('href')).toBe('/rounds/901');
      expect(linkIn(row, 'R2')?.getAttribute('href')).toBe('/rounds/902');
    });

    it('shows a completed game with no winner as the score alone', async () => {
      await open(
        seasonSchedule({
          member_night_games: [
            {
              ordinal: 1,
              games: [memberNightGame({ red_points: 1, blue_points: 1, winner_name: null })],
            },
          ],
        }),
      );
      expect(cells(qa('#member-nights tbody tr')[0])[1]).toBe('1–1');
    });

    it('shows "Not played" for a game not yet played', async () => {
      await open(withMemberNights());
      const row = qa('#member-nights tbody tr')[1];
      expect(cells(row)).toEqual(['MN Arena G2 A vs MN Arena G2 B', 'Not played', '']);
    });
  });

  describe('#member-nights fragment', () => {
    it('scrolls the member-nights section into view once it has rendered', async () => {
      await open(withMemberNights(), 'member-nights');
      expect(scrolled).toEqual([q('#member-nights')]);
    });

    it('does not scroll without the fragment', async () => {
      await open(withMemberNights());
      expect(scrolled).toEqual([]);
    });

    it('does not scroll when there are no member nights', async () => {
      await open(seasonSchedule(), 'member-nights');
      expect(scrolled).toEqual([]);
    });
  });

  describe('load (I2)', () => {
    it('shows a failed load with Retry, which re-sends only the schedule GET', async () => {
      await setUp();
      await harness.navigateByUrl(seasonUrl('schedule'));
      await releaseSeasonLayout(harness);
      scheduleRequest().flush(...failure(500, 'Database is down.'));
      await harness.fixture.whenStable();
      const alert = q('[role="alert"]');
      expect(text(alert?.querySelector('span'))).toBe('Database is down.');
      expect(q('table')).toBeNull();

      const retry = [...(alert?.querySelectorAll('button') ?? [])].find((b) => text(b) === 'Retry');
      retry?.click();
      await harness.fixture.whenStable();
      expect(http.match(`${BASE}/seasons/11/`)).toHaveLength(0);
      scheduleRequest().flush({ data: seasonSchedule(), meta: {} });
      await harness.fixture.whenStable();
      expect(q('[role="alert"]')).toBeNull();
      expect(qa('.season-schedule__matchday tbody tr')).toHaveLength(1);
    });
  });
});
