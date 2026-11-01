import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { AuthService } from './auth.service';

/** Every app route but `/login`: a signed-out visitor goes to `/login?returnUrl=<url>`. */
export const authGuard: CanActivateFn = (_route, state) => {
  if (inject(AuthService).isSignedIn()) {
    return true;
  }
  return inject(Router).createUrlTree(['/login'], { queryParams: { returnUrl: state.url } });
};

/**
 * Where a signed-in Account lands when nothing names a page: sign-in, register, this guard. The
 * landing page (spec #460).
 */
export const HOME_URL = '/';

/** `/login` and `/register`: a signed-in visit goes straight to `HOME_URL`. */
export const signedOutGuard: CanActivateFn = () =>
  inject(AuthService).isSignedIn() ? inject(Router).parseUrl(HOME_URL) : true;
