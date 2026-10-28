import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../environments/environment';
import { ApiError } from './api-client';
import { PoolEntry, Tournament, TournamentDetail } from './tournaments.models';
import { TournamentsService } from './tournaments.service';

const BASE = environment.apiBaseUrl;

const TOURNAMENT: Tournament = {
  id: 4,
  name: 'Autumn Cup',
  format: 'single_elimination',
  format_label: 'Single elimination',
  state: 'setup',
  team_assembly: 'preset',
  role_assignment_mode: 'random',
  champion: null,
  embedded: false,
  season: null,
  final_series_length: 1,
  semifinal_series_length: 1,
  quarterfinal_series_length: 1,
  earlier_series_length: 1,
  wb_advancers: 0,
  lb_advancers: 0,
  swiss_rounds: 0,
  manager: 1,
  created_at: '2026-10-27T09:15:00Z',
};

const DETAIL: TournamentDetail = {
  ...TOURNAMENT,
  participants: [{ team: { id: 3, name: 'Red Raiders' }, seed: 1 }],
  brackets: [],
  rr_standings: [],
  swiss_standings: [],
  stage: 'setup',
  cut_labels: {},
  pool_size: 0,
  is_drawn: false,
};

const ENTRY: PoolEntry = {
  player: { id: 25, name: 'Ace', team: { id: 3, name: 'Red Raiders' } },
  tier: null,
  drawn_team: null,
};

const META = { page: 1, page_size: 20, count: 1, total_pages: 1 };

describe('TournamentsService', () => {
  let tournaments: TournamentsService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    tournaments = TestBed.inject(TournamentsService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  /** The multipart body of `req`: exactly one `csv_file` part holding `file`, no JSON header. */
  function expectCsvUpload(body: unknown, file: File): void {
    expect(body).toBeInstanceOf(FormData);
    const form = body as FormData;
    expect([...form.keys()]).toEqual(['csv_file']);
    expect(form.get('csv_file')).toBe(file);
  }

  it('lists Tournaments with GET tournaments/ and its params', async () => {
    const result = firstValueFrom(tournaments.list({ page: 2, page_size: 20, state: 'setup' }));
    const req = http.expectOne((r) => r.url === `${BASE}/tournaments/`);
    expect(req.request.method).toBe('GET');
    expect(req.request.params.get('page')).toBe('2');
    expect(req.request.params.get('page_size')).toBe('20');
    expect(req.request.params.get('state')).toBe('setup');
    req.flush({ data: [TOURNAMENT], meta: META });
    expect(await result).toEqual({ items: [TOURNAMENT], meta: META });
  });

  it('lists Tournaments with no params', async () => {
    const result = firstValueFrom(tournaments.list());
    const req = http.expectOne((r) => r.url === `${BASE}/tournaments/`);
    expect(req.request.params.keys()).toEqual([]);
    req.flush({ data: [], meta: { ...META, count: 0 } });
    expect((await result).items).toEqual([]);
  });

  it('gets one Tournament with GET tournaments/{id}/', async () => {
    const result = firstValueFrom(tournaments.get(4));
    const req = http.expectOne(`${BASE}/tournaments/4/`);
    expect(req.request.method).toBe('GET');
    req.flush({ data: DETAIL, meta: {} });
    expect(await result).toEqual(DETAIL);
  });

  it("surfaces another Account's Tournament as ApiError 404", async () => {
    const result = firstValueFrom(tournaments.get(99)).catch((e: unknown) => e);
    http
      .expectOne(`${BASE}/tournaments/99/`)
      .flush(
        { errors: [{ code: 'not_found', message: 'Not found.', field: null }], meta: {} },
        { status: 404, statusText: 'Not Found' },
      );
    const err = (await result) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(404);
  });

  it('creates a Tournament with POST tournaments/ and the body as given', async () => {
    const body = { name: 'Autumn Cup', format: 'swiss' as const, teams: [3, 5], swiss_rounds: 0 };
    const result = firstValueFrom(tournaments.create(body));
    const req = http.expectOne(`${BASE}/tournaments/`);
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual(body);
    req.flush({ data: DETAIL, meta: {} }, { status: 201, statusText: 'Created' });
    expect(await result).toEqual(DETAIL);
  });

  it('reports a create error by field (too_few_teams on teams)', async () => {
    const result = firstValueFrom(tournaments.create({ name: 'Cup', teams: [3] })).catch(
      (e: unknown) => e,
    );
    http.expectOne(`${BASE}/tournaments/`).flush(
      {
        errors: [
          {
            code: 'too_few_teams',
            message: 'A tournament requires at least 4 teams.',
            field: 'teams',
          },
        ],
        meta: {},
      },
      { status: 400, statusText: 'Bad Request' },
    );
    const err = (await result) as ApiError;
    expect(err.status).toBe(400);
    expect(err.errors[0].field).toBe('teams');
  });

  it('lists the Player pool with GET tournaments/{id}/pool/ and its params', async () => {
    const result = firstValueFrom(tournaments.pool(4, { page: 1, page_size: 100 }));
    const req = http.expectOne((r) => r.url === `${BASE}/tournaments/4/pool/`);
    expect(req.request.method).toBe('GET');
    expect(req.request.params.get('page')).toBe('1');
    expect(req.request.params.get('page_size')).toBe('100');
    req.flush({ data: [ENTRY], meta: META });
    expect(await result).toEqual({ items: [ENTRY], meta: META });
  });

  it('adds Players to the pool with POST tournaments/{id}/pool/players/ and {player_ids}', async () => {
    const result = firstValueFrom(tournaments.addPoolPlayers(4, [25, 26]));
    const req = http.expectOne(`${BASE}/tournaments/4/pool/players/`);
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual({ player_ids: [25, 26] });
    req.flush({ data: [ENTRY], meta: {} }, { status: 201, statusText: 'Created' });
    expect(await result).toEqual([ENTRY]);
  });

  it('removes a pool Player with DELETE tournaments/{id}/pool/players/{playerId}/', async () => {
    const result = firstValueFrom(tournaments.removePoolPlayer(4, 25), { defaultValue: 'done' });
    const req = http.expectOne(`${BASE}/tournaments/4/pool/players/25/`);
    expect(req.request.method).toBe('DELETE');
    req.flush(null, { status: 204, statusText: 'No Content' });
    expect(await result).toBeUndefined();
  });

  it('generates pool Players with POST tournaments/{id}/pool/generate/', async () => {
    const body = { count: 24, mean: 50, std_dev: 15 };
    const result = firstValueFrom(tournaments.generatePool(4, body));
    const req = http.expectOne(`${BASE}/tournaments/4/pool/generate/`);
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual(body);
    req.flush({ data: [ENTRY], meta: {} }, { status: 201, statusText: 'Created' });
    expect(await result).toEqual([ENTRY]);
  });

  it('imports pool Players as multipart POST tournaments/{id}/pool/import/ under csv_file', async () => {
    const file = new File(['team,name,role\n'], 'pool.csv', { type: 'text/csv' });
    const result = firstValueFrom(tournaments.importPool(4, file));
    const req = http.expectOne(`${BASE}/tournaments/4/pool/import/`);
    expect(req.request.method).toBe('POST');
    expectCsvUpload(req.request.body, file);
    expect(req.request.headers.has('Content-Type')).toBe(false);
    req.flush({ data: [ENTRY], meta: {} }, { status: 201, statusText: 'Created' });
    expect(await result).toEqual([ENTRY]);
  });

  it('imports participants as multipart POST tournaments/{id}/import-participants/ under csv_file', async () => {
    const file = new File(['team,name,role\n'], 'teams.csv', { type: 'text/csv' });
    const result = firstValueFrom(tournaments.importParticipants(4, file));
    const req = http.expectOne(`${BASE}/tournaments/4/import-participants/`);
    expect(req.request.method).toBe('POST');
    expectCsvUpload(req.request.body, file);
    expect(req.request.headers.has('Content-Type')).toBe(false);
    req.flush({ data: DETAIL, meta: {} }, { status: 201, statusText: 'Created' });
    expect(await result).toEqual(DETAIL);
  });

  it('reports a stale pool write as ApiError 409 tournament_not_setup', async () => {
    const result = firstValueFrom(tournaments.addPoolPlayers(4, [25])).catch((e: unknown) => e);
    http.expectOne(`${BASE}/tournaments/4/pool/players/`).flush(
      {
        errors: [
          {
            code: 'tournament_not_setup',
            message: 'The Tournament is no longer in setup.',
            field: null,
          },
        ],
        meta: {},
      },
      { status: 409, statusText: 'Conflict' },
    );
    const err = (await result) as ApiError;
    expect(err.status).toBe(409);
    expect(err.errors[0].code).toBe('tournament_not_setup');
  });
});
