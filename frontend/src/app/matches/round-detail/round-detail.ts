import { DatePipe, DecimalPipe } from '@angular/common';
import { Component, computed, inject, signal } from '@angular/core';
import { MatAnchor, MatButton } from '@angular/material/button';
import { RouterLink } from '@angular/router';
import { Role } from '../../api/players.models';
import { GameRound, ScoreboardRow, ScoreboardSide } from '../../api/rounds.models';
import { RoundsService } from '../../api/rounds.service';
import { AuthService } from '../../auth/auth.service';
import { ROLE_LABELS } from '../../teams/shared/roster';
import { RecordNotFound } from '../shared/record-not-found';
import { loadRouteRecord } from '../shared/route-record';

type Side = 'red' | 'blue';
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

/** Ticks per second (1 tick = 0.5 s); 1801 ticks means "survived". */
const TICKS_PER_SECOND = 2;
const SURVIVED_TICK = 1801;

/** Roles that carry missiles: their Msls column reads `landed/starting`. */
const MISSILE_ROLES = new Set(['commander', 'heavy']);

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

function roleLabel(role: string): string {
  return ROLE_LABELS[role as Role] ?? role.toUpperCase();
}

/** `m:ss` for a tick count. */
function clock(ticks: number): string {
  const seconds = Math.floor(ticks / TICKS_PER_SECOND);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
}

function sortRows(rows: readonly ScoreboardRow[], state: SortState): ScoreboardRow[] {
  const col = COLUMNS.find((c) => c.key === state.key) ?? COLUMNS[2];
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

/** Hands `blob` to the browser as a download named `filename`, then frees the object URL. */
function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
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

  /** A Side with no rows (a legacy Round) has no players to have been eliminated. */
  protected eliminated(round: GameRound, side: Side): boolean {
    const board = round.scoreboard[side];
    return board.eliminated && board.players.length > 0;
  }

  protected elimBonus(round: GameRound, side: Side): number {
    const other: Side = side === 'red' ? 'blue' : 'red';
    return this.eliminated(round, other) ? round.scoreboard[side].elim_bonus : 0;
  }

  protected eliminatedAt(round: GameRound): string | null {
    const ticks = round.eliminated_at;
    return ticks > 0 && ticks < SURVIVED_TICK ? clock(ticks) : null;
  }

  protected carriesMissiles(row: ScoreboardRow): boolean {
    return MISSILE_ROLES.has(row.role);
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
