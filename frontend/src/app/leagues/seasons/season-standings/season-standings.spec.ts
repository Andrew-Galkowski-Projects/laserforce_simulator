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
import { League, Season } from '../../../api/leagues.models';
import { SeasonStandings as Standings } from '../../../api/seasons.models';
import { signIn } from '../../../api/testing/auth-fixtures';
import { routes } from '../../../app.routes';
import { authInterceptor } from '../../../auth/auth.interceptor';
import { league, standingsRow } from '../../testing/league-fixtures';
import { failure } from '../../testing/league-stats-fixtures';
import { season, seasonStandings, seasonUrl } from '../testing/season-fixtures';
import { releaseSeasonLayout } from '../testing/season-loads';
import { releasePlayControls } from '../../testing/leagues-loads';

const BASE = environment.apiBaseUrl;

describe('Season Standings (/leagues/:id/seasons/:seasonId/standings, spec #444)', () => {
  let http: HttpTestingController;
  let harness: RouterTestingHarness;

  beforeEach(async () => {
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
  });

  afterEach(() => {
    http.match((req) => req.url.endsWith('/nav/'));
    releasePlayControls();
    http.verify();
  });

  const page = (): HTMLElement =>
    (harness.routeNativeElement as HTMLElement).querySelector(
      'app-season-standings',
    ) as HTMLElement;
  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const q = (selector: string): HTMLElement | null => page().querySelector<HTMLElement>(selector);
  const linkIn = (el: Element | null, label: string): HTMLAnchorElement | undefined =>
    [...(el?.querySelectorAll('a') ?? [])].find((a) => text(a) === label);

  function standingsRequest(): TestRequest {
    const req = http.expectOne((r) => r.url === `${BASE}/seasons/11/standings/`);
    expect(req.request.method).toBe('GET');
    return req;
  }

  function answerLeague(body: League = league()): void {
    http
      .expectOne((r) => r.method === 'GET' && r.url === `${BASE}/leagues/1/`)
      .flush({ data: body, meta: {} });
  }

  /** Lands on `url`, answers the layout's Season, the League and the standings GET. */
  async function open(
    body: Standings = seasonStandings(),
    opts: { url?: string; season?: Season; league?: League } = {},
  ): Promise<void> {
    await harness.navigateByUrl(opts.url ?? seasonUrl('standings'));
    await releaseSeasonLayout(harness, opts.season ?? season());
    answerLeague(opts.league ?? league());
    standingsRequest().flush({ data: body, meta: {} });
    await harness.fixture.whenStable();
  }

  describe('header', () => {
    it('shows the breadcrumb above the h1 "Standings"', async () => {
      await open();
      expect(text(q('nav[aria-label="Breadcrumb"]'))).toBe('Metro League › Season 1');
      expect(text(q('h1'))).toBe('Standings');
    });

    it.each([
      ['draft', 'Draft'],
      ['active', 'Active'],
      ['completed', 'Completed'],
    ] as const)('shows the %s Season state as a "%s" badge', async (state, label) => {
      await open(seasonStandings(), { season: season({ state }) });
      const badge = q('.season-standings__state');
      expect(text(badge)).toBe(label);
      expect(badge?.dataset['state']).toBe(state);
    });

    it('links "Schedule" to the Season\'s schedule', async () => {
      await open();
      expect(linkIn(page(), 'Schedule')?.getAttribute('href')).toBe(
        '/leagues/1/seasons/11/schedule',
      );
    });
  });

  describe('table', () => {
    const headers = (table: Element | null = q('table')): string[] =>
      [...(table?.querySelectorAll('thead th') ?? [])].map(text);
    const cells = (row: Element | null): string[] => [...(row?.children ?? [])].map(text);

    it('has the 17 column headers in order, the active one with its arrow (I6)', async () => {
      await open();
      expect(headers()).toEqual([
        'Rank ↑',
        'Team',
        'MP',
        'W',
        'L',
        'T',
        'Pts',
        'RW',
        'TS',
        'Streak',
        'L5',
        'R Streak',
        'R L5',
        'Red Rec',
        'Blue Rec',
        'Red PF',
        'Blue PF',
      ]);
    });

    it('shows every cell of a row: streaks as <kind><length>, records as w-l-t', async () => {
      await open();
      expect(cells(q('tbody tr'))).toEqual([
        '8',
        'Green Giants',
        '7',
        '1',
        '6',
        '0',
        '3',
        '3',
        '70120',
        'L3',
        '1-4-0',
        'L2',
        '1-4-0',
        '1-3-0',
        '2-8-0',
        '35100',
        '35020',
      ]);
    });

    it('links the Team name to the Team (I5)', async () => {
      await open();
      expect(linkIn(q('tbody tr'), 'Green Giants')?.getAttribute('href')).toBe('/teams/7');
    });

    it('shows "(unknown)" for a missing Team and "—" for an empty streak', async () => {
      await open(
        seasonStandings({
          groups: [
            {
              conference: null,
              name: null,
              rows: [standingsRow({ team: null, match_streak: ['', 0], round_streak: ['', 0] })],
            },
          ],
        }),
      );
      const row = cells(q('tbody tr'));
      expect(row[1]).toBe('(unknown)');
      expect(q('tbody tr a')).toBeNull();
      expect(row[9]).toBe('—');
      expect(row[11]).toBe('—');
    });
  });

  describe('groups and states', () => {
    const PREVIEW = 'Preview — Season not started. Teams are ranked by computed team rating.';
    const EMPTY_TEXT = 'No teams enrolled in this Season yet.';

    it('renders one table per Conference, each headed by its name', async () => {
      await open(
        seasonStandings({
          groups: [
            {
              conference: { id: 31, name: 'East' },
              name: 'East',
              rows: [standingsRow({ team_id: 3, team: { id: 3, name: 'Red Raiders' } })],
            },
            {
              conference: { id: 32, name: 'West' },
              name: 'West',
              rows: [standingsRow(), standingsRow({ team_id: 8, team: { id: 8, name: 'Gold' } })],
            },
          ],
        }),
      );
      const tables = [...page().querySelectorAll('table')];
      expect(tables).toHaveLength(2);
      expect([...page().querySelectorAll('h2')].map(text)).toEqual(['East', 'West']);
      expect(tables.map((t) => t.querySelectorAll('tbody tr').length)).toEqual([1, 2]);
    });

    it('gives the single flat group no h2', async () => {
      await open();
      expect(page().querySelectorAll('table')).toHaveLength(1);
      expect(q('h2')).toBeNull();
    });

    it('says a draft Season is a preview ranked by team rating', async () => {
      await open(seasonStandings({ is_draft_preview: true }), {
        season: season({ state: 'draft' }),
      });
      expect(text(q('.season-standings__preview'))).toBe(PREVIEW);
      expect(q('table')).not.toBeNull();
    });

    it('has no preview text on a started Season', async () => {
      await open();
      expect(page().textContent).not.toContain('Preview');
    });

    it('says no Teams are enrolled, with no table, on an empty preview', async () => {
      await open(
        seasonStandings({
          is_draft_preview: true,
          groups: [{ conference: null, name: null, rows: [] }],
        }),
        { season: season({ state: 'draft' }) },
      );
      expect(text(q('.season-standings__preview'))).toBe(PREVIEW);
      expect(text(q('.season-standings__empty'))).toBe(EMPTY_TEXT);
      expect(q('table')).toBeNull();
    });

    it('keeps the empty table of a started Season with no rows', async () => {
      await open(seasonStandings({ groups: [{ conference: null, name: null, rows: [] }] }));
      expect(page().textContent).not.toContain(EMPTY_TEXT);
      expect(q('table')).not.toBeNull();
    });

    it('names the champion below the tables when the Season has one', async () => {
      await open(seasonStandings(), {
        season: season({ state: 'completed', champion_team: { id: 3, name: 'Red Raiders' } }),
      });
      expect(text(q('.season-standings__champion'))).toBe('Champion: Red Raiders');
    });

    it('names no champion without one', async () => {
      await open();
      expect(page().textContent).not.toContain('Champion');
    });
  });

  describe('"You" (I4)', () => {
    const twoRows = seasonStandings({
      groups: [
        {
          conference: null,
          name: null,
          rows: [
            standingsRow({ team_id: 3, rank: 1, team: { id: 3, name: 'Red Raiders' } }),
            standingsRow(),
          ],
        },
      ],
    });
    const marked = (): string[] =>
      [...page().querySelectorAll('tbody tr')]
        .filter((r) => r.querySelector('.you'))
        .map((r) => text(r.querySelector('a')));

    it('marks the managed Team\'s row "You" in a career League', async () => {
      await open(twoRows);
      expect(marked()).toEqual(['Red Raiders']);
      expect(text(q('tbody tr .you'))).toBe('You');
    });

    it.each(['sandbox', 'multiplayer'] as const)('marks no row in a %s League', async (mode) => {
      await open(twoRows, { league: league({ mode }) });
      expect(marked()).toEqual([]);
    });

    it('still shows the table, unmarked, when the League GET fails', async () => {
      await harness.navigateByUrl(seasonUrl('standings'));
      await releaseSeasonLayout(harness);
      http.expectOne(`${BASE}/leagues/1/`).flush(...failure(500, 'Database is down.'));
      standingsRequest().flush({ data: twoRows, meta: {} });
      await harness.fixture.whenStable();
      expect(q('[role="alert"]')).toBeNull();
      expect(page().querySelectorAll('tbody tr')).toHaveLength(2);
      expect(marked()).toEqual([]);
    });

    it('marks no row when the career League has no current Team', async () => {
      await open(twoRows, { league: league({ current_team: null }) });
      expect(marked()).toEqual([]);
    });
  });

  describe('load (I2)', () => {
    it('shows a failed load with Retry, which re-sends only the standings GET', async () => {
      await harness.navigateByUrl(seasonUrl('standings?sort=wins&dir=desc'));
      await releaseSeasonLayout(harness);
      answerLeague();
      standingsRequest().flush(...failure(500, 'Database is down.'));
      await harness.fixture.whenStable();
      const alert = q('[role="alert"]');
      expect(text(alert?.querySelector('span'))).toBe('Database is down.');
      expect(q('table')).toBeNull();

      const retry = [...(alert?.querySelectorAll('button') ?? [])].find((b) => text(b) === 'Retry');
      retry?.click();
      await harness.fixture.whenStable();
      expect(http.match(`${BASE}/seasons/11/`)).toHaveLength(0);
      expect(http.match(`${BASE}/leagues/1/`)).toHaveLength(0);
      const req = standingsRequest();
      expect(req.request.params.get('sort')).toBe('wins');
      req.flush({ data: seasonStandings({ sort: 'wins', dir: 'desc' }), meta: {} });
      await harness.fixture.whenStable();
      expect(q('[role="alert"]')).toBeNull();
      expect(q('table')).not.toBeNull();
    });
  });

  describe('sort (?sort=&dir=)', () => {
    const header = (label: string): HTMLAnchorElement | undefined =>
      [...page().querySelectorAll('thead th a')].find((a) =>
        text(a).startsWith(label),
      ) as HTMLAnchorElement;

    it('sends no params by default', async () => {
      await harness.navigateByUrl(seasonUrl('standings'));
      await releaseSeasonLayout(harness);
      answerLeague();
      const req = standingsRequest();
      expect(req.request.params.keys()).toEqual([]);
      req.flush({ data: seasonStandings(), meta: {} });
    });

    it("sends the URL's known sort and dir", async () => {
      await harness.navigateByUrl(seasonUrl('standings?sort=wins&dir=desc'));
      await releaseSeasonLayout(harness);
      answerLeague();
      const req = standingsRequest();
      expect(req.request.params.get('sort')).toBe('wins');
      expect(req.request.params.get('dir')).toBe('desc');
      req.flush({ data: seasonStandings({ sort: 'wins', dir: 'desc' }), meta: {} });
    });

    it('drops an unknown sort or dir rather than asking for a 400', async () => {
      await harness.navigateByUrl(seasonUrl('standings?sort=bogus&dir=sideways'));
      await releaseSeasonLayout(harness);
      answerLeague();
      const req = standingsRequest();
      expect(req.request.params.keys()).toEqual([]);
      req.flush({ data: seasonStandings(), meta: {} });
    });

    it('keeps a known sort when only the dir is unknown', async () => {
      await harness.navigateByUrl(seasonUrl('standings?sort=team&dir=up'));
      await releaseSeasonLayout(harness);
      answerLeague();
      const req = standingsRequest();
      expect(req.request.params.get('sort')).toBe('team');
      expect(req.request.params.has('dir')).toBe(false);
      req.flush({ data: seasonStandings({ sort: 'team' }), meta: {} });
    });

    it('the active ascending header toggles to desc; another header sorts asc', async () => {
      await open();
      expect(header('Rank')?.getAttribute('href')).toBe(
        '/leagues/1/seasons/11/standings?sort=rank&dir=desc',
      );
      expect(header('Pts')?.getAttribute('href')).toBe(
        '/leagues/1/seasons/11/standings?sort=league_points&dir=asc',
      );
    });

    it('the response drives the arrow: an active descending header shows ↓ and toggles to asc', async () => {
      await open(seasonStandings({ sort: 'total_score', dir: 'desc' }), {
        url: seasonUrl('standings?sort=total_score&dir=desc'),
      });
      expect(text(header('TS'))).toBe('TS ↓');
      expect(text(header('Rank'))).toBe('Rank');
      expect(header('TS')?.getAttribute('href')).toBe(
        '/leagues/1/seasons/11/standings?sort=total_score&dir=asc',
      );
    });

    it('clicking a header re-sends the standings GET with that sort', async () => {
      await open();
      header('W')?.click();
      await harness.fixture.whenStable();
      const req = standingsRequest();
      expect(req.request.params.get('sort')).toBe('wins');
      expect(req.request.params.get('dir')).toBe('asc');
      req.flush({ data: seasonStandings({ sort: 'wins' }), meta: {} });
      await harness.fixture.whenStable();
      expect(text(header('W'))).toBe('W ↑');
    });
  });
});
