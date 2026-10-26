import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import {
  HttpTestingController,
  TestRequest,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { Title } from '@angular/platform-browser';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { environment } from '../../../environments/environment';
import { Player } from '../../api/players.models';
import { PROFILE, STATS } from '../../api/testing/player-fixtures';
import { USER, signIn } from '../../api/testing/auth-fixtures';
import { APP_TITLE } from '../../app-title';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';
import { User } from '../../auth/user';
import { releaseTeamsLoads } from '../../teams/testing/teams-loads';
import { releasePlayersLoads } from '../testing/players-loads';

const BASE = environment.apiBaseUrl;

/** Every Stat distinct (1..19 in serializer order), so a swapped column shows. */
function player(id: number, name: string, overrides: Partial<Player> = {}): Player {
  const stats = Object.fromEntries(Object.keys(STATS).map((k, i) => [k, i + 1]));
  return {
    ...PROFILE,
    ...(stats as unknown as typeof STATS),
    id,
    team: 3,
    team_name: 'Red Dragons',
    name,
    preferred_roles: ['commander', 'scout'],
    overall_rating: 52.16,
    ...overrides,
  };
}

const LABELS = [
  'Name',
  'Team',
  'Preferred Roles',
  'Overall',
  'Player Aware',
  'Game Aware',
  'Resource Aware',
  'Decision',
  'Positioning',
  'Stamina',
  'Speed',
  'Flexibility',
  'Adaptability',
  'Communication',
  'Teamwork',
  'Offensive Syn',
  'Defensive Syn',
  'Midfield Syn',
  'Resupply Syn',
  'Resupply Eff',
  'Accuracy',
  'Survival',
  'Special Usage',
];

describe('Players list (/players)', () => {
  let http: HttpTestingController;
  let harness: RouterTestingHarness;

  async function setUp(user: User = USER): Promise<void> {
    TestBed.configureTestingModule({
      providers: [
        provideRouter(routes),
        provideHttpClient(withInterceptors([authInterceptor])),
        provideHttpClientTesting(),
      ],
    });
    http = TestBed.inject(HttpTestingController);
    await signIn(user);
    harness = await RouterTestingHarness.create();
  }

  afterEach(() => http.verify());

  const page = (): HTMLElement => {
    const root = harness.routeNativeElement as HTMLElement;
    return root.querySelector<HTMLElement>('app-player-list') ?? root;
  };
  const squash = (s: string | null | undefined): string => (s ?? '').replace(/\s+/g, ' ').trim();
  const text = (): string => squash(page().textContent);
  const routerUrl = (): string => TestBed.inject(Router).url;

  function listRequest(): TestRequest {
    return http.expectOne((req) => req.url === `${BASE}/players/`);
  }

  /** The list request's query, as `key=value` in a fixed order. */
  function query(req: TestRequest): string {
    return req.request.params
      .keys()
      .sort()
      .map((k) => `${k}=${req.request.params.get(k)}`)
      .join('&');
  }

  function flush(
    req: TestRequest,
    items: Player[],
    meta: { page?: number; page_size?: number; count?: number; total_pages?: number } = {},
  ): void {
    req.flush({
      data: items,
      meta: { page: 1, page_size: 10, count: items.length, total_pages: 1, ...meta },
    });
  }

  /** Opens `url`, answers the list request with `items` / `meta`, and returns its query. */
  async function open(
    url = '/players',
    items: Player[] = [player(40, 'Ace')],
    meta: Parameters<typeof flush>[2] = {},
  ): Promise<string> {
    await harness.navigateByUrl(url);
    const req = listRequest();
    expect(req.request.method).toBe('GET');
    const sent = query(req);
    flush(req, items, meta);
    await harness.fixture.whenStable();
    return sent;
  }

  const headers = (): HTMLElement[] => [...page().querySelectorAll<HTMLElement>('thead th')];
  const header = (label: string): HTMLElement | undefined =>
    headers().find((th) => squash(th.textContent).replace(/ [↑↓]$/, '') === label);

  /** An element's child nodes' texts (text runs, links, badges) joined by one space. */
  const parts = (el: Element | null | undefined): string =>
    [...(el?.childNodes ?? [])]
      .filter((n) => n.nodeType !== Node.COMMENT_NODE)
      .map((n) => squash(n.textContent))
      .filter((s) => s !== '')
      .join(' ');

  function cells(): string[][] {
    return [...page().querySelectorAll('tbody tr')].map((tr) =>
      [...tr.querySelectorAll('td')].map(parts),
    );
  }

  function link(label: string): HTMLAnchorElement | undefined {
    return [...page().querySelectorAll('a')].find((a) => squash(a.textContent) === label);
  }

  it('asks for page 1 of 10, ordered by Team ascending, by default', async () => {
    await setUp();
    expect(await open()).toBe('ordering=team&page=1&page_size=10');
    expect(squash(page().querySelector('h1')?.textContent)).toBe('Players');
    expect(TestBed.inject(Title).getTitle()).toBe(`Players - ${APP_TITLE}`);
  });

  it('says how many Players there are in total, pluralised', async () => {
    await setUp();
    await open('/players', [player(40, 'Ace')], { count: 1 });
    expect(text()).toContain('1 player total');
    expect(text()).not.toContain('1 players total');
  });

  it('says "N players total" for more than one', async () => {
    await setUp();
    await open('/players', [player(40, 'Ace'), player(41, 'Bea')], { count: 2 });
    expect(text()).toContain('2 players total');
  });

  it("shows Django's 23 column headers in order", async () => {
    await setUp();
    await open();
    expect(headers().map((th) => squash(th.textContent).replace(/ [↑↓]$/, ''))).toEqual(LABELS);
  });

  it('makes every header but Preferred Roles a sort link (22 of them)', async () => {
    await setUp();
    await open();
    expect(header('Preferred Roles')?.querySelector('a')).toBeNull();
    expect(headers().filter((th) => th.querySelector('a'))).toHaveLength(22);
  });

  it('marks the active column ↑ and links it to the other direction; others link ascending', async () => {
    await setUp();
    await open();
    expect(squash(header('Team')?.textContent)).toBe('Team ↑');
    expect(header('Team')?.querySelector('a')?.getAttribute('href')).toBe(
      '/players?sort=team&dir=desc&per_page=10',
    );
    expect(squash(header('Name')?.textContent)).toBe('Name');
    expect(header('Name')?.querySelector('a')?.getAttribute('href')).toBe(
      '/players?sort=name&dir=asc&per_page=10',
    );
    expect(header('Offensive Syn')?.querySelector('a')?.getAttribute('href')).toBe(
      '/players?sort=offensive_synergy&dir=asc&per_page=10',
    );
  });

  it('orders descending with a "-" and marks the column ↓, linking back to ascending', async () => {
    await setUp();
    expect(await open('/players?sort=overall_rating&dir=desc&per_page=25')).toBe(
      'ordering=-overall_rating&page=1&page_size=25',
    );
    expect(squash(header('Overall')?.textContent)).toBe('Overall ↓');
    expect(header('Overall')?.querySelector('a')?.getAttribute('href')).toBe(
      '/players?sort=overall_rating&dir=asc&per_page=25',
    );
    expect(squash(header('Team')?.textContent)).toBe('Team');
  });

  it('re-sorts from page 1 on a header click, keeping the page size, without a reload', async () => {
    await setUp();
    await open('/players?sort=name&dir=asc&page=3&per_page=25', [player(40, 'Ace')], {
      page: 3,
      page_size: 25,
      count: 60,
      total_pages: 3,
    });
    header('Name')?.querySelector('a')?.click();
    await harness.fixture.whenStable();
    expect(routerUrl()).toBe('/players?sort=name&dir=desc&per_page=25');
    const req = listRequest();
    expect(query(req)).toBe('ordering=-name&page=1&page_size=25');
    flush(req, [player(41, 'Bea')]);
    await harness.fixture.whenStable();
    expect(cells()[0][0]).toBe('Bea');
  });

  it('falls back to the default for an unknown sort, direction, page or page size', async () => {
    await setUp();
    expect(await open('/players?sort=preferred_roles&dir=sideways&page=abc&per_page=7')).toBe(
      'ordering=team&page=1&page_size=10',
    );
    expect(squash(header('Team')?.textContent)).toBe('Team ↑');
  });

  it('falls back to page 1 for a page below 1', async () => {
    await setUp();
    expect(await open('/players?page=-2')).toBe('ordering=team&page=1&page_size=10');
  });

  it('falls back to page 1 for a page that is not a whole number', async () => {
    await setUp();
    expect(await open('/players?page=1.5')).toBe('ordering=team&page=1&page_size=10');
  });

  describe('page size', () => {
    const select = (): HTMLSelectElement => {
      const el = page().querySelector<HTMLSelectElement>('select');
      if (!el) throw new Error('no page-size select');
      return el;
    };

    it('offers 10, 25, 50 and 100 per page, labelled, with the current size selected', async () => {
      await setUp();
      await open('/players?per_page=50');
      expect([...select().options].map((o) => o.value)).toEqual(['10', '25', '50', '100']);
      expect(select().value).toBe('50');
      const label = page().querySelector(`label[for="${select().id}"]`);
      expect(squash(label?.textContent)).toBe('Show:');
      expect(text()).toContain('per page');
    });

    it('applies on change, from page 1, keeping the sort', async () => {
      await setUp();
      await open('/players?sort=speed&dir=desc&page=2', [player(40, 'Ace')], {
        page: 2,
        count: 15,
        total_pages: 2,
      });
      select().value = '100';
      select().dispatchEvent(new Event('change'));
      await harness.fixture.whenStable();
      expect(routerUrl()).toBe('/players?sort=speed&dir=desc&per_page=100');
      const req = listRequest();
      expect(query(req)).toBe('ordering=-speed&page=1&page_size=100');
      flush(req, [player(40, 'Ace')], { page_size: 100 });
      await harness.fixture.whenStable();
    });
  });

  describe('pagination', () => {
    const pager = (): HTMLElement | null => page().querySelector('nav');

    it('shows Previous, "Page X of Y" and Next on a middle page', async () => {
      await setUp();
      await open('/players?sort=name&dir=asc&page=2', [player(40, 'Ace')], {
        page: 2,
        count: 25,
        total_pages: 3,
      });
      expect(parts(pager())).toBe('Previous Page 2 of 3 Next');
      expect(link('Previous')?.getAttribute('href')).toBe(
        '/players?sort=name&dir=asc&per_page=10&page=1',
      );
      expect(link('Next')?.getAttribute('href')).toBe(
        '/players?sort=name&dir=asc&per_page=10&page=3',
      );
    });

    it('loads the next page from Next', async () => {
      await setUp();
      await open('/players', [player(40, 'Ace')], { count: 25, total_pages: 3 });
      link('Next')?.click();
      await harness.fixture.whenStable();
      expect(routerUrl()).toBe('/players?sort=team&dir=asc&per_page=10&page=2');
      const req = listRequest();
      expect(query(req)).toBe('ordering=team&page=2&page_size=10');
      flush(req, [player(41, 'Bea')], { page: 2, count: 25, total_pages: 3 });
      await harness.fixture.whenStable();
      expect(parts(pager())).toBe('Previous Page 2 of 3 Next');
    });

    it('has no Previous on the first page', async () => {
      await setUp();
      await open('/players', [player(40, 'Ace')], { count: 25, total_pages: 3 });
      expect(parts(pager())).toBe('Page 1 of 3 Next');
    });

    it('has no Next on the last page', async () => {
      await setUp();
      await open('/players?page=3', [player(40, 'Ace')], { page: 3, count: 25, total_pages: 3 });
      expect(parts(pager())).toBe('Previous Page 3 of 3');
    });

    it('shows no pagination when everything fits on one page', async () => {
      await setUp();
      await open();
      expect(pager()).toBeNull();
    });

    it('goes back to page 1 when the page asked for no longer exists', async () => {
      await setUp();
      await harness.navigateByUrl('/players?sort=name&dir=asc&page=9');
      listRequest().flush(
        { errors: [{ code: 'not_found', message: 'Invalid page.', field: null }], meta: {} },
        { status: 404, statusText: 'Not Found' },
      );
      await harness.fixture.whenStable();
      expect(routerUrl()).toBe('/players?sort=name&dir=asc&per_page=10');
      const req = listRequest();
      expect(query(req)).toBe('ordering=name&page=1&page_size=10');
      flush(req, [player(40, 'Ace')]);
      await harness.fixture.whenStable();
      expect(cells()).toHaveLength(1);
    });
  });

  describe('rows', () => {
    it('shows the name, Team, roles, Overall to 1 decimal and the 19 Stats', async () => {
      await setUp();
      await open();
      expect(cells()).toEqual([
        [
          'Ace',
          'Red Dragons',
          'commander, scout',
          '52.2',
          ...Array.from({ length: 19 }, (_, i) => String(i + 1)),
        ],
      ]);
    });

    it("links the name to the Player's career stats and the Team to its page", async () => {
      await setUp();
      await open('/players', [
        player(40, 'Ace'),
        player(41, 'Loner', { team: 1, team_name: 'Free Agents' }),
      ]);
      expect(link('Ace')?.getAttribute('href')).toBe('/players/40/stats');
      expect(link('Loner')?.getAttribute('href')).toBe('/players/41/stats');
      const teams = [...page().querySelectorAll('tbody tr')].map((tr) =>
        tr.querySelectorAll('td')[1].querySelector('a')?.getAttribute('href'),
      );
      expect(teams).toEqual(['/teams/3', '/teams/1']);
      expect(cells().map((r) => r[1])).toEqual(['Red Dragons', 'Free Agents']);
    });

    it('opens a Team from its name', async () => {
      await setUp();
      await open();
      link('Red Dragons')?.click();
      await harness.fixture.whenStable();
      releaseTeamsLoads();
      expect(routerUrl()).toBe('/teams/3');
    });

    it('opens career stats from a name', async () => {
      await setUp();
      await open();
      link('Ace')?.click();
      await harness.fixture.whenStable();
      releasePlayersLoads();
      expect(routerUrl()).toBe('/players/40/stats');
    });

    it('puts a "You" badge after the Self-insert\'s name link', async () => {
      await setUp({ ...USER, self_insert: player(41, 'Bea') });
      await open('/players', [player(40, 'Ace'), player(41, 'Bea')]);
      expect(cells().map((r) => r[0])).toEqual(['Ace', 'Bea You']);
      const badge = page().querySelector('.badge--you');
      expect(badge?.closest('a')).toBeNull();
      expect(squash(badge?.previousElementSibling?.textContent)).toBe('Bea');
      expect(badge?.previousElementSibling?.tagName).toBe('A');
    });
  });

  it('says "No players yet." and shows no table when there are none', async () => {
    await setUp();
    await open('/players', [], { count: 0 });
    expect(text()).toContain('No players yet.');
    expect(text()).toContain('0 players total');
    expect(page().querySelector('table')).toBeNull();
  });

  it('scrolls the wide table sideways inside its own container', async () => {
    await setUp();
    await open();
    const box = page().querySelector('table')?.parentElement;
    expect(box).toBeTruthy();
    expect(getComputedStyle(box as Element).overflowX).toBe('auto');
  });

  it('shows the API error in an alert when the list fails to load', async () => {
    await setUp();
    await harness.navigateByUrl('/players');
    listRequest().flush(
      { errors: [{ code: 'server_error', message: 'Try later.', field: null }], meta: {} },
      { status: 503, statusText: 'Unavailable' },
    );
    await harness.fixture.whenStable();
    expect(page().querySelector('[role="alert"]')?.textContent).toContain('Try later.');
    expect(page().querySelector('table')).toBeNull();
  });
});
