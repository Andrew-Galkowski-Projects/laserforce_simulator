import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import {
  HttpTestingController,
  TestRequest,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { MATERIAL_ANIMATIONS } from '@angular/material/core';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { environment } from '../../../environments/environment';
import { LeagueDashboard } from '../../api/leagues.models';
import { SELF_INSERT, USER, signIn } from '../../api/testing/auth-fixtures';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';
import { User } from '../../auth/user';
import {
  deleteSummary,
  leagueDashboard,
  leagueNav,
  leaderRow,
  season,
  seasonDashboard,
  standingsSnippetRow,
} from '../testing/league-fixtures';

const BASE = environment.apiBaseUrl;

describe('League dashboard (/leagues/:id, spec #409 F10)', () => {
  let http: HttpTestingController;
  let harness: RouterTestingHarness;

  async function setUp(user: User = USER): Promise<void> {
    TestBed.configureTestingModule({
      providers: [
        provideRouter(routes),
        provideHttpClient(withInterceptors([authInterceptor])),
        provideHttpClientTesting(),
        { provide: MATERIAL_ANIMATIONS, useValue: { animationsDisabled: true } },
      ],
    });
    http = TestBed.inject(HttpTestingController);
    await signIn(user);
    harness = await RouterTestingHarness.create();
  }

  afterEach(() => {
    document.querySelector('.cdk-overlay-container')?.remove();
    // The League layout's nav load is its own spec's business.
    http.match((req) => req.url.endsWith('/nav/'));
    http.verify();
  });

  const page = (): HTMLElement =>
    (harness.routeNativeElement as HTMLElement).querySelector(
      'app-league-dashboard',
    ) as HTMLElement;
  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const routerUrl = (): string => TestBed.inject(Router).url;
  const q = (selector: string): HTMLElement | null => page().querySelector<HTMLElement>(selector);
  const buttonIn = (el: Element | null, label: string): HTMLButtonElement | undefined =>
    [...(el?.querySelectorAll('button') ?? [])].find((b) => text(b) === label);
  const linkIn = (el: Element | null, label: string): HTMLAnchorElement | undefined =>
    [...(el?.querySelectorAll('a') ?? [])].find((a) => text(a) === label);

  /** A two-sided card row (name side, value side) as "name | value". */
  const rowText = (row: Element): string => [...row.children].map(text).join(' | ');

  function dashboardRequest(id = 1): TestRequest {
    return http.expectOne(`${BASE}/leagues/${id}/dashboard/`);
  }

  function fail(status: number, message: string, code = 'x') {
    return [
      { errors: [{ code, message, field: null }], meta: {} },
      { status, statusText: 'Error' },
    ] as const;
  }

  async function open(body: LeagueDashboard = leagueDashboard(), user: User = USER) {
    await setUp(user);
    await harness.navigateByUrl(`/leagues/${body.league.id}`);
    http
      .match(`${BASE}/leagues/${body.league.id}/nav/`)
      .forEach((r) => r.flush({ data: leagueNav(), meta: {} }));
    dashboardRequest(body.league.id).flush({ data: body, meta: {} });
    await harness.fixture.whenStable();
  }

  it("loads the dashboard with GET leagues/{id}/dashboard/ and shows the League's name", async () => {
    await setUp();
    await harness.navigateByUrl('/leagues/1');
    const req = dashboardRequest();
    expect(req.request.method).toBe('GET');
    req.flush({ data: leagueDashboard(), meta: {} });
    await harness.fixture.whenStable();
    expect(text(q('h1'))).toBe('Metro League');
  });

  it.each([
    ['none', 'No Season'],
    ['draft', 'Draft'],
    ['active', 'Active'],
    ['completed', 'Completed'],
  ] as const)('shows the %s Season mode as a "%s" badge', async (mode, label) => {
    await open(leagueDashboard({ season_mode: mode }));
    const badge = q('.dashboard__mode');
    expect(text(badge)).toBe(label);
    expect(badge?.dataset['mode']).toBe(mode);
  });

  it('shows the displayed Season name as Soon, never a link (I1)', async () => {
    await open();
    const season = q('.dashboard__season');
    expect(text(season)).toBe('Season 1 Soon');
    expect(season?.querySelector('span.soon')).not.toBeNull();
    expect(season?.querySelector('a')).toBeNull();
  });

  it('shows no Season name when there is no displayed Season', async () => {
    await open(leagueDashboard({ displayed_season: null, season_mode: 'none' }));
    expect(q('.dashboard__season')).toBeNull();
  });

  it('says "You manage <team>." in a career League', async () => {
    await open();
    expect(text(q('.dashboard__managed'))).toBe('You manage Red Raiders.');
    expect(text(q('.dashboard__managed strong'))).toBe('Red Raiders');
  });

  it('has no "You manage" line without a managed team', async () => {
    await open(leagueDashboard({ mode: 'sandbox', managed_team: null }));
    expect(q('.dashboard__managed')).toBeNull();
  });

  it('shows the map config label', async () => {
    await open(leagueDashboard({ map_config_label: 'Map: Rotate by matchday (3 maps)' }));
    expect(text(q('.dashboard__map-config'))).toBe('Map: Rotate by matchday (3 maps)');
  });

  it('shows the no-Season notice only when the League has no Season', async () => {
    await open(leagueDashboard({ displayed_season: null, season_mode: 'none' }));
    expect(text(q('.dashboard__no-season'))).toBe('No Season exists for this League yet.');
  });

  it.each(['draft', 'active', 'completed'] as const)(
    'has no no-Season notice on a %s Season',
    async (mode) => {
      await open(leagueDashboard({ season_mode: mode }));
      expect(q('.dashboard__no-season')).toBeNull();
    },
  );

  describe('load (I2)', () => {
    async function failLoad(status: number, message: string): Promise<void> {
      await setUp();
      await harness.navigateByUrl('/leagues/1');
      http.match(`${BASE}/leagues/1/nav/`).forEach((r) => r.flush({ data: leagueNav(), meta: {} }));
      dashboardRequest().flush(...fail(status, message));
      await harness.fixture.whenStable();
    }

    it('shows not-found for a 404, with a link back to the Leagues', async () => {
      await failLoad(404, 'Not found.');
      expect(text(q('h1'))).toBe('League not found');
      expect(linkIn(page(), '← Back to Leagues')?.getAttribute('href')).toBe('/leagues');
      expect(q('[role="alert"]')).toBeNull();
    });

    it('shows another failure in the error box, and Retry loads the dashboard again', async () => {
      await failLoad(500, 'Database is down.');
      expect(text(q('[role="alert"] span'))).toBe('Database is down.');
      expect(text(q('h1'))).not.toBe('League not found');

      buttonIn(q('[role="alert"]'), 'Retry')?.click();
      await harness.fixture.whenStable();
      dashboardRequest().flush({ data: leagueDashboard(), meta: {} });
      await harness.fixture.whenStable();
      expect(q('[role="alert"]')).toBeNull();
      expect(text(q('h1'))).toBe('Metro League');
    });

    it('loads the new League when the :id changes', async () => {
      await open();
      await harness.navigateByUrl('/leagues/2');
      http.match(`${BASE}/leagues/2/nav/`).forEach((r) => r.flush({ data: leagueNav(), meta: {} }));
      dashboardRequest(2).flush({
        data: leagueDashboard({ league: { id: 2, name: 'Rival' } }),
        meta: {},
      });
      await harness.fixture.whenStable();
      expect(text(q('h1'))).toBe('Rival');
    });
  });

  describe('Start Season (draft)', () => {
    const draft = () => leagueDashboard({ season_mode: 'draft' });
    const startButton = () => buttonIn(q('.dashboard__start'), 'Start Season');

    function startRequest(): TestRequest {
      return http.expectOne(`${BASE}/seasons/11/start/`);
    }

    it('offers Start Season with the onboarding text on a draft Season', async () => {
      await open(draft());
      expect(text(q('.dashboard__start span'))).toBe(
        "This Season hasn't started yet. Start it to lock in the teams and schedule, then " +
          'play games from the Play menu.',
      );
      expect(startButton()).toBeDefined();
    });

    it.each(['none', 'active', 'completed'] as const)(
      'has no Start Season on a %s Season',
      async (mode) => {
        await open(leagueDashboard({ season_mode: mode }));
        expect(q('.dashboard__start')).toBeNull();
      },
    );

    it.each([
      [1, 'Season 1 started — 1 Round scheduled.'],
      [2, 'Season 1 started — 2 Rounds scheduled.'],
      [56, 'Season 1 started — 56 Rounds scheduled.'],
    ])(
      'starts the Season, says how many Rounds (%i) were scheduled, then reloads',
      async (n, message) => {
        await open(draft());
        startButton()?.click();
        await harness.fixture.whenStable();
        const req = startRequest();
        expect(req.request.method).toBe('POST');
        req.flush({ data: seasonDashboard({ round_count_total: n }), meta: {} });
        await harness.fixture.whenStable();
        expect(document.body.textContent).toContain(message);

        dashboardRequest().flush({ data: leagueDashboard(), meta: {} });
        await harness.fixture.whenStable();
        expect(text(q('.dashboard__mode'))).toBe('Active');
        expect(q('.dashboard__start')).toBeNull();
      },
    );

    it('disables Start Season while the start runs (I3)', async () => {
      await open(draft());
      startButton()?.click();
      await harness.fixture.whenStable();
      expect(startButton()?.disabled).toBe(true);
      startRequest().flush({ data: seasonDashboard(), meta: {} });
      await harness.fixture.whenStable();
      dashboardRequest().flush({ data: draft(), meta: {} });
      await harness.fixture.whenStable();
      expect(startButton()?.disabled).toBe(false);
    });

    it('shows a failed start in the error box and keeps the dashboard (I3)', async () => {
      await open(draft());
      startButton()?.click();
      await harness.fixture.whenStable();
      startRequest().flush(...fail(409, 'This Season is not a draft.', 'season_not_draft'));
      await harness.fixture.whenStable();
      expect(text(q('.dashboard__write-error'))).toBe('This Season is not a draft.');
      expect(q('.dashboard__write-error')?.getAttribute('role')).toBe('alert');
      expect(text(q('h1'))).toBe('Metro League');
      expect(text(q('.dashboard__mode'))).toBe('Draft');
      expect(startButton()?.disabled).toBe(false);
    });
  });

  describe('Start next Season / Choose new team (completed Season)', () => {
    const completed = (awaiting = false) =>
      leagueDashboard({
        season_mode: 'completed',
        next_fixture: null,
        manager_awaiting_reassignment: awaiting,
      });
    const nextButton = () => buttonIn(page(), 'Start next Season');
    const chooseLink = () => linkIn(page(), 'Choose new team');

    function nextRequest(): TestRequest {
      return http.expectOne(`${BASE}/leagues/1/next-season/`);
    }

    it('offers Start next Season, and no Choose new team, when the Manager is not awaiting', async () => {
      await open(completed());
      expect(nextButton()).toBeDefined();
      expect(chooseLink()).toBeUndefined();
    });

    it('offers Choose new team, and no Start next Season, when awaiting reassignment', async () => {
      await open(completed(true));
      expect(nextButton()).toBeUndefined();
      expect(chooseLink()?.getAttribute('href')).toBe('/leagues/1/new-team');
    });

    it.each(['none', 'draft', 'active'] as const)('offers neither on a %s Season', async (mode) => {
      await open(leagueDashboard({ season_mode: mode }));
      expect(nextButton()).toBeUndefined();
      expect(chooseLink()).toBeUndefined();
    });

    it('starts the next Season with POST leagues/{id}/next-season/, then reloads', async () => {
      await open(completed());
      nextButton()?.click();
      await harness.fixture.whenStable();
      const req = nextRequest();
      expect(req.request.method).toBe('POST');
      expect(nextButton()?.disabled).toBe(true);
      req.flush(
        { data: season({ state: 'active' }), meta: {} },
        { status: 201, statusText: 'Created' },
      );
      await harness.fixture.whenStable();

      dashboardRequest().flush({
        data: leagueDashboard({ displayed_season: { id: 12, name: 'Season 2' } }),
        meta: {},
      });
      await harness.fixture.whenStable();
      expect(text(q('.dashboard__season'))).toBe('Season 2 Soon');
      expect(nextButton()).toBeUndefined();
    });

    it('goes to the new-team picker on a 409 manager_fired', async () => {
      await open(completed());
      nextButton()?.click();
      await harness.fixture.whenStable();
      nextRequest().flush(
        ...fail(
          409,
          'The Manager was fired. Choose a new team before starting the next Season.',
          'manager_fired',
        ),
      );
      await harness.fixture.whenStable();
      http.match((req) => req.url.endsWith('/new-team-candidates/'));
      expect(routerUrl()).toBe('/leagues/1/new-team');
    });

    it('shows another failure in the error box and keeps the dashboard (I3)', async () => {
      await open(completed());
      nextButton()?.click();
      await harness.fixture.whenStable();
      nextRequest().flush(
        ...fail(409, 'The League already has a Season in progress.', 'season_in_progress'),
      );
      await harness.fixture.whenStable();
      expect(text(q('.dashboard__write-error'))).toBe(
        'The League already has a Season in progress.',
      );
      expect(routerUrl()).toBe('/leagues/1');
      expect(text(q('.dashboard__mode'))).toBe('Completed');
      expect(nextButton()?.disabled).toBe(false);
    });
  });

  describe('Delete (F6)', () => {
    const deleteButton = () => buttonIn(page(), 'Delete League');

    it('offers Delete League only on a career League', async () => {
      await open();
      expect(deleteButton()).toBeDefined();
    });

    it.each(['sandbox', 'multiplayer'] as const)(
      'offers no Delete on a %s League',
      async (mode) => {
        await open(leagueDashboard({ mode, managed_team: null }));
        expect(deleteButton()).toBeUndefined();
      },
    );

    it('deletes through the shared flow and lands on /leagues', async () => {
      await open();
      deleteButton()?.click();
      await harness.fixture.whenStable();
      expect(deleteButton()?.disabled).toBe(true);
      http
        .expectOne(`${BASE}/leagues/1/delete-summary/`)
        .flush({ data: deleteSummary(), meta: {} });
      await harness.fixture.whenStable();
      const dialog = document.querySelector('mat-dialog-container');
      expect(text(dialog?.querySelector('h2'))).toBe('Delete "Metro League"?');
      buttonIn(dialog, 'Delete permanently')?.click();
      await harness.fixture.whenStable();
      http.expectOne(`${BASE}/leagues/1/`).flush(null, { status: 204, statusText: 'No Content' });
      await harness.fixture.whenStable();
      http.match((req) => req.url === `${BASE}/leagues/`);
      expect(document.body.textContent).toContain('League "Metro League" deleted.');
      expect(routerUrl()).toBe('/leagues');
    });

    it('shows a failed delete in the error box and keeps the dashboard (I3)', async () => {
      await open();
      deleteButton()?.click();
      await harness.fixture.whenStable();
      http.expectOne(`${BASE}/leagues/1/delete-summary/`).flush(...fail(500, 'Database is down.'));
      await harness.fixture.whenStable();
      expect(text(q('.dashboard__write-error'))).toBe('Database is down.');
      expect(text(q('h1'))).toBe('Metro League');
      expect(deleteButton()?.disabled).toBe(false);
    });
  });

  describe('links', () => {
    it('shows "View bracket" as Soon when the Season has a playoff Tournament (I1)', async () => {
      await open(leagueDashboard({ playoff_tournament_id: 44 }));
      const bracket = q('.dashboard__bracket');
      expect(text(bracket)).toBe('View bracket Soon');
      expect(bracket?.querySelector('span.soon')).not.toBeNull();
      expect(bracket?.querySelector('a')).toBeNull();
    });

    it('has no "View bracket" without a playoff Tournament', async () => {
      await open(leagueDashboard({ playoff_tournament_id: null }));
      expect(q('.dashboard__bracket')).toBeNull();
    });

    it('shows "Manage Conferences" as Soon on a draft Season (I1)', async () => {
      await open(leagueDashboard({ season_mode: 'draft' }));
      const conferences = q('.dashboard__conferences');
      expect(text(conferences)).toBe('Manage Conferences Soon');
      expect(conferences?.querySelector('a')).toBeNull();
    });

    it.each(['active', 'completed'] as const)(
      'has no "Manage Conferences" on a %s Season',
      async (mode) => {
        await open(leagueDashboard({ season_mode: mode }));
        expect(q('.dashboard__conferences')).toBeNull();
      },
    );

    it('links "View past evaluations" to History when the League has evaluations', async () => {
      await open(leagueDashboard({ has_owner_evaluations: true }));
      const link = linkIn(page(), 'View past evaluations');
      expect(link?.getAttribute('href')).toBe('/leagues/1/history');
    });

    it('has no "View past evaluations" without evaluations', async () => {
      await open(leagueDashboard({ has_owner_evaluations: false }));
      expect(linkIn(page(), 'View past evaluations')).toBeUndefined();
    });
  });

  describe('standings snippet', () => {
    const snippetRows = (): HTMLElement[] => [
      ...page().querySelectorAll<HTMLElement>('.dashboard__standings li'),
    ];

    it('lists "Top standings" rows as "<rank>. <team>" and "<points> pts"', async () => {
      await open(
        leagueDashboard({
          standings_snippet: [
            standingsSnippetRow(),
            standingsSnippetRow({
              team_id: 5,
              rank: 2,
              league_points: 7,
              team: { id: 5, name: 'Blue Bolts' },
            }),
            standingsSnippetRow({ team_id: 6, rank: 3, league_points: 4, team: null }),
          ],
        }),
      );
      expect(text(q('.dashboard__standings h2'))).toBe('Top standings');
      expect(snippetRows().map(rowText)).toEqual([
        '1. Red Raiders You | 9 pts',
        '2. Blue Bolts | 7 pts',
        '3. (unknown) | 4 pts',
      ]);
    });

    it('marks "You" on the row whose team_id is the managed team, and only it (I4)', async () => {
      await open(
        leagueDashboard({
          managed_team: { id: 5, name: 'Blue Bolts' },
          standings_snippet: [
            standingsSnippetRow(),
            standingsSnippetRow({ team_id: 5, rank: 2, team: { id: 5, name: 'Blue Bolts' } }),
          ],
        }),
      );
      const marked = snippetRows().filter((r) => r.querySelector('.you'));
      expect(marked.map(rowText)).toEqual(['2. Blue Bolts You | 9 pts']);
      expect(snippetRows()[1].classList).toContain('dashboard__row--you');
    });

    it('marks no row without a managed team', async () => {
      await open(leagueDashboard({ mode: 'sandbox', managed_team: null }));
      expect(page().querySelector('.dashboard__standings .you')).toBeNull();
    });

    it.each(['draft', 'active', 'completed'] as const)('shows on a %s Season', async (mode) => {
      await open(leagueDashboard({ season_mode: mode }));
      expect(q('.dashboard__standings')).not.toBeNull();
    });

    it('is not shown when the League has no Season', async () => {
      await open(leagueDashboard({ season_mode: 'none', displayed_season: null }));
      expect(q('.dashboard__standings')).toBeNull();
    });
  });

  describe('member night', () => {
    it('shows "Member night: <played> / <total> games played" with a Soon View results', async () => {
      await open(leagueDashboard({ member_night_progress: { played: 3, total: 10 } }));
      const card = q('.dashboard__member-night');
      expect(text(card)).toBe('Member night: 3 / 10 games played View results Soon');
      expect(card?.querySelector('span.soon')).not.toBeNull();
      expect(card?.querySelector('a')).toBeNull();
    });

    it('is not shown without member-night progress', async () => {
      await open(leagueDashboard({ member_night_progress: null }));
      expect(q('.dashboard__member-night')).toBeNull();
    });
  });

  describe('play progress (active or completed Season)', () => {
    it('shows the next round: matchday, Round, the two teams and the date', async () => {
      await open();
      const card = q('.dashboard__next-round');
      expect(text(card?.querySelector('h2'))).toBe('Next round');
      expect([...(card?.querySelectorAll('p') ?? [])].map(text)).toEqual([
        'Matchday 5 · Round 1',
        'Red Raiders vs Blue Bolts',
        '2026-11-24',
      ]);
    });

    it('says "All fixtures played" when no fixture is left', async () => {
      await open(leagueDashboard({ season_mode: 'completed', next_fixture: null }));
      expect(text(q('.dashboard__next-round p'))).toBe('All fixtures played');
    });

    it('shows "Rounds played: <completed> / <total>"', async () => {
      await open(leagueDashboard({ round_count_completed: 8, round_count_total: 56 }));
      expect(text(q('.dashboard__round-count'))).toBe('Rounds played: 8 / 56');
    });

    it('shows the three leader cards, each value to two places', async () => {
      await open(
        leagueDashboard({
          leaders_points: [
            leaderRow({ player_id: 25, player_name: 'Ace', value: 9120.5 }),
            leaderRow({ player_id: 26, player_name: 'Bolt', value: 8000, rank: 2 }),
          ],
          leaders_tags: [leaderRow({ player_id: 27, player_name: 'Cy', value: 41.257 })],
          leaders_ratio: [leaderRow({ player_id: 28, player_name: 'Di', value: 1.8 })],
        }),
      );
      const cards = [...page().querySelectorAll('.dashboard__leaders')];
      expect(cards.map((c) => text(c.querySelector('h2')))).toEqual([
        'Points per game',
        'Tags per game',
        'Tag ratio',
      ]);
      expect(cards.map((c) => [...c.querySelectorAll('li')].map(rowText))).toEqual([
        ['Ace | 9120.50', 'Bolt | 8000.00'],
        ['Cy | 41.26'],
        ['Di | 1.80'],
      ]);
    });

    it("links each leader's name to /players/:id/stats", async () => {
      await open(leagueDashboard({ leaders_points: [leaderRow({ player_id: 25 })] }));
      const link = q('.dashboard__leaders li a');
      expect(link?.getAttribute('href')).toBe('/players/25/stats');
    });

    it('marks "You" on the signed-in Account\'s Self-insert leader rows, and only them (I4)', async () => {
      await open(
        leagueDashboard({
          leaders_points: [
            leaderRow({ player_id: 25, player_name: 'Ace' }),
            leaderRow({ player_id: 9, player_name: 'Dan', rank: 2 }),
          ],
          leaders_tags: [leaderRow({ player_id: 25, player_name: 'Ace' })],
          leaders_ratio: [leaderRow({ player_id: 9, player_name: 'Dan' })],
        }),
        { ...USER, self_insert: SELF_INSERT },
      );
      const marked = [...page().querySelectorAll('.dashboard__leaders li')].filter((r) =>
        r.querySelector('.you'),
      );
      expect(marked.map((r) => text(r.querySelector('a')))).toEqual(['Dan', 'Dan']);
    });

    it('marks no leader without a Self-insert', async () => {
      await open(leagueDashboard({ leaders_points: [leaderRow({ player_id: 9 })] }));
      expect(page().querySelector('.dashboard__leaders .you')).toBeNull();
    });

    it('links "View all leaders" to League Leaders (spec #417)', async () => {
      await open();
      const all = q('.dashboard__all-leaders');
      expect(text(all)).toBe('View all leaders');
      expect(all?.querySelector('a')?.getAttribute('href')).toBe('/leagues/1/stats/league-leaders');
      expect(all?.querySelector('.soon')).toBeNull();
    });

    it.each(['active', 'completed'] as const)('shows on a %s Season', async (mode) => {
      await open(leagueDashboard({ season_mode: mode }));
      expect(q('.dashboard__next-round')).not.toBeNull();
      expect(q('.dashboard__round-count')).not.toBeNull();
      expect(page().querySelectorAll('.dashboard__leaders')).toHaveLength(3);
      expect(q('.dashboard__all-leaders')).not.toBeNull();
    });

    it.each(['none', 'draft'] as const)('is not shown on a %s Season', async (mode) => {
      await open(leagueDashboard({ season_mode: mode }));
      expect(q('.dashboard__next-round')).toBeNull();
      expect(q('.dashboard__round-count')).toBeNull();
      expect(q('.dashboard__leaders')).toBeNull();
      expect(q('.dashboard__all-leaders')).toBeNull();
    });
  });
});
