import { HttpErrorResponse, HttpInterceptorFn, HttpRequest } from '@angular/common/http';
import { inject } from '@angular/core';
import { Router } from '@angular/router';
import { Observable, catchError, switchMap, throwError } from 'rxjs';
import { environment } from '../../environments/environment';
import { AuthService } from './auth.service';

const API_PREFIX = `${environment.apiBaseUrl}/`;

function withBearer<T>(req: HttpRequest<T>, token: string | null): HttpRequest<T> {
  return token ? req.clone({ setHeaders: { Authorization: `Bearer ${token}` } }) : req;
}

function isUnauthorized(error: unknown): error is HttpErrorResponse {
  return error instanceof HttpErrorResponse && error.status === 401;
}

/**
 * ADR-0041 on the client:
 * - `token/*` calls carry the refresh cookie (`withCredentials`) and no Bearer.
 * - Other `apiBaseUrl` calls carry `Authorization: Bearer <token>` when one is held.
 * - A 401 refreshes once (single-flight, shared by concurrent 401s) and replays
 *   the request once; a failed refresh or a second 401 signs out and lands on
 *   `/login?returnUrl=...`, and the caller still sees the 401.
 * - Anything outside `apiBaseUrl` passes through untouched.
 */
export const authInterceptor: HttpInterceptorFn = (req, next) => {
  if (!req.url.startsWith(API_PREFIX)) {
    return next(req);
  }
  if (req.url.slice(API_PREFIX.length).startsWith('token/')) {
    // The refresh cookie (Path=/api/token/) only needs to travel here.
    return next(req.clone({ withCredentials: true }));
  }

  const auth = inject(AuthService);
  const router = inject(Router);

  const sendToLogin = (error: HttpErrorResponse): Observable<never> => {
    auth.clearSession();
    void router.navigate(['/login'], { queryParams: { returnUrl: router.url } });
    return throwError(() => error);
  };

  return next(withBearer(req, auth.accessToken())).pipe(
    catchError((error: unknown) => {
      if (!isUnauthorized(error)) {
        return throwError(() => error);
      }
      return auth.refresh().pipe(
        catchError(() => sendToLogin(error)),
        switchMap((token) =>
          next(withBearer(req, token)).pipe(
            catchError((replayError: unknown) =>
              isUnauthorized(replayError)
                ? sendToLogin(replayError)
                : throwError(() => replayError),
            ),
          ),
        ),
      );
    }),
  );
};
