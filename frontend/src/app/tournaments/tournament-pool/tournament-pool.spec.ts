import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import {
  HttpTestingController,
  TestRequest,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { HarnessLoader } from '@angular/cdk/testing';
import { TestbedHarnessEnvironment } from '@angular/cdk/testing/testbed';
import { MatFormFieldHarness } from '@angular/material/form-field/testing';
import { MatSelectHarness } from '@angular/material/select/testing';
import { MatSnackBarHarness } from '@angular/material/snack-bar/testing';
import { provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { environment } from '../../../environments/environment';
import { Player } from '../../api/players.models';
import { SELF_INSERT, USER, signIn } from '../../api/testing/auth-fixtures';
import { PoolEntry, TournamentDetail } from '../../api/tournaments.models';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';
import { User } from '../../auth/user';
import { tournamentDetail } from '../testing/detail-fixtures';
import { poolEntry, poolPage, poolPlayer, releasePoolLoads } from '../testing/pool-fixtures';

const BASE = environment.apiBaseUrl;

/** A Player-pool Tournament in setup, `pool_size` players. */
const poolTournament = (overrides: Partial<TournamentDetail> = {}): TournamentDetail =>
  tournamentDetail({
    team_assembly: 'player_pool',
    format: 'round_robin_double_elim',
    format_label: 'Random Draw',
    participants: [],
    pool_size: 0,
    ...overrides,
  });

describe('Tournament Player Pool (Tournament detail)', () => {
  let http: HttpTestingController;
  let harness: RouterTestingHarness;
  let loader: HarnessLoader;

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
    loader = TestbedHarnessEnvironment.documentRootLoader(harness.fixture);
  });

  afterEach(() => http.verify());

  const page = (): HTMLElement => harness.routeNativeElement as HTMLElement;
  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const section = (): HTMLElement | null => page().querySelector('app-tournament-pool');
  const stable = (): Promise<void> => harness.fixture.whenStable();

  async function open(detail: TournamentDetail = poolTournament()): Promise<void> {
    await harness.navigateByUrl(`/tournaments/${detail.id}`);
    http.expectOne(`${BASE}/tournaments/${detail.id}/`).flush({ data: detail, meta: {} });
    await stable();
  }

  describe('the heading and size warning', () => {
    const WARNING =
      'The pool must be divisible by 6 and have at least 24 players (4+ teams) before you can draw.';
    const warning = (): HTMLElement | null =>
      section()?.querySelector('.tournament-pool__warning') ?? null;

    it('heads the section "Player Pool (N)" from pool_size', async () => {
      await open(poolTournament({ pool_size: 7 }));
      releasePoolLoads();
      expect(text(section()?.querySelector('h2'))).toBe('Player Pool (7)');
    });

    // 30 (5 Teams of 6) is drawable under the Django rule, so it does not warn; the ticket's
    // "shows for 30" contradicts that rule (reported). 25 and 27 cover "not a multiple of 6".
    it.each([18, 25, 27, 0])('warns for a pool of %i', async (size) => {
      await open(poolTournament({ pool_size: size }));
      releasePoolLoads();
      expect(text(warning())).toBe(WARNING);
    });

    it.each([24, 30])('does not warn for a pool of %i', async (size) => {
      await open(poolTournament({ pool_size: size }));
      releasePoolLoads();
      expect(warning()).toBeNull();
      expect(text(section())).not.toContain('divisible by 6');
    });

    it('is not shown for a preset Tournament', async () => {
      await open(tournamentDetail());
      expect(section()).toBeNull();
      expect(page().textContent).not.toContain('Player Pool');
    });
  });

  const POOL_URL = `${BASE}/tournaments/5/pool/`;
  const PLAYERS_URL = `${BASE}/players/`;

  function poolRequest(pageNo = 1): TestRequest {
    return http.expectOne(
      (req) => req.url === POOL_URL && req.params.get('page') === String(pageNo),
    );
  }

  /** Opens the page and answers the pool GET (and, while editable, the Players GET). */
  async function openWith(
    detail: TournamentDetail = poolTournament(),
    pool: PoolEntry[] = [],
    players: Player[] | null = [],
    user: User = USER,
  ): Promise<void> {
    if (user !== USER) {
      await signIn(user);
    }
    await open(detail);
    poolRequest().flush(poolPage(pool));
    if (players !== null) {
      http.expectOne((req) => req.url === PLAYERS_URL).flush(poolPage(players));
    }
    await stable();
  }

  const rows = (): HTMLElement[] => [
    ...(section()?.querySelectorAll<HTMLElement>('.tournament-pool__table tbody tr') ?? []),
  ];
  const cells = (row: HTMLElement): string[] =>
    [...row.querySelectorAll('td')].map((td) => text(td));

  describe('the pool table', () => {
    it('loads every page of the pool', async () => {
      await open(poolTournament({ pool_size: 3 }));
      const first = poolRequest(1);
      expect(first.request.method).toBe('GET');
      expect(first.request.params.get('page_size')).toBe('100');
      first.flush(poolPage([poolEntry(21, 'Ace'), poolEntry(22, 'Bea')], 1, 2));
      poolRequest(2).flush(poolPage([poolEntry(23, 'Cy')], 2, 2));
      http.expectOne((req) => req.url === PLAYERS_URL).flush(poolPage([]));
      await stable();
      expect(rows().map((row) => cells(row)[0])).toEqual(['Ace', 'Bea', 'Cy']);
    });

    it('shows Player, Tier and Drawn team, with "—" for an empty tier or team', async () => {
      await openWith(poolTournament({ pool_size: 2 }), [
        poolEntry(21, 'Ace', { tier: 2, drawn_team: { id: 61, name: 'Draw Team 1' } }),
        poolEntry(22, 'Bea'),
      ]);
      const heads = [...(section()?.querySelectorAll('.tournament-pool__table th') ?? [])];
      expect(heads.slice(0, 3).map((th) => text(th))).toEqual(['Player', 'Tier', 'Drawn team']);
      expect(rows().map((row) => cells(row).slice(0, 3))).toEqual([
        ['Ace', '2', 'Draw Team 1'],
        ['Bea', '—', '—'],
      ]);
    });

    it('links a Player with a Team to its Player page, and a teamless one to nothing', async () => {
      await openWith(poolTournament({ pool_size: 2 }), [
        poolEntry(21, 'Ace', {}, { id: 40, name: 'Free Agents' }),
        poolEntry(22, 'Bea', {}, null),
      ]);
      expect(rows()[0].querySelector('a')?.getAttribute('href')).toBe('/teams/40/players/21');
      expect(rows()[1].querySelector('a')).toBeNull();
      expect(cells(rows()[1])[0]).toBe('Bea');
    });

    it('shows the You badge only on the signed-in Self-insert Player', async () => {
      const self = { ...SELF_INSERT, id: 22, name: 'Bea' };
      await openWith(
        poolTournament({ pool_size: 2 }),
        [poolEntry(21, 'Ace'), poolEntry(22, 'Bea')],
        [],
        { ...USER, self_insert: self },
      );
      expect(rows()[0].querySelector('.badge--you')).toBeNull();
      expect(text(rows()[1].querySelector('.badge--you'))).toBe('You');
    });

    it('shows no You badge when the Account has no Self-insert', async () => {
      await openWith(poolTournament({ pool_size: 1 }), [poolEntry(9, 'Dan')]);
      expect(section()?.querySelector('.badge--you')).toBeNull();
    });

    it('shows a failed pool load in an alert', async () => {
      await open(poolTournament());
      poolRequest().flush(
        { errors: [{ code: 'server_error', message: 'Database is down.', field: null }], meta: {} },
        { status: 500, statusText: 'Server Error' },
      );
      http.expectOne((req) => req.url === PLAYERS_URL).flush(poolPage([]));
      await stable();
      expect(text(section()?.querySelector('[role="alert"]'))).toBe('Database is down.');
    });
  });

  const cardTitles = (): string[] =>
    [...(section()?.querySelectorAll('.tournament-pool__card h3') ?? [])].map((h) => text(h));
  const removeButtons = (): HTMLButtonElement[] =>
    [...(section()?.querySelectorAll<HTMLButtonElement>('button') ?? [])].filter(
      (b) => text(b) === 'Remove',
    );
  const CARDS = ['Add existing players', 'Generate players', 'Import players (CSV)'];

  describe('the pool controls', () => {
    it('shows the three cards and a Remove per row in setup', async () => {
      await openWith(poolTournament({ pool_size: 2 }), [
        poolEntry(21, 'Ace'),
        poolEntry(22, 'Bea'),
      ]);
      expect(cardTitles()).toEqual(CARDS);
      expect(removeButtons()).toHaveLength(2);
    });

    it.each([
      ['active', { state: 'active' }],
      ['completed', { state: 'completed' }],
      ['embedded', { embedded: true, season: { id: 3, name: 'Season 2026' } }],
    ] as [string, Partial<TournamentDetail>][])(
      'hides the cards and Remove for a %s Tournament, keeping the table',
      async (_label, overrides) => {
        await openWith(
          poolTournament({ pool_size: 2, ...overrides }),
          [poolEntry(21, 'Ace'), poolEntry(22, 'Bea')],
          null,
        );
        expect(cardTitles()).toEqual([]);
        expect(removeButtons()).toEqual([]);
        expect(section()?.querySelector('form')).toBeNull();
        expect(rows().map((row) => cells(row))).toEqual([
          ['Ace', '—', '—'],
          ['Bea', '—', '—'],
        ]);
        expect(section()?.querySelectorAll('.tournament-pool__table th')).toHaveLength(3);
      },
    );
  });

  const field = (label: string): Promise<MatFormFieldHarness> =>
    loader.getHarness(MatFormFieldHarness.with({ floatingLabelText: label }));

  async function playerSelect(): Promise<MatSelectHarness> {
    const control = await (await field('Players')).getControl(MatSelectHarness);
    if (!control) throw new Error('no Players select');
    return control;
  }

  function button(label: string): HTMLButtonElement {
    const found = [...(section()?.querySelectorAll('button') ?? [])].find((b) => text(b) === label);
    if (!found) throw new Error(`no ${label} button`);
    return found;
  }

  const cardOf = (title: string): HTMLElement | null =>
    [...(section()?.querySelectorAll<HTMLElement>('.tournament-pool__card') ?? [])].find(
      (card) => text(card.querySelector('h3')) === title,
    ) ?? null;
  const cardAlert = (title: string): string => text(cardOf(title)?.querySelector('[role="alert"]'));

  async function snackMessage(): Promise<string> {
    return (await loader.getHarness(MatSnackBarHarness)).getMessage();
  }

  /** The reload after a write: the pool, the Players and the host's detail GET. */
  async function answerReload(
    detail: TournamentDetail,
    pool: PoolEntry[],
    players: Player[] = [],
  ): Promise<void> {
    poolRequest().flush(poolPage(pool));
    http.expectOne((req) => req.url === PLAYERS_URL).flush(poolPage(players));
    http.expectOne(`${BASE}/tournaments/${detail.id}/`).flush({ data: detail, meta: {} });
    await stable();
  }

  const ADD = 'Add existing players';
  const ADD_URL = `${BASE}/tournaments/5/pool/players/`;

  describe('Add existing players', () => {
    const PLAYERS = [
      poolPlayer(23, 'Cy', 'Gold Guns'),
      poolPlayer(21, 'Ace'),
      poolPlayer(24, 'Abe', 'Red Raiders'),
      poolPlayer(22, 'Bea'),
    ];

    it('offers every Player not already pooled, sorted by name', async () => {
      await openWith(poolTournament({ pool_size: 1 }), [poolEntry(21, 'Ace')], PLAYERS);
      const select = await playerSelect();
      await select.open();
      const options = await Promise.all((await select.getOptions()).map((o) => o.getText()));
      await select.close();
      expect(options).toEqual(['Abe (Red Raiders)', 'Bea (Free Agents)', 'Cy (Gold Guns)']);
    });

    it('POSTs {player_ids}, shows "Added N players." and reloads the pool and detail', async () => {
      const detail = poolTournament({ pool_size: 1 });
      await openWith(detail, [poolEntry(21, 'Ace')], PLAYERS);
      expect(button('Add selected').disabled).toBe(true);
      await (await playerSelect()).clickOptions({ text: 'Abe (Red Raiders)' });
      await (await playerSelect()).clickOptions({ text: 'Cy (Gold Guns)' });
      button('Add selected').click();
      await stable();
      const req = http.expectOne(ADD_URL);
      expect(req.request.method).toBe('POST');
      expect(req.request.body).toEqual({ player_ids: [24, 23] });
      req.flush(
        { data: [poolEntry(24, 'Abe'), poolEntry(23, 'Cy')], meta: {} },
        { status: 201, statusText: 'Created' },
      );
      await stable();
      expect(await snackMessage()).toBe('Added 2 players.');
      const after = [poolEntry(21, 'Ace'), poolEntry(24, 'Abe'), poolEntry(23, 'Cy')];
      await answerReload(poolTournament({ pool_size: 3 }), after, PLAYERS);
      expect(text(section()?.querySelector('h2'))).toBe('Player Pool (3)');
      expect(rows().map((row) => cells(row)[0])).toEqual(['Ace', 'Abe', 'Cy']);
      const select = await playerSelect();
      expect(await select.getValueText()).toBe('');
      // The added ids leave the options, so only the cleared control keeps Add from re-posting them.
      expect(button('Add selected').disabled).toBe(true);
      await select.open();
      const options = await Promise.all((await select.getOptions()).map((o) => o.getText()));
      await select.close();
      expect(options).toEqual(['Bea (Free Agents)']);
    });

    it('says "Added 1 player." for a single Player', async () => {
      await openWith(poolTournament(), [], PLAYERS);
      await (await playerSelect()).clickOptions({ text: 'Bea (Free Agents)' });
      button('Add selected').click();
      await stable();
      http
        .expectOne(ADD_URL)
        .flush({ data: [poolEntry(22, 'Bea')], meta: {} }, { status: 201, statusText: 'Created' });
      await stable();
      expect(await snackMessage()).toBe('Added 1 player.');
      await answerReload(poolTournament({ pool_size: 1 }), [poolEntry(22, 'Bea')], PLAYERS);
    });

    it('puts a player_ids error on the picker and keeps the selection', async () => {
      await openWith(poolTournament(), [], PLAYERS);
      await (await playerSelect()).clickOptions({ text: 'Bea (Free Agents)' });
      button('Add selected').click();
      await stable();
      http.expectOne(ADD_URL).flush(
        {
          errors: [
            {
              code: 'already_in_pool',
              message: 'Player 22 is already in the pool.',
              field: 'player_ids',
            },
          ],
          meta: {},
        },
        { status: 400, statusText: 'Bad Request' },
      );
      await stable();
      expect(await (await field('Players')).getTextErrors()).toEqual([
        'Player 22 is already in the pool.',
      ]);
      expect(cardAlert(ADD)).toBe('');
      expect(await (await playerSelect()).getValueText()).toBe('Bea (Free Agents)');
      expect(button('Add selected').disabled).toBe(false);
    });

    it('shows a stale 409 in the card alert', async () => {
      await openWith(poolTournament(), [], PLAYERS);
      await (await playerSelect()).clickOptions({ text: 'Bea (Free Agents)' });
      button('Add selected').click();
      await stable();
      http.expectOne(ADD_URL).flush(
        {
          errors: [
            {
              code: 'tournament_not_setup',
              message: 'The Tournament is no longer in setup.',
              field: null,
            },
          ],
          meta: {},
        },
        { status: 409, statusText: 'Conflict' },
      );
      await stable();
      expect(cardAlert(ADD)).toBe('The Tournament is no longer in setup.');
      expect(await (await playerSelect()).getValueText()).toBe('Bea (Free Agents)');
    });

    it('disables Add selected while the request is in flight', async () => {
      await openWith(poolTournament(), [], PLAYERS);
      await (await playerSelect()).clickOptions({ text: 'Bea (Free Agents)' });
      button('Add selected').click();
      await stable();
      expect(button('Add selected').disabled).toBe(true);
      button('Add selected').click();
      await stable();
      http
        .expectOne(ADD_URL)
        .flush(
          { errors: [{ code: 'x', message: 'Nope.', field: null }], meta: {} },
          { status: 400, statusText: 'Bad Request' },
        );
      await stable();
      expect(button('Add selected').disabled).toBe(false);
    });
  });

  const GENERATE = 'Generate players';
  const GENERATE_URL = `${BASE}/tournaments/5/pool/generate/`;

  describe('Generate players', () => {
    function numberInput(name: string): HTMLInputElement {
      const input = cardOf(GENERATE)?.querySelector<HTMLInputElement>(`input[name="${name}"]`);
      if (!input) throw new Error(`no ${name} input`);
      return input;
    }

    async function type(name: string, value: string): Promise<void> {
      const input = numberInput(name);
      input.value = value;
      input.dispatchEvent(new Event('input'));
      await stable();
    }

    it.each([
      ['count', 'How many', '24', '1', '1000'],
      ['mean', 'Mean', '50', '0', '100'],
      ['std_dev', 'Std dev', '15', '1', '40'],
    ])(
      'has a %s control "%s", default %s, with the Form bounds %s..%s',
      async (name, label, value, min, max) => {
        await openWith();
        const input = numberInput(name);
        expect(input.type).toBe('number');
        expect(input.value).toBe(value);
        expect(input.getAttribute('min')).toBe(min);
        expect(input.getAttribute('max')).toBe(max);
        expect(await (await field(label)).getControl()).not.toBeNull();
      },
    );

    it('POSTs {count, mean, std_dev}, shows "Generated N players." and reloads', async () => {
      await openWith();
      await type('count', '6');
      await type('mean', '60');
      await type('std_dev', '10');
      button('Generate').click();
      await stable();
      const req = http.expectOne(GENERATE_URL);
      expect(req.request.method).toBe('POST');
      expect(req.request.body).toEqual({ count: 6, mean: 60, std_dev: 10 });
      const made = [1, 2, 3, 4, 5, 6].map((i) => poolEntry(100 + i, `Gen ${i}`));
      req.flush({ data: made, meta: {} }, { status: 201, statusText: 'Created' });
      await stable();
      expect(await snackMessage()).toBe('Generated 6 players.');
      await answerReload(poolTournament({ pool_size: 6 }), made);
      expect(text(section()?.querySelector('h2'))).toBe('Player Pool (6)');
      expect(rows()).toHaveLength(6);
    });

    it('says "Generated 1 player." for a single Player', async () => {
      await openWith();
      await type('count', '1');
      button('Generate').click();
      await stable();
      http
        .expectOne(GENERATE_URL)
        .flush({ data: [poolEntry(101, 'Gen')], meta: {} }, { status: 201, statusText: 'Created' });
      await stable();
      expect(await snackMessage()).toBe('Generated 1 player.');
      await answerReload(poolTournament({ pool_size: 1 }), [poolEntry(101, 'Gen')]);
    });

    it.each([
      ['count', 'How many', '1001', 'Ensure this value is less than or equal to 1000.'],
      ['count', 'How many', '0', 'Ensure this value is greater than or equal to 1.'],
      ['count', 'How many', '', 'This field is required.'],
      ['mean', 'Mean', '101', 'Ensure this value is less than or equal to 100.'],
      ['mean', 'Mean', '-1', 'Ensure this value is greater than or equal to 0.'],
      ['std_dev', 'Std dev', '41', 'Ensure this value is less than or equal to 40.'],
      ['std_dev', 'Std dev', '0', 'Ensure this value is greater than or equal to 1.'],
    ])(
      'keeps %s "%s" = "%s" on the client with the Form message, sending nothing',
      async (name, label, value, message) => {
        await openWith();
        await type(name, value);
        button('Generate').click();
        await stable();
        http.expectNone(GENERATE_URL);
        expect(await (await field(label)).getTextErrors()).toEqual([message]);
        expect(numberInput(name).value).toBe(value);
      },
    );

    it.each([
      ['count', 'How many', '30', 'Too many players for this pool.'],
      ['mean', 'Mean', '60', 'Mean is out of range.'],
      ['std_dev', 'Std dev', '10', 'Std dev is out of range.'],
    ])(
      'puts a server %s error on "%s" and keeps the input',
      async (name, label, value, message) => {
        await openWith();
        await type(name, value);
        button('Generate').click();
        await stable();
        http
          .expectOne(GENERATE_URL)
          .flush(
            { errors: [{ code: 'invalid', message, field: name }], meta: {} },
            { status: 400, statusText: 'Bad Request' },
          );
        await stable();
        expect(await (await field(label)).getTextErrors()).toEqual([message]);
        expect(cardAlert(GENERATE)).toBe('');
        expect(numberInput(name).value).toBe(value);
      },
    );

    it('shows a stale 409 in the card alert', async () => {
      await openWith();
      button('Generate').click();
      await stable();
      http.expectOne(GENERATE_URL).flush(
        {
          errors: [
            {
              code: 'tournament_not_setup',
              message: 'The Tournament is no longer in setup.',
              field: null,
            },
          ],
          meta: {},
        },
        { status: 409, statusText: 'Conflict' },
      );
      await stable();
      expect(cardAlert(GENERATE)).toBe('The Tournament is no longer in setup.');
      expect(numberInput('count').value).toBe('24');
    });

    it('disables Generate while the request is in flight', async () => {
      await openWith();
      button('Generate').click();
      await stable();
      expect(button('Generate').disabled).toBe(true);
      button('Generate').click();
      await stable();
      http
        .expectOne(GENERATE_URL)
        .flush(
          { errors: [{ code: 'x', message: 'Nope.', field: null }], meta: {} },
          { status: 400, statusText: 'Bad Request' },
        );
      await stable();
      expect(button('Generate').disabled).toBe(false);
    });

    it('clears a failed pool load once a reload succeeds', async () => {
      await open(poolTournament());
      poolRequest().flush(
        { errors: [{ code: 'server_error', message: 'Database is down.', field: null }], meta: {} },
        { status: 500, statusText: 'Server Error' },
      );
      http.expectOne((req) => req.url === PLAYERS_URL).flush(poolPage([]));
      await stable();
      button('Generate').click();
      await stable();
      http
        .expectOne(GENERATE_URL)
        .flush({ data: [poolEntry(101, 'Gen')], meta: {} }, { status: 201, statusText: 'Created' });
      await stable();
      await answerReload(poolTournament({ pool_size: 1 }), [poolEntry(101, 'Gen')]);
      expect(text(section()?.querySelector('[role="alert"]'))).toBe('');
      expect(rows()).toHaveLength(1);
    });
  });

  const IMPORT = 'Import players (CSV)';
  const IMPORT_URL = `${BASE}/tournaments/5/pool/import/`;

  describe('Import players (CSV)', () => {
    function fileInput(): HTMLInputElement {
      const input = cardOf(IMPORT)?.querySelector<HTMLInputElement>(
        'input[type="file"][name="csv_file"]',
      );
      if (!input) throw new Error('no csv_file input');
      return input;
    }

    async function choose(file: File): Promise<void> {
      const input = fileInput();
      Object.defineProperty(input, 'files', { value: [file], configurable: true });
      input.dispatchEvent(new Event('change'));
      await stable();
    }

    const csv = (name = 'pool.csv'): File =>
      new File(['team,name,role\nFree Agents,Ace,commander\n'], name, { type: 'text/csv' });

    const rowErrors = (): string[] =>
      [...(cardOf(IMPORT)?.querySelectorAll('.tournament-pool__rows li') ?? [])].map((li) =>
        text(li),
      );
    const fileError = (): string =>
      text(fileInput().closest('.tournament-pool__file')?.querySelector('mat-error'));

    async function failWith(
      errors: { code: string; message: string; field: string | null }[],
      status = 400,
    ): Promise<void> {
      button('Import players').click();
      await stable();
      http.expectOne(IMPORT_URL).flush({ errors, meta: {} }, { status, statusText: 'Bad Request' });
      await stable();
    }

    it('has a CSV chooser, Import players (off until a file is chosen) and the template action', async () => {
      await openWith();
      expect(fileInput().getAttribute('accept')).toBe('.csv,text/csv');
      expect(text(cardOf(IMPORT))).toContain('No file chosen');
      expect(button('Import players').type).toBe('submit');
      expect(button('Import players').disabled).toBe(true);
      expect(cardOf(IMPORT)?.contains(button('Download a template CSV'))).toBe(true);
    });

    it('POSTs the file as multipart csv_file, shows "Imported N players." and reloads', async () => {
      await openWith();
      const file = csv('autumn-pool.csv');
      await choose(file);
      expect(text(cardOf(IMPORT))).toContain('autumn-pool.csv');
      button('Import players').click();
      await stable();
      const req = http.expectOne(IMPORT_URL);
      expect(req.request.method).toBe('POST');
      const form = req.request.body as FormData;
      expect([...form.keys()]).toEqual(['csv_file']);
      expect(form.get('csv_file')).toBe(file);
      const made = [poolEntry(31, 'Ace'), poolEntry(32, 'Bea')];
      req.flush({ data: made, meta: {} }, { status: 201, statusText: 'Created' });
      await stable();
      expect(await snackMessage()).toBe('Imported 2 players.');
      await answerReload(poolTournament({ pool_size: 2 }), made);
      expect(text(section()?.querySelector('h2'))).toBe('Player Pool (2)');
      expect(rows().map((row) => cells(row)[0])).toEqual(['Ace', 'Bea']);
      expect(text(cardOf(IMPORT))).toContain('No file chosen');
    });

    it('says "Imported 1 player." for a single Player', async () => {
      await openWith();
      await choose(csv());
      button('Import players').click();
      await stable();
      http
        .expectOne(IMPORT_URL)
        .flush({ data: [poolEntry(31, 'Ace')], meta: {} }, { status: 201, statusText: 'Created' });
      await stable();
      expect(await snackMessage()).toBe('Imported 1 player.');
      await answerReload(poolTournament({ pool_size: 1 }), [poolEntry(31, 'Ace')]);
    });

    it('lists row errors as "Row N (column): message" and keeps the chosen file', async () => {
      await openWith();
      await choose(csv('autumn-pool.csv'));
      await failWith([
        { code: 'invalid', message: 'Unknown role "pilot".', field: 'row:2:role' },
        { code: 'invalid', message: 'Row has 7 columns, expected 8.', field: 'row:4' },
      ]);
      expect(rowErrors()).toEqual([
        'Row 2 (role): Unknown role "pilot".',
        'Row 4: Row has 7 columns, expected 8.',
      ]);
      expect(cardAlert(IMPORT)).toBe('');
      expect(fileError()).toBe('');
      expect(text(cardOf(IMPORT))).toContain('autumn-pool.csv');
      expect(button('Import players').disabled).toBe(false);
    });

    it('shows a csv_file error on the file control', async () => {
      await openWith();
      await choose(csv());
      await failWith([
        { code: 'invalid', message: 'The file must be UTF-8 encoded.', field: 'csv_file' },
      ]);
      expect(fileError()).toBe('The file must be UTF-8 encoded.');
      expect(cardAlert(IMPORT)).toBe('');
      expect(rowErrors()).toEqual([]);
    });

    it('shows a stale 409 in the card alert', async () => {
      await openWith();
      await choose(csv());
      await failWith(
        [
          {
            code: 'tournament_not_setup',
            message: 'The Tournament is no longer in setup.',
            field: null,
          },
        ],
        409,
      );
      expect(cardAlert(IMPORT)).toBe('The Tournament is no longer in setup.');
    });

    it('clears the previous errors when Import players is clicked again', async () => {
      await openWith();
      await choose(csv());
      await failWith([
        { code: 'invalid', message: 'Unknown role.', field: 'row:1:role' },
        { code: 'invalid', message: 'Nothing to import.', field: null },
      ]);
      expect(rowErrors()).toHaveLength(1);
      button('Import players').click();
      await stable();
      expect(rowErrors()).toEqual([]);
      expect(cardAlert(IMPORT)).toBe('');
      http
        .expectOne(IMPORT_URL)
        .flush(
          { errors: [{ code: 'x', message: 'x', field: null }], meta: {} },
          { status: 400, statusText: 'Bad Request' },
        );
      await stable();
    });

    it('disables Import players while the request is in flight', async () => {
      await openWith();
      await choose(csv());
      button('Import players').click();
      await stable();
      expect(button('Import players').disabled).toBe(true);
      button('Import players').click();
      await stable();
      http
        .expectOne(IMPORT_URL)
        .flush(
          { errors: [{ code: 'x', message: 'Nope.', field: null }], meta: {} },
          { status: 400, statusText: 'Bad Request' },
        );
      await stable();
      expect(button('Import players').disabled).toBe(false);
    });

    it('downloads the template CSV through the API and saves the blob', async () => {
      await openWith();
      const url = URL as unknown as Record<string, unknown>;
      const before = { create: url['createObjectURL'], revoke: url['revokeObjectURL'] };
      url['createObjectURL'] = vi.fn(() => 'blob:pool-template');
      url['revokeObjectURL'] = vi.fn();
      const downloads: string[] = [];
      const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
        this: HTMLAnchorElement,
      ) {
        downloads.push(this.download);
      });
      try {
        button('Download a template CSV').click();
        await stable();
        const req = http.expectOne(`${BASE}/teams/import/template/`);
        expect(req.request.method).toBe('GET');
        req.flush(new Blob(['team,name\n'], { type: 'text/csv' }));
        await stable();
        expect(downloads).toEqual(['roster_template.csv']);
      } finally {
        click.mockRestore();
        url['createObjectURL'] = before.create;
        url['revokeObjectURL'] = before.revoke;
      }
    });

    it('shows a failed template download in the card alert', async () => {
      await openWith();
      button('Download a template CSV').click();
      await stable();
      http
        .expectOne(`${BASE}/teams/import/template/`)
        .flush(new Blob(['nope']), { status: 500, statusText: 'Server Error' });
      await stable();
      expect(cardAlert(IMPORT)).toBe('The server responded with 500.');
    });
  });

  describe('Remove', () => {
    const removeUrl = (id: number): string => `${BASE}/tournaments/5/pool/players/${id}/`;
    const ENTRIES = [poolEntry(21, 'Ace'), poolEntry(22, 'Bea')];

    it('DELETEs at once (no confirm), shows "Removed <name>." and reloads', async () => {
      await openWith(poolTournament({ pool_size: 2 }), ENTRIES);
      removeButtons()[1].click();
      await stable();
      expect(document.querySelector('mat-dialog-container')).toBeNull();
      const req = http.expectOne(removeUrl(22));
      expect(req.request.method).toBe('DELETE');
      req.flush(null, { status: 204, statusText: 'No Content' });
      await stable();
      expect(await snackMessage()).toBe('Removed Bea.');
      await answerReload(poolTournament({ pool_size: 1 }), [ENTRIES[0]]);
      expect(text(section()?.querySelector('h2'))).toBe('Player Pool (1)');
      expect(rows().map((row) => cells(row)[0])).toEqual(['Ace']);
    });

    it('shows a failed Remove in the pool alert and keeps the row', async () => {
      await openWith(poolTournament({ pool_size: 2 }), ENTRIES);
      removeButtons()[0].click();
      await stable();
      http.expectOne(removeUrl(21)).flush(
        {
          errors: [
            {
              code: 'tournament_not_setup',
              message: 'The Tournament is no longer in setup.',
              field: null,
            },
          ],
          meta: {},
        },
        { status: 409, statusText: 'Conflict' },
      );
      await stable();
      expect(text(section()?.querySelector('.tournament-pool__table-alert'))).toBe(
        'The Tournament is no longer in setup.',
      );
      expect(rows()).toHaveLength(2);
    });

    it('disables Remove while a remove is in flight', async () => {
      await openWith(poolTournament({ pool_size: 2 }), ENTRIES);
      removeButtons()[0].click();
      await stable();
      expect(removeButtons().every((b) => b.disabled)).toBe(true);
      removeButtons()[0].click();
      await stable();
      http.expectOne(removeUrl(21)).flush(
        {
          errors: [{ code: 'not_found', message: 'That Player is not in the pool.', field: null }],
          meta: {},
        },
        { status: 404, statusText: 'Not Found' },
      );
      await stable();
      expect(removeButtons().every((b) => !b.disabled)).toBe(true);
    });
  });

  const DRAW_URL = `${BASE}/tournaments/5/draw/`;
  const findButton = (label: string): HTMLButtonElement | null =>
    [...(section()?.querySelectorAll<HTMLButtonElement>('button') ?? [])].find(
      (b) => text(b) === label,
    ) ?? null;
  const drawArea = (): HTMLElement | null =>
    section()?.querySelector<HTMLElement>('.tournament-pool__draw') ?? null;

  /** Two drawn Teams, as the participants of a drawn Player-pool Tournament. */
  const DRAWN_TEAMS = [
    { team: { id: 61, name: 'Draw Team 1' }, seed: 1 },
    { team: { id: 62, name: 'Draw Team 2' }, seed: 2 },
  ];
  const drawn = (overrides: Partial<TournamentDetail> = {}): TournamentDetail =>
    poolTournament({ pool_size: 24, is_drawn: true, participants: DRAWN_TEAMS, ...overrides });
  const T1 = { id: 61, name: 'Draw Team 1' };
  const T2 = { id: 62, name: 'Draw Team 2' };
  const DRAWN_ENTRIES = [
    poolEntry(21, 'Ace', { tier: 1, drawn_team: T1 }),
    poolEntry(22, 'Bea', { tier: 2, drawn_team: T2 }),
    poolEntry(23, 'Cy', { tier: 3, drawn_team: T1 }),
  ];

  describe('Draw', () => {
    it('offers "Draw teams" before the draw and "Re-roll draw" after it', async () => {
      await openWith(poolTournament({ pool_size: 24 }));
      expect(findButton('Draw teams')).not.toBeNull();
      expect(findButton('Re-roll draw')).toBeNull();
    });

    it('offers "Re-roll draw" once drawn', async () => {
      await openWith(drawn(), DRAWN_ENTRIES);
      expect(findButton('Re-roll draw')).not.toBeNull();
      expect(findButton('Draw teams')).toBeNull();
    });

    it.each([
      [18, true],
      [25, true],
      [24, false],
      [30, false],
    ])('for a pool of %i, disabled is %s', async (size, disabled) => {
      await openWith(poolTournament({ pool_size: size }));
      expect(findButton('Draw teams')?.disabled).toBe(disabled);
    });

    it('POSTs draw, notifies "Teams drawn." and reloads the pool and detail', async () => {
      await openWith(poolTournament({ pool_size: 24 }));
      findButton('Draw teams')?.click();
      await stable();
      const req = http.expectOne(DRAW_URL);
      expect(req.request.method).toBe('POST');
      req.flush({ data: drawn(), meta: {} });
      await stable();
      expect(await snackMessage()).toBe('Teams drawn.');
      await answerReload(drawn(), DRAWN_ENTRIES);
      expect(rows().map((row) => cells(row)[2])).toEqual([
        'Draw Team 1',
        'Draw Team 2',
        'Draw Team 1',
      ]);
      expect(findButton('Re-roll draw')).not.toBeNull();
    });

    it('notifies "Draw re-rolled." after a re-roll', async () => {
      await openWith(drawn(), DRAWN_ENTRIES);
      findButton('Re-roll draw')?.click();
      await stable();
      http.expectOne(DRAW_URL).flush({ data: drawn(), meta: {} });
      await stable();
      expect(await snackMessage()).toBe('Draw re-rolled.');
      await answerReload(drawn(), DRAWN_ENTRIES);
    });

    it('shows a draw 400 (the rule message) in an error box', async () => {
      await openWith(poolTournament({ pool_size: 24 }));
      findButton('Draw teams')?.click();
      await stable();
      http.expectOne(DRAW_URL).flush(
        {
          errors: [{ code: 'invalid', message: 'Pool must be divisible by 6.', field: null }],
          meta: {},
        },
        { status: 400, statusText: 'Bad Request' },
      );
      await stable();
      expect(text(drawArea()?.querySelector('[role="alert"]'))).toBe(
        'Pool must be divisible by 6.',
      );
      expect(findButton('Draw teams')?.disabled).toBe(false);
    });

    it('disables the button in flight and clears its error when the next draw starts', async () => {
      await openWith(poolTournament({ pool_size: 24 }));
      findButton('Draw teams')?.click();
      await stable();
      expect(findButton('Draw teams')?.disabled).toBe(true);
      http
        .expectOne(DRAW_URL)
        .flush(
          { errors: [{ code: 'invalid', message: 'Nope.', field: null }], meta: {} },
          { status: 400, statusText: 'Bad Request' },
        );
      await stable();
      expect(text(drawArea()?.querySelector('[role="alert"]'))).toBe('Nope.');
      findButton('Draw teams')?.click();
      await stable();
      expect(drawArea()?.querySelector('[role="alert"]')).toBeNull();
      http
        .expectOne(DRAW_URL)
        .flush(
          { errors: [{ code: 'invalid', message: 'Nope.', field: null }], meta: {} },
          { status: 400, statusText: 'Bad Request' },
        );
      await stable();
    });
  });

  describe('Drawn Teams', () => {
    const teamCards = (): HTMLElement[] => [
      ...(section()?.querySelectorAll<HTMLElement>('.tournament-pool__drawn-team') ?? []),
    ];
    const cardRows = (card: HTMLElement): HTMLElement[] => [
      ...card.querySelectorAll<HTMLElement>('li'),
    ];
    const rowOf = (name: string): HTMLElement => {
      const row = teamCards()
        .flatMap(cardRows)
        .find((li) => text(li.querySelector('.tournament-pool__drawn-name')).startsWith(name));
      if (!row) throw new Error(`no drawn row for ${name}`);
      return row;
    };
    const tierInput = (name: string): HTMLInputElement => {
      const input = rowOf(name).querySelector<HTMLInputElement>('input[name="tier"]');
      if (!input) throw new Error(`no tier input for ${name}`);
      return input;
    };
    async function typeTier(name: string, value: string): Promise<void> {
      const input = tierInput(name);
      input.value = value;
      input.dispatchEvent(new Event('input'));
      await stable();
    }
    const setTier = (name: string): HTMLButtonElement => {
      const found = [...rowOf(name).querySelectorAll('button')].find((b) => text(b) === 'Set tier');
      if (!found) throw new Error(`no Set tier for ${name}`);
      return found;
    };
    const entryUrl = (playerId: number): string =>
      `${BASE}/tournaments/5/draw/entries/${playerId}/`;

    it('is not shown before the draw', async () => {
      await openWith(poolTournament({ pool_size: 24 }), [poolEntry(21, 'Ace')]);
      expect(teamCards()).toEqual([]);
      expect(text(section())).not.toContain('Drawn Teams');
    });

    it("lists one card per drawn Team with its Players' names, You badge and Tier", async () => {
      const self = { ...SELF_INSERT, id: 23, name: 'Cy' };
      await openWith(drawn(), DRAWN_ENTRIES, [], { ...USER, self_insert: self });
      expect(text(section()?.querySelector('.tournament-pool__drawn h3'))).toBe('Drawn Teams');
      expect(teamCards().map((card) => text(card.querySelector('h4')))).toEqual([
        'Draw Team 1',
        'Draw Team 2',
      ]);
      // The name is the row's first text; the badge and "Tier N" are their own elements.
      expect(
        teamCards().map((card) =>
          cardRows(card).map((li) => [
            li.querySelector('.tournament-pool__drawn-name')?.firstChild?.textContent?.trim(),
            text(li.querySelector('.tournament-pool__tier')),
          ]),
        ),
      ).toEqual([
        [
          ['Ace', 'Tier 1'],
          ['Cy', 'Tier 3'],
        ],
        [['Bea', 'Tier 2']],
      ]);
      expect(rowOf('Ace').querySelector('.badge--you')).toBeNull();
      expect(text(rowOf('Cy').querySelector('.badge--you'))).toBe('You');
    });

    it('has a Tier input (required, 1..6) holding the tier on every drawn row in setup', async () => {
      await openWith(drawn(), DRAWN_ENTRIES);
      const input = tierInput('Cy');
      expect(input.type).toBe('number');
      expect(input.value).toBe('3');
      expect(input.hasAttribute('required')).toBe(true);
      expect(input.getAttribute('min')).toBe('1');
      expect(input.getAttribute('max')).toBe('6');
      expect(
        teamCards()
          .flatMap(cardRows)
          .every((li) => li.querySelector('input')),
      ).toBe(true);
    });

    it('PATCHes {tier}, notifies and reloads the pool and detail', async () => {
      await openWith(drawn(), DRAWN_ENTRIES);
      await typeTier('Bea', '5');
      setTier('Bea').click();
      await stable();
      const req = http.expectOne(entryUrl(22));
      expect(req.request.method).toBe('PATCH');
      expect(req.request.body).toEqual({ tier: 5 });
      req.flush({ data: poolEntry(22, 'Bea', { tier: 5, drawn_team: T2 }), meta: {} });
      await stable();
      expect(await snackMessage()).toBe('Set Bea to Tier 5.');
      const after = [
        DRAWN_ENTRIES[0],
        poolEntry(22, 'Bea', { tier: 5, drawn_team: T2 }),
        DRAWN_ENTRIES[2],
      ];
      await answerReload(drawn(), after);
      expect(text(rowOf('Bea').querySelector('.tournament-pool__tier'))).toBe('Tier 5');
    });

    it.each([
      ['7', 'Ensure this value is less than or equal to 6.'],
      ['0', 'Ensure this value is greater than or equal to 1.'],
      ['', 'This field is required.'],
    ])('keeps a tier of "%s" on the client with "%s", sending nothing', async (value, message) => {
      await openWith(drawn(), DRAWN_ENTRIES);
      await typeTier('Ace', value);
      setTier('Ace').click();
      await stable();
      http.expectNone(entryUrl(21));
      expect(text(rowOf('Ace').querySelector('mat-error'))).toBe(message);
      expect(rowOf('Cy').querySelector('mat-error')).toBeNull();
    });

    it('shows a Set tier error on that row only', async () => {
      await openWith(drawn(), DRAWN_ENTRIES);
      setTier('Cy').click();
      await stable();
      http.expectOne(entryUrl(23)).flush(
        {
          errors: [
            {
              code: 'tournament_not_setup',
              message: 'The Tournament is no longer in setup.',
              field: null,
            },
          ],
          meta: {},
        },
        { status: 409, statusText: 'Conflict' },
      );
      await stable();
      expect(text(rowOf('Cy').querySelector('[role="alert"]'))).toBe(
        'The Tournament is no longer in setup.',
      );
      expect(rowOf('Ace').querySelector('[role="alert"]')).toBeNull();
      expect(rowOf('Bea').querySelector('[role="alert"]')).toBeNull();
    });

    it('disables Set tier in flight and clears its error when the next request starts', async () => {
      await openWith(drawn(), DRAWN_ENTRIES);
      setTier('Ace').click();
      await stable();
      expect(setTier('Ace').disabled).toBe(true);
      setTier('Ace').click();
      await stable();
      http
        .expectOne(entryUrl(21))
        .flush(
          { errors: [{ code: 'invalid', message: 'Bad tier.', field: 'tier' }], meta: {} },
          { status: 400, statusText: 'Bad Request' },
        );
      await stable();
      expect(setTier('Ace').disabled).toBe(false);
      expect(text(rowOf('Ace').querySelector('[role="alert"]'))).toBe('Bad tier.');
      setTier('Ace').click();
      await stable();
      expect(rowOf('Ace').querySelector('[role="alert"]')).toBeNull();
      http
        .expectOne(entryUrl(21))
        .flush(
          { errors: [{ code: 'invalid', message: 'Bad tier.', field: 'tier' }], meta: {} },
          { status: 400, statusText: 'Bad Request' },
        );
      await stable();
    });
  });

  describe('draw controls when not editable (INV-3)', () => {
    it.each([
      ['active', { state: 'active' }],
      ['completed', { state: 'completed' }],
      ['embedded', { embedded: true, season: { id: 3, name: 'Season 2026' } }],
    ] as [string, Partial<TournamentDetail>][])(
      'keeps the Drawn Teams cards but hides Draw, Tier and Set tier for a %s Tournament',
      async (_label, overrides) => {
        await openWith(drawn(overrides), DRAWN_ENTRIES, null);
        expect(findButton('Re-roll draw')).toBeNull();
        expect(findButton('Draw teams')).toBeNull();
        expect(findButton('Set tier')).toBeNull();
        expect(section()?.querySelector('input')).toBeNull();
        expect(
          [...(section()?.querySelectorAll('.tournament-pool__drawn-team') ?? [])].length,
        ).toBe(2);
      },
    );
  });
});
