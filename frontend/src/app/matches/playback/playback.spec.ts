import { Component, signal } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
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
import { roundDetail } from '../testing/match-fixtures';
import { roundPlayback } from '../testing/playback-fixtures';
import { timeline, timelineEvent } from '../testing/timeline-fixtures';
import { Playback } from './playback';
import { PlaybackFeed } from './playback-feed/playback-feed';
import { PlaybackMap } from './playback-map/playback-map';
import { PlaybackScoreboard } from './playback-scoreboard/playback-scoreboard';
import { POLL_TIMERS } from '../../shared/testing/poll-timers';

const BASE = environment.apiBaseUrl;

describe('Playback (/rounds/:id/playback)', () => {
  let http: HttpTestingController;
  let harness: RouterTestingHarness;

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

  afterEach(() => {
    // The three loads run together: when one fails, the others are cancelled.
    http.verify({ ignoreCancelled: true });
  });

  const page = (): HTMLElement => harness.routeNativeElement as HTMLElement;
  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const link = (label: string): HTMLAnchorElement | undefined =>
    [...page().querySelectorAll('a')].find((a) => text(a).includes(label));

  /** Opens `/rounds/9/playback` and answers its three GETs (Round, timeline, playback). */
  async function open(
    t: RoundTimeline = timeline(),
    p: RoundPlayback = roundPlayback(),
  ): Promise<void> {
    await setUp();
    await harness.navigateByUrl('/rounds/9/playback');
    http
      .expectOne(`${BASE}/rounds/9/`)
      .flush({ data: roundDetail({ id: 9, team_red_name: 'Comets', team_blue_name: 'Vipers' }) });
    http.expectOne(`${BASE}/rounds/9/timeline/`).flush({ data: t, meta: {} });
    http.expectOne(`${BASE}/rounds/9/playback/`).flush({ data: p, meta: {} });
    await harness.fixture.whenStable();
  }

  describe('route and loading', () => {
    it('routes :id/playback to Playback (not Round detail) and loads all three', async () => {
      await open();
      const de = harness.fixture.debugElement;
      expect(de.query(By.directive(Playback))).not.toBeNull();
      expect(de.query(By.directive(RoundDetail))).toBeNull();
      expect(text(page().querySelector('h1'))).toBe('Comets vs Vipers');
      expect(text(page().querySelector('.pb-kicker'))).toBe('Playback');
    });

    it('titles the tab "Playback - <app title>"', async () => {
      await open();
      expect(TestBed.inject(Title).getTitle()).toBe(`Playback - ${APP_TITLE}`);
    });
  });

  describe('links and panels', () => {
    it('links "Back to Round" and "Full event log"', async () => {
      await open();
      expect(link('Back to Round')?.getAttribute('href')).toBe('/rounds/9');
      expect(link('Full event log')?.getAttribute('href')).toBe('/rounds/9/events');
    });

    it('renders a Red then a Blue scoreboard with their teams and players, the map and the feed', async () => {
      const t = timeline();
      const p = roundPlayback();
      await open(t, p);
      const de = harness.fixture.debugElement;
      const boards = de
        .queryAll(By.directive(PlaybackScoreboard))
        .map((b) => b.componentInstance as PlaybackScoreboard);
      expect(boards.map((b) => [b.side(), b.teamName(), b.players().map((pl) => pl.name)])).toEqual(
        [
          ['red', 'Comets', ['Ace', 'Cleo']],
          ['blue', 'Vipers', ['Bolt', 'Dax']],
        ],
      );
      for (const b of boards) {
        expect(b.cursor()).toBe(0);
        expect(b.notTargetableTicks()).toBe(8);
        expect(b.respawnTicks()).toBe(16);
        expect(b.snapshots().get(201)?.[0].lives).toBe(10);
        expect(b.snapshots().size).toBe(4);
      }

      const map = de.query(By.directive(PlaybackMap)).componentInstance as PlaybackMap;
      expect(map.map()).toBeNull();
      expect(map.cursor()).toBe(0);
      expect(map.snapshots().size).toBe(4);
      expect(map.respawnTicks()).toBe(16);

      const feed = de.query(By.directive(PlaybackFeed)).componentInstance as PlaybackFeed;
      expect(feed.events()).toEqual(t.events);
      expect(feed.cursor()).toBe(0);
    });

    it('lays out the controls, the two boards side by side, the map, then the feed', async () => {
      await open();
      const order = [
        ...page().querySelectorAll('.pb-transport, .pb-boards, app-playback-map, .pb-feed'),
      ].map((el) => el.className || el.tagName.toLowerCase());
      expect(order.map((c) => c.split(' ')[0])).toEqual([
        'pb-transport',
        'pb-boards',
        'app-playback-map',
        'pb-feed',
      ]);
      const boards = page().querySelector<HTMLElement>('.pb-boards');
      expect([...(boards?.children ?? [])].map((b) => b.getAttribute('data-lf-side'))).toEqual([
        'red',
        'blue',
      ]);
      expect(getComputedStyle(boards as HTMLElement).display).toBe('grid');
    });
  });

  describe('transport', () => {
    const button = (label: string): HTMLButtonElement => {
      const found =
        page().querySelector<HTMLButtonElement>(`.pb-transport button[aria-label="${label}"]`) ??
        [...page().querySelectorAll<HTMLButtonElement>('.pb-transport button')].find(
          (b) => `Speed ${text(b)}` === label,
        );
      if (!found) throw new Error(`no ${label} button`);
      return found;
    };
    async function press(label: string): Promise<void> {
      button(label).click();
      await harness.fixture.whenStable();
    }
    const clockText = (): string => text(page().querySelector('.pb-clock'));
    const screen = (): Playback =>
      harness.fixture.debugElement.query(By.directive(Playback)).componentInstance as Playback;
    const feedRows = (): string[] =>
      [...page().querySelectorAll('.pf-row .pf-row__desc')].map((d) => text(d));
    const boardCursors = (): number[] =>
      harness.fixture.debugElement
        .queryAll(By.directive(PlaybackScoreboard))
        .map((b) => (b.componentInstance as PlaybackScoreboard).cursor());

    /** Tags at ticks 40, 100 and 100, a Movement at 70 and one more tag at 1700. */
    const events = () =>
      timeline({
        events: [
          timelineEvent({ tick: 40, description: 'first' }),
          timelineEvent({ tick: 70, event_type: 'movement', description: 'moved' }),
          timelineEvent({ tick: 100, description: 'second' }),
          timelineEvent({ tick: 100, description: 'second too' }),
          timelineEvent({ tick: 1700, description: 'last' }),
        ],
      });

    it('reads "mm:ss / mm:ss" for the cursor and the Round length', async () => {
      await open();
      expect(clockText()).toBe('00:00 / 15:00');
    });

    it('jumps to the end (the final state) and back to the start', async () => {
      await open(events());
      expect(feedRows()).toEqual([]);

      await press('Jump to end');
      expect(clockText()).toBe('15:00 / 15:00');
      expect(boardCursors()).toEqual([1800, 1800]);
      expect(feedRows()[0]).toBe('last');

      await press('Jump to start');
      expect(clockText()).toBe('00:00 / 15:00');
      expect(boardCursors()).toEqual([0, 0]);
      expect(feedRows()).toEqual([]);
    });

    it('steps to the next / previous distinct event tick, skipping Movement', async () => {
      await open(events());
      const steps: number[] = [];
      for (let i = 0; i < 4; i++) {
        await press('Next event');
        steps.push(boardCursors()[0]);
      }
      // 70 is the Movement; 100 appears once though two events sit on it; 1700 is the last.
      expect(steps).toEqual([40, 100, 1700, 1700]);

      const back: number[] = [];
      for (let i = 0; i < 3; i++) {
        await press('Previous event');
        back.push(boardCursors()[0]);
      }
      expect(back).toEqual([100, 40, 40]);

      screen().seek(55);
      await press('Previous event');
      expect(boardCursors()[0]).toBe(40);
    });

    it('steps back past the event tick a mid-play fractional cursor is still on', async () => {
      await open(events());
      // Mid-play the cursor sits between ticks: 100.4 is still on event tick 100.
      screen().seek(100.4);
      await press('Previous event');
      expect(boardCursors()[0]).toBe(40);
    });

    it('offers 1x / 2x / 4x / 8x, the active one marked (1x at first)', async () => {
      await open();
      const speeds = (): [string, string | null, boolean][] =>
        [...page().querySelectorAll<HTMLButtonElement>('.pb-speeds button')].map((b) => [
          text(b),
          b.getAttribute('aria-pressed'),
          b.classList.contains('is-active'),
        ]);
      expect(speeds()).toEqual([
        ['1x', 'true', true],
        ['2x', 'false', false],
        ['4x', 'false', false],
        ['8x', 'false', false],
      ]);
      await press('Speed 4x');
      expect(speeds().map(([label, pressed]) => `${label}:${pressed}`)).toEqual([
        '1x:false',
        '2x:false',
        '4x:true',
        '8x:false',
      ]);
    });

    describe('play loop', () => {
      /**
       * Fakes the interval timers and rAF (`POLL_TIMERS`), after the page has loaded: zoneless
       * change detection and `whenStable` still settle on the real `setTimeout`.
       */
      const fakeIntervals = (): void => {
        vi.useFakeTimers({ toFake: POLL_TIMERS });
      };
      afterEach(() => {
        vi.useRealTimers();
      });

      async function advance(ms: number): Promise<void> {
        vi.advanceTimersByTime(ms);
        await harness.fixture.whenStable();
      }

      it('advances speed x 0.2 ticks every 100 ms while playing, and pauses', async () => {
        await open();
        fakeIntervals();
        await press('Play');
        expect(button('Pause')).toBeTruthy();
        await advance(1000);
        expect(boardCursors()[0]).toBeCloseTo(2);
        expect(clockText()).toBe('00:01 / 15:00');

        await press('Speed 8x');
        await advance(1000);
        expect(boardCursors()[0]).toBeCloseTo(18);

        await press('Pause');
        expect(button('Play')).toBeTruthy();
        await advance(1000);
        expect(boardCursors()[0]).toBeCloseTo(18);
        expect(vi.getTimerCount()).toBe(0);
      });

      it('stops at the end of the Round and restarts from the start on the next Play', async () => {
        await open();
        fakeIntervals();
        screen().seek(1799);
        await press('Speed 2x');
        await press('Play');
        await advance(5000);
        expect(boardCursors()).toEqual([1800, 1800]);
        expect(button('Play')).toBeTruthy();
        expect(vi.getTimerCount()).toBe(0);

        await press('Play');
        expect(boardCursors()).toEqual([0, 0]);
        await advance(500);
        expect(boardCursors()[0]).toBeCloseTo(2);
      });

      it('scrubs in whole ticks up to the Round length, pausing when dragged', async () => {
        await open();
        const scrubber = page().querySelector<HTMLInputElement>('.pb-scrubber input[type="range"]');
        if (!scrubber) throw new Error('no scrubber');
        expect(scrubber.step).toBe('1');
        expect(scrubber.max).toBe('1800');
        expect(scrubber.min).toBe('0');

        fakeIntervals();
        await press('Play');
        await advance(1000);
        expect(scrubber.value).toBe('2');

        scrubber.value = '600';
        scrubber.dispatchEvent(new Event('input'));
        await harness.fixture.whenStable();
        expect(button('Play')).toBeTruthy();
        expect(boardCursors()).toEqual([600, 600]);
        expect(clockText()).toBe('05:00 / 15:00');
        await advance(1000);
        expect(boardCursors()).toEqual([600, 600]);
      });

      it('tears the loop down when the screen is destroyed', async () => {
        await open();
        fakeIntervals();
        await press('Play');
        expect(vi.getTimerCount()).toBe(1);
        harness.fixture.destroy();
        expect(vi.getTimerCount()).toBe(0);
      });
    });

    it('keeps an empty marker slot for RV-04 directly above the scrubber', async () => {
      await open();
      const slot = page().querySelector('.pb-markers');
      expect(slot).not.toBeNull();
      expect(slot?.children.length).toBe(0);
      expect(slot?.nextElementSibling?.classList).toContain('pb-scrubber');
    });

    it('exposes seek(tick) for RV-04, clamped to the Round', async () => {
      await open(events());
      screen().seek(100);
      await harness.fixture.whenStable();
      expect(clockText()).toBe('00:50 / 15:00');
      expect(feedRows()).toEqual(['second too', 'second', 'first']);

      screen().seek(5000);
      await harness.fixture.whenStable();
      expect(boardCursors()).toEqual([1800, 1800]);
      screen().seek(-3);
      await harness.fixture.whenStable();
      expect(boardCursors()).toEqual([0, 0]);
    });
  });

  describe('load failures', () => {
    const notFound = {
      errors: [{ code: 'not_found', message: 'Not found.', field: null }],
      meta: {},
    };

    it('shows "Round not found" on a 404 (missing or another Account\'s)', async () => {
      await setUp();
      await harness.navigateByUrl('/rounds/99/playback');
      http
        .expectOne(`${BASE}/rounds/99/playback/`)
        .flush(notFound, { status: 404, statusText: 'Not Found' });
      await harness.fixture.whenStable();
      expect(text(page().querySelector('h1'))).toBe('Round not found');
      expect(link('Back to Matches')?.getAttribute('href')).toBe('/matches');
    });

    it('shows any other failure as an alert', async () => {
      await setUp();
      await harness.navigateByUrl('/rounds/9/playback');
      http
        .expectOne(`${BASE}/rounds/9/`)
        .flush(
          { errors: [{ code: 'server_error', message: 'Boom.', field: null }], meta: {} },
          { status: 500, statusText: 'Server Error' },
        );
      await harness.fixture.whenStable();
      expect(text(page().querySelector('[role="alert"]'))).toContain('Could not load the Round.');
    });
  });
});

/** Playback embedded by another screen (the One Week (Live) screen, spec #454). */
@Component({
  imports: [Playback],
  template: `<app-playback [roundId]="roundId()" />`,
})
class EmbeddingHost {
  readonly roundId = signal(31);
}

describe('Playback with a roundId input (spec #454)', () => {
  let http: HttpTestingController;
  let fixture: ComponentFixture<EmbeddingHost>;

  afterEach(() => http.verify({ ignoreCancelled: true }));

  const host = (): HTMLElement => fixture.nativeElement as HTMLElement;
  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';

  /** Answers Round `id`'s three GETs, its teams named after it. */
  async function answer(id: number): Promise<void> {
    http.expectOne(`${BASE}/rounds/${id}/`).flush({
      data: roundDetail({ id, team_red_name: `Red ${id}`, team_blue_name: `Blue ${id}` }),
    });
    http.expectOne(`${BASE}/rounds/${id}/timeline/`).flush({ data: timeline(), meta: {} });
    http.expectOne(`${BASE}/rounds/${id}/playback/`).flush({ data: roundPlayback(), meta: {} });
    await fixture.whenStable();
  }

  beforeEach(async () => {
    TestBed.configureTestingModule({
      providers: [provideRouter([]), provideHttpClient(), provideHttpClientTesting()],
    });
    http = TestBed.inject(HttpTestingController);
    fixture = TestBed.createComponent(EmbeddingHost);
    await fixture.whenStable();
  });

  it('loads the input Round, not the route :id', async () => {
    http.expectNone(`${BASE}/rounds/0/`);
    await answer(31);
    expect(text(host().querySelector('h2'))).toBe('Red 31 vs Blue 31');
  });

  it('loads the new Round when the input changes', async () => {
    await answer(31);
    fixture.componentInstance.roundId.set(32);
    await fixture.whenStable();
    await answer(32);
    expect(text(host().querySelector('h2'))).toBe('Red 32 vs Blue 32');
  });

  it('renders embedded: no kicker, no h1, no "Back to Round" link', async () => {
    await answer(31);
    expect(host().querySelector('.pb-kicker')).toBeNull();
    expect(host().querySelector('h1')).toBeNull();
    const links = [...host().querySelectorAll('a')].map((a) => text(a));
    expect(links.some((label) => label.includes('Back to Round'))).toBe(false);
    expect(links).toContain('Full event log');
  });
});
