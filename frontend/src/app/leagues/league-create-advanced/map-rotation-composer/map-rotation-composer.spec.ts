import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { MATERIAL_ANIMATIONS } from '@angular/material/core';
import { ArenaMap } from '../../../api/maps.models';
import { editorMap } from '../../../maps/testing/editor-fixtures';
import { MapRotationComposer, rotationWire } from './map-rotation-composer';

// `create_advanced.html`'s rotation `serialize()` (:569-579): row order, duplicates kept, a row
// whose select has no value skipped, comma-joined.
describe('rotationWire', () => {
  it.each<[string, (number | null)[], string]>([
    ['no rows', [], ''],
    ['rows in author order, duplicates kept', [7, 2, 7], '7,2,7'],
    ['a row with no map skipped', [null, 3, null], '3'],
  ])('serialises %s', (_name, ids, wire) => {
    expect(rotationWire(ids)).toBe(wire);
  });
});

@Component({
  imports: [MapRotationComposer],
  template: `<app-map-rotation-composer [maps]="maps" [(ids)]="ids" [disabled]="disabled()" />`,
})
class Host {
  readonly maps: ArenaMap[] = [editorMap({ id: 4, name: 'Arena One' })];
  readonly ids = signal<(number | null)[]>([4]);
  readonly disabled = signal(false);
}

describe('MapRotationComposer disabled (spec #444 I3)', () => {
  function render(disabled: boolean): HTMLElement {
    TestBed.configureTestingModule({
      providers: [{ provide: MATERIAL_ANIMATIONS, useValue: { animationsDisabled: true } }],
    });
    const fixture = TestBed.createComponent(Host);
    fixture.componentInstance.disabled.set(disabled);
    fixture.detectChanges();
    return fixture.nativeElement as HTMLElement;
  }

  const buttons = (el: HTMLElement): HTMLButtonElement[] => [...el.querySelectorAll('button')];

  it('locks every row select and button while disabled', () => {
    const el = render(true);
    expect(buttons(el).map((b) => b.disabled)).toEqual([true, true]);
    expect(el.querySelector('mat-select')?.getAttribute('aria-disabled')).toBe('true');
  });

  it('leaves them enabled by default', () => {
    const el = render(false);
    expect(buttons(el).map((b) => b.disabled)).toEqual([false, false]);
    expect(el.querySelector('mat-select')?.getAttribute('aria-disabled')).toBe('false');
  });
});
