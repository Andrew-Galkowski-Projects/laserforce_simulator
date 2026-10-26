import { Component, DestroyRef, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { NonNullableFormBuilder, ReactiveFormsModule } from '@angular/forms';
import { DatePipe } from '@angular/common';
import { MatAnchor, MatButton } from '@angular/material/button';
import { MatFormField, MatLabel } from '@angular/material/form-field';
import { MatInput } from '@angular/material/input';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { EMPTY, switchMap } from 'rxjs';
import {
  ImpactfulPlayer,
  TeamH2HDetailRow,
  TeamH2HParams,
  TeamHeadToHead as TeamH2HData,
} from '../../api/analytics.models';
import { AnalyticsService } from '../../api/analytics.service';
import { listAll } from '../../api/list-all';
import { TeamListItem } from '../../api/teams.models';
import { TeamsService } from '../../api/teams.service';
import {
  PROVENANCE_OPTIONS,
  QueryReader,
  paramOrNull,
  queryDate,
  queryId,
  queryProvenance,
} from '../../shared/head-to-head/h2h-query';
import { H2HCard, H2HPlayerName } from '../../shared/head-to-head/h2h-card';
import { H2HCharts } from '../../shared/head-to-head/h2h-charts';
import { H2HMapTable } from '../../shared/head-to-head/h2h-map-table';
import { plural, record } from '../../shared/head-to-head/h2h-format';
import { DASH, fixed } from '../../players/shared/benchmark-format';
import { apiMessage } from '../../shared/api-message';
import { GENERIC_ERROR_MESSAGE } from '../../shared/form-errors';
import { notFoundOrError } from '../../shared/not-found-or-error';
import { TeamNotFound } from '../shared/team-not-found';

/** The URL's picks, each dropped when the API would refuse it (INV-1). */
function teamH2HQuery(query: QueryReader): Partial<TeamH2HParams> {
  return {
    team_a: queryId(query, 'team_a'),
    team_b: queryId(query, 'team_b'),
    provenance: queryProvenance(query),
    from: queryDate(query, 'from'),
    to: queryDate(query, 'to'),
  };
}

/**
 * `/teams/head-to-head`: the **Head-to-head record** of two Teams (ANG-06-9,
 * `head_to_head.html`). The picks and filters live in the URL (INV-1); Compare only navigates.
 */
@Component({
  selector: 'app-team-head-to-head',
  imports: [
    DatePipe,
    H2HCard,
    H2HCharts,
    H2HMapTable,
    H2HPlayerName,
    MatAnchor,
    MatButton,
    RouterLink,
    MatFormField,
    MatInput,
    MatLabel,
    ReactiveFormsModule,
    TeamNotFound,
  ],
  templateUrl: './team-head-to-head.html',
  styleUrl: '../../shared/head-to-head/h2h-page.scss',
})
export class TeamHeadToHead {
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);

  protected readonly teams = signal<TeamListItem[]>([]);
  protected readonly result = signal<TeamH2HData | null>(null);
  /** INV-2 / INV-4: the analytics call's message, shown above the picker. */
  protected readonly error = signal<string | null>(null);
  /** INV-4: the Team list's message. */
  protected readonly listError = signal<string | null>(null);
  /** INV-3: a Team is missing or another Account's. */
  protected readonly notFound = signal(false);
  protected readonly provenanceOptions = PROVENANCE_OPTIONS;

  protected readonly form = inject(NonNullableFormBuilder).group({
    team_a: '',
    team_b: '',
    provenance: 'all',
    from: '',
    to: '',
  });

  constructor() {
    const destroyRef = inject(DestroyRef);
    const teams = inject(TeamsService);
    const analytics = inject(AnalyticsService);
    listAll((page) => teams.list({ ...page, ordering: 'name' }))
      .pipe(takeUntilDestroyed(destroyRef))
      .subscribe({
        next: (all) => this.teams.set(all),
        error: (error: unknown) => this.listError.set(apiMessage(error, GENERIC_ERROR_MESSAGE)),
      });

    this.route.queryParamMap
      .pipe(
        switchMap((query) => {
          const q = teamH2HQuery(query);
          this.form.setValue({
            team_a: q.team_a?.toString() ?? '',
            team_b: q.team_b?.toString() ?? '',
            provenance: q.provenance ?? 'all',
            from: q.from ?? '',
            to: q.to ?? '',
          });
          this.result.set(null);
          this.error.set(null);
          this.notFound.set(false);
          if (q.team_a === undefined || q.team_b === undefined) return EMPTY;
          return analytics
            .teamHeadToHead({ ...q, team_a: q.team_a, team_b: q.team_b })
            .pipe(notFoundOrError(this.notFound, this.error));
        }),
        takeUntilDestroyed(destroyRef),
      )
      .subscribe((loaded) => this.result.set(loaded));
  }

  protected readonly plural = plural;
  protected readonly fixed = fixed;
  protected readonly record = record;
  protected readonly dash = DASH;

  /** The two "Top impactful" cards: Team A's, then Team B's. */
  protected impactful(
    r: TeamH2HData,
  ): { team: string; player: ImpactfulPlayer | null; note: string | null }[] {
    const card = (team: string, player: ImpactfulPlayer | null) => ({
      team,
      player,
      note: player
        ? `MVP ${fixed(player.mvp_total, 1)} over ${plural(player.games, 'round')}`
        : null,
    });
    return [
      card(r.team_a.name, r.top_impactful.team_a),
      card(r.team_b.name, r.top_impactful.team_b),
    ];
  }

  /** INV-5: the Angular Match or Round page, by id. */
  protected gameLink(row: TeamH2HDetailRow): string[] {
    return row.match_id !== null
      ? ['/matches', String(row.match_id)]
      : ['/rounds', String(row.round_id)];
  }

  /** Writes the picks to the URL, which loads the record. */
  protected compare(): void {
    const v = this.form.getRawValue();
    void this.router.navigate([], {
      relativeTo: this.route,
      queryParams: {
        team_a: paramOrNull(v.team_a),
        team_b: paramOrNull(v.team_b),
        provenance: v.provenance === 'all' ? null : v.provenance,
        from: paramOrNull(v.from),
        to: paramOrNull(v.to),
      },
    });
  }
}
