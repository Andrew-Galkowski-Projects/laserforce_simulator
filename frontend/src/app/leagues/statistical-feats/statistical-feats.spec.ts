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
import { SeasonScope, StatisticalFeats as Feats } from '../../api/league-stats.models';
import { SELF_INSERT, USER, signIn } from '../../api/testing/auth-fixtures';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';
import {
  badParam,
  failure,
  featRow,
  pagedEnvelope,
  seasonScope,
  statisticalFeats,
  teamFeat,
  teamOptions,
} from '../testing/league-stats-fixtures';
import { leagueNav } from '../testing/league-fixtures';
import { StatisticalFeats } from './statistical-feats';

const BASE = environment.apiBaseUrl;
const FEATS = `${BASE}/leagues/1/statistical-feats/`;
const PATH = '/leagues/1/stats/statistical-feats';
const INTRO = 'One row per (player, round) that achieved a feat — most recent first.';
const NO_SEASON = 'No Season in this League yet — start a Season to record statistical feats.';
const NO_FEATS = 'No statistical feats recorded yet in this Season.';
const DEFAULT = { ordering: '-round', page: '1', page_size: '10' };

interface Reply {
  data?: Feats;
  scope?: SeasonScope;
  teams?: TeamRef[];
  page?: number;
  totalPages?: number;
}

describe('Statistical Feats (/leagues/:id/stats/statistical-feats, spec #417)', () => {
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
    await signIn({ ...USER, self_insert: { ...SELF_INSERT, id: 25 } });
    harness = await RouterTestingHarness.create();
  });

  afterEach(() => http.verify());

  const page = (): HTMLElement => harness.routeNativeElement as HTMLElement;
  const q = (selector: string): HTMLElement | null =>
    page().querySelector<HTMLElement>(`app-statistical-feats ${selector}`);
  const all = (selector: string): HTMLElement[] => [
    ...page().querySelectorAll<HTMLElement>(`app-statistical-feats ${selector}`),
  ];
  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const url = (): string => TestBed.inject(Router).url;

  function featsRequest(): TestRequest {
    return http.expectOne((req) => req.url === FEATS);
  }

  function sent(req: TestRequest): Record<string, string> {
    const params = req.request.params;
    return Object.fromEntries(params.keys().map((k) => [k, params.get(k) ?? '']));
  }

  async function answer(reply: Reply = {}): Promise<Record<string, string>> {
    const req = featsRequest();
    const params = sent(req);
    const data = reply.data ?? statisticalFeats();
    req.flush(
      pagedEnvelope(data, data.feats.length, {
        page: reply.page ?? 1,
        total_pages: reply.totalPages ?? 1,
        season_scope: reply.scope ?? seasonScope(),
        team_options: reply.teams ?? teamOptions(),
      }),
    );
    await harness.fixture.whenStable();
    return params;
  }

  async function navigate(path: string): Promise<void> {
    await harness.navigateByUrl(path);
    http.expectOne(`${BASE}/leagues/1/nav/`).flush({ data: leagueNav(), meta: {} });
  }

  async function open(path = PATH, reply: Reply = {}): Promise<Record<string, string>> {
    await navigate(path);
    return answer(reply);
  }

  const headers = (): string[] => all('#statistical-feats-table th').map((th) => text(th));
  const header = (label: string): HTMLAnchorElement =>
    all('#statistical-feats-table th a').find(
      (a) => text(a).replace(/ [↑↓]$/, '') === label,
    ) as HTMLAnchorElement;
  const rows = (): HTMLElement[] => all('#statistical-feats-table tbody tr');
  const cells = (row: HTMLElement): string[] =>
    [...row.querySelectorAll('td')].map((td) => text(td));
  const select = (id: string): HTMLSelectElement => q(`#${id}`) as unknown as HTMLSelectElement;

  async function change(id: string, value: string): Promise<void> {
    select(id).value = value;
    select(id).dispatchEvent(new Event('change'));
    await harness.fixture.whenStable();
  }

  async function click(el: HTMLElement): Promise<void> {
    el.click();
    await harness.fixture.whenStable();
  }

  it('asks for the displayed Season, newest Round first, page 1 of 10', async () => {
    expect(await open()).toEqual(DEFAULT);
    expect(harness.fixture.debugElement.query(By.directive(StatisticalFeats))).not.toBeNull();
  });

  it("heads the page with the League's name and the template's intro line", async () => {
    await open();
    expect(text(q('h1'))).toBe('Metro League — Statistical Feats');
    expect(text(q('.stats-intro'))).toBe(INTRO);
  });

  it('has the template columns in order, Round marked as the active descending sort', async () => {
    await open();
    expect(headers()).toEqual([
      'Player',
      'Role',
      'Team',
      'Opp',
      'Result',
      'Season',
      'Feats',
      'Points',
      'MVP',
      'Tags',
      'Tagged',
      'Acc%',
      'Lives',
      'Resup',
      'Missiles',
      'Specials',
      'Follow-up',
      'Reaction',
      'Combo Resup',
      'Nukes',
      'Round ↓',
    ]);
    // Every header is a sort link.
    expect(all('#statistical-feats-table th a')).toHaveLength(21);
  });

  it("shows a row in the template's formats: MVP and Acc% 1 decimal, the rest whole", async () => {
    await open(PATH, {
      data: statisticalFeats({
        feats: [
          featRow({
            stats: { ...featRow().stats, points_scored: 12400.6, mvp: 16.54, accuracy: 0.48 },
          }),
        ],
      }),
    });
    expect(cells(rows()[0])).toEqual([
      '☆ Ace You',
      'commander',
      'Red Raiders',
      'Blue Bolts',
      'W',
      'Season 1',
      'Tags Points (season best)',
      '12401',
      '16.5',
      '22',
      '9',
      '0.5',
      '4',
      '0',
      '2',
      '1',
      '3',
      '1',
      '0',
      '0',
      'View round',
    ]);
    const numeric = [...rows()[0].querySelectorAll('td')].map((td) => td.classList.contains('num'));
    expect(numeric).toEqual([...Array(7).fill(false), ...Array(13).fill(true), false]);
  });

  it('puts the Player cell first: a League player link and the Watch flag (I4)', async () => {
    await open(PATH, { data: statisticalFeats({ feats: [featRow({ watched: true })] }) });
    const cell = rows()[0].querySelector('td.name app-player-cell')!;
    expect(cell.querySelector('a')!.getAttribute('href')).toBe('/leagues/1/players/25');
    expect(cell.querySelector('button')!.getAttribute('aria-pressed')).toBe('true');
  });

  it('gives each feat a badge; a season best is marked apart and says so', async () => {
    await open();
    const badges = [...rows()[0].querySelectorAll<HTMLElement>('.feat-badge')];
    expect(badges.map((b) => text(b))).toEqual(['Tags', 'Points (season best)']);
    expect(badges.map((b) => b.hasAttribute('data-season-best'))).toEqual([false, true]);
  });

  it('links each row to its Round', async () => {
    await open();
    const link = rows()[0].querySelector('td:last-child a')!;
    expect(text(link)).toBe('View round');
    expect(link.getAttribute('href')).toBe('/rounds/70');
  });

  describe('sorting (I1)', () => {
    it('a new header sorts descending, back to page 1, in the URL and the request', async () => {
      await open(`${PATH}?page=2`, { page: 2, totalPages: 3 });
      await click(header('Tags'));
      expect(url()).toBe(`${PATH}?sort=tags_made&dir=desc`);
      expect(await answer()).toEqual({ ordering: '-tags_made', page: '1', page_size: '10' });
      expect(headers()[9]).toBe('Tags ↓');
      expect(headers()[20]).toBe('Round');
    });

    it('the active header toggles to ascending', async () => {
      await open();
      await click(header('Round'));
      expect(url()).toBe(`${PATH}?sort=round&dir=asc`);
      expect(await answer()).toEqual({ ordering: 'round', page: '1', page_size: '10' });
      expect(headers()[20]).toBe('Round ↑');
    });

    it('sorts on every descriptor key the API takes', async () => {
      await open();
      const keys = ['Player', 'Role', 'Team', 'Opp', 'Result', 'Season', 'Feats'].map((label) =>
        new URL(header(label).href).searchParams.get('sort'),
      );
      expect(keys).toEqual(['name', 'role', 'team', 'opp', 'result', 'season', 'feat']);
    });

    it('takes the default for a hand-edited sort it does not know', async () => {
      expect(await open(`${PATH}?sort=bogus&dir=up`)).toEqual(DEFAULT);
    });
  });

  describe('pickers', () => {
    it('offers the Seasons plus Career, and All Teams plus the enrolled Teams', async () => {
      await open();
      expect([...select('stats-season-select').options].map((o) => text(o))).toEqual([
        'Season 1 (2026)',
        'Season 0 (2025)',
        'Career',
      ]);
      expect([...select('stats-team-select').options].map((o) => text(o))).toEqual([
        'All Teams',
        'Blue Bolts',
        'Red Raiders',
      ]);
      expect(select('stats-per-page-select')).not.toBeNull();
    });

    it('a Season change clears the Team and the page', async () => {
      await open(`${PATH}?team=3&page=2`, { page: 2, totalPages: 3 });
      await change('stats-season-select', 'career');
      expect(url()).toBe(`${PATH}?season=career`);
      expect(await answer()).toEqual({ ...DEFAULT, season: 'career' });
    });

    it('a Team change goes back to page 1 and sends team_id', async () => {
      await open(`${PATH}?page=2`, { page: 2, totalPages: 3 });
      await change('stats-team-select', '5');
      expect(url()).toBe(`${PATH}?team=5`);
      expect(await answer()).toEqual({ ...DEFAULT, team_id: '5' });
      expect(select('stats-team-select').value).toBe('5');
    });

    it('a per-page change goes back to page 1', async () => {
      await open(`${PATH}?page=2`, { page: 2, totalPages: 3 });
      await change('stats-per-page-select', '25');
      expect(url()).toBe(`${PATH}?per_page=25`);
      expect(await answer()).toEqual({ ...DEFAULT, page_size: '25' });
    });
  });

  describe('pagination', () => {
    it('is hidden on a single page', async () => {
      await open();
      expect(q('.stats-pager')).toBeNull();
    });

    it('shows "Page X of N" and Next loads the next page', async () => {
      await open(PATH, { totalPages: 3 });
      expect(text(q('.stats-pager__page'))).toBe('Page 1 of 3');
      const next = all('.stats-pager button').find((b) => text(b) === 'Next')!;
      await click(next);
      expect(url()).toBe(`${PATH}?page=2`);
      expect(await answer({ page: 2, totalPages: 3 })).toEqual({ ...DEFAULT, page: '2' });
    });

    it('shows the page the API answered, and Previous goes back to page 1', async () => {
      expect(await open(`${PATH}?page=2`, { page: 2, totalPages: 3 })).toEqual({
        ...DEFAULT,
        page: '2',
      });
      expect(text(q('.stats-pager__page'))).toBe('Page 2 of 3');
      const previous = all('.stats-pager button').find((b) => text(b) === 'Previous')!;
      await click(previous);
      expect(url()).toBe(PATH);
      expect(await answer()).toEqual(DEFAULT);
    });
  });

  describe('Team Feats', () => {
    it('lists each Team feat under its h2: label, Team history link and View round', async () => {
      await open();
      expect(text(q('#statistical-feats-team-feats h2'))).toBe('Team Feats');
      const item = q('#stat-team-feat-comeback_win')!;
      expect(text(item.querySelector('.team-feat__label'))).toBe(
        'Comeback win (won the match after losing round 1)',
      );
      const links = [...item.querySelectorAll('a')];
      expect(links.map((a) => [text(a), a.getAttribute('href')])).toEqual([
        ['Red Raiders', '/teams/3/history'],
        ['View round', '/rounds/70'],
      ]);
    });

    it('shows the Team name as plain text with no Team, and no View round with no Round', async () => {
      await open(PATH, {
        data: statisticalFeats({
          team_feats: [teamFeat({ team: null, team_name: 'Old Guard', round_id: null })],
        }),
      });
      const item = q('#stat-team-feat-comeback_win')!;
      expect(text(item.querySelector('.team-feat__team'))).toBe('Old Guard');
      expect(item.querySelectorAll('a')).toHaveLength(0);
    });

    it('shows the Team feats without a table when no Player feat is on the page', async () => {
      await open(PATH, { data: statisticalFeats({ feats: [] }) });
      expect(q('#statistical-feats-table')).toBeNull();
      expect(q('#statistical-feats-team-feats')).not.toBeNull();
      expect(q('#stat-feats-empty-notice')).toBeNull();
    });

    it('has no Team Feats section when there are none', async () => {
      await open(PATH, { data: statisticalFeats({ team_feats: [] }) });
      expect(q('#statistical-feats-team-feats')).toBeNull();
    });
  });

  describe('lenient fallback (I2)', () => {
    it('drops a stale season the API rejects, replacing the URL, and reloads', async () => {
      await navigate(`${PATH}?season=99&sort=mvp&dir=asc`);
      const nav = vi.spyOn(TestBed.inject(Router), 'navigate');
      featsRequest().flush(...badParam('season'));
      await harness.fixture.whenStable();
      expect(url()).toBe(`${PATH}?sort=mvp&dir=asc`);
      expect(nav.mock.calls.map(([, extras]) => extras?.replaceUrl)).toEqual([true]);
      expect(await answer()).toEqual({ ...DEFAULT, ordering: 'mvp' });
      expect(q('[role="alert"]')).toBeNull();
    });

    it('drops a team the API rejects and reloads without team_id', async () => {
      await navigate(`${PATH}?team=99`);
      featsRequest().flush(...badParam('team_id'));
      await harness.fixture.whenStable();
      expect(url()).toBe(PATH);
      expect(await answer()).toEqual(DEFAULT);
    });

    it('drops an ordering the API rejects', async () => {
      await navigate(`${PATH}?sort=mvp&dir=desc`);
      featsRequest().flush(...badParam('ordering'));
      await harness.fixture.whenStable();
      expect(url()).toBe(PATH);
      expect(await answer()).toEqual(DEFAULT);
    });

    it('goes back to page 1 on a 404 past page 1', async () => {
      await navigate(`${PATH}?page=9`);
      featsRequest().flush(...failure(404, 'Invalid page.'));
      await harness.fixture.whenStable();
      expect(url()).toBe(PATH);
      expect(await answer()).toEqual(DEFAULT);
    });
  });

  describe('empty states (I3)', () => {
    it('shows the no-Season notice, and no Team or per-page picker, with no Season', async () => {
      await open(PATH, {
        data: statisticalFeats({ feats: [], team_feats: [] }),
        scope: seasonScope({ selected: null, options: [] }),
        teams: [],
      });
      expect(text(q('#stat-feats-empty-notice'))).toBe(NO_SEASON);
      expect(q('#stats-season-select')).not.toBeNull();
      expect(q('#stats-team-select')).toBeNull();
      expect(q('#statistical-feats-table')).toBeNull();
    });

    it('shows the no-feats notice when the Season has no feats at all', async () => {
      await open(PATH, { data: statisticalFeats({ feats: [], team_feats: [] }) });
      expect(text(q('#stat-feats-empty-notice'))).toBe(NO_FEATS);
      expect(q('#statistical-feats-table')).toBeNull();
      expect(q('#statistical-feats-team-feats')).toBeNull();
      expect(q('#stats-team-select')).not.toBeNull();
    });

    it('shows no notice while there are feats', async () => {
      await open();
      expect(q('#stat-feats-empty-notice')).toBeNull();
    });
  });

  it("shows any other failure in the error box with the server's message", async () => {
    await navigate(PATH);
    featsRequest().flush(...failure(500, 'Database is down.'));
    await harness.fixture.whenStable();
    expect(text(q('[role="alert"]'))).toBe('Database is down.');
    expect(q('#statistical-feats-table')).toBeNull();
    expect(url()).toBe(PATH);
  });

  it("shows the layout's not-found state for another Account's League (I7)", async () => {
    await harness.navigateByUrl(PATH);
    http.expectOne(`${BASE}/leagues/1/nav/`).flush(...failure(404, 'Not found.'));
    http.match((req) => req.url === FEATS);
    await harness.fixture.whenStable();
    expect(text(page().querySelector('h1'))).toBe('League not found');
    expect(page().querySelector('app-statistical-feats')).toBeNull();
  });
});
