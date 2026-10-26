import { Component, inject, signal, viewChild } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import {
  FormGroupDirective,
  NonNullableFormBuilder,
  ReactiveFormsModule,
  Validators,
} from '@angular/forms';
import { MatButton } from '@angular/material/button';
import { MatError, MatFormField, MatLabel } from '@angular/material/form-field';
import { MatInput } from '@angular/material/input';
import { AuthService } from '../../auth/auth.service';
import { applyApiErrors } from '../../shared/form-errors';

/** Changes the signed-in Account's password (`StyledPasswordChangeForm`); stays signed in. */
@Component({
  imports: [ReactiveFormsModule, MatButton, MatError, MatFormField, MatInput, MatLabel],
  selector: 'app-password-change',
  styleUrl: './password-change.scss',
  templateUrl: './password-change.html',
})
export class PasswordChange {
  private readonly auth = inject(AuthService);
  private readonly formDirective = viewChild(FormGroupDirective);

  protected readonly changed = signal(false);
  protected readonly errors = signal<string[]>([]);
  protected readonly busy = signal(false);
  protected readonly form = inject(NonNullableFormBuilder).group({
    old_password: ['', [Validators.required]],
    new_password1: ['', [Validators.required]],
    new_password2: ['', [Validators.required]],
  });

  constructor() {
    // The confirmation is about the last submit; editing again starts a new change.
    this.form.valueChanges.pipe(takeUntilDestroyed()).subscribe(() => {
      if (this.form.dirty) {
        this.changed.set(false);
      }
    });
  }

  protected submit(): void {
    if (this.busy()) {
      return;
    }
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }
    this.errors.set([]);
    this.busy.set(true);
    this.auth.changePassword(this.form.getRawValue()).subscribe({
      next: () => {
        this.busy.set(false);
        // Through the directive, so `submitted` clears too and the empty fields show no errors.
        this.formDirective()?.resetForm();
        this.changed.set(true);
      },
      error: (error: unknown) => {
        this.busy.set(false);
        this.errors.set(applyApiErrors(this.form, error));
      },
    });
  }
}
