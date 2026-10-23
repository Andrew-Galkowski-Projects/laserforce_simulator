import { Component, input } from '@angular/core';
import { PlaybackSnapshot, Side, TimelinePlayer } from '../../../api/rounds.models';

/**
 * One Side's live Playback scoreboard. STUB (ANG-06f T2): T3 (#308) replaces this file's body
 * and template; the inputs below are the seam the shell binds, and stay as they are.
 *
 * Inputs:
 * - `side`: the Side this board shows (`'red'` | `'blue'`).
 * - `teamName`: that Side's team name (the board's heading).
 * - `players`: that Side's `TimelinePlayer`s, in `PlayerRoundState.id` order.
 * - `snapshots`: every player's Playback snapshots by player id (`RoundPlayback.players`); read a
 *   player's live state with `snapshotAt(snapshots.get(id) ?? [], cursor)`.
 * - `cursor`: the Playback cursor in ticks (fractional while playing).
 * - `notTargetableTicks` / `respawnTicks`: `RoundPlayback.not_targetable_ticks` (8) and
 *   `respawn_ticks` (16), for the respawn dot.
 */
@Component({
  selector: 'app-playback-scoreboard',
  template: '',
})
export class PlaybackScoreboard {
  readonly side = input.required<Side>();
  readonly teamName = input.required<string>();
  readonly players = input.required<readonly TimelinePlayer[]>();
  readonly snapshots = input.required<ReadonlyMap<number, readonly PlaybackSnapshot[]>>();
  readonly cursor = input.required<number>();
  readonly notTargetableTicks = input.required<number>();
  readonly respawnTicks = input.required<number>();
}
