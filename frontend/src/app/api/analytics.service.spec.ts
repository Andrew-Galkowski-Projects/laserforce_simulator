import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../environments/environment';
import { ApiError } from './api-client';
import { AnalyticsService } from './analytics.service';

const BASE = environment.apiBaseUrl;

describe('AnalyticsService', () => {
  let analytics: AnalyticsService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    analytics = TestBed.inject(AnalyticsService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('GETs the team head-to-head with its params and unwraps data', async () => {
    const result = firstValueFrom(
      analytics.teamHeadToHead({
        team_a: 3,
        team_b: 4,
        provenance: 'real',
        from: '2026-01-01',
        to: undefined,
      }),
    );
    const req = http.expectOne((r) => r.url === `${BASE}/teams/head-to-head/`);
    expect(req.request.method).toBe('GET');
    expect(req.request.params.keys().sort()).toEqual(['from', 'provenance', 'team_a', 'team_b']);
    expect(req.request.params.get('team_a')).toBe('3');
    expect(req.request.params.get('team_b')).toBe('4');
    expect(req.request.params.get('provenance')).toBe('real');
    expect(req.request.params.get('from')).toBe('2026-01-01');
    req.flush({ data: { team_a: { id: 3, name: 'A' } }, meta: {} });
    expect(await result).toEqual({ team_a: { id: 3, name: 'A' } });
  });

  it('GETs the player head-to-head with its params', async () => {
    const result = firstValueFrom(
      analytics.playerHeadToHead({ player_a: 7, player_b: 8, role: 'scout', to: '2026-02-01' }),
    );
    const req = http.expectOne((r) => r.url === `${BASE}/players/head-to-head/`);
    expect(req.request.method).toBe('GET');
    expect(req.request.params.keys().sort()).toEqual(['player_a', 'player_b', 'role', 'to']);
    expect(req.request.params.get('player_a')).toBe('7');
    expect(req.request.params.get('role')).toBe('scout');
    req.flush({ data: { round_record: { n: 0 } }, meta: {} });
    expect(await result).toEqual({ round_record: { n: 0 } });
  });

  it('GETs compare rounds with round_a and round_b', async () => {
    const result = firstValueFrom(analytics.compareRounds(11, 12));
    const req = http.expectOne((r) => r.url === `${BASE}/rounds/compare/`);
    expect(req.request.method).toBe('GET');
    expect(req.request.params.keys().sort()).toEqual(['round_a', 'round_b']);
    expect(req.request.params.get('round_a')).toBe('11');
    expect(req.request.params.get('round_b')).toBe('12');
    req.flush({ data: { stat_keys: [] }, meta: {} });
    expect(await result).toEqual({ stat_keys: [] });
  });

  it('surfaces a compare 400 as an ApiError with the server message', async () => {
    const result = firstValueFrom(analytics.compareRounds(11, 11));
    const message = 'Pick two different rounds to compare.';
    http
      .expectOne((r) => r.url === `${BASE}/rounds/compare/`)
      .flush(
        { errors: [{ code: 'invalid', message, field: null }], meta: {} },
        { status: 400, statusText: 'Bad Request' },
      );
    await expect(result).rejects.toEqual(
      new ApiError(400, [{ code: 'invalid', message, field: null }]),
    );
  });

  describe('teamHistory', () => {
    it('sends no scope for null (every scope)', async () => {
      const result = firstValueFrom(analytics.teamHistory(5, null));
      const req = http.expectOne((r) => r.url === `${BASE}/teams/5/history/`);
      expect(req.request.method).toBe('GET');
      expect(req.request.params.keys()).toEqual([]);
      req.flush({ data: { team: { id: 5, name: 'T' } }, meta: {} });
      expect(await result).toEqual({ team: { id: 5, name: 'T' } });
    });

    it('sends scope= for an empty list (no scope)', async () => {
      const result = firstValueFrom(analytics.teamHistory(5, []));
      const req = http.expectOne((r) => r.url === `${BASE}/teams/5/history/`);
      expect(req.request.params.getAll('scope')).toEqual(['']);
      expect(req.request.urlWithParams).toBe(`${BASE}/teams/5/history/?scope=`);
      req.flush({ data: {}, meta: {} });
      await result;
    });

    it('repeats scope once per selected key', async () => {
      const result = firstValueFrom(analytics.teamHistory(5, ['freeform', 't9']));
      const req = http.expectOne((r) => r.url === `${BASE}/teams/5/history/`);
      expect(req.request.params.getAll('scope')).toEqual(['freeform', 't9']);
      req.flush({ data: {}, meta: {} });
      await result;
    });
  });
});
