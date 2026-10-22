import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { MATERIAL_ANIMATIONS } from '@angular/material/core';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { environment } from '../../../environments/environment';
import { Player, Role } from '../../api/players.models';
import { STATS } from '../../api/testing/player-fixtures';
import { USER, signIn } from '../../api/testing/auth-fixtures';
import { releaseTeamsLoads } from '../testing/teams-loads';
import { Team } from '../../api/teams.models';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';

const BASE = environment.apiBaseUrl;

function player(id: number, name: string, preferred_roles: Role[] = []): Player {
  return { ...STATS, id, team: 7, name, preferred_roles };
}

/** Players in id order; the slots put them in a different order (CMD Cat, SCT 1 Ann, AMO Bob). */
function team(overrides: Partial<Team> = {}): Team {
  return {
    id: 7,
    name: 'Red Dragons',
    created_date: '2026-10-01T09:00:00Z',
    wins: 0,
    losses: 0,
    slot_commander: 13,
    slot_heavy: null,
    slot_scout_1: 11,
    slot_scout_2: null,
    slot_medic: null,
    slot_ammo: 12,
    is_valid_roster: false,
    roster_errors: ['Heavy slot is empty.', 'Medic slot is empty.'],
    players: [
      player(11, 'Ann', ['scout']),
      player(12, 'Bob'),
      player(13, 'Cat', ['commander', 'heavy']),
      player(14, 'Dan', ['medic']),
      player(15, 'Eve'),
    ],
    ...overrides,
  };
}

describe('Team detail page', () => {
  let http: HttpTestingController;
  let harness: RouterTestingHarness;

  /**
   * Signs `USER` in (or `user`), opens `/teams/7` and answers `GET teams/7/` with `body`
   * (`null`: the API's 404 envelope, as for a missing or another Account's Team).
   */
  async function open(body: Team | null = team(), user = USER): Promise<void> {
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
    const nav = harness.navigateByUrl('/teams/7');
    await harness.fixture.whenStable();
    answerTeam(body);
    await nav;
    await harness.fixture.whenStable();
  }

  function answerTeam(body: Team | null): void {
    const req = http.expectOne(`${BASE}/teams/7/`);
    expect(req.request.method).toBe('GET');
    if (body === null) {
      req.flush(
        { errors: [{ code: 'not_found', message: 'Not found.', field: null }], meta: {} },
        { status: 404, statusText: 'Not Found' },
      );
    } else {
      req.flush({ data: body, meta: {} });
    }
  }

  afterEach(() => http.verify());

  /** The routed screen inside the shell (the shell's header and sidebar left out). */
  const page = (): HTMLElement => {
    const root = harness.routeNativeElement as HTMLElement;
    return root.querySelector<HTMLElement>('app-team-detail') ?? root;
  };
  const text = (): string => page().textContent ?? '';
  const routerUrl = (): string => TestBed.inject(Router).url;

  it('shows the Team name, the player count and an Incomplete Roster badge', async () => {
    await open();
    expect(page().querySelector('h1')?.textContent?.trim()).toBe('Red Dragons');
    expect(text()).toContain('5 players');
    expect(text()).toContain('Incomplete Roster');
    expect(text()).not.toContain('Valid Roster');
  });

  it('shows a Valid Roster badge and no Roster issues alert for a valid Team', async () => {
    await open(team({ is_valid_roster: true, roster_errors: [] }));
    expect(text()).toContain('Valid Roster');
    expect(text()).not.toContain('Incomplete Roster');
    expect(text()).not.toContain('Roster issues:');
  });

  it('says "1 player" for a one-player Team', async () => {
    await open(
      team({
        slot_commander: null,
        slot_scout_1: null,
        slot_ammo: null,
        players: [player(11, 'Ann')],
      }),
    );
    expect(text()).toContain('1 player');
    expect(text()).not.toContain('1 players');
  });

  it('lists each server roster error under "Roster issues:"', async () => {
    await open();
    const alert = page().querySelector('[role="alert"]');
    expect(alert?.textContent).toContain('Roster issues:');
    const items = [...(alert?.querySelectorAll('li') ?? [])].map((li) => li.textContent?.trim());
    expect(items).toEqual(['Heavy slot is empty.', 'Medic slot is empty.']);
  });

  /** A cell's text with its child nodes (text runs, badges) joined by one space. */
  function cellText(td: Element): string {
    return [...td.childNodes]
      .filter((n) => n.nodeType !== Node.COMMENT_NODE)
      .map((n) => n.textContent?.trim() ?? '')
      .filter((s) => s !== '')
      .join(' ');
  }

  /** Each body row of the table under the heading `title`, as cell texts. */
  function rows(title: string): string[][] {
    const heading = [...page().querySelectorAll('h2')].find((h) => h.textContent?.trim() === title);
    const table = heading?.parentElement?.querySelector('table');
    if (!table) throw new Error(`no ${title} table`);
    return [...table.querySelectorAll('tbody tr')].map((tr) =>
      [...tr.querySelectorAll('td')].map(cellText),
    );
  }

  it('lists the filled slots in CMD, HVY, SCT 1, SCT 2, MED, AMO order, skipping empty ones', async () => {
    await open();
    expect(rows('Active Slots')).toEqual([
      ['CMD', 'Cat', 'CMD HVY', 'Remove'],
      ['SCT 1', 'Ann', 'SCT', 'Remove'],
      ['AMO', 'Bob', '—', 'Remove'],
    ]);
  });

  it('benches the players in no slot', async () => {
    await open();
    expect(rows('Bench')).toEqual([
      ['Dan', 'MED', 'Remove'],
      ['Eve', '—', 'Remove'],
    ]);
  });

  const headings = (): string[] =>
    [...page().querySelectorAll('h2')].map((h) => h.textContent?.trim() ?? '');

  it('says "No slots assigned yet." when no slot is filled, and keeps the Bench', async () => {
    await open(
      team({ slot_commander: null, slot_scout_1: null, slot_ammo: null, is_valid_roster: false }),
    );
    expect(text()).toContain(
      'No slots assigned yet. Use Assign Slots to set up the active roster.',
    );
    expect(page().querySelectorAll('table')).toHaveLength(1);
    expect(rows('Bench').map((r) => r[0])).toEqual(['Ann', 'Bob', 'Cat', 'Dan', 'Eve']);
    expect(text()).not.toContain('No players added yet.');
  });

  it('shows no Bench when every player is in a slot', async () => {
    await open(
      team({
        players: [player(11, 'Ann'), player(12, 'Bob'), player(13, 'Cat')],
      }),
    );
    expect(headings()).not.toContain('Bench');
    expect(rows('Active Slots')).toHaveLength(3);
  });

  it('shows a slotted Player from another Team as a placeholder row, counted as Active', async () => {
    // Django's active_roster shows (and counts) a slotted Player even when they are not on this
    // Team (roster_errors reports it); the API only sends this Team's players, so only the id.
    await open(
      team({
        slot_heavy: 99,
        roster_errors: ['Player in Heavy slot is not on this team.', 'Medic slot is empty.'],
      }),
    );
    expect(rows('Active Slots')).toEqual([
      ['CMD', 'Cat', 'CMD HVY', 'Remove'],
      ['HVY', 'Player #99 (not on this Team)', '—', ''],
      ['SCT 1', 'Ann', 'SCT', 'Remove'],
      ['AMO', 'Bob', '—', 'Remove'],
    ]);
    const card = page().querySelector('.status');
    expect(card?.querySelector('.status__row--total')?.textContent).toContain('4 / 2');
  });

  it('says "No players added yet." for a Team with no players', async () => {
    await open(team({ slot_commander: null, slot_scout_1: null, slot_ammo: null, players: [] }));
    expect(text()).toContain('0 players');
    expect(text()).toContain('No slots assigned yet.');
    expect(text()).toContain('No players added yet.');
    expect(headings()).not.toContain('Bench');
    expect(page().querySelector('table')).toBeNull();
  });

  it('marks the signed-in Account\'s self-insert Player with a "You" badge', async () => {
    await open(team(), { ...USER, self_insert: player(14, 'Dan', ['medic']) });
    expect(rows('Bench')).toEqual([
      ['Dan You', 'MED', 'Remove'],
      ['Eve', '—', 'Remove'],
    ]);
    expect(rows('Active Slots').map((r) => r[1])).toEqual(['Cat', 'Ann', 'Bob']);
  });

  it('marks a slotted self-insert Player too', async () => {
    await open(team(), { ...USER, self_insert: player(11, 'Ann', ['scout']) });
    expect(rows('Active Slots').map((r) => r[1])).toEqual(['Cat', 'Ann You', 'Bob']);
  });

  it('has a Roster Status card: 1 slot per role, Scout 2, Active / Bench counts, SM5 Rules', async () => {
    await open();
    const card = page().querySelector('.status');
    if (!card) throw new Error('no Roster Status card');
    const lines = [...card.querySelectorAll('.status__row')].map(cellText);
    expect(lines).toEqual([
      'CMD 1 slot',
      'HVY 1 slot',
      'SCT 2 slots',
      'MED 1 slot',
      'AMO 1 slot',
      'Active / Bench 3 / 2',
    ]);
    expect(page().querySelector('.status')?.parentElement?.textContent).toContain('Roster Status');
    expect(text().replace(/\s+/g, ' ')).toContain(
      'SM5 Rules: 6 active slots: Commander, Heavy, Scout ×2, Medic, Ammo. ' +
        'Remaining players are bench (not simulated).',
    );
  });

  /** The page's links and buttons, as `label -> href` (buttons have no href). */
  function actions(): string[] {
    return [...page().querySelectorAll('a, button')].map(
      (el) => `${el.textContent?.trim()} -> ${el.getAttribute('href') ?? ''}`,
    );
  }

  it('links Assign Slots, Edit Team and Back to Teams, and nothing to unmigrated pages', async () => {
    await open();
    const links = actions();
    expect(links).toContain('Assign Slots -> /teams/7/slots');
    expect(links).toContain('Edit Team -> /teams/7/edit');
    expect(links).toContain('← Back to Teams -> /teams');
    expect(links.filter((l) => l.startsWith('Remove'))).toHaveLength(5);
    for (const label of ['View ->', 'Edit ->', 'Add Player', 'Match History']) {
      expect(links.some((l) => l.startsWith(label))).toBe(false);
    }
    expect(links).toHaveLength(8);
  });

  it('opens Assign Slots at /teams/7/slots', async () => {
    await open();
    const link = [...page().querySelectorAll('a')].find(
      (a) => a.textContent?.trim() === 'Assign Slots',
    );
    link?.click();
    await harness.fixture.whenStable();
    releaseTeamsLoads();
    expect(routerUrl()).toBe('/teams/7/slots');
  });

  it('shows "Team not found" with a link back to /teams on a 404', async () => {
    await open(null);
    expect(page().querySelector('h1')?.textContent?.trim()).toBe('Team not found');
    expect(page().querySelector('table')).toBeNull();
    const back = [...page().querySelectorAll('a')].find((a) =>
      a.textContent?.includes('Back to Teams'),
    );
    expect(back?.getAttribute('href')).toBe('/teams');
    back?.click();
    await harness.fixture.whenStable();
    expect(routerUrl()).toBe('/teams');
    // The Teams list loads itself; answer it so no request is left open.
    for (const req of http.match((r) => r.url === `${BASE}/teams/`)) {
      req.flush({ data: [], meta: { page: 1, page_size: 100, count: 0, total_pages: 1 } });
    }
  });

  describe('Remove', () => {
    /** The CDK overlay (dialogs and snackbars render here, outside the page). */
    const overlay = (): HTMLElement | null => document.querySelector('.cdk-overlay-container');
    const dialog = (): HTMLElement | null => document.querySelector('mat-dialog-container');

    function overlayButton(label: string): HTMLButtonElement {
      const b = [...(dialog()?.querySelectorAll('button') ?? [])].find(
        (el) => el.textContent?.trim() === label,
      );
      if (!b) throw new Error(`no ${label} button in the dialog`);
      return b;
    }

    /** Clicks Remove on the row whose first player cell reads `name`. */
    async function clickRemove(name: string): Promise<void> {
      const row = [...page().querySelectorAll('tbody tr')].find((tr) =>
        [...tr.querySelectorAll('td')].some((td) => cellText(td) === name),
      );
      const button = [...(row?.querySelectorAll('button') ?? [])].find(
        (b) => b.textContent?.trim() === 'Remove',
      );
      if (!button) throw new Error(`no Remove for ${name}`);
      button.click();
      await harness.fixture.whenStable();
    }

    afterEach(() => {
      overlay()?.remove();
    });

    it('asks "Remove <name> from <team>? This deletes the player." first', async () => {
      await open();
      await clickRemove('Eve');
      expect(dialog()?.textContent).toContain(
        'Remove Eve from Red Dragons? This deletes the player.',
      );
      http.expectNone(`${BASE}/players/15/`);
    });

    it('sends nothing when the dialog is cancelled', async () => {
      await open();
      await clickRemove('Eve');
      overlayButton('Cancel').click();
      await harness.fixture.whenStable();
      expect(dialog()).toBeNull();
      http.expectNone(`${BASE}/players/15/`);
      http.expectNone(`${BASE}/teams/7/`);
      expect(rows('Bench').map((r) => r[0])).toEqual(['Dan', 'Eve']);
    });

    it('deletes the player, reloads the Team, then confirms with a snackbar', async () => {
      await open();
      await clickRemove('Cat');
      overlayButton('Remove').click();
      await harness.fixture.whenStable();

      const del = http.expectOne(`${BASE}/players/13/`);
      expect(del.request.method).toBe('DELETE');
      http.expectNone(`${BASE}/teams/7/`);
      del.flush(null, { status: 204, statusText: 'No Content' });
      await harness.fixture.whenStable();
      expect(overlay()?.textContent).not.toContain('removed from');

      answerTeam(
        team({
          slot_commander: null,
          players: team().players.filter((p) => p.id !== 13),
          roster_errors: ['Commander slot is empty.'],
        }),
      );
      await harness.fixture.whenStable();

      expect(rows('Active Slots').map((r) => r[1])).toEqual(['Ann', 'Bob']);
      expect(text()).toContain('4 players');
      expect(text()).toContain('Commander slot is empty.');
      expect(overlay()?.textContent).toContain('Player "Cat" removed from Red Dragons');
      const action = overlay()?.querySelector('.mat-mdc-snack-bar-action');
      expect(action?.textContent?.trim()).toBe('Close');
    });

    it('keeps the roster and shows the API error when the delete fails', async () => {
      await open();
      await clickRemove('Eve');
      overlayButton('Remove').click();
      await harness.fixture.whenStable();
      http
        .expectOne(`${BASE}/players/15/`)
        .flush(
          { errors: [{ code: 'not_found', message: 'Not found.', field: null }], meta: {} },
          { status: 404, statusText: 'Not Found' },
        );
      await harness.fixture.whenStable();
      http.expectNone(`${BASE}/teams/7/`);
      expect(rows('Bench').map((r) => r[0])).toEqual(['Dan', 'Eve']);
      expect(overlay()?.textContent).toContain('Could not remove Eve: Not found.');
    });
  });

  it('shows the API error, not "Team not found", when the load fails otherwise', async () => {
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
    const nav = harness.navigateByUrl('/teams/7');
    await harness.fixture.whenStable();
    http.expectOne(`${BASE}/teams/7/`).flush('boom', { status: 500, statusText: 'Server Error' });
    await nav;
    await harness.fixture.whenStable();
    expect(page().querySelector('[role="alert"]')?.textContent).toContain(
      'Could not load the Team.',
    );
    expect(text()).not.toContain('Team not found');
  });

  it('shows no "You" badge when the Account has no self-insert', async () => {
    await open();
    expect(text()).not.toContain('You');
  });
});
