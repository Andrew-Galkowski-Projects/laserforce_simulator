import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { HarnessLoader } from '@angular/cdk/testing';
import { TestbedHarnessEnvironment } from '@angular/cdk/testing/testbed';
import { MatFormFieldHarness } from '@angular/material/form-field/testing';
import { MatSelectHarness } from '@angular/material/select/testing';
import { Title } from '@angular/platform-browser';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { environment } from '../../../environments/environment';
import { TeamListItem } from '../../api/teams.models';
import { signIn } from '../../api/testing/auth-fixtures';
import { APP_TITLE } from '../../app-title';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';

const BASE = environment.apiBaseUrl;

function team(id: number, name: string): TeamListItem {
  return {
    id,
    name,
    created_date: '2026-10-22T12:00:00Z',
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
}

const TEAM_OPTIONS = ['6', '7', '8', '9', 'Random (6–8)'];
const POOL_OPTIONS = [...Array.from({ length: 89 }, (_, i) => String(12 + i)), 'Random (12–100)'];

describe('Generate Players page', () => {
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

  // The lazy route renders inside the shell, so scope every query to this screen's host.
  const page = (): HTMLElement => {
    const host = harness.routeNativeElement?.querySelector<HTMLElement>('app-team-generate');
    if (!host) throw new Error('not on the Generate Players screen');
    return host;
  };
  const routerUrl = (): string => TestBed.inject(Router).url;
  const field = (label: string): Promise<MatFormFieldHarness> =>
    loader.getHarness(MatFormFieldHarness.with({ floatingLabelText: label }));

  async function select(label: string): Promise<MatSelectHarness> {
    const control = await (await field(label)).getControl(MatSelectHarness);
    if (!control) throw new Error(`no ${label} select`);
    return control;
  }

  async function optionTexts(label: string): Promise<string[]> {
    const control = await select(label);
    await control.open();
    const texts = await Promise.all((await control.getOptions()).map((o) => o.getText()));
    await control.close();
    return texts;
  }

  const control = (name: string): HTMLInputElement => {
    const input = page().querySelector<HTMLInputElement>(`input[name="${name}"]`);
    if (!input) throw new Error(`no ${name} input`);
    return input;
  };

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

  async function open(): Promise<void> {
    await harness.navigateByUrl('/teams/generate');
    await harness.fixture.whenStable();
  }

  it('switches the players select between team mode and pool mode, resetting to Random', async () => {
    await open();
    expect(routerUrl()).toBe('/teams/generate');
    expect(page().querySelector('h1')?.textContent?.trim()).toBe('Generate Players');
    expect(await (await select('Number of teams')).getValueText()).toBe('Random (2–10)');
    expect(await (await select('Players per team')).getValueText()).toBe('Random (6–8)');
    expect(await optionTexts('Players per team')).toEqual(TEAM_OPTIONS);
    expect(page().textContent).toContain('6–9 players per team (players 7+ go to the bench).');

    await (await select('Number of teams')).clickOptions({ text: '0 (free-agent pool)' });
    await harness.fixture.whenStable();
    expect(await await field('Players per team').catch(() => null)).toBeNull();
    expect(await (await select('Free-agent players')).getValueText()).toBe('Random (12–100)');
    expect(await optionTexts('Free-agent players')).toEqual(POOL_OPTIONS);
    expect(page().textContent).toContain('12–100 players for the free-agent pool.');
    expect(page().textContent).not.toContain('players 7+ go to the bench');

    await (await select('Number of teams')).clickOptions({ text: '4' });
    await harness.fixture.whenStable();
    expect(await (await select('Players per team')).getValueText()).toBe('Random (6–8)');
    expect(await optionTexts('Players per team')).toEqual(TEAM_OPTIONS);
  });

  it('keeps the players choice when the team count changes within the same mode', async () => {
    await open();
    await (await select('Players per team')).clickOptions({ text: '9' });
    await (await select('Number of teams')).clickOptions({ text: '5' });
    await harness.fixture.whenStable();
    expect(await (await select('Players per team')).getValueText()).toBe('9');
    await (await select('Number of teams')).clickOptions({ text: '0 (free-agent pool)' });
    await (await select('Free-agent players')).clickOptions({ text: '40' });
    await (await select('Number of teams')).clickOptions({ text: '0 (free-agent pool)' });
    await harness.fixture.whenStable();
    expect(await (await select('Free-agent players')).getValueText()).toBe('40');
  });

  it('POSTs the defaults: the selects as strings, the stats as numbers', async () => {
    await open();
    button('Generate').click();
    await harness.fixture.whenStable();
    const req = http.expectOne(`${BASE}/teams/generate/`);
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual({
      num_teams: 'random_2_10',
      players_per_team: 'random_team',
      mean: 50,
      std_dev: 15,
    });
    expect(typeof req.request.body.num_teams).toBe('string');
    expect(typeof req.request.body.players_per_team).toBe('string');
    expect(typeof req.request.body.mean).toBe('number');
    expect(typeof req.request.body.std_dev).toBe('number');
    req.flush(
      { errors: [{ code: 'x', message: 'x', field: null }], meta: {} },
      { status: 400, statusText: 'Bad Request' },
    );
    await harness.fixture.whenStable();
  });

  it('shows a field error under its control and the cross-field error in the alert line', async () => {
    await open();
    button('Generate').click();
    await harness.fixture.whenStable();
    http.expectOne(`${BASE}/teams/generate/`).flush(
      {
        errors: [
          {
            code: 'max_value',
            message: 'Ensure this value is less than or equal to 100.',
            field: 'mean',
          },
          {
            code: 'invalid',
            message: 'Players per team must be between 6 and 9 for team mode.',
            field: null,
          },
        ],
        meta: {},
      },
      { status: 400, statusText: 'Bad Request' },
    );
    await harness.fixture.whenStable();
    expect(await (await field('Stat mean')).getTextErrors()).toEqual([
      'Ensure this value is less than or equal to 100.',
    ]);
    expect(await (await field('Stat standard deviation')).getTextErrors()).toEqual([]);
    expect(page().querySelector('[role="alert"]')?.textContent).toContain(
      'Players per team must be between 6 and 9 for team mode.',
    );
    expect(routerUrl()).toBe('/teams/generate');
    expect(page().querySelector('mat-select')).not.toBeNull();
  });

  it('disables Generate while the request is in flight and re-enables it after a failure', async () => {
    await open();
    expect(button('Generate').disabled).toBe(false);
    button('Generate').click();
    await harness.fixture.whenStable();
    expect(button('Generate').disabled).toBe(true);
    http
      .expectOne(`${BASE}/teams/generate/`)
      .flush(
        { errors: [{ code: 'x', message: 'x', field: null }], meta: {} },
        { status: 400, statusText: 'Bad Request' },
      );
    await harness.fixture.whenStable();
    expect(button('Generate').disabled).toBe(false);
  });

  it('lists the created Teams as links to their detail pages, with no free-agent line', async () => {
    await open();
    button('Generate').click();
    await harness.fixture.whenStable();
    http.expectOne(`${BASE}/teams/generate/`).flush({
      data: { created_teams: [team(7, 'Alpha'), team(9, 'Bravo')], free_agent_count: 0 },
      meta: {},
    });
    await harness.fixture.whenStable();
    expect(routerUrl()).toBe('/teams/generate');
    expect(page().querySelector('h1')?.textContent?.trim()).toBe('Generation complete');
    expect(page().querySelector('h2')?.textContent?.trim()).toBe('Created teams');
    const links = [...page().querySelectorAll('ul li a')];
    expect(links.map((a) => a.textContent?.trim())).toEqual(['Alpha', 'Bravo']);
    expect(links.map((a) => a.getAttribute('href'))).toEqual(['/teams/7', '/teams/9']);
    expect(page().textContent).not.toContain('free-agent');
    expect(page().querySelector('mat-select')).toBeNull();
    expect(page().querySelector('a[href="/teams"]')?.textContent?.trim()).toBe('Back to Teams');
  });

  it('reports the free-agent count for a pool run, with no Team list', async () => {
    await open();
    button('Generate').click();
    await harness.fixture.whenStable();
    http
      .expectOne(`${BASE}/teams/generate/`)
      .flush({ data: { created_teams: [], free_agent_count: 12 }, meta: {} });
    await harness.fixture.whenStable();
    expect(page().querySelector('h1')?.textContent?.trim()).toBe('Generation complete');
    expect(page().textContent).toContain('Created 12 free-agent players.');
    expect(page().querySelector('h2')).toBeNull();
    expect(page().querySelector('ul')).toBeNull();
    expect(document.body.querySelector('mat-snack-bar-container')).toBeNull();
  });

  it('"Generate more" shows the form again with the previously chosen values', async () => {
    await open();
    await (await select('Number of teams')).clickOptions({ text: '3' });
    await (await select('Players per team')).clickOptions({ text: '8' });
    await type('mean', '62');
    await type('std_dev', '9');
    button('Generate').click();
    await harness.fixture.whenStable();
    const req = http.expectOne(`${BASE}/teams/generate/`);
    expect(req.request.body).toEqual({
      num_teams: '3',
      players_per_team: '8',
      mean: 62,
      std_dev: 9,
    });
    req.flush({ data: { created_teams: [team(7, 'Alpha')], free_agent_count: 0 }, meta: {} });
    await harness.fixture.whenStable();
    expect(page().querySelector('h1')?.textContent?.trim()).toBe('Generation complete');
    button('Generate more').click();
    await harness.fixture.whenStable();
    expect(routerUrl()).toBe('/teams/generate');
    expect(page().querySelector('h1')?.textContent?.trim()).toBe('Generate Players');
    expect(page().querySelector('h2')).toBeNull();
    expect(page().textContent).not.toContain('Alpha');
    expect(await (await select('Number of teams')).getValueText()).toBe('3');
    expect(await (await select('Players per team')).getValueText()).toBe('8');
    expect(control('mean').value).toBe('62');
    expect(control('std_dev').value).toBe('9');
    expect(button('Generate').disabled).toBe(false);
  });

  it('clears the previous errors on the next submit', async () => {
    await open();
    button('Generate').click();
    await harness.fixture.whenStable();
    http.expectOne(`${BASE}/teams/generate/`).flush(
      {
        errors: [
          { code: 'max_value', message: 'Too high.', field: 'std_dev' },
          { code: 'invalid', message: 'Mode mismatch.', field: null },
        ],
        meta: {},
      },
      { status: 400, statusText: 'Bad Request' },
    );
    await harness.fixture.whenStable();
    expect(page().querySelector('[role="alert"]')?.textContent).toContain('Mode mismatch.');
    expect(await (await field('Stat standard deviation')).getTextErrors()).toEqual(['Too high.']);
    // Resubmit without touching std_dev: the stale server error must still go.
    button('Generate').click();
    await harness.fixture.whenStable();
    expect(page().querySelector('[role="alert"]')).toBeNull();
    expect(await (await field('Stat standard deviation')).getTextErrors()).toEqual([]);
    http
      .expectOne(`${BASE}/teams/generate/`)
      .flush({ data: { created_teams: [], free_agent_count: 0 }, meta: {} });
    await harness.fixture.whenStable();
    expect(page().querySelector('h1')?.textContent?.trim()).toBe('Generation complete');
  });

  it('cancels back to the Teams list, sets the page title and bounds the stats as Django does', async () => {
    await open();
    const cancel = [...page().querySelectorAll('a')].find(
      (a) => a.textContent?.trim() === 'Cancel',
    );
    expect(cancel?.getAttribute('href')).toBe('/teams');
    expect(TestBed.inject(Title).getTitle()).toBe(`Generate Players - ${APP_TITLE}`);
    expect(page().textContent).toContain(
      'Create randomised Teams and/or Players for testing or league bootstrap.',
    );
    expect(control('mean').type).toBe('number');
    expect([control('mean').min, control('mean').max]).toEqual(['0', '100']);
    expect(control('mean').required).toBe(true);
    expect(control('std_dev').type).toBe('number');
    expect([control('std_dev').min, control('std_dev').max]).toEqual(['1', '40']);
    expect(control('std_dev').required).toBe(true);
  });
});
