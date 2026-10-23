import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { Title } from '@angular/platform-browser';
import { provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { environment } from '../../../environments/environment';
import { RoundTimeline, TimelineEventType } from '../../api/rounds.models';
import { signIn } from '../../api/testing/auth-fixtures';
import { APP_TITLE } from '../../app-title';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';
import { roundDetail } from '../testing/match-fixtures';
import { eventSummary, highlight, timeline, timelineEvent } from '../testing/timeline-fixtures';

const BASE = environment.apiBaseUrl;

/** The timeline viewport's height in the specs (jsdom lays nothing out, so every size is 0). */
const VIEWPORT_PX = 720;

/**
 * Lays out CDK virtual-scroll viewports the way a browser would, since jsdom lays out nothing:
 * - every viewport gets a `VIEWPORT_PX` client height, so it renders the rows a real 720px
 *   viewport would (20 at 36px, plus its buffer) instead of jsdom's 0px worth;
 * - `scrollTo` (missing in jsdom) moves `scrollTop` and fires `scroll`, as a browser does.
 * Returns the restore function.
 */
function layOutViewports(): () => void {
  const proto = HTMLElement.prototype as unknown as Record<string, unknown>;
  const before = Object.getOwnPropertyDescriptor(Element.prototype, 'clientHeight');
  Object.defineProperty(proto, 'clientHeight', {
    configurable: true,
    get(this: HTMLElement): number {
      return this.classList.contains('cdk-virtual-scroll-viewport')
        ? VIEWPORT_PX
        : (before?.get?.call(this) ?? 0);
    },
  });
  proto['scrollTo'] = function (this: HTMLElement, options: ScrollToOptions): void {
    if (options.top !== undefined) this.scrollTop = options.top;
    this.dispatchEvent(new Event('scroll'));
  };
  return () => {
    delete proto['clientHeight'];
    delete proto['scrollTo'];
  };
}

/** jsdom has no `matchMedia`; this one reports a viewport `width` px wide (as `shell.spec.ts`). */
function viewport(width: number): void {
  const matches = (query: string): boolean => {
    const min = /min-width:\s*(\d+)px/.exec(query);
    const max = /max-width:\s*([\d.]+)px/.exec(query);
    return (!min || width >= Number(min[1])) && (!max || width <= Number(max[1]));
  };
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: (query: string) => ({
      matches: matches(query),
      media: query,
      onchange: null,
      addListener: () => undefined,
      removeListener: () => undefined,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      dispatchEvent: () => false,
    }),
  });
}

describe('Event log (/rounds/:id/events)', () => {
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

  let restoreViewports: () => void;
  beforeEach(() => {
    restoreViewports = layOutViewports();
    viewport(1280);
  });

  afterEach(() => {
    restoreViewports();
    delete (window as Partial<Window>).matchMedia;
    // The Round and its timeline load together: when one fails, the other is cancelled.
    http.verify({ ignoreCancelled: true });
  });

  const page = (): HTMLElement => harness.routeNativeElement as HTMLElement;
  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const link = (label: string): HTMLAnchorElement | undefined =>
    [...page().querySelectorAll('a')].find((a) => text(a).includes(label));
  /** A filter checkbox's input, by its label (an Event group or a player). */
  const checkbox = (label: string): HTMLInputElement => {
    const box = [...page().querySelectorAll('mat-checkbox')].find((c) => text(c) === label);
    const input = box?.querySelector<HTMLInputElement>('input[type="checkbox"]');
    if (!input) throw new Error(`no ${label} checkbox`);
    return input;
  };
  async function toggle(label: string): Promise<void> {
    checkbox(label).click();
    await harness.fixture.whenStable();
  }
  /** The timeline rows the virtual scroll has rendered. */
  const rows = (): HTMLElement[] => [...page().querySelectorAll<HTMLElement>('.re-row')];

  /** Opens `/rounds/9/events` and answers its two GETs (the Round and its timeline). */
  async function open(t: RoundTimeline = timeline()): Promise<void> {
    await setUp();
    await harness.navigateByUrl('/rounds/9/events');
    http
      .expectOne(`${BASE}/rounds/9/`)
      .flush({ data: roundDetail({ id: 9, team_red_name: 'Comets', team_blue_name: 'Vipers' }) });
    http.expectOne(`${BASE}/rounds/9/timeline/`).flush({ data: t, meta: {} });
    await harness.fixture.whenStable();
  }

  describe('page head', () => {
    it('reads "Event Log", names both teams and links back to the Round', async () => {
      await open();
      expect(text(page().querySelector('h1'))).toBe('Event Log');
      expect(text(page().querySelector('.re-meta'))).toBe('Comets vs Vipers');
      expect(link('Back to Round')?.getAttribute('href')).toBe('/rounds/9');
      expect(text(link('Back to Round'))).toBe('← Back to Round');
    });

    it('titles the tab "Event Log - <app title>"', async () => {
      await open();
      expect(TestBed.inject(Title).getTitle()).toBe(`Event Log - ${APP_TITLE}`);
    });
  });

  describe('summary cards', () => {
    it('shows the seven counts in order, without missiles', async () => {
      await open(
        timeline({
          summary: eventSummary({
            tags: 41,
            moves: 1200,
            misses: 17,
            eliminations: 3,
            specials: 5,
            resupplies: 9,
            missiles: 4,
            total_events: 1279,
          }),
        }),
      );
      const cards = [...page().querySelectorAll('.re-summary .re-card')].map(
        (c) =>
          `${text(c.querySelector('.re-card__value'))} ${text(c.querySelector('.re-card__label'))}`,
      );
      expect(cards).toEqual([
        '41 Tags',
        '1,200 Moves',
        '17 Misses',
        '3 Eliminations',
        '5 Specials',
        '9 Resupplies',
        '1,279 Total',
      ]);
    });
  });

  describe('Highlights', () => {
    const highlightRows = (): HTMLElement[] => [
      ...page().querySelectorAll<HTMLElement>('.re-highlights .re-highlight'),
    ];

    it('lists each with its kind label, mm:ss, label text and Side', async () => {
      await open(
        timeline({
          highlights: [
            highlight({ kind: 'nuke_detonation', tick: 61, team: 'red', label: 'Ace nuked' }),
            highlight({ kind: 'nuke_cancelled', tick: 300, label: 'Nuke stopped' }),
            highlight({ kind: 'medic_reset', tick: 450, label: 'Cleo reset x2' }),
            highlight({ kind: 'first_elimination', tick: 240, label: 'Bolt eliminated Ace' }),
            highlight({ kind: 'team_elimination', tick: 1500, team: 'red', label: 'Blue wiped' }),
            highlight({ kind: 'scoring_burst', tick: 1201, team: 'red', label: '+900 in 30s' }),
          ],
        }),
      );
      const rows = highlightRows().map((h) => [
        text(h.querySelector('.re-highlight__kind')),
        text(h.querySelector('.re-highlight__time')),
        text(h.querySelector('.re-highlight__label')),
        h.getAttribute('data-lf-side'),
      ]);
      expect(rows).toEqual([
        ['Nuke', '00:30', 'Ace nuked', 'red'],
        ['Nuke Cancelled', '02:30', 'Nuke stopped', 'blue'],
        ['Medic Reset', '03:45', 'Cleo reset x2', 'blue'],
        ['First Elim', '02:00', 'Bolt eliminated Ace', 'blue'],
        ['Team Wipe', '12:30', 'Blue wiped', 'red'],
        ['Scoring Burst', '10:00', '+900 in 30s', 'red'],
      ]);
    });

    it('scrolls the timeline to the first visible event at or after its tick', async () => {
      // A tag at every tick 0-120 but 70 (e0 ... e120), and hidden Movement rows at ticks
      // 10-19 and 70: the first *visible* event at tick 70 or later is e71, visible row 70.
      const moved = (tick: number) =>
        timelineEvent({ tick, event_type: 'movement', description: `moved ${tick}`, points: 0 });
      const events = [...Array(121).keys()].flatMap((t) =>
        t === 70
          ? [moved(t)]
          : t >= 10 && t < 20
            ? [timelineEvent({ tick: t, description: `e${t}` }), moved(t)]
            : [timelineEvent({ tick: t, description: `e${t}` })],
      );
      await open(timeline({ events, highlights: [highlight({ tick: 70, label: 'Wipe' })] }));
      const viewport = page().querySelector<HTMLElement>('cdk-virtual-scroll-viewport');
      const shown = () => rows().map((r) => text(r.querySelector('.re-row__desc')));
      expect(shown()).not.toContain('e71');

      highlightRows()[0].click();
      // The viewport re-renders its range on the frame after `scroll`.
      await new Promise((resolve) => requestAnimationFrame(resolve));
      await harness.fixture.whenStable();

      // e71 is the 71st visible row (index 70): 70 rows of 36px above it.
      expect(viewport?.scrollTop).toBe(70 * 36);
      expect(shown()).toContain('e71');
      const current = page().querySelectorAll('.re-row.is-current');
      expect([...current].map((r) => text(r.querySelector('.re-row__desc')))).toEqual(['e71']);
    });

    it('says "No highlights for this round" when there are none', async () => {
      await open(timeline({ highlights: [] }));
      expect(highlightRows()).toEqual([]);
      expect(text(page().querySelector('.re-highlights'))).toContain(
        'No highlights for this round',
      );
    });
  });

  describe('timeline rows', () => {
    it('shows mm:ss, the group chip, description, Side-wrapped names and a muted +N', async () => {
      await open(
        timeline({
          events: [
            timelineEvent(),
            timelineEvent({
              tick: 245,
              event_type: 'miss',
              points: 0,
              description: 'Cleo missed',
              actor_id: 102,
              actor_name: 'Cleo',
              target_id: null,
              target_name: null,
              target_side: null,
            }),
          ],
        }),
      );
      const [tag, miss] = rows();
      expect(text(tag.querySelector('.re-row__time'))).toBe('01:00');
      expect(text(tag.querySelector('.re-row__chip'))).toBe('Tag');
      expect(text(tag.querySelector('.re-row__desc'))).toBe('Ace tagged Bolt');
      const actor = tag.querySelector('.re-row__actor');
      expect(text(actor)).toBe('Ace');
      expect(actor?.getAttribute('data-lf-side')).toBe('red');
      const target = tag.querySelector('.re-row__target');
      expect(text(target)).toBe('Bolt');
      expect(target?.getAttribute('data-lf-side')).toBe('blue');
      const points = tag.querySelector('.re-row__points');
      expect(text(points)).toBe('+100');
      expect(points?.classList).toContain('re-muted');

      expect(text(miss.querySelector('.re-row__time'))).toBe('02:02');
      expect(text(miss.querySelector('.re-row__chip'))).toBe('Miss');
      expect(miss.querySelector('.re-row__target')).toBeNull();
      expect(miss.querySelector('.re-row__points')).toBeNull();
    });

    it("marks each row with its Event group's token", async () => {
      await open(
        timeline({
          events: [
            timelineEvent({ event_type: 'tag' }),
            timelineEvent({ event_type: 'team_elimination' }),
            timelineEvent({ event_type: 'locking' }),
            timelineEvent({ event_type: 'base_capture' }),
          ],
        }),
      );
      expect(rows().map((r) => r.style.getPropertyValue('--re-ev'))).toEqual([
        'var(--lf-ev-tag)',
        'var(--lf-ev-elimination)',
        'var(--lf-ev-locking)',
        'var(--lf-ev-base)',
      ]);
      expect(rows().map((r) => text(r.querySelector('.re-row__chip')))).toEqual([
        'Tag',
        'Elimination',
        'Locking',
        'Base capture',
      ]);
    });
  });

  describe('filters', () => {
    const move = (tick: number) =>
      timelineEvent({
        tick,
        event_type: 'movement',
        points: 0,
        description: `Ace moved ${tick}`,
        target_id: null,
        target_name: null,
        target_side: null,
      });
    const descriptions = (): string[] => rows().map((r) => text(r.querySelector('.re-row__desc')));

    it('hides Movement by default and shows it once its group is checked', async () => {
      await open(timeline({ events: [move(2), timelineEvent({ tick: 4 }), move(6)] }));
      expect(descriptions()).toEqual(['Ace tagged Bolt']);
      expect(checkbox('Movement').checked).toBe(false);
      expect(checkbox('Tag').checked).toBe(true);

      await toggle('Movement');
      expect(descriptions()).toEqual(['Ace moved 2', 'Ace tagged Bolt', 'Ace moved 6']);
    });

    /** One event of every one of the 15 timeline types, described by its type. */
    const ALL_TYPES: TimelineEventType[] = [
      'tag',
      'elimination',
      'team_elimination',
      'miss',
      'special',
      'nuke_cancelled',
      'missiled',
      'missile_dodge',
      'locking',
      'resupply_ammo',
      'resupply_lives',
      'combo_resupply',
      'medic_reset',
      'base_capture',
      'movement',
    ];
    const everyType = () =>
      timeline({
        events: ALL_TYPES.map((type, i) =>
          timelineEvent({ tick: i, event_type: type, description: type }),
        ),
      });

    it('puts each of the 15 types in its one Event group, nine groups in order', async () => {
      await open(everyType());
      await toggle('Movement');
      expect(
        [...page().querySelectorAll('.re-filters mat-checkbox')].map((c) => text(c)).slice(0, 9),
      ).toEqual([
        'Tag',
        'Elimination',
        'Miss',
        'Special',
        'Missile',
        'Locking',
        'Resupply',
        'Base capture',
        'Movement',
      ]);
      const chips = rows().map((r) => text(r.querySelector('.re-row__chip')));
      expect(descriptions().map((d, i) => `${chips[i]}:${d}`)).toEqual([
        'Tag:tag',
        'Elimination:elimination',
        'Elimination:team_elimination',
        'Miss:miss',
        'Special:special',
        'Special:nuke_cancelled',
        'Missile:missiled',
        'Missile:missile_dodge',
        'Locking:locking',
        'Resupply:resupply_ammo',
        'Resupply:resupply_lives',
        'Resupply:combo_resupply',
        'Resupply:medic_reset',
        'Base capture:base_capture',
        'Movement:movement',
      ]);
    });

    it('lists the players by Side, all checked', async () => {
      await open();
      const sides = [...page().querySelectorAll<HTMLElement>('.re-filters__players')].map((s) => [
        s.getAttribute('data-lf-side'),
        text(s.querySelector('legend')),
        ...[...s.querySelectorAll('mat-checkbox')].map((c) => text(c)),
      ]);
      expect(sides).toEqual([
        ['red', 'Red', 'Ace', 'Cleo'],
        ['blue', 'Blue', 'Bolt', 'Dax'],
      ]);
      expect(['Ace', 'Bolt', 'Cleo', 'Dax'].map((n) => checkbox(n).checked)).toEqual([
        true,
        true,
        true,
        true,
      ]);
    });

    it('shows an event while its actor OR its target is checked', async () => {
      await open(
        timeline({
          events: [
            timelineEvent({ description: 'Ace tagged Bolt' }),
            timelineEvent({
              description: 'Ace missed',
              event_type: 'miss',
              target_id: null,
              target_name: null,
              target_side: null,
            }),
            timelineEvent({
              description: 'Dax tagged Ace',
              actor_id: 202,
              actor_name: 'Dax',
              actor_side: 'blue',
              target_id: 101,
              target_name: 'Ace',
              target_side: 'red',
            }),
            timelineEvent({
              description: 'Cleo tagged Dax',
              actor_id: 102,
              actor_name: 'Cleo',
              target_id: 202,
              target_name: 'Dax',
            }),
          ],
        }),
      );
      await toggle('Ace');
      expect(descriptions()).toEqual(['Ace tagged Bolt', 'Dax tagged Ace', 'Cleo tagged Dax']);
      await toggle('Bolt');
      expect(descriptions()).toEqual(['Dax tagged Ace', 'Cleo tagged Dax']);
      await toggle('Dax');
      expect(descriptions()).toEqual(['Cleo tagged Dax']);
      await toggle('Ace');
      expect(descriptions()).toEqual([
        'Ace tagged Bolt',
        'Ace missed',
        'Dax tagged Ace',
        'Cleo tagged Dax',
      ]);
    });

    it('reads "Showing N of M events", M counting Movement', async () => {
      await open(timeline({ events: [move(2), timelineEvent({ tick: 4 }), move(6)] }));
      expect(text(page().querySelector('.re-count'))).toBe('Showing 1 of 3 events');
      await toggle('Movement');
      expect(text(page().querySelector('.re-count'))).toBe('Showing 3 of 3 events');
    });

    it('says "No events match these filters" instead of the list when nothing is visible', async () => {
      await open(timeline({ events: [timelineEvent()] }));
      expect(page().querySelector('cdk-virtual-scroll-viewport')).not.toBeNull();
      expect(text(page().querySelector('.re-timeline'))).not.toContain('No events match');
      await toggle('Tag');
      expect(text(page().querySelector('.re-count'))).toBe('Showing 0 of 1 events');
      expect(text(page().querySelector('.re-timeline'))).toContain('No events match these filters');
      expect(page().querySelector('cdk-virtual-scroll-viewport')).toBeNull();
      expect(rows()).toEqual([]);
    });

    it('shows each row\'s metadata as "key: value" text once "Show metadata" is on', async () => {
      await open(
        timeline({
          events: [
            timelineEvent({ metadata: { shots: 12, zone: 'A3', targets: [{ id: 201 }] } }),
            timelineEvent({ description: 'Ace tagged Dax', metadata: {} }),
          ],
        }),
      );
      const meta = () => rows().map((r) => text(r.querySelector('.re-row__meta')));
      const toggleInput = page().querySelector<HTMLInputElement>(
        'mat-slide-toggle button[role="switch"]',
      );
      expect(text(page().querySelector('mat-slide-toggle'))).toBe('Show metadata');
      expect(toggleInput?.getAttribute('aria-checked')).toBe('false');
      expect(meta()).toEqual(['', '']);

      toggleInput?.click();
      await harness.fixture.whenStable();
      expect(meta()).toEqual(['shots: 12 · zone: A3 · targets: [{"id":201}]', '']);
    });

    it.each<[string, TimelineEventType[]]>([
      ['Elimination', ['elimination', 'team_elimination']],
      ['Special', ['special', 'nuke_cancelled']],
      ['Missile', ['missiled', 'missile_dodge']],
      ['Resupply', ['resupply_ammo', 'resupply_lives', 'combo_resupply', 'medic_reset']],
    ])('unchecking %s hides exactly %j', async (label, types) => {
      await open(everyType());
      await toggle(label);
      const shown = ALL_TYPES.filter((t) => t !== 'movement' && !types.includes(t));
      expect(descriptions()).toEqual(shown);
    });
  });

  describe('layout', () => {
    it('puts the filters beside the timeline at 1280px', async () => {
      viewport(1280);
      await open();
      expect(page().querySelector('mat-expansion-panel')).toBeNull();
      const aside = page().querySelector('.re-body > aside.re-filters');
      expect(aside?.querySelector('mat-slide-toggle')).not.toBeNull();
      expect(aside?.nextElementSibling?.classList).toContain('re-timeline');
    });

    it('folds the filters into a collapsed panel above the timeline at 768px', async () => {
      viewport(768);
      await open();
      expect(page().querySelector('aside.re-filters')).toBeNull();
      const panel = page().querySelector('.re-body > mat-expansion-panel');
      expect(text(panel?.querySelector('mat-panel-title'))).toBe('Filters');
      expect(panel?.querySelector('[aria-expanded]')?.getAttribute('aria-expanded')).toBe('false');
      expect(panel?.nextElementSibling?.classList).toContain('re-timeline');
    });
  });

  describe('load failures', () => {
    const notFound = {
      errors: [{ code: 'not_found', message: 'Not found.', field: null }],
      meta: {},
    };

    it('shows "Round not found" on a 404 (missing or another Account\'s)', async () => {
      await setUp();
      await harness.navigateByUrl('/rounds/99/events');
      http
        .expectOne(`${BASE}/rounds/99/`)
        .flush(notFound, { status: 404, statusText: 'Not Found' });
      await harness.fixture.whenStable();
      expect(text(page().querySelector('h1'))).toBe('Round not found');
      expect(link('Back to Matches')?.getAttribute('href')).toBe('/matches');
      expect(page().querySelector('.re-summary')).toBeNull();
    });

    it('shows a 404 on the timeline alone as not found, too', async () => {
      await setUp();
      await harness.navigateByUrl('/rounds/99/events');
      http.expectOne(`${BASE}/rounds/99/timeline/`).flush(notFound, {
        status: 404,
        statusText: 'Not Found',
      });
      await harness.fixture.whenStable();
      expect(text(page().querySelector('h1'))).toBe('Round not found');
    });

    it('shows any other failure as an alert', async () => {
      await setUp();
      await harness.navigateByUrl('/rounds/9/events');
      http
        .expectOne(`${BASE}/rounds/9/timeline/`)
        .flush(
          { errors: [{ code: 'server_error', message: 'Boom.', field: null }], meta: {} },
          { status: 500, statusText: 'Server Error' },
        );
      await harness.fixture.whenStable();
      expect(text(page().querySelector('[role="alert"]'))).toContain('Could not load the Round.');
    });
  });
});
