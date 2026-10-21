import { DecimalPipe } from '@angular/common';
import { Component, computed, inject, input, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import {
  SAMPLE_FIXTURE,
  SampleEventType,
  SamplePlayerState,
  SampleScreen,
  SampleTeam,
  Side,
  tickToClock,
} from '../fixture/sample-fixture';

type SortDir = 'asc' | 'desc';
interface SortState {
  key: string;
  dir: SortDir;
}

interface Column<T> {
  key: string;
  label: string;
  /** Numeric columns are centred, tabular, and sort descending first. */
  num: boolean;
  value: (row: T) => number | string;
}

const PLAYER_COLUMNS: Column<SamplePlayerState>[] = [
  { key: 'callsign', label: 'Callsign', num: false, value: (p) => p.playerName },
  { key: 'pos', label: 'Pos', num: false, value: (p) => p.role },
  { key: 'score', label: 'Score', num: true, value: (p) => p.points },
  { key: 'mvp', label: 'MVP', num: true, value: (p) => p.mvp },
  { key: 'hitDiff', label: 'HitDiff', num: true, value: (p) => p.hitDiff },
  { key: 'acc', label: 'Acc', num: true, value: (p) => p.accuracy },
  { key: 'medic', label: 'Medic Hits', num: true, value: (p) => p.medicHits },
  { key: 'lives', label: 'Lives', num: true, value: (p) => p.lives },
  { key: 'shots', label: 'Shots', num: true, value: (p) => p.shotsFired },
  { key: 'msls', label: 'Msls', num: true, value: (p) => p.missilesLanded },
  { key: 'msld', label: 'Msld', num: true, value: (p) => p.timesMissiled },
  { key: 'survived', label: 'Survived', num: true, value: (p) => p.survivedSeconds },
];

const TEAM_COLUMNS: Column<SampleTeam>[] = [
  { key: 'team', label: 'Team', num: false, value: (t) => t.name },
  { key: 'wl', label: 'W–L', num: true, value: (t) => t.wins - t.losses },
  { key: 'players', label: 'Players', num: true, value: (t) => t.players.length },
];

function sortRows<T>(rows: readonly T[], columns: Column<T>[], state: SortState | undefined): T[] {
  const copy = [...rows];
  const col = state && columns.find((c) => c.key === state.key);
  if (!state || !col) return copy;
  const sign = state.dir === 'asc' ? 1 : -1;
  return copy.sort((a, b) => {
    const va = col.value(a);
    const vb = col.value(b);
    const cmp =
      typeof va === 'number' && typeof vb === 'number'
        ? va - vb
        : String(va).localeCompare(String(vb));
    return cmp * sign;
  });
}

/**
 * Layout B -- LFstats-like (ANG-04b-3, #249). Top header + collapsible grouped sidebar (a drawer
 * below 768px), team strips over flat sortable stat tables, and an m:ss event log.
 * Prototype only: never merges.
 */
@Component({
  selector: 'app-layout-b',
  imports: [RouterLink, DecimalPipe],
  templateUrl: './layout-b.html',
  styleUrl: './layout-b.scss',
})
export class LayoutB {
  readonly screen = input<SampleScreen>('round');

  protected readonly fixture = inject(SAMPLE_FIXTURE);
  protected readonly round = this.fixture.round;
  protected readonly sides: Side[] = ['red', 'blue'];
  protected readonly playerColumns = PLAYER_COLUMNS;
  protected readonly teamColumns = TEAM_COLUMNS;
  protected readonly clock = tickToClock;

  /** Desktop: sidebar collapsed to an icon rail. Mobile: drawer open. */
  protected readonly collapsed = signal(false);
  protected readonly drawerOpen = signal(false);

  protected readonly sort = signal<Record<string, SortState>>({
    red: { key: 'score', dir: 'desc' },
    blue: { key: 'score', dir: 'desc' },
    teams: { key: 'team', dir: 'asc' },
  });

  protected readonly topMvp = computed(() => {
    const best = [...this.round.playerStates].sort((a, b) => b.mvp - a.mvp)[0];
    return best?.playerName ?? null;
  });

  protected readonly sortedTeams = computed(() =>
    sortRows(this.fixture.teams, TEAM_COLUMNS, this.sort()['teams']),
  );

  protected readonly redRows = computed(() =>
    sortRows(this.statesFor('red'), PLAYER_COLUMNS, this.sort()['red']),
  );
  protected readonly blueRows = computed(() =>
    sortRows(this.statesFor('blue'), PLAYER_COLUMNS, this.sort()['blue']),
  );

  protected readonly title = computed(() => {
    switch (this.screen()) {
      case 'teams':
        return 'Teams';
      case 'timeline':
        return 'Replay log';
      default:
        return 'Scorecards';
    }
  });

  protected rowsFor(side: Side): SamplePlayerState[] {
    return side === 'red' ? this.redRows() : this.blueRows();
  }

  private statesFor(side: Side): SamplePlayerState[] {
    return this.round.playerStates.filter((p) => p.side === side);
  }

  protected teamName(side: Side): string {
    return side === 'red' ? this.round.redTeamName : this.round.blueTeamName;
  }

  protected points(side: Side): number {
    return side === 'red' ? this.round.redPoints : this.round.bluePoints;
  }

  protected eliminated(side: Side): boolean {
    return side === 'red' ? this.round.redEliminated : this.round.blueEliminated;
  }

  /** The surviving Side earns the bonus when the opponent is eliminated. */
  protected bonus(side: Side): number {
    const other: Side = side === 'red' ? 'blue' : 'red';
    return this.eliminated(other) && !this.eliminated(side) ? this.round.eliminationBonus : 0;
  }

  protected toggleSort(table: string, key: string, num: boolean): void {
    this.sort.update((all) => {
      const cur = all[table];
      const dir: SortDir =
        cur?.key === key ? (cur.dir === 'asc' ? 'desc' : 'asc') : num ? 'desc' : 'asc';
      return { ...all, [table]: { key, dir } };
    });
  }

  protected ariaSort(table: string, key: string): 'ascending' | 'descending' | 'none' {
    const s = this.sort()[table];
    if (s?.key !== key) return 'none';
    return s.dir === 'asc' ? 'ascending' : 'descending';
  }

  protected arrow(table: string, key: string): string {
    const s = this.sort()[table];
    if (s?.key !== key) return '↕';
    return s.dir === 'asc' ? '▲' : '▼';
  }

  protected toggleNav(): void {
    if (typeof window !== 'undefined' && window.matchMedia('(max-width: 767.98px)').matches) {
      this.drawerOpen.update((v) => !v);
    } else {
      this.collapsed.update((v) => !v);
    }
  }

  protected survivedPct(p: SamplePlayerState): number {
    return Math.round((p.survivedSeconds / 900) * 100);
  }

  protected survivedClock(p: SamplePlayerState): string {
    return tickToClock(p.survivedSeconds * 2);
  }

  protected evGlyph(type: SampleEventType): string {
    const glyphs: Record<SampleEventType, string> = {
      elimination: '✖',
      missile: '➤',
      special: '★',
      nuke: '☢',
      miss: '○',
      resupply: '✚',
      tag: '●',
    };
    return glyphs[type];
  }

  protected sideLetter(side: Side | null): string {
    return side === 'red' ? 'R' : side === 'blue' ? 'B' : '';
  }
}
