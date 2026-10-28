import { Component, inject } from '@angular/core';
import { MatButton } from '@angular/material/button';
import {
  MAT_DIALOG_DATA,
  MatDialogActions,
  MatDialogClose,
  MatDialogContent,
  MatDialogTitle,
} from '@angular/material/dialog';

/** What the confirm dialog asks, and the label of the button that says yes. */
export interface ConfirmDialogData {
  title: string;
  message: string;
  confirm: string;
  /** Optional lines listed under the message (e.g. what a delete removes). */
  details?: readonly string[];
}

/** A yes / no `MatDialog`: closes with `true` on confirm, `undefined` on Cancel or Escape. */
@Component({
  selector: 'app-confirm-dialog',
  imports: [MatButton, MatDialogActions, MatDialogClose, MatDialogContent, MatDialogTitle],
  template: `
    <h2 mat-dialog-title>{{ data.title }}</h2>
    <mat-dialog-content>
      {{ data.message }}
      @if (data.details?.length) {
        <ul class="confirm-dialog__details">
          @for (line of data.details; track $index) {
            <li>{{ line }}</li>
          }
        </ul>
      }
    </mat-dialog-content>
    <mat-dialog-actions align="end">
      <button mat-button type="button" mat-dialog-close>Cancel</button>
      <button mat-flat-button type="button" [mat-dialog-close]="true">{{ data.confirm }}</button>
    </mat-dialog-actions>
  `,
})
export class ConfirmDialog {
  protected readonly data = inject<ConfirmDialogData>(MAT_DIALOG_DATA);
}
