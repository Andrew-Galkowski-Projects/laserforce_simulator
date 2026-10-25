// The drawing hook: how a mode component paints the editor canvas and hears its pointer events.
//
// HOW A MODE USES IT (Zones & Bases, Sight Lines, Heatmap)
//
//   export class ZonesBasesMode {
//     private readonly store = inject(MapEditorStore);
//     private readonly hover = signal<Cell | null>(null);   // mode-local state
//     private readonly canvas = useMapLayer({               // call in the constructor / a field
//       draw: (f) => { paintCells(f); paintGridLines(f); paintBases(f, this.store.zoneEdit()!.bases); },
//       pointer: (p) => { if (p.type === 'click' && p.cell) this.store.editZones(...); },
//       deps: () => this.hover(),                           // every mode-local signal draw reads
//     });
//   }
//
// - One layer is active at a time: the routed mode's. `useMapLayer` registers it and unregisters
//   it when the mode component is destroyed (a mode toggle). With no layer, or a layer without
//   `draw`, the host paints the read-only grid (Cell types + grid lines).
// - Redraws are full and coalesced to one `requestAnimationFrame`. The host schedules one after
//   any change to the store's state (grid, zoneEdit, sight, rankedCells, strongSpots, heatmap,
//   mode), the palette (a light / dark flip), the layer, or whatever `deps()` reads. Call
//   `requestRedraw()` for anything else. `draw` runs outside any reactive context: read freely.
// - `draw` gets the overlay canvas, already cleared, sized to the processed image's natural size
//   (`width` x `height`; CSS scales it with the image). `drawHeatmap` gets the second,
//   pointer-events-none canvas, shown only in Heatmap mode (the overlay is hidden there).
// - `pointer` hears `down` / `move` / `up` (pointer events, captured on down so `up` arrives
//   off-canvas too) and `click`, each with the Cell under the pointer, or `null` off the grid.
// - Paint helpers for the shared pieces (Cell types, grid lines, Bases, Elevation, Spawn cells,
//   one Cell's fill / outline / label) live in `map-paint.ts`; colours in `frame.palette`.
import { DestroyRef, Injectable, inject, signal } from '@angular/core';
import { ZoneType } from '../../api/maps.models';
import { Cell } from './grid';
import { MapPalette } from './map-palette';

/** What a layer's draw callback paints with. */
export interface MapFrame {
  /** The canvas's 2D context, cleared. */
  ctx: CanvasRenderingContext2D;
  rows: number;
  cols: number;
  /** The Zone size in image px. */
  cellSize: number;
  /** The canvas size: the processed image's natural size. */
  width: number;
  height: number;
  /** The Cell types as edited (`store.zoneEdit().zones`, else the loaded grid's). */
  zones: readonly (readonly ZoneType[])[];
  /** Every `--lf-map-*` colour, the Side colours and the body font, read at draw time. */
  palette: MapPalette;
}

/** A pointer event over the overlay canvas. */
export interface CellPointer {
  type: 'down' | 'move' | 'up' | 'click';
  /** The Cell under the pointer; `null` off the grid. */
  cell: Cell | null;
  event: MouseEvent;
}

/** A mode's hooks into the canvas host. Every member is optional. */
export interface MapLayer {
  /** Paints the overlay canvas. Omitted: the host paints the read-only grid. */
  draw?(frame: MapFrame): void;
  /** Paints the heatmap canvas (shown in Heatmap mode only). */
  drawHeatmap?(frame: MapFrame): void;
  pointer?(pointer: CellPointer): void;
  /** Read inside the host's redraw effect: read every mode-local signal `draw` depends on. */
  deps?(): unknown;
}

/** The registry between the canvas host and the active mode; one per editor (`MapEditor`). */
@Injectable()
export class MapCanvas {
  private readonly _layer = signal<MapLayer | null>(null);
  private readonly _redraws = signal(0);

  /** The active mode's layer. */
  readonly layer = this._layer.asReadonly();
  /** Bumped by `requestRedraw()`; the host's redraw effect reads it. */
  readonly redraws = this._redraws.asReadonly();
  /** The overlay canvas's CSS cursor (e.g. `crosshair` while a brush is armed). */
  readonly cursor = signal('default');

  /** Makes `layer` the active one; the returned function unregisters it (if still active). */
  use(layer: MapLayer): () => void {
    this._layer.set(layer);
    return () => {
      if (this._layer() === layer) {
        this._layer.set(null);
        this.cursor.set('default');
      }
    };
  }

  /** Schedules a redraw on the next animation frame (coalesced with any other). */
  requestRedraw(): void {
    this._redraws.update((n) => n + 1);
  }
}

/**
 * Registers `layer` for the calling mode component and unregisters it when that component is
 * destroyed. Call in an injection context. Returns the `MapCanvas` (for `requestRedraw`,
 * `cursor`).
 */
export function useMapLayer(layer: MapLayer): MapCanvas {
  const canvas = inject(MapCanvas);
  inject(DestroyRef).onDestroy(canvas.use(layer));
  return canvas;
}
