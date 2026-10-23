import { BreakpointObserver } from '@angular/cdk/layout';
import {
  CdkFixedSizeVirtualScroll,
  CdkVirtualForOf,
  CdkVirtualScrollViewport,
} from '@angular/cdk/scrolling';
import { DecimalPipe, NgTemplateOutlet } from '@angular/common';
import { Component, computed, inject, signal, viewChild } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { MatAnchor } from '@angular/material/button';
import { MatCheckbox } from '@angular/material/checkbox';
import {
  MatExpansionPanel,
  MatExpansionPanelHeader,
  MatExpansionPanelTitle,
} from '@angular/material/expansion';
import { MatSlideToggle } from '@angular/material/slide-toggle';
import { RouterLink } from '@angular/router';
import { forkJoin, map } from 'rxjs';
import {
  EventSummary,
  Highlight,
  HighlightKind,
  Side,
  TimelineEvent,
  TimelinePlayer,
} from '../../api/rounds.models';
import { RoundsService } from '../../api/rounds.service';
import { EVENT_GROUPS, EventGroup, HIDDEN_BY_DEFAULT, eventGroupOf } from '../shared/event-groups';
import { RecordNotFound } from '../shared/record-not-found';
import { loadRouteRecord } from '../shared/route-record';

/** The seven summary cards, as the Django page shows them (`missiles` is not one). */
const CARDS: { key: keyof EventSummary; label: string }[] = [
  { key: 'tags', label: 'Tags' },
  { key: 'moves', label: 'Moves' },
  { key: 'misses', label: 'Misses' },
  { key: 'eliminations', label: 'Eliminations' },
  { key: 'specials', label: 'Specials' },
  { key: 'resupplies', label: 'Resupplies' },
  { key: 'total_events', label: 'Total' },
];

/** Each **Highlight** kind's label (the Django `KIND_LABEL`, minus its dead `base_capture`). */
const KIND_LABELS: Record<HighlightKind, string> = {
  nuke_detonation: 'Nuke',
  nuke_cancelled: 'Nuke Cancelled',
  medic_reset: 'Medic Reset',
  first_elimination: 'First Elim',
  team_elimination: 'Team Wipe',
  scoring_burst: 'Scoring Burst',
};

/** Ticks per second (1 tick = 0.5 s, as `GameEvent.formatted_timestamp`). */
const TICKS_PER_SECOND = 2;

/** The filter panel sits beside the timeline from here up; narrower (768px) it folds above. */
const WIDE = '(min-width: 1024px)';

/** A timeline row's fixed height in px (the virtual scroll's item size). */
const ROW_HEIGHT = 36;
/** The row height with "Show metadata" on (a second, one-line metadata line). */
const ROW_HEIGHT_WITH_META = 56;

/** `mm:ss` for a tick count. */
function clock(ticks: number): string {
  const seconds = Math.floor(ticks / TICKS_PER_SECOND);
  const mm = String(Math.floor(seconds / 60)).padStart(2, '0');
  return `${mm}:${String(seconds % 60).padStart(2, '0')}`;
}

/**
 * `/rounds/:id/events`: the **Event log** of one Round. The page head (from the Round), the
 * summary cards, the **Highlights** and the timeline, all from `GET rounds/:id/timeline/`.
 */
@Component({
  selector: 'app-round-events',
  imports: [
    CdkFixedSizeVirtualScroll,
    CdkVirtualForOf,
    CdkVirtualScrollViewport,
    DecimalPipe,
    MatAnchor,
    MatCheckbox,
    MatExpansionPanel,
    MatExpansionPanelHeader,
    MatExpansionPanelTitle,
    MatSlideToggle,
    NgTemplateOutlet,
    RecordNotFound,
    RouterLink,
  ],
  templateUrl: './round-events.html',
  styleUrl: './round-events.scss',
})
export class RoundEvents {
  private readonly rounds = inject(RoundsService);

  protected readonly load = loadRouteRecord('Round', (id) =>
    forkJoin({ round: this.rounds.get(id), timeline: this.rounds.timeline(id) }),
  );
  protected readonly cards = CARDS;
  protected readonly clock = clock;
  protected readonly rowHeight = ROW_HEIGHT;
  protected readonly rowHeightWithMeta = ROW_HEIGHT_WITH_META;
  /** True at `WIDE` and wider: the filters sit beside the timeline, else in a folded panel. */
  protected readonly wide = toSignal(
    inject(BreakpointObserver)
      .observe(WIDE)
      .pipe(map((state) => state.matches)),
    { initialValue: false },
  );
  private readonly viewport = viewChild(CdkVirtualScrollViewport);
  /** The event the last clicked Highlight scrolled to (its row is marked `is-current`). */
  protected readonly currentEvent = signal<TimelineEvent | null>(null);
  /** "Show metadata": off at first. */
  protected readonly showMetadata = signal(false);
  protected readonly group = (e: TimelineEvent): EventGroup => eventGroupOf(e.event_type);
  /** Timeline rows carry no id (the API sends none), so they are tracked by index. */
  protected readonly trackByIndex = (index: number): number => index;
  protected readonly groups = EVENT_GROUPS;
  protected readonly sides: Side[] = ['red', 'blue'];

  /** The checked Event groups' keys: all but Movement at first. */
  private readonly checkedGroups = signal<ReadonlySet<string>>(
    new Set(EVENT_GROUPS.map((g) => g.key).filter((key) => !HIDDEN_BY_DEFAULT.includes(key))),
  );

  /** The unchecked players' ids: none at first, so every player starts checked. */
  private readonly uncheckedPlayers = signal<ReadonlySet<number>>(new Set());

  /**
   * The events the filters let through, in chronological order: the type's group is checked
   * and its actor or its target is a checked player.
   */
  protected readonly visibleEvents = computed(() => {
    const events = this.load.record()?.timeline.events ?? [];
    const groups = this.checkedGroups();
    const off = this.uncheckedPlayers();
    return events.filter(
      (e) =>
        groups.has(eventGroupOf(e.event_type).key) &&
        (!off.has(e.actor_id) || (e.target_id !== null && !off.has(e.target_id))),
    );
  });

  protected playersOn(players: readonly TimelinePlayer[], side: Side): TimelinePlayer[] {
    return players.filter((p) => p.side === side);
  }

  protected playerChecked(id: number): boolean {
    return !this.uncheckedPlayers().has(id);
  }

  protected togglePlayer(id: number, checked: boolean): void {
    this.uncheckedPlayers.update((all) => {
      const next = new Set(all);
      if (checked) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  protected groupChecked(key: string): boolean {
    return this.checkedGroups().has(key);
  }

  protected toggleGroup(key: string, checked: boolean): void {
    this.checkedGroups.update((all) => {
      const next = new Set(all);
      if (checked) next.add(key);
      else next.delete(key);
      return next;
    });
  }

  /** An event's metadata as `key: value` text (objects as JSON), `''` when it has none. */
  protected metadataText(e: TimelineEvent): string {
    return Object.entries(e.metadata)
      .map(([key, value]) => {
        const shown = value !== null && typeof value === 'object' ? JSON.stringify(value) : value;
        return `${key}: ${String(shown)}`;
      })
      .join(' · ');
  }

  protected kindLabel(h: Highlight): string {
    return KIND_LABELS[h.kind] ?? h.kind;
  }

  /** Scrolls the timeline to the first visible event at or after the Highlight's tick. */
  protected jumpTo(h: Highlight): void {
    if (h.tick === null) return;
    const tick = h.tick;
    const visible = this.visibleEvents();
    const index = visible.findIndex((e) => e.tick >= tick);
    if (index < 0) return;
    this.currentEvent.set(visible[index]);
    this.viewport()?.scrollToIndex(index);
  }
}
