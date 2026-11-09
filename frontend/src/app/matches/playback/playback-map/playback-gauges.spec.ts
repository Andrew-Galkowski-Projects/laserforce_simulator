import { PlaybackMapPlayer, PlaybackSnapshot } from '../../../api/rounds.models';
import { playbackMapPlayer, playbackSnapshot } from '../../testing/playback-fixtures';
import {
  Rgb,
  anySpecialRunning,
  fuseProgress,
  gaugeColour,
  hexRgb,
  lapColour,
  playerGauges,
} from './playback-gauges';

// Ace is a Commander: 30 lives, 60 shots, 5 missiles. A Scout carries no missiles.
const commander = playbackMapPlayer();
const scout = playbackMapPlayer({
  id: 102,
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
      special: { lap: 0, fill: 0, running: 'none', fuse: null },
    });
  });
});

describe('playerGauges special bar', () => {
  const heavy = playbackMapPlayer({
    role: 'heavy',
    max_lives: 20,
    max_shots: 40,
    special_cost: null,
  });
  const special = (player: PlaybackMapPlayer, snapshot: Partial<PlaybackSnapshot>) =>
    playerGauges(player, [playbackSnapshot(snapshot)], 0).special;

  it('has no special bar for a Heavy, whose special_cost is null', () => {
    expect(special(heavy, { sp: 25 })).toBeNull();
  });

  // A Commander's nuke costs 20 SP; SP caps at 99 (lap 4, 19 / 20 into the fifth).
  for (const [sp, lap, fill] of [
    [0, 0, 0],
    [19, 0, 19 / 20],
    [20, 1, 0],
    [21, 1, 1 / 20],
    [45, 2, 5 / 20],
    [120, 4, 19 / 20],
  ] as const) {
    it(`is lap ${lap}, fill ${fill} for a Commander at ${sp} SP`, () => {
      expect(special(commander, { sp })).toMatchObject({ lap, fill });
    });
  }

  it('is lap 9, fill 9 / 10 for a Scout at 99 SP', () => {
    expect(special(scout, { sp: 99 })).toMatchObject({ lap: 9, fill: 9 / 10 });
  });

  it('runs nothing with neither flag set', () => {
    expect(special(scout, {})).toMatchObject({ running: 'none', fuse: null });
  });

  it("runs a Scout's rapid fire", () => {
    expect(special(scout, { rapid_fire: true })).toMatchObject({
      running: 'rapid-fire',
      fuse: null,
    });
  });

  it("runs a Commander's nuke, with its fuse", () => {
    const snapshots = [
      playbackSnapshot({ tick: 79, nuking: true }),
      playbackSnapshot({ tick: 90, nuking: false }),
    ];
    expect(playerGauges(commander, snapshots, 84.5).special).toMatchObject({
      running: 'nuke',
      fuse: 0.5,
    });
  });
});

describe('fuseProgress', () => {
  const nuke = (tick: number, nuking = true) => playbackSnapshot({ tick, nuking });

  it('fills from the lit tick to the snapshot that clears nuking', () => {
    // The tick-83 snapshot is still nuking (SP changed): the fuse was lit at 79.
    const snapshots = [nuke(0, false), nuke(79), nuke(83), nuke(90, false)];
    expect(fuseProgress(snapshots, 79)).toBe(0);
    // Before the still-nuking tick-83 snapshot, the end is still the tick-90 one.
    expect(fuseProgress(snapshots, 81.75)).toBe(0.25);
    expect(fuseProgress(snapshots, 84.5)).toBe(0.5);
    expect(fuseProgress(snapshots, 90)).toBe(1);
  });

  it('ends at lit + 14 when nuking is still set at the last snapshot', () => {
    const snapshots = [nuke(0, false), nuke(79)];
    expect(fuseProgress(snapshots, 86)).toBe(0.5);
    expect(fuseProgress(snapshots, 100)).toBe(1);
  });

  it('is null before the fuse is lit, and after it ends', () => {
    const snapshots = [nuke(0, false), nuke(79), nuke(90, false)];
    expect(fuseProgress(snapshots, 78)).toBeNull();
    expect(fuseProgress(snapshots, 90.5)).toBeNull();
    expect(fuseProgress(snapshots, 200)).toBeNull();
    expect(fuseProgress([], 5)).toBeNull();
  });

  it('starts a second nuke later in the Round on its own run', () => {
    const snapshots = [nuke(0, false), nuke(79), nuke(90, false), nuke(300), nuke(310, false)];
    expect(fuseProgress(snapshots, 300)).toBe(0);
    expect(fuseProgress(snapshots, 305)).toBe(0.5);
  });
});

describe('lapColour', () => {
  const laps = ['#22a046', '#f08c00', '#d62828', '#a51c1c'];

  it('is the first colour on lap 0', () => {
    expect(lapColour(0, laps)).toBe('#22a046');
  });

  it("is lap 2's own colour", () => {
    expect(lapColour(2, laps)).toBe('#d62828');
  });

  it('takes the last colour for a lap past the end', () => {
    expect(lapColour(12, laps)).toBe('#a51c1c');
  });
});

describe('anySpecialRunning', () => {
  const players = [commander, scout];
  const byId = (commanderSnap: Partial<PlaybackSnapshot>, scoutSnap: Partial<PlaybackSnapshot>) =>
    new Map([
      [commander.id, [playbackSnapshot(commanderSnap)]],
      [scout.id, [playbackSnapshot(scoutSnap)]],
    ]);

  it('is false while no special runs', () => {
    expect(anySpecialRunning(players, byId({}, {}), 5)).toBe(false);
  });

  it("is true while a Scout's rapid fire runs", () => {
    expect(anySpecialRunning(players, byId({}, { rapid_fire: true }), 5)).toBe(true);
  });

  it("is true while a Commander's nuke runs", () => {
    expect(anySpecialRunning(players, byId({ nuking: true }, {}), 5)).toBe(true);
  });

  it('is false before the run starts, and for a player with no snapshots', () => {
    expect(anySpecialRunning(players, byId({ tick: 10, nuking: true }, {}), 5)).toBe(false);
    expect(anySpecialRunning(players, new Map(), 5)).toBe(false);
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
