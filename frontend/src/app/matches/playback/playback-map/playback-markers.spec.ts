import { MARKER_DIM, MARKER_FOOTPRINT, markerLook, markerSize } from './playback-markers';

describe('MARKER_FOOTPRINT', () => {
  it("is the gauge arcs' outer edge: radius 0.56 plus half their 0.1 width", () => {
    expect(MARKER_FOOTPRINT).toBeCloseTo(0.61);
  });
});

describe('markerSize', () => {
  it('is 1.5 times max(18, zone * 1.3) image px', () => {
    expect(markerSize(10)).toBe(27);
    expect(markerSize(20)).toBeCloseTo(39);
  });
});

describe('markerLook', () => {
  const colours = { side: { red: '#d33a40', blue: '#1a4a94' }, muted: '#4a505c' };

  it("is the player's Side colour at full opacity while active", () => {
    expect(markerLook('red', false, colours)).toEqual({ colour: '#d33a40', alpha: 1 });
    expect(markerLook('blue', false, colours)).toEqual({ colour: '#1a4a94', alpha: 1 });
  });

  it('is the muted grey, dimmed to 0.35, while deactivated', () => {
    expect(MARKER_DIM).toBe(0.35);
    expect(markerLook('blue', true, colours)).toEqual({ colour: '#4a505c', alpha: 0.35 });
  });
});
