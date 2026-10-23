import { Routes } from '@angular/router';
import { APP_TITLE } from '../app-title';
import { MatchDetail } from './match-detail/match-detail';
import { MatchList } from './match-list/match-list';
import { RoundDetail } from './round-detail/round-detail';
import { SetupForm } from './setup-form/setup-form';
import { SetupKind } from './shared/setup-kind';

/**
 * The Matches area, lazily loaded under `/matches` by `app.routes.ts`. `new` comes before `:id`
 * so it is never read as a Match id.
 */
export const MATCHES_ROUTES: Routes = [
  { path: '', component: MatchList, title: `Match History - ${APP_TITLE}` },
  {
    path: 'new',
    component: SetupForm,
    data: { kind: 'match' satisfies SetupKind },
    title: `New Match - ${APP_TITLE}`,
  },
  { path: ':id', component: MatchDetail, title: `Match - ${APP_TITLE}` },
];

/**
 * Single Rounds, lazily loaded under `/rounds` from this same module (one chunk for the area).
 * There is no `/rounds` list: Single Rounds are a section of Match History.
 */
export const ROUNDS_ROUTES: Routes = [
  {
    path: 'new',
    component: SetupForm,
    data: { kind: 'round' satisfies SetupKind },
    title: `New Single Round - ${APP_TITLE}`,
  },
  { path: ':id', component: RoundDetail, title: `Round - ${APP_TITLE}` },
];
