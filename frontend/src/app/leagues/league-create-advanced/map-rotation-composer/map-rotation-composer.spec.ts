import { rotationWire } from './map-rotation-composer';

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
