import { TestBed } from '@angular/core/testing';
import { HttpClient, provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../environments/environment';
import { ApiClient, ApiError } from '../api/api-client';
import { routes } from '../app.routes';
import { authInterceptor } from './auth.interceptor';
import { AuthService } from './auth.service';

const BASE = environment.apiBaseUrl;
const UNAUTHORIZED = { status: 401, statusText: 'Unauthorized' };
const EXPIRED = {
  errors: [{ code: 'token_not_valid', message: 'Token is expired.', field: null }],
  meta: {},
};

describe('authInterceptor', () => {
  let api: ApiClient;
  let auth: AuthService;
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
    api = TestBed.inject(ApiClient);
    auth = TestBed.inject(AuthService);
    http = TestBed.inject(HttpTestingController);
    harness = await RouterTestingHarness.create();
    await harness.navigateByUrl('/?page=2');
  });

  afterEach(() => http.verify());

  /** Holds `token` the way the app gets one: through a refresh. */
  async function holdToken(token: string): Promise<void> {
    const done = firstValueFrom(auth.refresh());
    http.expectOne(`${BASE}/token/refresh/`).flush({ data: { access: token }, meta: {} });
    await done;
  }

  function failure(promise: Promise<unknown>): Promise<ApiError> {
    return promise.then(
      () => {
        throw new Error('expected the call to fail');
      },
      (err: unknown) => err as ApiError,
    );
  }

  const routerUrl = (): string => TestBed.inject(Router).url;

  describe('Bearer header', () => {
    it('is absent while no token is held', async () => {
      const result = firstValueFrom(api.get('teams/'));
      const req = http.expectOne(`${BASE}/teams/`);
      expect(req.request.headers.has('Authorization')).toBe(false);
      expect(req.request.withCredentials).toBe(false);
      req.flush({ data: [], meta: {} });
      await result;
    });

    it('is attached to API calls once a token is held, without credentials', async () => {
      await holdToken('acc-1');
      const result = firstValueFrom(api.get('teams/'));
      const req = http.expectOne(`${BASE}/teams/`);
      expect(req.request.headers.get('Authorization')).toBe('Bearer acc-1');
      expect(req.request.withCredentials).toBe(false);
      req.flush({ data: [], meta: {} });
      await result;
    });

    it('is never sent to the token endpoints', async () => {
      await holdToken('acc-1');
      const result = firstValueFrom(auth.refresh());
      const req = http.expectOne(`${BASE}/token/refresh/`);
      expect(req.request.headers.has('Authorization')).toBe(false);
      req.flush({ data: { access: 'acc-2' }, meta: {} });
      await result;
    });

    it('is never sent outside apiBaseUrl, and neither are cookies', async () => {
      await holdToken('acc-1');
      const raw = TestBed.inject(HttpClient);
      const urls = ['https://evil.example.com/api/teams/', `${BASE}x/teams/`];
      for (const url of urls) {
        const result = firstValueFrom(raw.get(url));
        const req = http.expectOne(url);
        expect(req.request.headers.has('Authorization')).toBe(false);
        expect(req.request.withCredentials).toBe(false);
        req.flush({});
        await result;
      }
    });
  });

  describe('on a 401', () => {
    it('refreshes once and replays the request (any method) with the new token', async () => {
      await holdToken('old');
      const result = firstValueFrom(api.post<{ id: number }>('teams/', { name: 'Red' }));

      const first = http.expectOne(`${BASE}/teams/`);
      expect(first.request.headers.get('Authorization')).toBe('Bearer old');
      first.flush(EXPIRED, UNAUTHORIZED);

      http.expectOne(`${BASE}/token/refresh/`).flush({ data: { access: 'new' }, meta: {} });

      const replay = http.expectOne(`${BASE}/teams/`);
      expect(replay.request.method).toBe('POST');
      expect(replay.request.body).toEqual({ name: 'Red' });
      expect(replay.request.headers.get('Authorization')).toBe('Bearer new');
      replay.flush({ data: { id: 5 }, meta: {} }, { status: 201, statusText: 'Created' });

      expect(await result).toEqual({ id: 5 });
      expect(auth.accessToken()).toBe('new');
      expect(routerUrl()).toBe('/?page=2');
    });

    it('makes exactly one refresh request for three concurrent 401s', async () => {
      await holdToken('old');
      const results = Promise.all([
        firstValueFrom(api.get('teams/1/')),
        firstValueFrom(api.get('teams/2/')),
        firstValueFrom(api.get('teams/3/')),
      ]);
      for (const id of [1, 2, 3]) {
        http.expectOne(`${BASE}/teams/${id}/`).flush(EXPIRED, UNAUTHORIZED);
      }

      const refreshes = http.match(`${BASE}/token/refresh/`);
      expect(refreshes).toHaveLength(1);
      refreshes[0].flush({ data: { access: 'new' }, meta: {} });

      for (const id of [1, 2, 3]) {
        const replay = http.expectOne(`${BASE}/teams/${id}/`);
        expect(replay.request.headers.get('Authorization')).toBe('Bearer new');
        replay.flush({ data: { id }, meta: {} });
      }
      expect(await results).toEqual([{ id: 1 }, { id: 2 }, { id: 3 }]);
      http.expectNone(`${BASE}/token/refresh/`);
    });

    it('signs out and sends the person to /login with a returnUrl when the refresh fails', async () => {
      await holdToken('old');
      const result = failure(firstValueFrom(api.get('teams/')));
      http.expectOne(`${BASE}/teams/`).flush(EXPIRED, UNAUTHORIZED);
      http.expectOne(`${BASE}/token/refresh/`).flush(EXPIRED, UNAUTHORIZED);

      const err = await result;
      expect(err).toBeInstanceOf(ApiError);
      expect(err.status).toBe(401);
      await harness.fixture.whenStable();
      expect(routerUrl()).toBe('/login?returnUrl=%2F%3Fpage%3D2');
      expect(auth.accessToken()).toBeNull();
      expect(auth.isSignedIn()).toBe(false);
    });

    it('does not refresh again when the replay is also 401, and sends the person to /login', async () => {
      await holdToken('old');
      const result = failure(firstValueFrom(api.patch('teams/1/', { name: 'X' })));
      http.expectOne(`${BASE}/teams/1/`).flush(EXPIRED, UNAUTHORIZED);
      http.expectOne(`${BASE}/token/refresh/`).flush({ data: { access: 'new' }, meta: {} });
      http.expectOne(`${BASE}/teams/1/`).flush(EXPIRED, UNAUTHORIZED);

      const err = await result;
      expect(err.status).toBe(401);
      http.expectNone(`${BASE}/token/refresh/`);
      await harness.fixture.whenStable();
      expect(routerUrl()).toBe('/login?returnUrl=%2F%3Fpage%3D2');
      expect(auth.accessToken()).toBeNull();
    });

    it('never refreshes for a 401 from a token endpoint', async () => {
      const result = failure(firstValueFrom(api.post('token/', { email: 'a@b.c', password: 'x' })));
      http.expectOne(`${BASE}/token/`).flush(EXPIRED, UNAUTHORIZED);
      expect((await result).status).toBe(401);
      http.expectNone(`${BASE}/token/refresh/`);
      expect(routerUrl()).toBe('/?page=2');
    });

    it('passes other errors straight through', async () => {
      await holdToken('old');
      const result = failure(firstValueFrom(api.get('teams/')));
      http
        .expectOne(`${BASE}/teams/`)
        .flush(
          { errors: [{ code: 'permission_denied', message: 'No.', field: null }], meta: {} },
          { status: 403, statusText: 'Forbidden' },
        );
      expect((await result).status).toBe(403);
      http.expectNone(`${BASE}/token/refresh/`);
      expect(auth.accessToken()).toBe('old');
    });
  });
});
