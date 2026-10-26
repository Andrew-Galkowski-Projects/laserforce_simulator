import { Component, inject, signal } from '@angular/core';
import { NonNullableFormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButton } from '@angular/material/button';
import { MatError, MatFormField, MatHint, MatLabel } from '@angular/material/form-field';
import { MatInput } from '@angular/material/input';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { catchError, map, of, switchMap } from 'rxjs';
import { APP_TITLE } from '../app-title';
import { HOME_URL } from '../auth/auth.guard';
import { AuthService } from '../auth/auth.service';
import { REGISTERED_STATE } from '../login/login';
import { applyApiErrors } from '../shared/form-errors';

/** Invite-only sign-up (`RegisterForm`), open to signed-out visitors only. */
@Component({
  imports: [
    ReactiveFormsModule,
    RouterLink,
    MatButton,
    MatError,
    MatFormField,
    MatHint,
    MatInput,
    MatLabel,
  ],
  selector: 'app-register',
  styleUrl: '../shared/auth-card.scss',
  templateUrl: './register.html',
})
export class Register {
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);

  protected readonly title = APP_TITLE;
  protected readonly errors = signal<string[]>([]);
  protected readonly busy = signal(false);
  protected readonly form = inject(NonNullableFormBuilder).group({
    invite: [
      inject(ActivatedRoute).snapshot.queryParamMap.get('invite') ?? '',
      [Validators.required],
    ],
    email: ['', [Validators.required]],
    password1: ['', [Validators.required]],
    password2: ['', [Validators.required]],
  });

  protected submit(): void {
    if (this.busy()) {
      return;
    }
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }
    const body = this.form.getRawValue();
    this.errors.set([]);
    this.busy.set(true);
    this.auth
      .register(body)
      .pipe(
        // The Account exists once register succeeds, so a failed sign-in is not a form error:
        // it becomes `false`, and they are sent to sign in by hand.
        switchMap(() =>
          this.auth.login(body.email, body.password1).pipe(
            map(() => true),
            catchError(() => of(false)),
          ),
        ),
      )
      .subscribe({
        next: (signedIn) =>
          void (signedIn
            ? this.router.navigateByUrl(HOME_URL)
            : this.router.navigateByUrl('/login', { state: REGISTERED_STATE })),
        error: (error: unknown) => {
          this.busy.set(false);
          this.errors.set(applyApiErrors(this.form, error));
        },
      });
  }
}
