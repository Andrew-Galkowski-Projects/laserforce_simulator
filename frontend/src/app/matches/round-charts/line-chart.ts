import {
  Component,
  ElementRef,
  afterNextRender,
  computed,
  effect,
  inject,
  DestroyRef,
  input,
  viewChild,
} from '@angular/core';
import {
  Chart,
  ChartConfiguration,
  Legend,
  LineController,
  LineElement,
  LinearScale,
  Plugin,
  PointElement,
  Tooltip,
  TooltipItem,
} from 'chart.js';
import { TICKS_PER_SECOND, tickClock } from '../shared/clock';
import { ChartPoint } from './event-charts';

// Only what a line chart draws (ADR-0045): no `registerables`. Our own plugins are never
// registered here; each chart gets them inline through its `plugins:` array (the RES-02 lesson).
Chart.register(LineController, LineElement, PointElement, LinearScale, Legend, Tooltip);

/** One line on the chart. */
export interface LineSeries {
  label: string;
  /** A concrete colour (the canvas cannot read a CSS variable). */
  color: string;
  points: readonly ChartPoint[];
  /** A Side total: drawn dashed. */
  dashed?: boolean;
}

/** A **Chart overlay** as drawn: a vertical line at `sec`, labelled, in a concrete colour. */
export interface DrawnOverlay {
  sec: number;
  label: string;
  color: string;
}

/** The chart chrome's colours, resolved from the `--lf-*` tokens. */
export interface ChartTheme {
  /** Tick labels, axis titles, the legend. */
  text: string;
  grid: string;
  /** The SP cap line. */
  cap: string;
}

/** The second as `mm:ss`. */
const clock = (sec: number): string => tickClock(sec * TICKS_PER_SECOND, { pad: true });

/**
 * Holds each line's last value to the end of the Round, so a player whose value stopped
 * changing still reads across the whole chart.
 */
function toData(points: readonly ChartPoint[], maxSec: number): { x: number; y: number }[] {
  const data = points.map((p) => ({ x: p.sec, y: p.value }));
  const last = data.at(-1);
  if (last && last.x < maxSec) data.push({ x: maxSec, y: last.y });
  return data;
}

/**
 * `app-line-chart`: one Chart.js line chart over a Round (ADR-0045). Every input change updates
 * the chart in place; the chart is destroyed with the component.
 */
@Component({
  selector: 'app-line-chart',
  template: `<canvas #canvas role="img" [attr.aria-label]="title()"></canvas>`,
  styles: `
    :host {
      display: block;
      position: relative;
      height: 100%;
    }
  `,
})
export class LineChart {
  /** The chart's accessible name (the card heading). */
  readonly title = input.required<string>();
  readonly datasets = input.required<readonly LineSeries[]>();
  /** The Round's length in seconds: the x-axis's end. */
  readonly maxSec = input.required<number>();
  readonly theme = input.required<ChartTheme>();
  /** Fixes the y-axis at 0 to `yMax` (SP: 99). */
  readonly yMax = input<number | undefined>(undefined);
  /** Draws a dashed line at `yMax` (the SP cap). */
  readonly capLine = input(false);
  /** The Chart overlays to draw (already filtered by kind). */
  readonly overlays = input<readonly DrawnOverlay[]>([]);

  private readonly canvas = viewChild.required<ElementRef<HTMLCanvasElement>>('canvas');
  private chart: Chart<'line'> | null = null;

  /** The SP cap: a dashed line across the chart at `yMax`. */
  private readonly capPlugin: Plugin<'line'> = {
    id: 'lfCapLine',
    afterDatasetsDraw: (chart) => {
      const max = this.yMax();
      if (max === undefined) return;
      const { ctx, chartArea } = chart;
      const y = chart.scales['y'].getPixelForValue(max);
      ctx.save();
      ctx.strokeStyle = this.theme().cap;
      ctx.lineWidth = 1;
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(chartArea.left, y);
      ctx.lineTo(chartArea.right, y);
      ctx.stroke();
      ctx.restore();
    },
  };

  /**
   * The Chart overlays (RES-02's pattern): a vertical line across the chart area per overlay and
   * its label, rotated, at the top. It reads the `overlays` input at draw time.
   */
  private readonly overlayPlugin: Plugin<'line'> = {
    id: 'lfOverlays',
    afterDatasetsDraw: (chart) => {
      const { ctx, chartArea } = chart;
      for (const overlay of this.overlays()) {
        const x = chart.scales['x'].getPixelForValue(overlay.sec);
        ctx.save();
        ctx.globalAlpha = 0.55;
        ctx.strokeStyle = overlay.color;
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.moveTo(x, chartArea.top);
        ctx.lineTo(x, chartArea.bottom);
        ctx.stroke();
        ctx.globalAlpha = 1;
        ctx.fillStyle = overlay.color;
        ctx.font = '10px sans-serif';
        ctx.textAlign = 'right';
        ctx.translate(x - 2, chartArea.top + 2);
        ctx.rotate(-Math.PI / 2);
        ctx.fillText(overlay.label, 0, 0);
        ctx.restore();
      }
    },
  };

  /** The whole Chart.js config, built from the inputs (read by specs: jsdom has no canvas). */
  readonly config = computed<ChartConfiguration<'line'>>(() => {
    const theme = this.theme();
    const maxSec = this.maxSec();
    const yMax = this.yMax();
    const axis = { ticks: { color: theme.text }, grid: { color: theme.grid } };
    return {
      type: 'line',
      data: {
        datasets: this.datasets().map((s) => ({
          label: s.label,
          data: toData(s.points, maxSec),
          borderColor: s.color,
          backgroundColor: s.color,
          borderWidth: 1.5,
          borderDash: s.dashed ? [6, 4] : [],
          pointRadius: 0,
          stepped: 'before' as const,
        })),
      },
      options: {
        animation: false,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        scales: {
          x: {
            type: 'linear',
            min: 0,
            max: maxSec,
            title: { display: true, text: 'Time (s)', color: theme.text },
            ...axis,
            ticks: { ...axis.ticks, stepSize: 60, callback: (value) => clock(Number(value)) },
          },
          y: yMax === undefined ? { beginAtZero: true, ...axis } : { min: 0, max: yMax, ...axis },
        },
        plugins: {
          legend: { position: 'top', labels: { color: theme.text } },
          tooltip: {
            callbacks: { title: (items: TooltipItem<'line'>[]) => clock(items[0]?.parsed.x ?? 0) },
          },
        },
      },
      plugins: this.capLine() ? [this.overlayPlugin, this.capPlugin] : [this.overlayPlugin],
    };
  });

  constructor() {
    afterNextRender(() => {
      const canvas = this.canvas().nativeElement;
      if (!canvas.getContext('2d')) return;
      this.chart = new Chart(canvas, this.config());
    });
    effect(() => {
      const config = this.config();
      this.overlays(); // The overlay plugin reads them at draw time: redraw when they change.
      if (!this.chart) return;
      this.chart.data = config.data;
      this.chart.options = config.options ?? {};
      this.chart.update('none');
    });
    inject(DestroyRef).onDestroy(() => {
      this.chart?.destroy();
      this.chart = null;
    });
  }
}
