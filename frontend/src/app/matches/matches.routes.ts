import { Routes } from '@angular/router';
import { APP_TITLE } from '../app-title';
import { BatchSimulate } from './batch-simulate/batch-simulate';
import { CompareRounds } from './compare-rounds/compare-rounds';
import { MatchDetail } from './match-detail/match-detail';
import { MatchList } from './match-list/match-list';
import { MissileLog } from './missile-log/missile-log';
import { Playback } from './playback/playback';
import { RoundCharts } from './round-charts/round-charts';
import { RoundDetail } from './round-detail/round-detail';
import { RoundEvents } from './round-events/round-events';
import { RoundHeatmap } from './round-heatmap/round-heatmap';
import { SetupForm } from './setup-form/setup-form';
import { SetupKind } from './shared/setup-kind';

/**
 * The Matches area, lazily loaded under `/matches` by `app.routes.ts`. `new` and `batch` (Batch
 * Simulate) come before `:id` so neither is read as a Match id.
 */
export const MATCHES_ROUTES: Routes = [
  { path: '', component: MatchList, title: `Match History - ${APP_TITLE}` },
  {
    path: 'new',
    component: SetupForm,
    data: { kind: 'match' satisfies SetupKind },
    title: `New Match - ${APP_TITLE}`,
  },
  { path: 'batch', component: BatchSimulate, title: `Batch Simulate - ${APP_TITLE}` },
  { path: ':id', component: MatchDetail, title: `Match - ${APP_TITLE}` },
];

/**
 * Single Rounds, lazily loaded under `/rounds` from this same module (one chunk for the area).
 * There is no `/rounds` list: Single Rounds are a section of Match History. `compare` (Compare
 * Rounds), `:id/events` (the Event log), `:id/playback` (Playback), `:id/charts` (the event
 * charts), `:id/missile-log` (the Missile log) and `:id/heatmap` (the Movement heatmap) come
 * before `:id`.
 */
export const ROUNDS_ROUTES: Routes = [
  {
    path: 'new',
    component: SetupForm,
    data: { kind: 'round' satisfies SetupKind },
    title: `New Single Round - ${APP_TITLE}`,
  },
  { path: 'compare', component: CompareRounds, title: `Compare Rounds - ${APP_TITLE}` },
  { path: ':id/events', component: RoundEvents, title: `Event Log - ${APP_TITLE}` },
  { path: ':id/playback', component: Playback, title: `Playback - ${APP_TITLE}` },
  { path: ':id/charts', component: RoundCharts, title: `Charts - ${APP_TITLE}` },
  { path: ':id/missile-log', component: MissileLog, title: `Missile Log - ${APP_TITLE}` },
  { path: ':id/heatmap', component: RoundHeatmap, title: `Movement Heatmap - ${APP_TITLE}` },
  { path: ':id', component: RoundDetail, title: `Round - ${APP_TITLE}` },
];
