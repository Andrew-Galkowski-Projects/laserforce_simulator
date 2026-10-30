import { convertToParamMap } from '@angular/router';
import { ApiError } from '../../api/api-client';
import { PlayerStatsParams, StatRate } from '../../api/league-stats.models';
import {
  PagedStatsApiParams,
  PagedStatsQueryConfig,
  StatsQuery,
  StatsQueryConfig,
  parseStatsQuery,
  recoverFrom,
  sortArrow,
  statsApiParams,
  toQueryParams,
  withPage,
  withPerPage,
  withRate,
  withSeason,
  withSort,
  withTeam,
} from './stats-query';

/** A Player-Stats-like screen: every feature on. */
const FULL: StatsQueryConfig = {
  sortKeys: ['name', 'team', 'points_scored', 'mvp'],
  defaultSort: 'points_scored',
  defaultDir: 'desc',
  firstDir: 'desc',
  rates: ['total', 'per_game', 'per_10'],
  team: true,
  paged: true,
};

/** FULL as Player Stats declares it: paged, its rates the `StatRate`s. */
const PAGED: PagedStatsQueryConfig<StatRate> = {
  ...FULL,
  rates: ['total', 'per_game', 'per_10'],
  paged: true,
};

/** A Team-Stats-like screen: Season and sort only. */
const PLAIN: StatsQueryConfig = {
  sortKeys: ['team', 'avg_margin'],
  defaultSort: 'team',
  defaultDir: 'asc',
  firstDir: 'asc',
};

const parse = (params: Record<string, string>, config = FULL): StatsQuery =>
  parseStatsQuery(convertToParamMap(params), config);

const DEFAULTS: StatsQuery = {
  season: null,
  team: null,
  rate: 'total',
  sort: 'points_scored',
  dir: 'desc',
  page: 1,
  perPage: 10,
};

function apiError(status: number, field: string | null): ApiError {
  return new ApiError(status, [{ code: 'x', message: 'Bad.', field }]);
}

describe('parseStatsQuery (I1, I2: lenient)', () => {
  it('reads the defaults from an empty query', () => {
    expect(parse({})).toEqual(DEFAULTS);
  });

  it('reads every key', () => {
    expect(
      parse({
        season: '10',
        team: '3',
        rate: 'per_10',
        sort: 'mvp',
        dir: 'asc',
        page: '4',
        per_page: '50',
      }),
    ).toEqual({
      season: 10,
      team: 3,
      rate: 'per_10',
      sort: 'mvp',
      dir: 'asc',
      page: 4,
      perPage: 50,
    });
  });

  it('reads season=career when Career is offered, and drops it when not', () => {
    expect(parse({ season: 'career' }).season).toBe('career');
    expect(parse({ season: 'career' }, { ...FULL, career: false }).season).toBeNull();
  });

  it.each([
    ['season', 'x7'],
    ['season', '0'],
    ['team', '-3'],
    ['team', 'red'],
    ['rate', 'per_hour'],
    ['page', '0'],
    ['page', '2.5'],
    ['per_page', '30'],
  ])('falls back to the default on %s=%s', (key, value) => {
    expect(parse({ [key]: value })).toEqual(DEFAULTS);
  });

  it('takes the default sort and its direction for an unknown sort key', () => {
    expect(parse({ sort: 'bogus', dir: 'asc' })).toEqual(DEFAULTS);
  });

  it('gives a known sort without a dir the direction a new header starts in', () => {
    expect(parse({ sort: 'avg_margin' }, PLAIN).dir).toBe('asc');
    expect(parse({ sort: 'mvp' }).dir).toBe('desc');
    expect(parse({ sort: 'mvp', dir: 'sideways' }).dir).toBe('desc');
  });

  it('ignores team and rate on a screen without them', () => {
    const q = parse({ team: '3', rate: 'per_10' }, PLAIN);
    expect(q.team).toBeNull();
    expect(q.rate).toBeNull();
  });
});

describe('statsApiParams', () => {
  it('sends ordering as key or -key, page and page_size always on a paged screen', () => {
    expect(statsApiParams(DEFAULTS, FULL)).toEqual({
      rate: 'total',
      ordering: '-points_scored',
      page: 1,
      page_size: 10,
    });
  });

  it('sends the team as team_id, the season as given', () => {
    const q = {
      ...DEFAULTS,
      season: 'career' as const,
      team: 3,
      sort: 'name',
      dir: 'asc' as const,
    };
    expect(statsApiParams(q, FULL)).toEqual({
      season: 'career',
      team_id: 3,
      rate: 'total',
      ordering: 'name',
      page: 1,
      page_size: 10,
    });
  });

  it('sends only season and ordering on an unpaged screen', () => {
    const q = parse({ season: '11', sort: 'avg_margin', dir: 'desc' }, PLAIN);
    expect(statsApiParams(q, PLAIN)).toEqual({ season: 11, ordering: '-avg_margin' });
  });

  it('gives a PagedStatsQueryConfig params with page and page_size required', () => {
    const q = parseStatsQuery(convertToParamMap({ page: '3', per_page: '25' }), PAGED);
    // Typed as `PagedStatsApiParams<StatRate>`: assignable to a paged service's params as is.
    const params: PagedStatsApiParams<StatRate> = statsApiParams(q, PAGED);
    const playerStats: PlayerStatsParams = params;
    expect(playerStats).toEqual({
      rate: 'total',
      ordering: '-points_scored',
      page: 3,
      page_size: 25,
    });
  });

  it('reads the rate as one of config.rates, typed as their value type', () => {
    const rate: StatRate | null = parseStatsQuery(
      convertToParamMap({ rate: 'per_game' }),
      PAGED,
    ).rate;
    expect(rate).toBe('per_game');
    expect(parseStatsQuery(convertToParamMap({ rate: 'per_hour' }), PAGED).rate).toBe('total');
  });
});

describe('sortArrow', () => {
  it('points up for ascending and down for descending', () => {
    expect(sortArrow('asc')).toBe(' ↑');
    expect(sortArrow('desc')).toBe(' ↓');
  });
});

describe('toQueryParams', () => {
  it('writes nothing for the defaults', () => {
    expect(toQueryParams(DEFAULTS, FULL)).toEqual({});
  });

  it('writes every key away from its default, and round-trips through parse', () => {
    const q: StatsQuery = {
      season: 10,
      team: 3,
      rate: 'per_game',
      sort: 'name',
      dir: 'asc',
      page: 2,
      perPage: 25,
    };
    const params = toQueryParams(q, FULL);
    expect(params).toEqual({
      season: 10,
      team: 3,
      rate: 'per_game',
      sort: 'name',
      dir: 'asc',
      page: 2,
      per_page: 25,
    });
    expect(
      parse(Object.fromEntries(Object.entries(params).map(([k, v]) => [k, String(v)]))),
    ).toEqual(q);
  });

  it('writes sort and dir together when only the direction differs', () => {
    expect(toQueryParams({ ...DEFAULTS, dir: 'asc' }, FULL)).toEqual({
      sort: 'points_scored',
      dir: 'asc',
    });
  });
});

describe('changes (I1: every one goes back to page 1)', () => {
  const deep: StatsQuery = { ...DEFAULTS, season: 10, team: 3, page: 3 };

  it('a new sort column starts in firstDir', () => {
    expect(withSort(deep, 'mvp', FULL)).toEqual({ ...deep, sort: 'mvp', dir: 'desc', page: 1 });
    expect(withSort(parse({}, PLAIN), 'avg_margin', PLAIN)).toMatchObject({
      sort: 'avg_margin',
      dir: 'asc',
    });
  });

  it('the active sort column toggles its direction', () => {
    expect(withSort(deep, 'points_scored', FULL)).toMatchObject({ dir: 'asc', page: 1 });
    expect(withSort({ ...deep, dir: 'asc' }, 'points_scored', FULL).dir).toBe('desc');
  });

  it('a Season change clears the Team', () => {
    expect(withSeason(deep, 'career')).toEqual({ ...deep, season: 'career', team: null, page: 1 });
    expect(withSeason(deep, null)).toEqual({ ...deep, season: null, team: null, page: 1 });
  });

  it('a Team change keeps the Season', () => {
    expect(withTeam(deep, 5)).toEqual({ ...deep, team: 5, page: 1 });
    expect(withTeam(deep, null).team).toBeNull();
  });

  it('a rate or per-page change resets the page', () => {
    expect(withRate(deep, 'per_10')).toEqual({ ...deep, rate: 'per_10', page: 1 });
    expect(withPerPage(deep, 50)).toEqual({ ...deep, perPage: 50, page: 1 });
  });

  it('a page change keeps everything else', () => {
    expect(withPage(deep, 4)).toEqual({ ...deep, page: 4 });
  });
});

describe('recoverFrom (I2)', () => {
  const deep: StatsQuery = {
    season: 10,
    team: 3,
    rate: 'per_10',
    sort: 'mvp',
    dir: 'asc',
    page: 2,
    perPage: 25,
  };

  it('drops season on a 400 naming it', () => {
    expect(recoverFrom(deep, apiError(400, 'season'), FULL)).toEqual({
      ...deep,
      season: null,
      page: 1,
    });
  });

  it('drops the team on a 400 naming team_id', () => {
    expect(recoverFrom(deep, apiError(400, 'team_id'), FULL)).toEqual({
      ...deep,
      team: null,
      page: 1,
    });
  });

  it('drops rate and ordering on a 400 naming them', () => {
    expect(recoverFrom(deep, apiError(400, 'rate'), FULL)).toMatchObject({ rate: 'total' });
    expect(recoverFrom(deep, apiError(400, 'ordering'), FULL)).toMatchObject({
      sort: 'points_scored',
      dir: 'desc',
    });
  });

  it('goes back to page 1 on a 404 past page 1', () => {
    expect(recoverFrom(deep, apiError(404, null), FULL)).toEqual({ ...deep, page: 1 });
  });

  it.each([
    ['a 404 on page 1', { ...deep, page: 1 }, apiError(404, null)],
    ['a 400 on another field', deep, apiError(400, 'page_size')],
    ['a 400 with no field', deep, apiError(400, null)],
    ['a 500', deep, apiError(500, 'season')],
  ])('gives up (null) on %s', (_label, q, error) => {
    expect(recoverFrom(q, error, FULL)).toBeNull();
  });

  it('gives up on a non-API error', () => {
    expect(recoverFrom(deep, new Error('boom'), FULL)).toBeNull();
  });
});
