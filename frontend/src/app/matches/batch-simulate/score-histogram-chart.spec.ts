import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Chart } from 'chart.js';
import { stubCanvasContext } from '../testing/fake-canvas';
import { ScoreHistogram } from './score-histogram';
import { ScoreHistogramChart } from './score-histogram-chart';

const HIST: ScoreHistogram = {
  labels: ['0–5,000', '5,000–10,000'],
  red: [1, 3],
  blue: [2, 0],
};

const TOKENS: Record<string, string> = {
  '--lf-side-red': 'rgb(200, 0, 0)',
  '--lf-side-blue': 'rgb(0, 0, 200)',
  '--lf-text-muted': 'rgb(1, 2, 3)',
  '--lf-border': 'rgb(4, 5, 6)',
  '--lf-font-body': 'Test Mono',
};

describe('ScoreHistogramChart', () => {
  let fixture: ComponentFixture<ScoreHistogramChart>;
  let restore: () => void;

  async function render(
    inputs: Partial<Record<string, unknown>> = {},
  ): Promise<ScoreHistogramChart> {
    fixture = TestBed.createComponent(ScoreHistogramChart);
    const all = { histogram: HIST, redName: 'Alpha', blueName: 'Bravo', ...inputs };
    for (const [name, value] of Object.entries(all)) fixture.componentRef.setInput(name, value);
    await fixture.whenStable();
    return fixture.componentInstance;
  }

  const canvas = (): HTMLCanvasElement =>
    (fixture.nativeElement as HTMLElement).querySelector('canvas') as HTMLCanvasElement;

  beforeEach(() => {
    restore = stubCanvasContext();
    const root = document.documentElement.style;
    for (const [name, value] of Object.entries(TOKENS)) root.setProperty(name, value);
  });

  afterEach(() => {
    fixture?.destroy();
    restore();
    const root = document.documentElement.style;
    for (const name of Object.keys(TOKENS)) root.removeProperty(name);
  });

  it('names the canvas as an image "Score distribution"', async () => {
    await render();
    expect(canvas().getAttribute('role')).toBe('img');
    expect(canvas().getAttribute('aria-label')).toBe('Score distribution');
  });

  it('draws one bar series per Team, named for it, over the bin labels', async () => {
    const config = (await render()).config();
    expect(config.type).toBe('bar');
    expect(config.data.labels).toEqual(['0–5,000', '5,000–10,000']);
    expect(config.data.datasets.map((d) => [d.label, d.data])).toEqual([
      ['Alpha', [1, 3]],
      ['Bravo', [2, 0]],
    ]);
  });

  it("outlines each Team's bars in its Side colour", async () => {
    const [red, blue] = (await render()).config().data.datasets;
    expect(red.borderColor).toBe('rgb(200, 0, 0)');
    expect(blue.borderColor).toBe('rgb(0, 0, 200)');
  });

  it('titles the axes "Score range" and "Number of simulations", from 0, in the theme', async () => {
    const options = (await render()).config().options;
    const x = options?.scales?.['x'];
    const y = options?.scales?.['y'] as { beginAtZero?: boolean; title?: unknown };
    expect(x?.title).toMatchObject({ display: true, text: 'Score range', color: 'rgb(1, 2, 3)' });
    expect(y.title).toMatchObject({ display: true, text: 'Number of simulations' });
    expect(y.beginAtZero).toBe(true);
    expect(x?.grid?.color).toBe('rgb(4, 5, 6)');
    expect(options?.plugins?.legend?.position).toBe('top');
    expect(options?.plugins?.legend?.labels?.font).toEqual({ family: 'Test Mono' });
  });

  it('updates the one chart in place when the histogram changes, and destroys it', async () => {
    await render();
    const chart = Chart.getChart(canvas());
    expect(chart?.data.datasets.map((d) => d.data)).toEqual([
      [1, 3],
      [2, 0],
    ]);

    fixture.componentRef.setInput('histogram', { labels: ['0–5,000'], red: [4], blue: [5] });
    await fixture.whenStable();
    expect(Chart.getChart(canvas())).toBe(chart);
    expect(chart?.data.labels).toEqual(['0–5,000']);
    expect(chart?.data.datasets.map((d) => d.data)).toEqual([[4], [5]]);

    const el = canvas();
    fixture.destroy();
    expect(Chart.getChart(el)).toBeUndefined();
  });
});
