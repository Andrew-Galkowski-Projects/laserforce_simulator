import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { Title } from '@angular/platform-browser';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { environment } from '../../../environments/environment';
import { PlayerStats } from '../../api/players.models';
import { Team } from '../../api/teams.models';
import { signIn } from '../../api/testing/auth-fixtures';
import { STATS } from '../../api/testing/player-fixtures';
import { APP_TITLE } from '../../app-title';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';

const BASE = environment.apiBaseUrl;
const STAT_KEYS = Object.keys(STATS) as (keyof PlayerStats)[];

const TEAM: Team = {
  id: 3,
  name: 'Red Raiders',
  created_date: '2026-10-21T12:00:00Z',
  wins: 0,
  losses: 0,
  slot_commander: null,
  slot_heavy: null,
  slot_scout_1: null,
  slot_scout_2: null,
  slot_medic: null,
  slot_ammo: null,
  is_valid_roster: false,
  roster_errors: ['Missing Commander'],
  players: [],
};

const NOT_FOUND = {
  errors: [{ code: 'not_found', message: 'No Team matches the given query.', field: null }],
  meta: {},
};

describe('Add Player page', () => {
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
    await signIn();
    harness = await RouterTestingHarness.create();
  });

  afterEach(() => http.verify());

  const page = (): HTMLElement => harness.routeNativeElement as HTMLElement;
  const routerUrl = (): string => TestBed.inject(Router).url;
  const control = (name: string): HTMLInputElement => {
    const input = page().querySelector<HTMLInputElement>(`input[name="${name}"]`);
    if (!input) throw new Error(`no ${name} input`);
    return input;
  };
  const overall = (): string => page().querySelector('[role="status"]')?.textContent?.trim() ?? '';

  async function open(): Promise<void> {
    await harness.navigateByUrl('/teams/3/players/new');
    http.expectOne(`${BASE}/teams/3/`).flush({ data: TEAM, meta: {} });
    await harness.fixture.whenStable();
  }

  async function type(name: string, value: string): Promise<void> {
    const input = control(name);
    input.value = value;
    input.dispatchEvent(new Event('input'));
    await harness.fixture.whenStable();
  }

  function button(label: string): HTMLButtonElement {
    const found = [...page().querySelectorAll('button')].find(
      (b) => b.textContent?.trim() === label,
    );
    if (!found) throw new Error(`no ${label} button`);
    return found;
  }

  it('renders the form with every Stat at 50, the Overall at 50.0 and the randomise hint', async () => {
    await open();
    expect(page().querySelector('h1')?.textContent).toContain('Add Player to Red Raiders');
    expect(STAT_KEYS.map((k) => control(k).value)).toEqual(STAT_KEYS.map(() => '50'));
    for (const k of STAT_KEYS) {
      expect(control(k).type).toBe('number');
      expect(control(k).min).toBe('0');
      expect(control(k).max).toBe('100');
    }
    expect(overall()).toBe('Overall: 50.0');
    expect(page().textContent).toContain('Leave blank to randomise');
    for (const name of ['age', 'started_playing_age', 'total_games', 'home_site', 'height']) {
      expect(control(name).value).toBe('');
    }
    expect(TestBed.inject(Title).getTitle()).toBe(`Add Player - ${APP_TITLE}`);
  });

  it('groups the Stats into the five sections', async () => {
    await open();
    const headings = [...page().querySelectorAll('fieldset legend')].map((l) =>
      l.textContent?.trim(),
    );
    expect(headings).toEqual(
      expect.arrayContaining(['Awareness', 'Decision-making', 'Physical', 'Team', 'Role']),
    );
    expect(page().textContent).toContain('Offensive Synergy');
  });

  it('updates the Overall as a Stat is typed', async () => {
    await open();
    await type('accuracy', '70');
    await type('survival', '88');
    // (17 * 50 + 70 + 88) / 19 = 53.05...
    expect(overall()).toBe('Overall: 53.1');
  });

  it('sets every Stat from a preset and updates the Overall', async () => {
    await open();
    await type('accuracy', '12');
    button('Set all to Elite (90)').click();
    await harness.fixture.whenStable();
    expect(STAT_KEYS.map((k) => control(k).value)).toEqual(STAT_KEYS.map(() => '90'));
    expect(overall()).toBe('Overall: 90.0');
    button('Set all to Average (50)').click();
    await harness.fixture.whenStable();
    expect(STAT_KEYS.map((k) => control(k).value)).toEqual(STAT_KEYS.map(() => '50'));
    expect(overall()).toBe('Overall: 50.0');
  });

  it('shows "—" while a Stat is empty or outside 0-100', async () => {
    await open();
    await type('speed', '101');
    expect(overall()).toBe('Overall: —');
    await type('speed', '');
    expect(overall()).toBe('Overall: —');
    await type('speed', '-1');
    expect(overall()).toBe('Overall: —');
    await type('speed', '100');
    expect(overall()).not.toContain('—');
  });

  it('POSTs the name, roles and Stats, leaving blank profile fields out', async () => {
    await open();
    await type('name', 'Ace');
    await type('accuracy', '91');
    const checkbox = (label: string): HTMLInputElement => {
      const box = [...page().querySelectorAll('mat-checkbox')].find(
        (b) => b.textContent?.trim() === label,
      );
      const input = box?.querySelector<HTMLInputElement>('input[type="checkbox"]');
      if (!input) throw new Error(`no ${label} checkbox`);
      return input;
    };
    expect([...page().querySelectorAll('mat-checkbox')].map((b) => b.textContent?.trim())).toEqual([
      'Commander',
      'Heavy Weapons',
      'Scout',
      'Medic',
      'Ammo',
    ]);
    checkbox('Medic').click();
    checkbox('Commander').click();
    await harness.fixture.whenStable();
    button('Add Player').click();
    await harness.fixture.whenStable();
    const req = http.expectOne(`${BASE}/teams/3/players/`);
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual({
      name: 'Ace',
      preferred_roles: ['commander', 'medic'],
      ...STATS,
      accuracy: 91,
    });
    req.flush(
      { errors: [{ code: 'x', message: 'x', field: null }], meta: {} },
      { status: 400, statusText: 'Bad Request' },
    );
    await harness.fixture.whenStable();
  });

  it('POSTs the profile fields that were filled in', async () => {
    await open();
    await type('name', 'Ace');
    await type('age', '27');
    await type('home_site', 'Laserforce Bristol');
    button('Add Player').click();
    await harness.fixture.whenStable();
    const req = http.expectOne(`${BASE}/teams/3/players/`);
    expect(req.request.body.age).toBe(27);
    expect(req.request.body.home_site).toBe('Laserforce Bristol');
    expect(Object.keys(req.request.body)).not.toContain('started_playing_age');
    expect(Object.keys(req.request.body)).not.toContain('total_games');
    expect(Object.keys(req.request.body)).not.toContain('height');
    req.flush(
      { errors: [{ code: 'x', message: 'x', field: null }], meta: {} },
      { status: 400, statusText: 'Bad Request' },
    );
    await harness.fixture.whenStable();
  });

  it('disables Add Player while the save is in flight', async () => {
    await open();
    await type('name', 'Ace');
    button('Add Player').click();
    await harness.fixture.whenStable();
    expect(button('Add Player').disabled).toBe(true);
    http
      .expectOne(`${BASE}/teams/3/players/`)
      .flush(
        { errors: [{ code: 'x', message: 'x', field: null }], meta: {} },
        { status: 400, statusText: 'Bad Request' },
      );
    await harness.fixture.whenStable();
    expect(button('Add Player').disabled).toBe(false);
  });

  it('confirms with a snackbar and lands on the Team detail page', async () => {
    await open();
    await type('name', 'Ace');
    button('Add Player').click();
    await harness.fixture.whenStable();
    http
      .expectOne(`${BASE}/teams/3/players/`)
      .flush(
        { data: { ...STATS, id: 40, team: 3, name: 'Ace', preferred_roles: [] }, meta: {} },
        { status: 201, statusText: 'Created' },
      );
    await harness.fixture.whenStable();
    // The detail page the form lands on loads its Team; that screen's own spec covers it.
    http.match(`${BASE}/teams/3/`);
    expect(document.body.textContent).toContain('Player "Ace" added to Red Raiders!');
    expect(routerUrl()).toBe('/teams/3');
  });

  it('shows a Stat field error under that Stat and stays on the page', async () => {
    await open();
    await type('name', 'Ace');
    button('Add Player').click();
    await harness.fixture.whenStable();
    http.expectOne(`${BASE}/teams/3/players/`).flush(
      {
        errors: [
          {
            code: 'max_value',
            message: 'Ensure this value is less than or equal to 100.',
            field: 'accuracy',
          },
        ],
        meta: {},
      },
      { status: 400, statusText: 'Bad Request' },
    );
    await harness.fixture.whenStable();
    const field = control('accuracy').closest('mat-form-field');
    expect(field?.querySelector('mat-error')?.textContent).toContain(
      'Ensure this value is less than or equal to 100.',
    );
    expect(page().querySelector('[role="alert"]')).toBeNull();
    expect(routerUrl()).toBe('/teams/3/players/new');
  });

  it('shows a duplicate-name (field: null) error in the alert line', async () => {
    await open();
    await type('name', 'Twin');
    button('Add Player').click();
    await harness.fixture.whenStable();
    http.expectOne(`${BASE}/teams/3/players/`).flush(
      {
        errors: [
          {
            code: 'unique_together',
            message: 'Player with this Team and Name already exists.',
            field: null,
          },
        ],
        meta: {},
      },
      { status: 400, statusText: 'Bad Request' },
    );
    await harness.fixture.whenStable();
    expect(page().querySelector('[role="alert"]')?.textContent).toContain(
      'Player with this Team and Name already exists.',
    );
  });

  it('cancels back to the Team page', async () => {
    await open();
    const cancel = [...page().querySelectorAll('a')].find(
      (a) => a.textContent?.trim() === 'Cancel',
    );
    expect(cancel?.getAttribute('href')).toBe('/teams/3');
  });

  it('shows "Team not found" for a missing or cross-Account Team', async () => {
    await harness.navigateByUrl('/teams/99/players/new');
    http.expectOne(`${BASE}/teams/99/`).flush(NOT_FOUND, { status: 404, statusText: 'Not Found' });
    await harness.fixture.whenStable();
    expect(page().textContent).toContain('Team not found');
    expect(page().querySelector('input[name="name"]')).toBeNull();
  });
});
