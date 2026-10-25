import {
  Component,
  DestroyRef,
  ElementRef,
  computed,
  effect,
  inject,
  input,
  output,
  signal,
  viewChild,
} from '@angular/core';
import { inGrid, pointerToCell } from './grid';
import { CellPointer, MapCanvas, MapFrame } from './map-canvas';
import { MapEditorStore } from './map-editor.store';
import { paintCells, paintGridLines } from './map-paint';
import { readMapPalette } from './map-palette';

/**
 * The canvas host: the processed image with the overlay canvas and the heatmap canvas over it.
 * Both canvases have the image's natural size (the grid's `img_width` x `img_height`) and CSS
 * scales them with the image. Pointer events on the overlay go to the active mode's layer, and
 * redraws are coalesced to one animation frame (see `map-canvas.ts` for the hook).
 */
@Component({
  selector: 'app-map-stage',
  template: `
    <div class="me-stage">
      <img class="me-processed" [src]="imageUrl()" [alt]="alt()" (error)="imageError.emit()" />
      <canvas
        #overlay
        class="me-overlay"
        aria-hidden="true"
        [hidden]="heatmapMode()"
        [width]="size().width"
        [height]="size().height"
        [style.cursor]="canvas.cursor()"
        (pointerdown)="forward('down', $event)"
        (pointermove)="forward('move', $event)"
        (pointerup)="forward('up', $event)"
        (click)="forward('click', $event)"
      ></canvas>
      <canvas
        #heat
        class="me-heatmap"
        aria-hidden="true"
        [hidden]="!heatmapMode()"
        [width]="size().width"
        [height]="size().height"
      ></canvas>
    </div>
  `,
  styles: `
    :host {
      display: block;
    }

    // The canvases have the image's natural size; CSS scales all three together.
    .me-stage {
      position: relative;
      width: fit-content;
      max-width: 100%;
    }

    .me-processed {
      display: block;
      max-width: 100%;
      height: auto;
    }

    .me-overlay,
    .me-heatmap {
      position: absolute;
      inset: 0;
      width: 100%;
      height: 100%;
      touch-action: none;
    }

    .me-heatmap {
      pointer-events: none;
    }
  `,
})
export class MapStage {
  /** The processed image's object URL. */
  readonly imageUrl = input.required<string>();
  readonly alt = input.required<string>();
  /** The processed image would not decode. */
  readonly imageError = output();

  private readonly store = inject(MapEditorStore);
  protected readonly canvas = inject(MapCanvas);

  private readonly overlay = viewChild.required<ElementRef<HTMLCanvasElement>>('overlay');
  private readonly heat = viewChild.required<ElementRef<HTMLCanvasElement>>('heat');

  protected readonly heatmapMode = computed(() => this.store.mode() === 'heatmap');
  protected readonly size = computed(() => {
    const grid = this.store.grid();
    return { width: grid?.img_width ?? 0, height: grid?.img_height ?? 0 };
  });

  /** Bumped when the OS colour scheme flips, so the palette is read again. */
  private readonly scheme = signal(0);
  private readonly palette = computed(() => {
    this.scheme();
    return readMapPalette();
  });

  private frame: number | null = null;

  constructor() {
    const destroyRef = inject(DestroyRef);
    const query = window.matchMedia?.('(prefers-color-scheme: dark)');
    if (query) {
      const onChange = (): void => this.scheme.update((n) => n + 1);
      query.addEventListener('change', onChange);
      destroyRef.onDestroy(() => query.removeEventListener('change', onChange));
    }
    destroyRef.onDestroy(() => {
      if (this.frame !== null) cancelAnimationFrame(this.frame);
    });

    // Everything a redraw depends on; any change schedules one frame.
    effect(() => {
      const s = this.store;
      s.grid();
      s.zoneEdit();
      s.sight();
      s.rankedCells();
      s.strongSpots();
      s.heatmap();
      s.mode();
      this.size();
      this.palette();
      this.canvas.redraws();
      this.canvas.layer()?.deps?.();
      this.schedule();
    });
  }

  /** Sends a pointer event to the active layer, with the Cell under it (null off the grid). */
  protected forward(type: CellPointer['type'], event: MouseEvent): void {
    const layer = this.canvas.layer();
    const grid = this.store.grid();
    if (!layer?.pointer || !grid) return;
    const el = this.overlay().nativeElement;
    if (type === 'down' && 'pointerId' in event) {
      try {
        el.setPointerCapture((event as PointerEvent).pointerId);
      } catch {
        // Not every pointer can be captured (e.g. a synthetic event); the drag still works.
      }
    }
    const cell = pointerToCell(
      event.clientX,
      event.clientY,
      el.getBoundingClientRect(),
      { width: el.width, height: el.height },
      grid.cell_size,
    );
    layer.pointer({
      type,
      cell: cell && inGrid(cell, grid.rows, grid.cols) ? cell : null,
      event,
    });
  }

  private schedule(): void {
    if (this.frame !== null) return;
    this.frame = requestAnimationFrame(() => {
      this.frame = null;
      this.paint();
    });
  }

  /** One full redraw of both canvases. */
  private paint(): void {
    const grid = this.store.grid();
    if (!grid) return;
    const layer = this.canvas.layer();
    const base = {
      rows: grid.rows,
      cols: grid.cols,
      cellSize: grid.cell_size,
      width: grid.img_width,
      height: grid.img_height,
      zones: this.store.zoneEdit()?.zones ?? grid.zones,
      palette: this.palette(),
    };
    const frameFor = (el: HTMLCanvasElement): MapFrame | null => {
      const ctx = el.getContext('2d');
      if (!ctx) return null;
      ctx.clearRect(0, 0, el.width, el.height);
      return { ...base, ctx };
    };

    const overlay = frameFor(this.overlay().nativeElement);
    if (overlay) {
      if (layer?.draw) {
        layer.draw(overlay);
      } else {
        paintCells(overlay);
        paintGridLines(overlay);
      }
    }
    const heat = frameFor(this.heat().nativeElement);
    if (heat) layer?.drawHeatmap?.(heat);
  }
}
