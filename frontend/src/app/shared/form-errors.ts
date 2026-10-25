// Shows an API failure on a reactive form (shared by the team, match-setup and map forms).
import { FormGroup } from '@angular/forms';
import { ApiError } from '../api/api-client';

/** The alert-line message for a failure that is not an `ApiError` (network, timeout, a bug). */
export const GENERIC_ERROR_MESSAGE = 'Something went wrong. Please try again.';

/**
 * Puts each envelope field error on the matching control (as its `server` error, marked
 * touched so `mat-error` shows it) and returns the messages no control owns, for the
 * alert above the form. A non-`ApiError` gives one generic message.
 */
export function applyApiErrors(form: FormGroup, error: unknown): string[] {
  if (!(error instanceof ApiError)) {
    return [GENERIC_ERROR_MESSAGE];
  }
  const unowned: string[] = [];
  const byField = new Map<string, string[]>();
  for (const item of error.errors) {
    if (item.field && form.contains(item.field)) {
      byField.set(item.field, [...(byField.get(item.field) ?? []), item.message]);
    } else {
      unowned.push(item.message);
    }
  }
  for (const [field, messages] of byField) {
    const control = form.get(field);
    control?.setErrors({ server: messages.join(' ') });
    control?.markAsTouched();
  }
  return unowned;
}
