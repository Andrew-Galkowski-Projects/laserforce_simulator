// The Teams screens' confirmation snackbar: one action label and one duration.
import { inject } from '@angular/core';
import { MatSnackBar } from '@angular/material/snack-bar';

/** Returns a function that shows `message` in a snackbar with a "Close" action for 5 s. */
export function injectNotify(): (message: string) => void {
  const snackBar = inject(MatSnackBar);
  return (message) => snackBar.open(message, 'Close', { duration: 5000 });
}
