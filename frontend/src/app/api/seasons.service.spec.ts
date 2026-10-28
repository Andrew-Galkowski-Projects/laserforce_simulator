import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../environments/environment';
import { seasonDashboard } from '../leagues/testing/league-fixtures';
import { ApiError } from './api-client';
import { SeasonsService } from './seasons.service';

const BASE = environment.apiBaseUrl;

describe('SeasonsService', () => {
  let seasons: SeasonsService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    seasons = TestBed.inject(SeasonsService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('starts a draft Season with POST seasons/{id}/start/ and returns its dashboard', async () => {
    const result = firstValueFrom(seasons.start(11));
    const req = http.expectOne(`${BASE}/seasons/11/start/`);
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual({});
    const body = seasonDashboard({ round_count_total: 56 });
    req.flush({ data: body, meta: {} });
    expect((await result).round_count_total).toBe(56);
  });

  it('surfaces a Season that is not a draft as ApiError 409 season_not_draft', async () => {
    const result = firstValueFrom(seasons.start(11)).catch((e: unknown) => e);
    http
      .expectOne(`${BASE}/seasons/11/start/`)
      .flush(
        { errors: [{ code: 'season_not_draft', message: 'Not a draft.', field: null }], meta: {} },
        { status: 409, statusText: 'Conflict' },
      );
    const err = (await result) as ApiError;
    expect(err.status).toBe(409);
    expect(err.errors[0].code).toBe('season_not_draft');
  });
});
