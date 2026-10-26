import { Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatAnchor } from '@angular/material/button';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { EMPTY, catchError, combineLatest, switchMap } from 'rxjs';
import { ApiError } from '../../api/api-client';
import { CareerStatRow, CareerStats } from '../../api/players.models';
import { PlayersService } from '../../api/players.service';
import { AuthService } from '../../auth/auth.service';
import { ChartTheme, LineChart, LineSeries } from '../../shared/line-chart';
import { readToken } from '../../shared/read-token';
import { BenchmarkFilter, benchmarkParams } from '../role-benchmarks/benchmark-filter';
import { DASH, capitalise, fixed } from '../role-benchmarks/benchmark-format';

/** The trend line's label and its y-axis title (Django's chart). */
const TREND_LABEL = 'Avg points (rolling 10)';

/** Ticks to whole seconds plus "s" (the Django `div:2|floatformat:0` filter, TIME-01). */
const seconds = (ticks: number): string => `${fixed(ticks / 2, 0)}s`;

/** A per-role table row as shown: the Player value and the five benchmark cells. */
interface StatCells {
  key: string;
  label: string;
  value: string;
  /** Mean, Median, Δ, Percentile, n; all five a dash when the stat has no benchmark. */
  benchmark: [string, string, string, string, string];
}

/**
 * `/players/:id/stats`: a Player's career (ANG-06-8, `player_career_stats.html`): the totals,
 * one table per role against the role benchmarks, and the points trend. The filters live in the
 * URL (`?threshold=&display=`) and reload the career when they change.
 */
@Component({
  selector: 'app-player-career',
  imports: [BenchmarkFilter, LineChart, MatAnchor, RouterLink],
  templateUrl: './player-career.html',
  styleUrl: '../role-benchmarks/benchmark-page.scss',
})
export class PlayerCareer {
  private readonly auth = inject(AuthService);

  protected readonly career = signal<CareerStats | null>(null);
  protected readonly notFound = signal(false);
  protected readonly loadError = signal<string | null>(null);

  /** I4: this Player is the signed-in Account's Self-insert. */
  protected readonly isSelf = computed(() => {
    const selfId = this.auth.currentUser()?.self_insert?.id;
    return selfId !== undefined && this.career()?.player.id === selfId;
  });
  protected readonly capitalise = capitalise;
  protected readonly trendLabel = TREND_LABEL;

  protected readonly totals = computed(() => {
    const c = this.career()?.career;
    if (!c) return [];
    return [
      String(c.games),
      fixed(c.avg_points, 1),
      fixed(c.tag_ratio, 2),
      seconds(c.avg_survival_ticks),
      `${fixed(c.avg_accuracy_pct, 0)}%`,
      fixed(c.avg_sp_earned, 1),
    ];
  });

  protected readonly roles = computed(() => {
    const c = this.career();
    if (!c) return [];
    return c.per_role_with_benchmarks.map((r) => ({
      role: r.role,
      games: r.games,
      rows: r.stat_rows.map((s) => this.cells(s, c)),
    }));
  });

  /** The chart chrome from the tokens, read when the career loads (ADR-0045). */
  protected readonly theme = computed<ChartTheme>(() => {
    this.career();
    const muted = readToken('--lf-text-muted');
    return {
      text: muted,
      grid: readToken('--lf-border'),
      cap: muted,
      font: readToken('--lf-font-body'),
    };
  });

  protected readonly trend = computed<LineSeries[]>(() => [
    {
      label: TREND_LABEL,
      color: readToken('--lf-accent'),
      dashed: true,
      pointRadius: 2,
      points: (this.career()?.trend ?? []).map(([round, mean]) => ({ sec: round, value: mean })),
    },
  ]);

  /** The last round number: the x-axis's end. */
  protected readonly lastRound = computed(() => this.career()?.trend.at(-1)?.[0] ?? 0);

  constructor() {
    const route = inject(ActivatedRoute);
    const players = inject(PlayersService);
    combineLatest([route.paramMap, route.queryParamMap])
      .pipe(
        switchMap(([params, query]) => {
          this.career.set(null);
          this.notFound.set(false);
          this.loadError.set(null);
          return players.career(Number(params.get('id')), benchmarkParams(query)).pipe(
            catchError((error: unknown) => {
              if (error instanceof ApiError && error.status === 404) this.notFound.set(true);
              else this.loadError.set(error instanceof Error ? error.message : String(error));
              return EMPTY;
            }),
          );
        }),
        takeUntilDestroyed(inject(DestroyRef)),
      )
      .subscribe((loaded) => this.career.set(loaded));
  }

  /** One stat row, formatted as `player_career_stats.html` does. */
  private cells(stat: CareerStatRow, c: CareerStats): StatCells {
    const b = stat.benchmark;
    const dash = [DASH, DASH, DASH, DASH, DASH] as StatCells['benchmark'];
    if (!b) return { key: stat.key, label: stat.label, value: playerValue(stat), benchmark: dash };
    const accuracy = stat.key === 'avg_accuracy_pct';
    const benchmarkValue = (v: number | null): string =>
      v === null ? DASH : accuracy ? `${fixed(v, 0)}%` : fixed(v, 2);
    const delta = c.display === 'median' ? b.delta_median : b.delta_mean;
    let deltaCell: string;
    if (!b.qualified) deltaCell = `${DASH} (need ${c.min_rounds}+ rounds)`;
    else if (delta === null) deltaCell = DASH;
    else deltaCell = `(${delta >= 0 ? '+' : ''}${fixed(delta, 2)})`;
    return {
      key: stat.key,
      label: stat.label,
      value: playerValue(stat),
      benchmark: [
        benchmarkValue(b.benchmark_mean),
        benchmarkValue(b.benchmark_median),
        deltaCell,
        b.percentile === null ? '' : `p${b.percentile}`,
        `n = ${b.n}`,
      ],
    };
  }
}

/** The Player value, by stat key (the Django template's `if` chain). */
function playerValue(stat: CareerStatRow): string {
  const v = stat.player_value;
  switch (stat.key) {
    case 'tag_ratio':
    case 'mvp':
      return fixed(v, 2);
    case 'avg_survival_ticks':
      return seconds(v);
    case 'avg_accuracy_pct':
      return `${fixed(v, 0)}%`;
    default:
      // avg_points, avg_sp_earned and every other stat: 1 decimal.
      return fixed(v, 1);
  }
}
