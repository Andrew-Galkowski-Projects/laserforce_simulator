import { Component, inject, signal } from '@angular/core';
import { NonNullableFormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButton } from '@angular/material/button';
import { MatError, MatFormField, MatLabel } from '@angular/material/form-field';
import { MatInput } from '@angular/material/input';
import { ActivatedRoute, Router } from '@angular/router';
import { APP_TITLE } from '../app-title';
import { AuthService } from '../auth/auth.service';

/** Where a sign-in lands when there is no usable `returnUrl`. */
const HOME = '/teams';

/** The sign-in page (the only route open to a signed-out visitor). */
@Component({
  imports: [ReactiveFormsModule, MatButton, MatError, MatFormField, MatInput, MatLabel],
  selector: 'app-login',
  styleUrl: './login.scss',
  templateUrl: './login.html',
})
export class Login {
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);

  protected readonly title = APP_TITLE;
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
    return url && /^\/(?![/\\])/.test(url) ? url : HOME;
  }
}
