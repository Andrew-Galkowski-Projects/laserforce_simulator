import { DecimalPipe } from '@angular/common';
import { Component, computed, inject, input } from '@angular/core';
import { RouterLink } from '@angular/router';
import {
  SAMPLE_FIXTURE,
  SAMPLE_SCREENS,
  SamplePlayerState,
  SampleEventType,
  SampleScreen,
  Side,
  tickToClock,
} from '../fixture/sample-fixture';

interface SideTotals {
  points: number;
  mvp: number;
  tagsMade: number;
  timesTagged: number;
  hitDiff: number;
  accuracy: number;
  medicHits: number;
  lives: number;
  startingLives: number;
  shotsFired: number;
  missilesLanded: number;
  timesMissiled: number;
  survivedSeconds: number;
}

interface SideSummary {
  side: Side;
  label: string;
  teamName: string;
  points: number;
  eliminated: boolean;
  won: boolean;
  players: SamplePlayerState[];
  totals: SideTotals;
}

const ROUND_SECONDS = 900;

/**
 * Layout A -- Dense dashboard (ticket #248). Power-user, data-first: a slim header with screen
 * tabs, a team sidebar with the Side summary, and compact sticky-header tables. Prototype only.
 */
@Component({
  selector: 'app-layout-a',
  imports: [RouterLink, DecimalPipe],
  templateUrl: './layout-a.html',
  styleUrl: './layout-a.scss',
})
export class LayoutA {
  readonly screen = input<SampleScreen>('round');

  protected readonly fixture = inject(SAMPLE_FIXTURE);
  protected readonly screens = SAMPLE_SCREENS;
  protected readonly screenLabels: Record<SampleScreen, string> = {
    teams: 'Teams',
    round: 'Round',
    timeline: 'Timeline',
  };
  protected readonly clock = tickToClock;
  protected readonly eventTypes: SampleEventType[] = [
    'tag',
    'miss',
    'missile',
    'special',
    'nuke',
    'resupply',
    'elimination',
  ];

  protected readonly sides = computed<SideSummary[]>(() => {
    const r = this.fixture.round;
    return (['red', 'blue'] as Side[]).map((side) => {
      const players = r.playerStates.filter((p) => p.side === side);
      return {
        side,
        label: side === 'red' ? 'RED' : 'BLUE',
        teamName: side === 'red' ? r.redTeamName : r.blueTeamName,
        points: side === 'red' ? r.redPoints : r.bluePoints,
        eliminated: side === 'red' ? r.redEliminated : r.blueEliminated,
        won: r.winner === side,
        players,
        totals: totalsOf(players),
      };
    });
  });

  protected readonly topMvp = computed(() =>
    Math.max(...this.fixture.round.playerStates.map((p) => p.mvp)),
  );

  /** Team name -> Side in the sampled round, so the Teams list can wear Side colours. */
  protected sideOfTeam(name: string): Side | null {
    const r = this.fixture.round;
    if (name === r.redTeamName) return 'red';
    if (name === r.blueTeamName) return 'blue';
    return null;
  }

  protected sideLetter(side: Side | null): string {
    return side === 'red' ? 'R' : side === 'blue' ? 'B' : '';
  }

  protected survivedPct(seconds: number): number {
    return Math.round((seconds / ROUND_SECONDS) * 100);
  }

  protected secondsClock(seconds: number): string {
    return tickToClock(seconds * 2);
  }
}

function totalsOf(players: SamplePlayerState[]): SideTotals {
  const sum = (f: (p: SamplePlayerState) => number) => players.reduce((a, p) => a + f(p), 0);
  const tagsMade = sum((p) => p.tagsMade);
  const timesTagged = sum((p) => p.timesTagged);
  const shots = sum((p) => p.shotsFired);
  return {
    points: sum((p) => p.points),
    mvp: sum((p) => p.mvp),
    tagsMade,
    timesTagged,
    hitDiff: timesTagged ? tagsMade / timesTagged : tagsMade,
    accuracy: shots ? sum((p) => p.accuracy * p.shotsFired) / shots : 0,
    medicHits: sum((p) => p.medicHits),
    lives: sum((p) => p.lives),
    startingLives: sum((p) => p.startingLives),
    shotsFired: shots,
    missilesLanded: sum((p) => p.missilesLanded),
    timesMissiled: sum((p) => p.timesMissiled),
    survivedSeconds: players.length ? sum((p) => p.survivedSeconds) / players.length : 0,
  };
}
