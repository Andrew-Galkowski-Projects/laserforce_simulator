import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../environments/environment';
import { routes } from '../app.routes';
import { authInterceptor } from './auth.interceptor';
import { AuthService } from './auth.service';
import { User } from './user';

const BASE = environment.apiBaseUrl;

const USER: User = {
  id: 1,
  email: 'ann@example.com',
  date_joined: '2026-10-01T09:00:00Z',
  is_staff: false,
  self_insert: null,
};

describe('AuthService', () => {
  let auth: AuthService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideRouter(routes),
        provideHttpClient(withInterceptors([authInterceptor])),
        provideHttpClientTesting(),
      ],
    });
    auth = TestBed.inject(AuthService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('starts signed out', () => {
    expect(auth.currentUser()).toBeNull();
    expect(auth.isSignedIn()).toBe(false);
    expect(auth.accessToken()).toBeNull();
  });

  it('login posts credentials with cookies, keeps the access token and loads the user', async () => {
    const result = firstValueFrom(auth.login('ann@example.com', 'pw'));

    const tokenReq = http.expectOne(`${BASE}/token/`);
    expect(tokenReq.request.method).toBe('POST');
    expect(tokenReq.request.body).toEqual({ email: 'ann@example.com', password: 'pw' });
    expect(tokenReq.request.withCredentials).toBe(true);
    expect(tokenReq.request.headers.has('Authorization')).toBe(false);
    tokenReq.flush({ data: { access: 'acc-1' }, meta: {} });

    const meReq = http.expectOne(`${BASE}/accounts/me/`);
    expect(meReq.request.method).toBe('GET');
    expect(meReq.request.headers.get('Authorization')).toBe('Bearer acc-1');
    expect(meReq.request.withCredentials).toBe(false);
    meReq.flush({ data: USER, meta: {} });

    expect(await result).toEqual(USER);
    expect(auth.currentUser()).toEqual(USER);
    expect(auth.isSignedIn()).toBe(true);
    expect(auth.accessToken()).toBe('acc-1');
  });

  it('login with bad credentials fails with the 401 ApiError and stays signed out', async () => {
    const result = firstValueFrom(auth.login('ann@example.com', 'nope')).catch((e: unknown) => e);
    http.expectOne(`${BASE}/token/`).flush(
      {
        errors: [{ code: 'no_active_account', message: 'No active account.', field: null }],
        meta: {},
      },
      { status: 401, statusText: 'Unauthorized' },
    );
    expect(await result).toMatchObject({ status: 401 });
    expect(auth.isSignedIn()).toBe(false);
    expect(auth.accessToken()).toBeNull();
  });

  it('login clears the session and fails when accounts/me/ fails after the token was issued', async () => {
    const result = firstValueFrom(auth.login('ann@example.com', 'pw')).catch((e: unknown) => e);
    http.expectOne(`${BASE}/token/`).flush({ data: { access: 'acc-1' }, meta: {} });
    http
      .expectOne(`${BASE}/accounts/me/`)
      .flush(
        { errors: [{ code: 'server_error', message: 'Boom.', field: null }], meta: {} },
        { status: 500, statusText: 'Server Error' },
      );
    expect(await result).toMatchObject({ status: 500 });
    expect(auth.accessToken()).toBeNull();
    expect(auth.currentUser()).toBeNull();
    expect(auth.isSignedIn()).toBe(false);
  });

  async function signIn(): Promise<void> {
    const result = firstValueFrom(auth.login('ann@example.com', 'pw'));
    http.expectOne(`${BASE}/token/`).flush({ data: { access: 'acc-1' }, meta: {} });
    http.expectOne(`${BASE}/accounts/me/`).flush({ data: USER, meta: {} });
    await result;
  }

  it('register creates the Account but does not sign in', async () => {
    const body = {
      email: 'new@example.com',
      password1: 'Secret-123',
      password2: 'Secret-123',
      invite: 'ABC',
    };
    const result = firstValueFrom(auth.register(body));
    const req = http.expectOne(`${BASE}/accounts/register/`);
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual(body);
    expect(req.request.withCredentials).toBe(false);
    const created = { ...USER, id: 2, email: 'new@example.com' };
    req.flush({ data: created, meta: {} }, { status: 201, statusText: 'Created' });
    expect(await result).toEqual(created);
    expect(auth.accessToken()).toBeNull();
    expect(auth.currentUser()).toBeNull();
    expect(auth.isSignedIn()).toBe(false);
  });

  describe('refresh', () => {
    it('posts token/refresh/ with cookies and no body, and keeps the new token', async () => {
      const result = firstValueFrom(auth.refresh());
      const req = http.expectOne(`${BASE}/token/refresh/`);
      expect(req.request.method).toBe('POST');
      expect(req.request.body).toBeNull();
      expect(req.request.withCredentials).toBe(true);
      req.flush({ data: { access: 'acc-2' }, meta: {} });
      expect(await result).toBe('acc-2');
      expect(auth.accessToken()).toBe('acc-2');
    });

    it('is single-flight: concurrent callers share one request', async () => {
      const results = Promise.all([
        firstValueFrom(auth.refresh()),
        firstValueFrom(auth.refresh()),
        firstValueFrom(auth.refresh()),
      ]);
      const reqs = http.match(`${BASE}/token/refresh/`);
      expect(reqs).toHaveLength(1);
      reqs[0].flush({ data: { access: 'acc-3' }, meta: {} });
      expect(await results).toEqual(['acc-3', 'acc-3', 'acc-3']);
    });

    it('makes a new request once the previous one has finished', async () => {
      const first = firstValueFrom(auth.refresh());
      http.expectOne(`${BASE}/token/refresh/`).flush({ data: { access: 'a' }, meta: {} });
      await first;
      const second = firstValueFrom(auth.refresh());
      http.expectOne(`${BASE}/token/refresh/`).flush({ data: { access: 'b' }, meta: {} });
      expect(await second).toBe('b');
    });

    it('errors every waiting caller when the cookie is gone', async () => {
      const a = firstValueFrom(auth.refresh()).catch((e: unknown) => e);
      const b = firstValueFrom(auth.refresh()).catch((e: unknown) => e);
      http
        .expectOne(`${BASE}/token/refresh/`)
        .flush(
          { errors: [{ code: 'not_authenticated', message: 'No cookie.', field: null }], meta: {} },
          { status: 401, statusText: 'Unauthorized' },
        );
      expect(await a).toMatchObject({ status: 401 });
      expect(await b).toMatchObject({ status: 401 });
    });
  });

  describe('restoreSession', () => {
    it('refreshes silently and loads the user', async () => {
      const done = auth.restoreSession();
      http.expectOne(`${BASE}/token/refresh/`).flush({ data: { access: 'acc-r' }, meta: {} });
      await Promise.resolve();
      const me = http.expectOne(`${BASE}/accounts/me/`);
      expect(me.request.headers.get('Authorization')).toBe('Bearer acc-r');
      me.flush({ data: USER, meta: {} });
      await done;
      expect(auth.currentUser()).toEqual(USER);
      expect(auth.isSignedIn()).toBe(true);
    });

    it('leaves the app signed out, without navigating, when there is no session', async () => {
      const harness = await RouterTestingHarness.create();
      await harness.navigateByUrl('/');
      const done = auth.restoreSession();
      http
        .expectOne(`${BASE}/token/refresh/`)
        .flush(
          { errors: [{ code: 'not_authenticated', message: 'No cookie.', field: null }], meta: {} },
          { status: 401, statusText: 'Unauthorized' },
        );
      await expect(done).resolves.toBeUndefined();
      await harness.fixture.whenStable();
      expect(auth.currentUser()).toBeNull();
      expect(auth.accessToken()).toBeNull();
      expect(TestBed.inject(Router).url).toBe('/');
    });

    it('a 401 from accounts/me/ during restore leaves the app signed out, makes no second refresh, and does not navigate', async () => {
      const harness = await RouterTestingHarness.create();
      await harness.navigateByUrl('/');
      const done = auth.restoreSession();
      http.expectOne(`${BASE}/token/refresh/`).flush({ data: { access: 'acc-r' }, meta: {} });
      await Promise.resolve();
      const me = http.expectOne(`${BASE}/accounts/me/`);
      expect(me.request.headers.get('Authorization')).toBe('Bearer acc-r');
      me.flush(
        { errors: [{ code: 'token_not_valid', message: 'Bad token.', field: null }], meta: {} },
        { status: 401, statusText: 'Unauthorized' },
      );
      await expect(done).resolves.toBeUndefined();
      http.expectNone(`${BASE}/token/refresh/`);
      await harness.fixture.whenStable();
      expect(auth.currentUser()).toBeNull();
      expect(auth.accessToken()).toBeNull();
      expect(TestBed.inject(Router).url).toBe('/');
    });

    it('never rejects, even when the server is down', async () => {
      const done = auth.restoreSession();
      http.expectOne(`${BASE}/token/refresh/`).error(new ProgressEvent('error'), { status: 0 });
      await expect(done).resolves.toBeUndefined();
      expect(auth.isSignedIn()).toBe(false);
    });
  });

  describe('logout', () => {
    it('posts to token/logout/ with cookies, forgets the session and goes to /login', async () => {
      const harness = await RouterTestingHarness.create();
      await harness.navigateByUrl('/');
      await signIn();

      const done = firstValueFrom(auth.logout(), { defaultValue: undefined });
      const req = http.expectOne(`${BASE}/token/logout/`);
      expect(req.request.method).toBe('POST');
      expect(req.request.withCredentials).toBe(true);
      expect(req.request.headers.has('Authorization')).toBe(false);
      req.flush(null, { status: 204, statusText: 'No Content' });
      await done;
      await harness.fixture.whenStable();

      expect(auth.accessToken()).toBeNull();
      expect(auth.currentUser()).toBeNull();
      expect(auth.isSignedIn()).toBe(false);
      expect(TestBed.inject(Router).url).toBe('/login');
    });

    it('still forgets the session when the call fails', async () => {
      const harness = await RouterTestingHarness.create();
      await harness.navigateByUrl('/');
      await signIn();

      const done = firstValueFrom(auth.logout(), { defaultValue: undefined }).catch(() => null);
      http.expectOne(`${BASE}/token/logout/`).error(new ProgressEvent('error'), { status: 0 });
      await done;
      await harness.fixture.whenStable();

      expect(auth.accessToken()).toBeNull();
      expect(auth.currentUser()).toBeNull();
      expect(TestBed.inject(Router).url).toBe('/login');
    });
  });
});
