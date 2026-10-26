import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Chart, TooltipItem } from 'chart.js';
import { stubCanvasContext } from '../matches/testing/fake-canvas';
import { ChartTheme, DrawnOverlay, LineChart, LineSeries } from './line-chart';

const THEME: ChartTheme = {
  text: 'rgb(1, 2, 3)',
  grid: 'rgb(4, 5, 6)',
  cap: 'rgb(7, 8, 9)',
  font: 'Test Mono',
};

const ACE: LineSeries = {
  label: 'Ace',
  color: 'rgb(200, 0, 0)',
  points: [
    { sec: 0, value: 30 },
    { sec: 60, value: 29 },
  ],
};

describe('LineChart', () => {
  let fixture: ComponentFixture<LineChart>;
  let restore: () => void;

  async function render(inputs: Partial<Record<string, unknown>> = {}): Promise<LineChart> {
    fixture = TestBed.createComponent(LineChart);
    const all = { title: 'Shots over time', datasets: [ACE], maxSec: 900, theme: THEME, ...inputs };
    for (const [name, value] of Object.entries(all)) fixture.componentRef.setInput(name, value);
    await fixture.whenStable();
    return fixture.componentInstance;
  }

  const canvas = (): HTMLCanvasElement =>
    (fixture.nativeElement as HTMLElement).querySelector('canvas') as HTMLCanvasElement;

  beforeEach(() => {
    restore = stubCanvasContext();
  });

  afterEach(() => {
    fixture?.destroy();
    restore();
  });

  it('names the canvas as an image with the chart title', async () => {
    await render();
    expect(canvas().getAttribute('role')).toBe('img');
    expect(canvas().getAttribute('aria-label')).toBe('Shots over time');
  });

  it('draws each series as a stepped line with no point markers', async () => {
    const chart = await render();
    const [ds] = chart.config().data.datasets;
    expect(ds.label).toBe('Ace');
    expect(ds.data).toEqual([
      { x: 0, y: 30 },
      { x: 60, y: 29 },
      { x: 900, y: 29 },
    ]);
    expect(ds.stepped).toBe('before');
    expect(ds.pointRadius).toBe(0);
    expect(ds.borderWidth).toBe(1.5);
    expect(ds.borderColor).toBe('rgb(200, 0, 0)');
    expect(ds.borderDash).toEqual([]);
  });

  it('dashes a dashed series', async () => {
    const chart = await render({ datasets: [{ ...ACE, dashed: true }] });
    expect(chart.config().data.datasets[0].borderDash).toEqual([6, 4]);
  });

  it('does not extend a series that already reaches the end, or an empty one', async () => {
    const chart = await render({
      datasets: [
        {
          ...ACE,
          points: [
            { sec: 0, value: 1 },
            { sec: 900, value: 2 },
          ],
        },
        { ...ACE, label: 'Gone', points: [] },
      ],
    });
    const [full, empty] = chart.config().data.datasets;
    expect(full.data).toEqual([
      { x: 0, y: 1 },
      { x: 900, y: 2 },
    ]);
    expect(empty.data).toEqual([]);
  });

  it('runs the x-axis from 0 to the round length in seconds, labelled mm:ss', async () => {
    const chart = await render();
    const x = chart.config().options?.scales?.['x'];
    expect(x?.type).toBe('linear');
    expect(x?.min).toBe(0);
    expect(x?.max).toBe(900);
    const tickLabel = x?.ticks?.callback as (value: number) => string;
    expect(tickLabel(90)).toBe('01:30');
  });

  it('titles the tooltip with the mm:ss of the hovered second', async () => {
    const chart = await render();
    const tooltip = chart.config().options?.plugins?.tooltip;
    expect(chart.config().options?.interaction).toEqual({ mode: 'index', intersect: false });
    const title = tooltip?.callbacks?.title as unknown as (items: TooltipItem<'line'>[]) => string;
    expect(title([{ parsed: { x: 125, y: 0 } } as TooltipItem<'line'>])).toBe('02:05');
  });

  it('caps the y-axis at yMax and draws the cap line only then', async () => {
    const plain = await render();
    expect(plain.config().options?.scales?.['y']?.max).toBeUndefined();
    expect(plain.config().plugins?.map((p) => p.id)).not.toContain('lfCapLine');
    expect(plain.config().plugins?.map((p) => p.id)).toContain('lfOverlays');
    fixture.destroy();

    const sp = await render({ yMax: 99 });
    const y = sp.config().options?.scales?.['y'];
    expect(y?.min).toBe(0);
    expect(y?.max).toBe(99);
    expect(sp.config().plugins?.map((p) => p.id)).toContain('lfCapLine');
  });

  describe('Chart overlays', () => {
    const OVERLAYS: DrawnOverlay[] = [
      { sec: 120, label: 'Bolt', color: 'rgb(1, 1, 1)' },
      { sec: 150, label: 'Ace', color: 'rgb(2, 2, 2)' },
    ];

    /** Runs the overlay plugin's draw against a recording context; returns what it did. */
    function draw(chart: LineChart): string[] {
      const calls: string[] = [];
      const ctx = new Proxy({} as Record<string, unknown>, {
        get: (target, prop: string) =>
          prop in target ? target[prop] : (...args: unknown[]) => calls.push(`${prop}(${args})`),
        set: (target, prop: string, value) => {
          calls.push(`${prop}=${value}`);
          target[prop] = value;
          return true;
        },
      });
      const fakeChart = {
        ctx,
        chartArea: { top: 10, bottom: 210, left: 0, right: 500 },
        scales: { x: { getPixelForValue: (v: number) => v * 2 } },
      };
      const plugin = chart.config().plugins?.find((p) => p.id === 'lfOverlays');
      (plugin?.afterDatasetsDraw as unknown as (c: typeof fakeChart) => void)(fakeChart);
      return calls;
    }

    it('passes the overlay plugin inline on every chart', async () => {
      const chart = await render();
      expect(chart.config().plugins?.map((p) => p.id)).toContain('lfOverlays');
      expect(Chart.registry.plugins.get('lfOverlays')).toBeUndefined();
    });

    it('draws a vertical line in its colour and a label for each overlay, at its second', async () => {
      const calls = draw(await render({ overlays: OVERLAYS }));
      expect(calls).toContain('strokeStyle=rgb(1, 1, 1)');
      expect(calls).toContain('moveTo(240,10)');
      expect(calls).toContain('lineTo(240,210)');
      expect(calls).toContain('fillText(Bolt,0,0)');
      expect(calls).toContain('strokeStyle=rgb(2, 2, 2)');
      expect(calls).toContain('moveTo(300,10)');
      expect(calls).toContain('fillText(Ace,0,0)');
    });

    it('labels the overlays in the theme font', async () => {
      expect(draw(await render({ overlays: OVERLAYS }))).toContain('font=10px Test Mono');
    });

    it('draws nothing with no overlays', async () => {
      expect(draw(await render())).toEqual([]);
    });

    it('redraws when the overlays change', async () => {
      await render();
      const chart = Chart.getChart(canvas());
      const update = vi.spyOn(chart as Chart, 'update');
      fixture.componentRef.setInput('overlays', OVERLAYS);
      await fixture.whenStable();
      expect(update).toHaveBeenCalled();
    });
  });

  describe('a count axis (the career points trend)', () => {
    const TREND: LineSeries = {
      label: 'Avg points (rolling 10)',
      color: 'rgb(9, 9, 9)',
      points: [
        { sec: 1, value: 2500 },
        { sec: 2, value: 4500 },
      ],
      pointRadius: 2,
    };

    it('titles the clock axis "Time (s)" and leaves the y-axis untitled by default', async () => {
      const options = (await render()).config().options;
      expect(options?.scales?.['x']?.title?.text).toBe('Time (s)');
      expect(options?.scales?.['y']?.title?.display).toBeFalsy();
    });

    it('titles both axes in the theme colour and font when given titles', async () => {
      const chart = await render({
        datasets: [TREND],
        maxSec: 2,
        xAxis: 'count',
        xTitle: 'Round number',
        yTitle: 'Avg points (rolling 10)',
      });
      const scales = chart.config().options?.scales;
      expect(scales?.['x']?.title).toMatchObject({
        display: true,
        text: 'Round number',
        color: THEME.text,
        font: { family: 'Test Mono' },
      });
      expect(scales?.['y']?.title).toMatchObject({
        display: true,
        text: 'Avg points (rolling 10)',
        color: THEME.text,
        font: { family: 'Test Mono' },
      });
    });

    it('draws straight lines with the series point radius, labelled with plain numbers', async () => {
      const chart = await render({ datasets: [TREND], maxSec: 2, xAxis: 'count' });
      const [ds] = chart.config().data.datasets;
      expect(ds.stepped).toBe(false);
      expect(ds.pointRadius).toBe(2);
      expect(ds.data).toEqual([
        { x: 1, y: 2500 },
        { x: 2, y: 4500 },
      ]);
      const x = chart.config().options?.scales?.['x'];
      expect(x?.min).toBeUndefined();
      expect(x?.max).toBe(2);
      const ticks = x?.ticks as { stepSize?: number; precision?: number };
      expect(ticks.stepSize).toBeUndefined();
      expect(ticks.precision).toBe(0);
      const tooltip = chart.config().options?.plugins?.tooltip;
      const title = tooltip?.callbacks?.title as unknown as (
        items: TooltipItem<'line'>[],
      ) => string;
      expect(title([{ parsed: { x: 7, y: 0 } } as TooltipItem<'line'>])).toBe('7');
    });
  });

  it('passes its plugins inline and never registers them globally', async () => {
    await render({ yMax: 99 });
    expect(Chart.registry.plugins.get('lfCapLine')).toBeUndefined();
  });

  it('sets the theme font on the ticks, axis title, legend and tooltip', async () => {
    const options = (await render()).config().options;
    const x = options?.scales?.['x'] as { ticks?: { font?: unknown }; title?: { font?: unknown } };
    const family = { family: 'Test Mono' };
    expect(x.ticks?.font).toEqual(family);
    expect(x.title?.font).toEqual(family);
    expect(options?.scales?.['y']?.ticks?.font).toEqual(family);
    expect(options?.plugins?.legend?.labels?.font).toEqual(family);
    expect(options?.plugins?.tooltip?.titleFont).toEqual(family);
    expect(options?.plugins?.tooltip?.bodyFont).toEqual(family);
  });

  it('colours the axes and grid from the theme', async () => {
    const chart = await render();
    const x = chart.config().options?.scales?.['x'];
    expect(x?.ticks?.color).toBe(THEME.text);
    expect(x?.grid?.color).toBe(THEME.grid);
    expect(chart.config().options?.plugins?.legend?.labels?.color).toBe(THEME.text);
  });

  it('creates a chart on the canvas, updates it when the inputs change, and destroys it', async () => {
    await render();
    const chart = Chart.getChart(canvas());
    expect(chart?.data.datasets.map((d) => d.label)).toEqual(['Ace']);

    fixture.componentRef.setInput('datasets', [{ ...ACE, label: 'Bolt' }]);
    await fixture.whenStable();
    expect(Chart.getChart(canvas())?.data.datasets.map((d) => d.label)).toEqual(['Bolt']);

    const el = canvas();
    fixture.destroy();
    expect(Chart.getChart(el)).toBeUndefined();
  });
});
