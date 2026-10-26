import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { Title } from '@angular/platform-browser';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { environment } from '../../../environments/environment';
import { Player, PlayerStats } from '../../api/players.models';
import { signIn } from '../../api/testing/auth-fixtures';
import { STATS } from '../../api/testing/player-fixtures';
import { APP_TITLE } from '../../app-title';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';

const BASE = environment.apiBaseUrl;
const STAT_KEYS = Object.keys(STATS) as (keyof PlayerStats)[];

/** Non-uniform Stats: (16 * 50 + 90 + 70 + 31) / 19 = 52.157..., so the Overall is 52.2. */
const PLAYER: Player = {
  ...STATS,
  accuracy: 90,
  survival: 70,
  speed: 31,
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
  overall_rating: 52.16,
};

const NOT_FOUND = {
  errors: [{ code: 'not_found', message: 'No Player matches the given query.', field: null }],
  meta: {},
};

describe('Edit Player page', () => {
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

  /** Opens `/teams/3/players/40/edit` and answers `GET players/40/` with `body`. */
  async function open(body: Player = PLAYER): Promise<void> {
    await harness.navigateByUrl('/teams/3/players/40/edit');
    http.expectOne(`${BASE}/players/40/`).flush({ data: body, meta: {} });
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

  it('pre-fills the name, profile, Preferred roles and every Stat from the GET', async () => {
    await open();
    expect(page().querySelector('h1')?.textContent?.trim()).toBe('Edit Ace');
    expect(TestBed.inject(Title).getTitle()).toBe(`Edit Player - ${APP_TITLE}`);
    expect(control('name').value).toBe('Ace');
    expect(control('age').value).toBe('27');
    expect(control('started_playing_age').value).toBe('15');
    expect(control('total_games').value).toBe('340');
    expect(control('home_site').value).toBe('Laserforce Bristol');
    expect(control('height').value).toBe('180cm');
    expect(STAT_KEYS.map((k) => control(k).value)).toEqual(STAT_KEYS.map((k) => String(PLAYER[k])));
    const checked = [...page().querySelectorAll('mat-checkbox')]
      .filter((b) => b.querySelector<HTMLInputElement>('input[type="checkbox"]')?.checked)
      .map((b) => b.textContent?.trim());
    expect(checked).toEqual(['Commander', 'Scout']);
    expect(page().textContent).not.toContain('Leave blank to randomise');
    expect(routerUrl()).toBe('/teams/3/players/40/edit');
  });

  it("starts the Overall at the server's overall_rating, then follows live edits", async () => {
    await open();
    expect(overall()).toBe('Overall: 52.2');
    await type('speed', '50');
    // (17 * 50 + 90 + 70) / 19 = 53.157...
    expect(overall()).toBe('Overall: 53.2');
    await type('speed', '101');
    expect(overall()).toBe('Overall: —');
  });

  it('PATCHes every field, sending null for a blank age and "" for a blank home site / height', async () => {
    await open();
    await type('name', 'Ace Two');
    await type('age', '');
    await type('started_playing_age', '');
    await type('home_site', '');
    await type('height', '');
    await type('accuracy', '95');
    button('Save Changes').click();
    await harness.fixture.whenStable();
    const req = http.expectOne(`${BASE}/players/40/`);
    expect(req.request.method).toBe('PATCH');
    expect(req.request.body).toEqual({
      ...STATS,
      accuracy: 95,
      survival: 70,
      speed: 31,
      name: 'Ace Two',
      preferred_roles: ['commander', 'scout'],
      age: null,
      started_playing_age: null,
      total_games: 340,
      home_site: '',
      height: '',
    });
    expect(button('Save Changes').disabled).toBe(true);
    req.flush(
      { errors: [{ code: 'x', message: 'x', field: null }], meta: {} },
      { status: 400, statusText: 'Bad Request' },
    );
    await harness.fixture.whenStable();
    expect(button('Save Changes').disabled).toBe(false);
  });

  it('PATCHes the unchanged Player as it was loaded', async () => {
    await open();
    button('Save Changes').click();
    await harness.fixture.whenStable();
    const req = http.expectOne(`${BASE}/players/40/`);
    // The body is every field the form edits: the read-only ones (incl. ANG-06-8's team_name) stay out.
    const { id, team, team_name, overall_rating, ...fields } = PLAYER;
    void [id, team, team_name, overall_rating];
    expect(req.request.body).toEqual(fields);
    req.flush(
      { errors: [{ code: 'x', message: 'x', field: null }], meta: {} },
      { status: 400, statusText: 'Bad Request' },
    );
    await harness.fixture.whenStable();
  });

  it('confirms with a snackbar naming the saved Player and lands on the Team page', async () => {
    await open();
    await type('name', 'Ace Two');
    button('Save Changes').click();
    await harness.fixture.whenStable();
    http.expectOne(`${BASE}/players/40/`).flush({ data: { ...PLAYER, name: 'Ace Two' }, meta: {} });
    await harness.fixture.whenStable();
    // The detail page the form lands on loads its Team; that screen's own spec covers it.
    http.match(`${BASE}/teams/3/`);
    expect(document.body.textContent).toContain('Player "Ace Two" updated!');
    expect(routerUrl()).toBe('/teams/3');
  });

  it('sends a blank Stat and Total Games as null and shows the server field error under each', async () => {
    await open();
    await type('total_games', '');
    await type('accuracy', '');
    button('Save Changes').click();
    await harness.fixture.whenStable();
    const req = http.expectOne(`${BASE}/players/40/`);
    expect(req.request.body.accuracy).toBeNull();
    expect(req.request.body.total_games).toBeNull();
    req.flush(
      {
        errors: [
          { code: 'required', message: 'This field is required.', field: 'accuracy' },
          { code: 'required', message: 'Total games is required.', field: 'total_games' },
        ],
        meta: {},
      },
      { status: 400, statusText: 'Bad Request' },
    );
    await harness.fixture.whenStable();
    const errorUnder = (name: string): string | undefined =>
      control(name).closest('mat-form-field')?.querySelector('mat-error')?.textContent ?? undefined;
    expect(errorUnder('accuracy')).toContain('This field is required.');
    expect(errorUnder('total_games')).toContain('Total games is required.');
    expect(page().querySelector('[role="alert"]')).toBeNull();
    expect(routerUrl()).toBe('/teams/3/players/40/edit');
  });

  it('shows a duplicate-name (field: null) error in the alert line', async () => {
    await open();
    button('Save Changes').click();
    await harness.fixture.whenStable();
    http.expectOne(`${BASE}/players/40/`).flush(
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

  /** The "Player not found" state: heading, a link back to `/teams/3`, no form. */
  function expectPlayerNotFound(): void {
    expect(page().querySelector('h1')?.textContent?.trim()).toBe('Player not found');
    const back = [...page().querySelectorAll('a')].find((a) => a.textContent?.includes('Back'));
    expect(back?.getAttribute('href')).toBe('/teams/3');
    expect(page().querySelector('input[name="name"]')).toBeNull();
  }

  it('shows "Player not found" for a missing or cross-Account Player', async () => {
    await harness.navigateByUrl('/teams/3/players/40/edit');
    http
      .expectOne(`${BASE}/players/40/`)
      .flush(NOT_FOUND, { status: 404, statusText: 'Not Found' });
    await harness.fixture.whenStable();
    expectPlayerNotFound();
  });

  it('shows "Player not found" when the Player is on another Team than the URL', async () => {
    await open({ ...PLAYER, team: 8 });
    expectPlayerNotFound();
  });

  it('shows the API error, not "Player not found", when the load fails otherwise', async () => {
    await harness.navigateByUrl('/teams/3/players/40/edit');
    http
      .expectOne(`${BASE}/players/40/`)
      .flush(
        { errors: [{ code: 'server_error', message: 'Boom.', field: null }], meta: {} },
        { status: 500, statusText: 'Server Error' },
      );
    await harness.fixture.whenStable();
    expect(page().querySelector('[role="alert"]')?.textContent).toContain(
      'Could not load the Player.',
    );
    expect(page().textContent).not.toContain('Player not found');
  });
});
