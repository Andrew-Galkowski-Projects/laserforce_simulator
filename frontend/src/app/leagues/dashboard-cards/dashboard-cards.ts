import { Component, computed, inject, input } from '@angular/core';
import { Params, RouterLink } from '@angular/router';
import { DashboardBody, LeaderRow, MemberNightProgress } from '../../api/leagues.models';
import { AuthService } from '../../auth/auth.service';
import { SoonBadge } from '../soon-badge/soon-badge';

/** A `routerLink` target: absolute commands plus an optional query and fragment. */
export interface DashboardLink {
  commands: readonly (string | number)[];
  queryParams?: Params;
  fragment?: string;
}

/** One top-3 leader card. */
interface LeaderBoard {
  title: string;
  rows: LeaderRow[];
}

/**
 * The body cards the League and Season dashboards share (spec #444), from Django's
 * `_partials/dashboard_*.html`: "Top standings" (any Season), the member-night card (when
 * `memberNight` is set), and on an active or completed Season "Next round", "Rounds played", the
 * three leader cards and "View all leaders". A "You" badge marks the managed Team's standings row
 * (`managedTeamId`; null on the Season dashboard, as Django passes none there) and the signed-in
 * Account's Self-insert leader rows (I4). `resultsLink` null shows "View results" as Soon.
 */
@Component({
  selector: 'app-dashboard-cards',
  imports: [RouterLink, SoonBadge],
  templateUrl: './dashboard-cards.html',
  styleUrl: './dashboard-cards.scss',
})
export class DashboardCards {
  readonly body = input.required<DashboardBody>();
  readonly managedTeamId = input<number | null>(null);
  readonly memberNight = input<MemberNightProgress | null>(null);
  readonly leadersLink = input.required<DashboardLink>();
  readonly resultsLink = input<DashboardLink | null>(null);

  private readonly auth = inject(AuthService);
  /** I4: the signed-in Account's Self-insert Player id, if linked. */
  protected readonly selfId = computed(() => this.auth.currentUser()?.self_insert?.id);

  /** The three leader cards, in the Django dashboard's order. */
  protected readonly boards = computed<LeaderBoard[]>(() => {
    const body = this.body();
    return [
      { title: 'Points per game', rows: body.leaders_points },
      { title: 'Tags per game', rows: body.leaders_tags },
      { title: 'Tag ratio', rows: body.leaders_ratio },
    ];
  });
}
