import { Routes } from '@angular/router';
import { APP_TITLE } from './app-title';
import { Home } from './home/home';

export const routes: Routes = [
  { path: '', component: Home, title: APP_TITLE },
  { path: '**', redirectTo: '' },
];
