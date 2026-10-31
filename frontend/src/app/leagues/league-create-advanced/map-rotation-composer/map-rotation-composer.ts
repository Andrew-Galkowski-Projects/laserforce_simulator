import { Component, input, model } from '@angular/core';
import { MatButton } from '@angular/material/button';
import { MatFormField, MatLabel } from '@angular/material/form-field';
import { MatOption, MatSelect } from '@angular/material/select';
import { ArenaMap } from '../../../api/maps.models';

/**
 * The ordered ids as `create_advanced.html`'s rotation `serialize()` writes them (:569-579): row
 * order, comma-joined, a row with no map skipped.
 */
export function rotationWire(ids: readonly (number | null)[]): string {
  return ids.filter((id): id is number => id !== null).join(',');
}

/**
 * The map-rotation composer (`create_advanced.html` :511-590): one map per row, in order, no row
 * to begin with. A new row picks the first map, as a new `<select>` does.
 */
@Component({
  selector: 'app-map-rotation-composer',
  imports: [MatButton, MatFormField, MatLabel, MatOption, MatSelect],
  styleUrl: './map-rotation-composer.scss',
  templateUrl: './map-rotation-composer.html',
})
export class MapRotationComposer {
  readonly maps = input.required<readonly ArenaMap[]>();
  readonly ids = model.required<(number | null)[]>();
  /** Locks every row and button, e.g. while the host form saves (spec #444 I3). */
  readonly disabled = input(false);

  protected add(): void {
    const first = this.maps()[0]?.id ?? null;
    this.ids.update((ids) => [...ids, first]);
  }

  protected remove(index: number): void {
    this.ids.update((ids) => ids.filter((_, i) => i !== index));
  }

  protected set(index: number, id: number): void {
    this.ids.update((ids) => ids.map((old, i) => (i === index ? id : old)));
  }
}
