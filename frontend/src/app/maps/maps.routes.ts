import { Routes } from '@angular/router';
import { APP_TITLE } from '../app-title';
import { MapEditor } from './map-editor/map-editor';
import { confirmLeaveMapEditor } from './map-editor/map-editor.guard';
import { MapList } from './map-list/map-list';

/** The Arena maps area, lazily loaded under `/maps` by `app.routes.ts`. */
export const MAPS_ROUTES: Routes = [
  { path: '', component: MapList, title: `Arena Maps - ${APP_TITLE}` },
  {
    path: ':id/editor',
    component: MapEditor,
    title: `Map Editor - ${APP_TITLE}`,
    canDeactivate: [confirmLeaveMapEditor],
  },
];
