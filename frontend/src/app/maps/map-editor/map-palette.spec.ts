import { LF_MAP_TOKENS } from '../../theme/theme-tokens';
import { MAP_PALETTE_TOKENS, readMapPalette } from './map-palette';

describe('map palette', () => {
  afterEach(() => {
    document.documentElement.style.removeProperty('--lf-map-wall-low');
    document.documentElement.style.removeProperty('--lf-side-blue');
  });

  it('reaches every --lf-map-* token, the Side colours and the body font', () => {
    const tokens = Object.values(MAP_PALETTE_TOKENS);
    expect(LF_MAP_TOKENS.filter((t) => !tokens.includes(t))).toEqual([]);
    expect(tokens).toEqual(
      expect.arrayContaining(['--lf-side-red', '--lf-side-blue', '--lf-font-body']),
    );
  });

  it("reads each entry from its token's current value on the document root", () => {
    document.documentElement.style.setProperty('--lf-map-wall-low', ' rgb(1 2 3) ');
    document.documentElement.style.setProperty('--lf-side-blue', '#123456');
    const palette = readMapPalette();
    expect(palette.wallLow).toBe('rgb(1 2 3)');
    expect(palette.sideBlue).toBe('#123456');
    expect(palette.heatHigh).toBe('');
  });
});
