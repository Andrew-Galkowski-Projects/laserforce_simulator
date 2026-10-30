import { Routes } from '@angular/router';
import { APP_TITLE } from '../app-title';
import { LeagueCreateAdvanced } from './league-create-advanced/league-create-advanced';
import { LeagueCreate } from './league-create/league-create';
import { LeagueDashboard } from './league-dashboard/league-dashboard';
import { LeagueHistory } from './league-history/league-history';
import { LeagueLayout } from './league-layout/league-layout';
import { LeagueList } from './league-list/league-list';
import { LeagueNewTeam } from './league-new-team/league-new-team';

/**
 * The Leagues area, lazily loaded under `/leagues` by `app.routes.ts`. Every `/leagues/:id/...`
 * screen is a child of `LeagueLayout`, which puts the League nav beside it.
 */
export const LEAGUES_ROUTES: Routes = [
  { path: '', component: LeagueList, title: `Leagues - ${APP_TITLE}` },
  { path: 'new', component: LeagueCreate, title: `Create League - ${APP_TITLE}` },
  {
    path: 'new/advanced',
    component: LeagueCreateAdvanced,
    title: `Advanced League - ${APP_TITLE}`,
  },
  {
    path: ':id',
    component: LeagueLayout,
    children: [
      { path: '', component: LeagueDashboard, title: `League - ${APP_TITLE}` },
      { path: 'history', component: LeagueHistory, title: `League History - ${APP_TITLE}` },
      { path: 'new-team', component: LeagueNewTeam, title: `Choose New Team - ${APP_TITLE}` },
      // ANG-06-15: the League stats screens, each lazily loaded from its own folder.
      {
        path: 'stats/league-leaders',
        loadComponent: () => import('./league-leaders/league-leaders').then((m) => m.LeagueLeaders),
        title: `League Leaders - ${APP_TITLE}`,
      },
      {
        path: 'stats/player-stats',
        loadComponent: () => import('./player-stats/player-stats').then((m) => m.PlayerStats),
        title: `Player Stats - ${APP_TITLE}`,
      },
      {
        path: 'stats/player-ratings',
        loadComponent: () => import('./player-ratings/player-ratings').then((m) => m.PlayerRatings),
        title: `Player Ratings - ${APP_TITLE}`,
      },
      {
        path: 'stats/team-stats',
        loadComponent: () => import('./team-stats/team-stats').then((m) => m.TeamStats),
        title: `Team Stats - ${APP_TITLE}`,
      },
      {
        path: 'stats/statistical-feats',
        loadComponent: () =>
          import('./statistical-feats/statistical-feats').then((m) => m.StatisticalFeats),
        title: `Statistical Feats - ${APP_TITLE}`,
      },
      {
        path: 'stats/game-log',
        loadComponent: () => import('./game-log/game-log').then((m) => m.GameLog),
        title: `Game Log - ${APP_TITLE}`,
      },
      {
        path: 'power-rankings',
        loadComponent: () => import('./power-rankings/power-rankings').then((m) => m.PowerRankings),
        title: `Power Rankings - ${APP_TITLE}`,
      },
      {
        path: 'playoffs',
        loadComponent: () => import('./playoffs/playoffs').then((m) => m.Playoffs),
        title: `Playoffs - ${APP_TITLE}`,
      },
    ],
  },
];
