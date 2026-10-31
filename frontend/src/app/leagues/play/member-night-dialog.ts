import { Component, computed, inject, signal } from '@angular/core';
import { MatButton } from '@angular/material/button';
import { MatCheckbox } from '@angular/material/checkbox';
import {
  MAT_DIALOG_DATA,
  MatDialogActions,
  MatDialogClose,
  MatDialogContent,
  MatDialogTitle,
} from '@angular/material/dialog';
import { MemberNightSite } from '../../api/leagues.models';

/** The viable Sites the dialog offers (`controls.member_night_sites`). */
export interface MemberNightDialogData {
  sites: MemberNightSite[];
}

/**
 * The member-night setup (spec #454), ported from the `topnav_play.html` set-up form: an "All
 * Sites" toggle and one box per viable Site, every one checked. Closes with the checked Sites'
 * names on Confirm, `undefined` on Cancel or Escape.
 */
@Component({
  selector: 'app-member-night-dialog',
  imports: [
    MatButton,
    MatCheckbox,
    MatDialogActions,
    MatDialogClose,
    MatDialogContent,
    MatDialogTitle,
  ],
  template: `
    <h2 mat-dialog-title>Set Up Member Night</h2>
    <mat-dialog-content class="member-night-dialog">
      <mat-checkbox
        class="member-night-dialog__all"
        [checked]="allChecked()"
        [indeterminate]="mixed()"
        (change)="toggleAll($event.checked)"
        >All Sites</mat-checkbox
      >
      @for (entry of data.sites; track $index) {
        <mat-checkbox [checked]="checked()[$index]" (change)="toggle($index, $event.checked)"
          >{{ entry.site || '(no site)' }} ({{ entry.count }})</mat-checkbox
        >
      } @empty {
        <p class="member-night-dialog__empty">No Site has enough players (need 12+).</p>
      }
    </mat-dialog-content>
    <mat-dialog-actions align="end">
      <button mat-button type="button" [mat-dialog-close]="undefined">Cancel</button>
      <button
        mat-flat-button
        type="button"
        [disabled]="chosen().length === 0"
        [mat-dialog-close]="chosen()"
      >
        Confirm — Set Up Member Night
      </button>
    </mat-dialog-actions>
  `,
  styles: `
    .member-night-dialog {
      display: flex;
      flex-direction: column;
    }
    .member-night-dialog__all {
      font-weight: 600;
    }
  `,
})
export class MemberNightDialog {
  protected readonly data = inject<MemberNightDialogData>(MAT_DIALOG_DATA);

  /** One flag per Site, in `data.sites` order; every Site starts checked. */
  protected readonly checked = signal(this.data.sites.map(() => true));

  protected readonly allChecked = computed(() => this.checked().every(Boolean));
  protected readonly mixed = computed(() => {
    const flags = this.checked();
    return flags.some(Boolean) && !flags.every(Boolean);
  });

  /** The checked Sites' names, sent as the `sites` field. */
  protected readonly chosen = computed(() =>
    this.data.sites.filter((_, i) => this.checked()[i]).map((entry) => entry.site),
  );

  protected toggle(index: number, on: boolean): void {
    this.checked.update((flags) => flags.map((flag, i) => (i === index ? on : flag)));
  }

  protected toggleAll(on: boolean): void {
    this.checked.update((flags) => flags.map(() => on));
  }
}
