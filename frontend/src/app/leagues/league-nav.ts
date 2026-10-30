import { LeagueNav } from '../api/leagues.models';

/** The League nav's groups; `top` has no heading. */
export type LeagueNavSection = 'top' | 'league' | 'team' | 'players' | 'stats';

/** One League nav entry. */
export interface LeagueNavEntry {
  key: string;
  label: string;
  section: LeagueNavSection;
  /** The router link for a live entry; null for a Soon one. */
  link: (nav: LeagueNav) => (string | number)[] | null;
  soon: boolean;
}

/** The headings above each group, as the Django sidebar prints them. */
export const LEAGUE_NAV_SECTION_LABELS: Readonly<Record<LeagueNavSection, string | null>> = {
  top: null,
  league: 'LEAGUE',
  team: 'TEAM',
  players: 'PLAYERS',
  stats: 'STATS',
};

/** A screen inside the League: its link is `/leagues/:id/<path...>`. */
function live(
  section: LeagueNavSection,
  key: string,
  label: string,
  path: string[],
): LeagueNavEntry {
  return { key, label, section, link: (nav) => ['/leagues', nav.league.id, ...path], soon: false };
}

/** A screen not ported yet: ANG-06-17..20 turn these live. */
function soon(section: LeagueNavSection, key: string, label: string): LeagueNavEntry {
  return { key, label, section, link: () => null, soon: true };
}

/**
 * Every League nav entry, in the Django sidebar's order (`_build_league_sidebar_links`,
 * league_views.py): 1 top + 6 LEAGUE + 4 TEAM + 6 PLAYERS + 6 STATS. Live: Dashboard and History
 * (ANG-06-14), Playoffs, Power Rankings and the six STATS entries (ANG-06-15), TEAM Roster /
 * Schedule / History and PLAYERS Free Agents / Watch List (ANG-06-16; the team screens pick their
 * Team from `?team=`, else the nav's `schedule_team`, so the links carry no query), LEAGUE
 * Finances (ANG-06-17; shown in a finance-off League too, as in Django: the screen explains).
 */
export const LEAGUE_NAV_ENTRIES: readonly LeagueNavEntry[] = [
  {
    key: 'dashboard',
    label: 'Dashboard',
    section: 'top',
    link: (nav) => ['/leagues', nav.league.id],
    soon: false,
  },
  soon('league', 'standings', 'Standings'),
  soon('league', 'schedule', 'Schedule'),
  live('league', 'playoffs', 'Playoffs', ['playoffs']),
  live('league', 'finances', 'Finances', ['finances']),
  {
    key: 'history',
    label: 'History',
    section: 'league',
    link: (nav) => ['/leagues', nav.league.id, 'history'],
    soon: false,
  },
  live('league', 'power_rankings', 'Power Rankings', ['power-rankings']),
  live('team', 'roster', 'Roster', ['team', 'roster']),
  live('team', 'schedule_team', 'Schedule', ['team', 'schedule']),
  soon('team', 'finances_team', 'Finances'),
  live('team', 'history_team', 'History', ['team', 'history']),
  live('players', 'free_agents', 'Free Agents', ['players', 'free-agents']),
  soon('players', 'trade', 'Trade'),
  soon('players', 'trading_block', 'Trading Block'),
  soon('players', 'prospects', 'Prospects'),
  live('players', 'watch_list', 'Watch List', ['players', 'watch-list']),
  soon('players', 'hall_of_fame', 'Hall of Fame'),
  live('stats', 'game_log', 'Game Log', ['stats', 'game-log']),
  live('stats', 'league_leaders', 'League Leaders', ['stats', 'league-leaders']),
  live('stats', 'player_ratings', 'Player Ratings', ['stats', 'player-ratings']),
  live('stats', 'player_stats', 'Player Stats', ['stats', 'player-stats']),
  live('stats', 'team_stats', 'Team Stats', ['stats', 'team-stats']),
  live('stats', 'statistical_feats', 'Statistical Feats', ['stats', 'statistical-feats']),
];
