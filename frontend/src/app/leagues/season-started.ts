import { SeasonDashboard } from '../api/leagues.models';
import { countOf } from '../shared/count-of';

/**
 * The Start Season confirmation (`league_views.start_season`, UXP-13): one scheduled fixture is
 * one Round. Shared by the League dashboard's Start Season and the Play menu's (spec #454).
 */
export function seasonStartedMessage(started: SeasonDashboard): string {
  return `${started.season.name} started — ${countOf(started.round_count_total, 'Round')} scheduled.`;
}
