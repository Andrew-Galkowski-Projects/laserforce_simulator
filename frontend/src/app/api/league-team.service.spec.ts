import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  TestRequest,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { convertToParamMap } from '@angular/router';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../environments/environment';
import {
  PagedStatsQueryConfig,
  parseStatsQuery,
  statsApiParams,
} from '../leagues/stats-shared/stats-query';
import { failure, pagedEnvelope, teamOptions } from '../leagues/testing/league-stats-fixtures';
import {
  teamHistory,
  teamHistoryPlayerRow,
  teamRoster,
  teamSchedule,
  withTeamOptions,
} from '../leagues/testing/league-team-fixtures';
import { ApiError } from './api-client';
import { TeamHistoryPlayersParams } from './league-team.models';
import { LeagueTeamService } from './league-team.service';

const BASE = environment.apiBaseUrl;

describe('LeagueTeamService (spec #428 F1)', () => {
  let teams: LeagueTeamService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    teams = TestBed.inject(LeagueTeamService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  function expectGet(path: string): TestRequest {
    const req = http.expectOne((r) => r.url === `${BASE}/leagues/4/teams/3/${path}`);
    expect(req.request.method).toBe('GET');
    return req;
  }

  function sent(req: TestRequest): Record<string, string> {
    const params = req.request.params;
    return Object.fromEntries(params.keys().map((k) => [k, params.get(k) ?? '']));
  }

  it('reads the roster with GET leagues/{id}/teams/{team_id}/roster/, keeping team_options', async () => {
    const result = firstValueFrom(teams.roster(4, 3));
    const req = expectGet('roster/');
    expect(sent(req)).toEqual({});
    req.flush(withTeamOptions(teamRoster()));
    expect(await result).toEqual({
      data: teamRoster(),
      meta: { team_options: teamOptions() },
    });
  });

  it('reads the Team schedule with GET leagues/{id}/teams/{team_id}/schedule/', async () => {
    const result = firstValueFrom(teams.schedule(4, 3));
    expectGet('schedule/').flush(withTeamOptions(teamSchedule()));
    const out = await result;
    expect(out.data).toEqual(teamSchedule());
    expect(out.meta.team_options).toEqual(teamOptions());
  });

  it('reads the Team history with GET leagues/{id}/teams/{team_id}/history/', async () => {
    const result = firstValueFrom(teams.history(4, 3));
    expectGet('history/').flush(withTeamOptions(teamHistory(), []));
    expect(await result).toEqual({ data: teamHistory(), meta: { team_options: [] } });
  });

  it('pages the history Players with ordering, page and page_size', async () => {
    const result = firstValueFrom(
      teams.historyPlayers(4, 3, { ordering: '-games_played', page: 2, page_size: 25 }),
    );
    const req = expectGet('history/players/');
    expect(sent(req)).toEqual({ ordering: '-games_played', page: '2', page_size: '25' });
    req.flush(pagedEnvelope([teamHistoryPlayerRow()], 26, { page: 2, total_pages: 2 }));
    const page = await result;
    expect(page.items).toEqual([teamHistoryPlayerRow()]);
    expect(page.meta.count).toBe(26);
  });

  it("takes the kit's paged statsApiParams as the history Players params", () => {
    const config: PagedStatsQueryConfig = {
      sortKeys: ['name', 'games_played'],
      defaultSort: 'name',
      defaultDir: 'asc',
      firstDir: 'desc',
      paged: true,
    };
    // A compile-time check: the kit's output is assignable to the service's params as is.
    const params: TeamHistoryPlayersParams = statsApiParams(
      parseStatsQuery(convertToParamMap({ per_page: '25' }), config),
      config,
    );
    expect(params).toEqual({ ordering: 'name', page: 1, page_size: 25 });
  });

  it('surfaces a stale team as an ApiError 404', async () => {
    const result = firstValueFrom(teams.roster(4, 3)).catch((e: unknown) => e);
    expectGet('roster/').flush(...failure(404, 'Not found.'));
    const err = (await result) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(404);
  });
});
