import { Routes } from '@angular/router';
import { APP_TITLE } from './app-title';
import { Home } from './home/home';
import { Login } from './login/login';

export const routes: Routes = [
  { path: '', component: Home, title: APP_TITLE },
  { path: 'login', component: Login, title: `Sign in - ${APP_TITLE}` },
  { path: '**', redirectTo: '' },
];
