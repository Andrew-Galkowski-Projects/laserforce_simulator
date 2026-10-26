import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { environment } from '../../../environments/environment';
import { signIn } from '../../api/testing/auth-fixtures';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';

const BASE = environment.apiBaseUrl;
const FILLED = {
  old_password: 'Secret-123',
  new_password1: 'Newer-456',
  new_password2: 'Newer-456',
};

describe('Change password page', () => {
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
    await harness.navigateByUrl('/account/password');
  });

  afterEach(() => http.verify());

  const page = (): HTMLElement => harness.routeNativeElement as HTMLElement;

  function input(name: string): HTMLInputElement {
    const el = page().querySelector<HTMLInputElement>(`input[formcontrolname="${name}"]`);
    if (!el) throw new Error(`no ${name} input`);
    return el;
  }

  function field(name: string): HTMLElement {
    const el = input(name).closest('mat-form-field');
    if (!el) throw new Error(`no ${name} field`);
    return el as HTMLElement;
  }

  async function type(values: Partial<typeof FILLED>): Promise<void> {
    for (const [name, value] of Object.entries(values)) {
      input(name).value = value;
      input(name).dispatchEvent(new Event('input'));
    }
    await harness.fixture.whenStable();
  }

  async function submit(values: Partial<typeof FILLED> = FILLED): Promise<void> {
    await type(values);
    const button = [...page().querySelectorAll('button')].find(
      (b) => b.textContent?.trim() === 'Change password',
    );
    if (!button) throw new Error('no Change password button');
    button.click();
    await harness.fixture.whenStable();
  }

  it('asks for the current password and the new one twice', () => {
    expect(page().querySelector('h1')?.textContent).toContain('Change password');
    expect(field('old_password').textContent).toContain('Current password');
    expect(field('new_password1').textContent).toContain('New password');
    expect(field('new_password2').textContent).toContain('Confirm new password');
    for (const name of Object.keys(FILLED)) {
      expect(input(name).type).toBe('password');
    }
  });

  it('posts the three fields, then confirms the change and clears the form', async () => {
    await submit();
    const req = http.expectOne(`${BASE}/accounts/me/password/`);
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual(FILLED);
    req.flush(null, { status: 204, statusText: 'No Content' });
    await harness.fixture.whenStable();

    expect(page().querySelector('[role="status"]')?.textContent).toContain(
      'Your password has been changed.',
    );
    for (const name of Object.keys(FILLED)) {
      expect(input(name).value).toBe('');
    }
    expect(page().querySelector('mat-error')).toBeNull();
    expect(TestBed.inject(Router).url).toBe('/account/password');
  });

  it('hides the confirmation once the form is edited again', async () => {
    await submit();
    http
      .expectOne(`${BASE}/accounts/me/password/`)
      .flush(null, { status: 204, statusText: 'No Content' });
    await harness.fixture.whenStable();
    await type({ old_password: 'x' });
    expect(page().textContent).not.toContain('Your password has been changed.');
  });

  it('shows a wrong current password and a mismatch under their fields', async () => {
    await submit();
    http.expectOne(`${BASE}/accounts/me/password/`).flush(
      {
        errors: [
          {
            code: 'password_incorrect',
            message: 'Your old password was entered incorrectly. Please enter it again.',
            field: 'old_password',
          },
          {
            code: 'password_mismatch',
            message: 'The two password fields didn’t match.',
            field: 'new_password2',
          },
        ],
        meta: {},
      },
      { status: 400, statusText: 'Bad Request' },
    );
    await harness.fixture.whenStable();
    expect(field('old_password').textContent).toContain(
      'Your old password was entered incorrectly. Please enter it again.',
    );
    expect(field('new_password2').textContent).toContain('The two password fields didn’t match.');
    expect(page().textContent).not.toContain('Your password has been changed.');
  });

  it('shows an error that belongs to no field in the alert line', async () => {
    await submit();
    http
      .expectOne(`${BASE}/accounts/me/password/`)
      .error(new ProgressEvent('error'), { status: 0 });
    await harness.fixture.whenStable();
    expect(page().querySelector('[role="alert"]')?.textContent).toContain(
      'Could not reach the server.',
    );
  });

  it('marks every empty field as required and sends nothing', async () => {
    await submit({});
    expect(field('old_password').textContent).toContain('Enter your current password.');
    expect(field('new_password1').textContent).toContain('Enter a new password.');
    expect(field('new_password2').textContent).toContain('Enter the new password again.');
    http.expectNone(`${BASE}/accounts/me/password/`);
  });
});
