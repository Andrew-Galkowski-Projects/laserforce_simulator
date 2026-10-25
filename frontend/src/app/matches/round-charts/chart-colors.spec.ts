import { readToken } from '../../shared/read-token';
import { playerColors, resolveColor, shade } from './chart-colors';
import { ChartPlayer } from './event-charts';

const player = (id: number, side: 'red' | 'blue'): ChartPlayer => ({
  id,
  name: `P${id}`,
  side,
  role: 'scout',
});

describe('chart colours', () => {
  afterEach(() => {
    document.documentElement.style.removeProperty('--lf-side-red');
  });

  it('reads a token off the document root, trimmed', () => {
    document.documentElement.style.setProperty('--lf-side-red', ' rgb(10, 0, 0) ');
    expect(readToken('--lf-side-red')).toBe('rgb(10, 0, 0)');
  });

  it('gives the first player the pure Side colour and darkens or lightens the rest toward the text colour', () => {
    expect(shade('B', 'T', 0, 3)).toBe('color-mix(in oklch, B 100%, T)');
    expect(shade('B', 'T', 1, 3)).toBe('color-mix(in oklch, B 78%, T)');
    expect(shade('B', 'T', 2, 3)).toBe('color-mix(in oklch, B 55%, T)');
    expect(shade('B', 'T', 0, 1)).toBe('color-mix(in oklch, B 100%, T)');
  });

  it('shades each Side separately, by the player order within the Side', () => {
    const colors = playerColors([player(1, 'red'), player(2, 'blue'), player(3, 'red')], {
      red: 'R',
      blue: 'U',
      mix: 'T',
    });
    expect(colors.get(1)).toBe('color-mix(in oklch, R 100%, T)');
    expect(colors.get(3)).toBe('color-mix(in oklch, R 55%, T)');
    expect(colors.get(2)).toBe('color-mix(in oklch, U 100%, T)');
  });

  it('returns a colour the browser cannot compute as given, and leaves no probe behind', () => {
    const host = document.createElement('div');
    expect(resolveColor('not-a-colour', host)).toBe('not-a-colour');
    expect(host.childElementCount).toBe(0);
  });
});
