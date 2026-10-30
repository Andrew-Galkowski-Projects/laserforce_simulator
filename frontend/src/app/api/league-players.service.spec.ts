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
import { leaguePlayer, watchResult } from '../leagues/testing/league-players-fixtures';
import {
  failure,
  pagedEnvelope,
  playerRatingRow,
  playerStatRow,
  seasonScope,
} from '../leagues/testing/league-stats-fixtures';
import { ApiError } from './api-client';
import { FreeAgentsParams, WatchListParams } from './league-players.models';
import { StatRate } from './league-stats.models';
import { LeaguePlayersService } from './league-players.service';

const BASE = environment.apiBaseUrl;

describe('LeaguePlayersService (spec #428 F2)', () => {
  let players: LeaguePlayersService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    players = TestBed.inject(LeaguePlayersService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  function expectOne(method: string, path: string): TestRequest {
    const req = http.expectOne((r) => r.url === `${BASE}/leagues/4/${path}`);
    expect(req.request.method).toBe(method);
    return req;
  }

  function sent(req: TestRequest): Record<string, string> {
    const params = req.request.params;
    return Object.fromEntries(params.keys().map((k) => [k, params.get(k) ?? '']));
  }

  it('pages the Free Agents with GET leagues/{id}/free-agents/', async () => {
    const result = firstValueFrom(
      players.freeAgents(4, { ordering: '-overall_rating', page: 2, page_size: 25 }),
    );
    const req = expectOne('GET', 'free-agents/');
    expect(sent(req)).toEqual({ ordering: '-overall_rating', page: '2', page_size: '25' });
    req.flush(pagedEnvelope([playerRatingRow()], 26, { page: 2, total_pages: 2 }));
    const page = await result;
    expect(page.items).toEqual([playerRatingRow()]);
    expect(page.meta.count).toBe(26);
  });

  it('pages the Watch List with GET leagues/{id}/watch-list/, keeping the season scope', async () => {
    const result = firstValueFrom(
      players.watchList(4, {
        season: 'career',
        rate: 'per_game',
        ordering: '-mvp',
        page: 1,
        page_size: 10,
      }),
    );
    const req = expectOne('GET', 'watch-list/');
    expect(sent(req)).toEqual({
      season: 'career',
      rate: 'per_game',
      ordering: '-mvp',
      page: '1',
      page_size: '10',
    });
    req.flush(
      pagedEnvelope([playerStatRow({ watched: true })], 1, { season_scope: seasonScope() }),
    );
    const page = await result;
    expect(page.items).toEqual([playerStatRow({ watched: true })]);
    expect(page.meta.season_scope).toEqual(seasonScope());
  });

  it('reads the League player page with GET leagues/{id}/players/{player_id}/', async () => {
    const result = firstValueFrom(players.player(4, 25));
    expectOne('GET', 'players/25/').flush({
      data: leaguePlayer({ can_link_self_insert: true }),
      meta: {},
    });
    const out = await result;
    expect(out).toEqual(leaguePlayer({ can_link_self_insert: true }));
    expect(out.can_link_self_insert).toBe(true);
  });

  it('watches a Player with PUT leagues/{id}/watch-list/{player_id}/', async () => {
    const result = firstValueFrom(players.watch(4, 25));
    expectOne('PUT', 'watch-list/25/').flush({ data: watchResult(true), meta: {} });
    expect(await result).toEqual(watchResult(true));
  });

  it('unwatches a Player with DELETE leagues/{id}/watch-list/{player_id}/', async () => {
    const result = firstValueFrom(players.unwatch(4, 25));
    expectOne('DELETE', 'watch-list/25/').flush({ data: watchResult(false), meta: {} });
    await result;
  });

  it('clears the watch list with DELETE leagues/{id}/watch-list/', async () => {
    const result = firstValueFrom(players.clearWatchList(4));
    expectOne('DELETE', 'watch-list/').flush({ data: { removed: 3 }, meta: {} });
    await result;
  });

  it('surfaces a failed watch as an ApiError with the server message', async () => {
    const result = firstValueFrom(players.watch(4, 25)).catch((e: unknown) => e);
    expectOne('PUT', 'watch-list/25/').flush(...failure(400, 'The watch list is full.'));
    const err = (await result) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.message).toBe('The watch list is full.');
  });

  it("takes the kit's paged statsApiParams as the Free Agents and Watch List params", () => {
    const freeAgents: PagedStatsQueryConfig = {
      sortKeys: ['team', 'overall_rating'],
      defaultSort: 'team',
      defaultDir: 'asc',
      firstDir: 'asc',
      paged: true,
    };
    const watchList: PagedStatsQueryConfig<StatRate> = {
      sortKeys: ['points_scored', 'mvp'],
      defaultSort: 'points_scored',
      defaultDir: 'desc',
      firstDir: 'desc',
      rates: ['total', 'per_game', 'per_10'],
      paged: true,
    };
    const none = convertToParamMap({});
    // Compile-time checks: the kit's output is assignable to the service's params as is.
    const fa: FreeAgentsParams = statsApiParams(parseStatsQuery(none, freeAgents), freeAgents);
    const wl: WatchListParams = statsApiParams(parseStatsQuery(none, watchList), watchList);
    expect(fa).toEqual({ ordering: 'team', page: 1, page_size: 10 });
    expect(wl).toEqual({ rate: 'total', ordering: '-points_scored', page: 1, page_size: 10 });
  });
});
