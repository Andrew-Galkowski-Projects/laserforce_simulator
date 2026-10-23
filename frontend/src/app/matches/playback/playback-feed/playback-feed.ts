import { Component, computed, input, signal } from '@angular/core';
import { MatCheckbox } from '@angular/material/checkbox';
import { TimelineEvent } from '../../../api/rounds.models';
import { tickClock } from '../../shared/clock';
import { EventGroup, eventGroupOf } from '../../shared/event-groups';
import { sideTag } from '../../shared/side-tag';

/** True when the actor, the description or the target contains `term` (already lower-cased). */
function matches(e: TimelineEvent, term: string): boolean {
  return [e.actor_name, e.description, e.target_name ?? ''].some((field) =>
    field.toLowerCase().includes(term),
  );
}

/**
 * The Playback event feed: every event up to the cursor, newest first, the newest row marked
 * current. Rows look like the Event log's (Event group colour, Side-coloured names). Misses and
 * Movement start hidden; a search box matches actor, description and target.
 */
@Component({
  selector: 'app-playback-feed',
  imports: [MatCheckbox],
  templateUrl: './playback-feed.html',
  styleUrl: './playback-feed.scss',
})
export class PlaybackFeed {
  /** The Round's timeline events, in `(tick, id)` order. */
  readonly events = input.required<readonly TimelineEvent[]>();
  /** The Playback cursor, in ticks. */
  readonly cursor = input.required<number>();

  /** "Misses": off at first, so the feed isn't flooded. */
  protected readonly showMisses = signal(false);
  /** "Movement": off at first. */
  protected readonly showMovement = signal(false);

  /** The search box's text. */
  protected readonly search = signal('');

  /** The events at or before the cursor, in order (before any filter). */
  protected readonly reached = computed(() => {
    const cursor = this.cursor();
    return this.events().filter((e) => e.tick <= cursor);
  });

  /** The reached events the filters and the search let through, newest first. */
  protected readonly shown = computed(() => {
    const misses = this.showMisses();
    const movement = this.showMovement();
    const term = this.search().trim().toLowerCase();
    return this.reached()
      .filter(
        (e) =>
          (misses || e.event_type !== 'miss') &&
          (movement || e.event_type !== 'movement') &&
          (term === '' || matches(e, term)),
      )
      .reverse();
  });

  protected readonly group = (e: TimelineEvent): EventGroup => eventGroupOf(e.event_type);
  protected readonly clock = (ticks: number): string => tickClock(ticks, { pad: true });
  protected readonly sideTag = sideTag;
}
