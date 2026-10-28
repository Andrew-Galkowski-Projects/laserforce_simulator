import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../environments/environment';
import {
  deleteSummary,
  historyRow,
  league,
  leagueCreateResult,
  leagueDashboard,
  leagueNav,
  leagueTemplate,
  newTeamCandidates,
  season,
} from '../leagues/testing/league-fixtures';
import { ApiError } from './api-client';
import { LeaguesService } from './leagues.service';

const BASE = environment.apiBaseUrl;
const META = { page: 1, page_size: 20, count: 1, total_pages: 1 };

describe('LeaguesService', () => {
  let leagues: LeaguesService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    leagues = TestBed.inject(LeaguesService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  function fail(status: number, code: string, message: string) {
    return [
      { errors: [{ code, message, field: null }], meta: {} },
      { status, statusText: 'Error' },
    ] as const;
  }

  it('lists Leagues with GET leagues/ and its params', async () => {
    const result = firstValueFrom(leagues.list({ page: 1, page_size: 100, state: 'archived' }));
    const req = http.expectOne((r) => r.url === `${BASE}/leagues/`);
    expect(req.request.method).toBe('GET');
    expect(req.request.params.get('page')).toBe('1');
    expect(req.request.params.get('page_size')).toBe('100');
    expect(req.request.params.get('state')).toBe('archived');
    req.flush({ data: [league()], meta: META });
    expect(await result).toEqual({ items: [league()], meta: META });
  });

  it('lists Leagues with no params', async () => {
    const result = firstValueFrom(leagues.list());
    const req = http.expectOne((r) => r.url === `${BASE}/leagues/`);
    expect(req.request.params.keys()).toEqual([]);
    req.flush({ data: [], meta: { ...META, count: 0 } });
    expect((await result).items).toEqual([]);
  });

  it('gets one League with GET leagues/{id}/', async () => {
    const result = firstValueFrom(leagues.get(1));
    const req = http.expectOne(`${BASE}/leagues/1/`);
    expect(req.request.method).toBe('GET');
    req.flush({ data: league(), meta: {} });
    expect(await result).toEqual(league());
  });

  it("surfaces another Account's League as ApiError 404", async () => {
    const result = firstValueFrom(leagues.get(99)).catch((e: unknown) => e);
    http.expectOne(`${BASE}/leagues/99/`).flush(...fail(404, 'not_found', 'Not found.'));
    const err = (await result) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(404);
  });

  it('reads the templates with GET leagues/templates/ (a plain list)', async () => {
    const result = firstValueFrom(leagues.templates());
    const req = http.expectOne(`${BASE}/leagues/templates/`);
    expect(req.request.method).toBe('GET');
    req.flush({ data: [leagueTemplate()], meta: {} });
    expect(await result).toEqual([leagueTemplate()]);
  });

  it('creates a League from a template with POST leagues/ and the body as given', async () => {
    const body = { template: '8_team_classic', league_name: 'Metro', difficulty: 'hard' as const };
    const result = firstValueFrom(leagues.create(body));
    const req = http.expectOne(`${BASE}/leagues/`);
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual(body);
    req.flush({ data: leagueCreateResult(), meta: {} }, { status: 201, statusText: 'Created' });
    expect(await result).toEqual(leagueCreateResult());
  });

  it('creates an Advanced League with POST leagues/ and the Form fields as given', async () => {
    const body = {
      league_name: 'Metro',
      season_name: 'Season 1',
      start_date: '2026-11-01',
      num_teams: 8 as const,
      mean: 50,
      std_dev: 15,
      map_mode: 'rotate_by_matchday' as const,
      map_rotation: '4,2,9',
      phases: 'round_robin,tournament',
    };
    const result = firstValueFrom(leagues.create(body));
    const req = http.expectOne(`${BASE}/leagues/`);
    expect(req.request.body).toEqual(body);
    req.flush({ data: leagueCreateResult(), meta: {} }, { status: 201, statusText: 'Created' });
    expect((await result).draft_season).toEqual({ id: 11, name: 'Season 1' });
  });

  it('deletes a League with DELETE leagues/{id}/', async () => {
    const result = firstValueFrom(leagues.delete(1));
    const req = http.expectOne(`${BASE}/leagues/1/`);
    expect(req.request.method).toBe('DELETE');
    req.flush(null, { status: 204, statusText: 'No Content' });
    expect(await result).toBeUndefined();
  });

  it('reads the delete summary with GET leagues/{id}/delete-summary/', async () => {
    const result = firstValueFrom(leagues.deleteSummary(1));
    const req = http.expectOne(`${BASE}/leagues/1/delete-summary/`);
    expect(req.request.method).toBe('GET');
    req.flush({ data: deleteSummary(), meta: {} });
    expect(await result).toEqual(deleteSummary());
  });

  it('surfaces a non-career delete summary as ApiError 409 not_career_league', async () => {
    const result = firstValueFrom(leagues.deleteSummary(2)).catch((e: unknown) => e);
    http
      .expectOne(`${BASE}/leagues/2/delete-summary/`)
      .flush(...fail(409, 'not_career_league', 'Only career Leagues can be deleted.'));
    const err = (await result) as ApiError;
    expect(err.status).toBe(409);
    expect(err.errors[0].code).toBe('not_career_league');
  });

  it('reads the dashboard with GET leagues/{id}/dashboard/', async () => {
    const result = firstValueFrom(leagues.dashboard(1));
    const req = http.expectOne(`${BASE}/leagues/1/dashboard/`);
    expect(req.request.method).toBe('GET');
    req.flush({ data: leagueDashboard(), meta: {} });
    expect(await result).toEqual(leagueDashboard());
  });

  it('reads the nav with GET leagues/{id}/nav/', async () => {
    const result = firstValueFrom(leagues.nav(1));
    const req = http.expectOne(`${BASE}/leagues/1/nav/`);
    expect(req.request.method).toBe('GET');
    req.flush({ data: leagueNav(), meta: {} });
    expect(await result).toEqual(leagueNav());
  });

  it('reads one history page with GET leagues/{id}/history/ and page / page_size', async () => {
    const result = firstValueFrom(leagues.history(1, 2, 25));
    const req = http.expectOne((r) => r.url === `${BASE}/leagues/1/history/`);
    expect(req.request.method).toBe('GET');
    expect(req.request.params.get('page')).toBe('2');
    expect(req.request.params.get('page_size')).toBe('25');
    const meta = { page: 2, page_size: 25, count: 26, total_pages: 2 };
    req.flush({ data: [historyRow()], meta });
    expect(await result).toEqual({ items: [historyRow()], meta });
  });

  it('reads the new-team candidates with GET leagues/{id}/new-team-candidates/', async () => {
    const result = firstValueFrom(leagues.newTeamCandidates(1));
    const req = http.expectOne(`${BASE}/leagues/1/new-team-candidates/`);
    expect(req.request.method).toBe('GET');
    req.flush({ data: newTeamCandidates(), meta: {} });
    expect(await result).toEqual(newTeamCandidates());
  });

  it('surfaces a Manager who was not fired as ApiError 409 manager_not_fired', async () => {
    const result = firstValueFrom(leagues.newTeamCandidates(1)).catch((e: unknown) => e);
    http
      .expectOne(`${BASE}/leagues/1/new-team-candidates/`)
      .flush(...fail(409, 'manager_not_fired', 'You were not fired.'));
    expect(((await result) as ApiError).errors[0].code).toBe('manager_not_fired');
  });

  it('reassigns the team with POST leagues/{id}/reassign-team/ and {team_id}', async () => {
    const result = firstValueFrom(leagues.reassignTeam(1, 7));
    const req = http.expectOne(`${BASE}/leagues/1/reassign-team/`);
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual({ team_id: 7 });
    req.flush({ data: season(), meta: {} }, { status: 201, statusText: 'Created' });
    expect(await result).toEqual(season());
  });

  it('starts the next Season with POST leagues/{id}/next-season/ and an empty body', async () => {
    const result = firstValueFrom(leagues.nextSeason(1));
    const req = http.expectOne(`${BASE}/leagues/1/next-season/`);
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual({});
    req.flush(
      { data: season({ state: 'active' }), meta: {} },
      { status: 201, statusText: 'Created' },
    );
    expect((await result).state).toBe('active');
  });

  it('surfaces a fired Manager on next-season as ApiError 409 manager_fired', async () => {
    const result = firstValueFrom(leagues.nextSeason(1)).catch((e: unknown) => e);
    http
      .expectOne(`${BASE}/leagues/1/next-season/`)
      .flush(...fail(409, 'manager_fired', 'You were fired.'));
    expect(((await result) as ApiError).errors[0].code).toBe('manager_fired');
  });
});
