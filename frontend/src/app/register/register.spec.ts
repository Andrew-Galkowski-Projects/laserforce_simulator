import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { environment } from '../../environments/environment';
import { USER } from '../api/testing/auth-fixtures';
import { releaseTeamsLoads } from '../teams/testing/teams-loads';
import { routes } from '../app.routes';
import { authInterceptor } from '../auth/auth.interceptor';

const BASE = environment.apiBaseUrl;

describe('Register page', () => {
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
    harness = await RouterTestingHarness.create();
  });

  afterEach(() => {
    releaseTeamsLoads();
    http.verify();
  });

  const page = (): HTMLElement => harness.routeNativeElement as HTMLElement;

  function input(name: string): HTMLInputElement {
    const el = page().querySelector<HTMLInputElement>(`input[formcontrolname="${name}"]`);
    if (!el) throw new Error(`no ${name} input`);
    return el;
  }

  const routerUrl = (): string => TestBed.inject(Router).url;

  const FILLED = {
    invite: 'ABC123',
    email: 'new@example.com',
    password1: 'Secret-123',
    password2: 'Secret-123',
  };

  async function submit(values: Partial<typeof FILLED> = FILLED): Promise<void> {
    for (const [name, value] of Object.entries(values)) {
      input(name).value = value;
      input(name).dispatchEvent(new Event('input'));
    }
    const button = [...page().querySelectorAll('button')].find(
      (b) => b.textContent?.trim() === 'Create account',
    );
    if (!button) throw new Error('no Create account button');
    button.click();
    await harness.fixture.whenStable();
  }

  function answerRegister(): void {
    const req = http.expectOne(`${BASE}/accounts/register/`);
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual(FILLED);
    req.flush(
      { data: { ...USER, email: FILLED.email }, meta: {} },
      { status: 201, statusText: 'Created' },
    );
  }

  it('creates the Account, signs it in with the new password and lands on /teams', async () => {
    await harness.navigateByUrl('/register');
    await submit();
    answerRegister();
    const token = http.expectOne(`${BASE}/token/`);
    expect(token.request.body).toEqual({ email: FILLED.email, password: FILLED.password1 });
    token.flush({ data: { access: 'acc-1' }, meta: {} });
    http.expectOne(`${BASE}/accounts/me/`).flush({ data: USER, meta: {} });
    await harness.fixture.whenStable();
    expect(routerUrl()).toBe('/teams');
  });

  it('lands on /login with "Account created. Please sign in." when the sign-in after it fails', async () => {
    await harness.navigateByUrl('/register');
    await submit();
    answerRegister();
    http.expectOne(`${BASE}/token/`).error(new ProgressEvent('error'), { status: 0 });
    await harness.fixture.whenStable();
    expect(routerUrl()).toBe('/login');
    expect(page().textContent).toContain('Account created. Please sign in.');
  });

  it('sends a signed-in visitor on to /teams', async () => {
    await harness.navigateByUrl('/register');
    await submit();
    answerRegister();
    http.expectOne(`${BASE}/token/`).flush({ data: { access: 'acc-1' }, meta: {} });
    http.expectOne(`${BASE}/accounts/me/`).flush({ data: USER, meta: {} });
    await harness.fixture.whenStable();
    await harness.navigateByUrl('/register?invite=X');
    expect(routerUrl()).toBe('/teams');
  });

  it('links back to the sign-in page', async () => {
    await harness.navigateByUrl('/register');
    const link = [...page().querySelectorAll('a')].find(
      (a) => a.textContent?.trim() === 'Already have an account? Sign in',
    );
    expect(link?.getAttribute('href')).toBe('/login');
  });

  function field(name: string): HTMLElement {
    const el = input(name).closest('mat-form-field');
    if (!el) throw new Error(`no ${name} field`);
    return el as HTMLElement;
  }

  it('shows each server field error under its field and stays on /register', async () => {
    await harness.navigateByUrl('/register');
    await submit();
    http.expectOne(`${BASE}/accounts/register/`).flush(
      {
        errors: [
          {
            code: 'invalid',
            message: 'This invite code is invalid or has already been used.',
            field: 'invite',
          },
          {
            code: 'password_mismatch',
            message: 'The two password fields didn’t match.',
            field: 'password2',
          },
        ],
        meta: {},
      },
      { status: 400, statusText: 'Bad Request' },
    );
    await harness.fixture.whenStable();
    expect(field('invite').textContent).toContain(
      'This invite code is invalid or has already been used.',
    );
    expect(field('password2').textContent).toContain('The two password fields didn’t match.');
    expect(page().querySelector('[role="alert"]')).toBeNull();
    expect(routerUrl()).toBe('/register');
  });

  it('shows an error that belongs to no field in the alert line, and can submit again', async () => {
    await harness.navigateByUrl('/register');
    await submit();
    http
      .expectOne(`${BASE}/accounts/register/`)
      .flush(
        { errors: [{ code: 'invalid', message: 'Something is off.', field: null }], meta: {} },
        { status: 400, statusText: 'Bad Request' },
      );
    await harness.fixture.whenStable();
    expect(page().querySelector('[role="alert"]')?.textContent).toContain('Something is off.');
    await submit();
    answerRegister();
    http.expectOne(`${BASE}/token/`).flush({ data: { access: 'acc-1' }, meta: {} });
    http.expectOne(`${BASE}/accounts/me/`).flush({ data: USER, meta: {} });
    await harness.fixture.whenStable();
  });

  it('says so in the alert line when the server cannot be reached', async () => {
    await harness.navigateByUrl('/register');
    await submit();
    http.expectOne(`${BASE}/accounts/register/`).error(new ProgressEvent('error'), { status: 0 });
    await harness.fixture.whenStable();
    expect(page().querySelector('[role="alert"]')?.textContent).toContain(
      'Could not reach the server.',
    );
  });

  it('marks every empty field as required and sends nothing', async () => {
    await harness.navigateByUrl('/register');
    await submit({});
    expect(field('invite').textContent).toContain('An invite code is required to register.');
    expect(field('email').textContent).toContain('Enter your email.');
    expect(field('password1').textContent).toContain('Enter a password.');
    expect(field('password2').textContent).toContain('Enter the password again.');
    http.expectNone(`${BASE}/accounts/register/`);
  });

  it('disables Create account while the request is in flight, and enables it after a failure', async () => {
    await harness.navigateByUrl('/register');
    await submit();
    const button = (): HTMLButtonElement | undefined =>
      [...page().querySelectorAll('button')].find(
        (b) => b.textContent?.trim() === 'Create account',
      );
    expect(button()?.disabled).toBe(true);
    http
      .expectOne(`${BASE}/accounts/register/`)
      .flush(
        { errors: [{ code: 'invalid', message: 'Something is off.', field: null }], meta: {} },
        { status: 400, statusText: 'Bad Request' },
      );
    await harness.fixture.whenStable();
    expect(button()?.disabled).toBe(false);
  });

  it('shows the invite, email and two password fields, with the invite code from the link', async () => {
    await harness.navigateByUrl('/register?invite=ABC123');
    expect(page().querySelector('h1')?.textContent).toContain('Create an account');
    expect(page().textContent).toContain('Invite code');
    expect(page().textContent).toContain(
      'Registration is invite-only. Use the link or code you were sent.',
    );
    expect(page().textContent).toContain('Your email address is how you sign in.');
    expect(page().textContent).toContain('Confirm password');
    expect(input('invite').value).toBe('ABC123');
    expect(input('email').type).toBe('email');
    expect(input('password1').type).toBe('password');
    expect(input('password2').type).toBe('password');
  });
});
