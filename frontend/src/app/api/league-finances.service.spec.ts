import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  TestRequest,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../environments/environment';
import {
  leagueFinances,
  teamFinances,
  teamFinancesHeader,
  teamFinancesUpdate,
} from '../leagues/testing/league-finances-fixtures';
import { failure, teamOptions } from '../leagues/testing/league-stats-fixtures';
import { withTeamOptions } from '../leagues/testing/league-team-fixtures';
import { ApiError } from './api-client';
import { LeagueFinancesService } from './league-finances.service';

const BASE = environment.apiBaseUrl;

describe('LeagueFinancesService (spec #439)', () => {
  let finances: LeagueFinancesService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    finances = TestBed.inject(LeagueFinancesService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  function expectOne(path: string, method: string): TestRequest {
    const req = http.expectOne((r) => r.url === `${BASE}/${path}`);
    expect(req.request.method).toBe(method);
    return req;
  }

  it('reads every Team with GET leagues/{id}/finances/', async () => {
    const result = firstValueFrom(finances.league(4));
    const req = expectOne('leagues/4/finances/', 'GET');
    expect(req.request.params.keys()).toEqual([]);
    req.flush({ data: leagueFinances(), meta: {} });
    expect(await result).toEqual(leagueFinances());
  });

  it('reads one Team with GET leagues/{id}/teams/{team_id}/finances/, keeping team_options', async () => {
    const result = firstValueFrom(finances.team(4, 3));
    expectOne('leagues/4/teams/3/finances/', 'GET').flush(withTeamOptions(teamFinances()));
    expect(await result).toEqual({
      data: teamFinances(),
      meta: { team_options: teamOptions() },
    });
  });

  it('passes the finance-off header through as the data', async () => {
    const result = firstValueFrom(finances.team(4, 3));
    expectOne('leagues/4/teams/3/finances/', 'GET').flush(withTeamOptions(teamFinancesHeader()));
    expect((await result).data).toEqual({
      finance_enabled: false,
      team: { id: 3, name: 'Red Raiders' },
      editable: false,
    });
  });

  it('saves with PATCH leagues/{id}/teams/{team_id}/finances/, sending all six fields', async () => {
    const result = firstValueFrom(finances.updateTeam(4, 3, teamFinancesUpdate()));
    const req = expectOne('leagues/4/teams/3/finances/', 'PATCH');
    expect(req.request.body).toEqual({
      budget_scouting: 40,
      budget_coaching: 34,
      budget_facilities: 50,
      budget_health: 20,
      injury_policy: 'play_hurt',
      ticket_price: 12.5,
    });
    req.flush(withTeamOptions(teamFinances()));
    expect(await result).toEqual(teamFinances());
  });

  it('surfaces a stale Team as an ApiError 404', async () => {
    const result = firstValueFrom(finances.team(4, 99)).catch((e: unknown) => e);
    expectOne('leagues/4/teams/99/finances/', 'GET').flush(...failure(404, 'Not found.'));
    const err = (await result) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(404);
  });
});
