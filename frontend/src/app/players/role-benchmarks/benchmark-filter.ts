import { Component, effect, inject, input } from '@angular/core';
import { NonNullableFormBuilder, ReactiveFormsModule } from '@angular/forms';
import { MatButton } from '@angular/material/button';
import { MatError, MatFormField, MatLabel } from '@angular/material/form-field';
import { MatInput } from '@angular/material/input';
import { ActivatedRoute, Router } from '@angular/router';
import { BenchmarkDisplay, CareerParams } from '../../api/players.models';

/**
 * Reads the `?threshold=&display=` filters (I1). A value the API's strict validator would refuse
 * (not a whole number >= 0, an unknown display) is dropped, so the server default applies.
 */
export function benchmarkParams(query: { get(name: string): string | null }): CareerParams {
  const raw = query.get('threshold');
  const threshold = raw !== null && /^\d+$/.test(raw) ? Number(raw) : undefined;
  const display = query.get('display');
  return {
    threshold,
    display: display === 'mean' || display === 'median' ? display : undefined,
  };
}

/**
 * The *Min rounds in role* / *Show* form shared by Career stats and Role Benchmarks (Django's
 * `benchmark-filter-form`). It shows the values the server used; Apply writes the new ones to
 * the URL, which reloads the screen.
 */
@Component({
  selector: 'app-benchmark-filter',
  imports: [MatButton, MatError, MatFormField, MatInput, MatLabel, ReactiveFormsModule],
  template: `
    <form class="bf" [formGroup]="form" (ngSubmit)="apply()" novalidate>
      <mat-form-field appearance="outline">
        <mat-label>Min rounds in role</mat-label>
        <input
          matInput
          type="number"
          name="threshold"
          formControlName="threshold"
          min="0"
          required
        />
        @if (form.controls.threshold.invalid) {
          <mat-error>Enter 0 or more.</mat-error>
        }
      </mat-form-field>
      <mat-form-field appearance="outline">
        <mat-label>Show</mat-label>
        <select matNativeControl name="display" formControlName="display">
          <option value="mean">Mean</option>
          <option value="median">Median</option>
        </select>
      </mat-form-field>
      <button mat-flat-button type="submit">Apply</button>
    </form>
  `,
  styles: `
    .bf {
      display: flex;
      flex-wrap: wrap;
      gap: 0 1rem;
      align-items: baseline;
    }
  `,
})
export class BenchmarkFilter {
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);

  /** The threshold the server used (`min_rounds`). */
  readonly minRounds = input.required<number>();
  readonly display = input.required<BenchmarkDisplay>();

  /** The template's `min="0"` and `required` attributes are the threshold's validators. */
  protected readonly form = inject(NonNullableFormBuilder).group({
    threshold: null as number | null,
    display: 'mean' as BenchmarkDisplay,
  });

  constructor() {
    effect(() => this.form.setValue({ threshold: this.minRounds(), display: this.display() }));
  }

  protected apply(): void {
    this.form.markAllAsTouched();
    if (this.form.invalid) return;
    const { threshold, display } = this.form.getRawValue();
    void this.router.navigate([], {
      relativeTo: this.route,
      queryParams: { threshold, display },
    });
  }
}
