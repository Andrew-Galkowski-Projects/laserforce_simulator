import { Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  TestRequest,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { MatSnackBar } from '@angular/material/snack-bar';
import { Router, provideRouter } from '@angular/router';
import { environment } from '../../../environments/environment';
import { PlayControls } from '../../api/leagues.models';
import { SeasonPlayJobStatus } from '../../api/seasons.models';
import { POLL_TIMERS } from '../../shared/testing/poll-timers';
import { playControls, season, seasonDashboard } from '../testing/league-fixtures';
import { PlayAction, PlayRun } from './play-run';

const BASE = environment.apiBaseUrl;
const CONTROLS_URL = `${BASE}/leagues/1/play-controls/`;
const jobUrl = (jobId: string): string => `${BASE}/seasons/11/play-jobs/${jobId}/`;

/** Any URL the run navigates to lands here: the spec reads `Router.url`. */
@Component({ template: '' })
class Blank {}

const status = (overrides: Partial<SeasonPlayJobStatus> = {}): SeasonPlayJobStatus => ({
  status: 'running',
  completed: 0,
  total: 8,
  cancelled: false,
  error: null,
  ...overrides,
});

/** An envelope error body, flushed as `req.flush(...fail(409, code, message, field))`. */
const fail = (status: number, code: string, message: string, field: string | null = null) =>
  [
    { errors: [{ code, message, field }], meta: {} },
    { status, statusText: 'Error' },
  ] as const;

describe('PlayRun (spec #454)', () => {
  let run: PlayRun;
  let http: HttpTestingController;
  let snack: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideRouter([{ path: '**', component: Blank }]),
        provideHttpClient(),
        provideHttpClientTesting(),
      ],
    });
    run = TestBed.inject(PlayRun);
    http = TestBed.inject(HttpTestingController);
    snack = vi.spyOn(TestBed.inject(MatSnackBar), 'open');
  });

  afterEach(() => {
    vi.useRealTimers();
    http.verify();
  });

  const routerUrl = (): string => TestBed.inject(Router).url;

  /** Lets the Router finish a navigation (its steps are promises and a macrotask or two). */
  async function settle(): Promise<void> {
    for (let i = 0; i < 3; i++) await new Promise((resolve) => setTimeout(resolve, 0));
  }

  function expectPost(path: string): TestRequest {
    const req = http.expectOne(`${BASE}/${path}`);
    expect(req.request.method).toBe('POST');
    return req;
  }

  function answerControls(body: PlayControls = playControls()): void {
    const req = http.expectOne(CONTROLS_URL);
    expect(req.request.method).toBe('GET');
    req.flush({ data: body, meta: {} });
  }

  /** `watch(1)` with the controls answered. */
  function watched(body: PlayControls = playControls()): void {
    run.watch(1);
    answerControls(body);
  }

  describe('watch', () => {
    it('loads the League play controls', () => {
      run.watch(1);
      expect(run.controls()).toBeNull();
      answerControls();
      expect(run.controls()?.displayed_season).toEqual({ id: 11, name: 'Season 1' });
    });

    it('clears the controls with null, sending nothing', () => {
      watched();
      run.watch(null);
      expect(run.controls()).toBeNull();
      http.expectNone(CONTROLS_URL);
    });
  });

  describe('sync actions (I2, I4)', () => {
    it.each<[PlayAction, string]>([
      ['play_week', 'seasons/11/play-week/'],
      ['play_single_round', 'seasons/11/play-single-round/'],
      ['start_season', 'seasons/11/start/'],
    ])('%s POSTs %s, lands on the Season dashboard and re-fetches', async (action, path) => {
      watched();
      const before = run.changed();
      run.run(action);
      expect(run.busy()).toBe(true);
      expectPost(path).flush({ data: seasonDashboard(), meta: {} });
      await settle();
      expect(run.busy()).toBe(false);
      expect(routerUrl()).toBe('/leagues/1/seasons/11');
      expect(run.changed()).toBe(before + 1);
      answerControls();
    });

    it('notifies the Rounds scheduled after Start Season', async () => {
      watched(playControls({ action_button_state: 'start_season' }));
      run.run('start_season');
      expectPost('seasons/11/start/').flush({
        data: seasonDashboard({ round_count_total: 56 }),
        meta: {},
      });
      await settle();
      expect(snack).toHaveBeenCalledWith(
        'Season 1 started — 56 Rounds scheduled.',
        'Close',
        expect.anything(),
      );
      answerControls();
    });

    it('sends nothing with no Displayed Season', () => {
      watched(playControls({ displayed_season: null, action_button_state: 'none' }));
      run.run('play_week');
      http.expectNone(`${BASE}/seasons/11/play-week/`);
      expect(run.busy()).toBe(false);
    });
  });

  describe('screenStale: whether the open screen must be re-created', () => {
    it('does not tick when the landing opened another URL (the router created that screen)', async () => {
      watched();
      const before = run.screenStale();
      run.run('play_week');
      expectPost('seasons/11/play-week/').flush({ data: seasonDashboard(), meta: {} });
      await settle();
      expect(routerUrl()).toBe('/leagues/1/seasons/11');
      expect(run.screenStale()).toBe(before);
      answerControls();
    });

    it('ticks with changed when the landing URL was already open', async () => {
      await TestBed.inject(Router).navigateByUrl('/leagues/1/seasons/11');
      watched();
      const stale = run.screenStale();
      const changed = run.changed();
      run.run('play_week');
      expectPost('seasons/11/play-week/').flush({ data: seasonDashboard(), meta: {} });
      await settle();
      expect(run.screenStale()).toBe(stale + 1);
      expect(run.changed()).toBe(changed + 1);
      answerControls();
    });

    it('ticks when a Job finishes', () => {
      watched();
      vi.useFakeTimers({ toFake: POLL_TIMERS });
      run.run('play_two_months');
      expectPost('seasons/11/play-two-months/').flush({
        data: { job_id: 'job-1', season_id: 11, status_url: '' },
        meta: {},
      });
      const before = run.screenStale();
      http.expectOne(jobUrl('job-1')).flush({ data: status({ status: 'complete' }), meta: {} });
      expect(run.screenStale()).toBe(before + 1);
      answerControls();
    });
  });

  describe('Start Next Season', () => {
    it('lands on the new draft Season’s Conferences', async () => {
      watched(playControls({ action_button_state: 'start_next_season' }));
      run.run('start_next_season');
      expectPost('leagues/1/next-season/').flush(
        { data: season({ id: 12, state: 'draft' }), meta: {} },
        { status: 201, statusText: 'Created' },
      );
      await settle();
      expect(routerUrl()).toBe('/leagues/1/seasons/12/conferences');
      answerControls();
    });

    it('lands on the new active Season’s dashboard', async () => {
      watched(playControls({ action_button_state: 'start_next_season' }));
      const before = run.changed();
      run.run('start_next_season');
      expectPost('leagues/1/next-season/').flush(
        { data: season({ id: 12, state: 'active' }), meta: {} },
        { status: 201, statusText: 'Created' },
      );
      await settle();
      expect(routerUrl()).toBe('/leagues/1/seasons/12');
      expect(run.changed()).toBe(before + 1);
      answerControls();
    });

    it('sends a fired Manager to the new-team picker, with no error', async () => {
      watched(playControls({ action_button_state: 'start_next_season' }));
      run.run('start_next_season');
      expectPost('leagues/1/next-season/').flush(...fail(409, 'manager_fired', 'You were fired.'));
      await settle();
      expect(routerUrl()).toBe('/leagues/1/new-team');
      expect(run.error()).toBe('');
      expect(run.busy()).toBe(false);
    });
  });

  describe('errors (I3)', () => {
    it('shows a refused action’s message and lands nowhere', async () => {
      watched();
      run.run('play_week');
      expectPost('seasons/11/play-week/').flush(
        ...fail(409, 'season_not_active', 'The Season is not active.'),
      );
      await settle();
      expect(run.error()).toBe('The Season is not active.');
      expect(run.busy()).toBe(false);
      expect(routerUrl()).toBe('/');
    });

    it('shows the generic message for a non-API failure', async () => {
      watched();
      run.run('play_week');
      http
        .expectOne(`${BASE}/seasons/11/play-week/`)
        .error(new ProgressEvent('error'), { status: 0 });
      await settle();
      expect(run.error()).not.toBe('');
    });

    it('clears the error at the next action', () => {
      watched();
      run.run('play_week');
      expectPost('seasons/11/play-week/').flush(...fail(409, 'x', 'Nope.'));
      run.run('play_week');
      expect(run.error()).toBe('');
      expectPost('seasons/11/play-week/');
    });
  });

  describe('I1: one action at a time', () => {
    it('ignores a second action while one is in flight', () => {
      watched();
      run.run('play_week');
      run.run('play_two_months');
      http.expectNone(`${BASE}/seasons/11/play-two-months/`);
      expectPost('seasons/11/play-week/');
    });
  });

  describe('member nights (ANG-06-19b)', () => {
    const MEMBER_NIGHT = playControls({
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

    it('setUpMemberNight POSTs member-night/ with the Sites, lands and re-fetches', async () => {
      watched(MEMBER_NIGHT);
      const before = run.changed();
      run.setUpMemberNight(['Downtown', 'Uptown']);
      expect(run.busy()).toBe(true);
      const req = expectPost('seasons/11/member-night/');
      expect(req.request.body).toEqual({ sites: ['Downtown', 'Uptown'] });
      req.flush({
        data: seasonDashboard({ member_night_progress: { played: 0, total: 6 } }),
        meta: {},
      });
      await settle();
      expect(run.busy()).toBe(false);
      expect(routerUrl()).toBe('/leagues/1/seasons/11');
      expect(run.changed()).toBe(before + 1);
      expect(snack).toHaveBeenCalledWith(
        'Member night set up: 6 games drawn across 2 Sites.',
        'Close',
        expect.anything(),
      );
      answerControls(UNPLAYED);
    });

    it('says "Site" for one Site, and 0 games with no progress', async () => {
      watched(MEMBER_NIGHT);
      run.setUpMemberNight(['Downtown']);
      expectPost('seasons/11/member-night/').flush({ data: seasonDashboard(), meta: {} });
      await settle();
      expect(snack).toHaveBeenCalledWith(
        'Member night set up: 0 games drawn across 1 Site.',
        'Close',
        expect.anything(),
      );
      answerControls();
    });

    it('setUpMemberNight shows a 409 wrong_phase message (I3)', async () => {
      watched(MEMBER_NIGHT);
      run.setUpMemberNight(['Downtown']);
      expectPost('seasons/11/member-night/').flush(
        ...fail(409, 'wrong_phase', 'The Season is not on a member-night phase.'),
      );
      await settle();
      expect(run.error()).toBe('The Season is not on a member-night phase.');
      expect(routerUrl()).toBe('/');
    });

    it('setUpMemberNight is ignored while another action is in flight (I1)', () => {
      watched(MEMBER_NIGHT);
      run.run('play_week');
      run.setUpMemberNight(['Downtown']);
      http.expectNone(`${BASE}/seasons/11/member-night/`);
      expectPost('seasons/11/play-week/');
    });

    /** Answers the pre-play `member-night/` GET with T games, U of them unplayed. */
    function answerMemberNight(total: number, unplayed: number): void {
      const req = http.expectOne(`${BASE}/seasons/11/member-night/`);
      expect(req.request.method).toBe('GET');
      req.flush({
        data: {
          season: { id: 11, name: 'Season 1' },
          phase_active: true,
          sites: [{ site: 'Downtown', count: 14, games: total }],
          games_total: total,
          games_unplayed: unplayed,
        },
        meta: {},
      });
    }

    it('member_night_single GETs member-night/, then POSTs play-single and notifies P / T', async () => {
      watched(UNPLAYED);
      const before = run.changed();
      run.run('member_night_single');
      expect(run.busy()).toBe(true);
      http.expectNone(`${BASE}/seasons/11/member-night/play-single/`);
      answerMemberNight(6, 4);
      expectPost('seasons/11/member-night/play-single/').flush({
        data: seasonDashboard(),
        meta: {},
      });
      await settle();
      expect(run.busy()).toBe(false);
      expect(routerUrl()).toBe('/leagues/1/seasons/11');
      expect(run.changed()).toBe(before + 1);
      expect(snack).toHaveBeenCalledWith(
        'Member-night game played: 3 / 6 games done.',
        'Close',
        expect.anything(),
      );
      answerControls(UNPLAYED);
    });

    it('member_night_single on the last game says the night is complete', async () => {
      watched(UNPLAYED);
      run.run('member_night_single');
      answerMemberNight(6, 1);
      expectPost('seasons/11/member-night/play-single/').flush({
        data: seasonDashboard(),
        meta: {},
      });
      await settle();
      expect(snack).toHaveBeenCalledWith(
        'Member night complete: all 6 games played.',
        'Close',
        expect.anything(),
      );
      answerControls();
    });

    it('member_night_single shows a refused play and lands nowhere (I3)', async () => {
      watched(UNPLAYED);
      run.run('member_night_single');
      answerMemberNight(6, 4);
      expectPost('seasons/11/member-night/play-single/').flush(
        ...fail(409, 'wrong_phase', 'Not a member night.'),
      );
      await settle();
      expect(run.error()).toBe('Not a member night.');
      expect(run.busy()).toBe(false);
      expect(routerUrl()).toBe('/');
      expect(snack).not.toHaveBeenCalled();
    });

    it('member_night_single shows a failed pre-play GET and sends no POST', async () => {
      watched(UNPLAYED);
      run.run('member_night_single');
      http
        .expectOne(`${BASE}/seasons/11/member-night/`)
        .flush(...fail(404, 'not_found', 'Not found.'));
      await settle();
      expect(run.error()).toBe('Not found.');
      expect(run.busy()).toBe(false);
      http.expectNone(`${BASE}/seasons/11/member-night/play-single/`);
    });

    it('member_night_single picks up a 409 play_in_progress Job', async () => {
      watched(UNPLAYED);
      vi.useFakeTimers({ toFake: POLL_TIMERS });
      run.run('member_night_single');
      answerMemberNight(6, 4);
      expectPost('seasons/11/member-night/play-single/').flush(
        ...fail(409, 'play_in_progress', 'A play run is already in progress.', 'job:live-3'),
      );
      expect(run.error()).toBe('');
      expect(run.progress()).toEqual({ completed: 0, total: 0 });
      http.expectOne(jobUrl('live-3'));
    });

    it('member_night_play_all POSTs member-night/play/ and polls its Job until it ends', async () => {
      watched(UNPLAYED);
      vi.useFakeTimers({ toFake: POLL_TIMERS });
      run.run('member_night_play_all');
      expect(run.busy()).toBe(true);
      expectPost('seasons/11/member-night/play/').flush({
        data: { job_id: 'job-5', season_id: 11, status_url: '/api/seasons/11/play-jobs/job-5/' },
        meta: {},
      });
      expect(run.busy()).toBe(false);
      http.expectOne(jobUrl('job-5')).flush({ data: status({ completed: 2, total: 6 }), meta: {} });
      expect(run.progress()).toEqual({ completed: 2, total: 6 });
      run.stop();
      expectPost('seasons/11/play-cancel/').flush({ data: { cancel_requested: true }, meta: {} });
      vi.advanceTimersByTime(1000);
      const before = run.changed();
      http
        .expectOne(jobUrl('job-5'))
        .flush({ data: status({ status: 'complete', cancelled: true }), meta: {} });
      expect(run.progress()).toBeNull();
      expect(run.changed()).toBe(before + 1);
      answerControls();
    });

    it('member_night_play_all picks up a 409 play_in_progress Job', () => {
      watched(UNPLAYED);
      vi.useFakeTimers({ toFake: POLL_TIMERS });
      run.run('member_night_play_all');
      expectPost('seasons/11/member-night/play/').flush(
        ...fail(409, 'play_in_progress', 'A play run is already in progress.', 'job:live-8'),
      );
      expect(run.error()).toBe('');
      http.expectOne(jobUrl('live-8'));
    });

    it('member_night_play_all shows a 409 wrong_phase message (I3)', () => {
      watched(UNPLAYED);
      run.run('member_night_play_all');
      expectPost('seasons/11/member-night/play/').flush(
        ...fail(409, 'wrong_phase', 'Not a member night.'),
      );
      expect(run.error()).toBe('Not a member night.');
      expect(run.progress()).toBeNull();
    });
  });

  describe('Live actions (ANG-06-19c)', () => {
    const LIVE = playControls({ live_preview_available: true });
    const live = (round_ids: number[], job: string | null = null) => ({
      data: {
        kind: 'rr',
        round_ids,
        job: job && { job_id: job, season_id: 11, status_url: `/api/seasons/11/play-jobs/${job}/` },
      },
      meta: {},
    });

    it.each<['season' | 'member_night', string]>([
      ['season', 'seasons/11/play-live/'],
      ['member_night', 'seasons/11/member-night/play-live/'],
    ])('%s POSTs %s and lands on the Live screen with its Rounds', async (source, path) => {
      watched(LIVE);
      const before = run.changed();
      run.playLive(source);
      expect(run.busy()).toBe(true);
      const req = expectPost(path);
      req.flush(live([31, 32]));
      await settle();
      expect(run.busy()).toBe(false);
      expect(routerUrl()).toBe('/leagues/1/seasons/11/live?rounds=31,32');
      expect(run.changed()).toBe(before + 1);
      answerControls();
    });

    it('lands on the Season dashboard on a bye (no Rounds)', async () => {
      watched(LIVE);
      const before = run.changed();
      run.playLive('season');
      expectPost('seasons/11/play-live/').flush(live([]));
      await settle();
      expect(routerUrl()).toBe('/leagues/1/seasons/11');
      expect(run.changed()).toBe(before + 1);
      answerControls();
    });

    it('polls a returned Job as a background run', async () => {
      watched(LIVE);
      vi.useFakeTimers({ toFake: POLL_TIMERS });
      run.playLive('season');
      expectPost('seasons/11/play-live/').flush(live([31], 'job-4'));
      expect(run.progress()).toEqual({ completed: 0, total: 0 });
      http.expectOne(jobUrl('job-4')).flush({ data: status({ completed: 1, total: 3 }), meta: {} });
      expect(run.progress()).toEqual({ completed: 1, total: 3 });
      await settle();
      expect(routerUrl()).toBe('/leagues/1/seasons/11/live?rounds=31');
      answerControls();
    });

    it('keeps polling the Job when the re-fetch after the POST names it', async () => {
      watched(LIVE);
      vi.useFakeTimers({ toFake: POLL_TIMERS });
      run.playLive('season');
      expectPost('seasons/11/play-live/').flush(live([31], 'job-4'));
      http.expectOne(jobUrl('job-4')).flush({ data: status({ completed: 1, total: 3 }), meta: {} });
      await settle();
      answerControls(playControls({ live_preview_available: true, active_play_job_id: 'job-4' }));
      expect(run.progress()).toEqual({ completed: 1, total: 3 });
      http.expectNone(jobUrl('job-4'));
      vi.advanceTimersByTime(1000);
      http.expectOne(jobUrl('job-4'));
    });

    it('shows a 409 no_live_cursor message and lands nowhere (I3)', async () => {
      watched(LIVE);
      run.playLive('season');
      expectPost('seasons/11/play-live/').flush(
        ...fail(409, 'no_live_cursor', 'No live game to play for your team.'),
      );
      await settle();
      expect(run.error()).toBe('No live game to play for your team.');
      expect(run.busy()).toBe(false);
      expect(routerUrl()).toBe('/');
    });

    it('picks up a 409 play_in_progress Job', () => {
      watched(LIVE);
      vi.useFakeTimers({ toFake: POLL_TIMERS });
      run.playLive('member_night');
      expectPost('seasons/11/member-night/play-live/').flush(
        ...fail(409, 'play_in_progress', 'A play run is already in progress.', 'job:live-2'),
      );
      expect(run.error()).toBe('');
      http.expectOne(jobUrl('live-2'));
    });

    it('is ignored while another action is in flight (I1)', () => {
      watched(LIVE);
      run.run('play_week');
      run.playLive('season');
      http.expectNone(`${BASE}/seasons/11/play-live/`);
      expectPost('seasons/11/play-week/');
    });
  });

  describe('background runs', () => {
    /** `watch(1)`, fakes the poll timer, runs `action` and answers its POST with job `job-1`. */
    async function startRun(action: PlayAction = 'play_two_months', path = 'play-two-months') {
      watched();
      vi.useFakeTimers({ toFake: POLL_TIMERS });
      run.run(action);
      expectPost(`seasons/11/${path}/`).flush({
        data: { job_id: 'job-1', season_id: 11, status_url: '/api/seasons/11/play-jobs/job-1/' },
        meta: {},
      });
    }

    function answer(answered: SeasonPlayJobStatus, jobId = 'job-1'): void {
      const req = http.expectOne(jobUrl(jobId));
      expect(req.request.method).toBe('GET');
      req.flush({ data: answered, meta: {} });
    }

    it.each<[PlayAction, string]>([
      ['play_two_months', 'play-two-months'],
      ['play_until_end', 'play-until-end'],
      ['play_playoffs', 'play-playoffs'],
    ])('%s POSTs play %s and polls its Job', async (action, path) => {
      await startRun(action, path);
      expect(run.busy()).toBe(false);
      expect(run.progress()).toEqual({ completed: 0, total: 0 });
      answer(status({ completed: 2, total: 8 }));
      expect(run.progress()).toEqual({ completed: 2, total: 8 });
      vi.advanceTimersByTime(1000);
      http.expectOne(jobUrl('job-1'));
    });

    it('polls again 1000 ms after each answer', async () => {
      await startRun();
      answer(status({ completed: 1 }));
      vi.advanceTimersByTime(999);
      http.expectNone(jobUrl('job-1'));
      vi.advanceTimersByTime(1);
      answer(status({ completed: 2 }));
      expect(run.progress()).toEqual({ completed: 2, total: 8 });
      vi.advanceTimersByTime(1000);
      http.expectOne(jobUrl('job-1'));
    });

    it('on "complete" clears the progress, re-fetches and stops polling (I2)', async () => {
      await startRun();
      const before = run.changed();
      answer(status({ status: 'complete', completed: 8, total: 8 }));
      expect(run.progress()).toBeNull();
      expect(run.changed()).toBe(before + 1);
      answerControls();
      vi.advanceTimersByTime(3000);
      http.expectNone(jobUrl('job-1'));
    });

    it('treats a cancelled Job as finished', async () => {
      await startRun();
      const before = run.changed();
      answer(status({ status: 'complete', cancelled: true, completed: 3 }));
      expect(run.progress()).toBeNull();
      expect(run.error()).toBe('');
      expect(run.changed()).toBe(before + 1);
      answerControls();
    });

    it('on "error" shows the Job error, clears the progress and re-fetches (I3, I2)', async () => {
      await startRun();
      const before = run.changed();
      answer(status({ status: 'error', error: 'Team 9 does not exist.' }));
      expect(run.error()).toBe('Team 9 does not exist.');
      expect(run.progress()).toBeNull();
      expect(run.changed()).toBe(before + 1);
      answerControls();
      vi.advanceTimersByTime(3000);
      http.expectNone(jobUrl('job-1'));
    });

    it('on "error" with no message says "Play job failed."', async () => {
      await startRun();
      answer(status({ status: 'error', error: null }));
      expect(run.error()).toBe('Play job failed.');
      answerControls();
    });

    it('keeps polling after a failed poll request, with no error', async () => {
      await startRun();
      answer(status({ completed: 1 }));
      vi.advanceTimersByTime(1000);
      http.expectOne(jobUrl('job-1')).error(new ProgressEvent('error'), { status: 0 });
      expect(run.error()).toBe('');
      expect(run.progress()).toEqual({ completed: 1, total: 8 });
      vi.advanceTimersByTime(999);
      http.expectNone(jobUrl('job-1'));
      vi.advanceTimersByTime(1);
      answer(status({ completed: 2 }));
      expect(run.progress()).toEqual({ completed: 2, total: 8 });
    });

    it('ignores every action while a Job is polled (I1)', async () => {
      await startRun();
      answer(status({ completed: 1 }));
      run.run('play_week');
      http.expectNone(`${BASE}/seasons/11/play-week/`);
    });

    it('picks up the named Job on a 409 play_in_progress, with no error', async () => {
      watched();
      vi.useFakeTimers({ toFake: POLL_TIMERS });
      run.run('play_until_end');
      expectPost('seasons/11/play-until-end/').flush(
        ...fail(409, 'play_in_progress', 'A play run is already in progress.', 'job:live-7'),
      );
      expect(run.error()).toBe('');
      expect(run.busy()).toBe(false);
      answer(status({ completed: 3, total: 5 }), 'live-7');
      expect(run.progress()).toEqual({ completed: 3, total: 5 });
    });

    it('shows a start failure (503 broker_unavailable) and does not poll', async () => {
      watched();
      vi.useFakeTimers({ toFake: POLL_TIMERS });
      run.run('play_two_months');
      expectPost('seasons/11/play-two-months/').flush(
        ...fail(503, 'broker_unavailable', 'The task queue is unavailable.'),
      );
      expect(run.error()).toBe('The task queue is unavailable.');
      expect(run.progress()).toBeNull();
      vi.advanceTimersByTime(3000);
      http.expectNone((req) => req.url.includes('/play-jobs/'));
    });

    it('resumes polling the active_play_job_id on watch', () => {
      vi.useFakeTimers({ toFake: POLL_TIMERS });
      watched(playControls({ active_play_job_id: 'job-9' }));
      expect(run.progress()).toEqual({ completed: 0, total: 0 });
      answer(status({ completed: 4 }), 'job-9');
      expect(run.progress()).toEqual({ completed: 4, total: 8 });
    });

    it('does not poll a finished Job again when the re-fetch still names it', async () => {
      await startRun();
      answer(status({ status: 'complete', completed: 8 }));
      answerControls(playControls({ active_play_job_id: 'job-1' }));
      expect(run.progress()).toBeNull();
      http.expectNone(jobUrl('job-1'));
    });

    it('stops polling when watch moves to another League', async () => {
      await startRun();
      answer(status({ completed: 1 }));
      run.watch(2);
      expect(run.progress()).toBeNull();
      http.expectOne(`${BASE}/leagues/2/play-controls/`).flush({
        data: playControls(),
        meta: {},
      });
      vi.advanceTimersByTime(3000);
      http.expectNone(jobUrl('job-1'));
    });

    it('Stop POSTs play-cancel and keeps polling until the Job ends', async () => {
      await startRun();
      answer(status({ completed: 1 }));
      run.stop();
      expect(run.stopRequested()).toBe(true);
      expectPost('seasons/11/play-cancel/').flush({ data: { cancel_requested: true }, meta: {} });
      vi.advanceTimersByTime(1000);
      answer(status({ completed: 2 }));
      expect(run.progress()).toEqual({ completed: 2, total: 8 });
      run.stop();
      http.expectNone(`${BASE}/seasons/11/play-cancel/`);
      vi.advanceTimersByTime(1000);
      answer(status({ status: 'complete', cancelled: true, completed: 2 }));
      expect(run.progress()).toBeNull();
      expect(run.stopRequested()).toBe(false);
      answerControls();
    });
  });
});
