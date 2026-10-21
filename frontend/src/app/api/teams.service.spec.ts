import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../environments/environment';
import { ApiError } from './api-client';
import { Player } from './players.models';
import { Team, TeamListItem } from './teams.models';
import { TeamsService } from './teams.service';
import { STATS } from './testing/player-fixtures';

const BASE = environment.apiBaseUrl;

const SLOTS = {
  slot_commander: 11,
  slot_heavy: null,
  slot_scout_1: null,
  slot_scout_2: null,
  slot_medic: null,
  slot_ammo: null,
};

const PLAYER: Player = {
  id: 11,
  team: 3,
  name: 'Ace',
  preferred_roles: ['commander'],
  ...STATS,
};

const TEAM: Team = {
  id: 3,
  name: 'Red Raiders',
  created_date: '2026-10-21T12:00:00Z',
  wins: 2,
  losses: 1,
  ...SLOTS,
  players: [PLAYER],
};

const TEAM_LIST_ITEM: TeamListItem = {
  ...TEAM,
  players: [{ id: 11, name: 'Ace', preferred_roles: ['commander'] }],
};

describe('TeamsService', () => {
  let teams: TeamsService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    teams = TestBed.inject(TeamsService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('lists Teams with filters and pagination', async () => {
    const result = firstValueFrom(teams.list({ name: 'red', include_free_agents: true, page: 1 }));
    const req = http.expectOne((r) => r.url === `${BASE}/teams/`);
    expect(req.request.method).toBe('GET');
    expect(req.request.params.get('name')).toBe('red');
    expect(req.request.params.get('include_free_agents')).toBe('true');
    expect(req.request.params.get('page')).toBe('1');
    const meta = { page: 1, page_size: 20, count: 1, total_pages: 1 };
    req.flush({ data: [TEAM_LIST_ITEM], meta });
    expect(await result).toEqual({ items: [TEAM_LIST_ITEM], meta });
  });

  it('lists Teams with no params', async () => {
    const result = firstValueFrom(teams.list());
    const req = http.expectOne((r) => r.url === `${BASE}/teams/`);
    expect(req.request.params.keys()).toEqual([]);
    req.flush({ data: [], meta: { page: 1, page_size: 20, count: 0, total_pages: 1 } });
    expect((await result).items).toEqual([]);
  });

  it('gets one Team with full players', async () => {
    const result = firstValueFrom(teams.get(3));
    const req = http.expectOne(`${BASE}/teams/3/`);
    expect(req.request.method).toBe('GET');
    req.flush({ data: TEAM, meta: {} });
    expect(await result).toEqual(TEAM);
  });

  it('surfaces a cross-Account Team as ApiError 404', async () => {
    const result = firstValueFrom(teams.get(99)).catch((e: unknown) => e);
    http.expectOne(`${BASE}/teams/99/`).flush(
      {
        errors: [{ code: 'not_found', message: 'No Team matches the given query.', field: null }],
        meta: {},
      },
      { status: 404, statusText: 'Not Found' },
    );
    const err = (await result) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(404);
  });

  it('creates a Team', async () => {
    const result = firstValueFrom(teams.create({ name: 'Red Raiders' }));
    const req = http.expectOne(`${BASE}/teams/`);
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual({ name: 'Red Raiders' });
    req.flush({ data: TEAM, meta: {} }, { status: 201, statusText: 'Created' });
    expect(await result).toEqual(TEAM);
  });

  it('reports a create validation error by field', async () => {
    const result = firstValueFrom(teams.create({ name: '' })).catch((e: unknown) => e);
    http.expectOne(`${BASE}/teams/`).flush(
      {
        errors: [{ code: 'required', message: 'This field is required.', field: 'name' }],
        meta: {},
      },
      { status: 400, statusText: 'Bad Request' },
    );
    const err = (await result) as ApiError;
    expect(err.status).toBe(400);
    expect(err.errors[0].field).toBe('name');
  });

  it('renames a Team with PATCH', async () => {
    const result = firstValueFrom(teams.update(3, { name: 'Blue Barons' }));
    const req = http.expectOne(`${BASE}/teams/3/`);
    expect(req.request.method).toBe('PATCH');
    expect(req.request.body).toEqual({ name: 'Blue Barons' });
    req.flush({ data: { ...TEAM, name: 'Blue Barons' }, meta: {} });
    expect((await result).name).toBe('Blue Barons');
  });

  it('adds a Player to a Team', async () => {
    const body = { name: 'Ace', preferred_roles: ['commander' as const], accuracy: 70 };
    const result = firstValueFrom(teams.addPlayer(3, body));
    const req = http.expectOne(`${BASE}/teams/3/players/`);
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual(body);
    req.flush({ data: PLAYER, meta: {} }, { status: 201, statusText: 'Created' });
    expect(await result).toEqual(PLAYER);
  });
});
