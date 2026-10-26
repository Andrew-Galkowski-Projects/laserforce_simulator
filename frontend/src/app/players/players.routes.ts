import { Routes } from '@angular/router';
import { APP_TITLE } from '../app-title';
import { PlayerCareer } from './player-career/player-career';
import { PlayerHeadToHead } from './player-head-to-head/player-head-to-head';
import { PlayerList } from './player-list/player-list';
import { RoleBenchmarks } from './role-benchmarks/role-benchmarks';

/**
 * `/players` (ANG-06-8). `benchmarks` and `head-to-head` come before `:id/stats` so neither
 * reads as an id.
 */
export const PLAYERS_ROUTES: Routes = [
  { path: '', component: PlayerList, title: `Players - ${APP_TITLE}` },
  { path: 'benchmarks', component: RoleBenchmarks, title: `Role Benchmarks - ${APP_TITLE}` },
  {
    path: 'head-to-head',
    component: PlayerHeadToHead,
    title: `Player Head-to-Head - ${APP_TITLE}`,
  },
  { path: ':id/stats', component: PlayerCareer, title: `Career Stats - ${APP_TITLE}` },
];
