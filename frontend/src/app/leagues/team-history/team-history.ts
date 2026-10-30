import { Component, DestroyRef, WritableSignal, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed, toObservable } from '@angular/core/rxjs-interop';
import { MatButton } from '@angular/material/button';
import { ActivatedRoute, ParamMap, Params, Router, RouterLink } from '@angular/router';
import {
  EMPTY,
  Observable,
  Subject,
  catchError,
  combineLatest,
  distinctUntilChanged,
  map,
  shareReplay,
  startWith,
  switchMap,
  tap,
} from 'rxjs';
import { ApiError, Page, WithMeta } from '../../api/api-client';
import { TeamRef } from '../../api/analytics.models';
import { TeamOptionsMeta } from '../../api/league-stats.models';
import {
  TeamHistory,
  TeamHistoryPlayerRow,
  TeamHistoryPlayersParams,
  TeamHistorySeason,
} from '../../api/league-team.models';
import { LeagueTeamService } from '../../api/league-team.service';
import { apiMessage } from '../../shared/api-message';
import { GENERIC_ERROR_MESSAGE } from '../../shared/form-errors';
import { LeagueContext } from '../stats-shared/league-context';
import {
  PagedStatsQueryConfig,
  SortDir,
  StatsQuery,
  parseStatsQuery,
  sortArrow,
  recoverFrom,
  statsApiParams,
  toQueryParams,
  withPage,
  withPerPage,
  withSort,
  withTeam,
} from '../stats-shared/stats-query';
import { PerPagePicker, StatsPaginator } from '../stats-shared/stats-paginator';
import { PlayerCell } from '../stats-shared/player-cell';
import { resolveTeamParam } from '../stats-shared/team-param';

/** A Players column after Name: its sort key (the API's) and header. */
interface PlayerColumn {
  key: string;
  label: string;
  value: (row: TeamHistoryPlayerRow) => number | string;
}

const stat = (key: string, label: string): PlayerColumn => ({
  key,
  label,
  value: (row) => row.stats[key],
});

/** `_TH_PLAYERS_SORT_KEYS_DISPLAY` after Name, with `team_history.html`'s cells. */
const PLAYER_COLUMNS: readonly PlayerColumn[] = [
  { key: 'games_played', label: 'Games', value: (row) => row.games_played },
  stat('points_scored', 'Points'),
  stat('tags_made', 'Tags'),
  stat('times_tagged', 'Times tagged'),
  stat('missiles_landed', 'Missiles'),
  stat('resupplies_given', 'Resupplies'),
  stat('specials_used', 'Specials'),
  { key: 'last_season_year', label: 'Last season', value: (row) => row.last_season_year ?? '—' },
];

/** The Players table's sort keys (`TeamHistoryPlayersParamsForm`), default `name` asc. */
const PLAYERS_CONFIG: PagedStatsQueryConfig = {
  sortKeys: ['name', ...PLAYER_COLUMNS.map((c) => c.key)],
  defaultSort: 'name',
  defaultDir: 'asc',
  firstDir: 'asc',
  team: true,
  paged: true,
};

/** The Players request's params: the Team is in the path, so no `team_id`. */
function playersParams(query: StatsQuery): TeamHistoryPlayersParams {
  return statsApiParams(query, { ...PLAYERS_CONFIG, team: false });
}

/** The Seasons table's client-side sort keys (`team_history.html`'s three headers). */
const SEASONS_SORT_KEYS = ['year', 'wins', 'rank'] as const;
type SeasonsSortKey = (typeof SEASONS_SORT_KEYS)[number];

/** `?seasons_sort=&seasons_dir=`: default Year descending; a bad value takes its default. */
interface SeasonsSort {
  sort: SeasonsSortKey;
  dir: SortDir;
}

function parseSeasonsSort(params: ParamMap): SeasonsSort {
  const raw = params.get('seasons_sort');
  return {
    sort: SEASONS_SORT_KEYS.find((k) => k === raw) ?? 'year',
    dir: params.get('seasons_dir') === 'asc' ? 'asc' : 'desc',
  };
}

/**
 * `build_team_history`'s order: by the key (an unknown year / rank after every known one), then
 * Season id descending, the whole order reversed for `desc` (so unknowns lead a descending sort).
 */
function sortSeasons(rows: readonly TeamHistorySeason[], s: SeasonsSort): TeamHistorySeason[] {
  const key = (row: TeamHistorySeason): number[] => {
    const value = row[s.sort];
    return [value === null ? 1 : 0, value ?? 0, -row.season.id];
  };
  const sign = s.dir === 'desc' ? -1 : 1;
  return [...rows].sort((a, b) => {
    const ka = key(a);
    const kb = key(b);
    const i = ka.findIndex((v, n) => v !== kb[n]);
    return i === -1 ? 0 : sign * (ka[i] - kb[i]);
  });
}

/** What the screen shows for the current URL and nav. */
interface Target {
  leagueId: number;
  /** False until the League nav loads. */
  navLoaded: boolean;
  hasSeason: boolean;
  /** The resolved Team; null: no Team (or no Season). */
  team: number | null;
  /** The Players table's state (`?team=&sort=&dir=&page=&per_page=`). */
  query: StatsQuery;
  seasons: SeasonsSort;
}

/** One request's failure state. */
interface LoadState {
  notFound: WritableSignal<boolean>;
  error: WritableSignal<string | null>;
}

/**
 * `/leagues/:id/team/history?team=` (spec #428 Team History, `leagues/team_history.html`): a
 * franchise's Overall record, its Seasons (sorted here, `?seasons_sort=&seasons_dir=`) and the
 * Players who appeared for it (server-sorted and -paged, `?sort=&dir=&page=&per_page=`). The Team
 * is `resolveTeamParam`'s. I1: no request without a displayed Season or a Team. I2: a 404 on
 * either request is the "No team available…" state; another failure the error box with Retry.
 */
@Component({
  selector: 'app-league-team-history',
  imports: [MatButton, PerPagePicker, PlayerCell, RouterLink, StatsPaginator],
  templateUrl: './team-history.html',
  styleUrl: './team-history.scss',
})
export class LeagueTeamHistory {
  private readonly teams = inject(LeagueTeamService);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  protected readonly nav = inject(LeagueContext).nav;
  protected readonly columns = PLAYER_COLUMNS;

  protected readonly target = signal<Target | null>(null);
  protected readonly history = signal<TeamHistory | null>(null);
  protected readonly teamOptions = signal<TeamRef[]>([]);
  protected readonly players = signal<Page<TeamHistoryPlayerRow> | null>(null);

  private readonly historyLoad: LoadState = { notFound: signal(false), error: signal(null) };
  private readonly playersLoad: LoadState = { notFound: signal(false), error: signal(null) };
  /** A 404 on either request: the "No team available…" state. */
  protected readonly notFound = computed(
    () => this.historyLoad.notFound() || this.playersLoad.notFound(),
  );
  protected readonly loadError = computed(
    () => this.historyLoad.error() ?? this.playersLoad.error(),
  );

  /** The Seasons rows in the URL's order. */
  protected readonly seasonRows = computed(() => {
    const h = this.history();
    const t = this.target();
    return h && t ? sortSeasons(h.seasons, t.seasons) : [];
  });

  private readonly reloads = new Subject<void>();

  constructor() {
    const destroyRef = inject(DestroyRef);
    const id$ = (this.route.parent ?? this.route).paramMap.pipe(map((p) => Number(p.get('id'))));
    const target$: Observable<Target> = combineLatest([
      id$,
      toObservable(this.nav),
      this.route.queryParamMap,
    ]).pipe(
      map(([leagueId, nav, params]) => {
        const query = parseStatsQuery(params, PLAYERS_CONFIG);
        const hasSeason = !!nav?.displayed_season;
        return {
          leagueId,
          navLoaded: nav !== null,
          hasSeason,
          team: hasSeason ? resolveTeamParam(query.team, nav) : null,
          query,
          seasons: parseSeasonsSort(params),
        };
      }),
      tap((t) => this.target.set(t)),
      shareReplay({ bufferSize: 1, refCount: true }),
    );

    this.loadOn(
      target$,
      (t) => `${t.leagueId}/${t.team}`,
      (t) => this.teams.history(t.leagueId, t.team!),
      this.historyLoad,
      () => this.history.set(null),
    )
      .pipe(takeUntilDestroyed(destroyRef))
      .subscribe((loaded: WithMeta<TeamHistory, TeamOptionsMeta>) => {
        this.history.set(loaded.data);
        this.teamOptions.set(loaded.meta.team_options);
      });

    this.loadOn(
      target$,
      (t) => JSON.stringify([t.leagueId, t.team, playersParams(t.query)]),
      (t) => this.teams.historyPlayers(t.leagueId, t.team!, playersParams(t.query)),
      this.playersLoad,
      () => undefined,
      (t, error) => {
        const next = recoverFrom(t.query, error, PLAYERS_CONFIG);
        if (!next || JSON.stringify(next) === JSON.stringify(t.query)) return false;
        this.navigate(next, t.seasons, true);
        return true;
      },
    )
      .pipe(takeUntilDestroyed(destroyRef))
      .subscribe((loaded) => this.players.set(loaded));
  }

  protected retry(): void {
    this.reloads.next();
  }

  /** A Seasons header's query params: the active one flips, another starts ascending. */
  protected seasonsSortParams(key: SeasonsSortKey): Params {
    const t = this.target()!;
    const dir: SortDir =
      key === t.seasons.sort ? (t.seasons.dir === 'asc' ? 'desc' : 'asc') : 'asc';
    return this.queryParams(t.query, { sort: key, dir });
  }

  protected seasonsArrow(key: SeasonsSortKey): string {
    const s = this.target()!.seasons;
    return key === s.sort ? sortArrow(s.dir) : '';
  }

  /** A Players header's query params (`withSort`: back to page 1). */
  protected playersSortParams(key: string): Params {
    const t = this.target()!;
    return this.queryParams(withSort(t.query, key, PLAYERS_CONFIG), t.seasons);
  }

  protected playersArrow(key: string): string {
    const q = this.target()!.query;
    return key === q.sort ? sortArrow(q.dir) : '';
  }

  /** The Team picker: `?team=`, the Players back to page 1, both sorts kept. */
  protected pickTeam(event: Event): void {
    const t = this.target()!;
    const team = Number((event.target as HTMLSelectElement).value);
    this.navigate(withTeam(t.query, team), t.seasons);
  }

  protected pickPerPage(perPage: number): void {
    const t = this.target()!;
    this.navigate(withPerPage(t.query, perPage), t.seasons);
  }

  protected pickPage(page: number): void {
    const t = this.target()!;
    this.navigate(withPage(t.query, page), t.seasons);
  }

  /**
   * Sends `load` whenever `key` changes (a Team is resolved) or Retry is clicked; a new key
   * supersedes the request in flight. `recover` may handle a failure itself (return true).
   */
  private loadOn<R>(
    target$: Observable<Target>,
    key: (t: Target) => string,
    load: (t: Target) => Observable<R>,
    state: LoadState,
    reset: () => void,
    recover: (t: Target, error: unknown) => boolean = () => false,
  ): Observable<R> {
    const keyed$ = target$.pipe(
      map((t) => ({ t, key: t.team === null ? null : key(t) })),
      distinctUntilChanged((a, b) => a.key === b.key),
      tap(() => reset()),
    );
    return combineLatest([keyed$, this.reloads.pipe(startWith(undefined))]).pipe(
      tap(() => {
        state.notFound.set(false);
        state.error.set(null);
      }),
      switchMap(([{ t, key: k }]) => {
        if (k === null) return EMPTY;
        return load(t).pipe(
          catchError((error: unknown) => {
            if (recover(t, error)) return EMPTY;
            if (error instanceof ApiError && error.status === 404) state.notFound.set(true);
            else state.error.set(apiMessage(error, GENERIC_ERROR_MESSAGE));
            return EMPTY;
          }),
        );
      }),
    );
  }

  /** Both tables' state as query params (I3: neither sort resets the other), defaults left out. */
  private queryParams(players: StatsQuery, seasons: SeasonsSort): Params {
    const out = toQueryParams(players, PLAYERS_CONFIG);
    if (seasons.sort !== 'year' || seasons.dir !== 'desc') {
      out['seasons_sort'] = seasons.sort;
      out['seasons_dir'] = seasons.dir;
    }
    return out;
  }

  private navigate(players: StatsQuery, seasons: SeasonsSort, replaceUrl = false): void {
    void this.router.navigate([], {
      relativeTo: this.route,
      queryParams: this.queryParams(players, seasons),
      replaceUrl,
    });
  }
}
