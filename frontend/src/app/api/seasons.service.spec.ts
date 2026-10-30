import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  TestRequest,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../environments/environment';
import { seasonDashboard } from '../leagues/testing/league-fixtures';
import { failure } from '../leagues/testing/league-stats-fixtures';
import {
  conferencesState,
  ownerEvaluation,
  season,
  seasonAwards,
  seasonSchedule,
  seasonStandings,
} from '../leagues/seasons/testing/season-fixtures';
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

  function expectOne(path: string, method: string): TestRequest {
    const req = http.expectOne((r) => r.url === `${BASE}/${path}`);
    expect(req.request.method).toBe(method);
    return req;
  }

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

  it('reads a Season with GET seasons/{id}/', async () => {
    const result = firstValueFrom(seasons.get(11));
    const req = expectOne('seasons/11/', 'GET');
    expect(req.request.params.keys()).toEqual([]);
    req.flush({ data: season(), meta: {} });
    expect(await result).toEqual(season());
  });

  it("surfaces another Account's Season as ApiError 404", async () => {
    const result = firstValueFrom(seasons.get(99)).catch((e: unknown) => e);
    expectOne('seasons/99/', 'GET').flush(...failure(404, 'Not found.'));
    const err = (await result) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(404);
  });

  it('reads the dashboard with GET seasons/{id}/dashboard/, member-night progress included', async () => {
    const result = firstValueFrom(seasons.dashboard(11));
    const body = seasonDashboard({ member_night_progress: { played: 1, total: 2 } });
    expectOne('seasons/11/dashboard/', 'GET').flush({ data: body, meta: {} });
    expect((await result).member_night_progress).toEqual({ played: 1, total: 2 });
  });

  it('reads the standings with GET seasons/{id}/standings/ and no params by default', async () => {
    const result = firstValueFrom(seasons.standings(11, {}));
    const req = expectOne('seasons/11/standings/', 'GET');
    expect(req.request.params.keys()).toEqual([]);
    req.flush({ data: seasonStandings(), meta: {} });
    expect(await result).toEqual(seasonStandings());
  });

  it('sends ?sort=&dir= for the standings sort', async () => {
    const result = firstValueFrom(seasons.standings(11, { sort: 'wins', dir: 'desc' }));
    const req = expectOne('seasons/11/standings/', 'GET');
    expect(req.request.params.get('sort')).toBe('wins');
    expect(req.request.params.get('dir')).toBe('desc');
    req.flush({ data: seasonStandings({ sort: 'wins', dir: 'desc' }), meta: {} });
    expect((await result).sort).toBe('wins');
  });

  it('surfaces a bad standings param as ApiError 400', async () => {
    const result = firstValueFrom(seasons.standings(11, { dir: 'asc' })).catch((e: unknown) => e);
    expectOne('seasons/11/standings/', 'GET').flush(...failure(400, 'Bad choice.'));
    expect(((await result) as ApiError).status).toBe(400);
  });

  it('reads the schedule with GET seasons/{id}/schedule/', async () => {
    const result = firstValueFrom(seasons.schedule(11));
    expectOne('seasons/11/schedule/', 'GET').flush({ data: seasonSchedule(), meta: {} });
    expect(await result).toEqual(seasonSchedule());
  });

  it('reads the awards with GET seasons/{id}/awards/', async () => {
    const result = firstValueFrom(seasons.awards(11));
    expectOne('seasons/11/awards/', 'GET').flush({ data: seasonAwards(), meta: {} });
    expect(await result).toEqual(seasonAwards());
  });

  it('reads the owner evaluation with GET seasons/{id}/owner-evaluation/', async () => {
    const result = firstValueFrom(seasons.ownerEvaluation(11));
    expectOne('seasons/11/owner-evaluation/', 'GET').flush({ data: ownerEvaluation(), meta: {} });
    expect(await result).toEqual(ownerEvaluation());
  });

  it('surfaces a Season with no owner evaluation as ApiError 404', async () => {
    const result = firstValueFrom(seasons.ownerEvaluation(11)).catch((e: unknown) => e);
    expectOne('seasons/11/owner-evaluation/', 'GET').flush(
      ...failure(404, 'No owner evaluation for this Season.'),
    );
    expect(((await result) as ApiError).status).toBe(404);
  });

  it('reads the Conferences with GET seasons/{id}/conferences/', async () => {
    const result = firstValueFrom(seasons.conferences(11));
    expectOne('seasons/11/conferences/', 'GET').flush({ data: conferencesState(), meta: {} });
    expect(await result).toEqual(conferencesState());
  });

  it('replaces the Conferences with PUT seasons/{id}/conferences/ and returns the saved state', async () => {
    const body = {
      conferences: [
        { name: 'East', team_ids: [3, 5], map_rotation_ids: [2] },
        { name: 'West', team_ids: [7, 8], map_rotation_ids: [] },
      ],
    };
    const result = firstValueFrom(seasons.replaceConferences(11, body));
    const req = expectOne('seasons/11/conferences/', 'PUT');
    expect(req.request.body).toEqual(body);
    req.flush({ data: conferencesState({ unassigned_team_ids: [] }), meta: {} });
    expect((await result).unassigned_team_ids).toEqual([]);
  });

  it('surfaces a started Season as ApiError 409 season_not_draft on PUT', async () => {
    const result = firstValueFrom(seasons.replaceConferences(11, { conferences: [] })).catch(
      (e: unknown) => e,
    );
    const req = expectOne('seasons/11/conferences/', 'PUT');
    expect(req.request.body).toEqual({ conferences: [] });
    req.flush(
      {
        errors: [{ code: 'season_not_draft', message: 'Not a draft.', field: null }],
        meta: {},
      },
      { status: 409, statusText: 'Conflict' },
    );
    expect(((await result) as ApiError).errors[0].code).toBe('season_not_draft');
  });
});
