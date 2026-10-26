import { Component, inject, signal } from '@angular/core';
import { NonNullableFormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButton } from '@angular/material/button';
import { MatError, MatFormField, MatLabel } from '@angular/material/form-field';
import { MatInput } from '@angular/material/input';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { APP_TITLE } from '../app-title';
import { HOME_URL } from '../auth/auth.guard';
import { AuthService } from '../auth/auth.service';

/** The navigation state `/login` reads (`Router.navigateByUrl(url, { state })`). */
export interface LoginNavigationState {
  /** Set by `/register` when the Account was created but signing it in failed. */
  registered?: boolean;
}

export const REGISTERED_STATE: LoginNavigationState = { registered: true };

/** The sign-in page (the only route open to a signed-out visitor). */
@Component({
  imports: [ReactiveFormsModule, RouterLink, MatButton, MatError, MatFormField, MatInput, MatLabel],
  selector: 'app-login',
  styleUrl: '../shared/auth-card.scss',
  templateUrl: './login.html',
})
export class Login {
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);

  protected readonly title = APP_TITLE;
  protected readonly registered =
    (this.router.currentNavigation()?.extras.state as LoginNavigationState | undefined)
      ?.registered === true;
  protected readonly error = signal<string | null>(null);
  protected readonly busy = signal(false);
  protected readonly form = inject(NonNullableFormBuilder).group({
    email: ['', [Validators.required]],
    password: ['', [Validators.required]],
  });

  protected submit(): void {
    if (this.busy()) {
      return;
    }
    const { email, password } = this.form.getRawValue();
    this.error.set(null);
    this.busy.set(true);
    this.auth.login(email, password).subscribe({
      next: () => void this.router.navigateByUrl(this.returnUrl()),
      error: () => {
        this.busy.set(false);
        this.error.set('Invalid email or password.');
      },
    });
  }

  /** The `returnUrl` query param when it is an in-app path (`/x`, never `//x` or `/\x`). */
  private returnUrl(): string {
    const url = this.route.snapshot.queryParamMap.get('returnUrl');
    return url && /^\/(?![/\\])/.test(url) ? url : HOME_URL;
  }
}
