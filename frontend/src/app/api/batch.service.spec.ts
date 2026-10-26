import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../environments/environment';
import { authInterceptor } from '../auth/auth.interceptor';
import { aggregate, runStatus, STARTED } from '../matches/testing/batch-fixtures';
import { ApiError } from './api-client';
import { BatchService } from './batch.service';

const BASE = environment.apiBaseUrl;

describe('BatchService', () => {
  let batch: BatchService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(withInterceptors([authInterceptor])),
        provideHttpClientTesting(),
      ],
    });
    batch = TestBed.inject(BatchService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('starts a Batch run job and unwraps the started run', async () => {
    const result = firstValueFrom(
      batch.start({ team_red: 1, team_blue: 2, n: 100, arena_map: null }),
    );
    const req = http.expectOne(`${BASE}/simulate-batch/`);
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual({ team_red: 1, team_blue: 2, n: 100, arena_map: null });
    req.flush({ data: STARTED, meta: {} });
    expect(await result).toEqual(STARTED);
  });

  it("polls a Batch run job's status, with no query params", async () => {
    const status = runStatus({ status: 'complete', completed: 10, partial: aggregate() });
    const result = firstValueFrom(batch.status('job-1'));
    const req = http.expectOne(`${BASE}/simulate-batch/job-1/`);
    expect(req.request.method).toBe('GET');
    expect(req.request.params.keys()).toEqual([]);
    req.flush({ data: status, meta: {} });
    expect(await result).toEqual(status);
  });

  it('starts a Save-games job for a Batch run job', async () => {
    const input = { team_red: 1, team_blue: 2, arena_map: 4, game_type: 'outlier' as const, n: 5 };
    const result = firstValueFrom(batch.saveGames('job-1', input));
    const req = http.expectOne(`${BASE}/simulate-batch/job-1/save/`);
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual(input);
    req.flush({ data: { job_id: 'save-1' }, meta: {} });
    expect(await result).toEqual({ job_id: 'save-1' });
  });

  it('reports a save of an unfinished Batch run as ApiError 409 with its message', async () => {
    const input = { team_red: 1, team_blue: 2, arena_map: null, game_type: 'avg' as const, n: 1 };
    const result = firstValueFrom(batch.saveGames('old', input)).catch((e: unknown) => e);
    http.expectOne(`${BASE}/simulate-batch/old/save/`).flush(
      {
        errors: [
          { code: 'job_not_complete', message: 'The batch job is not complete.', field: null },
        ],
        meta: {},
      },
      { status: 409, statusText: 'Conflict' },
    );
    const err = (await result) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(409);
    expect(err.message).toBe('The batch job is not complete.');
  });

  it("polls a Save-games job's status", async () => {
    const status = { status: 'complete' as const, error: null, round_ids: [5, 6] };
    const result = firstValueFrom(batch.saveStatus('save-1'));
    const req = http.expectOne(`${BASE}/simulate-batch/save/save-1/`);
    expect(req.request.method).toBe('GET');
    req.flush({ data: status, meta: {} });
    expect(await result).toEqual(status);
  });
});
