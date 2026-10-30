import { Injectable, signal } from '@angular/core';
import { LeagueNav } from '../../api/leagues.models';

/**
 * The League the `/leagues/:id/...` layout has loaded, for the screens routed inside it.
 * `LeagueLayout` provides it and sets `nav` once `GET leagues/{id}/nav/` answers.
 *
 * How a screen uses it: `protected readonly nav = inject(LeagueContext).nav;`, then
 * `nav()?.league.name` for an "<League> — <Screen>" heading, or `nav()?.displayed_season` (Player
 * Ratings' I3: null means no Season). It is null until the nav loads; a 404 nav never renders
 * the screen at all (I7: the layout shows its not-found state).
 */
@Injectable()
export class LeagueContext {
  readonly nav = signal<LeagueNav | null>(null);
}
