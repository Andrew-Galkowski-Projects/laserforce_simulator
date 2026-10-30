import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed, toObservable, toSignal } from '@angular/core/rxjs-interop';
import { MatButton } from '@angular/material/button';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { Observable, catchError, combineLatest, map, of, startWith, switchMap } from 'rxjs';
import { ApiError, WithMeta } from '../../api/api-client';
import { TeamOptionsMeta } from '../../api/league-stats.models';
import { TeamSchedule } from '../../api/league-team.models';
import { LeagueTeamService } from '../../api/league-team.service';
import { apiMessage } from '../../shared/api-message';
import { GENERIC_ERROR_MESSAGE } from '../../shared/form-errors';
import { LeagueContext } from '../stats-shared/league-context';
import { resolveTeamParam } from '../stats-shared/team-param';

/** What the screen shows. */
type View =
  /** The nav or the schedule is still loading. */
  | { kind: 'loading' }
  /** I1: the League has no displayed Season (no request is sent). */
  | { kind: 'noSeason' }
  /** No Team resolves, or `?team=` 404s. */
  | { kind: 'noTeam' }
  /** I2: any other failure; Retry re-sends the request. */
  | { kind: 'error'; message: string }
  | { kind: 'loaded'; result: WithMeta<TeamSchedule, TeamOptionsMeta> };

/** The request the nav and `?team=` call for, or the empty state that replaces it. */
type Request = { leagueId: number; teamId: number } | View;

const POSITIVE_INT = /^[1-9]\d*$/;

/**
 * `/leagues/:id/team/schedule?team=` (spec #428 Team Schedule, `leagues/team_schedule.html`): a
 * Team's Upcoming and Completed Games and, when there are any, its Member Night Appearances. The
 * Team is `resolveTeamParam` (`?team=`, else the nav's `schedule_team`); the picker sets `?team=`.
 * Django 404s with no Season; this screen shows a notice instead (I1) and sends no request.
 */
@Component({
  selector: 'app-league-team-schedule',
  imports: [MatButton, RouterLink],
  templateUrl: './team-schedule.html',
  styleUrl: './team-schedule.scss',
})
export class LeagueTeamSchedule {
  private readonly teams = inject(LeagueTeamService);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly context = inject(LeagueContext);
  protected readonly nav = this.context.nav;

  private readonly teamQuery = toSignal(this.route.queryParamMap.pipe(map((p) => p.get('team'))), {
    initialValue: null,
  });
  private readonly reloads = signal(0);

  private readonly request = computed((): Request => {
    const nav = this.nav();
    const leagueId = this.context.leagueId();
    if (nav === null || leagueId === null) return { kind: 'loading' };
    if (nav.displayed_season === null) return { kind: 'noSeason' };
    const raw = this.teamQuery();
    const teamId = resolveTeamParam(
      raw !== null && POSITIVE_INT.test(raw) ? Number(raw) : null,
      nav,
    );
    return teamId === null ? { kind: 'noTeam' } : { leagueId, teamId };
  });

  protected readonly view = signal<View>({ kind: 'loading' });

  constructor() {
    combineLatest([toObservable(this.request), toObservable(this.reloads)])
      .pipe(
        switchMap(([req]) => ('kind' in req ? of(req) : this.load(req.leagueId, req.teamId))),
        takeUntilDestroyed(),
      )
      .subscribe((view) => this.view.set(view));
  }

  private load(leagueId: number, teamId: number): Observable<View> {
    return this.teams.schedule(leagueId, teamId).pipe(
      map((result): View => ({ kind: 'loaded', result })),
      catchError((error: unknown) =>
        of<View>(
          error instanceof ApiError && error.status === 404
            ? { kind: 'noTeam' }
            : { kind: 'error', message: apiMessage(error, GENERIC_ERROR_MESSAGE) },
        ),
      ),
      startWith<View>({ kind: 'loading' }),
    );
  }

  protected retry(): void {
    this.reloads.update((n) => n + 1);
  }

  /** The picker sets `?team=`; the load follows the URL. */
  protected pickTeam(event: Event): void {
    const team = Number((event.target as HTMLSelectElement).value);
    void this.router.navigate([], { relativeTo: this.route, queryParams: { team } });
  }
}
