// League stats URL state (spec #417 I1, I2): pure functions, no Angular state.
//
// How a screen uses it (usually through `injectStatsScreen` in `stats-screen.ts`, which calls
// these for you):
// - Declare one `StatsQueryConfig`: the API sort keys, the default sort + direction, the
//   direction a non-active header starts in (`firstDir`), and which of Career / Team / rate /
//   paging the screen has.
// - URL keys: `?season=<id>|career &team=<id> &rate= &sort=<key> &dir=asc|desc &page= &per_page=`.
//   `parseStatsQuery` reads them leniently: a bad or missing value takes its default.
// - `statsApiParams` turns the state into the service's params: `ordering` (`key` / `-key`,
//   always sent), the Team as `team_id`, and `page` + `page_size` (always sent) when paged.
// - `withSort` / `withSeason` / `withTeam` / `withRate` / `withPerPage` / `withPage` give the
//   next state: each goes back to page 1 (but `withPage`), and a Season change clears the Team.
//   `toQueryParams` writes a state back as query params, leaving defaults out.
// - `recoverFrom` is I2: the state to reload after a 400 naming a param (that param dropped) or
//   a 404 past page 1 (page 1); null means show the error box.
import { ParamMap, Params } from '@angular/router';
import { ApiError, ListParams } from '../../api/api-client';
import { SeasonParam, StatRate } from '../../api/league-stats.models';

/** The per-page choices; the first is the default. */
export const STATS_PAGE_SIZES = [10, 25, 50, 100] as const;

export type SortDir = 'asc' | 'desc';

export interface StatsQueryConfig {
  /** The API's sort keys (the `ordering` values without the `-`). */
  sortKeys: readonly string[];
  defaultSort: string;
  defaultDir: SortDir;
  /** The direction a header that is not the active sort starts in. */
  firstDir: SortDir;
  /** Whether `?season=career` is allowed (default true; Playoffs: false). */
  career?: boolean;
  /** Whether the screen has a Team picker (`?team=`, sent as `team_id`). */
  team?: boolean;
  /** The `?rate=` choices, the first the default; omit when the screen has no rate. */
  rates?: readonly string[];
  /** Whether the screen is paged (`?page=&per_page=`, sent as `page` + `page_size`). */
  paged?: boolean;
}

/** One stats screen's view state, as read from the URL. */
export interface StatsQuery {
  /** null: the displayed Season (the server's default). */
  season: SeasonParam | null;
  /** null: every Team. Always null on a screen without a Team picker. */
  team: number | null;
  /** null on a screen without a rate. */
  rate: string | null;
  sort: string;
  dir: SortDir;
  /** 1-based. */
  page: number;
  perPage: number;
}

/** The API params `statsApiParams` builds; assignable to every stats service method's params. */
export interface StatsApiParams extends ListParams {
  season?: SeasonParam;
  team_id?: number;
  /** One of `config.rates` (Player Stats: a `StatRate`). */
  rate?: StatRate;
  ordering: string;
  page?: number;
  page_size?: number;
}

const POSITIVE_INT = /^[1-9]\d*$/;

function positiveInt(raw: string | null): number | null {
  return raw !== null && POSITIVE_INT.test(raw) ? Number(raw) : null;
}

function defaultRate(config: StatsQueryConfig): string | null {
  return config.rates?.[0] ?? null;
}

/** Reads the view state from the query string; every bad or missing value takes its default. */
export function parseStatsQuery(params: ParamMap, config: StatsQueryConfig): StatsQuery {
  const rawSeason = params.get('season');
  const season: SeasonParam | null =
    rawSeason === 'career' && config.career !== false ? 'career' : positiveInt(rawSeason);
  const rawRate = params.get('rate');
  const rate = rawRate !== null && config.rates?.includes(rawRate) ? rawRate : defaultRate(config);

  const rawSort = params.get('sort');
  const rawDir = params.get('dir');
  let sort = config.defaultSort;
  let dir = config.defaultDir;
  if (rawSort !== null && config.sortKeys.includes(rawSort)) {
    sort = rawSort;
    if (rawDir === 'asc' || rawDir === 'desc') dir = rawDir;
    else if (sort !== config.defaultSort) dir = config.firstDir;
  }

  const perPage = Number(params.get('per_page'));
  return {
    season,
    team: config.team ? positiveInt(params.get('team')) : null,
    rate,
    sort,
    dir,
    page: positiveInt(params.get('page')) ?? 1,
    perPage: (STATS_PAGE_SIZES as readonly number[]).includes(perPage)
      ? perPage
      : STATS_PAGE_SIZES[0],
  };
}

/** The service params for `q`: undefined keys are not sent. */
export function statsApiParams(q: StatsQuery, config: StatsQueryConfig): StatsApiParams {
  const out: StatsApiParams = { ordering: q.dir === 'desc' ? `-${q.sort}` : q.sort };
  if (q.season !== null) out.season = q.season;
  if (config.team && q.team !== null) out.team_id = q.team;
  if (config.rates && q.rate !== null) out.rate = q.rate as StatRate;
  if (config.paged) {
    out.page = q.page;
    out.page_size = q.perPage;
  }
  return out;
}

/** `q` as query params for `router.navigate`, defaults left out. */
export function toQueryParams(q: StatsQuery, config: StatsQueryConfig): Params {
  const out: Params = {};
  if (q.season !== null) out['season'] = q.season;
  if (q.team !== null) out['team'] = q.team;
  if (q.rate !== null && q.rate !== defaultRate(config)) out['rate'] = q.rate;
  if (q.sort !== config.defaultSort || q.dir !== config.defaultDir) {
    out['sort'] = q.sort;
    out['dir'] = q.dir;
  }
  if (q.page !== 1) out['page'] = q.page;
  if (q.perPage !== STATS_PAGE_SIZES[0]) out['per_page'] = q.perPage;
  return out;
}

/** A header click: the active column toggles, another starts in `firstDir`. */
export function withSort(q: StatsQuery, key: string, config: StatsQueryConfig): StatsQuery {
  const dir: SortDir = key === q.sort ? (q.dir === 'asc' ? 'desc' : 'asc') : config.firstDir;
  return { ...q, sort: key, dir, page: 1 };
}

/** A Season change clears the Team (the new Season may not have it). */
export function withSeason(q: StatsQuery, season: SeasonParam | null): StatsQuery {
  return { ...q, season, team: null, page: 1 };
}

export function withTeam(q: StatsQuery, team: number | null): StatsQuery {
  return { ...q, team, page: 1 };
}

export function withRate(q: StatsQuery, rate: string): StatsQuery {
  return { ...q, rate, page: 1 };
}

export function withPerPage(q: StatsQuery, perPage: number): StatsQuery {
  return { ...q, perPage, page: 1 };
}

export function withPage(q: StatsQuery, page: number): StatsQuery {
  return { ...q, page };
}

/**
 * I2: the state to reload after `error`, or null to show the error box. A 400 whose `field` is
 * `season`, `team_id`, `rate` or `ordering` drops that param (back to page 1); a 404 past page 1
 * goes to page 1.
 */
export function recoverFrom(
  q: StatsQuery,
  error: unknown,
  config: StatsQueryConfig,
): StatsQuery | null {
  if (!(error instanceof ApiError)) return null;
  if (error.status === 404) return q.page > 1 ? { ...q, page: 1 } : null;
  if (error.status !== 400) return null;
  const fields = new Set(error.errors.map((e) => e.field));
  if (fields.has('season')) return { ...q, season: null, page: 1 };
  if (fields.has('team_id')) return { ...q, team: null, page: 1 };
  if (fields.has('rate')) return { ...q, rate: defaultRate(config), page: 1 };
  if (fields.has('ordering')) {
    return { ...q, sort: config.defaultSort, dir: config.defaultDir, page: 1 };
  }
  return null;
}
