import { Component, computed, inject, input } from '@angular/core';
import { RouterLink } from '@angular/router';
import {
  SAMPLE_FIXTURE,
  SAMPLE_SCREENS,
  SampleScreen,
  Side,
  tickToClock,
} from '../fixture/sample-fixture';

/**
 * Bare stand-in for LayoutA..D until each layout agent replaces its folder: the fixture as
 * plain tables, so palette switching is visible. Uses tokens only (see the contract spec).
 */
@Component({
  selector: 'app-placeholder-layout',
  imports: [RouterLink],
  templateUrl: './placeholder-layout.html',
  styleUrl: './placeholder-layout.scss',
})
export class PlaceholderLayout {
  readonly label = input.required<string>();
  readonly screen = input<SampleScreen>('round');

  protected readonly fixture = inject(SAMPLE_FIXTURE);
  protected readonly screens = SAMPLE_SCREENS;
  protected readonly sides: Side[] = ['red', 'blue'];
  protected readonly clock = tickToClock;
  protected readonly topMvp = computed(() =>
    Math.max(...this.fixture.round.playerStates.map((p) => p.mvp)),
  );

  protected statesFor(side: Side) {
    return this.fixture.round.playerStates.filter((p) => p.side === side);
  }

  protected teamNameFor(side: Side): string {
    return side === 'red' ? this.fixture.round.redTeamName : this.fixture.round.blueTeamName;
  }
}
