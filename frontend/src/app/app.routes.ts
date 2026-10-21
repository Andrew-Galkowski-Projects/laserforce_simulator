import { Routes } from '@angular/router';
import { APP_TITLE } from './app-title';
import { authGuard, signedOutGuard } from './auth/auth.guard';
import { Shell } from './shell/shell';

/**
 * `/login` is the only open route; everything else is a child of the layout-B `Shell`
 * behind `authGuard`. Feature areas are lazily loaded routes files.
 */
export const routes: Routes = [
  {
    path: 'login',
    loadComponent: () => import('./login/login').then((m) => m.Login),
    canActivate: [signedOutGuard],
    title: `Sign in - ${APP_TITLE}`,
  },
  {
    path: '',
    component: Shell,
    canActivate: [authGuard],
    canActivateChild: [authGuard],
    children: [
      { path: '', pathMatch: 'full', redirectTo: 'teams' },
      {
        path: 'teams',
        loadChildren: () => import('./teams/teams.routes').then((m) => m.TEAMS_ROUTES),
      },
    ],
  },
  { path: '**', redirectTo: '' },
];
