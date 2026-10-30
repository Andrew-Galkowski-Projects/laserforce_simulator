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
  badParam,
  gameLogRow,
  leaderBoards,
  pagedEnvelope,
  playerRatingRow,
  playerStatRow,
  playoffsScreen,
  powerRankingRow,
  scopedEnvelope,
  seasonScope,
  statisticalFeats,
  teamOptions,
  teamStatRow,
} from '../leagues/testing/league-stats-fixtures';
import { ApiError } from './api-client';
import { LeagueStatsService } from './league-stats.service';

const BASE = environment.apiBaseUrl;

describe('LeagueStatsService (spec #417)', () => {
  let stats: LeagueStatsService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    stats = TestBed.inject(LeagueStatsService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  function expectGet(path: string): TestRequest {
    const req = http.expectOne((r) => r.url === `${BASE}/leagues/4/${path}`);
    expect(req.request.method).toBe('GET');
    return req;
  }

  /** Every param the request sent, as strings. */
  function sent(req: TestRequest): Record<string, string> {
    const params = req.request.params;
    return Object.fromEntries(params.keys().map((k) => [k, params.get(k) ?? '']));
  }

  it('reads the leader boards with GET leagues/{id}/leaders/ and keeps the scope', async () => {
    const result = firstValueFrom(stats.leaders(4, { season: 'career' }));
    const req = expectGet('leaders/');
    expect(sent(req)).toEqual({ season: 'career' });
    req.flush(scopedEnvelope(leaderBoards()));
    expect(await result).toEqual({
      data: leaderBoards(),
      meta: { season_scope: seasonScope() },
    });
  });

  it('pages Player Stats with every param, page_size included', async () => {
    const result = firstValueFrom(
      stats.playerStats(4, {
        season: 10,
        rate: 'per_game',
        team_id: 3,
        ordering: '-mvp',
        page: 2,
        page_size: 25,
      }),
    );
    const req = expectGet('player-stats/');
    expect(sent(req)).toEqual({
      season: '10',
      rate: 'per_game',
      team_id: '3',
      ordering: '-mvp',
      page: '2',
      page_size: '25',
    });
    const body = pagedEnvelope([playerStatRow()], 26, {
      page: 2,
      page_size: 25,
      total_pages: 2,
      season_scope: seasonScope(),
      team_options: teamOptions(),
    });
    req.flush(body);
    const page = await result;
    expect(page.items).toEqual([playerStatRow()]);
    expect(page.meta.team_options).toEqual(teamOptions());
    expect(page.meta.season_scope).toEqual(seasonScope());
    expect(page.meta.total_pages).toBe(2);
  });

  it('sends page_size on Player Stats even with nothing else set', async () => {
    const result = firstValueFrom(stats.playerStats(4, { page: 1, page_size: 10 }));
    const req = expectGet('player-stats/');
    expect(sent(req)).toEqual({ page: '1', page_size: '10' });
    req.flush(pagedEnvelope([], 0, { season_scope: seasonScope(), team_options: [] }));
    await result;
  });

  it('pages Player Ratings with team_id and ordering', async () => {
    const result = firstValueFrom(
      stats.playerRatings(4, { team_id: 5, ordering: 'team', page: 1, page_size: 10 }),
    );
    const req = expectGet('player-ratings/');
    expect(sent(req)).toEqual({ team_id: '5', ordering: 'team', page: '1', page_size: '10' });
    req.flush(pagedEnvelope([playerRatingRow()], 1, { team_options: teamOptions() }));
    const page = await result;
    expect(page.items).toEqual([playerRatingRow()]);
    expect(page.meta.team_options).toEqual(teamOptions());
  });

  it('reads Team Stats with GET leagues/{id}/team-stats/', async () => {
    const result = firstValueFrom(stats.teamStats(4, { season: 11, ordering: '-avg_margin' }));
    const req = expectGet('team-stats/');
    expect(sent(req)).toEqual({ season: '11', ordering: '-avg_margin' });
    req.flush(scopedEnvelope([teamStatRow()]));
    expect(await result).toEqual({
      data: [teamStatRow()],
      meta: { season_scope: seasonScope() },
    });
  });

  it('sends no params for Team Stats left at its defaults', async () => {
    const result = firstValueFrom(stats.teamStats(4, {}));
    const req = expectGet('team-stats/');
    expect(sent(req)).toEqual({});
    req.flush(scopedEnvelope([]));
    await result;
  });

  it('pages Statistical Feats, keeping data whole and meta with the page keys', async () => {
    const result = firstValueFrom(
      stats.statisticalFeats(4, {
        season: 'career',
        team_id: 3,
        ordering: '-round',
        page: 1,
        page_size: 50,
      }),
    );
    const req = expectGet('statistical-feats/');
    expect(sent(req)).toEqual({
      season: 'career',
      team_id: '3',
      ordering: '-round',
      page: '1',
      page_size: '50',
    });
    req.flush(
      pagedEnvelope(statisticalFeats(), 1, {
        page_size: 50,
        season_scope: seasonScope(),
        team_options: teamOptions(),
      }),
    );
    const out = await result;
    expect(out.data).toEqual(statisticalFeats());
    expect(out.meta.count).toBe(1);
    expect(out.meta.team_options).toEqual(teamOptions());
  });

  it('reads the Game Log with season, team_id and ordering', async () => {
    const result = firstValueFrom(
      stats.gameLog(4, { season: 11, team_id: 5, ordering: '-date_played' }),
    );
    const req = expectGet('game-log/');
    expect(sent(req)).toEqual({ season: '11', team_id: '5', ordering: '-date_played' });
    req.flush(scopedEnvelope([gameLogRow()], { team_options: teamOptions() }));
    const out = await result;
    expect(out.data).toEqual([gameLogRow()]);
    expect(out.meta.team_options).toEqual(teamOptions());
  });

  it('reads Power Rankings with GET leagues/{id}/power-rankings/', async () => {
    const result = firstValueFrom(stats.powerRankings(4, { ordering: 'rank' }));
    const req = expectGet('power-rankings/');
    expect(sent(req)).toEqual({ ordering: 'rank' });
    req.flush(scopedEnvelope([powerRankingRow()]));
    expect((await result).data).toEqual([powerRankingRow()]);
  });

  it('reads Playoffs with GET leagues/{id}/playoffs/', async () => {
    const result = firstValueFrom(stats.playoffs(4, { season: 10 }));
    const req = expectGet('playoffs/');
    expect(sent(req)).toEqual({ season: '10' });
    req.flush(scopedEnvelope(playoffsScreen()));
    expect((await result).data).toEqual(playoffsScreen());
  });

  it('surfaces a bad param as an ApiError 400 naming the field', async () => {
    const result = firstValueFrom(stats.teamStats(4, { season: 99 })).catch((e: unknown) => e);
    expectGet('team-stats/').flush(...badParam('season'));
    const err = (await result) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(400);
    expect(err.errors[0].field).toBe('season');
  });
});
