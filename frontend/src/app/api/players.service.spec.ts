import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../environments/environment';
import { authInterceptor } from '../auth/auth.interceptor';
import { ApiError } from './api-client';
import { Player } from './players.models';
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
});
