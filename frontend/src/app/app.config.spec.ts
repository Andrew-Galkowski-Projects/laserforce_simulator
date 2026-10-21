import { TestBed } from '@angular/core/testing';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { firstValueFrom } from 'rxjs';
import { environment } from '../environments/environment';
import { ApiClient } from './api/api-client';
import { appConfig } from './app.config';
import { AuthService } from './auth/auth.service';

const BASE = environment.apiBaseUrl;

describe('appConfig', () => {
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [...appConfig.providers, provideHttpClientTesting()],
    });
    http = TestBed.inject(HttpTestingController);
  });

  it('restores the session on start-up and wires the auth interceptor into HttpClient', async () => {
    const auth = TestBed.inject(AuthService);
    http.expectOne(`${BASE}/token/refresh/`).flush({ data: { access: 'boot' }, meta: {} });
    await Promise.resolve();
    http.expectOne(`${BASE}/accounts/me/`).flush({
      data: { id: 1, email: 'a@b.c', date_joined: '', is_staff: false, self_insert: null },
      meta: {},
    });
    await vi.waitFor(() => expect(auth.isSignedIn()).toBe(true));

    const result = firstValueFrom(TestBed.inject(ApiClient).get('teams/'));
    const req = http.expectOne(`${BASE}/teams/`);
    expect(req.request.headers.get('Authorization')).toBe('Bearer boot');
    req.flush({ data: [], meta: {} });
    await result;
    http.verify();
  });
});
