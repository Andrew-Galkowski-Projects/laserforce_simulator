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
import { Role } from '../../api/players.models';
import { GameRound, ScoreboardRow } from '../../api/rounds.models';
import { USER, signIn } from '../../api/testing/auth-fixtures';
import { PROFILE, STATS } from '../../api/testing/player-fixtures';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';
import { User } from '../../auth/user';
import { releaseTeamsLoads } from '../../teams/testing/teams-loads';
import { roundDetail } from '../testing/match-fixtures';
import { scoreboardRow, scoreboardSide } from '../testing/scoreboard-fixtures';

const BASE = environment.apiBaseUrl;

/** 09:05 on Mar 17, 2026 in the test machine's own zone, so the HH:mm check is zone-proof. */
const PLAYED = new Date(2026, 2, 17, 9, 5).toISOString();

const HEADERS = [
  'Callsign',
  'Pos',
  'Score',
  'MVP',
  'Hit Diff',
  'Acc %',
  'Medic Hits',
  'Lives',
  'Shots',
  'Msls',
  'Msld',
  'Shot Team',
  'SP',
  'Elim',
];

/** A Single Round, Comets (red, team 3) beating Vipers (blue, team 4), one row per Side. */
function round(overrides: Partial<GameRound> = {}): GameRound {
  return roundDetail({
    id: 9,
    team_red_name: 'Comets',
    team_blue_name: 'Vipers',
    red_points: 5120,
    blue_points: 4880,
    winner: 3,
    winner_name: 'Comets',
    date_played: PLAYED,
    scoreboard: {
      red: scoreboardSide([scoreboardRow({ player_id: 101, name: 'Ace' })]),
      blue: scoreboardSide([
        scoreboardRow({ player_id: 201, name: 'Bolt', team_color: 'blue', mvp: 2 }),
      ]),
    },
    ...overrides,
  });
}

describe('Round detail (/rounds/:id)', () => {
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

  const page = (): HTMLElement => harness.routeNativeElement as HTMLElement;
  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const link = (label: string): HTMLAnchorElement | undefined =>
    [...page().querySelectorAll('a')].find((a) => text(a).includes(label));
  const side = (color: 'red' | 'blue'): HTMLElement => {
    const el = page().querySelector<HTMLElement>(`section[data-lf-side="${color}"]`);
    if (!el) throw new Error(`no ${color} side`);
    return el;
  };
  const strip = (color: 'red' | 'blue'): string => text(side(color).querySelector('.rd-strip'));
  const headers = (color: 'red' | 'blue'): string[] =>
    [...side(color).querySelectorAll('thead th')].map((th) =>
      text(th).replace(/[↕▲▼]/g, '').trim(),
    );
  const rows = (color: 'red' | 'blue'): string[][] =>
    [...side(color).querySelectorAll('tbody tr')].map((tr) =>
      [...tr.querySelectorAll('td')].map((td) => text(td)),
    );
  /** One column of a Side's table, by header label. */
  const column = (color: 'red' | 'blue', label: string): string[] =>
    rows(color).map((r) => r[headers(color).indexOf(label)]);
  const callsigns = (color: 'red' | 'blue'): string[] => column(color, 'Callsign');
  const button = (label: string): HTMLButtonElement => {
    const found = [...page().querySelectorAll('button')].find((b) => text(b).includes(label));
    if (!found) throw new Error(`no ${label} button`);
    return found;
  };

  function roundRequest(id = 9): TestRequest {
    return http.expectOne(`${BASE}/rounds/${id}/`);
  }

  async function open(r: GameRound, user: User = USER): Promise<void> {
    await setUp(user);
    await harness.navigateByUrl(`/rounds/${r.id}`);
    roundRequest(r.id).flush({ data: r, meta: {} });
    await harness.fixture.whenStable();
  }

  /** `open` with one red row (and none on blue): the row under test. */
  async function openRow(row: Partial<ScoreboardRow>): Promise<void> {
    await open(
      round({
        scoreboard: {
          red: scoreboardSide([scoreboardRow(row)]),
          blue: scoreboardSide([]),
        },
      }),
    );
  }

  const cell = (label: string): string => column('red', label)[0];

  async function sortBy(color: 'red' | 'blue', label: string): Promise<void> {
    const th = [...side(color).querySelectorAll('thead th')].find((h) => text(h).startsWith(label));
    const btn = th?.querySelector('button');
    if (!btn) throw new Error(`no ${label} header button`);
    btn.click();
    await harness.fixture.whenStable();
  }

  const ariaSort = (color: 'red' | 'blue', label: string): string | null =>
    [...side(color).querySelectorAll('thead th')]
      .find((h) => text(h).startsWith(label))
      ?.getAttribute('aria-sort') ?? null;

  it('gets the route id Round, once', async () => {
    await setUp();
    await harness.navigateByUrl('/rounds/31');
    const req = roundRequest(31);
    expect(req.request.method).toBe('GET');
    req.flush({ data: round({ id: 31 }), meta: {} });
    await harness.fixture.whenStable();
  });

  describe('page head', () => {
    it('names both teams and reads "Round N · result" (the date stays in the kicker)', async () => {
      await open(round());
      expect(text(page().querySelector('h1'))).toBe('Comets vs Vipers');
      expect(text(page().querySelector('.rd-meta'))).toBe('Round 1 · Comets won');
    });

    it('adds "elimination at m:ss" when a team was eliminated', async () => {
      // 1104 ticks = 552 s = 9:12.
      await open(round({ eliminated_at: 1104, blue_team_eliminated: true }));
      expect(text(page().querySelector('.rd-meta'))).toBe(
        'Round 1 · Comets won · elimination at 9:12',
      );
    });

    it('reads "Tie" when there is no winner', async () => {
      await open(round({ winner: null, winner_name: null }));
      expect(text(page().querySelector('.rd-meta'))).toContain('· Tie');
    });

    it('subtitles a Single Round "Single Round – date", back to Matches', async () => {
      await open(round());
      expect(text(page().querySelector('.rd-kicker'))).toBe('Single Round – Mar 17, 2026 09:05');
      expect(link('Back to Matches')?.getAttribute('href')).toBe('/matches');
      expect([...page().querySelectorAll('a')].map((a) => text(a))).not.toContain(
        '← Back to Match',
      );
    });

    it("links to the Round's Event log", async () => {
      await open(round());
      const events = [...page().querySelectorAll('a')].find((a) => text(a) === 'Event log');
      expect(events?.getAttribute('href')).toBe('/rounds/9/events');
    });

    it("links to the Round's Playback, right after the Event log", async () => {
      await open(round());
      const playback = [...page().querySelectorAll('a')].find((a) => text(a) === 'Playback');
      expect(playback?.getAttribute('href')).toBe('/rounds/9/playback');
      expect(text(playback?.previousElementSibling)).toBe('Event log');
    });

    it("links to the Round's Charts, right after Playback", async () => {
      await open(round());
      const charts = [...page().querySelectorAll('a')].find((a) => text(a) === 'Charts');
      expect(charts?.getAttribute('href')).toBe('/rounds/9/charts');
      expect(text(charts?.previousElementSibling)).toBe('Playback');
    });

    it("links to the Round's Missile log and Heatmap, right after Charts", async () => {
      await open(round());
      const links = [...page().querySelectorAll('a')];
      const missileLog = links.find((a) => text(a) === 'Missile log');
      const heatmap = links.find((a) => text(a) === 'Heatmap');
      expect(missileLog?.getAttribute('href')).toBe('/rounds/9/missile-log');
      expect(text(missileLog?.previousElementSibling)).toBe('Charts');
      expect(heatmap?.getAttribute('href')).toBe('/rounds/9/heatmap');
      expect(text(heatmap?.previousElementSibling)).toBe('Missile log');
    });

    it('links "{team} History" for both Teams', async () => {
      await open(round());
      const history = [...page().querySelectorAll('a')]
        .filter((a) => text(a).endsWith('History'))
        .map((a) => `${text(a)} -> ${a.getAttribute('href')}`);
      expect(history).toEqual([
        'Comets History -> /teams/3/history',
        'Vipers History -> /teams/4/history',
      ]);
    });

    it('leaves out the history link of a Team that no longer exists', async () => {
      await open(round({ team_red: null }));
      const history = [...page().querySelectorAll('a')]
        .filter((a) => text(a).endsWith('History'))
        .map((a) => text(a));
      expect(history).toEqual(['Vipers History']);
    });

    it("opens a Team's history from its link", async () => {
      await open(round());
      const link = [...page().querySelectorAll('a')].find((a) => text(a) === 'Comets History');
      link?.click();
      await harness.fixture.whenStable();
      releaseTeamsLoads();
      expect(TestBed.inject(Router).url).toBe('/teams/3/history');
    });

    it('subtitles a Match round "Match Round N – date", back to its Match', async () => {
      await open(round({ match: 7, round_number: 2 }));
      expect(text(page().querySelector('.rd-kicker'))).toBe('Match Round 2 – Mar 17, 2026 09:05');
      expect(text(page().querySelector('.rd-meta'))).toMatch(/^Round 2 ·/);
      const back = [...page().querySelectorAll('a')].find((a) => text(a) === '← Back to Match');
      expect(back?.getAttribute('href')).toBe('/matches/7');
      expect(link('Back to Matches')).toBeUndefined();
    });
  });

  describe('side strips', () => {
    it('shows each team in its Side with the base score, players and survivors', async () => {
      await open(
        round({
          scoreboard: {
            red: scoreboardSide([
              scoreboardRow({ player_id: 1, name: 'A1', final_lives: 3 }),
              scoreboardRow({ player_id: 2, name: 'A2', final_lives: 0, is_eliminated: true }),
            ]),
            blue: scoreboardSide([scoreboardRow({ player_id: 3, name: 'B1', team_color: 'blue' })]),
          },
        }),
      );
      expect(strip('red')).toContain('Comets');
      expect(strip('red')).toContain('5,120');
      expect(strip('red')).toContain('2 players · 1 survivor');
      expect(strip('blue')).toContain('Vipers');
      expect(strip('blue')).toContain('4,880');
      expect(strip('blue')).toContain('1 player · 1 survivor');
      expect(strip('red')).not.toContain('elim bonus');
    });

    it('shows the elim bonus muted and apart from the base score', async () => {
      await open(
        round({
          scoreboard: {
            red: scoreboardSide([scoreboardRow()], { elim_bonus: 10000 }),
            blue: scoreboardSide(
              [scoreboardRow({ player_id: 201, name: 'Bolt', team_color: 'blue', final_lives: 0 })],
              { eliminated: true },
            ),
          },
        }),
      );
      const bonus = side('red').querySelector('.rd-strip__bonus');
      expect(text(bonus)).toBe('(+10,000 elim bonus)');
      expect(bonus?.classList).toContain('rd-muted');
      expect(text(side('red').querySelector('.rd-strip__points'))).toBe('5,120');
      expect(strip('blue')).not.toContain('elim bonus');
    });

    it('badges Win / Loss and Eliminated', async () => {
      await open(
        round({
          scoreboard: {
            red: scoreboardSide([scoreboardRow()], { elim_bonus: 10000 }),
            blue: scoreboardSide(
              [scoreboardRow({ player_id: 201, name: 'Bolt', team_color: 'blue', final_lives: 0 })],
              { eliminated: true },
            ),
          },
        }),
      );
      const badges = (c: 'red' | 'blue') =>
        [...side(c).querySelectorAll('.rd-badge')].map((b) => text(b));
      expect(badges('red')).toEqual(['Win']);
      expect(badges('blue')).toEqual(['Loss', 'Eliminated']);
    });

    it('badges both Sides "Tie" when there is no winner', async () => {
      await open(round({ winner: null, winner_name: null }));
      expect(text(side('red').querySelector('.rd-badge'))).toBe('Tie');
      expect(text(side('blue').querySelector('.rd-badge'))).toBe('Tie');
    });

    it('says "No player data for this round." on a Side with no rows, keeping its score', async () => {
      await open(round({ scoreboard: { red: scoreboardSide([]), blue: scoreboardSide([]) } }));
      expect(text(side('red'))).toContain('No player data for this round.');
      expect(side('red').querySelector('table')).toBeNull();
      expect(strip('red')).toContain('5,120');
      expect(strip('blue')).toContain('4,880');
    });

    it('shows no Eliminated badge on an empty Side, and no elim bonus against it', async () => {
      // The server never counts a Side with no rows as eliminated, so the other earns no bonus.
      await open(
        round({
          scoreboard: {
            red: scoreboardSide([scoreboardRow()]),
            blue: scoreboardSide([], { eliminated: false, elim_bonus: 0 }),
          },
        }),
      );
      for (const c of ['red', 'blue'] as const) {
        expect(strip(c)).not.toContain('Eliminated');
        expect(strip(c)).not.toContain('elim bonus');
      }
    });
  });

  describe('player table', () => {
    it('has the 14 columns in order', async () => {
      await open(round());
      expect(headers('red')).toEqual(HEADERS);
      expect(headers('blue')).toEqual(HEADERS);
    });

    it('formats a row: CMD, score, MVP, Hit Diff (t/tt), Acc %, Lives, Shots, Shot Team, SP', async () => {
      await openRow({
        role: 'commander',
        points_scored: 9000,
        mvp: 12.5,
        tags_made: 15,
        times_tagged: 12,
        accuracy: 42,
        final_lives: 7,
        final_shots: 18,
        specific_tags_count: 4,
        specials_used: 2,
        special_cost: 20,
        final_special: 5,
        final_medic_hits: 2,
        medic_lives_removed_from_nuke: 3,
        missiles_landed: 3,
        starting_missiles: 5,
        times_missiled: 1,
      });
      expect(rows('red')[0]).toEqual([
        '★ Ace',
        'CMD',
        '9,000',
        '12.50',
        '1.25 (15/12)',
        '42%',
        '2 (3)',
        '7',
        '18',
        '3/5',
        '1',
        '4',
        '40/45',
        'Survived',
      ]);
    });

    it.each<[Role, string]>([
      ['heavy', 'HVY'],
      ['scout', 'SCT'],
      ['ammo', 'AMO'],
      ['medic', 'MED'],
    ])('shows the %s position as %s', async (role, label) => {
      await openRow({ role });
      expect(cell('Pos')).toBe(label);
    });

    it('shows Hit Diff "—" with (t/0) when the player was never tagged', async () => {
      await openRow({ tags_made: 9, times_tagged: 0 });
      expect(cell('Hit Diff')).toBe('— (9/0)');
    });

    it('shows Medic Hits without the nuke count for a non-commander', async () => {
      await openRow({ role: 'medic', final_medic_hits: 4, medic_lives_removed_from_nuke: 3 });
      expect(cell('Medic Hits')).toBe('4');
    });

    it('shows Msls as landed/starting for any row that started with missiles', async () => {
      await openRow({ role: 'scout', missiles_landed: 1, starting_missiles: 3 });
      expect(cell('Msls')).toBe('1/3');
    });

    it('shows Msls as landed only for a row that started with none, even a heavy', async () => {
      await openRow({ role: 'heavy', missiles_landed: 0, starting_missiles: 0 });
      expect(cell('Msls')).toBe('0');
    });

    it('shows an eliminated player with the m:ss, muted danger row and an OUT tag', async () => {
      await openRow({
        is_eliminated: true,
        was_eliminated_at: 1104,
        eliminated_timestamp: '9:12',
        final_lives: 0,
      });
      expect(cell('Elim')).toBe('9:12');
      const tr = side('red').querySelector('tbody tr');
      expect(tr?.classList).toContain('is-out');
      expect(text(tr?.querySelector('.rd-out'))).toBe('OUT');
    });

    it('marks the top MVP across both Sides with ★, every tied player', async () => {
      await open(
        round({
          scoreboard: {
            red: scoreboardSide([
              scoreboardRow({ player_id: 1, name: 'A1', mvp: 9.5, points_scored: 3000 }),
              scoreboardRow({ player_id: 2, name: 'A2', mvp: 3, points_scored: 2000 }),
            ]),
            blue: scoreboardSide([
              scoreboardRow({ player_id: 3, name: 'B1', team_color: 'blue', mvp: 9.5 }),
            ]),
          },
        }),
      );
      expect(callsigns('red')).toEqual(['★ A1', 'A2']);
      expect(callsigns('blue')).toEqual(['★ B1']);
      expect(side('red').querySelectorAll('tr.is-top-mvp').length).toBe(1);
    });

    it('badges the signed-in Account\'s Self-insert row "You"', async () => {
      const self = { ...STATS, ...PROFILE, id: 202, team: 4, name: 'Bolt', preferred_roles: [] };
      await open(
        round({
          scoreboard: {
            red: scoreboardSide([scoreboardRow({ player_id: 101, name: 'Ace' })]),
            blue: scoreboardSide([
              scoreboardRow({ player_id: 202, name: 'Bolt', team_color: 'blue', mvp: 1 }),
            ]),
          },
        }),
        { ...USER, self_insert: self },
      );
      expect(callsigns('blue')).toEqual(['Bolt You']);
      expect(callsigns('red')).toEqual(['★ Ace']);
    });

    it('sorts by Score descending by default, and by a clicked header', async () => {
      await open(
        round({
          scoreboard: {
            red: scoreboardSide([
              scoreboardRow({ player_id: 1, name: 'Cy', points_scored: 3000, accuracy: 20 }),
              scoreboardRow({ player_id: 2, name: 'Ann', points_scored: 2000, accuracy: 60 }),
              scoreboardRow({ player_id: 3, name: 'Bo', points_scored: 1000, accuracy: 40 }),
            ]),
            blue: scoreboardSide([]),
          },
        }),
      );
      const names = () => callsigns('red').map((n) => n.replace('★ ', ''));
      expect(names()).toEqual(['Cy', 'Ann', 'Bo']);
      expect(ariaSort('red', 'Score')).toBe('descending');

      await sortBy('red', 'Score');
      expect(names()).toEqual(['Bo', 'Ann', 'Cy']);
      expect(ariaSort('red', 'Score')).toBe('ascending');

      await sortBy('red', 'Callsign');
      expect(names()).toEqual(['Ann', 'Bo', 'Cy']);
      expect(ariaSort('red', 'Callsign')).toBe('ascending');
      expect(ariaSort('red', 'Score')).toBe('none');

      await sortBy('red', 'Acc %');
      expect(names()).toEqual(['Ann', 'Bo', 'Cy']);
      expect(ariaSort('red', 'Acc %')).toBe('descending');

      await sortBy('red', 'Callsign');
      await sortBy('red', 'Callsign');
      expect(names()).toEqual(['Cy', 'Bo', 'Ann']);
    });

    it("sorts each Side's table on its own", async () => {
      await open(
        round({
          scoreboard: {
            red: scoreboardSide([
              scoreboardRow({ player_id: 1, name: 'Ann', points_scored: 3000 }),
              scoreboardRow({ player_id: 2, name: 'Bo', points_scored: 1000 }),
            ]),
            blue: scoreboardSide([
              scoreboardRow({ player_id: 3, name: 'Cy', team_color: 'blue', points_scored: 5 }),
              scoreboardRow({ player_id: 4, name: 'Di', team_color: 'blue', points_scored: 9 }),
            ]),
          },
        }),
      );
      await sortBy('red', 'Score');
      expect(column('red', 'Score')).toEqual(['1,000', '3,000']);
      expect(column('blue', 'Score')).toEqual(['9', '5']);
    });
  });

  describe('Export PDF', () => {
    function stubObjectUrls() {
      const url = URL as unknown as Record<string, unknown>;
      const before = { create: url['createObjectURL'], revoke: url['revokeObjectURL'] };
      const create = vi.fn(() => 'blob:round-report');
      const revoke = vi.fn();
      url['createObjectURL'] = create;
      url['revokeObjectURL'] = revoke;
      return {
        create,
        revoke,
        restore: () => {
          url['createObjectURL'] = before.create;
          url['revokeObjectURL'] = before.revoke;
        },
      };
    }

    it('downloads the Round report as round-<id>.pdf, "Generating..." and disabled meanwhile', async () => {
      await open(round());
      const urls = stubObjectUrls();
      const downloads: string[] = [];
      const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
        this: HTMLAnchorElement,
      ) {
        downloads.push(this.download);
      });
      try {
        button('Export PDF').click();
        await harness.fixture.whenStable();
        const req = http.expectOne(`${BASE}/rounds/9/report/`);
        expect(req.request.method).toBe('GET');
        expect(req.request.responseType).toBe('blob');
        const busy = button('Generating...');
        expect(busy.disabled).toBe(true);
        expect(downloads).toEqual([]);

        req.flush(new Blob(['%PDF-1.4'], { type: 'application/pdf' }));
        await harness.fixture.whenStable();
        expect(urls.create).toHaveBeenCalledTimes(1);
        expect(downloads).toEqual(['round-9.pdf']);
        expect(urls.revoke).toHaveBeenCalledWith('blob:round-report');
        expect(button('Export PDF').disabled).toBe(false);
        expect(TestBed.inject(Router).url).toBe('/rounds/9');
      } finally {
        click.mockRestore();
        urls.restore();
      }
    });

    it('shows a failed report in an alert and re-enables the button', async () => {
      await open(round());
      button('Export PDF').click();
      await harness.fixture.whenStable();
      http
        .expectOne(`${BASE}/rounds/9/report/`)
        .flush(new Blob([]), { status: 500, statusText: 'Server Error' });
      await harness.fixture.whenStable();
      expect(text(page().querySelector('[role="alert"]'))).toContain('Could not generate the PDF.');
      expect(button('Export PDF').disabled).toBe(false);
    });
  });

  describe('load failures', () => {
    it('shows "Round not found" with the back link on a 404 (missing or another Account\'s)', async () => {
      await setUp();
      await harness.navigateByUrl('/rounds/99');
      roundRequest(99).flush(
        { errors: [{ code: 'not_found', message: 'Not found.', field: null }], meta: {} },
        { status: 404, statusText: 'Not Found' },
      );
      await harness.fixture.whenStable();
      expect(text(page().querySelector('h1'))).toBe('Round not found');
      expect(link('Back to Matches')?.getAttribute('href')).toBe('/matches');
      expect(page().querySelector('section[data-lf-side]')).toBeNull();
    });

    it('shows any other failure as an alert', async () => {
      await setUp();
      await harness.navigateByUrl('/rounds/9');
      roundRequest(9).flush(
        { errors: [{ code: 'server_error', message: 'Boom.', field: null }], meta: {} },
        { status: 500, statusText: 'Server Error' },
      );
      await harness.fixture.whenStable();
      expect(text(page().querySelector('[role="alert"]'))).toContain('Could not load the Round.');
      expect(page().querySelector('section[data-lf-side]')).toBeNull();
    });
  });
});
