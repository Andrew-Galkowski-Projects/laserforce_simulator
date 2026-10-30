import { NgTemplateOutlet } from '@angular/common';
import { Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed, toObservable } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router } from '@angular/router';
import { MatButton } from '@angular/material/button';
import { EMPTY, Subject, combineLatest, map, startWith, switchMap, tap } from 'rxjs';
import { WithMeta } from '../../api/api-client';
import { TeamOptionsMeta } from '../../api/league-stats.models';
import { RosterRow, TeamRoster } from '../../api/league-team.models';
import { LeagueTeamService } from '../../api/league-team.service';
import { notFoundOrError } from '../../shared/not-found-or-error';
import { LeagueContext } from '../stats-shared/league-context';
import { PlayerCell } from '../stats-shared/player-cell';
import { orDash, potentialText } from '../stats-shared/player-columns';
import {
  StatsQueryConfig,
  parseStatsQuery,
  toQueryParams,
  withTeam,
} from '../stats-shared/stats-query';
import { resolveTeamParam } from '../stats-shared/team-param';

type Loaded = WithMeta<TeamRoster, TeamOptionsMeta>;

/** Only `?team=` is URL state here: no sort, Season or paging. */
const CONFIG: StatsQueryConfig = {
  sortKeys: [],
  defaultSort: '',
  defaultDir: 'asc',
  firstDir: 'asc',
  team: true,
};

/**
 * `/leagues/:id/team/roster` (spec #428 Roster, `leagues/team_roster.html`): a Team's Starting
 * Six and Bench. The Team is `?team=`, else the manager's current Team (`resolveTeamParam`).
 */
@Component({
  selector: 'app-league-team-roster',
  imports: [MatButton, NgTemplateOutlet, PlayerCell],
  templateUrl: './team-roster.html',
  styleUrl: './team-roster.scss',
})
export class LeagueTeamRoster {
  private readonly teams = inject(LeagueTeamService);
  private readonly context = inject(LeagueContext);
  protected readonly nav = this.context.nav;

  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly query = signal(parseStatsQuery(this.route.snapshot.queryParamMap, CONFIG));
  /** I1: the nav has loaded and the League has no Season. */
  protected readonly noSeason = computed(() => {
    const nav = this.nav();
    return nav !== null && nav.displayed_season === null;
  });
  /** The Team to load: null before the nav, with no Season (I1), or with no Team to show. */
  private readonly teamId = computed(() => {
    const nav = this.nav();
    if (nav === null || nav.displayed_season === null) return null;
    return resolveTeamParam(this.query().team, nav);
  });
  protected readonly result = signal<Loaded | null>(null);
  /** The roster answered 404: a stale `?team=` (not enrolled in the displayed Season). */
  private readonly notFound = signal(false);
  protected readonly loadError = signal<string | null>(null);
  private readonly reloads = new Subject<void>();
  /** "No team available…": a Season, but no Team resolves or the Team is not found. */
  protected readonly noTeam = computed(
    () => !this.noSeason() && this.nav() !== null && (this.teamId() === null || this.notFound()),
  );

  constructor() {
    this.route.queryParamMap
      .pipe(takeUntilDestroyed())
      .subscribe((p) => this.query.set(parseStatsQuery(p, CONFIG)));
    const id$ = (this.route.parent ?? this.route).paramMap.pipe(map((p) => Number(p.get('id'))));
    combineLatest([id$, toObservable(this.teamId), this.reloads.pipe(startWith(undefined))])
      .pipe(
        tap(() => {
          this.result.set(null);
          this.notFound.set(false);
          this.loadError.set(null);
        }),
        switchMap(([id, teamId]) =>
          teamId === null
            ? EMPTY
            : this.teams
                .roster(id, teamId)
                .pipe(notFoundOrError<Loaded>(this.notFound, this.loadError)),
        ),
        takeUntilDestroyed(inject(DestroyRef)),
      )
      .subscribe((loaded) => this.result.set(loaded));
  }

  /** I2: re-sends the same roster request. */
  protected retry(): void {
    this.reloads.next();
  }

  /** The picker has no "All": every option is a Team, kept in `?team=`. */
  protected pickTeam(event: Event): void {
    const team = Number((event.target as HTMLSelectElement).value);
    void this.router.navigate([], {
      relativeTo: this.route,
      queryParams: toQueryParams(withTeam(this.query(), team), CONFIG),
    });
  }

  /** `role|title`: "commander" reads "Commander". */
  protected role(row: RosterRow): string {
    return (row.role ?? '').replace(/\b\w/g, (c) => c.toUpperCase());
  }

  protected potential(row: RosterRow): string {
    return potentialText(row.potential);
  }

  /** `default:"-"`: a missing number or an empty string shows "-". */
  protected orDash(value: number | string | null): string {
    return orDash(value);
  }
}
