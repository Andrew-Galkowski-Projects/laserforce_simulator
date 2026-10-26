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
  manager: null,
  players: [],
};

const NOT_FOUND = {
  errors: [{ code: 'not_found', message: 'No Team matches the given query.', field: null }],
  meta: {},
};

describe('Edit Team page', () => {
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
  const nameInput = (): HTMLInputElement | null =>
    page().querySelector<HTMLInputElement>('input[formcontrolname="name"]');

  async function open(): Promise<void> {
    await harness.navigateByUrl('/teams/3/edit');
    http.expectOne(`${BASE}/teams/3/`).flush({ data: TEAM, meta: {} });
    await harness.fixture.whenStable();
  }

  async function save(name: string): Promise<void> {
    const input = nameInput();
    if (!input) throw new Error('no name input');
    input.value = name;
    input.dispatchEvent(new Event('input'));
    const button = [...page().querySelectorAll('button')].find(
      (b) => b.textContent?.trim() === 'Save',
    );
    if (!button) throw new Error('no Save button');
    button.click();
    await harness.fixture.whenStable();
  }

  it('prefills the name from the Team', async () => {
    await open();
    expect(nameInput()?.value).toBe('Red Raiders');
  });

  it('PATCHes the new name, confirms with a snackbar and lands on the Team detail page', async () => {
    await open();
    await save('Blue Barons');
    const req = http.expectOne(`${BASE}/teams/3/`);
    expect(req.request.method).toBe('PATCH');
    expect(req.request.body).toEqual({ name: 'Blue Barons' });
    req.flush({ data: { ...TEAM, name: 'Blue Barons' }, meta: {} });
    await harness.fixture.whenStable();
    // The detail page the form lands on loads its Team; that screen's own spec covers it.
    http.match(`${BASE}/teams/3/`);
    expect(document.body.textContent).toContain('Team "Blue Barons" updated successfully!');
    expect(routerUrl()).toBe('/teams/3');
  });

  it('shows a name field error under the name and stays on the edit page', async () => {
    await open();
    await save('x'.repeat(101));
    http.expectOne(`${BASE}/teams/3/`).flush(
      {
        errors: [
          {
            code: 'max_length',
            message: 'Ensure this value has at most 100 characters (it has 101).',
            field: 'name',
          },
        ],
        meta: {},
      },
      { status: 400, statusText: 'Bad Request' },
    );
    await harness.fixture.whenStable();
    expect(page().querySelector('mat-form-field mat-error')?.textContent).toContain(
      'Ensure this value has at most 100 characters (it has 101).',
    );
    expect(routerUrl()).toBe('/teams/3/edit');
  });

  it('shows "Team not found" for a missing or cross-Account Team', async () => {
    await harness.navigateByUrl('/teams/99/edit');
    http.expectOne(`${BASE}/teams/99/`).flush(NOT_FOUND, { status: 404, statusText: 'Not Found' });
    await harness.fixture.whenStable();
    expect(page().textContent).toContain('Team not found');
    expect(page().querySelector('a[href="/teams"]')).not.toBeNull();
    expect(nameInput()).toBeNull();
  });
});
