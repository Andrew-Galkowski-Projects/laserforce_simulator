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
import { provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { environment } from '../../../environments/environment';
import { LeaguePlayer } from '../../api/league-players.models';
import { SELF_INSERT, USER, signIn } from '../../api/testing/auth-fixtures';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';
import { User } from '../../auth/user';
import { stubCanvasContext } from '../../matches/testing/fake-canvas';
import { LineChart } from '../../shared/line-chart';
import { leagueNav } from '../testing/league-fixtures';
import {
  leaguePlayer,
  leaguePlayerAward,
  leaguePlayerSeasonRow,
  leaguePlayerStatRow,
  ratingsHistoryRow,
  watchResult,
} from '../testing/league-players-fixtures';
import { failure } from '../testing/league-stats-fixtures';
import { releasePlayControls } from '../testing/leagues-loads';

const BASE = environment.apiBaseUrl;
const PATH = '/leagues/1/players/25';
const PLAYER_URL = `${BASE}/leagues/1/players/25/`;
const SELF_INSERT_URL = `${BASE}/accounts/me/self-insert/`;

/** `USER` with Ace (25) as the Self-insert. */
const SELF: User = { ...USER, self_insert: { ...SELF_INSERT, id: 25, name: 'Ace' } };

describe('League player (/leagues/:id/players/:playerId, spec #428)', () => {
  let http: HttpTestingController;
  let harness: RouterTestingHarness;
  let restoreCanvas: () => void;

  // jsdom has no canvas: a no-op 2D context lets the Ratings history chart build.
  beforeEach(() => (restoreCanvas = stubCanvasContext()));

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

  afterEach(() => {
    restoreCanvas();
    document.querySelector('.cdk-overlay-container')?.remove();
    releasePlayControls();
    http.verify();
  });

  const page = (): HTMLElement => harness.routeNativeElement as HTMLElement;
  const q = (selector: string): HTMLElement | null =>
    page().querySelector<HTMLElement>(`app-league-player ${selector}`);
  const qa = (selector: string): HTMLElement[] => [
    ...page().querySelectorAll<HTMLElement>(`app-league-player ${selector}`),
  ];
  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  /** An element's child elements' texts, joined by one space (heading + body, label + value). */
  const parts = (el: Element | null | undefined): string =>
    [...(el?.children ?? [])]
      .map((c) => text(c))
      .filter((s) => s !== '')
      .join(' ');
  const overlay = (): string => document.querySelector('.cdk-overlay-container')?.textContent ?? '';

  function playerRequest(): TestRequest {
    const req = http.expectOne(PLAYER_URL);
    expect(req.request.method).toBe('GET');
    return req;
  }

  /** Opens the page as `user`, answers the nav, then leaves the player GET to the caller. */
  async function navigate(user: User = USER): Promise<void> {
    await setUp(user);
    await harness.navigateByUrl(PATH);
    http.expectOne(`${BASE}/leagues/1/nav/`).flush({ data: leagueNav(), meta: {} });
    await harness.fixture.whenStable();
  }

  /** Opens the page as `user` and answers the player GET with `data`. */
  async function open(data: LeaguePlayer = leaguePlayer(), user: User = USER): Promise<void> {
    await navigate(user);
    playerRequest().flush({ data, meta: {} });
    await harness.fixture.whenStable();
  }

  function button(label: string): HTMLButtonElement | undefined {
    return qa('button').find((b) => text(b) === label) as HTMLButtonElement | undefined;
  }

  function link(label: string): HTMLAnchorElement | undefined {
    return qa('a').find((a) => text(a) === label) as HTMLAnchorElement | undefined;
  }

  describe('header', () => {
    it('puts the ★ flag before the h1 with the Player name', async () => {
      await open();
      expect(text(q('h1'))).toBe('Ace');
      const header = q('#league-player-header');
      const children = [...(header?.children ?? [])].map((c) => c.tagName.toLowerCase());
      expect(children.slice(0, 2)).toEqual(['app-watch-flag', 'h1']);
    });

    it("seeds the ★ from the payload's watched", async () => {
      await open(leaguePlayer({ watched: true }));
      const flag = q('#league-player-header app-watch-flag button');
      expect(flag?.getAttribute('aria-pressed')).toBe('true');
      expect(flag?.getAttribute('aria-label')).toBe('Stop watching Ace');
    });

    it('toggling the ★ sends the watch PUT and re-colours it', async () => {
      await open();
      const flag = (): HTMLElement | null => q('#league-player-header app-watch-flag button');
      expect(flag()?.getAttribute('aria-pressed')).toBe('false');
      flag()?.click();
      await harness.fixture.whenStable();
      const put = http.expectOne(`${BASE}/leagues/1/watch-list/25/`);
      expect(put.request.method).toBe('PUT');
      put.flush({ data: watchResult(true), meta: {} });
      await harness.fixture.whenStable();
      expect(flag()?.getAttribute('aria-pressed')).toBe('true');
      expect(flag()?.classList).toContain('watch-flag--on');
    });

    it("shows the 'You' badge after the name when the Player is the Self-insert", async () => {
      await open(leaguePlayer(), SELF);
      expect(text(q('h1'))).toBe('Ace You');
      expect(q('h1 .badge--you')).not.toBeNull();
    });

    it("shows no 'You' badge for another Self-insert", async () => {
      await open(leaguePlayer(), { ...USER, self_insert: SELF_INSERT });
      expect(text(q('h1'))).toBe('Ace');
      expect(q('.badge--you')).toBeNull();
    });

    it('links Career stats (global) to the global career page', async () => {
      await open();
      expect(link('Career stats (global)')?.getAttribute('href')).toBe('/players/25/stats');
    });
  });

  describe('Self-insert', () => {
    it('offers This is me when can_link_self_insert and the Player is not the Self-insert', async () => {
      await open(leaguePlayer({ can_link_self_insert: true }));
      expect(button('This is me')).toBeDefined();
      expect(button('Unlink')).toBeUndefined();
    });

    it('offers neither button when the Account cannot link the Player', async () => {
      await open(leaguePlayer({ can_link_self_insert: false }));
      expect(button('This is me')).toBeUndefined();
      expect(button('Unlink')).toBeUndefined();
    });

    it('offers Unlink (not This is me) when the Player is the Self-insert', async () => {
      await open(leaguePlayer({ can_link_self_insert: true }), SELF);
      expect(button('Unlink')).toBeDefined();
      expect(button('This is me')).toBeUndefined();
    });

    it('offers Unlink on the Self-insert even when the Account could not link it', async () => {
      await open(leaguePlayer({ can_link_self_insert: false }), SELF);
      expect(button('Unlink')).toBeDefined();
    });

    it('This is me links the Player, says so, and swaps in Unlink and the badge', async () => {
      await open(leaguePlayer({ can_link_self_insert: true }));
      button('This is me')?.click();
      const req = http.expectOne(SELF_INSERT_URL);
      expect(req.request.method).toBe('PUT');
      expect(req.request.body).toEqual({ player: 25 });
      req.flush({ data: SELF, meta: {} });
      await harness.fixture.whenStable();
      expect(overlay()).toContain('"Ace" is now your Self-insert.');
      expect(button('Unlink')).toBeDefined();
      expect(button('This is me')).toBeUndefined();
      expect(text(q('h1'))).toBe('Ace You');
    });

    it("shows the server's message when linking fails, and keeps This is me", async () => {
      await open(leaguePlayer({ can_link_self_insert: true }));
      button('This is me')?.click();
      http
        .expectOne(SELF_INSERT_URL)
        .flush(...failure(400, 'Only a Player on a Team you manage can be your Self-insert.'));
      await harness.fixture.whenStable();
      expect(overlay()).toContain('Only a Player on a Team you manage can be your Self-insert.');
      expect(button('This is me')).toBeDefined();
    });

    it('Unlink unlinks, says so, and swaps back to This is me without the badge', async () => {
      await open(leaguePlayer({ can_link_self_insert: true }), SELF);
      button('Unlink')?.click();
      const req = http.expectOne(SELF_INSERT_URL);
      expect(req.request.method).toBe('DELETE');
      req.flush(null, { status: 204, statusText: 'No Content' });
      await harness.fixture.whenStable();
      expect(overlay()).toContain('Self-insert unlinked.');
      expect(button('This is me')).toBeDefined();
      expect(button('Unlink')).toBeUndefined();
      expect(text(q('h1'))).toBe('Ace');
    });

    it("shows the server's message when unlinking fails, and keeps Unlink", async () => {
      await open(leaguePlayer(), SELF);
      button('Unlink')?.click();
      http.expectOne(SELF_INSERT_URL).flush(...failure(503, 'Try later.'));
      await harness.fixture.whenStable();
      expect(overlay()).toContain('Try later.');
      expect(button('Unlink')).toBeDefined();
    });
  });

  describe('cards', () => {
    it('shows the Overall card: the rating to 0 dp and capitalised Preferred roles', async () => {
      await open();
      expect(text(q('#league-player-overall h2'))).toBe('Overall');
      expect(text(q('#league-player-overall .player-card__value'))).toBe('72');
      const roles = qa('#league-player-overall .player-card__role').map((b) => text(b));
      expect(roles).toEqual(['Commander', 'Heavy']);
      expect(text(q('#league-player-overall'))).toContain('Preferred roles');
    });

    it('rounds the Overall rating half up to 0 dp', async () => {
      await open(leaguePlayer({ overall_rating: 72.5 }));
      expect(text(q('#league-player-overall .player-card__value'))).toBe('73');
    });

    it('leaves out Preferred roles when the Player has none', async () => {
      await open(leaguePlayer({ bio: { ...leaguePlayer().bio, preferred_roles: [] } }));
      expect(text(q('#league-player-overall'))).not.toContain('Preferred roles');
      expect(q('#league-player-overall .player-card__role')).toBeNull();
    });

    it('shows the Potential card: 1 dp and the help line', async () => {
      await open(leaguePlayer({ potential: 80.46 }));
      expect(text(q('#league-player-potential h2'))).toBe('Potential');
      expect(text(q('#league-player-potential .player-card__value'))).toBe('80.5');
      expect(text(q('#league-player-potential'))).toContain('Projected peak overall rating.');
    });

    it('shows "—" for no Potential', async () => {
      await open(leaguePlayer({ potential: null }));
      expect(text(q('#league-player-potential .player-card__value'))).toBe('—');
    });
  });

  describe('rating groups', () => {
    /** The rows of the group card headed `title`, as "label value". */
    function group(title: string): string[] {
      const card = qa('#league-player-ratings .rating-group').find(
        (c) => text(c.querySelector('h2')) === title,
      );
      if (!card) throw new Error(`no ${title} group`);
      return [...card.querySelectorAll('.rating-group__row')].map((r) => parts(r));
    }

    it('shows the five groups in the template order', async () => {
      await open();
      expect(qa('#league-player-ratings .rating-group h2').map((h) => text(h))).toEqual([
        'Awareness',
        'Decision-making',
        'Physical',
        'Team',
        'Role',
      ]);
    });

    it("labels each rating as the template does, with the payload's stats value", async () => {
      await open();
      expect(group('Awareness')).toEqual([
        'Player Awareness 70',
        'Game Awareness 75',
        'Resource Awareness 66',
      ]);
      expect(group('Decision-making')).toEqual(['Decision Making 71']);
      expect(group('Physical')).toEqual([
        'Positioning 69',
        'Stamina 80',
        'Speed 77',
        'Flexibility 64',
        'Adaptability 70',
      ]);
      expect(group('Team')).toEqual(['Communication 72', 'Teamwork 74']);
      expect(group('Role')).toEqual([
        'Offensive Synergy 68',
        'Defensive Synergy 63',
        'Midfield Synergy 65',
        'Resupply Synergy 60',
        'Resupply Efficiency 58',
        'Accuracy 68',
        'Survival 73',
        'Special Usage 70',
      ]);
    });

    it("lays the Role card out in Django's two columns: four rows, filled column by column", async () => {
      await open();
      // The spec runs from `frontend/` (`npm run test:ci`) or from the repo root.
      const scss = 'app/leagues/league-player/league-player.scss';
      const file = [
        join(process.cwd(), 'src', scss),
        join(process.cwd(), 'frontend', 'src', scss),
      ].find((path) => existsSync(path)) as string;
      const css = compile(file).css;
      const rule = /\.rating-group--wide \.rating-group__rows\s*\{([^}]*)\}/.exec(css)?.[1] ?? '';
      expect(rule).toContain('grid-auto-flow: column');
      expect(rule).toContain('grid-template-rows: repeat(4, auto)');
    });
  });

  const headers = (table: string): string[] => qa(`#${table} thead th`).map((th) => text(th));
  const rows = (table: string): string[][] =>
    qa(`#${table} tbody tr`).map((tr) => [...tr.querySelectorAll('td')].map((td) => text(td)));

  describe('Regular Season', () => {
    it('has the h2 and the columns in order', async () => {
      await open();
      expect(text(q('#league-player-rs-heading'))).toBe('Regular Season');
      expect(headers('league-player-rs-stats-table')).toEqual([
        'Year',
        'Team',
        'GP',
        'Points',
        'MVP',
        'Tags',
        'Tagged',
        'Tag Ratio',
        'Acc%',
        'Survival',
        'Lives',
        'Resup',
        'Missiles',
        'Specials',
        'Follow-up',
        'Reaction',
        'Combo Resup',
      ]);
    });

    it('shows one row per Season, then the bold Career row last', async () => {
      const older = leaguePlayerSeasonRow({
        season: { id: 10, name: '2025' },
        team: { id: 5, name: 'Blue Bolts' },
        games: 3,
      });
      await open(leaguePlayer({ seasons: [leaguePlayerSeasonRow(), older] }));
      const stats = [
        '9121',
        '14.2',
        '41',
        '22',
        '1.86',
        '0.4',
        '1s',
        '6',
        '0',
        '3',
        '2',
        '4',
        '1',
        '0',
      ];
      expect(rows('league-player-rs-stats-table')).toEqual([
        ['2026', 'Red Raiders', '8', ...stats],
        ['2025', 'Blue Bolts', '3', ...stats],
        ['Career', 'Red Raiders', '8', ...stats],
      ]);
      const last = qa('#league-player-rs-stats-table tbody tr').at(-1);
      expect(last?.classList).toContain('rs-career');
    });

    it('omits the Career row when career is null', async () => {
      await open(leaguePlayer({ career: null }));
      expect(rows('league-player-rs-stats-table').map((r) => r[0])).toEqual(['2026']);
    });

    it('shows "-" for a row with no Team', async () => {
      await open(leaguePlayer({ seasons: [], career: leaguePlayerStatRow({ team: null }) }));
      expect(rows('league-player-rs-stats-table')[0].slice(0, 2)).toEqual(['Career', '-']);
    });

    it('replaces the table with "<Player> has no recorded Rounds in <League> yet."', async () => {
      await open(leaguePlayer({ seasons: [], career: null }));
      expect(q('#league-player-rs-stats-table')).toBeNull();
      expect(text(q('#league-player-rs-stats-empty'))).toBe(
        'Ace has no recorded Rounds in Metro League yet.',
      );
      expect(text(q('#league-player-rs-heading'))).toBe('Regular Season');
    });
  });

  it('shows the Playoffs, Salaries and Transactions Coming-soon cards', async () => {
    await open();
    expect(parts(q('#league-player-playoffs-stub'))).toBe(
      'Playoffs Coming soon — arrives with LG-02.',
    );
    expect(parts(q('#league-player-salaries-stub'))).toBe(
      'Salaries Coming soon — awaiting a roster-economy model.',
    );
    expect(parts(q('#league-player-transactions-stub'))).toBe(
      'Transactions Coming soon — awaiting a transactions model.',
    );
  });

  describe('Ratings history', () => {
    const history = [
      ratingsHistoryRow(),
      ratingsHistoryRow({
        season: { id: 12, name: 'Season 2' },
        age: null,
        overall_rating: 75,
        potential: null,
      }),
    ];

    const chart = (): LineChart | undefined =>
      harness.fixture.debugElement.query(By.directive(LineChart))?.componentInstance as
        LineChart | undefined;

    it('charts Overall rating by Season number on a count axis', async () => {
      document.documentElement.style.setProperty('--lf-accent', 'rgb(1, 2, 3)');
      try {
        await open(leaguePlayer({ ratings_history: history }));
      } finally {
        document.documentElement.style.removeProperty('--lf-accent');
      }
      expect(text(q('#league-player-ratings-history h2'))).toBe('Ratings history');
      const c = chart();
      expect(c).toBeDefined();
      expect(c?.xAxis()).toBe('count');
      expect(c?.xTitle()).toBe('Season');
      expect(c?.yTitle()).toBe('Overall rating');
      expect(c?.maxSec()).toBe(2);
      // The tooltip and ticks name each point's Season, not its number.
      expect(c?.xLabel()?.(1)).toBe('Season 1');
      expect(c?.xLabel()?.(2)).toBe('Season 2');
      expect(c?.datasets()).toEqual([
        {
          label: 'Overall rating',
          color: 'rgb(1, 2, 3)',
          pointRadius: 2,
          points: [
            { sec: 1, value: 72.4 },
            { sec: 2, value: 75 },
          ],
        },
      ]);
    });

    it('has the table columns in order', async () => {
      await open(leaguePlayer({ ratings_history: history }));
      expect(headers('league-player-ratings-history-table')).toEqual([
        'Season',
        'Age',
        'Ovr',
        'Pot',
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
      ]);
    });

    it('shows one row per Season: name, age, Ovr / Pot to 1 dp (or "—"), the 19 ratings', async () => {
      await open(leaguePlayer({ ratings_history: history }));
      const ratings = [
        '70',
        '75',
        '66',
        '71',
        '69',
        '80',
        '77',
        '64',
        '70',
        '72',
        '74',
        '68',
        '63',
        '65',
        '60',
        '58',
        '68',
        '73',
        '70',
      ];
      expect(rows('league-player-ratings-history-table')).toEqual([
        ['Season 1', '24', '72.4', '80.5', ...ratings],
        ['Season 2', '-', '75.0', '—', ...ratings],
      ]);
    });

    it('shows "No ratings history yet." with neither the chart nor the table when empty', async () => {
      await open(leaguePlayer({ ratings_history: [] }));
      expect(text(q('#league-player-ratings-history-empty'))).toBe('No ratings history yet.');
      expect(chart()).toBeUndefined();
      expect(q('#league-player-ratings-history-table')).toBeNull();
    });
  });

  describe('Awards', () => {
    it("lists each Season's awards under its name, linked to that Season's awards", async () => {
      const second = leaguePlayerAward({
        season: { id: 10, name: 'Season 0' },
        award_labels: ['Rookie of the Year'],
      });
      await open(leaguePlayer({ awards: [leaguePlayerAward(), second] }));
      expect(text(q('#league-player-awards h2'))).toBe('Awards');
      const entries = qa('#league-player-awards .award');
      expect(
        entries.map((e) => {
          const link = e.querySelector('a');
          return [text(link), link?.getAttribute('href')];
        }),
      ).toEqual([
        ['Season 1', '/leagues/1/seasons/11/awards'],
        ['Season 0', '/leagues/1/seasons/10/awards'],
      ]);
      expect(entries.map((e) => [...e.querySelectorAll('li')].map((li) => text(li)))).toEqual([
        ['Season MVP', 'All-League First Team'],
        ['Rookie of the Year'],
      ]);
      expect(q('#league-player-awards app-soon-badge')).toBeNull();
    });

    it('says "No awards won in this League yet." when empty', async () => {
      await open(leaguePlayer({ awards: [] }));
      expect(text(q('#league-player-awards p'))).toBe('No awards won in this League yet.');
      expect(q('#league-player-awards .award')).toBeNull();
    });
  });

  describe('load errors', () => {
    it('a 404 (missing or cross-Account League or Player) shows the not-found state', async () => {
      await navigate();
      playerRequest().flush(...failure(404, 'Not found.'));
      await harness.fixture.whenStable();
      expect(text(q('h1'))).toBe('Player not found');
      expect(link('← Back to League')?.getAttribute('href')).toBe('/leagues/1');
      expect(q('#league-player-header')).toBeNull();
      expect(q('[role="alert"]')).toBeNull();
    });

    it("I2: any other failure shows the server's message with Retry, which re-sends the GET", async () => {
      await navigate();
      playerRequest().flush(...failure(500, 'Database is down.'));
      await harness.fixture.whenStable();
      expect(text(q('[role="alert"] span'))).toBe('Database is down.');
      expect(q('#league-player-header')).toBeNull();
      expect(text(q('h1'))).not.toBe('Player not found');

      const retry = qa('[role="alert"] button').find((b) => text(b) === 'Retry');
      retry?.click();
      await harness.fixture.whenStable();
      playerRequest().flush({ data: leaguePlayer(), meta: {} });
      await harness.fixture.whenStable();
      expect(q('[role="alert"]')).toBeNull();
      expect(text(q('h1'))).toBe('Ace');
    });
  });

  it('I6: the wide tables scroll sideways inside their own box (so they fit at 768px)', async () => {
    await open();
    for (const id of ['league-player-rs-stats-table', 'league-player-ratings-history-table']) {
      expect(q(`#${id}`)?.parentElement?.classList).toContain('stats-scroll');
    }
    // The spec runs from `frontend/` (`npm run test:ci`) or from the repo root.
    const scss = 'app/leagues/league-player/league-player.scss';
    const file = [
      join(process.cwd(), 'src', scss),
      join(process.cwd(), 'frontend', 'src', scss),
    ].find((path) => existsSync(path)) as string;
    const rule = /\.stats-scroll\s*\{([^}]*)\}/.exec(compile(file).css)?.[1] ?? '';
    expect(rule).toContain('max-width: 100%');
    expect(rule).toContain('overflow-x: auto');
  });
});
