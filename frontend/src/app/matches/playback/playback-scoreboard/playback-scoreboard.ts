import {
  Component,
  DestroyRef,
  computed,
  effect,
  inject,
  input,
  signal,
  untracked,
} from '@angular/core';
import { PlaybackSnapshot, Side, TimelinePlayer } from '../../../api/rounds.models';
import { roleLabel } from '../../shared/role-label';
import { sideTag } from '../../shared/side-tag';
import { snapshotAt } from '../snapshot-at';

/** One player's row at the cursor. */
interface BoardRow {
  id: number;
  name: string;
  pos: string;
  rank: number;
  snap: PlaybackSnapshot;
  acc: string;
  /** SP, or "—" for a Heavy (no special). */
  sp: string;
  /** Set while the player is Down: the respawn dot's fill and phase. */
  respawn: Respawn | null;
}

/** The respawn dot: `fill` sweeps 0-360deg over `respawn_ticks`; `phase` sets its colour. */
interface Respawn {
  fill: string;
  phase: 'not-targetable' | 'resetting';
}

/** How long a row keeps the rank-up flash class (the `sb-rank-up` keyframes run this long). */
const RANK_FLASH_MS = 700;

/** Everything zero / off: the state before a player's first snapshot (never sent in practice). */
const EMPTY: PlaybackSnapshot = {
  tick: 0,
  score: 0,
  lives: 0,
  shots: 0,
  missiles: 0,
  sp: 0,
  tags: 0,
  misses: 0,
  downed_at: null,
  eliminated: false,
  rapid_fire: false,
  nuking: false,
};

/** `tags / (tags + misses)` as a whole percent, or "—" before the first shot. */
function accuracy(s: PlaybackSnapshot): string {
  const shots = s.tags + s.misses;
  return shots > 0 ? `${Math.round((100 * s.tags) / shots)}%` : '—';
}

/** The respawn dot at the cursor, or `null` once `respawn_ticks` have passed since `downed_at`. */
function respawnAt(
  s: PlaybackSnapshot,
  cursor: number,
  notTargetableTicks: number,
  respawnTicks: number,
): Respawn | null {
  if (s.downed_at === null || s.eliminated) return null;
  const elapsed = cursor - s.downed_at;
  if (elapsed < 0 || elapsed >= respawnTicks) return null;
  return {
    fill: `${Math.round((360 * elapsed) / respawnTicks)}deg`,
    phase: elapsed < notTargetableTicks ? 'not-targetable' : 'resetting',
  };
}

/**
 * One Side's live Playback scoreboard (ANG-06f T3), after LFstats's Replay tab: a header strip
 * (team name, live Side total) over `# | Player | respawn dot | Pos | Score | Acc | Lives | Shots |
 * Msl | SP`, every value read from the player's Playback snapshot at the cursor.
 *
 * Inputs:
 * - `side`: the Side this board shows (`'red'` | `'blue'`).
 * - `teamName`: that Side's team name (the board's heading).
 * - `players`: that Side's `TimelinePlayer`s, in `PlayerRoundState.id` order.
 * - `snapshots`: every player's Playback snapshots by player id (`RoundPlayback.players`); read a
 *   player's live state with `snapshotAt(snapshots.get(id) ?? [], cursor)`.
 * - `cursor`: the Playback cursor in ticks (fractional while playing).
 * - `notTargetableTicks` / `respawnTicks`: `RoundPlayback.not_targetable_ticks` (8) and
 *   `respawn_ticks` (16), for the respawn dot.
 */
@Component({
  selector: 'app-playback-scoreboard',
  templateUrl: './playback-scoreboard.html',
  styleUrl: './playback-scoreboard.scss',
})
export class PlaybackScoreboard {
  readonly side = input.required<Side>();
  readonly teamName = input.required<string>();
  readonly players = input.required<readonly TimelinePlayer[]>();
  readonly snapshots = input.required<ReadonlyMap<number, readonly PlaybackSnapshot[]>>();
  readonly cursor = input.required<number>();
  readonly notTargetableTicks = input.required<number>();
  readonly respawnTicks = input.required<number>();

  protected readonly sideTag = sideTag;

  /** The Side's rows at the cursor, highest live score first; `rank` is the 1-based position. */
  protected readonly rows = computed<BoardRow[]>(() => {
    const cursor = this.cursor();
    const all = this.snapshots();
    const notTargetable = this.notTargetableTicks();
    const respawnTicks = this.respawnTicks();
    return this.players()
      .map((p) => {
        const snap = snapshotAt(all.get(p.id) ?? [], cursor) ?? EMPTY;
        return {
          id: p.id,
          name: p.name,
          pos: roleLabel(p.role),
          rank: 0,
          snap,
          acc: accuracy(snap),
          sp: p.role === 'heavy' ? '—' : String(snap.sp),
          respawn: respawnAt(snap, cursor, notTargetable, respawnTicks),
        };
      })
      .sort((a, b) => b.snap.score - a.snap.score)
      .map((row, i) => ({ ...row, rank: i + 1 }));
  });

  /** The live Side total: the sum of the rows' scores. */
  protected readonly total = computed(() => this.rows().reduce((sum, r) => sum + r.snap.score, 0));

  /** Ids of the players whose rank improved in the last `RANK_FLASH_MS`. */
  protected readonly flashing = signal<ReadonlySet<number>>(new Set());

  /** Each player's rank at the previous cursor (empty before the first render). */
  private previousRanks = new Map<number, number>();
  private readonly flashTimers = new Map<number, ReturnType<typeof setTimeout>>();

  constructor() {
    effect(() => {
      const rows = this.rows();
      const previous = this.previousRanks;
      this.previousRanks = new Map(rows.map((r) => [r.id, r.rank]));
      const improved = rows.filter((r) => r.rank < (previous.get(r.id) ?? r.rank));
      untracked(() => improved.forEach((r) => this.flash(r.id)));
    });
    inject(DestroyRef).onDestroy(() => this.flashTimers.forEach((t) => clearTimeout(t)));
  }

  private flash(id: number): void {
    clearTimeout(this.flashTimers.get(id));
    this.flashing.update((ids) => new Set(ids).add(id));
    this.flashTimers.set(
      id,
      setTimeout(() => {
        this.flashTimers.delete(id);
        this.flashing.update((ids) => {
          const next = new Set(ids);
          next.delete(id);
          return next;
        });
      }, RANK_FLASH_MS),
    );
  }
}
