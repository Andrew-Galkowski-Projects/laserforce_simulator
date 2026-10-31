import { Injectable, inject, signal } from '@angular/core';
import { Router } from '@angular/router';
import { Observable, Subscription, map, switchMap, timer } from 'rxjs';
import { ApiError } from '../../api/api-client';
import { PlayControls, SeasonDashboard } from '../../api/leagues.models';
import { LeaguesService } from '../../api/leagues.service';
import { MemberNightState, SeasonPlayJobRef, SeasonPlayJobStatus } from '../../api/seasons.models';
import { SeasonsService } from '../../api/seasons.service';
import { apiMessage } from '../../shared/api-message';
import { GENERIC_ERROR_MESSAGE } from '../../shared/form-errors';
import { injectNotify } from '../../shared/notify';
import { seasonStartedMessage } from '../season-started';

/** A Play menu action that `run` takes (spec #454). */
export type PlayAction =
  | 'start_season'
  | 'play_week'
  | 'play_single_round'
  | 'play_two_months'
  | 'play_until_end'
  | 'play_playoffs'
  | 'start_next_season'
  | 'member_night_single'
  | 'member_night_play_all';

/** The wait between a Job status answer (or a failed poll) and the next poll (Tournaments'). */
const POLL_MS = 1000;

/** A failed Job that did not say why (I3). */
const JOB_FAILED_MESSAGE = 'Play job failed.';

/** The `field` prefix of a 409 `play_in_progress`: `job:<id>` names the live run. */
const JOB_FIELD_PREFIX = 'job:';

type SyncAction = 'start_season' | 'play_week' | 'play_single_round';
type BackgroundAction =
  'play_two_months' | 'play_until_end' | 'play_playoffs' | 'member_night_play_all';

/**
 * The Play menu's seam (spec #454): the only code that calls the Season play endpoints. It holds
 * the watched League's play controls, runs one action at a time (I1), polls a background run's
 * Job, and ticks `changed` after every success and every finished Job (I2), which reloads the
 * open League screen (`LeagueLayout`). Every action targets the Displayed Season (I4).
 */
@Injectable({ providedIn: 'root' })
export class PlayRun {
  private readonly leagues = inject(LeaguesService);
  private readonly seasons = inject(SeasonsService);
  private readonly router = inject(Router);
  private readonly notify = injectNotify();

  /** The watched League's controls; null while loading or with no League watched. */
  private readonly _controls = signal<PlayControls | null>(null);
  /** I1: a play request is in flight. */
  private readonly _busy = signal(false);
  /** The polled Job's count; non-null exactly while a Job is polled. */
  private readonly _progress = signal<{ completed: number; total: number } | null>(null);
  /** Stop was asked for the polled Job. */
  private readonly _stopRequested = signal(false);
  /** I3: the last failed action or Job's message; `''` = none. */
  private readonly _error = signal('');
  /** I2: ticks after every successful action and every finished Job. */
  private readonly _changed = signal(0);

  readonly controls = this._controls.asReadonly();
  readonly busy = this._busy.asReadonly();
  readonly progress = this._progress.asReadonly();
  readonly stopRequested = this._stopRequested.asReadonly();
  readonly error = this._error.asReadonly();
  readonly changed = this._changed.asReadonly();

  private leagueId: number | null = null;
  private loading: Subscription | null = null;
  private action: Subscription | null = null;
  private poll: Subscription | null = null;
  /** The polled Job and the Season it belongs to. */
  private job: { seasonId: number; jobId: string } | null = null;
  /** Jobs seen to end, so a stale `active_play_job_id` is not polled again. */
  private readonly finished = new Set<string>();

  /** Watches `leagueId`'s controls (null: none), resuming its Active play run's Job. */
  watch(leagueId: number | null): void {
    if (leagueId === this.leagueId) return;
    this.leagueId = leagueId;
    this.loading?.unsubscribe();
    this.action?.unsubscribe();
    this.stopPolling();
    this._busy.set(false);
    this._error.set('');
    this._controls.set(null);
    if (leagueId !== null) this.loadControls();
  }

  run(action: PlayAction): void {
    const target = this.target();
    if (target === null) return;
    const { leagueId, seasonId } = target;
    switch (action) {
      case 'start_season':
      case 'play_week':
      case 'play_single_round':
        this.runSync(action, leagueId, seasonId);
        return;
      case 'play_two_months':
      case 'play_until_end':
      case 'play_playoffs':
      case 'member_night_play_all':
        this.runBackground(action, seasonId);
        return;
      case 'start_next_season':
        this.startNextSeason(leagueId);
        return;
      case 'member_night_single':
        this.playMemberNightSingle(leagueId, seasonId);
        return;
    }
  }

  /**
   * Reads the night's game counts (T total, U unplayed), plays one game, then says how far the
   * night is (Django `play_member_night_single`): P = T - U + 1 games done.
   */
  private playMemberNightSingle(leagueId: number, seasonId: number): void {
    const played = this.seasons
      .memberNight(seasonId)
      .pipe(
        switchMap((state) => this.seasons.memberNightPlaySingle(seasonId).pipe(map(() => state))),
      );
    this.send(played, (state) => {
      this.notify(memberNightPlayedMessage(state));
      this.landThenRefresh(['/leagues', leagueId, 'seasons', seasonId]);
    });
  }

  /** Draws member-night games for `sites`, then lands on the Season dashboard. */
  setUpMemberNight(sites: string[]): void {
    const target = this.target();
    if (target === null) return;
    const { leagueId, seasonId } = target;
    this.send(this.seasons.setUpMemberNight(seasonId, sites), (dashboard) => {
      this.notify(memberNightSetUpMessage(dashboard, sites.length));
      this.landThenRefresh(['/leagues', leagueId, 'seasons', seasonId]);
    });
  }

  /** Asks the polled Job to stop after its current game, and keeps polling until it ends. */
  stop(): void {
    const job = this.job;
    if (job === null || this.stopRequested()) return;
    this._stopRequested.set(true);
    this.seasons.playCancel(job.seasonId).subscribe({
      error: (failure: unknown) => {
        this._stopRequested.set(false);
        this._error.set(apiMessage(failure, GENERIC_ERROR_MESSAGE));
      },
    });
  }

  /** I1 + I4: the Displayed Season to act on, or null while another action holds the menu. */
  private target(): { leagueId: number; seasonId: number } | null {
    const season = this.controls()?.displayed_season;
    if (this.leagueId === null || !season || this.busy() || this.progress() !== null) return null;
    return { leagueId: this.leagueId, seasonId: season.id };
  }

  private runSync(action: SyncAction, leagueId: number, seasonId: number): void {
    const requests: Record<SyncAction, () => Observable<SeasonDashboard>> = {
      start_season: () => this.seasons.start(seasonId),
      play_week: () => this.seasons.playWeek(seasonId),
      play_single_round: () => this.seasons.playSingleRound(seasonId),
    };
    this.send(requests[action](), (dashboard) => {
      if (action === 'start_season') this.notify(seasonStartedMessage(dashboard));
      this.landThenRefresh(['/leagues', leagueId, 'seasons', seasonId]);
    });
  }

  private runBackground(action: BackgroundAction, seasonId: number): void {
    const requests: Record<BackgroundAction, () => Observable<SeasonPlayJobRef>> = {
      play_two_months: () => this.seasons.playTwoMonths(seasonId),
      play_until_end: () => this.seasons.playUntilEnd(seasonId),
      play_playoffs: () => this.seasons.playPlayoffs(seasonId),
      member_night_play_all: () => this.seasons.memberNightPlayAll(seasonId),
    };
    this.send(requests[action](), (ref) => this.startPolling(seasonId, ref.job_id));
  }

  /** Django's `next_season` redirect: a draft goes to its Conferences, else its dashboard. */
  private startNextSeason(leagueId: number): void {
    this.send(this.leagues.nextSeason(leagueId), (next) => {
      const base = ['/leagues', leagueId, 'seasons', next.id];
      this.landThenRefresh(next.state === 'draft' ? [...base, 'conferences'] : base);
    });
  }

  /**
   * Sends one play request (I1). A 409 `play_in_progress` polls the run it names; a 409
   * `manager_fired` goes to the new-team picker; anything else shows its message (I3).
   */
  private send<T>(request: Observable<T>, done: (result: T) => void): void {
    const target = this.target();
    if (target === null) return;
    this._busy.set(true);
    this._error.set('');
    this.action = request.subscribe({
      next: (result) => {
        this._busy.set(false);
        done(result);
      },
      error: (failure: unknown) => {
        this._busy.set(false);
        const liveJob = liveJobId(failure);
        if (liveJob !== null) {
          this.startPolling(target.seasonId, liveJob);
        } else if (errorCode(failure) === 'manager_fired') {
          void this.router.navigate(['/leagues', target.leagueId, 'new-team']);
        } else {
          this._error.set(apiMessage(failure, GENERIC_ERROR_MESSAGE));
        }
      },
    });
  }

  private landThenRefresh(commands: (string | number)[]): void {
    void this.router.navigate(commands).then(() => this.refresh());
  }

  /** I2: re-fetch the controls and tick `changed`. */
  private refresh(): void {
    this._changed.update((n) => n + 1);
    this.loadControls();
  }

  private loadControls(): void {
    const leagueId = this.leagueId;
    if (leagueId === null) return;
    this.loading?.unsubscribe();
    this.loading = this.leagues.playControls(leagueId).subscribe({
      next: (controls) => {
        this._controls.set(controls);
        const jobId = controls.active_play_job_id;
        const season = controls.displayed_season;
        if (jobId && season && !this.finished.has(jobId)) {
          this.startPolling(season.id, jobId);
        }
      },
      // The menu shows its disabled "Play" until a later load answers.
      error: () => this._controls.set(null),
    });
  }

  private startPolling(seasonId: number, jobId: string): void {
    this.stopPolling();
    this.job = { seasonId, jobId };
    this._progress.set({ completed: 0, total: 0 });
    this.pollStatus();
  }

  /** Asks for the Job's status; a `running` answer, or a failed request, asks again later. */
  private pollStatus(): void {
    const job = this.job;
    if (job === null) return;
    this.poll = this.seasons.playJob(job.seasonId, job.jobId).subscribe({
      next: (status) => this.onStatus(status),
      // A network blip is not an error (I3): the run goes on, so ask again.
      error: () => this.schedulePoll(),
    });
  }

  private onStatus(status: SeasonPlayJobStatus): void {
    if (status.status === 'running') {
      this._progress.set({ completed: status.completed, total: status.total });
      this.schedulePoll();
      return;
    }
    if (this.job !== null) this.finished.add(this.job.jobId);
    this.stopPolling();
    if (status.status === 'error') this._error.set(status.error ?? JOB_FAILED_MESSAGE);
    this.refresh();
  }

  private schedulePoll(): void {
    this.poll = timer(POLL_MS).subscribe(() => this.pollStatus());
  }

  private stopPolling(): void {
    this.poll?.unsubscribe();
    this.poll = null;
    this.job = null;
    this._progress.set(null);
    this._stopRequested.set(false);
  }
}

/** Django `member_night_setup`: T from the dashboard's progress (else 0), K = Sites sent. */
function memberNightSetUpMessage(dashboard: SeasonDashboard, siteCount: number): string {
  const games = dashboard.member_night_progress?.total ?? 0;
  const sites = siteCount === 1 ? 'Site' : 'Sites';
  return `Member night set up: ${games} games drawn across ${siteCount} ${sites}.`;
}

/** Django `play_member_night_single`, from the counts read before the game was played. */
function memberNightPlayedMessage(state: MemberNightState): string {
  const total = state.games_total;
  const done = total - state.games_unplayed + 1;
  return done < total
    ? `Member-night game played: ${done} / ${total} games done.`
    : `Member night complete: all ${total} games played.`;
}

function errorCode(failure: unknown): string | undefined {
  return failure instanceof ApiError ? failure.errors[0]?.code : undefined;
}

/** The live run's job id from a 409 `play_in_progress` (`field: "job:<id>"`), else null. */
function liveJobId(failure: unknown): string | null {
  if (!(failure instanceof ApiError) || failure.status !== 409) return null;
  const field = failure.errors.find((e) => e.code === 'play_in_progress')?.field;
  return field?.startsWith(JOB_FIELD_PREFIX) ? field.slice(JOB_FIELD_PREFIX.length) : null;
}
