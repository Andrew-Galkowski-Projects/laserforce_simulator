import { ZoneType } from '../../api/maps.models';
import {
  bulkToggleSight,
  bulkToggleStrongSpots,
  rectKeys,
  sightBatches,
  sightOf,
  toggleBaseSight,
  toggleSight,
  toggleStrongSpot,
} from './sight-edit';

// A 3 x 3 grid: [0, 0] is a high wall, [0, 1] a low wall, the rest floor.
const ZONES: ZoneType[][] = [
  [0, 4, 1],
  [1, 1, 1],
  [1, 1, 1],
];

describe('sightOf', () => {
  it("splits every other non-high-wall Cell into the selection's visible and blocked Cells", () => {
    const { visible, blocked } = sightOf({ '1,1': ['1,2', '2,0'] }, ZONES, [1, 1]);
    expect([...visible].sort()).toEqual(['1,2', '2,0']);
    expect([...blocked].sort()).toEqual(['0,1', '0,2', '1,0', '2,1', '2,2']);
  });

  it('gives a Cell with no Sight lines nothing visible and every other passable Cell blocked', () => {
    const { visible, blocked } = sightOf({}, ZONES, [2, 2]);
    expect(visible.size).toBe(0);
    expect([...blocked].sort()).toEqual(['0,1', '0,2', '1,0', '1,1', '1,2', '2,0', '2,1']);
  });
});

describe('toggleSight', () => {
  it('links two Cells in both directions, keeping their other Sight lines', () => {
    const before = { '1,1': ['2,2'] };
    expect(toggleSight(before, ZONES, [1, 1], [1, 2])).toEqual({
      '1,1': ['2,2', '1,2'],
      '1,2': ['1,1'],
    });
    expect(before).toEqual({ '1,1': ['2,2'] });
  });

  it('unlinks a linked pair in both directions and drops a Cell left with none', () => {
    const before = { '1,1': ['1,2', '2,2'], '1,2': ['1,1'], '2,2': ['1,1'] };
    expect(toggleSight(before, ZONES, [1, 1], [1, 2])).toEqual({
      '1,1': ['2,2'],
      '2,2': ['1,1'],
    });
  });

  it("follows the selection's own list: a one-way link back is not doubled when linking", () => {
    // Computed LOS is direction-aware: [1, 2] may see [1, 1] while [1, 1] does not see it.
    expect(toggleSight({ '1,2': ['1,1'] }, ZONES, [1, 1], [1, 2])).toEqual({
      '1,1': ['1,2'],
      '1,2': ['1,1'],
    });
  });

  it('leaves the Sight lines alone for a high wall or the selection itself', () => {
    const before = { '1,1': ['1,2'], '1,2': ['1,1'] };
    expect(toggleSight(before, ZONES, [1, 1], [0, 0])).toBe(before);
    expect(toggleSight(before, ZONES, [1, 1], [1, 1])).toBe(before);
  });
});

describe('rectKeys', () => {
  it('keys every Cell of the rectangle but the high walls, in any corner order', () => {
    const expected = ['0,1', '1,0', '1,1'];
    expect([...rectKeys(ZONES, [0, 0], [1, 1])].sort()).toEqual(expected);
    expect([...rectKeys(ZONES, [1, 1], [0, 0])].sort()).toEqual(expected);
    expect([...rectKeys(ZONES, [1, 0], [0, 1])].sort()).toEqual(expected);
  });

  it('is empty for a rectangle of one high wall', () => {
    expect(rectKeys(ZONES, [0, 0], [0, 0]).size).toBe(0);
  });
});

describe('bulkToggleSight', () => {
  it('links every dragged Cell to the selection both ways when none is linked yet', () => {
    const next = bulkToggleSight({ '1,1': ['2,2'], '2,2': ['1,1'] }, [1, 1], ['1,1', '1,2', '2,1']);
    expect(next).toEqual({
      '1,1': ['2,2', '1,2', '2,1'],
      '2,2': ['1,1'],
      '1,2': ['1,1'],
      '2,1': ['1,1'],
    });
  });

  it('unlinks only the dragged Cells the selection sees when it sees any of them', () => {
    // [2, 1] sees [1, 1] one way only: the selection does not see it, so it is left alone.
    const before = { '1,1': ['1,2', '2,2'], '1,2': ['1,1'], '2,2': ['1,1'], '2,1': ['1,1'] };
    expect(bulkToggleSight(before, [1, 1], ['1,2', '2,1'])).toEqual({
      '1,1': ['2,2'],
      '2,2': ['1,1'],
      '2,1': ['1,1'],
    });
  });

  it('leaves the Sight lines alone when the drag holds only the selection', () => {
    const before = { '1,1': ['1,2'], '1,2': ['1,1'] };
    expect(bulkToggleSight(before, [1, 1], ['1,1'])).toBe(before);
  });
});

describe('toggleBaseSight', () => {
  const sights = () => ({
    red: [[1, 1]] as [number, number][],
    blue: [] as [number, number][],
    neutral_1: [],
    neutral_2: [],
    neutral_3: [],
    neutral_4: [],
  });

  it("adds a Cell to the Base's tagging Cells, and removes it on a second toggle", () => {
    const added = toggleBaseSight(sights(), ZONES, 'red', [2, 2]);
    expect(added.red).toEqual([
      [1, 1],
      [2, 2],
    ]);
    expect(added.blue).toEqual([]);
    expect(toggleBaseSight(added, ZONES, 'red', [1, 1]).red).toEqual([[2, 2]]);
  });

  it('ignores a high wall', () => {
    const before = sights();
    expect(toggleBaseSight(before, ZONES, 'blue', [0, 0])).toBe(before);
  });
});

describe('toggleStrongSpot', () => {
  it('adds a Cell that is not a Strong spot and removes one that is', () => {
    expect(toggleStrongSpot([[1, 1]], ZONES, [0, 1])).toEqual([
      [1, 1],
      [0, 1],
    ]);
    expect(
      toggleStrongSpot(
        [
          [1, 1],
          [0, 1],
        ],
        ZONES,
        [1, 1],
      ),
    ).toEqual([[0, 1]]);
  });

  it('ignores a high wall', () => {
    const before: [number, number][] = [[1, 1]];
    expect(toggleStrongSpot(before, ZONES, [0, 0])).toBe(before);
  });
});

describe('bulkToggleStrongSpots', () => {
  it('adds every dragged Cell when none is a Strong spot', () => {
    expect(bulkToggleStrongSpots([[2, 2]], ['1,0', '1,1'])).toEqual([
      [2, 2],
      [1, 0],
      [1, 1],
    ]);
  });

  it('removes only the dragged Strong spots when any dragged Cell is one', () => {
    expect(
      bulkToggleStrongSpots(
        [
          [2, 2],
          [1, 1],
        ],
        ['1,0', '1,1'],
      ),
    ).toEqual([[2, 2]]);
  });

  it('leaves the Strong spots alone for an empty drag', () => {
    const before: [number, number][] = [[2, 2]];
    expect(bulkToggleStrongSpots(before, [])).toBe(before);
  });
});

describe('sightBatches', () => {
  const BASE_SIGHTS = {
    red: [[1, 1]] as [number, number][],
    blue: [],
    neutral_1: [],
    neutral_2: [],
    neutral_3: [],
    neutral_4: [],
  };
  /** `n` Sight-line entries keyed `"0,0"`, `"0,1"`, ... */
  const sightData = (n: number) =>
    Object.fromEntries(Array.from({ length: n }, (_v, i) => [`0,${i}`, [`1,${i}`]]));
  const sizes = (n: number) =>
    sightBatches(40, sightData(n), BASE_SIGHTS).map((b) => Object.keys(b.sight_data).length);

  it('sends nothing but the Base sights in one batch for no Sight lines', () => {
    expect(sightBatches(40, {}, BASE_SIGHTS)).toEqual([
      { zone_size: 40, sight_data: {}, base_sights: BASE_SIGHTS },
    ]);
  });

  it('splits into 100-key batches, in key order', () => {
    expect(sizes(100)).toEqual([100]);
    expect(sizes(101)).toEqual([100, 1]);
    expect(sizes(250)).toEqual([100, 100, 50]);
    const batches = sightBatches(40, sightData(250), BASE_SIGHTS);
    expect(Object.keys(batches[1].sight_data)[0]).toBe('0,100');
    expect(batches[2].sight_data['0,249']).toEqual(['1,249']);
  });

  it('sends the Base sights in the first batch only, and the Zone size in every one', () => {
    const batches = sightBatches(40, sightData(250), BASE_SIGHTS);
    expect(batches.map((b) => b.base_sights)).toEqual([BASE_SIGHTS, {}, {}]);
    expect(batches.map((b) => b.zone_size)).toEqual([40, 40, 40]);
  });
});
