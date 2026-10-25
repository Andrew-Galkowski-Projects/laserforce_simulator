import { ZoneType } from '../../api/maps.models';
import { ZoneEdit } from '../map-editor/map-editor.store';
import {
  clearBases,
  clickSpawn,
  paintElevation,
  paintSpawns,
  paintWall,
  placeBase,
  rampElevation,
  saveBody,
  saveMessage,
} from './zones';

/** A 3 x 4 Zones & Bases area: all floor but a high wall at [0, 0]. */
function edit(overrides: Partial<ZoneEdit> = {}): ZoneEdit {
  return {
    zones: Array.from({ length: 3 }, (_r, r) =>
      Array.from({ length: 4 }, (_c, c) => (r === 0 && c === 0 ? 0 : 1) as ZoneType),
    ),
    wallMeta: {},
    elevation: null,
    bases: {},
    redSpawn: new Set(),
    blueSpawn: new Set(),
    spawnEdited: false,
    ...overrides,
  };
}

describe('zones', () => {
  describe('placeBase', () => {
    it("places a Base at the Cell's centre, floored to whole pixels", () => {
      const next = placeBase(edit(), 'red', [1, 2], 25);
      expect(next.bases).toEqual({ red: { x_px: 62, y_px: 37 } });
    });

    it('moves a Base placed elsewhere and leaves the other Bases alone', () => {
      const state = edit({
        bases: { red: { x_px: 20, y_px: 20 }, blue: { x_px: 140, y_px: 100 } },
      });
      const next = placeBase(state, 'red', [2, 1], 40);
      expect(next.bases).toEqual({ red: { x_px: 60, y_px: 100 }, blue: { x_px: 140, y_px: 100 } });
      expect(state.bases.red).toEqual({ x_px: 20, y_px: 20 });
    });

    it('removes the Base when its own Cell is clicked again', () => {
      const state = edit({
        bases: { neutral_1: { x_px: 50, y_px: 90 }, red: { x_px: 1, y_px: 1 } },
      });
      const next = placeBase(state, 'neutral_1', [2, 1], 40);
      expect(next.bases).toEqual({ red: { x_px: 1, y_px: 1 } });
    });
  });

  describe('clearBases', () => {
    it('removes every Base', () => {
      const state = edit({ bases: { red: { x_px: 1, y_px: 1 }, blue: { x_px: 2, y_px: 2 } } });
      expect(clearBases(state).bases).toEqual({});
      expect(Object.keys(state.bases)).toEqual(['red', 'blue']);
    });

    it('is a no-op on no Bases', () => {
      expect(clearBases(edit()).bases).toEqual({});
    });
  });

  describe('paintWall', () => {
    it('paints a windowed wall with its facing on each Cell', () => {
      const state = edit();
      const next = paintWall(
        state,
        [
          [1, 1],
          [1, 2],
        ],
        5,
        'E',
      );
      expect(next.zones[1]).toEqual([1, 5, 5, 1]);
      expect(next.wallMeta).toEqual({ '1,1': { facing: 'E' }, '1,2': { facing: 'E' } });
      expect(state.zones[1]).toEqual([1, 1, 1, 1]);
      expect(state.wallMeta).toEqual({});
    });

    it("replaces a windowed Cell's stored height with just the facing", () => {
      const next = paintWall(
        edit({ wallMeta: { '1,1': { facing: 'N', height: 2 } } }),
        [[1, 1]],
        5,
        'S',
      );
      expect(next.wallMeta).toEqual({ '1,1': { facing: 'S' } });
    });

    it('removes the wall_meta entry of a Cell painted any other type, keeping the rest', () => {
      const state = edit({
        wallMeta: { '0,1': { facing: 'N' }, '2,3': { facing: 'W', height: 1.5 } },
      });
      for (const type of [0, 1, 4] as const) {
        const next = paintWall(state, [[0, 1]], type, 'E');
        expect(next.zones[0][1]).toBe(type);
        expect(next.wallMeta).toEqual({ '2,3': { facing: 'W', height: 1.5 } });
      }
    });

    it('leaves a Base on a painted Cell where it is', () => {
      const bases = { red: { x_px: 60, y_px: 60 } };
      const next = paintWall(edit({ bases }), [[1, 1]], 0, 'N');
      expect(next.zones[1][1]).toBe(0);
      expect(next.bases).toEqual(bases);
    });

    it('skips Cells off the grid', () => {
      const next = paintWall(
        edit(),
        [
          [3, 0],
          [-1, 2],
          [0, 4],
        ],
        4,
        'N',
      );
      expect(next.zones).toEqual(edit().zones);
      expect(next.wallMeta).toEqual({});
    });
  });

  describe('paintElevation', () => {
    it('starts an all-zero Elevation grid on the first paint', () => {
      const next = paintElevation(
        edit(),
        [
          [0, 1],
          [2, 3],
        ],
        1.5,
      );
      expect(next.elevation).toEqual([
        [0, 1.5, 0, 0],
        [0, 0, 0, 0],
        [0, 0, 0, 1.5],
      ]);
    });

    it('keeps the loaded values it does not repaint, without mutating them', () => {
      const elevation = [
        [1, 1, 1, 1],
        [2, 2, 2, 2],
        [3, 3, 3, 3],
      ];
      const next = paintElevation(edit({ elevation }), [[1, 1]], 0);
      expect(next.elevation?.[1]).toEqual([2, 0, 2, 2]);
      expect(next.elevation?.[0]).toEqual([1, 1, 1, 1]);
      expect(elevation[1][1]).toBe(2);
    });

    it('starts over from zeros when the stored grid has another shape', () => {
      const next = paintElevation(edit({ elevation: [[3, 3]] }), [[0, 0]], 0.5);
      expect(next.elevation).toEqual([
        [0.5, 0, 0, 0],
        [0, 0, 0, 0],
        [0, 0, 0, 0],
      ]);
    });

    it('skips Cells off the grid', () => {
      const next = paintElevation(edit(), [[5, 5]], 2);
      expect(next.elevation).toEqual([
        [0, 0, 0, 0],
        [0, 0, 0, 0],
        [0, 0, 0, 0],
      ]);
    });
  });

  describe('rampElevation', () => {
    const row = (values: number[]): number[][] => [[0, 0, 0, 0], values, [0, 0, 0, 0]];

    it('interpolates linearly between two Cells on the same row', () => {
      const state = edit({ elevation: row([0, 0, 0, 3]) });
      expect(rampElevation(state, [1, 0], [1, 3]).elevation).toEqual(row([0, 1, 2, 3]));
    });

    it('gives the same ramp whichever endpoint is clicked first', () => {
      const state = edit({ elevation: row([0, 0, 0, 3]) });
      expect(rampElevation(state, [1, 3], [1, 0]).elevation).toEqual(row([0, 1, 2, 3]));
    });

    it('fills the rectangle on a diagonal by projection, rounded to 2 dp', () => {
      const state = edit({
        elevation: [
          [0, 9, 9, 9],
          [9, 9, 9, 9],
          [9, 9, 9, 3],
        ],
      });
      expect(rampElevation(state, [0, 0], [2, 3]).elevation).toEqual([
        [0, 0.69, 1.38, 2.08],
        [0.46, 1.15, 1.85, 2.54],
        [0.92, 1.62, 2.31, 3],
      ]);
    });

    it('leaves Cells outside the rectangle alone', () => {
      const elevation = [
        [5, 5, 5, 5],
        [1, 5, 2, 5],
        [5, 5, 5, 5],
      ];
      expect(rampElevation(edit({ elevation }), [1, 0], [1, 2]).elevation).toEqual([
        [5, 5, 5, 5],
        [1, 1.5, 2, 5],
        [5, 5, 5, 5],
      ]);
    });

    it('takes the endpoint value when both clicks are on the same Cell', () => {
      const state = edit({ elevation: row([0, 2.5, 0, 0]) });
      expect(rampElevation(state, [1, 1], [1, 1]).elevation).toEqual(row([0, 2.5, 0, 0]));
    });
  });

  const keys = (set: ReadonlySet<string>): string[] => [...set].sort();

  describe('clickSpawn', () => {
    it('adds a Red Spawn cell, taking it from Blue, and marks spawns edited', () => {
      const state = edit({ blueSpawn: new Set(['1,1', '2,2']) });
      const next = clickSpawn(state, [1, 1], 'red');
      expect(keys(next.redSpawn)).toEqual(['1,1']);
      expect(keys(next.blueSpawn)).toEqual(['2,2']);
      expect(next.spawnEdited).toBe(true);
      expect(keys(state.blueSpawn)).toEqual(['1,1', '2,2']);
    });

    it("toggles a Cell already in the brush's Side off", () => {
      const next = clickSpawn(edit({ blueSpawn: new Set(['2,3']) }), [2, 3], 'blue');
      expect(keys(next.blueSpawn)).toEqual([]);
      expect(next.spawnEdited).toBe(true);
    });

    it('erases a Cell from both Sides', () => {
      const state = edit({ redSpawn: new Set(['0,1']), blueSpawn: new Set(['0,1', '1,0']) });
      const next = clickSpawn(state, [0, 1], 'erase');
      expect(keys(next.redSpawn)).toEqual([]);
      expect(keys(next.blueSpawn)).toEqual(['1,0']);
    });

    it('paints a high wall Cell too (the template skips walls only in Bulk Edit)', () => {
      expect(keys(clickSpawn(edit(), [0, 0], 'red').redSpawn)).toEqual(['0,0']);
    });
  });

  describe('paintSpawns', () => {
    it('adds every Cell to the Side without toggling, skipping high walls', () => {
      const state = edit({ redSpawn: new Set(['0,1']), blueSpawn: new Set(['1,0']) });
      const next = paintSpawns(
        state,
        [
          [0, 0],
          [0, 1],
          [1, 0],
          [1, 1],
        ],
        'red',
      );
      expect(keys(next.redSpawn)).toEqual(['0,1', '1,0', '1,1']);
      expect(keys(next.blueSpawn)).toEqual([]);
      expect(next.spawnEdited).toBe(true);
    });

    it('erases every Cell from both Sides, walls included', () => {
      const state = edit({ redSpawn: new Set(['0,0', '2,2']), blueSpawn: new Set(['0,1']) });
      const next = paintSpawns(
        state,
        [
          [0, 0],
          [0, 1],
        ],
        'erase',
      );
      expect(keys(next.redSpawn)).toEqual(['2,2']);
      expect(keys(next.blueSpawn)).toEqual([]);
    });
  });

  describe('saveBody', () => {
    it('always sends the Zone size, Cell types and Bases; nothing empty or unedited', () => {
      const state = edit({
        bases: { blue: { x_px: 140, y_px: 100 }, red: { x_px: 20, y_px: 60 } },
      });
      expect(saveBody(state, 40)).toEqual({
        zone_size: 40,
        zones: state.zones,
        bases: [
          { type: 'red', x_px: 20, y_px: 60 },
          { type: 'blue', x_px: 140, y_px: 100 },
        ],
      });
    });

    it('sends an empty Bases list when none is placed', () => {
      expect(saveBody(edit(), 20).bases).toEqual([]);
    });

    it('sends wall_meta when non-empty and Elevation whenever there is a grid', () => {
      const elevation = [
        [0, 0, 0, 0],
        [0, 0, 0, 0],
        [0, 0, 0, 0],
      ];
      const wallMeta = { '1,1': { facing: 'N' as const, height: 2 } };
      const body = saveBody(edit({ wallMeta, elevation }), 40);
      expect(body.wall_meta).toEqual(wallMeta);
      expect(body.elevation).toEqual(elevation);
      expect('red_spawn' in body).toBe(false);
      expect('blue_spawn' in body).toBe(false);
    });

    it('sends the Spawn cells only once they were edited', () => {
      const spawns = { redSpawn: new Set(['1,0', '0,2']), blueSpawn: new Set<string>() };
      expect('red_spawn' in saveBody(edit(spawns), 40)).toBe(false);
      const body = saveBody(edit({ ...spawns, spawnEdited: true }), 40);
      expect(body.red_spawn).toEqual([
        [1, 0],
        [0, 2],
      ]);
      expect(body.blue_spawn).toEqual([]);
    });
  });

  describe('saveMessage', () => {
    it('reports success with both team Bases placed', () => {
      const bases = {
        red: { x_px: 1, y_px: 1 },
        blue: { x_px: 2, y_px: 2 },
        neutral_2: { x_px: 3, y_px: 3 },
      };
      expect(saveMessage(40, bases)).toEqual({
        kind: 'success',
        text: 'Configuration saved! (40px zones, 3 base(s))',
      });
    });

    it('warns when one team Base is missing', () => {
      expect(saveMessage(50, { red: { x_px: 1, y_px: 1 } })).toEqual({
        kind: 'warning',
        text:
          'Configuration saved (50px zones, 1 base(s)) - place a blue base before using this map ' +
          "in a match. It won't be offered in match, round or League map pickers until then.",
      });
      expect(saveMessage(50, { blue: { x_px: 1, y_px: 1 } }).text).toContain('place a red base ');
    });

    it('warns when both team Bases are missing', () => {
      expect(saveMessage(20, { neutral_1: { x_px: 1, y_px: 1 } })).toEqual({
        kind: 'warning',
        text:
          'Configuration saved (20px zones, 1 base(s)) - place a red and a blue base before using ' +
          "this map in a match. It won't be offered in match, round or League map pickers until then.",
      });
    });
  });
});
