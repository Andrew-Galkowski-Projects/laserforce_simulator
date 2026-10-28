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

/** A screen not ported yet: ANG-06-15..20 turn these live. */
function soon(section: LeagueNavSection, key: string, label: string): LeagueNavEntry {
  return { key, label, section, link: () => null, soon: true };
}

/**
 * Every League nav entry, in the Django sidebar's order (`_build_league_sidebar_links`,
 * league_views.py): 1 top + 6 LEAGUE + 4 TEAM + 6 PLAYERS + 6 STATS. Live in ANG-06-14:
 * Dashboard and History.
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
  soon('league', 'playoffs', 'Playoffs'),
  soon('league', 'finances', 'Finances'),
  {
    key: 'history',
    label: 'History',
    section: 'league',
    link: (nav) => ['/leagues', nav.league.id, 'history'],
    soon: false,
  },
  soon('league', 'power_rankings', 'Power Rankings'),
  soon('team', 'roster', 'Roster'),
  soon('team', 'schedule_team', 'Schedule'),
  soon('team', 'finances_team', 'Finances'),
  soon('team', 'history_team', 'History'),
  soon('players', 'free_agents', 'Free Agents'),
  soon('players', 'trade', 'Trade'),
  soon('players', 'trading_block', 'Trading Block'),
  soon('players', 'prospects', 'Prospects'),
  soon('players', 'watch_list', 'Watch List'),
  soon('players', 'hall_of_fame', 'Hall of Fame'),
  soon('stats', 'game_log', 'Game Log'),
  soon('stats', 'league_leaders', 'League Leaders'),
  soon('stats', 'player_ratings', 'Player Ratings'),
  soon('stats', 'player_stats', 'Player Stats'),
  soon('stats', 'team_stats', 'Team Stats'),
  soon('stats', 'statistical_feats', 'Statistical Feats'),
];
