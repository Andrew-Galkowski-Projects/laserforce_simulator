import { Component, inject } from '@angular/core';
import { MapEditorStore } from '../map-editor/map-editor.store';

/**
 * Heatmap mode: a stub until ANG-06h-5 (#360) replaces it. Its state lives in `MapEditorStore`
 * (`loadHeatmap(side?)`, `heatmap`, `heatmapStatus`, `heatmapSide`); it paints the heatmap
 * canvas through a layer's `drawHeatmap` (`map-canvas.ts`), which the host shows in this mode
 * only.
 */
@Component({
  selector: 'app-heatmap-mode',
  template: `<p class="me-stub">The Movement heatmap is coming soon.</p>`,
})
export class HeatmapMode {
  protected readonly store = inject(MapEditorStore);
}
