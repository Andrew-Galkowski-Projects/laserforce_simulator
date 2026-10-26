import { Routes } from '@angular/router';
import { APP_TITLE } from '../app-title';
import { PlayerCareer } from './player-career/player-career';
import { PlayerList } from './player-list/player-list';
import { RoleBenchmarks } from './role-benchmarks/role-benchmarks';

/** `/players` (ANG-06-8). `benchmarks` comes before `:id/stats` so it never reads as an id. */
export const PLAYERS_ROUTES: Routes = [
  { path: '', component: PlayerList, title: `Players - ${APP_TITLE}` },
  { path: 'benchmarks', component: RoleBenchmarks, title: `Role Benchmarks - ${APP_TITLE}` },
  { path: ':id/stats', component: PlayerCareer, title: `Career Stats - ${APP_TITLE}` },
];
