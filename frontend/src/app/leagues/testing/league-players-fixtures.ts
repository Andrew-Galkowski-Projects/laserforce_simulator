// League Player screen spec fixtures (ANG-06-16, spec #428 F9): builders for
// `api/league-players.models.ts`, every required field filled, each a real-shaped payload that
// `overrides` adjusts. Imported only by *.spec.ts files. Free Agents rows are `playerRatingRow`
// and Watch List rows `playerStatRow` (league-stats-fixtures); `pagedEnvelope` wraps both.
import { PlayerRef } from '../../api/analytics.models';
import {
  LeaguePlayer,
  LeaguePlayerAward,
  LeaguePlayerBio,
  LeaguePlayerSeasonRow,
  LeaguePlayerStatRow,
  RatingsHistoryRow,
  WatchResult,
} from '../../api/league-players.models';
import { playerRatingRow, playerStatRow } from './league-stats-fixtures';

const ACE: PlayerRef = { id: 25, name: 'Ace', team: { id: 3, name: 'Red Raiders' } };

export function leaguePlayerBio(overrides: Partial<LeaguePlayerBio> = {}): LeaguePlayerBio {
  return {
    age: 24,
    height: '5\'11"',
    home_site: 'Laserforce Ventura',
    started_playing_age: 15,
    total_games: 120,
    preferred_roles: ['commander', 'heavy'],
    ...overrides,
  };
}

/** A Career row for Red Raiders over 8 games, with the Player Stats keys. */
export function leaguePlayerStatRow(
  overrides: Partial<LeaguePlayerStatRow> = {},
): LeaguePlayerStatRow {
  return {
    team: { id: 3, name: 'Red Raiders' },
    games: 8,
    stats: { ...playerStatRow().stats },
    ...overrides,
  };
}

/** Season 1's Regular Season row ("2026" is the template's year label). */
export function leaguePlayerSeasonRow(
  overrides: Partial<LeaguePlayerSeasonRow> = {},
): LeaguePlayerSeasonRow {
  return { season: { id: 11, name: '2026' }, ...leaguePlayerStatRow(), ...overrides };
}

export function leaguePlayerAward(overrides: Partial<LeaguePlayerAward> = {}): LeaguePlayerAward {
  return {
    season: { id: 11, name: 'Season 1' },
    award_labels: ['Season MVP', 'All-League First Team'],
    ...overrides,
  };
}

export function ratingsHistoryRow(overrides: Partial<RatingsHistoryRow> = {}): RatingsHistoryRow {
  return {
    season: { id: 11, name: 'Season 1' },
    age: 24,
    overall_rating: 72.4,
    potential: 80.5,
    stats: { ...playerRatingRow().stats },
    ...overrides,
  };
}

/** Ace (25) in Metro League: one Season, a Career row, one award, one ratings snapshot. */
export function leaguePlayer(overrides: Partial<LeaguePlayer> = {}): LeaguePlayer {
  return {
    player: ACE,
    watched: false,
    overall_rating: 72.4,
    potential: 80.5,
    bio: leaguePlayerBio(),
    stats: { ...playerRatingRow().stats },
    seasons: [leaguePlayerSeasonRow()],
    career: leaguePlayerStatRow(),
    awards: [leaguePlayerAward()],
    ratings_history: [ratingsHistoryRow()],
    can_link_self_insert: false,
    ...overrides,
  };
}

/** The watch / unwatch 200 body for `player`. */
export function watchResult(watched: boolean, player: PlayerRef = ACE): WatchResult {
  return { player, watched };
}
