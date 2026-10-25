import {
  baseCell,
  cellKey,
  cellKeys,
  cellPixel,
  cellsInRect,
  inGrid,
  keyCells,
  parseCellKey,
  pointerToCell,
} from './grid';

describe('grid', () => {
  describe('pointerToCell', () => {
    // A 400 x 200 image (canvas) drawn at half size, 50 px from the left and 10 px from the top.
    const rect = { left: 50, top: 10, width: 200, height: 100 };
    const canvas = { width: 400, height: 200 };

    it('scales the pointer to image pixels before dividing by the Zone size', () => {
      // (150 - 50) * 2 = 200 px -> col 5; (40 - 10) * 2 = 60 px -> row 1.
      expect(pointerToCell(150, 40, rect, canvas, 40)).toEqual([1, 5]);
    });

    it('maps the top-left corner to Cell 0,0 and the last pixel to the last Cell', () => {
      expect(pointerToCell(50, 10, rect, canvas, 40)).toEqual([0, 0]);
      expect(pointerToCell(249.9, 109.9, rect, canvas, 40)).toEqual([4, 9]);
    });

    it('gives negative indices left of / above the image', () => {
      expect(pointerToCell(30, 0, rect, canvas, 40)).toEqual([-1, -1]);
    });

    it('is null when the canvas has no rendered size yet', () => {
      expect(pointerToCell(10, 10, { left: 0, top: 0, width: 0, height: 0 }, canvas, 40)).toBe(
        null,
      );
    });
  });

  describe('inGrid', () => {
    it('accepts Cells inside rows x cols and rejects every edge beyond', () => {
      expect(inGrid([0, 0], 2, 3)).toBe(true);
      expect(inGrid([1, 2], 2, 3)).toBe(true);
      expect(inGrid([2, 0], 2, 3)).toBe(false);
      expect(inGrid([0, 3], 2, 3)).toBe(false);
      expect(inGrid([-1, 0], 2, 3)).toBe(false);
      expect(inGrid([0, -1], 2, 3)).toBe(false);
    });
  });

  describe('Base pixel <-> Cell', () => {
    it('puts a Base on the Cell holding its pixel centre (floor of px / Zone size)', () => {
      expect(baseCell({ x_px: 60, y_px: 20 }, 40)).toEqual([0, 1]);
      expect(baseCell({ x_px: 80, y_px: 79 }, 40)).toEqual([1, 2]);
    });

    it("places a Base at its Cell's centre, floored to whole pixels", () => {
      expect(cellPixel([1, 2], 40)).toEqual({ x_px: 100, y_px: 60 });
      expect(cellPixel([0, 0], 25)).toEqual({ x_px: 12, y_px: 12 });
    });

    it('round-trips a Cell through its pixel centre', () => {
      expect(baseCell(cellPixel([3, 7], 30), 30)).toEqual([3, 7]);
    });
  });

  describe('cellsInRect', () => {
    const expected = [
      [1, 2],
      [1, 3],
      [2, 2],
      [2, 3],
    ];

    it.each([
      ['top-left to bottom-right', [1, 2], [2, 3]],
      ['bottom-right to top-left', [2, 3], [1, 2]],
      ['top-right to bottom-left', [1, 3], [2, 2]],
      ['bottom-left to top-right', [2, 2], [1, 3]],
    ] as const)('lists every Cell row by row, dragged %s', (_name, a, b) => {
      expect(cellsInRect([a[0], a[1]], [b[0], b[1]])).toEqual(expected);
    });

    it('is the single Cell when both corners are the same', () => {
      expect(cellsInRect([4, 4], [4, 4])).toEqual([[4, 4]]);
    });
  });

  describe('"r,c" keys', () => {
    it('builds a key from a Cell', () => {
      expect(cellKey([3, 12])).toBe('3,12');
    });

    it('parses a key back to a Cell', () => {
      expect(parseCellKey('3,12')).toEqual([3, 12]);
    });

    it.each(['', '3', '3,', 'a,b', '1,2,3', '1.5,2', '-1,2'])(
      'rejects the malformed key %j',
      (key) => {
        expect(parseCellKey(key)).toBe(null);
      },
    );

    it('converts Cell lists to key sets and back, dropping malformed keys', () => {
      expect([
        ...cellKeys([
          [0, 1],
          [2, 3],
          [0, 1],
        ]),
      ]).toEqual(['0,1', '2,3']);
      expect(keyCells(['0,1', 'junk', '2,3'])).toEqual([
        [0, 1],
        [2, 3],
      ]);
    });
  });
});
