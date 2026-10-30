import { Injectable, Signal, inject, signal } from '@angular/core';
import { Season } from '../../api/leagues.models';
import { LeagueContext } from '../stats-shared/league-context';

/**
 * The Season the `/leagues/:id/seasons/:seasonId/...` layout has loaded, for the screens routed
 * inside it (spec #444). `SeasonLayout` provides it, sets `seasonId` from the route at once and
 * `season` once `GET seasons/{seasonId}/` answers; `leagueId` is the League layout's `:id`.
 *
 * How a screen uses it: `private readonly context = inject(SeasonContext);`, then
 * `context.requireSeasonId()` for its own GET (or `injectSeasonLoad`, which reads it),
 * `context.link('standings')` for a link to another Season screen and `context.season()` for the
 * state badge or `champion_team`. A screen only exists once `season()` is set and belongs to the
 * route's League (I1: the layout renders its outlet only then), so inside a screen `season()` is
 * not null and the `require*` accessors and `link()` never throw.
 */
@Injectable()
export class SeasonContext {
  readonly season = signal<Season | null>(null);
  /** The `:seasonId` route param. */
  readonly seasonId = signal<number | null>(null);
  /** The League layout's `:id` route param. */
  readonly leagueId: Signal<number | null> = inject(LeagueContext).leagueId.asReadonly();

  /** `seasonId()`, never null inside a Season screen; throws if read before the layout set it. */
  requireSeasonId(): number {
    return required(this.seasonId(), 'Season id');
  }

  /** `leagueId()`, never null inside a Season screen; throws if read before the layout set it. */
  requireLeagueId(): number {
    return required(this.leagueId(), 'League id');
  }

  /**
   * The router commands for `/leagues/:id/seasons/:seasonId/<...screen>`: the Season dashboard
   * with no `screen`. Reads the ids' signals, so a template binding follows them.
   */
  link(...screen: string[]): (string | number)[] {
    return ['/leagues', this.requireLeagueId(), 'seasons', this.requireSeasonId(), ...screen];
  }
}

function required(id: number | null, what: string): number {
  if (id === null) throw new Error(`SeasonContext: ${what} read before the layout set it.`);
  return id;
}
