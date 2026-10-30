import { leagueNav } from './testing/league-fixtures';
import { LEAGUE_NAV_ENTRIES } from './league-nav';

describe('LEAGUE_NAV_ENTRIES (spec #409 F5)', () => {
  it('lists the 23 entries in the Django order and sections', () => {
    expect(LEAGUE_NAV_ENTRIES.map((e) => `${e.section}.${e.key}:${e.label}`)).toEqual([
      'top.dashboard:Dashboard',
      'league.standings:Standings',
      'league.schedule:Schedule',
      'league.playoffs:Playoffs',
      'league.finances:Finances',
      'league.history:History',
      'league.power_rankings:Power Rankings',
      'team.roster:Roster',
      'team.schedule_team:Schedule',
      'team.finances_team:Finances',
      'team.history_team:History',
      'players.free_agents:Free Agents',
      'players.trade:Trade',
      'players.trading_block:Trading Block',
      'players.prospects:Prospects',
      'players.watch_list:Watch List',
      'players.hall_of_fame:Hall of Fame',
      'stats.game_log:Game Log',
      'stats.league_leaders:League Leaders',
      'stats.player_ratings:Player Ratings',
      'stats.player_stats:Player Stats',
      'stats.team_stats:Team Stats',
      'stats.statistical_feats:Statistical Feats',
    ]);
  });

  it('makes Dashboard, History and the eight stats screens live, linking into the League', () => {
    const nav = leagueNav({ league: { id: 7, name: 'Metro League', mode: 'league' } });
    const live = LEAGUE_NAV_ENTRIES.filter((e) => !e.soon);
    expect(live.map((e) => [e.key, e.link(nav)])).toEqual([
      ['dashboard', ['/leagues', 7]],
      ['playoffs', ['/leagues', 7, 'playoffs']],
      ['history', ['/leagues', 7, 'history']],
      ['power_rankings', ['/leagues', 7, 'power-rankings']],
      ['game_log', ['/leagues', 7, 'stats', 'game-log']],
      ['league_leaders', ['/leagues', 7, 'stats', 'league-leaders']],
      ['player_ratings', ['/leagues', 7, 'stats', 'player-ratings']],
      ['player_stats', ['/leagues', 7, 'stats', 'player-stats']],
      ['team_stats', ['/leagues', 7, 'stats', 'team-stats']],
      ['statistical_feats', ['/leagues', 7, 'stats', 'statistical-feats']],
    ]);
  });

  it('marks every other entry Soon, with no link', () => {
    const soon = LEAGUE_NAV_ENTRIES.filter((e) => e.soon);
    expect(soon).toHaveLength(13);
    expect(soon.map((e) => e.link(leagueNav()))).toEqual(soon.map(() => null));
  });
});
