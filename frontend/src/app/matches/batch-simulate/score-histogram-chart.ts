import {
  Component,
  DestroyRef,
  ElementRef,
  afterNextRender,
  computed,
  effect,
  inject,
  input,
  signal,
  viewChild,
} from '@angular/core';
import {
  BarController,
  BarElement,
  CategoryScale,
  Chart,
  ChartConfiguration,
  Legend,
  LinearScale,
  Tooltip,
} from 'chart.js';
import { readToken, resolveColor } from '../../shared/read-token';
import { ScoreHistogram } from './score-histogram';

// Only what a bar chart draws (ADR-0045): no `registerables`.
Chart.register(BarController, BarElement, CategoryScale, LinearScale, Legend, Tooltip);

/**
 * `app-score-histogram`: the Batch run's score distribution as a Chart.js bar chart, one series
 * per Team in its Side colour (ADR-0045). Every input change updates the chart in place; the
 * chart is destroyed with the component.
 */
@Component({
  selector: 'app-score-histogram',
  template: `<canvas #canvas role="img" aria-label="Score distribution"></canvas>`,
  styles: `
    :host {
      display: block;
      position: relative;
      height: 100%;
    }
  `,
})
export class ScoreHistogramChart {
  readonly histogram = input.required<ScoreHistogram>();
  readonly redName = input.required<string>();
  readonly blueName = input.required<string>();

  private readonly host = inject(ElementRef<HTMLElement>).nativeElement as HTMLElement;
  private readonly canvas = viewChild.required<ElementRef<HTMLCanvasElement>>('canvas');
  private chart: Chart<'bar'> | null = null;

  /** Bumped when the OS colour scheme flips, so the colours are read again. */
  private readonly scheme = signal(0);

  /** Every token the canvas draws with (a canvas cannot read a CSS variable). */
  private readonly palette = computed(() => {
    this.scheme();
    const color = (css: string): string => resolveColor(css, this.host);
    const side = (token: string) => {
      const base = readToken(token);
      return {
        border: color(base),
        fill: color(`color-mix(in srgb, ${base} 60%, transparent)`),
      };
    };
    return {
      red: side('--lf-side-red'),
      blue: side('--lf-side-blue'),
      text: color(readToken('--lf-text-muted')),
      grid: color(readToken('--lf-border')),
      font: readToken('--lf-font-body'),
    };
  });

  /** The whole Chart.js config, built from the inputs (read by specs: jsdom has no canvas). */
  readonly config = computed<ChartConfiguration<'bar'>>(() => {
    const hist = this.histogram();
    const p = this.palette();
    const font = { family: p.font };
    const axis = { ticks: { color: p.text, font }, grid: { color: p.grid } };
    const title = (text: string) => ({ display: true, text, color: p.text, font });
    const series = (label: string, data: number[], colors: { border: string; fill: string }) => ({
      label,
      data,
      backgroundColor: colors.fill,
      borderColor: colors.border,
      borderWidth: 1,
    });
    return {
      type: 'bar',
      data: {
        labels: hist.labels,
        datasets: [
          series(this.redName(), hist.red, p.red),
          series(this.blueName(), hist.blue, p.blue),
        ],
      },
      options: {
        animation: false,
        maintainAspectRatio: false,
        scales: {
          x: { ...axis, title: title('Score range') },
          y: { ...axis, beginAtZero: true, title: title('Number of simulations') },
        },
        plugins: {
          legend: { position: 'top', labels: { color: p.text, font } },
          tooltip: { titleFont: font, bodyFont: font },
        },
      },
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
      if (!this.chart) return;
      this.chart.data = config.data;
      this.chart.options = config.options ?? {};
      this.chart.update('none');
    });
    const destroyRef = inject(DestroyRef);
    const query = window.matchMedia?.('(prefers-color-scheme: dark)');
    if (query) {
      const onChange = (): void => this.scheme.update((n) => n + 1);
      query.addEventListener('change', onChange);
      destroyRef.onDestroy(() => query.removeEventListener('change', onChange));
    }
    destroyRef.onDestroy(() => {
      this.chart?.destroy();
      this.chart = null;
    });
  }
}
