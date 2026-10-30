import { Injectable, Signal, inject, signal } from '@angular/core';
import { Season } from '../../api/leagues.models';
import { LeagueContext } from '../stats-shared/league-context';

/**
 * The Season the `/leagues/:id/seasons/:seasonId/...` layout has loaded, for the screens routed
 * inside it (spec #444). `SeasonLayout` provides it, sets `seasonId` from the route at once and
 * `season` once `GET seasons/{seasonId}/` answers; `leagueId` is the League layout's `:id`.
 *
 * How a screen uses it: `private readonly ctx = inject(SeasonContext);`, then `ctx.seasonId()`
 * for its own GET and `ctx.season()` for the state badge or `champion_team`. A screen only exists
 * once `season()` is set and belongs to the route's League (I1: the layout renders its outlet
 * only then), so inside a screen `season()` is not null.
 */
@Injectable()
export class SeasonContext {
  readonly season = signal<Season | null>(null);
  /** The `:seasonId` route param. */
  readonly seasonId = signal<number | null>(null);
  /** The League layout's `:id` route param. */
  readonly leagueId: Signal<number | null> = inject(LeagueContext).leagueId.asReadonly();
}
