import { Routes } from '@angular/router';
import { APP_TITLE } from './app-title';
import { authGuard, signedOutGuard } from './auth/auth.guard';

/**
 * `/login` and `/register` are the only open routes; everything else is a child of the layout-B `Shell`
 * behind `authGuard`. The Shell and every feature area are lazily loaded, so the initial
 * bundle carries no Material layout code.
 */
export const routes: Routes = [
  {
    path: 'login',
    loadComponent: () => import('./login/login').then((m) => m.Login),
    canActivate: [signedOutGuard],
    title: `Sign in - ${APP_TITLE}`,
  },
  {
    path: 'register',
    loadComponent: () => import('./register/register').then((m) => m.Register),
    canActivate: [signedOutGuard],
    title: `Create an account - ${APP_TITLE}`,
  },
  {
    path: '',
    loadComponent: () => import('./shell/shell').then((m) => m.Shell),
    canActivate: [authGuard],
    canActivateChild: [authGuard],
    children: [
      { path: '', pathMatch: 'full', redirectTo: 'teams' },
      {
        path: 'teams',
        loadChildren: () => import('./teams/teams.routes').then((m) => m.TEAMS_ROUTES),
      },
      {
        path: 'matches',
        loadChildren: () => import('./matches/matches.routes').then((m) => m.MATCHES_ROUTES),
      },
      {
        path: 'rounds',
        loadChildren: () => import('./matches/matches.routes').then((m) => m.ROUNDS_ROUTES),
      },
      {
        path: 'players',
        loadChildren: () => import('./players/players.routes').then((m) => m.PLAYERS_ROUTES),
      },
      {
        path: 'account/password',
        loadComponent: () =>
          import('./account/password-change/password-change').then((m) => m.PasswordChange),
        title: `Change password - ${APP_TITLE}`,
      },
      {
        path: 'maps',
        loadChildren: () => import('./maps/maps.routes').then((m) => m.MAPS_ROUTES),
      },
    ],
  },
  { path: '**', redirectTo: '' },
];
