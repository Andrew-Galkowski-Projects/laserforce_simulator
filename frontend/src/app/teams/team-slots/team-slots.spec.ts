import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { HarnessLoader } from '@angular/cdk/testing';
import { TestbedHarnessEnvironment } from '@angular/cdk/testing/testbed';
import { MatButtonHarness } from '@angular/material/button/testing';
import { MatFormFieldHarness } from '@angular/material/form-field/testing';
import { MatSelectHarness } from '@angular/material/select/testing';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { environment } from '../../../environments/environment';
import { Player } from '../../api/players.models';
import { Team } from '../../api/teams.models';
import { signIn } from '../../api/testing/auth-fixtures';
import { releaseTeamsLoads } from '../testing/teams-loads';
import { PROFILE, STATS } from '../../api/testing/player-fixtures';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';

const BASE = environment.apiBaseUrl;

function player(id: number, name: string): Player {
  return { id, team: 3, name, preferred_roles: [], ...STATS, ...PROFILE };
}

const TEAM: Team = {
  id: 3,
  name: 'Red Raiders',
  created_date: '2026-10-21T12:00:00Z',
  wins: 0,
  losses: 0,
  slot_commander: 11,
  slot_heavy: 12,
  slot_scout_1: null,
  slot_scout_2: null,
  slot_medic: null,
  slot_ammo: null,
  is_valid_roster: false,
  roster_errors: ['Missing Scout'],
  players: [player(11, 'Ace'), player(12, 'Bo'), player(13, 'Cy')],
};

const NOT_FOUND = {
  errors: [{ code: 'not_found', message: 'No Team matches the given query.', field: null }],
  meta: {},
};

const LABELS = ['CMD', 'HVY', 'SCT 1', 'SCT 2', 'MED', 'AMO'];
const EMPTY = '— empty —';

describe('Assign Slots page', () => {
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

  afterEach(() => {
    // The detail page a save lands on loads its Team; that screen's own spec covers it.
    releaseTeamsLoads();
    http.verify();
  });

  const page = (): HTMLElement => harness.routeNativeElement as HTMLElement;
  const routerUrl = (): string => TestBed.inject(Router).url;
  const field = (label: string): Promise<MatFormFieldHarness> =>
    loader.getHarness(MatFormFieldHarness.with({ floatingLabelText: label }));

  async function select(label: string): Promise<MatSelectHarness> {
    const control = await (await field(label)).getControl(MatSelectHarness);
    if (!control) throw new Error(`no ${label} select`);
    return control;
  }

  async function open(team: Team = TEAM): Promise<void> {
    await harness.navigateByUrl('/teams/3/slots');
    http.expectOne(`${BASE}/teams/3/`).flush({ data: team, meta: {} });
    await harness.fixture.whenStable();
  }

  async function save(): Promise<void> {
    await (await loader.getHarness(MatButtonHarness.with({ text: 'Save Slots' }))).click();
    await harness.fixture.whenStable();
  }

  it('has one select per slot in CMD, HVY, SCT 1, SCT 2, MED, AMO order', async () => {
    await open();
    const fields = await loader.getAllHarnesses(MatFormFieldHarness);
    expect(await Promise.all(fields.map((f) => f.getLabel()))).toEqual(LABELS);
  });

  it('prefills each select with the current slot', async () => {
    await open();
    const values = await Promise.all(LABELS.map(async (l) => (await select(l)).getValueText()));
    expect(values).toEqual(['Ace', 'Bo', EMPTY, EMPTY, EMPTY, EMPTY]);
  });

  it("offers only this Team's players plus empty", async () => {
    await open();
    const scout = await select('SCT 1');
    await scout.open();
    const options = await scout.getOptions();
    expect(await Promise.all(options.map((o) => o.getText()))).toEqual([EMPTY, 'Ace', 'Bo', 'Cy']);
  });

  it('PUTs all six slots, confirms with a snackbar and lands on the Team detail page', async () => {
    await open();
    await (await select('HVY')).clickOptions({ text: EMPTY });
    await (await select('SCT 1')).clickOptions({ text: 'Cy' });
    await save();
    const req = http.expectOne(`${BASE}/teams/3/slots/`);
    expect(req.request.method).toBe('PUT');
    expect(req.request.body).toEqual({
      slot_commander: 11,
      slot_heavy: null,
      slot_scout_1: 13,
      slot_scout_2: null,
      slot_medic: null,
      slot_ammo: null,
    });
    req.flush({ data: { ...TEAM, slot_heavy: null, slot_scout_1: 13 }, meta: {} });
    await harness.fixture.whenStable();
    expect(document.body.textContent).toContain('Roster slots updated.');
    expect(routerUrl()).toBe('/teams/3');
  });

  it('shows a slot field error on the matching select and stays on the page', async () => {
    await open();
    await save();
    http.expectOne(`${BASE}/teams/3/slots/`).flush(
      {
        errors: [
          {
            code: 'invalid_choice',
            message: 'Select a valid choice. That choice is not one of the available choices.',
            field: 'slot_scout_2',
          },
        ],
        meta: {},
      },
      { status: 400, statusText: 'Bad Request' },
    );
    await harness.fixture.whenStable();
    expect(await (await field('SCT 2')).getTextErrors()).toEqual([
      'Select a valid choice. That choice is not one of the available choices.',
    ]);
    expect(await (await field('SCT 1')).getTextErrors()).toEqual([]);
    expect(page().querySelector('[role="alert"]')).toBeNull();
    expect(routerUrl()).toBe('/teams/3/slots');
  });

  it('shows a non-field error above the form', async () => {
    await open();
    await save();
    http.expectOne(`${BASE}/teams/3/slots/`).flush(
      {
        errors: [{ code: 'invalid', message: 'A player cannot fill two slots.', field: null }],
        meta: {},
      },
      { status: 400, statusText: 'Bad Request' },
    );
    await harness.fixture.whenStable();
    const alert = page().querySelector('[role="alert"]');
    expect(alert?.textContent).toContain('A player cannot fill two slots.');
    expect(alert?.compareDocumentPosition(page().querySelector('mat-form-field') as Node)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
  });

  it('shows "Team not found" for a missing or cross-Account Team', async () => {
    await harness.navigateByUrl('/teams/99/slots');
    http.expectOne(`${BASE}/teams/99/`).flush(NOT_FOUND, { status: 404, statusText: 'Not Found' });
    await harness.fixture.whenStable();
    expect(page().textContent).toContain('Team not found');
    expect(page().querySelector('a[href="/teams"]')).not.toBeNull();
    expect(page().querySelector('mat-select')).toBeNull();
  });
});
