import { Component, inject } from '@angular/core';
import { MapEditorStore } from '../map-editor/map-editor.store';

/**
 * Sight Lines mode: a stub until ANG-06h-4 (#359) replaces it. Its state lives in
 * `MapEditorStore` (`loadSightLines`, `sight`, `rankedCells`, `strongSpots`, `editSightLines`,
 * `commitSightLines`, `commitComputed`, `loadRanking`, `editStrongSpots`, `saveStrongSpots`).
 */
@Component({
  selector: 'app-sight-lines-mode',
  template: `<p class="me-stub">Sight Lines editing is coming soon.</p>`,
})
export class SightLinesMode {
  protected readonly store = inject(MapEditorStore);
}
