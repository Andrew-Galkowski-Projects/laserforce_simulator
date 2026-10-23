// Round Playback spec fixtures (ANG-06f). Imported only by *.spec.ts files.
// Player ids match `TIMELINE_PLAYERS` in timeline-fixtures.ts, so a spec can pair
// `timeline()` with `roundPlayback()` for the same four players.
import {
  PlaybackMap,
  PlaybackMapPlayer,
  PlaybackPlayer,
  PlaybackSnapshot,
  RoundPlayback,
} from '../../api/rounds.models';

/** A tick-0 opening snapshot: 15 lives, 30 shots, no missiles, everything else zero / off. */
export function playbackSnapshot(overrides: Partial<PlaybackSnapshot> = {}): PlaybackSnapshot {
  return {
    tick: 0,
    score: 0,
    lives: 15,
    shots: 30,
    missiles: 0,
    sp: 0,
    tags: 0,
    misses: 0,
    downed_at: null,
    eliminated: false,
    rapid_fire: false,
    nuking: false,
    ...overrides,
  };
}

/** A player with the given snapshots (default: just the tick-0 opening one). */
export function playbackPlayer(
  id: number,
  snapshots: PlaybackSnapshot[] = [playbackSnapshot()],
): PlaybackPlayer {
  return { id, snapshots };
}

/**
 * The four `TIMELINE_PLAYERS` at tick 0: Ace (101, red commander, 5 missiles),
 * Bolt (201, blue heavy, 5 missiles), Cleo (102, red medic), Dax (202, blue ammo).
 */
export const PLAYBACK_PLAYERS: PlaybackPlayer[] = [
  playbackPlayer(101, [playbackSnapshot({ missiles: 5 })]),
  playbackPlayer(201, [playbackSnapshot({ lives: 10, shots: 20, missiles: 5 })]),
  playbackPlayer(102, [playbackSnapshot({ lives: 20, shots: 15 })]),
  playbackPlayer(202, [playbackSnapshot({ lives: 10, shots: 15 })]),
];

/** Ace on the map: spawns at (0, 0), walks to (0, 2) at tick 10. */
export function playbackMapPlayer(overrides: Partial<PlaybackMapPlayer> = {}): PlaybackMapPlayer {
  return {
    id: 101,
    name: 'Ace',
    team: 'red',
    role: 'commander',
    scout: 0,
    spawn: [0, 0],
    moves: [
      [
        10,
        [
          [0, 1],
          [0, 2],
        ],
      ],
    ],
    ...overrides,
  };
}

/** The beta map for arena map 7, "Warehouse", with Ace's route. */
export function playbackMap(overrides: Partial<PlaybackMap> = {}): PlaybackMap {
  return {
    zone_size: 50,
    arena_map: { id: 7, name: 'Warehouse' },
    players: [playbackMapPlayer()],
    ...overrides,
  };
}

/**
 * A Round's Playback: the 1800-tick Round, the 8 / 16 respawn constants, the four
 * `PLAYBACK_PLAYERS` at tick 0 and no map. Pass `players` / `map` to override.
 */
export function roundPlayback(overrides: Partial<RoundPlayback> = {}): RoundPlayback {
  return {
    duration_ticks: 1800,
    not_targetable_ticks: 8,
    respawn_ticks: 16,
    players: PLAYBACK_PLAYERS,
    map: null,
    ...overrides,
  };
}
