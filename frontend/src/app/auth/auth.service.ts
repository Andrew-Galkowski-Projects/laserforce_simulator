import { HttpContext } from '@angular/common/http';
import { Injectable, computed, inject, signal } from '@angular/core';
import { Router } from '@angular/router';
import {
  Observable,
  catchError,
  finalize,
  firstValueFrom,
  map,
  of,
  share,
  switchMap,
  tap,
} from 'rxjs';
import { ApiClient } from '../api/api-client';
import { SKIP_AUTH_REFRESH, TOKEN_PATH } from './auth.tokens';
import { RegisterInput, User } from './user';

interface AccessToken {
  access: string;
}

/**
 * The signed-in Account (ADR-0041): the access token lives only in memory;
 * the refresh token only in the httpOnly `refresh_token` cookie.
 */
@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly api = inject(ApiClient);
  private readonly router = inject(Router);
  private readonly user = signal<User | null>(null);
  private token: string | null = null;
  private inflightRefresh: Observable<string> | null = null;

  readonly currentUser = this.user.asReadonly();
  readonly isSignedIn = computed(() => this.user() !== null);

  accessToken(): string | null {
    return this.token;
  }

  /** Signs in. If loading the user fails, the half-made session is cleared and the error rethrown. */
  login(email: string, password: string): Observable<User> {
    return this.api.post<AccessToken>(TOKEN_PATH, { email, password }).pipe(
      tap(({ access }) => (this.token = access)),
      switchMap(() => this.loadUser()),
      tap({ error: () => this.clearSession() }),
    );
  }

  /**
   * Trades the refresh cookie for a new access token. Single-flight: while one
   * request is in flight every caller shares it, so the rotating cookie is
   * never spent twice (a second use would be blacklisted).
   */
  refresh(): Observable<string> {
    this.inflightRefresh ??= this.api.post<AccessToken>(`${TOKEN_PATH}refresh/`, null).pipe(
      map(({ access }) => access),
      tap((access) => (this.token = access)),
      finalize(() => (this.inflightRefresh = null)),
      share(),
    );
    return this.inflightRefresh;
  }

  /** On app start: one silent refresh, then the user. Never rejects, never navigates. */
  async restoreSession(): Promise<void> {
    try {
      await firstValueFrom(this.refresh());
      await firstValueFrom(this.loadUser());
    } catch {
      this.clearSession();
    }
  }

  /** Blacklists the refresh cookie; the session is forgotten even if the call fails. */
  logout(): Observable<void> {
    return this.api.post<void>(`${TOKEN_PATH}logout/`, null).pipe(
      catchError(() => of(undefined)),
      finalize(() => {
        this.clearSession();
        void this.router.navigateByUrl('/login');
      }),
    );
  }

  /** Creates the Account. Does not sign in: call `login` afterwards. */
  register(body: RegisterInput): Observable<User> {
    return this.api.post<User>('accounts/register/', body);
  }

  /**
   * `GET accounts/me/` with the token just obtained, and sets `currentUser`.
   * A 401 here comes straight back (no second refresh, no redirect).
   */
  private loadUser(): Observable<User> {
    const context = new HttpContext().set(SKIP_AUTH_REFRESH, true);
    return this.api
      .get<User>('accounts/me/', undefined, { context })
      .pipe(tap((user) => this.user.set(user)));
  }

  /** Forgets the access token and the user (no request, no navigation). */
  clearSession(): void {
    this.token = null;
    this.user.set(null);
  }
}
