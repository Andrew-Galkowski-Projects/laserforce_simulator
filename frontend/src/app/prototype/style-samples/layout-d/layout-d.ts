import { DecimalPipe } from '@angular/common';
import { Component, computed, inject, input } from '@angular/core';
import { RouterLink } from '@angular/router';
import {
  SAMPLE_FIXTURE,
  SAMPLE_SCREENS,
  SampleEvent,
  SamplePlayerState,
  SampleScreen,
  Side,
  tickToClock,
} from '../fixture/sample-fixture';

/**
 * Layout D -- Esports broadcast (ticket #251). A live-stream overlay: a slim top bar with
 * broadcast-segment tabs, a lower-third ticker, a scoreboard header with a red-vs-blue score bar,
 * big team blocks of player cards, and the timeline as a ticker-style feed (newest first).
 * Colour only from the `--lf-*` contract and `--side*` (see palette-contract.ts).
 */
@Component({
  selector: 'app-layout-d',
  imports: [RouterLink, DecimalPipe],
  templateUrl: './layout-d.html',
  styleUrl: './layout-d.scss',
})
export class LayoutD {
  readonly screen = input<SampleScreen>('round');

  protected readonly fixture = inject(SAMPLE_FIXTURE);
  protected readonly round = this.fixture.round;
  protected readonly screens = SAMPLE_SCREENS;
  protected readonly sides: Side[] = ['red', 'blue'];
  protected readonly clock = tickToClock;

  protected readonly topMvp = computed(() =>
    Math.max(...this.round.playerStates.map((p) => p.mvp)),
  );
  /** Red share of the combined points, 0..100, for the score bar. */
  protected readonly redShare = computed(() => {
    const total = this.round.redPoints + this.round.bluePoints;
    return total === 0 ? 50 : Math.round((this.round.redPoints / total) * 100);
  });
  /** Newest first, for the ticker feed. */
  protected readonly feed = computed<SampleEvent[]>(() =>
    [...this.fixture.events].sort((a, b) => b.tick - a.tick),
  );
  protected readonly latest = computed(() => this.feed().slice(0, 6));

  protected statesFor(side: Side): SamplePlayerState[] {
    return this.round.playerStates.filter((p) => p.side === side);
  }

  protected teamNameFor(side: Side): string {
    return side === 'red' ? this.round.redTeamName : this.round.blueTeamName;
  }

  protected pointsFor(side: Side): number {
    return side === 'red' ? this.round.redPoints : this.round.bluePoints;
  }

  protected eliminated(side: Side): boolean {
    return side === 'red' ? this.round.redEliminated : this.round.blueEliminated;
  }

  /** Elimination bonus the Side earned (it is not included in the points). */
  protected bonusFor(side: Side): number {
    const opponentOut = side === 'red' ? this.round.blueEliminated : this.round.redEliminated;
    return opponentOut && !this.eliminated(side) ? this.round.eliminationBonus : 0;
  }

  protected pips(p: SamplePlayerState): boolean[] {
    // Medic resupplies can push lives above the starting count; show every pip.
    return Array.from({ length: Math.max(p.startingLives, p.lives) }, (_, i) => i < p.lives);
  }

  protected sideLetter(side: Side | null): string {
    return side === 'red' ? 'R' : side === 'blue' ? 'B' : '';
  }

  protected survivedClock(seconds: number): string {
    return tickToClock(seconds * 2);
  }
}
