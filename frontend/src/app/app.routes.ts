import { Routes } from '@angular/router';
import { APP_TITLE } from './app-title';
import { Home } from './home/home';

export const routes: Routes = [
  { path: '', component: Home, title: APP_TITLE },
  // ANG-04b prototype (never merged): visual style samples.
  {
    path: 'prototype/style-samples',
    loadComponent: () =>
      import('./prototype/style-samples/style-samples').then((m) => m.StyleSamples),
    title: `Style samples | ${APP_TITLE}`,
  },
  { path: '**', redirectTo: '' },
];
