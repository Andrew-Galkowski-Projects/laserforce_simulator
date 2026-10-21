import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../environments/environment';
import { authInterceptor } from '../auth/auth.interceptor';
import { ApiError } from './api-client';
import { Match } from './matches.models';
import { MatchesService } from './matches.service';

const BASE = environment.apiBaseUrl;

const MATCH: Match = {
  id: 5,
  round_ids: [21, 22],
  match_type: 'friendly',
  date_played: '2026-10-21T12:00:00Z',
  red_round1_points: 4000,
  blue_round1_points: 3000,
  red_round1_eliminated: false,
  blue_round1_eliminated: false,
  round1_eliminated_at: 1801,
  red_round2_points: 3500,
  blue_round2_points: 3600,
  red_round2_eliminated: false,
  blue_round2_eliminated: false,
  round2_eliminated_at: 1801,
  red_bonus_points: 0,
  blue_bonus_points: 0,
  leg: 1,
  is_completed: true,
  team_red: 3,
  team_blue: 4,
  winner: 3,
  season: null,
  season_phase: null,
  conference: null,
  manager: 1,
};

describe('MatchesService', () => {
  let matches: MatchesService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(withInterceptors([authInterceptor])),
        provideHttpClientTesting(),
      ],
    });
    matches = TestBed.inject(MatchesService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('lists Matches with filters and pagination', async () => {
    const result = firstValueFrom(
      matches.list({
        team: 3,
        match_type: 'friendly',
        date_played_after: '2026-10-01',
        ordering: 'date_played',
        page: 1,
      }),
    );
    const req = http.expectOne((r) => r.url === `${BASE}/matches/`);
    expect(req.request.method).toBe('GET');
    expect(req.request.params.get('team')).toBe('3');
    expect(req.request.params.get('match_type')).toBe('friendly');
    expect(req.request.params.get('date_played_after')).toBe('2026-10-01');
    expect(req.request.params.get('ordering')).toBe('date_played');
    expect(req.request.params.get('page')).toBe('1');
    const meta = { page: 1, page_size: 20, count: 1, total_pages: 1 };
    req.flush({ data: [MATCH], meta });
    expect(await result).toEqual({ items: [MATCH], meta });
  });

  it('gets one Match', async () => {
    const result = firstValueFrom(matches.get(5));
    const req = http.expectOne(`${BASE}/matches/5/`);
    expect(req.request.method).toBe('GET');
    req.flush({ data: MATCH, meta: {} });
    expect(await result).toEqual(MATCH);
  });

  it('surfaces a cross-Account Match as ApiError 404', async () => {
    const result = firstValueFrom(matches.get(99)).catch((e: unknown) => e);
    http
      .expectOne(`${BASE}/matches/99/`)
      .flush(
        { errors: [{ code: 'not_found', message: 'Not found.', field: null }], meta: {} },
        { status: 404, statusText: 'Not Found' },
      );
    const err = (await result) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(404);
  });

  it('creates (simulates) a Match and returns the finished Match', async () => {
    const body = { team_red: 3, team_blue: 4, match_type: 'friendly' as const, arena_map: 2 };
    const result = firstValueFrom(matches.create(body));
    const req = http.expectOne(`${BASE}/matches/`);
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual(body);
    req.flush({ data: MATCH, meta: {} }, { status: 201, statusText: 'Created' });
    expect(await result).toEqual(MATCH);
  });

  it('reports a create validation error by field', async () => {
    const body = { team_red: 3, team_blue: 3, match_type: 'friendly' as const };
    const result = firstValueFrom(matches.create(body)).catch((e: unknown) => e);
    http.expectOne(`${BASE}/matches/`).flush(
      {
        errors: [{ code: 'invalid_choice', message: 'Select a valid choice.', field: 'team_blue' }],
        meta: {},
      },
      { status: 400, statusText: 'Bad Request' },
    );
    const err = (await result) as ApiError;
    expect(err.status).toBe(400);
    expect(err.errors[0].field).toBe('team_blue');
  });
});
