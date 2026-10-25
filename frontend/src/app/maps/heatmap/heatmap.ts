import { Component, inject } from '@angular/core';
import { MapHeatmap } from '../../api/maps.models';
import { MapFrame, useMapLayer } from '../map-editor/map-canvas';
import { MapEditorStore } from '../map-editor/map-editor.store';
import { heatCells, heatColour } from './heat-cells';

/** A Side filter value: `''` is both Sides (no `team_color`). */
type SideFilter = '' | 'red' | 'blue';

/**
 * Heatmap mode (ANG-06h-5): the multi-round Movement heatmap for the current Zone size, over the
 * map. State lives in `MapEditorStore` (`loadHeatmap(side?)`, `heatmap`, `heatmapStatus`,
 * `heatmapSide`); a Zone-size change reloads it there.
 */
@Component({
  selector: 'app-heatmap-mode',
  templateUrl: './heatmap.html',
  styleUrl: './heatmap.scss',
})
export class HeatmapMode {
  protected readonly store = inject(MapEditorStore);

  protected readonly sides: { value: SideFilter; label: string }[] = [
    { value: '', label: 'Both' },
    { value: 'red', label: 'Red' },
    { value: 'blue', label: 'Blue' },
  ];

  constructor() {
    // Only the heatmap canvas: the host hides the overlay in this mode.
    useMapLayer({
      // The last heatmap loaded (null after a failed load) stays up while the next one loads.
      drawHeatmap: (frame) => {
        const data = this.store.heatmap();
        if (data) paintHeatmap(frame, data);
      },
    });
    // Entering the mode (re)loads, keeping the Side last picked in this editor visit.
    this.store.loadHeatmap(this.store.heatmapSide() ?? undefined);
  }

  protected pickSide(event: Event): void {
    const value = (event.target as HTMLSelectElement).value as SideFilter;
    this.store.loadHeatmap(value || undefined);
  }
}

/** Fills each hot Cell of `data` at its intensity's colour and alpha (the template's routine). */
function paintHeatmap({ ctx, palette }: MapFrame, data: MapHeatmap): void {
  const size = data.zone_size;
  for (const { cell, t, alpha } of heatCells(data.cell_occupancy)) {
    const [r, c] = cell;
    ctx.globalAlpha = alpha;
    ctx.fillStyle = heatColour(t, palette.heatLow, palette.heatHigh);
    ctx.fillRect(c * size, r * size, size, size);
  }
  ctx.globalAlpha = 1;
}
