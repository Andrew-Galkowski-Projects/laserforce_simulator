import { DatePipe, DecimalPipe } from '@angular/common';
import { Component, computed, inject, signal } from '@angular/core';
import { MatAnchor, MatButton } from '@angular/material/button';
import { RouterLink } from '@angular/router';
import { Role } from '../../api/players.models';
import { GameRound, ScoreboardRow, ScoreboardSide, Side } from '../../api/rounds.models';
import { RoundsService } from '../../api/rounds.service';
import { AuthService } from '../../auth/auth.service';
import { saveBlob } from '../../shared/save-blob';
import { ROLE_LABELS } from '../../teams/shared/roster';
import { RecordNotFound } from '../shared/record-not-found';
import { loadRouteRecord } from '../shared/route-record';

type SortDir = 'asc' | 'desc';

interface SortState {
  key: string;
  dir: SortDir;
}

interface Column {
  key: string;
  label: string;
  /** Numeric columns are centred, tabular, and sort descending first. */
  num: boolean;
  value: (row: ScoreboardRow) => number | string;
}

/** Ticks per second (1 tick = 0.5 s). */
const TICKS_PER_SECOND = 2;
/**
 * The tick past a 0-1800 round: `was_eliminated_at` / `eliminated_at` at or above it mean nobody
 * was eliminated (the server's TIME-01 survived sentinel). Survivors sort last on Elim with it.
 */
const SURVIVED_TICK = 1801;

/** SP spent (specials used x cost) and earned (spent plus the special left at the end). */
function specialSpent(row: ScoreboardRow): number {
  return row.specials_used * row.special_cost;
}

/** Tags made per time tagged; a player never tagged sorts above everyone. */
function hitDiff(row: ScoreboardRow): number {
  return row.times_tagged > 0 ? row.tags_made / row.times_tagged : Number.POSITIVE_INFINITY;
}

const COLUMNS: Column[] = [
  { key: 'callsign', label: 'Callsign', num: false, value: (r) => r.name },
  { key: 'pos', label: 'Pos', num: false, value: (r) => roleLabel(r.role) },
  { key: 'score', label: 'Score', num: true, value: (r) => r.points_scored },
  { key: 'mvp', label: 'MVP', num: true, value: (r) => r.mvp },
  { key: 'hitDiff', label: 'Hit Diff', num: true, value: hitDiff },
  { key: 'acc', label: 'Acc %', num: true, value: (r) => r.accuracy },
  { key: 'medic', label: 'Medic Hits', num: true, value: (r) => r.final_medic_hits },
  { key: 'lives', label: 'Lives', num: true, value: (r) => r.final_lives },
  { key: 'shots', label: 'Shots', num: true, value: (r) => r.final_shots },
  { key: 'msls', label: 'Msls', num: true, value: (r) => r.missiles_landed },
  { key: 'msld', label: 'Msld', num: true, value: (r) => r.times_missiled },
  { key: 'shotTeam', label: 'Shot Team', num: true, value: (r) => r.specific_tags_count },
  { key: 'sp', label: 'SP', num: true, value: specialSpent },
  {
    key: 'elim',
    label: 'Elim',
    num: true,
    value: (r) => (r.is_eliminated ? (r.was_eliminated_at ?? 0) : SURVIVED_TICK),
  },
];

const DEFAULT_SORT: SortState = { key: 'score', dir: 'desc' };

function roleLabel(role: Role): string {
  return ROLE_LABELS[role] ?? String(role).toUpperCase();
}

/** `m:ss` for a tick count. */
function clock(ticks: number): string {
  const seconds = Math.floor(ticks / TICKS_PER_SECOND);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
}

const columnFor = (key: string): Column | undefined => COLUMNS.find((c) => c.key === key);

function sortRows(rows: readonly ScoreboardRow[], state: SortState): ScoreboardRow[] {
  const col = columnFor(state.key) ?? columnFor(DEFAULT_SORT.key);
  if (!col) return [...rows];
  const sign = state.dir === 'asc' ? 1 : -1;
  return [...rows].sort((a, b) => {
    const va = col.value(a);
    const vb = col.value(b);
    const cmp =
      typeof va === 'number' && typeof vb === 'number'
        ? va === vb
          ? 0
          : va < vb
            ? -1
            : 1
        : String(va).localeCompare(String(vb));
    return cmp * sign;
  });
}

/**
 * `/rounds/:id`: the Round detail in ADR-0043 layout B. A page head, one strip per Side (base
 * score, muted elim bonus, badges, players and survivors) and one sortable **Round scoreboard**
 * table per Side, from the API's `scoreboard`. Export PDF downloads the Round report.
 */
@Component({
  selector: 'app-round-detail',
  imports: [DatePipe, DecimalPipe, MatAnchor, MatButton, RecordNotFound, RouterLink],
  templateUrl: './round-detail.html',
  styleUrl: './round-detail.scss',
})
export class RoundDetail {
  private readonly rounds = inject(RoundsService);
  private readonly auth = inject(AuthService);

  protected readonly load = loadRouteRecord('Round', (id) => this.rounds.get(id));
  protected readonly sides: Side[] = ['red', 'blue'];
  protected readonly columns = COLUMNS;
  protected readonly clock = clock;
  protected readonly roleLabel = roleLabel;
  protected readonly specialSpent = specialSpent;

  /** The signed-in Account's Self-insert Player id, for the "You" badge. */
  protected readonly selfId = computed(() => this.auth.currentUser()?.self_insert?.id ?? null);

  private readonly sort = signal<Record<Side, SortState>>({
    red: DEFAULT_SORT,
    blue: DEFAULT_SORT,
  });

  /** The highest MVP score across both Sides' rows (every tied player gets the star). */
  protected readonly topMvp = computed(() => {
    const board = this.load.record()?.scoreboard;
    if (!board) return null;
    const all = [...board.red.players, ...board.blue.players];
    return all.length ? Math.max(...all.map((r) => r.mvp)) : null;
  });

  /** True while the report PDF is being generated. */
  protected readonly exporting = signal(false);
  protected readonly exportError = signal<string | null>(null);

  protected board(round: GameRound, side: Side): ScoreboardSide {
    return round.scoreboard[side];
  }

  protected rows(round: GameRound, side: Side): ScoreboardRow[] {
    return sortRows(round.scoreboard[side].players, this.sort()[side]);
  }

  protected teamName(round: GameRound, side: Side): string {
    return side === 'red' ? round.team_red_name : round.team_blue_name;
  }

  protected points(round: GameRound, side: Side): number {
    return side === 'red' ? round.red_points : round.blue_points;
  }

  /** "Win" / "Loss", or "Tie" for both Sides when there is no winner. */
  protected outcome(round: GameRound, side: Side): 'Win' | 'Loss' | 'Tie' {
    if (round.winner === null) return 'Tie';
    const teamId = side === 'red' ? round.team_red : round.team_blue;
    return round.winner === teamId ? 'Win' : 'Loss';
  }

  protected eliminatedAt(round: GameRound): string | null {
    const ticks = round.eliminated_at;
    return ticks > 0 && ticks < SURVIVED_TICK ? clock(ticks) : null;
  }

  /** Rows that started with missiles read `landed/starting` in the Msls column. */
  protected carriesMissiles(row: ScoreboardRow): boolean {
    return row.starting_missiles > 0;
  }

  protected toggleSort(side: Side, col: Column): void {
    this.sort.update((all) => {
      const cur = all[side];
      const dir: SortDir =
        cur.key === col.key ? (cur.dir === 'asc' ? 'desc' : 'asc') : col.num ? 'desc' : 'asc';
      return { ...all, [side]: { key: col.key, dir } };
    });
  }

  protected ariaSort(side: Side, key: string): 'ascending' | 'descending' | 'none' {
    const s = this.sort()[side];
    if (s.key !== key) return 'none';
    return s.dir === 'asc' ? 'ascending' : 'descending';
  }

  protected arrow(side: Side, key: string): string {
    const s = this.sort()[side];
    if (s.key !== key) return '↕';
    return s.dir === 'asc' ? '▲' : '▼';
  }

  /** Fetches the Round report (behind the JWT, so not a plain link) and saves it. */
  protected exportPdf(id: number): void {
    if (this.exporting()) return;
    this.exporting.set(true);
    this.exportError.set(null);
    this.rounds.report(id).subscribe({
      next: (blob) => {
        this.exporting.set(false);
        saveBlob(blob, `round-${id}.pdf`);
      },
      error: () => {
        this.exporting.set(false);
        this.exportError.set('Could not generate the PDF. Try again.');
      },
    });
  }
}
