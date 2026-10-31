import { Component } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { MATERIAL_ANIMATIONS } from '@angular/material/core';
import { Router, provideRouter } from '@angular/router';
import { environment } from '../../../environments/environment';
import { PlayControls } from '../../api/leagues.models';
import { SeasonPlayJobStatus } from '../../api/seasons.models';
import { POLL_TIMERS } from '../../shared/testing/poll-timers';
import { playControls, seasonDashboard } from '../testing/league-fixtures';
import { PlayMenu } from './play-menu';
import { PlayRun } from './play-run';

const BASE = environment.apiBaseUrl;
const CONTROLS_URL = `${BASE}/leagues/1/play-controls/`;
const JOB_URL = `${BASE}/seasons/11/play-jobs/job-1/`;

@Component({ template: '' })
class Blank {}

/** jsdom has no `matchMedia`; this one reports a viewport `width` px wide. */
function viewport(width: number): void {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: (query: string) => {
      const min = /min-width:\s*(\d+)px/.exec(query);
      return {
        matches: !min || width >= Number(min[1]),
        media: query,
        onchange: null,
        addListener: () => undefined,
        removeListener: () => undefined,
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
        dispatchEvent: () => false,
      };
    },
  });
}

const status = (overrides: Partial<SeasonPlayJobStatus> = {}): SeasonPlayJobStatus => ({
  status: 'running',
  completed: 0,
  total: 8,
  cancelled: false,
  error: null,
  ...overrides,
});

describe('Play menu (spec #454)', () => {
  let http: HttpTestingController;
  let fixture: ComponentFixture<PlayMenu>;

  async function open(url: string, width = 1280): Promise<void> {
    viewport(width);
    TestBed.configureTestingModule({
      providers: [
        provideRouter([{ path: '**', component: Blank }]),
        provideHttpClient(),
        provideHttpClientTesting(),
        // jsdom fires no animationend, so the member-night dialog would never finish closing.
        { provide: MATERIAL_ANIMATIONS, useValue: { animationsDisabled: true } },
      ],
    });
    http = TestBed.inject(HttpTestingController);
    fixture = TestBed.createComponent(PlayMenu);
    await TestBed.inject(Router).navigateByUrl(url);
    await fixture.whenStable();
  }

  /** Opens `/leagues/1` and answers its controls with `body`. */
  async function openWith(body: PlayControls, width = 1280): Promise<void> {
    await open('/leagues/1', width);
    http.expectOne(CONTROLS_URL).flush({ data: body, meta: {} });
    await fixture.whenStable();
  }

  afterEach(() => {
    vi.useRealTimers();
    document.querySelector('.cdk-overlay-container')?.remove();
    http.verify();
    delete (window as Partial<Window>).matchMedia;
  });

  const host = (): HTMLElement => fixture.nativeElement as HTMLElement;
  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const trigger = (): HTMLButtonElement | null =>
    host().querySelector<HTMLButtonElement>('button[aria-label="Play"]');
  const items = (): HTMLElement[] => [
    ...document.querySelectorAll<HTMLElement>('.mat-mdc-menu-panel [mat-menu-item]'),
  ];
  const labels = (): string[] => items().map((item) => text(item));
  const item = (label: string): HTMLElement | undefined =>
    items().find((candidate) => text(candidate) === label);
  const disabled = (el: HTMLElement | undefined): boolean =>
    el?.getAttribute('aria-disabled') === 'true' || (el as HTMLButtonElement)?.disabled === true;
  const alert = (): HTMLElement | null => host().querySelector('[role="alert"]');

  async function openMenu(): Promise<void> {
    trigger()?.click();
    await fixture.whenStable();
  }

  describe('where it shows', () => {
    it.each(['/leagues/1', '/leagues/1/history', '/leagues/1/seasons/11/standings'])(
      'shows on %s and loads that League’s controls',
      async (url) => {
        await open(url);
        http.expectOne(CONTROLS_URL).flush({ data: playControls(), meta: {} });
        await fixture.whenStable();
        expect(trigger()).not.toBeNull();
        expect(text(trigger())).toContain('Play');
      },
    );

    it('stops watching when it is destroyed (sign-out), so the next Account loads afresh', async () => {
      await openWith(playControls());
      fixture.destroy();
      expect(TestBed.inject(PlayRun).controls()).toBeNull();
    });

    it.each(['/teams', '/leagues', '/leagues/new', '/matches/3'])(
      'is hidden on %s, sending nothing',
      async (url) => {
        await open(url);
        expect(trigger()).toBeNull();
        http.expectNone((req) => req.url.includes('play-controls'));
      },
    );
  });

  describe('branches (verbatim from topnav_play.html)', () => {
    it('Start Season for a draft Season', async () => {
      await openWith(playControls({ action_button_state: 'start_season' }));
      await openMenu();
      expect(labels()).toEqual(['Start Season']);
    });

    it('the round-robin trio, ending "Play Until End of Season"', async () => {
      await openWith(playControls());
      await openMenu();
      expect(labels()).toEqual(['Play One Week', 'Play Two Months', 'Play Until End of Season']);
    });

    it('"Play Until Playoffs" before a final tournament phase', async () => {
      await openWith(
        playControls({ has_following_tournament_phase: true, following_tournament_is_final: true }),
      );
      await openMenu();
      expect(labels()[2]).toBe('Play Until Playoffs');
    });

    it('"Play Until Tournament" before a mid-season tournament phase', async () => {
      await openWith(
        playControls({
          has_following_tournament_phase: true,
          following_tournament_is_final: false,
        }),
      );
      await openMenu();
      expect(labels()[2]).toBe('Play Until Tournament');
    });

    it('the playoff trio instead of the round robin’s', async () => {
      await openWith(playControls({ playoff_phase_active: true, playoff_tournament_id: 40 }));
      await openMenu();
      expect(labels()).toEqual(['Play Single Round', 'Play Two Months', 'Through Playoffs']);
    });

    it('no round-robin items on a member-night phase', async () => {
      await openWith(playControls({ member_night_phase_active: true }));
      await openMenu();
      expect(labels()).not.toContain('Play One Week');
    });

    it('Start Next Season for a completed non-career Season', async () => {
      await openWith(playControls({ action_button_state: 'start_next_season' }));
      await openMenu();
      expect(labels()).toEqual(['Start Next Season']);
      expect(item('Start Next Season')?.tagName).toBe('BUTTON');
    });

    it('Start Next Season links to the Owner Evaluation in career mode', async () => {
      await openWith(
        playControls({ action_button_state: 'start_next_season', is_career_mode: true }),
      );
      await openMenu();
      const link = item('Start Next Season');
      expect(link?.tagName).toBe('A');
      expect(link?.getAttribute('href')).toBe('/leagues/1/seasons/11/owner-evaluation');
      link?.click();
      await fixture.whenStable();
      http.expectNone(`${BASE}/leagues/1/next-season/`);
      expect(TestBed.inject(Router).url).toBe('/leagues/1/seasons/11/owner-evaluation');
    });

    it('a disabled "Play" with nothing to play', async () => {
      await openWith(playControls({ action_button_state: 'none' }));
      await openMenu();
      expect(labels()).toEqual(['Play']);
      expect(disabled(item('Play'))).toBe(true);
    });

    it('only the disabled "Play" with no Displayed Season (I4)', async () => {
      await openWith(playControls({ displayed_season: null }));
      await openMenu();
      expect(labels()).toEqual(['Play']);
      expect(disabled(item('Play'))).toBe(true);
    });
  });

  describe('actions', () => {
    const PLAYOFFS = playControls({ playoff_phase_active: true, playoff_tournament_id: 40 });
    it.each<[string, string, PlayControls]>([
      ['Start Season', 'start/', playControls({ action_button_state: 'start_season' })],
      ['Play One Week', 'play-week/', playControls()],
      ['Play Two Months', 'play-two-months/', playControls()],
      ['Play Until End of Season', 'play-until-end/', playControls()],
      ['Play Single Round', 'play-single-round/', PLAYOFFS],
      ['Through Playoffs', 'play-playoffs/', PLAYOFFS],
    ])('%s POSTs seasons/11/%s', async (label, path, body) => {
      await openWith(body);
      await openMenu();
      item(label)?.click();
      await fixture.whenStable();
      const req = http.expectOne(`${BASE}/seasons/11/${path}`);
      expect(req.request.method).toBe('POST');
    });

    it('Start Next Season POSTs leagues/1/next-season/', async () => {
      await openWith(playControls({ action_button_state: 'start_next_season' }));
      await openMenu();
      item('Start Next Season')?.click();
      await fixture.whenStable();
      expect(http.expectOne(`${BASE}/leagues/1/next-season/`).request.method).toBe('POST');
    });

    it('the playoff Play Two Months POSTs play-two-months', async () => {
      await openWith(PLAYOFFS);
      await openMenu();
      item('Play Two Months')?.click();
      await fixture.whenStable();
      http.expectOne(`${BASE}/seasons/11/play-two-months/`);
    });

    it('Play One Week POSTs play-week for the Displayed Season', async () => {
      await openWith(playControls());
      await openMenu();
      item('Play One Week')?.click();
      await fixture.whenStable();
      const req = http.expectOne(`${BASE}/seasons/11/play-week/`);
      expect(req.request.method).toBe('POST');
      req.flush({ data: seasonDashboard(), meta: {} });
      await fixture.whenStable();
      http.expectOne(CONTROLS_URL).flush({ data: playControls(), meta: {} });
    });

    it('disables every item while an action is in flight (I1)', async () => {
      await openWith(playControls());
      await openMenu();
      item('Play One Week')?.click();
      await fixture.whenStable();
      await openMenu();
      expect(items().every((el) => disabled(el))).toBe(true);
      http
        .expectOne(`${BASE}/seasons/11/play-week/`)
        .flush(
          { errors: [{ code: 'x', message: 'Nope.', field: null }], meta: {} },
          { status: 409, statusText: 'Conflict' },
        );
      await fixture.whenStable();
    });

    it('shows a refused action’s message in the alert (I3)', async () => {
      await openWith(playControls());
      await openMenu();
      item('Play One Week')?.click();
      http.expectOne(`${BASE}/seasons/11/play-week/`).flush(
        {
          errors: [{ code: 'season_not_active', message: 'Not active.', field: null }],
          meta: {},
        },
        { status: 409, statusText: 'Conflict' },
      );
      await fixture.whenStable();
      expect(text(alert())).toBe('Not active.');
    });
  });

  describe('member nights (ANG-06-19b)', () => {
    const SET_UP = 'Set Up Member Night…';
    const CONFIRM = 'Confirm — Set Up Member Night';
    const SETUP = playControls({
      member_night_phase_active: true,
      member_night_sites: [
        { site: 'Downtown', count: 14 },
        { site: 'Uptown', count: 12 },
      ],
    });
    const UNPLAYED = playControls({
      member_night_phase_active: true,
      member_night_has_unplayed: true,
    });

    const dialog = (): HTMLElement | null => document.querySelector('mat-dialog-container');
    const dialogButton = (label: string): HTMLButtonElement | undefined =>
      [...(dialog()?.querySelectorAll('button') ?? [])].find((b) => text(b) === label);

    async function click(el: HTMLElement | undefined): Promise<void> {
      el?.click();
      await fixture.whenStable();
    }

    it('offers only "Set Up Member Night…" while nothing is drawn', async () => {
      await openWith(SETUP);
      await openMenu();
      expect(labels()).toEqual([SET_UP]);
    });

    it('offers Play Single Members Game, its Live game and Play All once games are drawn', async () => {
      await openWith(UNPLAYED);
      await openMenu();
      expect(labels()).toEqual([
        'Play Single Members Game',
        'Single Members Game (Live)',
        'Play All',
      ]);
    });

    it('no member-night items off a member-night phase', async () => {
      await openWith(playControls({ member_night_has_unplayed: true }));
      await openMenu();
      expect(labels()).not.toContain('Play All');
      expect(labels()).not.toContain(SET_UP);
    });

    it('Set Up opens the Sites dialog; Confirm POSTs member-night/ with the Sites', async () => {
      await openWith(SETUP);
      await openMenu();
      await click(item(SET_UP));
      expect(text(dialog())).toContain('Downtown (14)');
      expect(text(dialog())).toContain('Uptown (12)');
      await click(dialogButton(CONFIRM));
      const req = http.expectOne(`${BASE}/seasons/11/member-night/`);
      expect(req.request.method).toBe('POST');
      expect(req.request.body).toEqual({ sites: ['Downtown', 'Uptown'] });
      req.flush({ data: seasonDashboard(), meta: {} });
      await fixture.whenStable();
      http.expectOne(CONTROLS_URL).flush({ data: UNPLAYED, meta: {} });
    });

    it('Cancel in the dialog sends nothing', async () => {
      await openWith(SETUP);
      await openMenu();
      await click(item(SET_UP));
      await click(dialogButton('Cancel'));
      expect(dialog()).toBeNull();
      http.expectNone(`${BASE}/seasons/11/member-night/`);
    });

    it('Play Single Members Game reads member-night/ first', async () => {
      await openWith(UNPLAYED);
      await openMenu();
      await click(item('Play Single Members Game'));
      expect(http.expectOne(`${BASE}/seasons/11/member-night/`).request.method).toBe('GET');
    });

    it('Play All POSTs member-night/play/', async () => {
      await openWith(UNPLAYED);
      await openMenu();
      await click(item('Play All'));
      expect(http.expectOne(`${BASE}/seasons/11/member-night/play/`).request.method).toBe('POST');
    });

    it.each([SETUP, UNPLAYED])('disables every member-night item while busy (I1)', async (body) => {
      await openWith(body);
      await openMenu();
      TestBed.inject(PlayRun).run('play_week');
      await fixture.whenStable();
      await openMenu();
      expect(items().length).toBeGreaterThan(0);
      expect(items().every((el) => disabled(el))).toBe(true);
      http
        .expectOne(`${BASE}/seasons/11/play-week/`)
        .flush(
          { errors: [{ code: 'x', message: 'Nope.', field: null }], meta: {} },
          { status: 409, statusText: 'Conflict' },
        );
      await fixture.whenStable();
    });

    it('shows a 409 wrong_phase message in the alert (I3)', async () => {
      await openWith(UNPLAYED);
      await openMenu();
      await click(item('Play All'));
      http.expectOne(`${BASE}/seasons/11/member-night/play/`).flush(
        {
          errors: [{ code: 'wrong_phase', message: 'Not a member night.', field: null }],
          meta: {},
        },
        { status: 409, statusText: 'Conflict' },
      );
      await fixture.whenStable();
      expect(text(alert())).toBe('Not a member night.');
    });
  });

  describe('Live items (ANG-06-19c)', () => {
    const LIVE_RR = playControls({ live_preview_available: true });
    const LIVE_PLAYOFFS = playControls({
      playoff_phase_active: true,
      playoff_tournament_id: 40,
      live_preview_available: true,
    });
    const UNPLAYED = playControls({
      member_night_phase_active: true,
      member_night_has_unplayed: true,
    });

    it('adds "One Week (Live)" after the round-robin trio with a live preview', async () => {
      await openWith(LIVE_RR);
      await openMenu();
      expect(labels()).toEqual([
        'Play One Week',
        'Play Two Months',
        'Play Until End of Season',
        'One Week (Live)',
      ]);
    });

    it('adds "Play Single Round (Live)" after the playoff trio with a live preview', async () => {
      await openWith(LIVE_PLAYOFFS);
      await openMenu();
      expect(labels()).toEqual([
        'Play Single Round',
        'Play Two Months',
        'Through Playoffs',
        'Play Single Round (Live)',
      ]);
    });

    it('no "(Live)" item in the playoffs without a live preview', async () => {
      await openWith(playControls({ playoff_phase_active: true, playoff_tournament_id: 40 }));
      await openMenu();
      expect(labels()).not.toContain('Play Single Round (Live)');
    });

    it('no "Single Members Game (Live)" before member-night games are drawn', async () => {
      await openWith(playControls({ member_night_phase_active: true }));
      await openMenu();
      expect(labels()).not.toContain('Single Members Game (Live)');
    });

    it.each<[string, string, PlayControls]>([
      ['One Week (Live)', 'play-live/', LIVE_RR],
      ['Play Single Round (Live)', 'play-live/', LIVE_PLAYOFFS],
      ['Single Members Game (Live)', 'member-night/play-live/', UNPLAYED],
    ])('%s POSTs seasons/11/%s', async (label, path, body) => {
      await openWith(body);
      await openMenu();
      item(label)?.click();
      await fixture.whenStable();
      expect(http.expectOne(`${BASE}/seasons/11/${path}`).request.method).toBe('POST');
    });

    it.each<[string, PlayControls]>([
      ['One Week (Live)', LIVE_RR],
      ['Play Single Round (Live)', LIVE_PLAYOFFS],
      ['Single Members Game (Live)', UNPLAYED],
    ])('disables %s while busy (I1)', async (label, body) => {
      await openWith(body);
      TestBed.inject(PlayRun).run('play_week');
      await fixture.whenStable();
      await openMenu();
      expect(disabled(item(label))).toBe(true);
      // The playoffs and member nights have no Play One Week: only the round robin sent it.
      http
        .match(`${BASE}/seasons/11/play-week/`)
        .forEach((req) =>
          req.flush(
            { errors: [{ code: 'x', message: 'Nope.', field: null }], meta: {} },
            { status: 409, statusText: 'Conflict' },
          ),
        );
      await fixture.whenStable();
    });

    it('shows a 409 no_live_cursor message in the alert (I3)', async () => {
      await openWith(LIVE_RR);
      await openMenu();
      item('One Week (Live)')?.click();
      http.expectOne(`${BASE}/seasons/11/play-live/`).flush(
        {
          errors: [
            { code: 'no_live_cursor', message: 'No live game to play for your team.', field: null },
          ],
          meta: {},
        },
        { status: 409, statusText: 'Conflict' },
      );
      await fixture.whenStable();
      expect(text(alert())).toBe('No live game to play for your team.');
    });
  });

  describe('a running Job', () => {
    async function running(width = 1280): Promise<void> {
      await openWith(playControls({ active_play_job_id: 'job-1' }), width);
      vi.useFakeTimers({ toFake: POLL_TIMERS });
    }

    const stopButton = (): HTMLButtonElement | undefined =>
      [...host().querySelectorAll('button')].find((b) => text(b) === 'Stop');
    const label = (): HTMLElement | null => host().querySelector('.play-menu__label');
    const bar = (): HTMLElement | null => host().querySelector('mat-progress-bar');

    it('swaps the trigger for Stop, "Running…" and an indeterminate bar while total is 0', async () => {
      await running();
      expect(trigger()).toBeNull();
      expect(stopButton()).toBeDefined();
      expect(text(label())).toBe('Running…');
      expect(bar()?.getAttribute('mode')).toBe('indeterminate');
      http.expectOne(JOB_URL);
    });

    it('reads "Running… c / t" with a determinate bar', async () => {
      await running();
      http.expectOne(JOB_URL).flush({ data: status({ completed: 2, total: 8 }), meta: {} });
      await fixture.whenStable();
      expect(text(label())).toBe('Running… 2 / 8');
      expect(bar()?.getAttribute('mode')).toBe('determinate');
      expect(bar()?.getAttribute('aria-valuenow')).toBe('25');
    });

    it('Stop POSTs play-cancel, then is disabled until the Job ends', async () => {
      await running();
      http.expectOne(JOB_URL).flush({ data: status({ completed: 1 }), meta: {} });
      await fixture.whenStable();
      stopButton()?.click();
      await fixture.whenStable();
      const req = http.expectOne(`${BASE}/seasons/11/play-cancel/`);
      expect(req.request.method).toBe('POST');
      req.flush({ data: { cancel_requested: true }, meta: {} });
      await fixture.whenStable();
      expect(stopButton()?.disabled).toBe(true);
    });

    it('brings the trigger back when the Job ends', async () => {
      await running();
      http.expectOne(JOB_URL).flush({ data: status({ status: 'complete' }), meta: {} });
      await fixture.whenStable();
      http.expectOne(CONTROLS_URL).flush({ data: playControls(), meta: {} });
      await fixture.whenStable();
      expect(stopButton()).toBeUndefined();
      expect(trigger()).not.toBeNull();
    });

    it('shows a failed Job’s error in the alert', async () => {
      await running();
      http
        .expectOne(JOB_URL)
        .flush({ data: status({ status: 'error', error: 'Boom.' }), meta: {} });
      await fixture.whenStable();
      http.expectOne(CONTROLS_URL).flush({ data: playControls(), meta: {} });
      await fixture.whenStable();
      expect(text(alert())).toBe('Boom.');
    });

    it('hides the label below 600px, keeping Stop and the bar', async () => {
      await running(599);
      expect(label()).toBeNull();
      expect(stopButton()).toBeDefined();
      expect(bar()).not.toBeNull();
      http.expectOne(JOB_URL);
    });
  });
});
