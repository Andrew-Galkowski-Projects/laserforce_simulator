import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { environment } from '../../environments/environment';
import { USER } from '../api/testing/auth-fixtures';
import { routes } from '../app.routes';
import { authInterceptor } from '../auth/auth.interceptor';

const BASE = environment.apiBaseUrl;
const INVALID = {
  errors: [
    {
      code: 'no_active_account',
      message: 'No active account found with the given credentials',
      field: null,
    },
  ],
  meta: {},
};

describe('Login page', () => {
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

  afterEach(() => http.verify());

  const routerUrl = (): string => TestBed.inject(Router).url;
  const page = (): HTMLElement => harness.routeNativeElement as HTMLElement;

  function input(name: string): HTMLInputElement {
    const el = page().querySelector<HTMLInputElement>(`input[formcontrolname="${name}"]`);
    if (!el) throw new Error(`no ${name} input`);
    return el;
  }

  async function submit(email: string, password: string): Promise<void> {
    input('email').value = email;
    input('email').dispatchEvent(new Event('input'));
    input('password').value = password;
    input('password').dispatchEvent(new Event('input'));
    const button = [...page().querySelectorAll('button')].find(
      (b) => b.textContent?.trim() === 'Sign in',
    );
    if (!button) throw new Error('no Sign in button');
    button.click();
    await harness.fixture.whenStable();
  }

  async function answerSignIn(): Promise<void> {
    const token = http.expectOne(`${BASE}/token/`);
    token.flush({ data: { access: 'acc-1' }, meta: {} });
    http.expectOne(`${BASE}/accounts/me/`).flush({ data: USER, meta: {} });
    await harness.fixture.whenStable();
  }

  it('has an Email and a Password field and a Sign in button', async () => {
    await harness.navigateByUrl('/login');
    expect(page().textContent).toContain('Email');
    expect(page().textContent).toContain('Password');
    expect(input('password').type).toBe('password');
  });

  it('sends the email and password to token/', async () => {
    await harness.navigateByUrl('/login');
    await submit('ann@example.com', 'Secret-123');
    const req = http.expectOne(`${BASE}/token/`);
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual({ email: 'ann@example.com', password: 'Secret-123' });
    req.flush({ data: { access: 'acc-1' }, meta: {} });
    http.expectOne(`${BASE}/accounts/me/`).flush({ data: USER, meta: {} });
    await harness.fixture.whenStable();
  });

  it('lands on /teams after signing in when there is no returnUrl', async () => {
    await harness.navigateByUrl('/login');
    await submit('ann@example.com', 'Secret-123');
    await answerSignIn();
    expect(routerUrl()).toBe('/teams');
  });

  it('returns to the returnUrl after signing in', async () => {
    await harness.navigateByUrl('/login?returnUrl=%2Fteams%2F3%2Fslots');
    await submit('ann@example.com', 'Secret-123');
    await answerSignIn();
    expect(routerUrl()).toBe('/teams/3/slots');
  });

  // Each would resolve to a real screen (/teams/new) if it were used as-is.
  it.each(['https://evil.example.com/teams/new', '//teams/new', '/\\teams/new', 'teams/new'])(
    'ignores a returnUrl that is not an in-app path (%s) and lands on /teams',
    async (returnUrl) => {
      await harness.navigateByUrl(`/login?returnUrl=${encodeURIComponent(returnUrl)}`);
      await submit('ann@example.com', 'Secret-123');
      await answerSignIn();
      expect(routerUrl()).toBe('/teams');
    },
  );

  it('shows "Invalid email or password." when the sign-in fails, and stays on /login', async () => {
    await harness.navigateByUrl('/login?returnUrl=%2Fteams');
    await submit('ann@example.com', 'wrong');
    http.expectOne(`${BASE}/token/`).flush(INVALID, { status: 401, statusText: 'Unauthorized' });
    await harness.fixture.whenStable();
    expect(page().textContent).toContain('Invalid email or password.');
    expect(routerUrl()).toBe('/login?returnUrl=%2Fteams');
  });

  it('shows the same message when the server cannot be reached', async () => {
    await harness.navigateByUrl('/login');
    await submit('ann@example.com', 'Secret-123');
    http.expectOne(`${BASE}/token/`).error(new ProgressEvent('error'), { status: 0 });
    await harness.fixture.whenStable();
    expect(page().textContent).toContain('Invalid email or password.');
  });
});
