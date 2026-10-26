import { Component, computed, input } from '@angular/core';
import { H2HSeries } from '../../api/analytics.models';
import { ChartTheme, LineChart, LineSeries, LinePoint } from '../line-chart';
import { readToken } from '../read-token';

const MARGIN_TITLE = 'Margin per round (A − B)';
const CUMULATIVE_TITLE = 'Cumulative round W − L (A − B)';

const toPoints = (series: H2HSeries): LinePoint[] =>
  series.map(([round, value]) => ({ sec: round, value }));

/**
 * The two head-to-head charts (ADR-0045, `count` x-axis by Round #): the margin per Round with a
 * dashed "Zero" line, and the cumulative Round W − L. A chart with an empty series is left out.
 */
@Component({
  selector: 'app-h2h-charts',
  imports: [LineChart],
  template: `
    <div class="h2h-charts">
      @if (margin().length > 0) {
        <section>
          <h3>{{ marginTitle }}</h3>
          <div class="h2h-chart">
            <app-line-chart
              [title]="marginTitle"
              [datasets]="marginDatasets()"
              [maxSec]="lastRound(margin())"
              [theme]="theme()"
              xAxis="count"
              xTitle="Round #"
              yTitle="Margin"
            />
          </div>
        </section>
      }
      @if (cumulative().length > 0) {
        <section>
          <h3>{{ cumulativeTitle }}</h3>
          <div class="h2h-chart">
            <app-line-chart
              [title]="cumulativeTitle"
              [datasets]="cumulativeDatasets()"
              [maxSec]="lastRound(cumulative())"
              [theme]="theme()"
              xAxis="count"
              xTitle="Round #"
              yTitle="Cumulative W − L"
            />
          </div>
        </section>
      }
    </div>
  `,
  styleUrl: './h2h-section.scss',
})
export class H2HCharts {
  /** `margin_series`: `[round #, A − B]`. */
  readonly margin = input.required<H2HSeries>();
  /** `cumulative_wl_series`: `[round #, A wins − B wins]`. */
  readonly cumulative = input.required<H2HSeries>();

  protected readonly marginTitle = MARGIN_TITLE;
  protected readonly cumulativeTitle = CUMULATIVE_TITLE;

  /** The chart chrome from the tokens, read when the series change (canvas ignores CSS). */
  protected readonly theme = computed<ChartTheme>(() => {
    this.margin();
    this.cumulative();
    const muted = readToken('--lf-text-muted');
    return {
      text: muted,
      grid: readToken('--lf-border'),
      cap: muted,
      font: readToken('--lf-font-body'),
    };
  });

  protected readonly marginDatasets = computed<LineSeries[]>(() => {
    const points = toPoints(this.margin());
    const first = points[0]?.sec ?? 0;
    const last = points.at(-1)?.sec ?? 0;
    return [
      { label: 'Margin (A − B)', color: readToken('--lf-accent'), pointRadius: 3, points },
      {
        label: 'Zero',
        color: readToken('--lf-text-muted'),
        dashed: true,
        points: [
          { sec: first, value: 0 },
          { sec: last, value: 0 },
        ],
      },
    ];
  });

  protected readonly cumulativeDatasets = computed<LineSeries[]>(() => [
    {
      label: 'Cumulative (A wins − B wins)',
      color: readToken('--lf-accent'),
      pointRadius: 3,
      points: toPoints(this.cumulative()),
    },
  ]);

  /** The x-axis's end: the last Round #. */
  protected lastRound(series: H2HSeries): number {
    return series.at(-1)?.[0] ?? 0;
  }
}
