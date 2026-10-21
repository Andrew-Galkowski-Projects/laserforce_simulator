import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { environment } from '../../../environments/environment';
import { Team } from '../../api/teams.models';
import { signIn } from '../../api/testing/auth-fixtures';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';

const BASE = environment.apiBaseUrl;

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

describe('Create Team page', () => {
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
    await harness.navigateByUrl('/teams/new');
  });

  afterEach(() => {
    // The detail page the form lands on loads its Team; that screen's own spec covers it.
    http.match(`${BASE}/teams/3/`);
    http.verify();
  });

  const page = (): HTMLElement => harness.routeNativeElement as HTMLElement;
  const routerUrl = (): string => TestBed.inject(Router).url;

  async function submitName(name: string): Promise<void> {
    const input = page().querySelector<HTMLInputElement>('input[formcontrolname="name"]');
    if (!input) throw new Error('no name input');
    input.value = name;
    input.dispatchEvent(new Event('input'));
    const button = [...page().querySelectorAll('button')].find(
      (b) => b.textContent?.trim() === 'Create Team',
    );
    if (!button) throw new Error('no Create Team button');
    button.click();
    await harness.fixture.whenStable();
  }

  it('POSTs the name to teams/', async () => {
    await submitName('Red Raiders');
    const req = http.expectOne(`${BASE}/teams/`);
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual({ name: 'Red Raiders' });
    req.flush({ data: TEAM, meta: {} }, { status: 201, statusText: 'Created' });
    await harness.fixture.whenStable();
  });

  it('confirms with a snackbar and lands on the new Team detail page', async () => {
    await submitName('Red Raiders');
    http
      .expectOne(`${BASE}/teams/`)
      .flush({ data: TEAM, meta: {} }, { status: 201, statusText: 'Created' });
    await harness.fixture.whenStable();
    expect(document.body.textContent).toContain('Team "Red Raiders" created successfully!');
    expect(routerUrl()).toBe('/teams/3');
  });

  it('shows a name field error under the name and stays on /teams/new', async () => {
    await submitName('');
    http.expectOne(`${BASE}/teams/`).flush(
      {
        errors: [{ code: 'required', message: 'This field is required.', field: 'name' }],
        meta: {},
      },
      { status: 400, statusText: 'Bad Request' },
    );
    await harness.fixture.whenStable();
    const field = page().querySelector('mat-form-field');
    expect(field?.querySelector('mat-error')?.textContent).toContain('This field is required.');
    expect(page().querySelector('[role="alert"]')).toBeNull();
    expect(routerUrl()).toBe('/teams/new');
  });

  it('shows a non-field error above the form', async () => {
    await submitName('Red Raiders');
    http.expectOne(`${BASE}/teams/`).flush(
      {
        errors: [{ code: 'invalid', message: 'Teams are locked.', field: null }],
        meta: {},
      },
      { status: 400, statusText: 'Bad Request' },
    );
    await harness.fixture.whenStable();
    const alert = page().querySelector('[role="alert"]');
    expect(alert?.textContent).toContain('Teams are locked.');
    expect(alert?.compareDocumentPosition(page().querySelector('mat-form-field') as Node)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
  });
});
