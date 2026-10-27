import { Routes } from '@angular/router';
import { APP_TITLE } from '../app-title';
import { TournamentCreate } from './tournament-create/tournament-create';
import { TournamentDetail } from './tournament-detail/tournament-detail';
import { TournamentList } from './tournament-list/tournament-list';

/** The sandbox Tournaments area, lazily loaded under `/tournaments` by `app.routes.ts`. */
export const TOURNAMENTS_ROUTES: Routes = [
  { path: '', component: TournamentList, title: `Tournaments - ${APP_TITLE}` },
  { path: 'new', component: TournamentCreate, title: `Create Tournament - ${APP_TITLE}` },
  { path: ':id', component: TournamentDetail, title: `Tournament - ${APP_TITLE}` },
];
