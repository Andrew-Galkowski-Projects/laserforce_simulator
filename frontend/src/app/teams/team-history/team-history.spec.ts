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
import {
  HistoryMatchRow,
  HistoryRosterRow,
  HistorySingleRoundRow,
  TeamHistory as TeamHistoryData,
} from '../../api/analytics.models';
import { SELF_INSERT, USER, signIn } from '../../api/testing/auth-fixtures';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';
import { TeamHistory } from './team-history';

const BASE = environment.apiBaseUrl;
const HISTORY_URL = `${BASE}/teams/3/history/`;

/** 14:30 on Oct 5, 2026 in the test machine's own zone, so the HH:mm check is zone-proof. */
const PLAYED = new Date(2026, 9, 5, 14, 30).toISOString();

const ROLE_COLUMNS: TeamHistoryData['role_columns'] = [
  { role: 'commander', label: 'Commander' },
  { role: 'heavy', label: 'Heavy Weapons' },
  { role: 'scout', label: 'Scout' },
  { role: 'ammo', label: 'Ammo Carrier' },
  { role: 'medic', label: 'Medic' },
];

function rosterRow(fields: Partial<HistoryRosterRow> = {}): HistoryRosterRow {
  return {
    player: { id: 21, name: 'Ace' },
    games_played: 4,
    is_merc: false,
    roles: [
      { role: 'commander', games: 3, avg_mvp: 4.256, avg_score: 6120.6 },
      { role: 'heavy', games: 1, avg_mvp: 2, avg_score: 4000 },
      { role: 'scout', games: 0, avg_mvp: null, avg_score: null },
      { role: 'ammo', games: 0, avg_mvp: null, avg_score: null },
      { role: 'medic', games: 0, avg_mvp: null, avg_score: null },
    ],
    ...fields,
  };
}

function matchRow(fields: Partial<HistoryMatchRow> = {}): HistoryMatchRow {
  return {
    id: 40,
    opponent: { id: 4, name: 'Vipers' },
    result: 'won',
    team_points: 10200,
    opponent_points: 9100,
    team_rounds_won: 2,
    opponent_rounds_won: 0,
    date_played: PLAYED,
    is_completed: true,
    ...fields,
  };
}

function singleRow(fields: Partial<HistorySingleRoundRow> = {}): HistorySingleRoundRow {
  return {
    id: 77,
    opponent: { id: 5, name: 'Owls' },
    result: 'won',
    team_points: 5120,
    opponent_points: 4880,
    date_played: PLAYED,
    ...fields,
  };
}

function history(fields: Partial<TeamHistoryData> = {}): TeamHistoryData {
  return {
    team: { id: 3, name: 'Hawks' },
    scope_options: [
      { key: 'freeform', label: 'Free-form matches', selected: true },
      { key: 't2', label: 'Spring Cup', selected: true },
    ],
    show_scope_filter: true,
    has_any_games: true,
    stats: {
      total_matches: 6,
      match_wins: 3,
      match_losses: 1,
      match_ties: 2,
      total_rounds: 3,
      round_wins: 5,
      round_losses: 2,
      round_ties: 1,
    },
    role_columns: ROLE_COLUMNS,
    roster: [rosterRow()],
    opponents: [
      { id: 5, name: 'Owls' },
      { id: 4, name: 'Vipers' },
    ],
    matches: [matchRow()],
    single_rounds: [singleRow()],
    ...fields,
  };
}

describe('Team match history (/teams/:id/history)', () => {
  let http: HttpTestingController;
  let harness: RouterTestingHarness;

  async function setUp(user = USER): Promise<void> {
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

  afterEach(() => http.verify({ ignoreCancelled: true }));

  /** The routed screen inside the shell (the shell's header and sidebar left out). */
  const page = (): HTMLElement => {
    const root = harness.routeNativeElement as HTMLElement;
    return root.querySelector<HTMLElement>('app-team-history') ?? root;
  };
  /** The visible words, one space between elements (jsdom has no `innerText`). */
  const text = (el: Element | null | undefined): string => {
    if (!el) return '';
    const words: string[] = [];
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      words.push(node.textContent ?? '');
    }
    return words.join(' ').replace(/\s+/g, ' ').trim();
  };
  const routerUrl = (): string => TestBed.inject(Router).url;
  const historyRequest = (): TestRequest => http.expectOne((r) => r.url === HISTORY_URL);
  const links = (): HTMLAnchorElement[] => [...page().querySelectorAll('a')];
  const link = (label: string): HTMLAnchorElement | undefined =>
    links().find((a) => text(a) === label);
  const section = (title: string): HTMLElement => {
    const heading = [...page().querySelectorAll('h2')].find((h) => text(h) === title);
    const found = heading?.closest('section');
    if (!found) throw new Error(`no ${title} section`);
    return found as HTMLElement;
  };

  /** Opens `url`, answers its history GET with `body`; returns that request. */
  async function open(
    body: TeamHistoryData = history(),
    url = '/teams/3/history',
    user = USER,
  ): Promise<TestRequest> {
    await setUp(user);
    await harness.navigateByUrl(url);
    const req = historyRequest();
    req.flush({ data: body, meta: {} });
    await harness.fixture.whenStable();
    return req;
  }

  describe('header', () => {
    it('routes to TeamHistory and asks for every scope on a fresh load', async () => {
      const req = await open();
      expect(harness.fixture.debugElement.query(By.directive(TeamHistory))).not.toBeNull();
      expect(req.request.method).toBe('GET');
      expect(req.request.params.has('scope')).toBe(false);
    });

    it('reads "{team} — Match History" with a Back to Team link', async () => {
      await open();
      expect(text(page().querySelector('h1'))).toBe('Hawks — Match History');
      expect(link('← Back to Team')?.getAttribute('href')).toBe('/teams/3');
    });
  });

  describe('tiles', () => {
    it('shows Match W-L-T, Round W-L-T and Total Games from stats', async () => {
      await open();
      const tiles = [...page().querySelectorAll('.th-tile')].map((t) => text(t));
      expect(tiles).toEqual(['3-1-2 Match W-L-T', '5-2-1 Round W-L-T', '9 Total Games']);
    });
  });

  describe('scope filter (INV-1)', () => {
    const box = (label: string): HTMLInputElement => {
      const lbl = [...page().querySelectorAll('#scope-filter label')].find(
        (l) => text(l) === label,
      );
      const input = lbl?.querySelector('input');
      if (!input) throw new Error(`no ${label} checkbox`);
      return input;
    };

    async function toggle(label: string): Promise<TestRequest> {
      box(label).click();
      await harness.fixture.whenStable();
      return historyRequest();
    }

    it('shows Overall and one checkbox per scope, ticked from the response', async () => {
      await open(
        history({
          scope_options: [
            { key: 'freeform', label: 'Free-form matches', selected: false },
            { key: 't2', label: 'Spring Cup', selected: true },
          ],
        }),
      );
      const labels = [...page().querySelectorAll('#scope-filter label')].map((l) => text(l));
      expect(labels).toEqual(['Overall', 'Free-form matches', 'Spring Cup']);
      expect(box('Overall').checked).toBe(false);
      expect(box('Free-form matches').checked).toBe(false);
      expect(box('Spring Cup').checked).toBe(true);
    });

    it('ticks Overall when every scope is selected', async () => {
      await open();
      expect(box('Overall').checked).toBe(true);
    });

    it('is hidden when show_scope_filter is unset', async () => {
      await open(history({ show_scope_filter: false }));
      expect(page().querySelector('#scope-filter')).toBeNull();
      expect(text(page())).not.toContain('Overall');
    });

    it('re-navigates with the still-ticked scopes plus applied on a change', async () => {
      await open();
      const req = await toggle('Free-form matches');
      expect(routerUrl()).toBe('/teams/3/history?scope=t2&applied=1');
      expect(req.request.params.getAll('scope')).toEqual(['t2']);
      req.flush({ data: history(), meta: {} });
    });

    it('sends scope= when the last scope is unticked', async () => {
      await open(
        history({
          scope_options: [
            { key: 'freeform', label: 'Free-form matches', selected: false },
            { key: 't2', label: 'Spring Cup', selected: true },
          ],
        }),
      );
      const req = await toggle('Spring Cup');
      expect(routerUrl()).toBe('/teams/3/history?scope=&applied=1');
      expect(req.request.params.getAll('scope')).toEqual(['']);
      req.flush({ data: history(), meta: {} });
    });

    it('selects every scope when Overall is ticked', async () => {
      await open(
        history({
          scope_options: [
            { key: 'freeform', label: 'Free-form matches', selected: false },
            { key: 't2', label: 'Spring Cup', selected: false },
          ],
        }),
      );
      const req = await toggle('Overall');
      expect(routerUrl()).toBe('/teams/3/history?scope=freeform&scope=t2&applied=1');
      expect(req.request.params.getAll('scope')).toEqual(['freeform', 't2']);
      req.flush({ data: history(), meta: {} });
    });

    it('selects no scope when Overall is unticked', async () => {
      await open();
      const req = await toggle('Overall');
      expect(routerUrl()).toBe('/teams/3/history?scope=&applied=1');
      expect(req.request.params.getAll('scope')).toEqual(['']);
      req.flush({ data: history(), meta: {} });
    });

    it('keeps the picks on a reload of the URL', async () => {
      const req = await open(history(), '/teams/3/history?scope=t2&applied=1');
      expect(req.request.params.getAll('scope')).toEqual(['t2']);
    });

    it('keeps an explicit empty pick on a reload', async () => {
      const req = await open(history(), '/teams/3/history?scope=&applied=1');
      expect(req.request.params.getAll('scope')).toEqual(['']);
    });

    it('drops a malformed scope key from the URL', async () => {
      const req = await open(
        history(),
        '/teams/3/history?scope=bogus&scope=t0&scope=t12&scope=freeform&applied=1',
      );
      expect(req.request.params.getAll('scope')).toEqual(['t12', 'freeform']);
    });

    it('ignores scope without applied, as a fresh load (every scope)', async () => {
      const req = await open(history(), '/teams/3/history?scope=t2');
      expect(req.request.params.has('scope')).toBe(false);
    });
  });

  describe('roster table', () => {
    const headRows = (): string[][] =>
      [...page().querySelectorAll('#roster-table thead tr')].map((tr) =>
        [...tr.querySelectorAll('th')].map((th) => text(th)),
      );
    const bodyRows = (): HTMLTableRowElement[] => [
      ...page().querySelectorAll<HTMLTableRowElement>('#roster-table tbody tr'),
    ];
    const cells = (tr: HTMLTableRowElement): string[] =>
      [...tr.querySelectorAll('td')].map((td) => text(td));

    it('has Merc, Player, Games Played, then Games / Avg. MVP / Avg. Score per Role', async () => {
      await open();
      const [top, sub] = headRows();
      expect(top).toEqual([
        'Merc',
        'Player',
        'Games Played',
        'Commander',
        'Heavy Weapons',
        'Scout',
        'Ammo Carrier',
        'Medic',
      ]);
      expect(sub).toEqual(Array(5).fill(['Games', 'Avg. MVP', 'Avg. Score']).flat());
    });

    it('shows Avg. MVP to 2 decimals and Avg. Score to 0, and a muted 0 for a Role not played', async () => {
      await open();
      const row = bodyRows()[0];
      expect(cells(row)).toEqual([
        '',
        'Ace',
        '4',
        '3',
        '4.26',
        '6121',
        '1',
        '2.00',
        '4000',
        ...Array(9).fill('0'),
      ]);
      const tds = [...row.querySelectorAll('td')];
      expect(tds.slice(3, 9).some((td) => td.classList.contains('muted'))).toBe(false);
      expect(tds.slice(9).every((td) => td.classList.contains('muted'))).toBe(true);
    });

    it('flags a Mercenary, titled "Mercenary (not a rostered member)"', async () => {
      await open(
        history({
          roster: [rosterRow(), rosterRow({ player: { id: 22, name: 'Merc' }, is_merc: true })],
        }),
      );
      const [member, merc] = bodyRows();
      expect(member.querySelector('[title]')).toBeNull();
      const flag = merc.querySelector('td:first-child [title]');
      expect(flag?.getAttribute('title')).toBe('Mercenary (not a rostered member)');
    });

    it("links each Player to their career page (INV-5) and badges the Account's Self-insert (INV-6)", async () => {
      await open(
        history({
          roster: [rosterRow(), rosterRow({ player: { id: SELF_INSERT.id, name: 'Dan' } })],
        }),
        '/teams/3/history',
        { ...USER, self_insert: SELF_INSERT },
      );
      const [ace, dan] = bodyRows();
      expect(ace.querySelector('a')?.getAttribute('href')).toBe('/players/21/stats');
      expect(dan.querySelector('a')?.getAttribute('href')).toBe(`/players/${SELF_INSERT.id}/stats`);
      expect(cells(ace)[1]).toBe('Ace');
      expect(cells(dan)[1]).toBe('Dan You');
    });

    it('scrolls sideways inside its own container', async () => {
      await open();
      const table = page().querySelector('#roster-table') as HTMLElement;
      expect(getComputedStyle(table.parentElement as HTMLElement).overflowX).toBe('auto');
    });

    it('is left out when the roster is empty', async () => {
      await open(history({ roster: [] }));
      expect(page().querySelector('#roster-table')).toBeNull();
    });
  });

  describe('Head-to-Head bar', () => {
    it('links "vs. {opp} — H2H" per opponent to team head-to-head', async () => {
      await open();
      const bar = [...page().querySelectorAll('#h2h-opponents-bar a')].map(
        (a) => `${text(a)} -> ${a.getAttribute('href')}`,
      );
      expect(bar).toEqual([
        'vs. Owls — H2H -> /teams/head-to-head?team_a=3&team_b=5',
        'vs. Vipers — H2H -> /teams/head-to-head?team_a=3&team_b=4',
      ]);
    });

    it('is left out with no opponents', async () => {
      await open(history({ opponents: [] }));
      expect(page().querySelector('#h2h-opponents-bar')).toBeNull();
    });
  });

  describe('"Matches" list', () => {
    const cards = (): string[] =>
      [...section('Matches').querySelectorAll('article')].map((c) => text(c));

    it('shows each Match: vs opponent, result, score, rounds and date', async () => {
      await open();
      expect(cards()).toEqual([
        'vs Vipers Won Score: 10200 - 9100 Rounds: 2 - 0 Oct 5, 2026 14:30 View Details',
      ]);
    });

    it('badges Won, Lost and Tie', async () => {
      await open(
        history({
          matches: [
            matchRow({ id: 1, result: 'won' }),
            matchRow({ id: 2, result: 'lost' }),
            matchRow({ id: 3, result: 'tie' }),
          ],
        }),
      );
      const badges = [...section('Matches').querySelectorAll('article app-result-chip')].map((b) =>
        text(b),
      );
      expect(badges).toEqual(['Won', 'Lost', 'Tie']);
    });

    it('opens the Match with View Details (INV-5)', async () => {
      await open();
      const view = [...section('Matches').querySelectorAll('a')].find(
        (a) => text(a) === 'View Details',
      );
      expect(view?.getAttribute('href')).toBe('/matches/40');
    });

    it('says "No matches played yet." when there are only Single Rounds', async () => {
      await open(history({ matches: [] }));
      expect(text(section('Matches'))).toBe('Matches No matches played yet.');
    });
  });

  describe('"Single Rounds" list', () => {
    const cards = (): string[] =>
      [...section('Single Rounds').querySelectorAll('article')].map((c) => text(c));

    it('shows each Single Round: vs opponent, result, score and date', async () => {
      await open();
      expect(cards()).toEqual(['vs Owls Won Score: 5120 - 4880 Oct 5, 2026 14:30 View Details']);
    });

    it('badges Won, Lost and Tie', async () => {
      await open(
        history({
          single_rounds: [
            singleRow({ id: 1, result: 'won' }),
            singleRow({ id: 2, result: 'lost' }),
            singleRow({ id: 3, result: 'tie' }),
          ],
        }),
      );
      const badges = [...section('Single Rounds').querySelectorAll('article app-result-chip')].map(
        (b) => text(b),
      );
      expect(badges).toEqual(['Won', 'Lost', 'Tie']);
    });

    it('opens the Round with View Details (INV-5)', async () => {
      await open();
      const view = [...section('Single Rounds').querySelectorAll('a')].find(
        (a) => text(a) === 'View Details',
      );
      expect(view?.getAttribute('href')).toBe('/rounds/77');
    });

    it('says "No single rounds played yet." when there are only Matches', async () => {
      await open(history({ single_rounds: [] }));
      expect(text(section('Single Rounds'))).toBe('Single Rounds No single rounds played yet.');
    });
  });

  describe('empty states', () => {
    it('says "No games match the current filter" when every game is out of scope', async () => {
      await open(history({ matches: [], single_rounds: [], has_any_games: true }));
      expect(text(page())).toContain(
        'No games match the current filter ' +
          "Toggle Overall on (or pick a scope above) to see this team's games.",
      );
      expect(text(page())).not.toContain('No matches played yet');
      expect(page().querySelector('article')).toBeNull();
    });

    it('says "No matches played yet" with Create links when the Team never played', async () => {
      await open(
        history({ matches: [], single_rounds: [], has_any_games: false, show_scope_filter: false }),
      );
      expect(text(page())).toContain(
        "No matches played yet This team hasn't participated in any matches or rounds.",
      );
      expect(text(page())).not.toContain('No games match the current filter');
      expect(link('Create Match')?.getAttribute('href')).toBe('/matches/new');
      expect(link('Create Single Round')?.getAttribute('href')).toBe('/rounds/new');
    });

    it('shows no empty state while there are games', async () => {
      await open();
      expect(text(page())).not.toContain('No games match the current filter');
      expect(link('Create Match')).toBeUndefined();
    });
  });

  describe('load failures', () => {
    it('says "Team not found" on a 404 (INV-3), with no results', async () => {
      await setUp();
      await harness.navigateByUrl('/teams/3/history');
      historyRequest().flush(
        { errors: [{ code: 'not_found', message: 'Not found.', field: null }], meta: {} },
        { status: 404, statusText: 'Not Found' },
      );
      await harness.fixture.whenStable();
      expect(text(page().querySelector('h1'))).toBe('Team not found');
      expect(page().querySelector('.th-tile')).toBeNull();
      expect(page().querySelector('[role="alert"]')).toBeNull();
    });

    it("shows any other failure's message in an alert (INV-4)", async () => {
      await setUp();
      await harness.navigateByUrl('/teams/3/history');
      historyRequest().flush(
        { errors: [{ code: 'server_error', message: 'Database down.', field: null }], meta: {} },
        { status: 500, statusText: 'Server Error' },
      );
      await harness.fixture.whenStable();
      expect(text(page().querySelector('[role="alert"]'))).toBe('Database down.');
      expect(text(page())).not.toContain('Team not found');
      expect(page().querySelector('.th-tile')).toBeNull();
    });

    it('says "Team not found" for a non-numeric id, with no request', async () => {
      await setUp();
      await harness.navigateByUrl('/teams/abc/history');
      await harness.fixture.whenStable();
      http.expectNone(() => true);
      expect(text(page().querySelector('h1'))).toBe('Team not found');
      expect(page().querySelector('[role="alert"]')).toBeNull();
    });
  });
});
