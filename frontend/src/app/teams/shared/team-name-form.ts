import { Component, effect, inject, input, output, signal } from '@angular/core';
import { NonNullableFormBuilder, ReactiveFormsModule } from '@angular/forms';
import { MatButton } from '@angular/material/button';
import { MatError, MatFormField, MatLabel } from '@angular/material/form-field';
import { MatInput } from '@angular/material/input';
import { RouterLink } from '@angular/router';
import { applyApiErrors } from '../../shared/form-errors';

/** The Team name form shared by Create Team and Edit Team (`TeamForm`: `name`). */
@Component({
  selector: 'app-team-name-form',
  imports: [ReactiveFormsModule, RouterLink, MatButton, MatError, MatFormField, MatInput, MatLabel],
  styleUrl: './team-form.scss',
  template: `
    <form [formGroup]="form" (ngSubmit)="submitted.emit(form.getRawValue().name)" novalidate>
      @for (message of nonFieldErrors(); track $index) {
        <p class="team-form__error" role="alert">{{ message }}</p>
      }
      <mat-form-field appearance="outline">
        <mat-label>Name</mat-label>
        <input matInput formControlName="name" autocomplete="off" />
        @if (form.controls.name.getError('server'); as message) {
          <mat-error>{{ message }}</mat-error>
        }
      </mat-form-field>
      <div class="team-form__actions">
        <button mat-flat-button type="submit" [disabled]="busy()">{{ submitLabel() }}</button>
        <a mat-button [routerLink]="cancelLink()">Cancel</a>
      </div>
    </form>
  `,
})
export class TeamNameForm {
  readonly submitLabel = input.required<string>();
  readonly cancelLink = input.required<string>();
  readonly initialName = input('');
  readonly busy = input(false);
  /** The failure of the last save (`null` before one or after a success). */
  readonly error = input<unknown>(null);
  readonly submitted = output<string>();

  protected readonly form = inject(NonNullableFormBuilder).group({ name: [''] });
  protected readonly nonFieldErrors = signal<string[]>([]);

  constructor() {
    effect(() => this.form.setValue({ name: this.initialName() }));
    effect(() => {
      const error = this.error();
      this.nonFieldErrors.set(error === null ? [] : applyApiErrors(this.form, error));
    });
  }
}
