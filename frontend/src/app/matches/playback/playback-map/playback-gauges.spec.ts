import { playbackMapPlayer, playbackSnapshot } from '../../testing/playback-fixtures';
import { Rgb, gaugeColour, hexRgb, playerGauges } from './playback-gauges';

// Ace is a Commander: 30 lives, 60 shots, 5 missiles. A Scout carries no missiles.
const commander = playbackMapPlayer();
const scout = playbackMapPlayer({
  role: 'scout',
  max_lives: 30,
  max_shots: 60,
  special_cost: 10,
  max_missiles: 0,
});

describe('playerGauges', () => {
  it('reads full lives and shots against the role maximum', () => {
    const snapshots = [playbackSnapshot({ lives: 30, shots: 60, missiles: 5 })];
    const gauges = playerGauges(commander, snapshots, 0);
    expect(gauges.lives).toBe(1);
    expect(gauges.shots).toBe(1);
  });

  it('reads half a Commander at 15 of 30 lives and 30 of 60 shots', () => {
    const snapshots = [playbackSnapshot({ lives: 15, shots: 30 })];
    const gauges = playerGauges(commander, snapshots, 0);
    expect(gauges.lives).toBe(0.5);
    expect(gauges.shots).toBe(0.5);
  });

  it('reads zero lives and shots as empty', () => {
    const snapshots = [playbackSnapshot({ lives: 0, shots: 0 })];
    const gauges = playerGauges(commander, snapshots, 0);
    expect(gauges.lives).toBe(0);
    expect(gauges.shots).toBe(0);
  });

  it('clamps a level over the role maximum to 1', () => {
    const snapshots = [playbackSnapshot({ lives: 45, shots: 90 })];
    const gauges = playerGauges(commander, snapshots, 0);
    expect(gauges.lives).toBe(1);
    expect(gauges.shots).toBe(1);
  });

  it('reads the levels from the last snapshot at or before a fractional tick', () => {
    const snapshots = [
      playbackSnapshot({ tick: 0, lives: 30, shots: 60, missiles: 5 }),
      playbackSnapshot({ tick: 10, lives: 12, shots: 15, missiles: 3 }),
      playbackSnapshot({ tick: 20, lives: 3, shots: 6, missiles: 1 }),
    ];
    expect(playerGauges(commander, snapshots, 9.9)).toMatchObject({
      lives: 1,
      shots: 1,
      missiles: 5,
    });
    expect(playerGauges(commander, snapshots, 10.5)).toMatchObject({
      lives: 0.4,
      shots: 0.25,
      missiles: 3,
    });
  });

  it("is a Commander's missiles left", () => {
    const snapshots = [playbackSnapshot({ missiles: 2 })];
    expect(playerGauges(commander, snapshots, 0).missiles).toBe(2);
  });

  it('has no missiles for a Scout, whose max_missiles is 0', () => {
    const snapshots = [playbackSnapshot({ missiles: 0 })];
    expect(playerGauges(scout, snapshots, 0).missiles).toBeNull();
  });

  it('reads empty for a player with no snapshot at the tick', () => {
    expect(playerGauges(commander, [], 5)).toEqual({
      lives: 0,
      shots: 0,
      missiles: 0,
      special: null,
    });
  });
});

describe('gaugeColour', () => {
  const full: Rgb = [30, 158, 62];
  const mid: Rgb = [227, 160, 8];
  const empty: Rgb = [127, 16, 16];

  it('is the full colour at 1', () => {
    expect(gaugeColour(1, full, mid, empty)).toBe('rgb(30, 158, 62)');
  });

  it('is half-way from mid to full at 0.75', () => {
    expect(gaugeColour(0.75, full, mid, empty)).toBe('rgb(129, 159, 35)');
  });

  it('is the mid colour at 0.5', () => {
    expect(gaugeColour(0.5, full, mid, empty)).toBe('rgb(227, 160, 8)');
  });

  it('is still on the empty-to-mid half just under 0.5', () => {
    expect(gaugeColour(0.45, full, mid, empty)).toBe('rgb(217, 146, 9)');
  });

  it('is half-way from empty to mid at 0.25', () => {
    expect(gaugeColour(0.25, full, mid, empty)).toBe('rgb(177, 88, 12)');
  });

  it('is the empty colour at 0', () => {
    expect(gaugeColour(0, full, mid, empty)).toBe('rgb(127, 16, 16)');
  });
});

describe('hexRgb', () => {
  it('reads a #rrggbb token', () => {
    expect(hexRgb('#1e9e3e')).toEqual([30, 158, 62]);
    expect(hexRgb(' #7F1010 ')).toEqual([127, 16, 16]);
  });

  it('reads an unparseable value as black', () => {
    expect(hexRgb('')).toEqual([0, 0, 0]);
  });
});
