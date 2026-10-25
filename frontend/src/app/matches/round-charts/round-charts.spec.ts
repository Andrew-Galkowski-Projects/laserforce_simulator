import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { By, Title } from '@angular/platform-browser';
import { provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { environment } from '../../../environments/environment';
import { RoundPlayback, RoundTimeline } from '../../api/rounds.models';
import { signIn } from '../../api/testing/auth-fixtures';
import { APP_TITLE } from '../../app-title';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';
import { RoundDetail } from '../round-detail/round-detail';
import { stubCanvasContext } from '../testing/fake-canvas';
import { roundDetail } from '../testing/match-fixtures';
import { playbackPlayer, playbackSnapshot, roundPlayback } from '../testing/playback-fixtures';
import { timeline, timelineEvent } from '../testing/timeline-fixtures';
import { LineChart } from './line-chart';
import { RoundCharts } from './round-charts';

const BASE = environment.apiBaseUrl;

describe('Round charts (/rounds/:id/charts)', () => {
  let http: HttpTestingController;
  let harness: RouterTestingHarness;
  let restoreCanvas: () => void;
  let schemeListeners: (() => void)[];
  let removedListeners: number;

  /**
   * jsdom has no `matchMedia`: this one matches nothing and records the colour-scheme query's
   * listeners (the shell's breakpoint queries use the legacy `addListener`, ignored here).
   */
  function stubMatchMedia(): void {
    schemeListeners = [];
    removedListeners = 0;
    const isScheme = (query: string): boolean => query.includes('prefers-color-scheme');
    Object.defineProperty(window, 'matchMedia', {
      configurable: true,
      writable: true,
      value: (query: string) => ({
        matches: false,
        media: query,
        onchange: null,
        addListener: () => undefined,
        removeListener: () => undefined,
        addEventListener: (_: string, fn: () => void) => {
          if (isScheme(query)) schemeListeners.push(fn);
        },
        removeEventListener: () => {
          if (isScheme(query)) removedListeners++;
        },
      }),
    });
  }

  async function setUp(): Promise<void> {
    TestBed.configureTestingModule({
      providers: [
        provideRouter(routes),
        provideHttpClient(withInterceptors([authInterceptor])),
        provideHttpClientTesting(),
      ],
    });
    http = TestBed.inject(HttpTestingController);
    await signIn();
    harness = await RouterTestingHarness.create();
  }

  beforeEach(() => {
    restoreCanvas = stubCanvasContext();
    stubMatchMedia();
  });

  afterEach(() => {
    http.verify({ ignoreCancelled: true });
    restoreCanvas();
    delete (window as Partial<Window>).matchMedia;
    document.documentElement.style.removeProperty('--lf-side-red');
    document.documentElement.style.removeProperty('--lf-side-blue');
  });

  const page = (): HTMLElement => harness.routeNativeElement as HTMLElement;
  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const link = (label: string): HTMLAnchorElement | undefined =>
    [...page().querySelectorAll('a')].find((a) => text(a).includes(label));
  const charts = (): LineChart[] =>
    harness.fixture.debugElement
      .queryAll(By.directive(LineChart))
      .map((d) => d.componentInstance as LineChart);

  /** Opens `/rounds/9/charts` and answers its three GETs (Round, timeline, playback). */
  async function open(
    t: RoundTimeline = timeline(),
    p: RoundPlayback = roundPlayback(),
  ): Promise<void> {
    await setUp();
    await harness.navigateByUrl('/rounds/9/charts');
    http
      .expectOne(`${BASE}/rounds/9/`)
      .flush({ data: roundDetail({ id: 9, team_red_name: 'Comets', team_blue_name: 'Vipers' }) });
    http.expectOne(`${BASE}/rounds/9/timeline/`).flush({ data: t, meta: {} });
    http.expectOne(`${BASE}/rounds/9/playback/`).flush({ data: p, meta: {} });
    await harness.fixture.whenStable();
  }

  describe('route and loading', () => {
    it('routes :id/charts to Round charts (not Round detail) and loads all three', async () => {
      await open();
      const de = harness.fixture.debugElement;
      expect(de.query(By.directive(RoundCharts))).not.toBeNull();
      expect(de.query(By.directive(RoundDetail))).toBeNull();
      expect(text(page().querySelector('h1'))).toBe('Comets vs Vipers');
      expect(text(page().querySelector('.rc-kicker'))).toBe('Charts');
    });

    it('titles the tab "Charts - <app title>"', async () => {
      await open();
      expect(TestBed.inject(Title).getTitle()).toBe(`Charts - ${APP_TITLE}`);
    });

    it('shows Round not found on a 404 (a missing or another Account’s Round)', async () => {
      await setUp();
      await harness.navigateByUrl('/rounds/9/charts');
      http
        .expectOne(`${BASE}/rounds/9/`)
        .flush(
          { errors: [{ code: 'not_found', message: 'Not found.', field: null }], meta: {} },
          { status: 404, statusText: 'Not Found' },
        );
      await harness.fixture.whenStable();
      expect(text(page())).toContain('Round not found');
      expect(charts()).toEqual([]);
    });
  });

  it('links back to the Round, and to its Event log and Playback', async () => {
    await open();
    expect(link('Back to Round')?.getAttribute('href')).toBe('/rounds/9');
    expect(link('Event log')?.getAttribute('href')).toBe('/rounds/9/events');
    expect(link('Playback')?.getAttribute('href')).toBe('/rounds/9/playback');
  });

  describe('the four charts', () => {
    it('renders Shots, Lives, Points and SP cards in that order, each naming its chart', async () => {
      await open();
      const titles = ['Shots over time', 'Lives over time', 'Points over time', 'SP over time'];
      expect([...page().querySelectorAll('.rc-card h2')].map((h) => text(h))).toEqual(titles);
      expect(
        [...page().querySelectorAll('canvas')].map((c) => c.getAttribute('aria-label')),
      ).toEqual(titles);
    });

    it('fixes only the SP chart at 0 to 99 with the cap line', async () => {
      await open();
      expect(charts().map((c) => [c.yMax(), c.capLine()])).toEqual([
        [undefined, false],
        [undefined, false],
        [undefined, false],
        [99, true],
      ]);
    });

    it('runs every chart to the end of the Round, in seconds', async () => {
      await open();
      expect(charts().map((c) => c.maxSec())).toEqual([900, 900, 900, 900]);
    });

    it('draws one line per player, in timeline order, from their Playback snapshots', async () => {
      const p = roundPlayback({
        players: [
          playbackPlayer(101, [playbackSnapshot(), playbackSnapshot({ tick: 120, score: 100 })]),
          playbackPlayer(201),
          playbackPlayer(102),
          playbackPlayer(202),
        ],
      });
      await open(timeline(), p);
      const [shots, , points] = charts();
      expect(shots.datasets().map((d) => d.label)).toEqual(['Ace', 'Bolt', 'Cleo', 'Dax']);
      expect(points.datasets()[0].points).toEqual([
        { sec: 0, value: 0 },
        { sec: 60, value: 100 },
      ]);
      expect(points.datasets().every((d) => !d.dashed)).toBe(true);
    });

    it('gives a player the same colour on all four charts, and each player a different one', async () => {
      document.documentElement.style.setProperty('--lf-side-red', 'rgb(10, 0, 0)');
      document.documentElement.style.setProperty('--lf-side-blue', 'rgb(0, 0, 10)');
      await open();
      const colourSets = charts().map((c) => c.datasets().map((d) => d.color));
      for (const colours of colourSets) expect(colours).toEqual(colourSets[0]);
      expect(new Set(colourSets[0]).size).toBe(4);
    });
  });

  describe('filter bar', () => {
    const boxes = (): HTMLElement[] => [...page().querySelectorAll<HTMLElement>('mat-checkbox')];
    const input = (label: string): HTMLInputElement => {
      const box = boxes().find((c) => text(c) === label);
      const el = box?.querySelector<HTMLInputElement>('input[type="checkbox"]');
      if (!el) throw new Error(`no ${label} checkbox`);
      return el;
    };
    async function toggle(label: string): Promise<void> {
      input(label).click();
      await harness.fixture.whenStable();
    }
    const labelsOn = (chart: LineChart): (string | undefined)[] =>
      chart.datasets().map((d) => d.label);

    /** Ace scores 100 at 1:00; the others stay at 0. */
    const scoring = (): RoundPlayback =>
      roundPlayback({
        players: [
          playbackPlayer(101, [playbackSnapshot(), playbackSnapshot({ tick: 120, score: 100 })]),
          playbackPlayer(201),
          playbackPlayer(102, [playbackSnapshot(), playbackSnapshot({ tick: 120, sp: 4 })]),
          playbackPlayer(202),
        ],
      });

    it('lists the Sides, roles, players, overlay kinds and Side totals, all ticked but totals', async () => {
      await open();
      const fieldsets = [...page().querySelectorAll('.rc-filters fieldset')].map((f) => [
        text(f.querySelector('legend')),
        [...f.querySelectorAll('mat-checkbox')].map((c) => text(c)),
      ]);
      expect(fieldsets).toEqual([
        ['Sides', ['R Red', 'B Blue']],
        ['Roles', ['CMD', 'HVY', 'SCT', 'AMO', 'MED']],
        ['Red players', ['Ace', 'Cleo']],
        ['Blue players', ['Bolt', 'Dax']],
        ['Overlays', ['Eliminations', 'Specials', 'Nukes']],
        ['Lines', ['Side totals']],
      ]);
      const unticked = boxes()
        .filter((c) => !c.querySelector<HTMLInputElement>('input')?.checked)
        .map((c) => text(c));
      expect(unticked).toEqual(['Side totals']);
    });

    it('drops a Side’s players from all four charts when the Side is unticked', async () => {
      await open();
      await toggle('B Blue');
      for (const chart of charts()) expect(labelsOn(chart)).toEqual(['Ace', 'Cleo']);
    });

    it('drops a role’s players when the role is unticked', async () => {
      await open();
      await toggle('HVY');
      expect(labelsOn(charts()[0])).toEqual(['Ace', 'Cleo', 'Dax']);
    });

    it('drops one player when their box is unticked, and brings them back', async () => {
      await open();
      await toggle('Ace');
      for (const chart of charts()) expect(labelsOn(chart)).toEqual(['Bolt', 'Cleo', 'Dax']);
      await toggle('Ace');
      expect(labelsOn(charts()[0])).toEqual(['Ace', 'Bolt', 'Cleo', 'Dax']);
    });

    it('adds a dashed Red and Blue total (an average on SP) when Side totals is ticked', async () => {
      await open(timeline(), scoring());
      await toggle('Side totals');
      const [, , points, sp] = charts();
      expect(labelsOn(points)).toEqual(['Ace', 'Bolt', 'Cleo', 'Dax', 'Red total', 'Blue total']);
      const redTotal = points.datasets()[4];
      expect(redTotal.dashed).toBe(true);
      expect(redTotal.points).toEqual([
        { sec: 0, value: 0 },
        { sec: 60, value: 100 },
      ]);
      expect(labelsOn(sp).slice(4)).toEqual(['Red avg', 'Blue avg']);
      expect(sp.datasets()[4].points).toEqual([
        { sec: 0, value: 0 },
        { sec: 60, value: 2 },
      ]);
    });

    it('totals only the visible players', async () => {
      await open(timeline(), scoring());
      await toggle('Side totals');
      await toggle('Cleo');
      const sp = charts()[3];
      expect(sp.datasets().find((d) => d.label === 'Red avg')?.points).toEqual([
        { sec: 0, value: 0 },
      ]);
    });

    it('leaves out the total of a Side with no visible players', async () => {
      await open();
      await toggle('Side totals');
      await toggle('B Blue');
      expect(labelsOn(charts()[0])).toEqual(['Ace', 'Cleo', 'Red total']);
    });
  });

  describe('Chart overlays', () => {
    const overlayTimeline = (): RoundTimeline =>
      timeline({
        events: [
          timelineEvent({ tick: 240, event_type: 'elimination', target_name: 'Bolt' }),
          timelineEvent({ tick: 90, event_type: 'special', actor_name: 'Cleo' }),
        ],
      });
    const toggleOverlay = async (label: string): Promise<void> => {
      const box = [...page().querySelectorAll('mat-checkbox')].find((c) => text(c) === label);
      box?.querySelector<HTMLInputElement>('input')?.click();
      await harness.fixture.whenStable();
    };

    it('marks every chart at each overlay, coloured by its kind', async () => {
      document.documentElement.style.setProperty('--lf-ev-elimination', 'rgb(9, 9, 9)');
      await open(overlayTimeline());
      for (const chart of charts()) {
        expect(chart.overlays().map((o) => [o.sec, o.label])).toEqual([
          [45, 'Cleo'],
          [120, 'Bolt'],
        ]);
      }
      expect(charts()[0].overlays()[1].color).toBe('rgb(9, 9, 9)');
      document.documentElement.style.removeProperty('--lf-ev-elimination');
    });

    it('removes a kind’s overlays from every chart when it is unticked', async () => {
      await open(overlayTimeline());
      await toggleOverlay('Specials');
      for (const chart of charts()) {
        expect(chart.overlays().map((o) => o.label)).toEqual(['Bolt']);
      }
    });
  });

  describe('theme colours', () => {
    it('shades each player from their Side token', async () => {
      document.documentElement.style.setProperty('--lf-side-red', 'rgb(10, 0, 0)');
      await open();
      const [ace, bolt] = charts()[0].datasets();
      expect(ace.color).toContain('rgb(10, 0, 0)');
      expect(bolt.color).not.toContain('rgb(10, 0, 0)');
    });

    it('re-reads the tokens when the colour scheme flips, and stops listening on leave', async () => {
      document.documentElement.style.setProperty('--lf-side-red', 'rgb(10, 0, 0)');
      await open();
      document.documentElement.style.setProperty('--lf-side-red', 'rgb(20, 0, 0)');
      for (const fn of schemeListeners) fn();
      await harness.fixture.whenStable();
      expect(charts()[0].datasets()[0].color).toContain('rgb(20, 0, 0)');

      await harness.navigateByUrl('/rounds/9');
      http.expectOne(`${BASE}/rounds/9/`).flush({ data: roundDetail({ id: 9 }) });
      await harness.fixture.whenStable();
      expect(removedListeners).toBe(schemeListeners.length);
    });
  });
});
