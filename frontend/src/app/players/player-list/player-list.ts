import { Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, ParamMap, Params, Router, RouterLink } from '@angular/router';
import { EMPTY, catchError, map, switchMap, tap } from 'rxjs';
import { ApiError, Page } from '../../api/api-client';
import { Player, PlayerStats } from '../../api/players.models';
import { PlayersService } from '../../api/players.service';
import { AuthService } from '../../auth/auth.service';
import { apiMessage } from '../../shared/api-message';
import { GENERIC_ERROR_MESSAGE } from '../../shared/form-errors';

type Dir = 'asc' | 'desc';

/** A column: its header, its sort key (`null`: not sortable) and its cell text. */
interface Column {
  label: string;
  sort: string | null;
  stat?: keyof PlayerStats;
}

/** A Stat column: the URL / API sort key is the field name in lower case. */
function stat(label: string, key: keyof PlayerStats): Column {
  return { label, sort: key.toLowerCase(), stat: key };
}

/**
 * Django's `_SORT_KEYS_DISPLAY` (`teams/views.py`): 23 columns. Preferred Roles is not sortable
 * here (the API cannot order a JSON list: a recorded deviation, #372).
 */
const COLUMNS: readonly Column[] = [
  { label: 'Name', sort: 'name' },
  { label: 'Team', sort: 'team' },
  { label: 'Preferred Roles', sort: null },
  { label: 'Overall', sort: 'overall_rating' },
  stat('Player Aware', 'player_awareness'),
  stat('Game Aware', 'game_awareness'),
  stat('Resource Aware', 'resource_awareness'),
  stat('Decision', 'decision_making'),
  stat('Positioning', 'positioning'),
  stat('Stamina', 'stamina'),
  stat('Speed', 'speed'),
  stat('Flexibility', 'flexibility'),
  stat('Adaptability', 'adaptability'),
  stat('Communication', 'communication'),
  stat('Teamwork', 'teamwork'),
  stat('Offensive Syn', 'Offensive_synergy'),
  stat('Defensive Syn', 'defensive_synergy'),
  stat('Midfield Syn', 'midfield_synergy'),
  stat('Resupply Syn', 'resupply_synergy'),
  stat('Resupply Eff', 'resupply_efficiency'),
  stat('Accuracy', 'accuracy'),
  stat('Survival', 'survival'),
  stat('Special Usage', 'special_usage'),
];

const SORT_KEYS = new Set(COLUMNS.flatMap((c) => (c.sort ? [c.sort] : [])));
const PAGE_SIZES = [10, 25, 50, 100] as const;

/** The list's view state, read from `?sort=&dir=&page=&per_page=`. */
interface ViewState {
  sort: string;
  dir: Dir;
  page: number;
  perPage: number;
}

/** Django's lenient parsing: an unknown or missing value takes its default. */
function parseState(params: ParamMap): ViewState {
  const sort = params.get('sort') ?? '';
  const dir = params.get('dir');
  const page = params.get('page') ?? '';
  const perPage = Number(params.get('per_page'));
  return {
    sort: SORT_KEYS.has(sort) ? sort : 'team',
    dir: dir === 'desc' ? 'desc' : 'asc',
    page: /^[1-9]\d*$/.test(page) ? Number(page) : 1,
    perPage: (PAGE_SIZES as readonly number[]).includes(perPage) ? perPage : 10,
  };
}

/**
 * `/players` (ANG-06-8a): every Player the Account can see (Free Agents included), server-sorted
 * and server-paged, with the view state in the URL.
 */
@Component({
  imports: [RouterLink],
  selector: 'app-player-list',
  styleUrl: './player-list.scss',
  templateUrl: './player-list.html',
})
export class PlayerList {
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly auth = inject(AuthService);

  protected readonly columns = COLUMNS;
  protected readonly pageSizes = PAGE_SIZES;
  protected readonly state = signal<ViewState>(parseState(this.route.snapshot.queryParamMap));
  protected readonly result = signal<Page<Player> | null>(null);
  protected readonly loadError = signal<string | null>(null);
  protected readonly selfId = computed(() => this.auth.currentUser()?.self_insert?.id ?? null);

  constructor() {
    const players = inject(PlayersService);
    this.route.queryParamMap
      .pipe(
        map(parseState),
        tap((s) => this.state.set(s)),
        switchMap((s) =>
          players
            .list({
              page: s.page,
              page_size: s.perPage,
              ordering: s.dir === 'desc' ? `-${s.sort}` : s.sort,
            })
            .pipe(
              catchError((error: unknown) => {
                this.failed(s, error);
                return EMPTY;
              }),
            ),
        ),
        takeUntilDestroyed(inject(DestroyRef)),
      )
      .subscribe((page) => {
        this.loadError.set(null);
        this.result.set(page);
      });
  }

  /** A header's link: the other direction on the active column, ascending on any other. */
  protected sortParams(column: Column): Params {
    const s = this.state();
    const dir: Dir = column.sort === s.sort && s.dir === 'asc' ? 'desc' : 'asc';
    return { sort: column.sort, dir, per_page: s.perPage };
  }

  protected pageParams(page: number): Params {
    const s = this.state();
    return { sort: s.sort, dir: s.dir, per_page: s.perPage, page };
  }

  protected arrow(column: Column): string {
    const s = this.state();
    if (column.sort !== s.sort) return '';
    return s.dir === 'asc' ? ' ↑' : ' ↓';
  }

  protected cell(p: Player, column: Column): string {
    return column.stat ? String(p[column.stat]) : '';
  }

  protected overall(p: Player): string {
    return p.overall_rating.toFixed(1);
  }

  /** A new page size starts again at page 1, keeping the sort. */
  protected setPageSize(event: Event): void {
    const s = this.state();
    const perPage = Number((event.target as HTMLSelectElement).value);
    void this.router.navigate([], {
      relativeTo: this.route,
      queryParams: { sort: s.sort, dir: s.dir, per_page: perPage },
    });
  }

  /** A page past the end (the list shrank) goes back to page 1, as Django's paginator does. */
  private failed(s: ViewState, error: unknown): void {
    if (error instanceof ApiError && error.status === 404 && s.page > 1) {
      void this.router.navigate([], {
        relativeTo: this.route,
        queryParams: { sort: s.sort, dir: s.dir, per_page: s.perPage },
      });
      return;
    }
    this.result.set(null);
    this.loadError.set(apiMessage(error, GENERIC_ERROR_MESSAGE));
  }
}
