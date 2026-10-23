import { Component, input } from '@angular/core';
import { PlaybackMap as PlaybackMapData, TimelinePlayer } from '../../../api/rounds.models';

/**
 * The beta Playback map. STUB (ANG-06f T2): T4 (#309) replaces this file's body and template;
 * the inputs below are the seam the shell binds, and stay as they are.
 *
 * Inputs:
 * - `map`: `RoundPlayback.map`, `null` for a map-less Round (show the "No map" notice).
 * - `players`: every `TimelinePlayer` of the Round (names, Sides, roles), both Sides.
 * - `cursor`: the Playback cursor in ticks (fractional while playing).
 *
 * The real map fetches its image only when the "Show map (beta)" toggle is first opened, so a
 * closed map makes no HTTP request; shell specs rely on that.
 */
@Component({
  selector: 'app-playback-map',
  template: '',
})
export class PlaybackMap {
  readonly map = input.required<PlaybackMapData | null>();
  readonly players = input.required<readonly TimelinePlayer[]>();
  readonly cursor = input.required<number>();
}
