import { Component, inject } from '@angular/core';
import { MapEditorStore } from '../map-editor/map-editor.store';

/**
 * Zones & Bases mode: a stub until ANG-06h-3 (#358) replaces it. It registers no canvas layer,
 * so the host paints the read-only grid. Its state lives in `MapEditorStore` (`zoneEdit`,
 * `editZones`, `saveZoneConfig`); the drawing hook is `useMapLayer` (`map-canvas.ts`).
 */
@Component({
  selector: 'app-zones-bases-mode',
  template: `<p class="me-stub">Zones &amp; Bases editing is coming soon.</p>`,
})
export class ZonesBasesMode {
  protected readonly store = inject(MapEditorStore);
}
