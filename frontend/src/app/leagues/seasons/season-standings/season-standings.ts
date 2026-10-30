import { Component, DestroyRef, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatAnchor, MatButton } from '@angular/material/button';
import { ActivatedRoute, ParamMap, Params, RouterLink } from '@angular/router';
import { EMPTY, Subject, catchError, combineLatest, map, startWith, switchMap, tap } from 'rxjs';
import { SEASON_STATE_LABELS, SeasonState, Streak, WinLossTie } from '../../../api/leagues.models';
import { LeaguesService } from '../../../api/leagues.service';
import {
  SeasonStandings as Standings,
  StandingsParams,
  StandingsSortKey,
} from '../../../api/seasons.models';
import { SeasonsService } from '../../../api/seasons.service';
import { apiMessage } from '../../../shared/api-message';
import { GENERIC_ERROR_MESSAGE } from '../../../shared/form-errors';
import { sortArrow } from '../../stats-shared/stats-query';
import { SeasonBreadcrumb } from '../season-breadcrumb/season-breadcrumb';
import { SeasonContext } from '../season-context';

/** `_STANDINGS_SORT_KEYS_DISPLAY`: every column's sort key and header, in table order. */
export const STANDINGS_COLUMNS: readonly { key: StandingsSortKey; label: string }[] = [
  { key: 'rank', label: 'Rank' },
  { key: 'team', label: 'Team' },
  { key: 'matches_played', label: 'MP' },
  { key: 'wins', label: 'W' },
  { key: 'losses', label: 'L' },
  { key: 'ties', label: 'T' },
  { key: 'league_points', label: 'Pts' },
  { key: 'round_wins', label: 'RW' },
  { key: 'total_score', label: 'TS' },
  { key: 'match_streak', label: 'Streak' },
  { key: 'match_l5', label: 'L5' },
  { key: 'round_streak', label: 'R Streak' },
  { key: 'round_l5', label: 'R L5' },
  { key: 'red_wlt', label: 'Red Rec' },
  { key: 'blue_wlt', label: 'Blue Rec' },
  { key: 'red_points_for', label: 'Red PF' },
  { key: 'blue_points_for', label: 'Blue PF' },
];

/** The URL's `?sort=&dir=`, each kept only when the API accepts it (never a 400). */
function standingsParams(query: ParamMap): StandingsParams {
  const params: StandingsParams = {};
  const sort = STANDINGS_COLUMNS.find((c) => c.key === query.get('sort'))?.key;
  if (sort !== undefined) params.sort = sort;
  const dir = query.get('dir');
  if (dir === 'asc' || dir === 'desc') params.dir = dir;
  return params;
}

/**
 * `/leagues/:id/seasons/:seasonId/standings` (spec #444 Standings, `seasons/standings.html`):
 * the Season's Standings, one table per Conference, sorted by `?sort=&dir=`.
 */
@Component({
  selector: 'app-season-standings',
  imports: [MatAnchor, MatButton, RouterLink, SeasonBreadcrumb],
  templateUrl: './season-standings.html',
  styleUrl: './season-standings.scss',
})
export class SeasonStandings {
  protected readonly context = inject(SeasonContext);
  protected readonly columns = STANDINGS_COLUMNS;
  protected readonly standings = signal<Standings | null>(null);
  protected readonly loadError = signal<string | null>(null);
  /** The manager's Team in a career League (marked "You"), else null. */
  protected readonly managedTeamId = signal<number | null>(null);
  private readonly reloads = new Subject<void>();

  constructor() {
    const seasons = inject(SeasonsService);
    const destroyRef = inject(DestroyRef);
    // I4: Django's `_managed_team_id`, `current_team` in a career League only. The badge is a
    // nicety, so a failed League GET just leaves every row unmarked.
    inject(LeaguesService)
      .get(this.context.leagueId() as number)
      .pipe(
        catchError(() => EMPTY),
        takeUntilDestroyed(destroyRef),
      )
      .subscribe((league) =>
        this.managedTeamId.set(league.mode === 'league' ? (league.current_team?.id ?? null) : null),
      );
    combineLatest([
      inject(ActivatedRoute).queryParamMap.pipe(map(standingsParams)),
      this.reloads.pipe(startWith(undefined)),
    ])
      .pipe(
        tap(() => this.loadError.set(null)),
        switchMap(([params]) =>
          seasons.standings(this.context.seasonId() as number, params).pipe(
            catchError((failure: unknown) => {
              this.loadError.set(apiMessage(failure, GENERIC_ERROR_MESSAGE));
              return EMPTY;
            }),
          ),
        ),
        takeUntilDestroyed(destroyRef),
      )
      .subscribe((loaded) => this.standings.set(loaded));
  }

  /** I2: re-sends only the standings GET, with the URL's sort. */
  protected retry(): void {
    this.reloads.next();
  }

  /** A draft preview with no rows in any group: no table, just the empty text. */
  protected noTeams(s: Standings): boolean {
    return s.is_draft_preview && s.groups.every((g) => g.rows.length === 0);
  }

  protected stateLabel(state: SeasonState): string {
    return SEASON_STATE_LABELS[state];
  }

  /** The header's arrow when it is the response's sort, else nothing. */
  protected arrow(s: Standings, key: StandingsSortKey): string {
    return s.sort === key ? sortArrow(s.dir) : '';
  }

  /** The active column toggles `dir`; any other column sorts by itself ascending. */
  protected sortParams(s: Standings, key: StandingsSortKey): Params {
    return { sort: key, dir: s.sort === key && s.dir === 'asc' ? 'desc' : 'asc' };
  }

  /** `W3`, or `—` when the streak has no kind. */
  protected streak([kind, length]: Streak): string {
    return kind ? `${kind}${length}` : '—';
  }

  protected wlt([w, l, t]: WinLossTie): string {
    return `${w}-${l}-${t}`;
  }
}
