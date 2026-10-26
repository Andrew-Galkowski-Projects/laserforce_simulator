import { Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { NonNullableFormBuilder, ReactiveFormsModule } from '@angular/forms';
import { MatButton } from '@angular/material/button';
import { MatFormField, MatLabel } from '@angular/material/form-field';
import { MatInput } from '@angular/material/input';
import { ActivatedRoute, Router } from '@angular/router';
import { EMPTY, catchError, switchMap } from 'rxjs';
import { PlayerStatDeltaRow, RoundComparison, StatDelta } from '../../api/analytics.models';
import { AnalyticsService } from '../../api/analytics.service';
import { ApiError } from '../../api/api-client';
import { listAll } from '../../api/list-all';
import { GameRoundListItem } from '../../api/rounds.models';
import { RoundsService } from '../../api/rounds.service';
import { AuthService } from '../../auth/auth.service';
import { apiMessage } from '../../shared/api-message';
import { GENERIC_ERROR_MESSAGE } from '../../shared/form-errors';
import { ChartTheme, LineChart, LinePoint, LineSeries } from '../../shared/line-chart';
import { readToken } from '../../shared/read-token';
import { TICKS_PER_SECOND } from '../shared/clock';
import { RecordNotFound } from '../shared/record-not-found';
import { roleLabel } from '../shared/role-label';
import { sideTag } from '../shared/side-tag';

/** The per-Team chart colours, in `points_series` order (a shared Team set has at most 2). */
const TEAM_COLOUR_TOKENS = ['--lf-chart-1', '--lf-chart-2'] as const;

/**
 * A `round_a` / `round_b` query value as an id (INV-1): a whole number >= 1, else `undefined`
 * so the pick is dropped.
 */
export function roundPick(raw: string | null): number | undefined {
  return raw !== null && /^[1-9]\d*$/.test(raw) ? Number(raw) : undefined;
}

/** A stat value as shown: whole numbers as-is, a fraction (MVP) to two decimals. */
function statValue(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(2);
}

/** One stat cell as shown (`compare_rounds.html`). */
interface DeltaCell {
  /** "a → b", or null when both sides are missing (shown as "—"). */
  value: string | null;
  /** "+d" / "d", or null when a side is missing. */
  change: string | null;
  mark: 'up' | 'down' | 'muted' | null;
}

function deltaCell({ a, b, delta }: StatDelta): DeltaCell {
  const mark = delta === null ? 'muted' : delta > 0 ? 'up' : delta < 0 ? 'down' : null;
  if (a === null && b === null) return { value: null, change: null, mark };
  const side = (v: number | null): string => (v === null ? '–' : statValue(v));
  return {
    value: `${side(a)} → ${side(b)}`,
    change: delta === null ? null : `${delta > 0 ? '+' : ''}${statValue(delta)}`,
    mark,
  };
}

/** `[tick, points]` pairs as chart points (ticks are half-seconds). */
function toPoints(pairs: [number, number][]): LinePoint[] {
  return pairs.map(([tick, value]) => ({ sec: tick / TICKS_PER_SECOND, value }));
}

/**
 * `/rounds/compare` (ANG-06-9, `compare_rounds.html`): two Rounds that share a Team, as a
 * per-Player stat-delta table and a points-over-time overlay. The picks live in the URL
 * (`?round_a=&round_b=`); the comparison loads only when both are there.
 */
@Component({
  selector: 'app-compare-rounds',
  imports: [
    LineChart,
    MatButton,
    MatFormField,
    MatInput,
    MatLabel,
    ReactiveFormsModule,
    RecordNotFound,
  ],
  templateUrl: './compare-rounds.html',
  styleUrl: './compare-rounds.scss',
})
export class CompareRounds {
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);

  protected readonly rounds = signal<GameRoundListItem[]>([]);
  protected readonly comparison = signal<RoundComparison | null>(null);
  protected readonly notFound = signal(false);
  private readonly compareError = signal<string | null>(null);
  private readonly listError = signal<string | null>(null);

  /** INV-2 / INV-4: one error box above the picker. */
  protected readonly error = computed(() => this.compareError() ?? this.listError());

  protected readonly form = inject(NonNullableFormBuilder).group({ round_a: '', round_b: '' });

  protected readonly roleLabel = roleLabel;
  protected readonly sideTag = sideTag;

  private readonly selfId = computed(() => this.auth.currentUser()?.self_insert?.id);

  /** The delta rows by name, each with its cells as shown. */
  protected readonly rows = computed(() => {
    const deltas = this.comparison()?.deltas ?? [];
    return [...deltas]
      .sort((x, y) => (x.name < y.name ? -1 : x.name > y.name ? 1 : 0))
      .map((row: PlayerStatDeltaRow) => ({
        ...row,
        isSelf: row.player_id === this.selfId(),
        shown: row.cells.map(deltaCell),
      }));
  });

  /** The chart chrome from the tokens, read when the comparison loads (ADR-0045). */
  protected readonly theme = computed<ChartTheme>(() => {
    this.comparison();
    const muted = readToken('--lf-text-muted');
    return {
      text: muted,
      grid: readToken('--lf-border'),
      cap: muted,
      font: readToken('--lf-font-body'),
    };
  });

  /** "{team} (A)" solid and "{team} (B)" dashed, one token colour per shared Team. */
  protected readonly series = computed<LineSeries[]>(() =>
    (this.comparison()?.points_series ?? []).flatMap((s, i) => {
      const color = readToken(TEAM_COLOUR_TOKENS[i % TEAM_COLOUR_TOKENS.length]);
      return [
        { label: `${s.team_name} (A)`, color, points: toPoints(s.a) },
        { label: `${s.team_name} (B)`, color, dashed: true, points: toPoints(s.b) },
      ];
    }),
  );

  /** The x-axis's end: the last tick of any line, in seconds. */
  protected readonly maxSec = computed(() =>
    Math.max(0, ...this.series().flatMap((s) => s.points.map((p) => p.sec))),
  );

  constructor() {
    const destroyRef = inject(DestroyRef);
    const analytics = inject(AnalyticsService);
    const roundsApi = inject(RoundsService);

    listAll((page) => roundsApi.list({ ...page, ordering: '-id' }))
      .pipe(takeUntilDestroyed(destroyRef))
      .subscribe({
        next: (rounds) => this.rounds.set(rounds),
        error: (error: unknown) => this.listError.set(apiMessage(error, GENERIC_ERROR_MESSAGE)),
      });

    this.route.queryParamMap
      .pipe(
        switchMap((query) => {
          const a = roundPick(query.get('round_a'));
          const b = roundPick(query.get('round_b'));
          this.form.setValue({ round_a: a ? String(a) : '', round_b: b ? String(b) : '' });
          this.comparison.set(null);
          this.notFound.set(false);
          this.compareError.set(null);
          if (a === undefined || b === undefined) return EMPTY;
          return analytics.compareRounds(a, b).pipe(
            catchError((error: unknown) => {
              if (error instanceof ApiError && error.status === 404) this.notFound.set(true);
              else this.compareError.set(apiMessage(error, GENERIC_ERROR_MESSAGE));
              return EMPTY;
            }),
          );
        }),
        takeUntilDestroyed(destroyRef),
      )
      .subscribe((loaded) => this.comparison.set(loaded));
  }

  /** INV-1: Compare writes the picks to the URL, which loads the comparison. */
  protected compare(): void {
    const { round_a, round_b } = this.form.getRawValue();
    void this.router.navigate([], {
      relativeTo: this.route,
      queryParams: { round_a: round_a || null, round_b: round_b || null },
    });
  }
}
