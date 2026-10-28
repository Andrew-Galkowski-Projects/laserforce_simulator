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
    ],
  },
];
