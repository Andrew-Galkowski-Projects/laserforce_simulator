import { Component, DestroyRef, computed, effect, inject, signal, untracked } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatAnchor, MatButton } from '@angular/material/button';
import { MatDialog } from '@angular/material/dialog';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { Observable, Subscription, map, of } from 'rxjs';
import { MapsService } from '../../api/maps.service';
import { ConfirmDialog, ConfirmDialogData } from '../../shared/confirm-dialog';
import { HeatmapMode } from '../heatmap/heatmap';
import { SightLinesMode } from '../sight-lines/sight-lines';
import { ZonesBasesMode } from '../zones-bases/zones-bases';
import { MapCanvas } from './map-canvas';
import { EditorMode, MapEditorStore, ZONE_SIZES } from './map-editor.store';
import { MapStage } from './map-stage';

/** The mode toggle, in the Django editor's order. */
const MODES: readonly { mode: EditorMode; label: string }[] = [
  { mode: 'zones', label: 'Zones & Bases' },
  { mode: 'sight-lines', label: 'Sight Lines' },
  { mode: 'heatmap', label: 'Heatmap' },
];

/** The colour legend: each entry's swatch is its `--lf-map-*` token. */
const LEGEND: readonly { label: string; token: string }[] = [
  { label: 'High wall', token: '--lf-map-wall-high' },
  { label: 'Floor', token: '--lf-map-floor' },
  { label: 'Low wall', token: '--lf-map-wall-low' },
  { label: 'Windowed wall', token: '--lf-map-wall-windowed' },
  { label: 'Red spawn', token: '--lf-map-spawn-red' },
  { label: 'Blue spawn', token: '--lf-map-spawn-blue' },
  { label: 'Elevation', token: '--lf-map-elevation' },
];

/**
 * `/maps/:id/editor`: the editor shell. The map's name and a link back to the list, the
 * Zone-size picker, the mode toggle (one mode component at a time, each reading and writing the
 * shared `MapEditorStore`), the processed image under the canvas host, the original image and
 * the colour legend. A Zone-size change or leaving the route asks first while anything is
 * unsaved (`confirmDiscard`, also behind the route's `canDeactivate`).
 *
 * `MapEditorStore` and `MapCanvas` are provided here, so each editor visit gets its own.
 */
@Component({
  selector: 'app-map-editor',
  imports: [
    MatAnchor,
    MatButton,
    RouterLink,
    MapStage,
    ZonesBasesMode,
    SightLinesMode,
    HeatmapMode,
  ],
  providers: [MapEditorStore, MapCanvas],
  templateUrl: './map-editor.html',
  styleUrl: './map-editor.scss',
})
export class MapEditor {
  protected readonly store = inject(MapEditorStore);
  private readonly maps = inject(MapsService);
  private readonly dialog = inject(MatDialog);

  protected readonly zoneSizes = ZONE_SIZES;
  protected readonly modes = MODES;
  protected readonly legend = LEGEND;

  /** The processed / original images' object URLs, once fetched. */
  protected readonly processedUrl = signal<string | null>(null);
  protected readonly originalUrl = signal<string | null>(null);
  protected readonly processedError = signal(false);
  protected readonly originalError = signal(false);

  private readonly mapId = computed(() => this.store.map()?.id ?? null);
  private imageSubs: Subscription[] = [];

  constructor() {
    inject(ActivatedRoute)
      .paramMap.pipe(takeUntilDestroyed())
      .subscribe((params) => this.store.open(Number(params.get('id'))));

    effect(() => {
      const id = this.mapId();
      untracked(() => this.loadImages(id));
    });
    inject(DestroyRef).onDestroy(() => {
      this.imageSubs.forEach((s) => s.unsubscribe());
      this.dropImages();
    });
  }

  /**
   * Resolves true at once when nothing is unsaved; otherwise asks through the confirm dialog
   * (`what` names the action, e.g. "Changing the Zone size") and resolves to the answer.
   */
  confirmDiscard(what: string): Observable<boolean> {
    if (!this.store.dirty()) return of(true);
    const data: ConfirmDialogData = {
      title: 'Discard unsaved changes?',
      message: `${what} discards your unsaved edits to this map.`,
      confirm: 'Discard',
    };
    return this.dialog
      .open<ConfirmDialog, ConfirmDialogData, boolean>(ConfirmDialog, { data })
      .afterClosed()
      .pipe(map((yes) => yes === true));
  }

  /** The picker changed: switch Zone size, after asking while dirty (Cancel restores it). */
  protected pickZoneSize(event: Event): void {
    const select = event.target as HTMLSelectElement;
    const size = Number(select.value);
    this.confirmDiscard('Changing the Zone size').subscribe((yes) => {
      if (yes) {
        this.store.changeZoneSize(size);
      } else {
        select.value = String(this.store.zoneSize());
      }
    });
  }

  /** Fetches both images of map `id` as Blobs into object URLs (revoked on a new map / destroy). */
  private loadImages(id: number | null): void {
    this.imageSubs.forEach((s) => s.unsubscribe());
    this.dropImages();
    this.processedError.set(false);
    this.originalError.set(false);
    if (id === null) return;
    this.imageSubs = [
      this.maps.processedImage(id).subscribe({
        next: (blob) => this.processedUrl.set(URL.createObjectURL(blob)),
        error: () => this.processedError.set(true),
      }),
    ];
    if (this.store.map()?.image) {
      this.imageSubs.push(
        this.maps.image(id).subscribe({
          next: (blob) => this.originalUrl.set(URL.createObjectURL(blob)),
          error: () => this.originalError.set(true),
        }),
      );
    }
  }

  private dropImages(): void {
    for (const url of [this.processedUrl(), this.originalUrl()]) {
      if (url) URL.revokeObjectURL(url);
    }
    this.processedUrl.set(null);
    this.originalUrl.set(null);
  }
}
