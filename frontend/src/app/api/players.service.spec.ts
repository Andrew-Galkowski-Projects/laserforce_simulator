import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../environments/environment';
import { authInterceptor } from '../auth/auth.interceptor';
import { ApiError } from './api-client';
import { CareerStats, Player, RoleBenchmarks } from './players.models';
import { PlayersService } from './players.service';
import { PROFILE, STATS } from './testing/player-fixtures';

const BASE = environment.apiBaseUrl;

const PLAYER: Player = {
  id: 11,
  team: 3,
  name: 'Ace',
  preferred_roles: ['commander'],
  ...STATS,
  ...PROFILE,
};

describe('PlayersService', () => {
  let players: PlayersService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(withInterceptors([authInterceptor])),
        provideHttpClientTesting(),
      ],
    });
    players = TestBed.inject(PlayersService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('lists Players with filters, ordering and pagination', async () => {
    const result = firstValueFrom(
      players.list({ team: 3, name: 'ac', ordering: '-offensive_synergy', page: 2 }),
    );
    const req = http.expectOne((r) => r.url === `${BASE}/players/`);
    expect(req.request.method).toBe('GET');
    expect(req.request.params.get('team')).toBe('3');
    expect(req.request.params.get('name')).toBe('ac');
    expect(req.request.params.get('ordering')).toBe('-offensive_synergy');
    expect(req.request.params.get('page')).toBe('2');
    const meta = { page: 2, page_size: 20, count: 21, total_pages: 2 };
    req.flush({ data: [PLAYER], meta });
    expect(await result).toEqual({ items: [PLAYER], meta });
  });

  it('gets one Player', async () => {
    const result = firstValueFrom(players.get(11));
    const req = http.expectOne(`${BASE}/players/11/`);
    expect(req.request.method).toBe('GET');
    req.flush({ data: PLAYER, meta: {} });
    expect(await result).toEqual(PLAYER);
  });

  it('surfaces a cross-Account Player as ApiError 404', async () => {
    const result = firstValueFrom(players.get(99)).catch((e: unknown) => e);
    http.expectOne(`${BASE}/players/99/`).flush(
      {
        errors: [{ code: 'not_found', message: 'No Player matches the given query.', field: null }],
        meta: {},
      },
      { status: 404, statusText: 'Not Found' },
    );
    const err = (await result) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(404);
    expect(err.errors[0].code).toBe('not_found');
  });

  it('edits a Player with PATCH', async () => {
    const body = { name: 'Ace II', accuracy: 80 };
    const result = firstValueFrom(players.update(11, body));
    const req = http.expectOne(`${BASE}/players/11/`);
    expect(req.request.method).toBe('PATCH');
    expect(req.request.body).toEqual(body);
    req.flush({ data: { ...PLAYER, ...body }, meta: {} });
    expect(await result).toEqual({ ...PLAYER, ...body });
  });

  it('deletes a Player (204 resolves to undefined)', async () => {
    const result = firstValueFrom(players.delete(11));
    const req = http.expectOne(`${BASE}/players/11/`);
    expect(req.request.method).toBe('DELETE');
    req.flush(null, { status: 204, statusText: 'No Content' });
    expect(await result).toBeUndefined();
  });

  const CAREER: CareerStats = {
    player: { id: 11, name: 'Ace', team: { id: 3, name: 'Red Dragons' } },
    total_rounds: 2,
    has_rounds: true,
    career: {
      games: 2,
      avg_points: 4522,
      tag_ratio: 0.96,
      avg_survival_ticks: 1238.5,
      avg_accuracy_pct: 54.6,
      avg_sp_earned: 37.5,
    },
    per_role: [],
    per_role_with_benchmarks: [],
    trend: [
      [1, 2502],
      [2, 4522],
    ],
    min_rounds: 3,
    display: 'median',
    stat_keys: ['points_scored'],
  };

  it('gets a Player career with the benchmark threshold and display', async () => {
    const result = firstValueFrom(players.career(11, { threshold: 3, display: 'median' }));
    const req = http.expectOne((r) => r.url === `${BASE}/players/11/career/`);
    expect(req.request.method).toBe('GET');
    expect(req.request.params.get('threshold')).toBe('3');
    expect(req.request.params.get('display')).toBe('median');
    req.flush({ data: CAREER, meta: {} });
    expect(await result).toEqual(CAREER);
  });

  it('leaves out the params it is not given, so the server defaults apply', async () => {
    const result = firstValueFrom(players.career(11));
    const req = http.expectOne((r) => r.url === `${BASE}/players/11/career/`);
    expect(req.request.params.keys()).toEqual([]);
    req.flush({ data: CAREER, meta: {} });
    await result;
  });

  it('gets the role benchmarks with the threshold and display', async () => {
    const body: RoleBenchmarks = {
      min_rounds: 0,
      display: 'mean',
      roles: ['commander'],
      stat_keys: ['points_scored'],
      benchmarks: {
        commander: [
          {
            stat: 'points_scored',
            mean: 7661.9,
            median: 7267,
            p25: 6582,
            p75: 7322,
            p90: 9531.5,
            n: 4,
          },
        ],
      },
      any_data: true,
    };
    const result = firstValueFrom(players.benchmarks({ threshold: 0, display: 'mean' }));
    const req = http.expectOne((r) => r.url === `${BASE}/players/benchmarks/`);
    expect(req.request.method).toBe('GET');
    expect(req.request.params.get('threshold')).toBe('0');
    expect(req.request.params.get('display')).toBe('mean');
    req.flush({ data: body, meta: {} });
    expect(await result).toEqual(body);
  });
});
