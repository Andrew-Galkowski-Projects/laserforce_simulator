import { Component, DestroyRef, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormControl, FormGroup, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatAnchor, MatButton } from '@angular/material/button';
import { MatDialog } from '@angular/material/dialog';
import { MatError, MatFormField, MatLabel } from '@angular/material/form-field';
import { MatInput } from '@angular/material/input';
import { Router, RouterLink } from '@angular/router';
import { EMPTY, Observable, expand, filter, reduce, switchMap } from 'rxjs';
import { ArenaMap } from '../../api/maps.models';
import { MapsService } from '../../api/maps.service';
import { ConfirmDialog, ConfirmDialogData } from '../../shared/confirm-dialog';
import { applyApiErrors } from '../../shared/form-errors';
import { apiMessage } from '../shared/api-message';

/** The API's page-size cap; the list follows further pages past it. */
const PAGE_SIZE = 100;

/** `MapUploadForm.name`'s `max_length`. */
const NAME_MAX_LENGTH = 100;

/** Django's field messages, so a client-side check reads the same as the server's. */
const REQUIRED_MESSAGE = 'This field is required.';

/**
 * `/maps`: one card per Arena map (thumbnail, name, size, Default and zone-config badges, Open
 * Editor, Delete), and the Upload form that lands on the new map's editor. Arena maps are shared
 * reference data (ADR-0038): every Account sees and manages every map.
 */
@Component({
  selector: 'app-map-list',
  imports: [
    ReactiveFormsModule,
    RouterLink,
    MatAnchor,
    MatButton,
    MatError,
    MatFormField,
    MatInput,
    MatLabel,
  ],
  templateUrl: './map-list.html',
  styleUrl: './map-list.scss',
})
export class MapList {
  private readonly maps = inject(MapsService);
  private readonly router = inject(Router);
  private readonly dialog = inject(MatDialog);
  private readonly destroyRef = inject(DestroyRef);

  /** The maps, oldest first; `null` until the list loads. */
  protected readonly list = signal<ArenaMap[] | null>(null);
  protected readonly loadError = signal(false);
  /** Map id -> object URL of its original image (only maps whose fetch succeeded). */
  protected readonly thumbs = signal<ReadonlyMap<number, string>>(new Map());
  /** The outcome of the last delete: a success line, or the server's refusal. */
  protected readonly deleted = signal<string | null>(null);
  protected readonly deleteError = signal<string | null>(null);

  protected readonly nameMaxLength = NAME_MAX_LENGTH;
  protected readonly uploadForm = new FormGroup({
    name: new FormControl('', {
      nonNullable: true,
      validators: [Validators.required, Validators.maxLength(NAME_MAX_LENGTH)],
    }),
    image: new FormControl<File | null>(null, { validators: [Validators.required] }),
  });
  protected readonly uploading = signal(false);
  protected readonly uploadErrors = signal<string[]>([]);

  constructor() {
    this.destroyRef.onDestroy(() => {
      for (const src of this.thumbs().values()) URL.revokeObjectURL(src);
    });
    this.listAll()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (maps) => {
          this.list.set(maps);
          maps.filter((m) => m.image !== null).forEach((m) => this.loadThumb(m.id));
        },
        error: () => this.loadError.set(true),
      });
  }

  /** Every Arena map, oldest first: page 1, then each further page `meta.total_pages` names. */
  private listAll(): Observable<ArenaMap[]> {
    const params = { page_size: PAGE_SIZE, ordering: 'created_at' };
    return this.maps.list(params).pipe(
      expand(({ meta }) =>
        meta.page < meta.total_pages ? this.maps.list({ ...params, page: meta.page + 1 }) : EMPTY,
      ),
      reduce((all, page) => [...all, ...page.items], [] as ArenaMap[]),
    );
  }

  /** The message under a field: the server's, else the client-side check's, once touched. */
  protected fieldError(field: 'name' | 'image'): string | null {
    const control = this.uploadForm.controls[field];
    if (!control.touched || control.valid) return null;
    const server = control.getError('server') as string | null;
    if (server) return server;
    if (control.hasError('required')) return REQUIRED_MESSAGE;
    const max = control.getError('maxlength') as { actualLength: number } | null;
    if (max) {
      return `Ensure this value has at most ${NAME_MAX_LENGTH} characters (it has ${max.actualLength}).`;
    }
    return null;
  }

  protected pickFile(event: Event): void {
    const file = (event.target as HTMLInputElement).files?.[0] ?? null;
    const control = this.uploadForm.controls.image;
    control.setValue(file);
    control.markAsTouched();
  }

  /** Uploads, then lands on the new map's editor; field errors render inline. */
  protected upload(): void {
    this.uploadForm.markAllAsTouched();
    const { name, image } = this.uploadForm.getRawValue();
    if (this.uploadForm.invalid || image === null) return;
    this.uploading.set(true);
    this.uploadErrors.set([]);
    this.maps.upload({ name, image }).subscribe({
      next: (map) => void this.router.navigate(['/maps', map.id, 'editor']),
      error: (error: unknown) => {
        this.uploading.set(false);
        this.uploadErrors.set(applyApiErrors(this.uploadForm, error));
      },
    });
  }

  /** Asks first; on yes deletes the map and removes its card, or shows the server's refusal. */
  protected remove(map: ArenaMap): void {
    const data: ConfirmDialogData = {
      title: 'Delete map',
      message:
        `Delete “${map.name}”? This permanently removes its zone config, bases, elevation, ` +
        'walls, and sight lines. Saved game rounds keep their results but lose the map link. ' +
        'This cannot be undone.',
      confirm: 'Delete',
    };
    this.dialog
      .open<ConfirmDialog, ConfirmDialogData, boolean>(ConfirmDialog, { data })
      .afterClosed()
      .pipe(
        filter((yes) => yes === true),
        switchMap(() => this.maps.delete(map.id)),
      )
      .subscribe({
        next: () => {
          this.list.update((maps) => (maps ?? []).filter((m) => m.id !== map.id));
          this.dropThumb(map.id);
          this.deleteError.set(null);
          this.deleted.set(`Deleted map “${map.name}”.`);
        },
        error: (err: unknown) => {
          const reason = apiMessage(err, 'Something went wrong.');
          this.deleted.set(null);
          this.deleteError.set(`Could not delete “${map.name}”: ${reason}`);
        },
      });
  }

  /** A failed fetch leaves the card on its "No image" placeholder. */
  private loadThumb(id: number): void {
    this.maps
      .image(id)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (blob) =>
          this.thumbs.update((thumbs) => new Map(thumbs).set(id, URL.createObjectURL(blob))),
        error: () => undefined,
      });
  }

  private dropThumb(id: number): void {
    const src = this.thumbs().get(id);
    if (src === undefined) return;
    URL.revokeObjectURL(src);
    this.thumbs.update((thumbs) => {
      const next = new Map(thumbs);
      next.delete(id);
      return next;
    });
  }
}
