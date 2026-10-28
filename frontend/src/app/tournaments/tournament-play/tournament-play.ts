import { Component, DestroyRef, computed, inject, input, output, signal } from '@angular/core';
import { MatButton } from '@angular/material/button';
import { MatProgressBar } from '@angular/material/progress-bar';
import { Subscription, timer } from 'rxjs';
import { ApiError } from '../../api/api-client';
import { PlayJobStatus, TournamentDetail } from '../../api/tournaments.models';
import { TournamentsService } from '../../api/tournaments.service';
import { apiMessage } from '../../shared/api-message';
import { GENERIC_ERROR_MESSAGE } from '../../shared/form-errors';
import { injectNotify } from '../../shared/notify';
import { playedMessage } from './played-message';

/** The wait between a status answer (or a failed poll) and the next poll: the Django page's. */
const POLL_MS = 1000;

/** A job that failed without saying why. */
const JOB_FAILED_MESSAGE = 'The Play All job failed. See the server logs.';

/** The `field` prefix of a 409 `play_in_progress`: `job:<id>` names the live run. */
const JOB_FIELD_PREFIX = 'job:';

/**
 * The Tournament detail's play section (ANG-06-13b-2): Play Next Match, and Play All run in the
 * background and polled live. Shown only while the Tournament is active and not in a League.
 * After a Match is played, or a run completes, it emits `changed` so the host reloads the detail.
 */
@Component({
  selector: 'app-tournament-play',
  imports: [MatButton, MatProgressBar],
  templateUrl: './tournament-play.html',
  styleUrl: './tournament-play.scss',
})
export class TournamentPlay {
  private readonly tournaments = inject(TournamentsService);
  private readonly notify = injectNotify();

  readonly detail = input.required<TournamentDetail>();
  /** Emitted after every Match played, so the host reloads the detail. */
  readonly changed = output<void>();

  /** Play controls: active, and not run by a League. */
  protected readonly visible = computed(
    () => this.detail().state === 'active' && !this.detail().embedded,
  );

  /** A Play Next is in flight. */
  protected readonly playingNext = signal(false);
  /** A Play All run is being started or polled. */
  protected readonly running = signal(false);
  /** Either play is in flight: both buttons wait for it. */
  protected readonly busy = computed(() => this.playingNext() || this.running());
  /** The last status's stage count; null until the first status answers. */
  protected readonly progress = signal<{ completed: number; total: number } | null>(null);
  /** The section's error box: a refused play or a failed run. */
  protected readonly error = signal<string | null>(null);

  protected readonly progressLabel = computed(() => {
    const progress = this.progress();
    return progress === null
      ? 'Starting…'
      : `Running… stage ${progress.completed} / ${progress.total}`;
  });

  /** The bar's fill, 0 to 100. */
  protected readonly progressValue = computed(() => {
    const progress = this.progress();
    return progress && progress.total > 0 ? (progress.completed / progress.total) * 100 : 0;
  });

  /** The pending status poll (or the wait before it). */
  private poll: Subscription | null = null;

  constructor() {
    inject(DestroyRef).onDestroy(() => this.stopPolling());
  }

  protected playNext(): void {
    if (this.busy()) return;
    this.playingNext.set(true);
    this.error.set(null);
    this.tournaments.playNext(this.detail().id).subscribe({
      next: (result) => {
        this.playingNext.set(false);
        this.notify(playedMessage(result));
        this.changed.emit();
      },
      error: (error: unknown) => {
        this.playingNext.set(false);
        this.error.set(apiMessage(error, GENERIC_ERROR_MESSAGE));
      },
    });
  }

  protected playAll(): void {
    if (this.busy()) return;
    this.running.set(true);
    this.progress.set(null);
    this.error.set(null);
    this.poll = this.tournaments.playAll(this.detail().id).subscribe({
      next: ({ job_id }) => this.pollStatus(job_id),
      error: (error: unknown) => {
        // A run already going: show its progress instead of an error.
        const liveJob = liveJobId(error);
        if (liveJob !== null) {
          this.pollStatus(liveJob);
        } else {
          this.fail(apiMessage(error, GENERIC_ERROR_MESSAGE));
        }
      },
    });
  }

  /** Asks for the run's status; a `running` answer, or a failed request, asks again later. */
  private pollStatus(jobId: string): void {
    this.poll = this.tournaments.playJob(this.detail().id, jobId).subscribe({
      next: (status) => this.onStatus(jobId, status),
      // A network blip: the run goes on, so keep the progress and ask again.
      error: () => this.schedulePoll(jobId),
    });
  }

  private onStatus(jobId: string, status: PlayJobStatus): void {
    if (status.status === 'error') {
      this.fail(status.error ?? JOB_FAILED_MESSAGE);
      return;
    }
    this.progress.set({ completed: status.completed, total: status.total });
    if (status.status === 'complete') {
      this.poll = null;
      this.running.set(false);
      this.progress.set(null);
      this.notify('Play All complete.');
      this.changed.emit();
      return;
    }
    this.schedulePoll(jobId);
  }

  private schedulePoll(jobId: string): void {
    this.poll = timer(POLL_MS).subscribe(() => this.pollStatus(jobId));
  }

  private fail(message: string): void {
    this.poll = null;
    this.running.set(false);
    this.progress.set(null);
    this.error.set(message);
  }

  private stopPolling(): void {
    this.poll?.unsubscribe();
    this.poll = null;
  }
}

/** The live run's job id from a 409 `play_in_progress` (`field: "job:<id>"`), else null. */
function liveJobId(error: unknown): string | null {
  if (!(error instanceof ApiError) || error.status !== 409) return null;
  const field = error.errors.find((e) => e.code === 'play_in_progress')?.field;
  return field?.startsWith(JOB_FIELD_PREFIX) ? field.slice(JOB_FIELD_PREFIX.length) : null;
}
