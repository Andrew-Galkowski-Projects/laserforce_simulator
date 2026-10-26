import {
  Component,
  DestroyRef,
  ElementRef,
  effect,
  inject,
  signal,
  viewChild,
} from '@angular/core';
import { takeUntilDestroyed, toObservable } from '@angular/core/rxjs-interop';
import { MatAnchor } from '@angular/material/button';
import { MatFormField, MatLabel } from '@angular/material/form-field';
import { MatOption, MatSelect } from '@angular/material/select';
import { RouterLink } from '@angular/router';
import { EMPTY, catchError, switchMap, tap } from 'rxjs';
import { MapsService } from '../../api/maps.service';
import { RoundHeatmap as RoundHeatmapData, Side } from '../../api/rounds.models';
import { RoundsService } from '../../api/rounds.service';
import { heatCells, heatColour } from '../../maps/heatmap/heat-cells';
import { apiMessage } from '../../shared/api-message';
import { readToken } from '../../shared/read-token';
import { RecordNotFound } from '../shared/record-not-found';
import { loadRouteRecord } from '../shared/route-record';
import { HeatmapFilter, filteredOccupancy } from './heatmap-filter';

/** The Role picker's options after "All", in ADR-0043 order. */
const ROLES: { value: string; label: string }[] = [
  { value: 'commander', label: 'Commander' },
  { value: 'heavy', label: 'Heavy' },
  { value: 'scout', label: 'Scout' },
  { value: 'ammo', label: 'Ammo' },
  { value: 'medic', label: 'Medic' },
];

/** The Team picker's options. */
const TEAMS: { value: Side | null; label: string }[] = [
  { value: null, label: 'Both' },
  { value: 'red', label: 'Red' },
  { value: 'blue', label: 'Blue' },
];

/** Fills each hot Cell of `occupancy` at its intensity's colour and alpha, over a cleared canvas. */
function paintHeat(
  ctx: CanvasRenderingContext2D,
  occupancy: Record<string, number>,
  zone: number | null,
): void {
  const { width, height } = ctx.canvas;
  ctx.clearRect(0, 0, width, height);
  if (!zone) return;
  const low = readToken('--lf-map-heat-low');
  const high = readToken('--lf-map-heat-high');
  for (const { cell, t, alpha } of heatCells(occupancy)) {
    const [r, c] = cell;
    ctx.globalAlpha = alpha;
    ctx.fillStyle = heatColour(t, low, high);
    ctx.fillRect(c * zone, r * zone, zone, zone);
  }
  ctx.globalAlpha = 1;
}

/**
 * `/rounds/:id/heatmap`: the Round's **Movement heatmap** (ANG-06-10). The arena's processed
 * image (fetched as a Blob, never `processed_image_url`) with a heat overlay of the **Cell
 * occupancy** of the players passing the Player / Role / Team pickers. The pickers filter
 * client-side; the canvas is redrawn by an effect, at the image's natural size, and CSS scales
 * both together to the content width.
 */
@Component({
  selector: 'app-round-heatmap',
  imports: [MatAnchor, MatFormField, MatLabel, MatOption, MatSelect, RecordNotFound, RouterLink],
  templateUrl: './round-heatmap.html',
  styleUrl: './round-heatmap.scss',
})
export class RoundHeatmap {
  private readonly rounds = inject(RoundsService);
  private readonly maps = inject(MapsService);

  protected readonly load = loadRouteRecord('Round', (id) => this.rounds.get(id));
  protected readonly roles = ROLES;
  protected readonly teams = TEAMS;

  /** The heatmap payload, once loaded. */
  protected readonly heatmap = signal<RoundHeatmapData | null>(null);
  /** A failed heatmap or map image load, as the server's message. */
  protected readonly error = signal<string | null>(null);
  /** The arena image's object URL, once fetched. */
  protected readonly imageUrl = signal<string | null>(null);
  /** The `<img>` has loaded, so its natural size is known. */
  protected readonly loaded = signal(false);
  /** The three pickers; all `null` (everyone) at first. */
  protected readonly filter = signal<HeatmapFilter>({ playerId: null, role: null, team: null });

  private readonly image = viewChild<ElementRef<HTMLImageElement>>('image');
  private readonly canvas = viewChild<ElementRef<HTMLCanvasElement>>('canvas');

  constructor() {
    toObservable(this.load.record)
      .pipe(
        switchMap((round) => {
          this.reset();
          if (!round) return EMPTY;
          return this.rounds.heatmap(round.id).pipe(
            tap((data) => this.heatmap.set(data)),
            switchMap((data) =>
              data.has_map && data.arena_map ? this.maps.processedImage(data.arena_map.id) : EMPTY,
            ),
            catchError((error: unknown) => {
              this.error.set(apiMessage(error, 'Could not load the heatmap.'));
              return EMPTY;
            }),
          );
        }),
        takeUntilDestroyed(),
      )
      .subscribe((blob) => this.imageUrl.set(URL.createObjectURL(blob)));

    effect(() => {
      const data = this.heatmap();
      const filter = this.filter();
      const img = this.image()?.nativeElement;
      const canvas = this.canvas()?.nativeElement;
      if (!this.loaded() || !data || !img || !canvas || !img.naturalWidth) return;
      if (canvas.width !== img.naturalWidth || canvas.height !== img.naturalHeight) {
        canvas.width = img.naturalWidth;
        canvas.height = img.naturalHeight;
      }
      const ctx = canvas.getContext('2d');
      if (ctx) paintHeat(ctx, filteredOccupancy(data, filter), data.zone_size);
    });

    inject(DestroyRef).onDestroy(() => this.revokeImage());
  }

  /** Changes one picker; the effect redraws, with no request. */
  protected setFilter(change: Partial<HeatmapFilter>): void {
    this.filter.update((f) => ({ ...f, ...change }));
  }

  /** An image that will not decode fails like a failed fetch. */
  protected imageFailed(): void {
    this.revokeImage();
    this.error.set('Could not load the map image.');
  }

  private reset(): void {
    this.revokeImage();
    this.heatmap.set(null);
    this.error.set(null);
    this.loaded.set(false);
  }

  private revokeImage(): void {
    const src = this.imageUrl();
    if (src) {
      URL.revokeObjectURL(src);
      this.imageUrl.set(null);
    }
  }
}
