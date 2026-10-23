import { Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { MatAnchor, MatIconButton } from '@angular/material/button';
import { MatSlider, MatSliderThumb } from '@angular/material/slider';
import { RouterLink } from '@angular/router';
import { forkJoin } from 'rxjs';
import { PlaybackSnapshot, Side, TimelinePlayer } from '../../api/rounds.models';
import { RoundsService } from '../../api/rounds.service';
import { tickClock } from '../shared/clock';
import { RecordNotFound } from '../shared/record-not-found';
import { loadRouteRecord } from '../shared/route-record';
import { PlaybackFeed } from './playback-feed/playback-feed';
import { PlaybackMap } from './playback-map/playback-map';
import { PlaybackScoreboard } from './playback-scoreboard/playback-scoreboard';

/** The speed group's speeds: 8x plays a 15-minute Round in under 2 minutes. */
const SPEEDS = [1, 2, 4, 8] as const;
type Speed = (typeof SPEEDS)[number];
/** The play loop's period in ms. */
const TICK_MS = 100;
/** Ticks advanced per step at 1x: 0.2 every 100 ms is 2 ticks a second, real time. */
const TICKS_PER_STEP = 0.2;

/**
 * `/rounds/:id/playback`: the **Playback** of one Round. Loads the Round, its timeline and its
 * Playback snapshots together, owns the cursor (a tick) and the transport, and hands the cursor
 * and the data to the scoreboards, the beta map and the feed.
 */
@Component({
  selector: 'app-playback',
  imports: [
    MatAnchor,
    MatIconButton,
    MatSlider,
    MatSliderThumb,
    PlaybackFeed,
    PlaybackMap,
    PlaybackScoreboard,
    RecordNotFound,
    RouterLink,
  ],
  templateUrl: './playback.html',
  styleUrl: './playback.scss',
})
export class Playback {
  private readonly rounds = inject(RoundsService);

  protected readonly load = loadRouteRecord('Round', (id) =>
    forkJoin({
      round: this.rounds.get(id),
      timeline: this.rounds.timeline(id),
      playback: this.rounds.playback(id),
    }),
  );
  protected readonly sides: Side[] = ['red', 'blue'];

  /** The Playback cursor, in ticks (fractional while playing). */
  readonly cursor = signal(0);
  protected readonly speeds = SPEEDS;
  /** The playback speed: 1x is real time (2 ticks a second). */
  protected readonly speed = signal<Speed>(1);
  /** True while the play loop runs. */
  protected readonly playing = signal(false);
  /** The play loop's interval handle, `null` while paused. */
  private timer: ReturnType<typeof setInterval> | null = null;

  constructor() {
    inject(DestroyRef).onDestroy(() => this.pause());
  }

  /** Starts the play loop (from the start when the cursor sits at the end). */
  protected play(): void {
    if (this.timer !== null) return;
    if (this.cursor() >= this.duration()) this.cursor.set(0);
    this.playing.set(true);
    this.timer = setInterval(() => this.tick(), TICK_MS);
  }

  /** Stops the play loop; the cursor stays where it is. */
  protected pause(): void {
    if (this.timer !== null) clearInterval(this.timer);
    this.timer = null;
    this.playing.set(false);
  }

  /** One play-loop step: `speed × 0.2` ticks, stopping at the end of the Round. */
  private tick(): void {
    // Rounded to 1/1000 tick, so ten 0.2 steps land on 2, not 1.9999... (the clock floors).
    const next = Math.round((this.cursor() + this.speed() * TICKS_PER_STEP) * 1000) / 1000;
    if (next >= this.duration()) {
      this.cursor.set(this.duration());
      this.pause();
    } else {
      this.cursor.set(next);
    }
  }

  /** Every player's snapshots by player id. */
  protected readonly snapshots = computed(
    () =>
      new Map<number, readonly PlaybackSnapshot[]>(
        (this.load.record()?.playback.players ?? []).map((p) => [p.id, p.snapshots]),
      ),
  );

  /** The Round's length in ticks (1800), 0 before the load. */
  protected readonly duration = computed(() => this.load.record()?.playback.duration_ticks ?? 0);

  /** `mm:ss / mm:ss`: the cursor and the Round's length. */
  protected readonly clock = computed(
    () =>
      `${tickClock(this.cursor(), { pad: true })} / ${tickClock(this.duration(), { pad: true })}`,
  );

  /** The distinct ticks that carry an event other than Movement, ascending. */
  private readonly eventTicks = computed(() => {
    const events = this.load.record()?.timeline.events ?? [];
    const ticks = new Set(events.filter((e) => e.event_type !== 'movement').map((e) => e.tick));
    return [...ticks].sort((a, b) => a - b);
  });

  /** Steps to the next event tick after the cursor (stays put after the last). */
  protected stepNext(): void {
    const cursor = this.cursor();
    const next = this.eventTicks().find((t) => t > cursor);
    if (next !== undefined) this.seek(next);
  }

  /**
   * Steps to the previous event tick before the cursor's whole tick (stays put before the
   * first): mid-play at 10.4 the cursor is still on tick 10, so Previous skips past it.
   */
  protected stepPrevious(): void {
    const tick = Math.floor(this.cursor());
    const previous = this.eventTicks()
      .filter((t) => t < tick)
      .at(-1);
    if (previous !== undefined) this.seek(previous);
  }

  /** The cursor's whole tick, for the scrubber. */
  protected readonly wholeTick = computed(() => Math.floor(this.cursor()));

  /** Dragging the scrubber pauses playback, so the cursor stays where it is put. */
  protected scrub(tick: number): void {
    this.pause();
    this.seek(tick);
  }

  /** Moves the cursor to `tick`, clamped to the Round (RV-04's Highlight pips call this). */
  seek(tick: number): void {
    this.cursor.set(Math.min(Math.max(tick, 0), this.duration()));
  }

  protected playersOn(players: readonly TimelinePlayer[], side: Side): TimelinePlayer[] {
    return players.filter((p) => p.side === side);
  }
}
