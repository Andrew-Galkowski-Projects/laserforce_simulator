import { heatCells, heatColour } from './heat-cells';

describe('heatColour', () => {
  it('mixes t of the high token into the low one', () => {
    expect(heatColour(0.5, 'rgb(1 1 1)', 'rgb(9 9 9)')).toBe(
      'color-mix(in srgb, rgb(9 9 9) 50%, rgb(1 1 1))',
    );
  });

  it('is the high token at t = 1 and the low one as t nears 0 (rounded to whole percent)', () => {
    expect(heatColour(1, 'LOW', 'HIGH')).toBe('color-mix(in srgb, HIGH 100%, LOW)');
    expect(heatColour(0.004, 'LOW', 'HIGH')).toBe('color-mix(in srgb, HIGH 0%, LOW)');
  });
});

describe('heatCells', () => {
  it('maps each Cell to sqrt(value / max) and alpha 0.45 + 0.45 t', () => {
    const cells = heatCells({ '0,1': 100, '2,3': 25 });
    expect(cells).toEqual([
      { cell: [0, 1], t: 1, alpha: expect.closeTo(0.9) },
      { cell: [2, 3], t: 0.5, alpha: expect.closeTo(0.675) },
    ]);
  });

  it('skips Cells at or below zero', () => {
    expect(heatCells({ '0,0': 0, '0,1': -3, '1,1': 4 }).map((h) => h.cell)).toEqual([[1, 1]]);
  });

  it('skips malformed keys (but they still set the max)', () => {
    const cells = heatCells({ 'x,1': 400, '1;2': 9, '1,1': 100 });
    expect(cells).toEqual([{ cell: [1, 1], t: 0.5, alpha: expect.closeTo(0.675) }]);
  });

  it('draws nothing for an empty occupancy or a max at or below zero', () => {
    expect(heatCells({})).toEqual([]);
    expect(heatCells({ '0,0': 0, '0,1': -2 })).toEqual([]);
  });
});
