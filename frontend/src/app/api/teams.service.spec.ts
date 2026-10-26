import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../environments/environment';
import { ApiError } from './api-client';
import { Player } from './players.models';
import { Team, TeamListItem } from './teams.models';
import { TeamsService } from './teams.service';
import { PROFILE, STATS } from './testing/player-fixtures';

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
  ...PROFILE,
};

const TEAM: Team = {
  id: 3,
  name: 'Red Raiders',
  created_date: '2026-10-21T12:00:00Z',
  wins: 2,
  losses: 1,
  ...SLOTS,
  is_valid_roster: false,
  roster_errors: ['missing Heavy'],
  manager: null,
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

  it('replaces all six role slots with PUT teams/{id}/slots/', async () => {
    const body = {
      slot_commander: 11,
      slot_heavy: null,
      slot_scout_1: null,
      slot_scout_2: null,
      slot_medic: null,
      slot_ammo: null,
    };
    const result = firstValueFrom(teams.setSlots(3, body));
    const req = http.expectOne(`${BASE}/teams/3/slots/`);
    expect(req.request.method).toBe('PUT');
    expect(req.request.body).toEqual(body);
    req.flush({ data: TEAM, meta: {} });
    expect(await result).toEqual(TEAM);
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

  it('generates Teams with POST teams/generate/ (selects as strings, stats as numbers)', async () => {
    const body = { num_teams: '2', players_per_team: 'random_team', mean: 50, std_dev: 15 };
    const result = firstValueFrom(teams.generate(body));
    const req = http.expectOne(`${BASE}/teams/generate/`);
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual(body);
    expect(typeof req.request.body.num_teams).toBe('string');
    expect(typeof req.request.body.players_per_team).toBe('string');
    expect(typeof req.request.body.mean).toBe('number');
    expect(typeof req.request.body.std_dev).toBe('number');
    const data = { created_teams: [TEAM_LIST_ITEM], free_agent_count: 0 };
    req.flush({ data, meta: {} }, { status: 201, statusText: 'Created' });
    expect(await result).toEqual(data);
  });

  it('imports a roster as multipart POST teams/import/ with the file under csv_file', async () => {
    const file = new File(['team,name,role\n'], 'roster.csv', { type: 'text/csv' });
    const result = firstValueFrom(teams.importRoster(file));
    const req = http.expectOne(`${BASE}/teams/import/`);
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toBeInstanceOf(FormData);
    const form = req.request.body as FormData;
    expect([...form.keys()]).toEqual(['csv_file']);
    expect(form.get('csv_file')).toBe(file);
    expect(req.request.headers.has('Content-Type')).toBe(false);
    const data = {
      created_teams: [TEAM_LIST_ITEM],
      appended_teams: [],
      player_count: 6,
      row_count: 6,
    };
    req.flush({ data, meta: {} }, { status: 201, statusText: 'Created' });
    expect(await result).toEqual(data);
  });

  it('downloads the roster template as a Blob from GET teams/import/template/, body as-is', async () => {
    const result = firstValueFrom(teams.importTemplate());
    const req = http.expectOne(`${BASE}/teams/import/template/`);
    expect(req.request.method).toBe('GET');
    expect(req.request.responseType).toBe('blob');
    const csv = new Blob(['team,name,role\n'], { type: 'text/csv' });
    req.flush(csv);
    expect(await result).toBe(csv);
  });
});
