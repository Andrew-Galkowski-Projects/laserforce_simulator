import { Component, DestroyRef, ElementRef, computed, inject, signal } from '@angular/core';
import { MatAnchor } from '@angular/material/button';
import { RouterLink } from '@angular/router';
import { forkJoin } from 'rxjs';
import { RoundsService } from '../../api/rounds.service';
import { TICKS_PER_SECOND } from '../shared/clock';
import { RecordNotFound } from '../shared/record-not-found';
import { loadRouteRecord } from '../shared/route-record';
import { playerColors, readToken, resolveColor } from './chart-colors';
import { CHART_KEYS, ChartKey, buildEventCharts } from './event-charts';
import { ChartTheme, LineChart, LineSeries } from './line-chart';

/** Each card's heading and y-axis. */
const CHART_META: Record<ChartKey, { title: string; yMax?: number; capLine?: boolean }> = {
  shots: { title: 'Shots over time' },
  lives: { title: 'Lives over time' },
  points: { title: 'Points over time' },
  sp: { title: 'SP over time', yMax: 99, capLine: true },
};

/** One card: its heading, its y-axis and its lines. */
interface ChartCard {
  key: ChartKey;
  title: string;
  yMax?: number;
  capLine: boolean;
  datasets: LineSeries[];
}

/**
 * `/rounds/:id/charts`: a Round's four event charts (ANG-06g): Shots, Lives, Points and SP
 * over time, one stepped line per player, built from the Round's Playback snapshots.
 */
@Component({
  selector: 'app-round-charts',
  imports: [LineChart, MatAnchor, RecordNotFound, RouterLink],
  templateUrl: './round-charts.html',
  styleUrl: './round-charts.scss',
})
export class RoundCharts {
  private readonly rounds = inject(RoundsService);
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef).nativeElement;

  protected readonly load = loadRouteRecord('Round', (id) =>
    forkJoin({
      round: this.rounds.get(id),
      timeline: this.rounds.timeline(id),
      playback: this.rounds.playback(id),
    }),
  );

  /** Bumped when the OS colour scheme flips, so the colours are read again. */
  private readonly scheme = signal(0);

  constructor() {
    const query = window.matchMedia?.('(prefers-color-scheme: dark)');
    if (query) {
      const onChange = (): void => this.scheme.update((n) => n + 1);
      query.addEventListener('change', onChange);
      inject(DestroyRef).onDestroy(() => query.removeEventListener('change', onChange));
    }
  }

  private readonly charts = computed(() => {
    const rec = this.load.record();
    return rec ? buildEventCharts(rec.timeline, rec.playback) : null;
  });

  /** The Round's length in seconds. */
  protected readonly maxSec = computed(
    () => (this.load.record()?.playback.duration_ticks ?? 0) / TICKS_PER_SECOND,
  );

  /** The chart chrome's colours, resolved from the tokens. */
  protected readonly theme = computed<ChartTheme>(() => {
    this.scheme();
    const muted = this.color(readToken('--lf-text-muted'));
    return { text: muted, grid: this.color(readToken('--lf-border')), cap: muted };
  });

  /** Every player's colour by id, the same on all four charts. */
  private readonly colors = computed(() => {
    this.scheme();
    const players = this.charts()?.players ?? [];
    const shades = playerColors(players, {
      red: readToken('--lf-side-red'),
      blue: readToken('--lf-side-blue'),
      mix: readToken('--lf-text'),
    });
    return new Map([...shades].map(([id, css]) => [id, this.color(css)]));
  });

  protected readonly cards = computed<ChartCard[]>(() => {
    const charts = this.charts();
    if (!charts) return [];
    const colors = this.colors();
    return CHART_KEYS.map((key) => ({
      key,
      ...CHART_META[key],
      capLine: CHART_META[key].capLine ?? false,
      datasets: charts.players.map((p) => ({
        label: p.name,
        color: colors.get(p.id) ?? '',
        points: charts.series[key].get(p.id) ?? [],
      })),
    }));
  });

  private color(css: string): string {
    return resolveColor(css, this.host);
  }
}
