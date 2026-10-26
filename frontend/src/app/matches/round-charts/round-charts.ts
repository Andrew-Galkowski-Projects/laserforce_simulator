import {
  Component,
  DestroyRef,
  ElementRef,
  computed,
  inject,
  linkedSignal,
  signal,
} from '@angular/core';
import { MatAnchor } from '@angular/material/button';
import { MatCheckbox } from '@angular/material/checkbox';
import { RouterLink } from '@angular/router';
import { forkJoin } from 'rxjs';
import { Side } from '../../api/rounds.models';
import { RoundsService } from '../../api/rounds.service';
import { TICKS_PER_SECOND } from '../shared/clock';
import { RecordNotFound } from '../shared/record-not-found';
import { roleLabel } from '../shared/role-label';
import { loadRouteRecord } from '../shared/route-record';
import { sideTag } from '../shared/side-tag';
import { readToken } from '../../shared/read-token';
import { playerColors, resolveColor } from './chart-colors';
import {
  CHART_KEYS,
  CHART_ROLES,
  ChartFilters,
  ChartKey,
  ChartPlayer,
  OVERLAY_KINDS,
  OverlayKind,
  SIDES,
  TotalMode,
  buildEventCharts,
  defaultFilters,
  sideTotals,
  visiblePlayers,
} from './event-charts';
import { ChartTheme, DrawnOverlay, LineChart, LineSeries } from '../../shared/line-chart';

/** Each card's heading and y-axis; SP's Side totals are averages, the others sums. */
const CHART_META: Record<ChartKey, { title: string; total: TotalMode; yMax?: number }> = {
  shots: { title: 'Shots over time', total: 'sum' },
  lives: { title: 'Lives over time', total: 'sum' },
  points: { title: 'Points over time', total: 'sum' },
  sp: { title: 'SP over time', total: 'avg', yMax: 99 },
};

const SIDE_NAMES: Record<Side, string> = { red: 'Red', blue: 'Blue' };

/** One card: its heading, its y-axis and its lines. */
interface ChartCard {
  key: ChartKey;
  title: string;
  yMax?: number;
  datasets: LineSeries[];
}

/** The `ChartFilters` fields that are sets of ticked values. */
type SetFilter = 'sides' | 'roles' | 'players' | 'overlays';

/**
 * `/rounds/:id/charts`: a Round's four event charts (ANG-06g): Shots, Lives, Points and SP
 * over time, one stepped line per player, built from the Round's Playback snapshots, with
 * Chart overlays from its timeline. One filter bar drives all four.
 */
@Component({
  selector: 'app-round-charts',
  imports: [LineChart, MatAnchor, MatCheckbox, RecordNotFound, RouterLink],
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
  protected readonly sides = SIDES;
  protected readonly roles = CHART_ROLES;
  protected readonly overlayKinds = OVERLAY_KINDS;
  protected readonly roleLabel = roleLabel;
  protected readonly sideTag = sideTag;
  protected readonly sideName = (side: Side): string => SIDE_NAMES[side];

  /** Bumped when the OS colour scheme flips, so the colours are read again. */
  private readonly scheme = signal(0);

  private readonly charts = computed(() => {
    const rec = this.load.record();
    return rec ? buildEventCharts(rec.timeline, rec.playback) : null;
  });

  /** The filter bar's state: everything but Side totals ticked whenever a Round loads. */
  protected readonly filters = linkedSignal<ChartFilters>(() =>
    defaultFilters(this.charts()?.players ?? []),
  );

  /** The Round's length in seconds. */
  protected readonly maxSec = computed(
    () => (this.load.record()?.playback.duration_ticks ?? 0) / TICKS_PER_SECOND,
  );

  /** Every token the canvas draws with, read again when the colour scheme flips. */
  private readonly palette = computed(() => {
    this.scheme();
    const side = { red: readToken('--lf-side-red'), blue: readToken('--lf-side-blue') };
    const muted = this.color(readToken('--lf-text-muted'));
    const theme: ChartTheme = {
      text: muted,
      grid: this.color(readToken('--lf-border')),
      cap: muted,
      font: readToken('--lf-font-body'),
    };
    const sides: Record<Side, string> = { red: this.color(side.red), blue: this.color(side.blue) };
    return {
      theme,
      /** The raw tokens the player shades are mixed from. */
      shadeBase: { ...side, mix: readToken('--lf-text') },
      /** Each Side's own colour, for its total line. */
      sides,
      /** Each overlay kind's colour, from its event token. */
      overlays: new Map<OverlayKind, string>(
        OVERLAY_KINDS.map((k) => [k.kind, this.color(readToken(k.token))]),
      ),
    };
  });

  /** The chart chrome's colours and font. */
  protected readonly theme = computed(() => this.palette().theme);

  /** Every player's colour by id, the same on all four charts. */
  protected readonly colors = computed(() => {
    const shades = playerColors(this.charts()?.players ?? [], this.palette().shadeBase);
    return new Map([...shades].map(([id, css]) => [id, this.color(css)]));
  });

  /** The ticked kinds' Chart overlays, drawn on every chart. */
  protected readonly overlays = computed<DrawnOverlay[]>(() => {
    const ticked = this.filters().overlays;
    const colors = this.palette().overlays;
    return (this.charts()?.overlays ?? [])
      .filter((o) => ticked.has(o.kind))
      .map((o) => ({ sec: o.sec, label: o.label, color: colors.get(o.kind) ?? '' }));
  });

  protected readonly cards = computed<ChartCard[]>(() => {
    const charts = this.charts();
    if (!charts) return [];
    const filters = this.filters();
    const visible = visiblePlayers(charts.players, filters);
    const colors = this.colors();
    const sideColors = this.palette().sides;
    return CHART_KEYS.map((key) => {
      const meta = CHART_META[key];
      const datasets: LineSeries[] = charts.players
        .filter((p) => visible.has(p.id))
        .map((p) => ({
          label: p.name,
          color: colors.get(p.id) ?? '',
          points: charts.series[key].get(p.id) ?? [],
        }));
      if (filters.sideTotals) {
        const totals = sideTotals(charts.players, charts.series[key], visible, meta.total);
        for (const side of SIDES) {
          if (totals[side].length === 0) continue;
          datasets.push({
            label: `${SIDE_NAMES[side]} ${meta.total === 'avg' ? 'avg' : 'total'}`,
            color: sideColors[side],
            points: totals[side],
            dashed: true,
          });
        }
      }
      return { key, title: meta.title, yMax: meta.yMax, datasets };
    });
  });

  constructor() {
    const query = window.matchMedia?.('(prefers-color-scheme: dark)');
    if (query) {
      const onChange = (): void => this.scheme.update((n) => n + 1);
      query.addEventListener('change', onChange);
      inject(DestroyRef).onDestroy(() => query.removeEventListener('change', onChange));
    }
  }

  /** Ticks or unticks `value` in one of the filter sets. */
  protected toggle<K extends SetFilter>(
    field: K,
    value: ChartFilters[K] extends ReadonlySet<infer V> ? V : never,
    checked: boolean,
  ): void {
    this.filters.update((f) => {
      const next = new Set(f[field] as ReadonlySet<unknown>);
      if (checked) next.add(value);
      else next.delete(value);
      return { ...f, [field]: next };
    });
  }

  protected setSideTotals(checked: boolean): void {
    this.filters.update((f) => ({ ...f, sideTotals: checked }));
  }

  protected isTicked<K extends SetFilter>(
    field: K,
    value: ChartFilters[K] extends ReadonlySet<infer V> ? V : never,
  ): boolean {
    return (this.filters()[field] as ReadonlySet<unknown>).has(value);
  }

  protected playersOn(side: Side): ChartPlayer[] {
    return (this.charts()?.players ?? []).filter((p) => p.side === side);
  }

  private color(css: string): string {
    return resolveColor(css, this.host);
  }
}
