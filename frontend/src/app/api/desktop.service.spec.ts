import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../environments/environment';
import { DesktopService } from './desktop.service';

const BASE = environment.apiBaseUrl;

describe('DesktopService', () => {
  let desktop: DesktopService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    desktop = TestBed.inject(DesktopService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('GETs accounts/desktop-update/ and unwraps the newer build', async () => {
    const result = firstValueFrom(desktop.update());
    const req = http.expectOne(`${BASE}/accounts/desktop-update/`);
    expect(req.request.method).toBe('GET');
    req.flush({
      data: {
        version: 'v0.2.0',
        url: 'https://github.com/o/r/releases/tag/v0.2.0',
      },
      meta: {},
    });
    expect(await result).toEqual({
      version: 'v0.2.0',
      url: 'https://github.com/o/r/releases/tag/v0.2.0',
    });
  });

  it('unwraps null when there is no newer build', async () => {
    const result = firstValueFrom(desktop.update());
    http.expectOne(`${BASE}/accounts/desktop-update/`).flush({ data: null, meta: {} });
    expect(await result).toBeNull();
  });
});
