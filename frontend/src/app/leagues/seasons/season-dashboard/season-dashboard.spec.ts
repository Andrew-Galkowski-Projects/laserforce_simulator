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
import { SeasonDashboard } from '../../../api/leagues.models';
import { SELF_INSERT, USER, signIn } from '../../../api/testing/auth-fixtures';
import { routes } from '../../../app.routes';
import { authInterceptor } from '../../../auth/auth.interceptor';
import { User } from '../../../auth/user';
import { leaderRow, seasonDashboard, standingsSnippetRow } from '../../testing/league-fixtures';
import { failure } from '../../testing/league-stats-fixtures';
import { season, seasonUrl } from '../testing/season-fixtures';
import { releaseSeasonLayout } from '../testing/season-loads';

const BASE = environment.apiBaseUrl;

describe('Season dashboard (/leagues/:id/seasons/:seasonId, spec #444)', () => {
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
    http.match((req) => req.url.endsWith('/nav/'));
    http.verify();
  });

  const page = (): HTMLElement =>
    (harness.routeNativeElement as HTMLElement).querySelector(
      'app-season-dashboard',
    ) as HTMLElement;
  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const q = (selector: string): HTMLElement | null => page().querySelector<HTMLElement>(selector);
  const linkIn = (el: Element | null, label: string): HTMLAnchorElement | undefined =>
    [...(el?.querySelectorAll('a') ?? [])].find((a) => text(a) === label);
  const href = (label: string): string | null | undefined =>
    linkIn(page(), label)?.getAttribute('href');
  const rowText = (row: Element): string => [...row.children].map(text).join(' | ');

  function dashboardRequest(): TestRequest {
    const req = http.expectOne(`${BASE}/seasons/11/dashboard/`);
    expect(req.request.method).toBe('GET');
    return req;
  }

  /** Lands on Season 11's dashboard (a Season in `mode`'s state) and answers its GET. */
  async function open(body: SeasonDashboard = seasonDashboard(), user: User = USER) {
    await setUp(user);
    await harness.navigateByUrl(seasonUrl());
    const state = body.season_mode === 'none' ? 'active' : body.season_mode;
    await releaseSeasonLayout(harness, season({ state }));
    dashboardRequest().flush({ data: body, meta: {} });
    await harness.fixture.whenStable();
  }

  it('loads GET seasons/{id}/dashboard/ and heads the page "<League> — <Season>"', async () => {
    await open();
    expect(text(q('h1'))).toBe('Metro League — Season 1');
  });

  it('has no breadcrumb (its h1 already names both)', async () => {
    await open();
    expect(page().querySelector('nav[aria-label="Breadcrumb"]')).toBeNull();
  });

  it.each([
    ['draft', 'Draft'],
    ['active', 'Active'],
    ['completed', 'Completed'],
  ] as const)('shows the %s state as a "%s" badge', async (mode, label) => {
    await open(seasonDashboard({ season_mode: mode }));
    const badge = q('.season-dashboard__state');
    expect(text(badge)).toBe(label);
    expect(badge?.dataset['state']).toBe(mode);
  });

  it('always links "View past evaluations" to the League History', async () => {
    await open();
    expect(href('View past evaluations')).toBe('/leagues/1/history');
  });

  it('links "View bracket" to the Playoffs for this Season when it has a bracket', async () => {
    await open(seasonDashboard({ playoff_tournament_id: 44 }));
    expect(href('View bracket')).toBe('/leagues/1/playoffs?season=11');
  });

  it('has no "View bracket" without a playoff Tournament', async () => {
    await open(seasonDashboard({ playoff_tournament_id: null }));
    expect(linkIn(page(), 'View bracket')).toBeUndefined();
  });

  it('shows the map configuration line', async () => {
    await open(seasonDashboard({ map_config_label: 'Map: Rotate by matchday (3 maps)' }));
    expect(text(q('.season-dashboard__map-config'))).toBe('Map: Rotate by matchday (3 maps)');
  });

  it('links "Awards" to the Season\'s awards', async () => {
    await open();
    expect(href('Awards')).toBe('/leagues/1/seasons/11/awards');
  });

  it('links "Manage Conferences" to the Conferences screen on a draft Season', async () => {
    await open(seasonDashboard({ season_mode: 'draft' }));
    expect(href('Manage Conferences')).toBe('/leagues/1/seasons/11/conferences');
  });

  it.each(['active', 'completed'] as const)(
    'has no "Manage Conferences" on a %s Season',
    async (mode) => {
      await open(seasonDashboard({ season_mode: mode }));
      expect(linkIn(page(), 'Manage Conferences')).toBeUndefined();
    },
  );

  it('has no play controls (ANG-06-19)', async () => {
    await open(seasonDashboard({ season_mode: 'draft' }));
    expect(page().querySelectorAll('button')).toHaveLength(0);
  });

  describe('Top standings', () => {
    it.each(['draft', 'active', 'completed'] as const)('shows on a %s Season', async (mode) => {
      await open(seasonDashboard({ season_mode: mode }));
      expect(text(q('.dashboard__standings h2'))).toBe('Top standings');
    });

    it('lists "<rank>. <team>" and "<points> pts", marking no row "You" (I4)', async () => {
      await open(
        seasonDashboard({
          standings_snippet: [
            standingsSnippetRow(),
            standingsSnippetRow({ team_id: 6, rank: 2, league_points: 4, team: null }),
          ],
        }),
      );
      const rows = [...page().querySelectorAll('.dashboard__standings li')];
      expect(rows.map(rowText)).toEqual(['1. Red Raiders | 9 pts', '2. (unknown) | 4 pts']);
      expect(page().querySelector('.dashboard__standings .you')).toBeNull();
    });
  });

  describe('member night', () => {
    it('shows the progress with "View results" linking to the Schedule\'s member nights', async () => {
      await open(seasonDashboard({ member_night_progress: { played: 3, total: 10 } }));
      const card = q('.dashboard__member-night');
      expect(text(card)).toBe('Member night: 3 / 10 games played View results');
      expect(linkIn(card, 'View results')?.getAttribute('href')).toBe(
        '/leagues/1/seasons/11/schedule#member-nights',
      );
    });

    it('is not shown without member-night progress', async () => {
      await open(seasonDashboard({ member_night_progress: null }));
      expect(q('.dashboard__member-night')).toBeNull();
    });
  });

  describe('play progress (active or completed Season)', () => {
    it.each(['active', 'completed'] as const)('shows on a %s Season', async (mode) => {
      await open(seasonDashboard({ season_mode: mode }));
      expect(q('.dashboard__next-round')).not.toBeNull();
      expect(q('.dashboard__round-count')).not.toBeNull();
      expect(page().querySelectorAll('.dashboard__leaders')).toHaveLength(3);
      expect(q('.dashboard__all-leaders')).not.toBeNull();
    });

    it('is not shown on a draft Season', async () => {
      await open(seasonDashboard({ season_mode: 'draft' }));
      expect(q('.dashboard__next-round')).toBeNull();
      expect(q('.dashboard__round-count')).toBeNull();
      expect(q('.dashboard__leaders')).toBeNull();
      expect(q('.dashboard__all-leaders')).toBeNull();
    });

    it('shows the next round, or "All fixtures played"', async () => {
      await open();
      expect([...(q('.dashboard__next-round')?.querySelectorAll('p') ?? [])].map(text)).toEqual([
        'Matchday 5 · Round 1',
        'Red Raiders vs Blue Bolts',
        '2026-11-24',
      ]);
    });

    it('says "All fixtures played" when no fixture is left', async () => {
      await open(seasonDashboard({ season_mode: 'completed', next_fixture: null }));
      expect(text(q('.dashboard__next-round p'))).toBe('All fixtures played');
    });

    it('shows "Rounds played: <completed> / <total>"', async () => {
      await open(seasonDashboard({ round_count_completed: 8, round_count_total: 56 }));
      expect(text(q('.dashboard__round-count'))).toBe('Rounds played: 8 / 56');
    });

    it('links "View all leaders" to League Leaders for this Season', async () => {
      await open();
      expect(href('View all leaders')).toBe('/leagues/1/stats/league-leaders?season=11');
    });

    it('lists the leader cards, each name a Player link, values to two places (I5)', async () => {
      await open(
        seasonDashboard({
          leaders_points: [leaderRow({ player_id: 25, player_name: 'Ace', value: 9120.5 })],
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
        ['Ace | 9120.50'],
        ['Cy | 41.26'],
        ['Di | 1.80'],
      ]);
      expect(q('.dashboard__leaders li a')?.getAttribute('href')).toBe('/players/25/stats');
    });

    it('marks "You" on the signed-in Account\'s Self-insert leader rows (I4)', async () => {
      await open(
        seasonDashboard({
          leaders_points: [
            leaderRow({ player_id: 25, player_name: 'Ace' }),
            leaderRow({ player_id: 9, player_name: 'Dan', rank: 2 }),
          ],
        }),
        { ...USER, self_insert: SELF_INSERT },
      );
      const marked = [...page().querySelectorAll('.dashboard__leaders li')].filter((r) =>
        r.querySelector('.you'),
      );
      expect(marked.map((r) => text(r.querySelector('a')))).toEqual(['Dan']);
    });
  });

  describe('load (I2)', () => {
    it('shows a failed load with Retry, which re-sends only the dashboard GET', async () => {
      await setUp();
      await harness.navigateByUrl(seasonUrl());
      await releaseSeasonLayout(harness);
      dashboardRequest().flush(...failure(500, 'Database is down.'));
      await harness.fixture.whenStable();
      const alert = q('[role="alert"]');
      expect(text(alert?.querySelector('span'))).toBe('Database is down.');
      expect(q('h1')).toBeNull();

      const retry = [...(alert?.querySelectorAll('button') ?? [])].find((b) => text(b) === 'Retry');
      retry?.click();
      await harness.fixture.whenStable();
      expect(http.match(`${BASE}/seasons/11/`)).toHaveLength(0);
      dashboardRequest().flush({ data: seasonDashboard(), meta: {} });
      await harness.fixture.whenStable();
      expect(q('[role="alert"]')).toBeNull();
      expect(text(q('h1'))).toBe('Metro League — Season 1');
    });
  });
});
