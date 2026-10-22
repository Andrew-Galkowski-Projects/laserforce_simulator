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
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { environment } from '../../../environments/environment';
import { ArenaMap } from '../../api/maps.models';
import { TeamListItem } from '../../api/teams.models';
import { signIn } from '../../api/testing/auth-fixtures';
import { matchRow, pageEnvelope, roundDetail } from '../testing/match-fixtures';
import { releaseMatchesLoads } from '../testing/matches-loads';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';

const BASE = environment.apiBaseUrl;

function team(id: number, name: string, valid = true): TeamListItem {
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
    is_valid_roster: valid,
    roster_errors: valid ? [] : ['Missing Commander'],
    players: [],
  };
}

function arenaMap(id: number, name: string): ArenaMap {
  return {
    id,
    name,
    image: null,
    is_default: false,
    img_width: 800,
    img_height: 600,
    created_at: '2026-10-22T12:00:00Z',
    has_confirmed_config: true,
  };
}

const TEAMS = [team(3, 'Alpha'), team(4, 'Bravo'), team(5, 'Charlie', false)];
const MAPS = [arenaMap(11, 'Classic Arena'), arenaMap(12, 'Warehouse')];

describe('Setup form (/matches/new, /rounds/new)', () => {
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

  const page = (): HTMLElement => {
    const host = harness.routeNativeElement?.querySelector<HTMLElement>('app-setup-form');
    if (!host) throw new Error('not on the setup form');
    return host;
  };
  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const routerUrl = (): string => TestBed.inject(Router).url;
  const field = (label: string): Promise<MatFormFieldHarness> =>
    loader.getHarness(MatFormFieldHarness.with({ floatingLabelText: label }));

  async function select(label: string): Promise<MatSelectHarness> {
    const control = await (await field(label)).getControl(MatSelectHarness);
    if (!control) throw new Error(`no ${label} select`);
    return control;
  }

  async function optionsOf(label: string): Promise<{ text: string; disabled: boolean }[]> {
    const control = await select(label);
    await control.open();
    const options = await Promise.all(
      (await control.getOptions()).map(async (o) => ({
        text: await o.getText(),
        disabled: await o.isDisabled(),
      })),
    );
    await control.close();
    return options;
  }

  function button(label: string): HTMLButtonElement {
    const found = [...page().querySelectorAll('button')].find((b) => text(b) === label);
    if (!found) throw new Error(`no ${label} button`);
    return found;
  }

  /** Navigates to `url` and answers both load GETs. */
  async function open(
    url: string,
    teams: TeamListItem[] = TEAMS,
    maps: ArenaMap[] = MAPS,
  ): Promise<void> {
    await harness.navigateByUrl(url);
    http.expectOne((req) => req.url === `${BASE}/teams/`).flush(pageEnvelope(teams));
    http.expectOne((req) => req.url === `${BASE}/maps/`).flush(pageEnvelope(maps));
    await harness.fixture.whenStable();
  }
  const paramsOf = (req: TestRequest): Record<string, string | null> =>
    Object.fromEntries(req.request.params.keys().map((k) => [k, req.request.params.get(k)]));

  it('loads the Teams by name and the confirmed-config maps by name, 100 a page', async () => {
    await harness.navigateByUrl('/matches/new');
    expect(routerUrl()).toBe('/matches/new');
    const teams = http.expectOne((req) => req.url === `${BASE}/teams/`);
    expect(teams.request.method).toBe('GET');
    expect(paramsOf(teams)).toEqual({ page_size: '100', ordering: 'name' });
    const maps = http.expectOne((req) => req.url === `${BASE}/maps/`);
    expect(maps.request.method).toBe('GET');
    expect(paramsOf(maps)).toEqual({
      has_confirmed_config: 'true',
      page_size: '100',
      ordering: 'name',
    });
    teams.flush({ data: [], meta: {} });
    maps.flush({ data: [], meta: {} });
    await harness.fixture.whenStable();
  });

  it('offers every Team on both sides, invalid rosters disabled and marked', async () => {
    await open('/matches/new');
    for (const label of ['Red Team', 'Blue Team']) {
      const options = await optionsOf(label);
      expect(options).toEqual([
        { text: 'Alpha', disabled: false },
        { text: 'Bravo', disabled: false },
        { text: 'Charlie (invalid roster)', disabled: true },
      ]);
    }
  });

  it('shows a link to Teams instead of the form when no Team has a valid roster', async () => {
    await open('/rounds/new', [team(5, 'Charlie', false)]);
    expect(page().querySelector('form')).toBeNull();
    expect(text(page())).toContain('You need at least one Team with a valid roster');
    const link = [...page().querySelectorAll('a')].find((a) => text(a) === 'Go to Teams');
    expect(link?.getAttribute('href')).toBe('/teams');
  });

  it('offers a Match type, Friendly by default, with the best-of-2 hint', async () => {
    await open('/matches/new');
    expect(await (await select('Match type')).getValueText()).toBe('Friendly');
    expect((await optionsOf('Match type')).map((o) => o.text)).toEqual(['Friendly', 'Tournament']);
    expect(await (await field('Match type')).getTextHints()).toEqual([
      'Tournament matches are best of 2 rounds with team color switching',
    ]);
  });

  it('has no Match type for a Single Round', async () => {
    await open('/rounds/new');
    expect(text(page().querySelector('h1'))).toBe('New Single Round');
    const matchType = await loader.getAllHarnesses(
      MatFormFieldHarness.with({ floatingLabelText: 'Match type' }),
    );
    expect(matchType.length).toBe(0);
    expect(text(page())).not.toContain('Tournament');
  });

  it('offers the confirmed-config maps after "No map (3-zone fallback)", the default', async () => {
    await open('/rounds/new');
    expect(await (await select('Arena Map')).getValueText()).toBe('No map (3-zone fallback)');
    expect((await optionsOf('Arena Map')).map((o) => o.text)).toEqual([
      'No map (3-zone fallback)',
      'Classic Arena',
      'Warehouse',
    ]);
    expect(await (await field('Arena Map')).getTextHints()).toEqual([
      'Only maps with a confirmed zone config appear here',
    ]);
  });

  it('posts the Match with the default type and no map, then opens the new Match', async () => {
    await open('/matches/new');
    await (await select('Red Team')).clickOptions({ text: 'Bravo' });
    await (await select('Blue Team')).clickOptions({ text: 'Alpha' });
    button('Simulate Match').click();
    await harness.fixture.whenStable();
    const req = http.expectOne(`${BASE}/matches/`);
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual({
      team_red: 4,
      team_blue: 3,
      match_type: 'friendly',
      arena_map: null,
    });
    req.flush({ data: matchRow({ id: 31 }), meta: {} }, { status: 201, statusText: 'Created' });
    await harness.fixture.whenStable();
    expect(routerUrl()).toBe('/matches/31');
    releaseMatchesLoads();
  });

  it('posts a Single Round with the chosen map and no match type, then opens it', async () => {
    await open('/rounds/new');
    await (await select('Red Team')).clickOptions({ text: 'Alpha' });
    await (await select('Blue Team')).clickOptions({ text: 'Bravo' });
    await (await select('Arena Map')).clickOptions({ text: 'Warehouse' });
    button('Simulate Round').click();
    await harness.fixture.whenStable();
    const req = http.expectOne(`${BASE}/rounds/`);
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual({ team_red: 3, team_blue: 4, arena_map: 12 });
    req.flush({ data: roundDetail({ id: 44 }), meta: {} }, { status: 201, statusText: 'Created' });
    await harness.fixture.whenStable();
    expect(routerUrl()).toBe('/rounds/44');
    releaseMatchesLoads();
  });

  it('posts a Tournament Match when Tournament is chosen', async () => {
    await open('/matches/new');
    await (await select('Red Team')).clickOptions({ text: 'Alpha' });
    await (await select('Blue Team')).clickOptions({ text: 'Bravo' });
    await (await select('Match type')).clickOptions({ text: 'Tournament' });
    await (await select('Arena Map')).clickOptions({ text: 'Classic Arena' });
    button('Simulate Match').click();
    await harness.fixture.whenStable();
    const req = http.expectOne(`${BASE}/matches/`);
    expect(req.request.body).toEqual({
      team_red: 3,
      team_blue: 4,
      match_type: 'tournament',
      arena_map: 11,
    });
    req.flush(
      { errors: [{ code: 'x', message: 'x', field: null }], meta: {} },
      { status: 400, statusText: 'Bad Request' },
    );
    await harness.fixture.whenStable();
  });

  it('posts the same Team on both sides unchecked and shows the setup error above the form', async () => {
    await open('/matches/new');
    await (await select('Red Team')).clickOptions({ text: 'Alpha' });
    await (await select('Blue Team')).clickOptions({ text: 'Alpha' });
    await (await select('Match type')).clickOptions({ text: 'Tournament' });
    button('Simulate Match').click();
    await harness.fixture.whenStable();
    const req = http.expectOne(`${BASE}/matches/`);
    expect(req.request.body).toEqual({
      team_red: 3,
      team_blue: 3,
      match_type: 'tournament',
      arena_map: null,
    });
    req.flush(
      {
        errors: [
          { code: 'match_setup', message: 'A team cannot play against itself.', field: null },
        ],
        meta: {},
      },
      { status: 400, statusText: 'Bad Request' },
    );
    await harness.fixture.whenStable();
    const alert = page().querySelector('[role="alert"]');
    expect(text(alert)).toBe('A team cannot play against itself.');
    // Above the form's controls, not inside a field.
    const firstField = page().querySelector('mat-form-field');
    expect(
      alert &&
        firstField &&
        alert.compareDocumentPosition(firstField) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(await (await field('Red Team')).getTextErrors()).toEqual([]);
    expect(routerUrl()).toBe('/matches/new');
    expect(await (await select('Red Team')).getValueText()).toBe('Alpha');
    expect(await (await select('Blue Team')).getValueText()).toBe('Alpha');
    expect(await (await select('Match type')).getValueText()).toBe('Tournament');
  });

  it.each([
    ['/matches/new', 'Simulate Match'],
    ['/rounds/new', 'Simulate Round'],
  ])('on %s, requires both Teams before posting anything', async (url, label) => {
    await open(url);
    button(label).click();
    await harness.fixture.whenStable();
    http.expectNone(`${BASE}/matches/`);
    http.expectNone(`${BASE}/rounds/`);
    expect(await (await field('Red Team')).getTextErrors()).toEqual(['This field is required.']);
    expect(await (await field('Blue Team')).getTextErrors()).toEqual(['This field is required.']);
    expect(routerUrl()).toBe(url);
  });

  it('flags only the empty side when one Team is picked', async () => {
    await open('/rounds/new');
    await (await select('Red Team')).clickOptions({ text: 'Bravo' });
    button('Simulate Round').click();
    await harness.fixture.whenStable();
    http.expectNone(`${BASE}/rounds/`);
    expect(await (await field('Red Team')).getTextErrors()).toEqual([]);
    expect(await (await field('Blue Team')).getTextErrors()).toEqual(['This field is required.']);
    expect(await (await select('Red Team')).getValueText()).toBe('Bravo');
  });

  it('shows a field error on its own control and keeps the choices', async () => {
    await open('/rounds/new');
    await (await select('Red Team')).clickOptions({ text: 'Bravo' });
    await (await select('Blue Team')).clickOptions({ text: 'Alpha' });
    await (await select('Arena Map')).clickOptions({ text: 'Classic Arena' });
    button('Simulate Round').click();
    await harness.fixture.whenStable();
    http.expectOne(`${BASE}/rounds/`).flush(
      {
        errors: [
          {
            code: 'invalid_choice',
            message: 'Select a valid choice. That choice is not one of the available choices.',
            field: 'team_blue',
          },
        ],
        meta: {},
      },
      { status: 400, statusText: 'Bad Request' },
    );
    await harness.fixture.whenStable();
    expect(await (await field('Blue Team')).getTextErrors()).toEqual([
      'Select a valid choice. That choice is not one of the available choices.',
    ]);
    expect(await (await field('Red Team')).getTextErrors()).toEqual([]);
    expect(page().querySelector('[role="alert"]')).toBeNull();
    expect(await (await select('Red Team')).getValueText()).toBe('Bravo');
    expect(await (await select('Arena Map')).getValueText()).toBe('Classic Arena');
  });

  it('reads "Simulating..." and is disabled while pending, and recovers after a failure', async () => {
    await open('/matches/new');
    await (await select('Red Team')).clickOptions({ text: 'Alpha' });
    await (await select('Blue Team')).clickOptions({ text: 'Bravo' });
    expect(button('Simulate Match').disabled).toBe(false);
    button('Simulate Match').click();
    await harness.fixture.whenStable();
    expect(button('Simulating...').disabled).toBe(true);
    http
      .expectOne(`${BASE}/matches/`)
      .flush(
        { errors: [{ code: 'x', message: 'Nope.', field: null }], meta: {} },
        { status: 400, statusText: 'Bad Request' },
      );
    await harness.fixture.whenStable();
    expect(button('Simulate Match').disabled).toBe(false);
  });

  it('clears the previous errors on the next submit', async () => {
    await open('/rounds/new');
    await (await select('Red Team')).clickOptions({ text: 'Alpha' });
    await (await select('Blue Team')).clickOptions({ text: 'Bravo' });
    button('Simulate Round').click();
    await harness.fixture.whenStable();
    http.expectOne(`${BASE}/rounds/`).flush(
      {
        errors: [
          { code: 'invalid_choice', message: 'Pick another Team.', field: 'team_red' },
          { code: 'match_setup', message: 'Setup failed.', field: null },
        ],
        meta: {},
      },
      { status: 400, statusText: 'Bad Request' },
    );
    await harness.fixture.whenStable();
    expect(await (await field('Red Team')).getTextErrors()).toEqual(['Pick another Team.']);
    // Resubmit without touching Red Team: its stale server error must still go.
    button('Simulate Round').click();
    await harness.fixture.whenStable();
    expect(page().querySelector('[role="alert"]')).toBeNull();
    expect(await (await field('Red Team')).getTextErrors()).toEqual([]);
    http
      .expectOne(`${BASE}/rounds/`)
      .flush({ data: roundDetail({ id: 45 }), meta: {} }, { status: 201, statusText: 'Created' });
    await harness.fixture.whenStable();
    expect(routerUrl()).toBe('/rounds/45');
    releaseMatchesLoads();
  });

  it('cancels back to Match History', async () => {
    await open('/rounds/new');
    const cancel = [...page().querySelectorAll('a')].find((a) => text(a) === 'Cancel');
    expect(cancel?.getAttribute('href')).toBe('/matches');
    cancel?.click();
    await harness.fixture.whenStable();
    expect(routerUrl()).toBe('/matches');
    releaseMatchesLoads();
  });
});
