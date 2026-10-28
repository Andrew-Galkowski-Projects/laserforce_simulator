import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { HarnessLoader } from '@angular/cdk/testing';
import { TestbedHarnessEnvironment } from '@angular/cdk/testing/testbed';
import { MatSnackBarHarness } from '@angular/material/snack-bar/testing';
import { provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { environment } from '../../../environments/environment';
import { signIn } from '../../api/testing/auth-fixtures';
import { PlayJobStatus, PlayNextResult, TournamentDetail } from '../../api/tournaments.models';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';
import { POLL_TIMERS } from '../../shared/testing/poll-timers';
import { tournamentDetail } from '../testing/detail-fixtures';

const BASE = environment.apiBaseUrl;
const DETAIL_URL = `${BASE}/tournaments/5/`;
const PLAY_NEXT_URL = `${BASE}/tournaments/5/play-next/`;
const PLAY_ALL_URL = `${BASE}/tournaments/5/play-all/`;
const jobUrl = (jobId: string): string => `${BASE}/tournaments/5/play-jobs/${jobId}/`;

/** An active sandbox single-elimination Tournament with a Match ready to play. */
const active = (overrides: Partial<TournamentDetail> = {}): TournamentDetail =>
  tournamentDetail({ state: 'active', stage: 'single_elimination', can_play: true, ...overrides });

/** The play-next answer for Round 1, Match 1: Red Raiders 1–0 Blue Bolts. */
const PLAYED: PlayNextResult = {
  node: {
    id: 40,
    bracket_type: 'winners',
    bracket_round: 1,
    position: 0,
    team_a: { id: 11, name: 'Red Raiders' },
    team_b: { id: 12, name: 'Blue Bolts' },
    seed_a: 1,
    seed_b: 2,
    is_bye: false,
    wins_a: 1,
    wins_b: 0,
    series_length: 1,
    winner: { id: 11, name: 'Red Raiders' },
    games: [{ game_number: 1, match_id: 70, score_a: 6, score_b: 0 }],
  },
  match_ids: [70],
  state: 'active',
  champion: null,
};

const status = (overrides: Partial<PlayJobStatus> = {}): PlayJobStatus => ({
  status: 'running',
  completed: 0,
  total: 3,
  error: null,
  tournament_id: 5,
  ...overrides,
});

/** An envelope error body. */
const failure = (code: string, message: string, field: string | null = null) => ({
  errors: [{ code, message, field }],
  meta: {},
});

describe('Tournament play (Tournament detail)', () => {
  let http: HttpTestingController;
  let harness: RouterTestingHarness;
  let loader: HarnessLoader;

  beforeEach(async () => {
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
    loader = TestbedHarnessEnvironment.documentRootLoader(harness.fixture);
  });

  afterEach(() => {
    vi.useRealTimers();
    http.verify();
  });

  const page = (): HTMLElement => harness.routeNativeElement as HTMLElement;
  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const section = (): HTMLElement | null => page().querySelector('app-tournament-play');
  const stable = (): Promise<void> => harness.fixture.whenStable();
  const errorBox = (): HTMLElement | null =>
    section()?.querySelector('.tournament-play__error') ?? null;
  const progress = (): HTMLElement | null =>
    section()?.querySelector('.tournament-play__progress') ?? null;
  const label = (): string => text(progress()?.querySelector('.tournament-play__label'));
  const bar = (): HTMLElement | null => progress()?.querySelector('mat-progress-bar') ?? null;

  function button(name: string): HTMLButtonElement {
    const found = [...(section()?.querySelectorAll('button') ?? [])].find((b) => text(b) === name);
    if (!found) throw new Error(`no ${name} button`);
    return found;
  }

  async function snackMessage(): Promise<string> {
    return (await loader.getHarness(MatSnackBarHarness)).getMessage();
  }

  async function open(detail: TournamentDetail = active()): Promise<void> {
    await harness.navigateByUrl(`/tournaments/${detail.id}`);
    http.expectOne(DETAIL_URL).flush({ data: detail, meta: {} });
    await stable();
  }

  /** The host's reload after `changed`. */
  async function answerReload(detail: TournamentDetail = active()): Promise<void> {
    http.expectOne(DETAIL_URL).flush({ data: detail, meta: {} });
    await stable();
  }

  describe('visibility', () => {
    it('shows both play buttons on an active sandbox Tournament', async () => {
      await open();
      expect(button('Play Next Match').disabled).toBe(false);
      expect(button('Play All').disabled).toBe(false);
    });

    it.each(['setup', 'completed'] as const)('is hidden in %s', async (state) => {
      await open(active({ state }));
      expect(section()?.querySelector('button')).toBeFalsy();
      expect(text(page())).not.toContain('Play Next Match');
    });

    it('is hidden on an embedded Tournament', async () => {
      await open(active({ embedded: true, season: { id: 3, name: 'S1' } }));
      expect(section()?.querySelector('button')).toBeFalsy();
    });

    it('disables Play Next Match when no Match is ready to play', async () => {
      await open(active({ can_play: false }));
      expect(button('Play Next Match').disabled).toBe(true);
      expect(button('Play All').disabled).toBe(false);
    });
  });

  describe('Play Next Match', () => {
    it('plays one Match, says what happened and reloads the detail', async () => {
      await open();
      button('Play Next Match').click();
      await stable();
      const req = http.expectOne(PLAY_NEXT_URL);
      expect(req.request.method).toBe('POST');
      req.flush({ data: PLAYED, meta: {} });
      await stable();
      expect(await snackMessage()).toBe('Played Round 1, Match 1: Red Raiders 1–0 Blue Bolts.');
      await answerReload();
    });

    it('disables both buttons while the Match is played', async () => {
      await open();
      button('Play Next Match').click();
      await stable();
      expect(button('Play Next Match').disabled).toBe(true);
      expect(button('Play All').disabled).toBe(true);
      http.expectOne(PLAY_NEXT_URL).flush({ data: PLAYED, meta: {} });
      await stable();
      await answerReload();
      expect(button('Play Next Match').disabled).toBe(false);
    });

    it.each([
      ['no_playable_node', 'No Match is ready to play.'],
      ['tournament_not_active', 'The Tournament is not active.'],
      ['play_in_progress', 'A play run is already in progress.'],
    ])('shows a 409 %s with its message', async (code, message) => {
      await open();
      button('Play Next Match').click();
      await stable();
      http
        .expectOne(PLAY_NEXT_URL)
        .flush(failure(code, message), { status: 409, statusText: 'Conflict' });
      await stable();
      expect(text(errorBox())).toBe(message);
      expect(errorBox()?.getAttribute('role')).toBe('alert');
      expect(button('Play Next Match').disabled).toBe(false);
      http.expectNone(DETAIL_URL);
    });

    it('clears the error when the next play starts', async () => {
      await open();
      button('Play Next Match').click();
      await stable();
      http
        .expectOne(PLAY_NEXT_URL)
        .flush(failure('no_playable_node', 'No Match is ready to play.'), {
          status: 409,
          statusText: 'Conflict',
        });
      await stable();
      button('Play Next Match').click();
      await stable();
      expect(errorBox()).toBeNull();
      http.expectOne(PLAY_NEXT_URL);
    });
  });

  describe('Play All', () => {
    /** Opens the page, fakes the poll timer, presses Play All. */
    async function pressPlayAll(): Promise<void> {
      await open();
      vi.useFakeTimers({ toFake: POLL_TIMERS });
      button('Play All').click();
      await stable();
    }

    /** Presses Play All and answers the start with job `job-1`. */
    async function startRun(): Promise<void> {
      await pressPlayAll();
      const req = http.expectOne(PLAY_ALL_URL);
      expect(req.request.method).toBe('POST');
      req.flush({
        data: {
          job_id: 'job-1',
          tournament_id: 5,
          status_url: '/api/tournaments/5/play-jobs/job-1/',
        },
        meta: {},
      });
      await stable();
    }

    async function answer(answered: PlayJobStatus, jobId = 'job-1'): Promise<void> {
      http.expectOne(jobUrl(jobId)).flush({ data: answered, meta: {} });
      await stable();
    }

    async function wait(ms: number): Promise<void> {
      vi.advanceTimersByTime(ms);
      await stable();
    }

    it('reads "Starting…" with an indeterminate bar until the first status', async () => {
      await pressPlayAll();
      expect(label()).toBe('Starting…');
      expect(bar()?.getAttribute('mode')).toBe('indeterminate');
      http.expectOne(PLAY_ALL_URL).flush({
        data: { job_id: 'job-1', tournament_id: 5, status_url: '' },
        meta: {},
      });
      await stable();
      expect(label()).toBe('Starting…');
      http.expectOne(jobUrl('job-1'));
    });

    it('shows the stage count with a determinate bar while running', async () => {
      await startRun();
      await answer(status({ completed: 1, total: 4 }));
      expect(label()).toBe('Running… stage 1 / 4');
      expect(bar()?.getAttribute('mode')).toBe('determinate');
      expect(bar()?.getAttribute('aria-valuenow')).toBe('25');
    });

    it('polls again 1000 ms after each answer', async () => {
      await startRun();
      await answer(status({ completed: 0 }));
      await wait(999);
      http.expectNone(jobUrl('job-1'));
      await wait(1);
      await answer(status({ completed: 2 }));
      expect(label()).toBe('Running… stage 2 / 3');
      await wait(1000);
      http.expectOne(jobUrl('job-1'));
    });

    it('disables both buttons while the run goes', async () => {
      await startRun();
      expect(button('Play All').disabled).toBe(true);
      expect(button('Play Next Match').disabled).toBe(true);
      await answer(status({ completed: 1 }));
      expect(button('Play All').disabled).toBe(true);
      expect(button('Play Next Match').disabled).toBe(true);
    });

    it('on "complete" notifies, reloads the detail and stops polling', async () => {
      await startRun();
      await answer(status({ completed: 1 }));
      await wait(1000);
      await answer(status({ status: 'complete', completed: 3, total: 3 }));
      expect(await snackMessage()).toBe('Play All complete.');
      await answerReload(active({ state: 'completed', can_play: false }));
      await wait(3000);
      http.expectNone(jobUrl('job-1'));
    });

    it('on "error" shows the job error and stops polling', async () => {
      await startRun();
      await answer(status({ status: 'error', error: 'Team 9 does not exist.' }));
      expect(text(errorBox())).toBe('Team 9 does not exist.');
      expect(progress()).toBeNull();
      expect(button('Play All').disabled).toBe(false);
      await wait(3000);
      http.expectNone(jobUrl('job-1'));
      http.expectNone(DETAIL_URL);
    });

    it('on "error" with no message points at the server logs', async () => {
      await startRun();
      await answer(status({ status: 'error', error: null }));
      expect(text(errorBox())).toBe('The Play All job failed. See the server logs.');
    });

    it('keeps polling after a failed status request, keeping the progress text', async () => {
      await startRun();
      await answer(status({ completed: 1 }));
      await wait(1000);
      http.expectOne(jobUrl('job-1')).error(new ProgressEvent('error'), { status: 0 });
      await stable();
      expect(errorBox()).toBeNull();
      expect(label()).toBe('Running… stage 1 / 3');
      expect(button('Play All').disabled).toBe(true);
      await wait(999);
      http.expectNone(jobUrl('job-1'));
      await wait(1);
      await answer(status({ completed: 2 }));
      expect(label()).toBe('Running… stage 2 / 3');
    });

    it('resumes the live run on a 409 play_in_progress, with no error', async () => {
      await pressPlayAll();
      http
        .expectOne(PLAY_ALL_URL)
        .flush(failure('play_in_progress', 'A play run is already in progress.', 'job:live-7'), {
          status: 409,
          statusText: 'Conflict',
        });
      await stable();
      expect(errorBox()).toBeNull();
      expect(label()).toBe('Starting…');
      await answer(status({ completed: 2, total: 5 }), 'live-7');
      expect(label()).toBe('Running… stage 2 / 5');
    });

    it('shows a 503 broker_unavailable message and stops', async () => {
      await pressPlayAll();
      http
        .expectOne(PLAY_ALL_URL)
        .flush(failure('broker_unavailable', 'The task queue is unavailable.'), {
          status: 503,
          statusText: 'Service Unavailable',
        });
      await stable();
      expect(text(errorBox())).toBe('The task queue is unavailable.');
      expect(progress()).toBeNull();
      expect(button('Play All').disabled).toBe(false);
      await wait(3000);
      http.expectNone((req) => req.url.includes('/play-jobs/'));
    });

    it('shows any other start error and does not poll', async () => {
      await pressPlayAll();
      http
        .expectOne(PLAY_ALL_URL)
        .flush(failure('tournament_not_active', 'The Tournament is not active.'), {
          status: 409,
          statusText: 'Conflict',
        });
      await stable();
      expect(text(errorBox())).toBe('The Tournament is not active.');
      await wait(3000);
      http.expectNone((req) => req.url.includes('/play-jobs/'));
    });

    it('clears the last error when Play All is pressed again', async () => {
      await startRun();
      await answer(status({ status: 'error', error: 'Boom.' }));
      button('Play All').click();
      await stable();
      expect(errorBox()).toBeNull();
      http.expectOne(PLAY_ALL_URL);
    });

    it('stops polling when the screen is left', async () => {
      await startRun();
      await answer(status({ completed: 1 }));
      harness.fixture.destroy();
      vi.advanceTimersByTime(3000);
      http.expectNone(jobUrl('job-1'));
    });
  });
});
