import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { MATERIAL_ANIMATIONS } from '@angular/material/core';
import { Title } from '@angular/platform-browser';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { environment } from '../../../environments/environment';
import { Player } from '../../api/players.models';
import { Team } from '../../api/teams.models';
import { PROFILE, STATS } from '../../api/testing/player-fixtures';
import { USER, signIn } from '../../api/testing/auth-fixtures';
import { APP_TITLE } from '../../app-title';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';
import { User } from '../../auth/user';
import { releasePlayersLoads } from '../../players/testing/players-loads';
import { releaseTeamsLoads } from '../testing/teams-loads';

const BASE = environment.apiBaseUrl;

/** Ace (40) on Red Dragons (3), with a full profile and Stats in every tier. */
function ace(overrides: Partial<Player> = {}): Player {
  return {
    ...STATS,
    ...PROFILE,
    player_awareness: 80,
    game_awareness: 79,
    resource_awareness: 60,
    decision_making: 59,
    positioning: 40,
    stamina: 39,
    accuracy: 100,
    survival: 0,
    id: 40,
    team: 3,
    team_name: 'Red Dragons',
    name: 'Ace',
    preferred_roles: ['commander', 'scout'],
    age: 27,
    started_playing_age: 15,
    total_games: 340,
    home_site: 'Laserforce Bristol',
    height: '180cm',
    overall_rating: 52.6,
    ...overrides,
  };
}

/** Red Dragons, managed by `USER` unless overridden. */
function team(overrides: Partial<Team> = {}): Team {
  return {
    id: 3,
    name: 'Red Dragons',
    created_date: '2026-10-01T09:00:00Z',
    wins: 0,
    losses: 0,
    slot_commander: null,
    slot_heavy: null,
    slot_scout_1: null,
    slot_scout_2: null,
    slot_medic: null,
    slot_ammo: null,
    is_valid_roster: false,
    roster_errors: [],
    manager: USER.id,
    players: [ace()],
    ...overrides,
  };
}

const NOT_FOUND = {
  errors: [{ code: 'not_found', message: 'Not found.', field: null }],
  meta: {},
};

describe('Player detail page', () => {
  let http: HttpTestingController;
  let harness: RouterTestingHarness;

  /**
   * Signs `user` in, opens `/teams/3/players/40` and answers `GET players/40/` and `GET teams/3/`
   * (`null`: the API's 404 envelope).
   */
  async function open(
    player: Player | null = ace(),
    t: Team | null = team(),
    user: User = USER,
  ): Promise<void> {
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
    await signIn(user);
    harness = await RouterTestingHarness.create();
    const nav = harness.navigateByUrl('/teams/3/players/40');
    await harness.fixture.whenStable();
    answer(`${BASE}/players/40/`, player);
    answer(`${BASE}/teams/3/`, t);
    await nav;
    await harness.fixture.whenStable();
  }

  function answer(url: string, body: unknown): void {
    const req = http.expectOne(url);
    expect(req.request.method).toBe('GET');
    if (body === null) {
      req.flush(NOT_FOUND, { status: 404, statusText: 'Not Found' });
    } else {
      req.flush({ data: body, meta: {} });
    }
  }

  afterEach(() => {
    document.querySelector('.cdk-overlay-container')?.remove();
    http.verify();
  });

  const page = (): HTMLElement => {
    const root = harness.routeNativeElement as HTMLElement;
    return root.querySelector<HTMLElement>('app-player-detail') ?? root;
  };
  const text = (): string => (page().textContent ?? '').replace(/\s+/g, ' ');
  const routerUrl = (): string => TestBed.inject(Router).url;
  const overlay = (): string => document.querySelector('.cdk-overlay-container')?.textContent ?? '';
  const dialog = (): HTMLElement | null => document.querySelector('mat-dialog-container');
  const squash = (s: string | null | undefined): string => (s ?? '').replace(/\s+/g, ' ').trim();
  /** An element's child elements' texts, joined by one space (cells, label + value). */
  const parts = (el: Element | null | undefined): string =>
    [...(el?.children ?? [])]
      .map((c) => squash(c.textContent))
      .filter((s) => s !== '')
      .join(' ');

  function link(label: string): HTMLAnchorElement | undefined {
    return [...page().querySelectorAll('a')].find((a) => squash(a.textContent) === label);
  }

  function button(label: string): HTMLButtonElement | undefined {
    return [...page().querySelectorAll('button')].find((b) => squash(b.textContent) === label);
  }

  it('shows the name, the Team, a back link to the Team and the page title', async () => {
    await open();
    expect(squash(page().querySelector('h1')?.textContent)).toBe('Ace');
    expect(text()).toContain('Red Dragons');
    expect(link('← Red Dragons')?.getAttribute('href')).toBe('/teams/3');
    expect(TestBed.inject(Title).getTitle()).toBe(`Player - ${APP_TITLE}`);
    expect(routerUrl()).toBe('/teams/3/players/40');
  });

  it("shows the name's initial as the avatar", async () => {
    await open(ace({ name: 'zed' }));
    expect(squash(page().querySelector('.player__avatar')?.textContent)).toBe('Z');
  });

  /** The profile table's rows, as "label value". */
  const profile = (): string[] => [...page().querySelectorAll('.player__profile tr')].map(parts);

  it('shows Height, Age, Started playing (age N), Home site and Total games when set', async () => {
    await open();
    expect(profile()).toEqual([
      'Height 180cm',
      'Age 27',
      'Started playing age 15',
      'Home site Laserforce Bristol',
      'Total games 340',
    ]);
  });

  it('leaves out the unset profile rows but always shows Total games', async () => {
    await open(ace({ ...PROFILE, team_name: 'Red Dragons' }));
    expect(profile()).toEqual(['Total games 0']);
  });

  it('shows the preferred roles as capitalised badges', async () => {
    await open();
    const badges = [...page().querySelectorAll('.player__roles .tag')].map((b) =>
      squash(b.textContent),
    );
    expect(badges).toEqual(['Commander', 'Scout']);
    expect(text()).toContain('Preferred roles');
  });

  it('shows no Preferred roles block when the Player has none', async () => {
    await open(ace({ preferred_roles: [] }));
    expect(text()).not.toContain('Preferred roles');
  });

  it('links Career stats and Edit, and offers Delete', async () => {
    await open();
    expect(link('Career stats')?.getAttribute('href')).toBe('/players/40/stats');
    expect(link('Edit')?.getAttribute('href')).toBe('/teams/3/players/40/edit');
    expect(button('Delete')).toBeDefined();
  });

  it('opens the Edit Player page from Edit', async () => {
    await open();
    link('Edit')?.click();
    await harness.fixture.whenStable();
    releasePlayersLoads();
    expect(routerUrl()).toBe('/teams/3/players/40/edit');
  });

  it('shows the Overall rating to 0 decimals', async () => {
    await open();
    expect(parts(page().querySelector('.player__overall'))).toBe('Overall 53');
  });

  /** Each Stat row of the group card headed `title`: label, value, bar width and tier. */
  function statRows(title: string): string[] {
    const card = [...page().querySelectorAll('.player__group')].find(
      (c) => squash(c.querySelector('h2')?.textContent) === title,
    );
    if (!card) throw new Error(`no ${title} card`);
    return [...card.querySelectorAll('.stat')].map((row) => {
      const bar = row.querySelector<HTMLElement>('.stat__bar');
      const tier = [...(bar?.classList ?? [])].find((c) => c.startsWith('stat__bar--')) ?? 'none';
      return `${parts(row)} | ${bar?.style.width} | ${tier.replace('stat__bar--', '')}`;
    });
  }

  it('groups the 19 Stats as Awareness, Decision-making, Physical, Team and Role', async () => {
    await open();
    const headings = [...page().querySelectorAll('.player__group h2')].map((h) =>
      squash(h.textContent),
    );
    expect(headings).toEqual(['Awareness', 'Decision-making', 'Physical', 'Team', 'Role']);
    expect(page().querySelectorAll('.stat')).toHaveLength(19);
  });

  it('draws each Stat as a bar its value wide, tiered at 80, 60 and 40, the value beside it', async () => {
    await open();
    expect(statRows('Awareness')).toEqual([
      'Player Awareness 80 | 80% | elite',
      'Game Awareness 79 | 79% | good',
      'Resource Awareness 60 | 60% | good',
    ]);
    expect(statRows('Decision-making')).toEqual(['Decision Making 59 | 59% | fair']);
    expect(statRows('Physical').slice(0, 2)).toEqual([
      'Positioning 40 | 40% | fair',
      'Stamina 39 | 39% | poor',
    ]);
    expect(statRows('Role').slice(-3)).toEqual([
      'Accuracy 100 | 100% | elite',
      'Survival 0 | 0% | poor',
      'Special Usage 50 | 50% | fair',
    ]);
  });

  describe('"You" badge', () => {
    it("follows the name when the Player is the Account's Self-insert", async () => {
      await open(ace(), team(), { ...USER, self_insert: ace() });
      const h1 = page().querySelector('h1');
      expect(squash(h1?.textContent)).toBe('Ace You');
      expect(h1?.querySelector('.badge--you')).not.toBeNull();
    });

    it('is absent for any other Player', async () => {
      await open(ace(), team(), { ...USER, self_insert: ace({ id: 41, name: 'Bea' }) });
      expect(squash(page().querySelector('h1')?.textContent)).toBe('Ace');
    });
  });

  describe('Self-insert', () => {
    it('offers This is me on a Player of a Team the Account manages', async () => {
      await open();
      expect(button('This is me')).toBeDefined();
      expect(button('Unlink')).toBeUndefined();
    });

    it('offers neither button on an Unmanaged Team', async () => {
      await open(ace(), team({ manager: null }));
      expect(button('This is me')).toBeUndefined();
      expect(button('Unlink')).toBeUndefined();
    });

    it("offers neither button on another Account's Team", async () => {
      await open(ace(), team({ manager: USER.id + 1 }));
      expect(button('This is me')).toBeUndefined();
      expect(button('Unlink')).toBeUndefined();
    });

    it('links the Player, says so, and swaps in Unlink and the "You" badge', async () => {
      await open();
      button('This is me')?.click();
      const req = http.expectOne(`${BASE}/accounts/me/self-insert/`);
      expect(req.request.method).toBe('PUT');
      expect(req.request.body).toEqual({ player: 40 });
      req.flush({ data: { ...USER, self_insert: ace() }, meta: {} });
      await harness.fixture.whenStable();
      expect(overlay()).toContain('"Ace" is now your Self-insert.');
      expect(button('Unlink')).toBeDefined();
      expect(button('This is me')).toBeUndefined();
      expect(squash(page().querySelector('h1')?.textContent)).toBe('Ace You');
    });

    it("shows the server's message when linking fails, and keeps This is me", async () => {
      await open();
      button('This is me')?.click();
      http.expectOne(`${BASE}/accounts/me/self-insert/`).flush(
        {
          errors: [
            {
              code: 'not_eligible',
              message: 'Only a Player on a Team you manage can be your Self-insert.',
              field: 'player',
            },
          ],
          meta: {},
        },
        { status: 400, statusText: 'Bad Request' },
      );
      await harness.fixture.whenStable();
      expect(overlay()).toContain('Only a Player on a Team you manage can be your Self-insert.');
      expect(button('This is me')).toBeDefined();
    });

    it('offers Unlink instead when the Player is already the Self-insert', async () => {
      await open(ace(), team({ manager: null }), { ...USER, self_insert: ace() });
      expect(button('Unlink')).toBeDefined();
      expect(button('This is me')).toBeUndefined();
    });

    it('unlinks, says so, and swaps back to This is me without the badge', async () => {
      await open(ace(), team(), { ...USER, self_insert: ace() });
      button('Unlink')?.click();
      const req = http.expectOne(`${BASE}/accounts/me/self-insert/`);
      expect(req.request.method).toBe('DELETE');
      req.flush(null, { status: 204, statusText: 'No Content' });
      await harness.fixture.whenStable();
      expect(overlay()).toContain('Self-insert unlinked.');
      expect(button('This is me')).toBeDefined();
      expect(button('Unlink')).toBeUndefined();
      expect(squash(page().querySelector('h1')?.textContent)).toBe('Ace');
    });

    it("shows the server's message when unlinking fails, and keeps Unlink", async () => {
      await open(ace(), team(), { ...USER, self_insert: ace() });
      button('Unlink')?.click();
      http
        .expectOne(`${BASE}/accounts/me/self-insert/`)
        .flush(
          { errors: [{ code: 'server_error', message: 'Try later.', field: null }], meta: {} },
          { status: 503, statusText: 'Unavailable' },
        );
      await harness.fixture.whenStable();
      expect(overlay()).toContain('Try later.');
      expect(button('Unlink')).toBeDefined();
    });
  });

  describe('Delete', () => {
    function dialogButton(label: string): HTMLButtonElement {
      const b = [...(dialog()?.querySelectorAll('button') ?? [])].find(
        (el) => squash(el.textContent) === label,
      );
      if (!b) throw new Error(`no ${label} button in the dialog`);
      return b;
    }

    async function clickDelete(): Promise<void> {
      button('Delete')?.click();
      await harness.fixture.whenStable();
    }

    it('asks "Are you sure you want to remove <name> from <team>?" first', async () => {
      await open();
      await clickDelete();
      expect(squash(dialog()?.querySelector('h2')?.textContent)).toBe('Remove Player');
      expect(squash(dialog()?.textContent)).toContain(
        'Are you sure you want to remove Ace from Red Dragons? This action cannot be undone.',
      );
      expect(dialogButton('Yes, Remove Player')).toBeDefined();
      http.expectNone(`${BASE}/players/40/`);
    });

    it('sends nothing when cancelled', async () => {
      await open();
      await clickDelete();
      dialogButton('Cancel').click();
      await harness.fixture.whenStable();
      expect(dialog()).toBeNull();
      http.expectNone(`${BASE}/players/40/`);
      expect(routerUrl()).toBe('/teams/3/players/40');
    });

    it('deletes the Player, says so and opens the Team', async () => {
      await open();
      await clickDelete();
      dialogButton('Yes, Remove Player').click();
      await harness.fixture.whenStable();
      const del = http.expectOne(`${BASE}/players/40/`);
      expect(del.request.method).toBe('DELETE');
      del.flush(null, { status: 204, statusText: 'No Content' });
      await harness.fixture.whenStable();
      releaseTeamsLoads();
      expect(routerUrl()).toBe('/teams/3');
      expect(overlay()).toContain('Player "Ace" removed from Red Dragons');
    });

    it('stays on the page and says why when the delete fails', async () => {
      await open();
      await clickDelete();
      dialogButton('Yes, Remove Player').click();
      await harness.fixture.whenStable();
      http.expectOne(`${BASE}/players/40/`).flush(NOT_FOUND, { status: 404, statusText: 'NF' });
      await harness.fixture.whenStable();
      expect(routerUrl()).toBe('/teams/3/players/40');
      expect(overlay()).toContain('Could not remove Ace: Not found.');
      expect(squash(page().querySelector('h1')?.textContent)).toBe('Ace');
    });
  });

  describe('not found', () => {
    function expectNotFound(): void {
      expect(squash(page().querySelector('h1')?.textContent)).toBe('Player not found');
      expect(link('← Back to Team')?.getAttribute('href')).toBe('/teams/3');
      expect(page().querySelector('.player__overall')).toBeNull();
    }

    it('shows "Player not found" with a link back when the Player is a 404', async () => {
      await open(null);
      expectNotFound();
    });

    it('shows "Player not found" when the Player is on another Team', async () => {
      await open(ace({ team: 8 }));
      expectNotFound();
    });

    it('shows "Player not found" when the Team is a 404', async () => {
      await open(ace(), null);
      expectNotFound();
    });
  });

  it('shows the API error in an alert when the load fails otherwise', async () => {
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
    const nav = harness.navigateByUrl('/teams/3/players/40');
    await harness.fixture.whenStable();
    http.expectOne(`${BASE}/players/40/`).flush('boom', { status: 500, statusText: 'Error' });
    answer(`${BASE}/teams/3/`, team());
    await nav;
    await harness.fixture.whenStable();
    expect(page().querySelector('[role="alert"]')?.textContent).toContain(
      'Could not load the Player.',
    );
    expect(text()).not.toContain('Player not found');
  });
});
