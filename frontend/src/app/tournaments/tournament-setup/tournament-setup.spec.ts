import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { MATERIAL_ANIMATIONS } from '@angular/material/core';
import { HarnessLoader } from '@angular/cdk/testing';
import { TestbedHarnessEnvironment } from '@angular/cdk/testing/testbed';
import { MatSnackBarHarness } from '@angular/material/snack-bar/testing';
import { provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { environment } from '../../../environments/environment';
import { signIn } from '../../api/testing/auth-fixtures';
import { TournamentDetail } from '../../api/tournaments.models';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';
import { tournamentDetail } from '../testing/detail-fixtures';
import { releasePoolLoads } from '../testing/pool-fixtures';

const BASE = environment.apiBaseUrl;
const DETAIL_URL = `${BASE}/tournaments/5/`;
const SEEDS_URL = `${BASE}/tournaments/5/seeds/`;
const LOCK_URL = `${BASE}/tournaments/5/lock/`;

/** A preset Tournament in setup with three participants, out of seed order on purpose. */
const preset = (overrides: Partial<TournamentDetail> = {}): TournamentDetail =>
  tournamentDetail({
    participants: [
      { team: { id: 12, name: 'Blue Bolts' }, seed: 2 },
      { team: { id: 11, name: 'Red Raiders' }, seed: 1 },
      { team: { id: 13, name: 'Gold Guns' }, seed: 3 },
    ],
    ...overrides,
  });

const errorBody = (code: string, message: string, field: string | null = null) => ({
  errors: [{ code, message, field }],
  meta: {},
});

describe('Tournament setup (Tournament detail)', () => {
  let http: HttpTestingController;
  let harness: RouterTestingHarness;
  let loader: HarnessLoader;

  beforeEach(async () => {
    TestBed.configureTestingModule({
      providers: [
        provideRouter(routes),
        provideHttpClient(withInterceptors([authInterceptor])),
        provideHttpClientTesting(),
        // jsdom fires no animationend, so a dialog would never finish closing.
        { provide: MATERIAL_ANIMATIONS, useValue: { animationsDisabled: true } },
      ],
    });
    http = TestBed.inject(HttpTestingController);
    await signIn();
    harness = await RouterTestingHarness.create();
    loader = TestbedHarnessEnvironment.documentRootLoader(harness.fixture);
  });

  afterEach(() => {
    document.querySelector('.cdk-overlay-container')?.remove();
    releasePoolLoads();
    http.verify();
  });

  const page = (): HTMLElement => harness.routeNativeElement as HTMLElement;
  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const section = (): HTMLElement | null => page().querySelector('app-tournament-setup');
  const stable = (): Promise<void> => harness.fixture.whenStable();
  const dialog = (): HTMLElement | null => document.querySelector('mat-dialog-container');

  async function open(detail: TournamentDetail = preset()): Promise<void> {
    await harness.navigateByUrl(`/tournaments/${detail.id}`);
    http.expectOne(DETAIL_URL).flush({ data: detail, meta: {} });
    await stable();
  }

  /** The host's reload after `changed`. */
  async function answerReload(detail: TournamentDetail): Promise<void> {
    http.expectOne(DETAIL_URL).flush({ data: detail, meta: {} });
    await stable();
  }

  function findButton(root: Element | null, label: string): HTMLButtonElement | null {
    return (
      [...(root?.querySelectorAll<HTMLButtonElement>('button') ?? [])].find(
        (b) => text(b) === label,
      ) ?? null
    );
  }

  function button(label: string, root: Element | null = section()): HTMLButtonElement {
    const found = findButton(root, label);
    if (!found) throw new Error(`no ${label} button`);
    return found;
  }

  async function snackMessage(): Promise<string> {
    return (await loader.getHarness(MatSnackBarHarness)).getMessage();
  }

  const seeding = (): HTMLElement | null =>
    section()?.querySelector<HTMLElement>('.tournament-setup__seeding') ?? null;
  const seedRows = (): HTMLElement[] => [
    ...(seeding()?.querySelectorAll<HTMLElement>('tbody tr') ?? []),
  ];
  const seedInput = (teamId: number): HTMLInputElement => {
    const input = seeding()?.querySelector<HTMLInputElement>(`input[name="seed_${teamId}"]`);
    if (!input) throw new Error(`no seed input for ${teamId}`);
    return input;
  };

  async function typeSeed(teamId: number, value: string): Promise<void> {
    const input = seedInput(teamId);
    input.value = value;
    input.dispatchEvent(new Event('input'));
    await stable();
  }

  describe('Seeding', () => {
    it('lists each participant in seed order: the linked Team name and its seed input', async () => {
      await open();
      expect(text(seeding()?.querySelector('h2'))).toBe('Seeding');
      expect(seedRows().map((row) => text(row.querySelector('a')))).toEqual([
        'Red Raiders',
        'Blue Bolts',
        'Gold Guns',
      ]);
      expect(seedRows()[0].querySelector('a')?.getAttribute('href')).toBe('/teams/11/history');
      const input = seedInput(12);
      expect(input.type).toBe('number');
      expect(input.value).toBe('2');
      expect(input.hasAttribute('required')).toBe(true);
      expect(input.getAttribute('min')).toBe('1');
    });

    it('PUTs every seed, notifies "Seeding saved." and reloads the detail', async () => {
      await open();
      await typeSeed(11, '3');
      await typeSeed(13, '1');
      button('Save Seeding').click();
      await stable();
      const req = http.expectOne(SEEDS_URL);
      expect(req.request.method).toBe('PUT');
      expect(req.request.body).toEqual({ seeds: { 11: 3, 12: 2, 13: 1 } });
      req.flush({ data: preset(), meta: {} });
      await stable();
      expect(await snackMessage()).toBe('Seeding saved.');
      await answerReload(preset());
    });

    it.each([
      ['', 'This field is required.'],
      ['0', 'Ensure this value is greater than or equal to 1.'],
    ])('keeps a seed of "%s" on the client with "%s", sending nothing', async (value, message) => {
      await open();
      await typeSeed(12, value);
      button('Save Seeding').click();
      await stable();
      http.expectNone(SEEDS_URL);
      expect(text(seedRows()[1].querySelector('mat-error'))).toBe(message);
    });

    it("shows a seeds 400 (not exactly 1..N) in the section's error box", async () => {
      await open();
      await typeSeed(11, '2');
      button('Save Seeding').click();
      await stable();
      http
        .expectOne(SEEDS_URL)
        .flush(errorBody('invalid', 'Seeds must be exactly 1..3.', 'seeds'), {
          status: 400,
          statusText: 'Bad Request',
        });
      await stable();
      expect(text(seeding()?.querySelector('[role="alert"]'))).toBe('Seeds must be exactly 1..3.');
      expect(seedInput(11).value).toBe('2');
    });

    it('shows a 409 through apiMessage', async () => {
      await open();
      button('Save Seeding').click();
      await stable();
      http
        .expectOne(SEEDS_URL)
        .flush(errorBody('tournament_not_setup', 'The Tournament is no longer in setup.'), {
          status: 409,
          statusText: 'Conflict',
        });
      await stable();
      expect(text(seeding()?.querySelector('[role="alert"]'))).toBe(
        'The Tournament is no longer in setup.',
      );
    });

    it('disables Save Seeding in flight and clears its error when the next save starts', async () => {
      await open();
      button('Save Seeding').click();
      await stable();
      expect(button('Save Seeding').disabled).toBe(true);
      button('Save Seeding').click();
      await stable();
      http.expectOne(SEEDS_URL).flush(errorBody('invalid', 'Nope.', 'seeds'), {
        status: 400,
        statusText: 'Bad Request',
      });
      await stable();
      expect(button('Save Seeding').disabled).toBe(false);
      expect(text(seeding()?.querySelector('[role="alert"]'))).toBe('Nope.');
      button('Save Seeding').click();
      await stable();
      expect(seeding()?.querySelector('[role="alert"]')).toBeNull();
      http.expectOne(SEEDS_URL).flush({ data: preset(), meta: {} });
      await stable();
      await answerReload(preset());
    });

    it('is not shown for a Player-pool Tournament, even once drawn', async () => {
      await open(preset({ team_assembly: 'player_pool', is_drawn: true, pool_size: 24 }));
      releasePoolLoads();
      expect(seeding()).toBeNull();
      expect(text(section())).not.toContain('Seeding');
    });
  });

  describe('Lock & Build Bracket', () => {
    const LOCK = 'Lock & Build Bracket';
    const lockArea = (): HTMLElement | null =>
      section()?.querySelector<HTMLElement>('.tournament-setup__lock') ?? null;

    it.each([
      ['preset', preset()],
      ['Player-pool', preset({ team_assembly: 'player_pool', participants: [], pool_size: 24 })],
    ] as [string, TournamentDetail][])(
      'asks first for a %s Tournament, and Cancel sends nothing',
      async (_label, detail) => {
        await open(detail);
        releasePoolLoads();
        button(LOCK).click();
        await stable();
        expect(text(dialog()?.querySelector('h2'))).toBe(LOCK);
        expect(text(dialog())).toContain('Seeding');
        button('Cancel', dialog()).click();
        await stable();
        expect(dialog()).toBeNull();
        http.expectNone(LOCK_URL);
      },
    );

    it('POSTs lock on confirm, notifies "Bracket locked." and reloads the detail', async () => {
      await open();
      button(LOCK).click();
      await stable();
      button('Lock', dialog()).click();
      await stable();
      const req = http.expectOne(LOCK_URL);
      expect(req.request.method).toBe('POST');
      const active = preset({ state: 'active' });
      req.flush({ data: active, meta: {} });
      await stable();
      expect(await snackMessage()).toBe('Bracket locked.');
      await answerReload(active);
      expect(section()?.querySelector('button')).toBeNull();
    });

    it.each([
      [400, 'invalid', 'Swiss needs an even number of Teams.'],
      [409, 'tournament_not_setup', 'The Tournament is no longer in setup.'],
    ])('shows a lock %i in an error box', async (status, code, message) => {
      await open();
      button(LOCK).click();
      await stable();
      button('Lock', dialog()).click();
      await stable();
      http.expectOne(LOCK_URL).flush(errorBody(code, message), { status, statusText: 'Error' });
      await stable();
      expect(text(lockArea()?.querySelector('[role="alert"]'))).toBe(message);
      expect(button(LOCK).disabled).toBe(false);
    });

    it('disables the button in flight and clears its error when the next lock starts', async () => {
      await open();
      button(LOCK).click();
      await stable();
      button('Lock', dialog()).click();
      await stable();
      expect(button(LOCK).disabled).toBe(true);
      http.expectOne(LOCK_URL).flush(errorBody('invalid', 'Too few Teams.'), {
        status: 400,
        statusText: 'Bad Request',
      });
      await stable();
      expect(text(lockArea()?.querySelector('[role="alert"]'))).toBe('Too few Teams.');
      button(LOCK).click();
      await stable();
      button('Lock', dialog()).click();
      await stable();
      expect(lockArea()?.querySelector('[role="alert"]')).toBeNull();
      http.expectOne(LOCK_URL).flush(errorBody('invalid', 'Too few Teams.'), {
        status: 400,
        statusText: 'Bad Request',
      });
      await stable();
    });
  });

  describe('hidden controls (INV-3)', () => {
    it.each([
      ['active', { state: 'active' }],
      ['completed', { state: 'completed' }],
      ['embedded', { embedded: true, season: { id: 3, name: 'Season 2026' } }],
    ] as [string, Partial<TournamentDetail>][])(
      'shows no Seeding or Lock for a %s Tournament',
      async (_label, overrides) => {
        await open(preset(overrides));
        expect(seeding()).toBeNull();
        expect(findButton(section(), 'Lock & Build Bracket')).toBeNull();
        expect(findButton(section(), 'Save Seeding')).toBeNull();
        expect(section()?.querySelector('input')).toBeNull();
      },
    );
  });
});
