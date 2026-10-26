import { DatePipe } from '@angular/common';
import { Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatAnchor } from '@angular/material/button';
import { ActivatedRoute, ParamMap, Router, RouterLink } from '@angular/router';
import { EMPTY, catchError, combineLatest, switchMap } from 'rxjs';
import { ApiError } from '../../api/api-client';
import { HistoryRoleCell, TeamHistory as TeamHistoryData } from '../../api/analytics.models';
import { AnalyticsService } from '../../api/analytics.service';
import { AuthService } from '../../auth/auth.service';
import { Result } from '../../matches/shared/result';
import { ResultChip } from '../../matches/shared/result-chip';
import { fixed } from '../../players/shared/benchmark-format';
import { apiMessage } from '../../shared/api-message';
import { GENERIC_ERROR_MESSAGE } from '../../shared/form-errors';
import { TeamNotFound } from '../shared/team-not-found';

/** A scope key the API knows the shape of: `freeform` or `t<tournament id>`. */
const SCOPE_KEY = /^(freeform|t[1-9]\d*)$/;

/**
 * The scopes to ask for (INV-1), Django's rule: without `applied` it is a fresh load (`null`,
 * every scope); with it, the well-formed `scope` values (none left is `[]`, Overall off).
 */
export function historyScopes(query: ParamMap): string[] | null {
  if (!query.has('applied')) return null;
  return query.getAll('scope').filter((key) => SCOPE_KEY.test(key));
}

const RESULTS: Record<'won' | 'lost' | 'tie', Result> = {
  won: { label: 'Won', muted: false },
  lost: { label: 'Lost', muted: false },
  tie: { label: 'Tie', muted: false },
};

/**
 * `/teams/:id/history`: one Team's **Team match history** (ANG-06-9, `team_history.html` minus
 * the doughnut): the record tiles, the scope filter (kept in the URL as `?scope=&applied=1`),
 * the roster by Role, the opponents' head-to-head links and the Matches / Single Rounds lists.
 */
@Component({
  selector: 'app-team-history',
  imports: [DatePipe, MatAnchor, ResultChip, RouterLink, TeamNotFound],
  templateUrl: './team-history.html',
  styleUrls: ['../shared/team-page.scss', './team-history.scss'],
})
export class TeamHistory {
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);

  protected readonly history = signal<TeamHistoryData | null>(null);
  protected readonly notFound = signal(false);
  protected readonly loadError = signal<string | null>(null);

  /** INV-6: the signed-in Account's Self-insert Player id. */
  protected readonly selfId = computed(() => this.auth.currentUser()?.self_insert?.id);

  /** Overall is ticked when every scope is. */
  protected readonly overall = computed(() => {
    const options = this.history()?.scope_options ?? [];
    return options.length > 0 && options.every((o) => o.selected);
  });

  protected readonly results = RESULTS;

  constructor() {
    const analytics = inject(AnalyticsService);
    combineLatest([this.route.paramMap, this.route.queryParamMap])
      .pipe(
        switchMap(([params, query]) => {
          this.history.set(null);
          this.notFound.set(false);
          this.loadError.set(null);
          return analytics.teamHistory(Number(params.get('id')), historyScopes(query)).pipe(
            catchError((error: unknown) => {
              if (error instanceof ApiError && error.status === 404) this.notFound.set(true);
              else this.loadError.set(apiMessage(error, GENERIC_ERROR_MESSAGE));
              return EMPTY;
            }),
          );
        }),
        takeUntilDestroyed(inject(DestroyRef)),
      )
      .subscribe((loaded) => this.history.set(loaded));
  }

  /** Overall on selects every scope; off selects none. */
  protected toggleOverall(checked: boolean): void {
    const keys = checked ? (this.history()?.scope_options ?? []).map((o) => o.key) : [];
    this.applyScopes(keys);
  }

  /** One scope ticked or unticked: the others keep their state. */
  protected toggleScope(key: string, checked: boolean): void {
    const keys = (this.history()?.scope_options ?? [])
      .filter((o) => (o.key === key ? checked : o.selected))
      .map((o) => o.key);
    this.applyScopes(keys);
  }

  protected avgMvp(cell: HistoryRoleCell): string {
    return cell.avg_mvp === null ? '0' : fixed(cell.avg_mvp, 2);
  }

  protected avgScore(cell: HistoryRoleCell): string {
    return cell.avg_score === null ? '0' : fixed(cell.avg_score, 0);
  }

  /** INV-1: the picks go to the URL, which reloads the history; an empty pick is `scope=`. */
  private applyScopes(keys: string[]): void {
    void this.router.navigate([], {
      relativeTo: this.route,
      queryParams: { scope: keys.length > 0 ? keys : '', applied: 1 },
    });
  }
}
