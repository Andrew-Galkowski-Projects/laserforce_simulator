import { Routes } from '@angular/router';
import { APP_TITLE } from '../app-title';
import { PlayerAdd } from './player-add/player-add';
import { PlayerEdit } from './player-edit/player-edit';
import { TeamCreate } from './team-create/team-create';
import { TeamDetail } from './team-detail/team-detail';
import { TeamEdit } from './team-edit/team-edit';
import { TeamList } from './team-list/team-list';
import { TeamSlots } from './team-slots/team-slots';

/** The Teams feature, lazily loaded under `/teams` by `app.routes.ts`. */
export const TEAMS_ROUTES: Routes = [
  { path: '', component: TeamList, title: `Teams - ${APP_TITLE}` },
  { path: 'new', component: TeamCreate, title: `Create Team - ${APP_TITLE}` },
  { path: ':id', component: TeamDetail, title: `Team - ${APP_TITLE}` },
  { path: ':id/edit', component: TeamEdit, title: `Edit Team - ${APP_TITLE}` },
  { path: ':id/slots', component: TeamSlots, title: `Assign Slots - ${APP_TITLE}` },
  { path: ':id/players/new', component: PlayerAdd, title: `Add Player - ${APP_TITLE}` },
  {
    path: ':id/players/:playerId/edit',
    component: PlayerEdit,
    title: `Edit Player - ${APP_TITLE}`,
  },
];
