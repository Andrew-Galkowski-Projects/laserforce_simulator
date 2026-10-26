import { DecimalPipe } from '@angular/common';
import { Component, DestroyRef, computed, inject, signal } from '@angular/core';
import {
  FormControl,
  NonNullableFormBuilder,
  ReactiveFormsModule,
  Validators,
} from '@angular/forms';
import { MatAnchor, MatButton } from '@angular/material/button';
import { MatError, MatFormField, MatLabel } from '@angular/material/form-field';
import { MatProgressBar } from '@angular/material/progress-bar';
import { MatOption, MatSelect } from '@angular/material/select';
import { RouterLink } from '@angular/router';
import { Subscription, timer } from 'rxjs';
import { BatchAggregate, BatchRunStarted, BatchRunStatus } from '../../api/batch.models';
import { BatchService } from '../../api/batch.service';
import { listAll } from '../../api/list-all';
import { ArenaMap } from '../../api/maps.models';
import { MapsService } from '../../api/maps.service';
import { TeamListItem } from '../../api/teams.models';
import { TeamsService } from '../../api/teams.service';
import { apiMessage } from '../../shared/api-message';
import { GENERIC_ERROR_MESSAGE, applyApiErrors } from '../../shared/form-errors';
import { sideTag } from '../shared/side-tag';
import { scoreHistogram } from './score-histogram';
import { ScoreHistogramChart } from './score-histogram-chart';

/** Django `BatchSimulateForm.N_CHOICES`. */
const N_CHOICES: readonly number[] = [10, 50, 100, 500];

/** The wait between a status answer and the next poll (SIM-10's cadence). */
const POLL_MS = 500;

/** The wait between a save status answer and the next poll. */
const SAVE_POLL_MS = 1500;

/** How many games one save replays (the API takes 1 to 10). */
const SAVE_CHOICES: readonly { value: number; label: string }[] = [
  { value: 1, label: '1 game' },
  { value: 5, label: '5 games' },
  { value: 10, label: '10 games' },
];

type RunPhase = 'idle' | 'running' | 'complete' | 'error';

/** A save's progress (`info`) or outcome line. */
interface SaveMessage {
  tone: 'info' | 'success' | 'error';
  text: string;
}

/** Django's wording for a missing required field, as the API sends it for the other forms. */
const REQUIRED_MESSAGE = 'This field is required.';

/**
 * `/matches/batch`: a Batch run of 10 to 500 in-memory games between two Teams, polled live, with
 * its Average and Highlight games savable as Rounds. Ports `batch_simulate.html`.
 */
@Component({
  selector: 'app-batch-simulate',
  imports: [
    DecimalPipe,
    ReactiveFormsModule,
    RouterLink,
    MatAnchor,
    MatButton,
    MatError,
    MatFormField,
    MatLabel,
    MatOption,
    MatProgressBar,
    MatSelect,
    ScoreHistogramChart,
  ],
  styleUrl: './batch-simulate.scss',
  templateUrl: './batch-simulate.html',
})
export class BatchSimulate {
  private readonly fb = inject(NonNullableFormBuilder);
  private readonly batch = inject(BatchService);

  /** The Account's Teams; null until loaded. */
  protected readonly teams = signal<TeamListItem[] | null>(null);
  protected readonly maps = signal<ArenaMap[]>([]);
  /** Loaded, but no Team can play: the form gives way to a link to Teams. */
  protected readonly noValidTeams = computed(() => {
    const teams = this.teams();
    return teams !== null && !teams.some((team) => team.is_valid_roster);
  });

  /** Control names match the API's fields, so field errors map onto them. */
  protected readonly form = this.fb.group({
    // `required` as on Django's `BatchSimulateForm`; cross-field rules stay server-side.
    team_red: new FormControl<number | null>(null, Validators.required),
    team_blue: new FormControl<number | null>(null, Validators.required),
    /** null is the 3-zone fallback. */
    arena_map: new FormControl<number | null>(null),
    n: [100],
  });

  protected readonly nChoices = N_CHOICES;
  protected readonly requiredMessage = REQUIRED_MESSAGE;
  /** Errors no control owns (`field: null`, e.g. a `MatchSetupError`), shown above the form. */
  protected readonly nonFieldErrors = signal<string[]>([]);

  /** Where the current run is; `idle` before the first run and after a refused start. */
  protected readonly phase = signal<RunPhase>('idle');
  /** A run is being started or polled: Run is disabled. */
  protected readonly running = computed(() => this.phase() === 'running');
  /** The run in progress or last finished; null before the first. */
  protected readonly run = signal<BatchRunStarted | null>(null);
  /** The last poll's game count; null until the first poll answers. */
  protected readonly progress = signal<{ completed: number; total: number } | null>(null);
  /** The latest aggregate (partial while running); null until the job first reports one. */
  protected readonly aggregate = signal<BatchAggregate | null>(null);
  /** Why the run stopped: the job's error, or a failed request's message. */
  protected readonly runError = signal<string | null>(null);
  /** Seconds from Run to `complete`, measured here. */
  protected readonly elapsed = signal<number | null>(null);

  protected readonly progressLabel = computed(() => {
    const elapsed = this.elapsed();
    if (elapsed !== null) return `Complete in ${elapsed.toFixed(1)}s`;
    const progress = this.progress();
    if (progress === null) return 'Starting…';
    return `Simulating game ${progress.completed} of ${progress.total}…`;
  });

  /** The progress bar's fill, 0 to 100. */
  protected readonly progressValue = computed(() => {
    if (this.phase() === 'complete') return 100;
    const progress = this.progress();
    return progress && progress.total > 0 ? (progress.completed / progress.total) * 100 : 0;
  });

  protected readonly histogram = computed(() => {
    const agg = this.aggregate();
    return agg ? scoreHistogram(agg.red_scores, agg.blue_scores) : null;
  });

  protected readonly sideTag = sideTag;

  /** How many Average / Highlight games each save button saves. */
  protected readonly saveCounts = this.fb.group({ avg: [1], outlier: [1] });
  protected readonly saveChoices = SAVE_CHOICES;
  /** Save games is offered once the run completes with games to replay. */
  protected readonly canSave = computed(() => {
    const agg = this.aggregate();
    return (
      this.phase() === 'complete' &&
      agg !== null &&
      agg.avg_seeds.length + agg.outlier_seeds.length > 0
    );
  });
  /** A save is being started or polled: both save buttons are disabled. */
  protected readonly saving = signal(false);
  /** The save's progress or outcome line; null before the first save. */
  protected readonly saveMessage = signal<SaveMessage | null>(null);
  /** The Rounds the last save made. */
  protected readonly savedRounds = signal<number[]>([]);

  /** The pending status poll (or the wait before it). */
  private poll: Subscription | null = null;
  /** The pending save status poll (or the wait before it). */
  private savePoll: Subscription | null = null;
  /** `Date.now()` when Run was pressed. */
  private startedAt = 0;

  constructor() {
    const teams = inject(TeamsService);
    const maps = inject(MapsService);
    listAll((page) => teams.list({ ...page, ordering: 'name' })).subscribe((items) =>
      this.teams.set(items),
    );
    listAll((page) =>
      maps.list({ ...page, has_confirmed_config: true, ordering: 'name' }),
    ).subscribe((items) => this.maps.set(items));
    inject(DestroyRef).onDestroy(() => this.stopPolling());
  }

  /** Starts a Batch run. Only `required` is checked here; the matchup rules are the server's. */
  protected start(): void {
    this.nonFieldErrors.set([]);
    // A control's `server` error only clears on a value change, so drop it here too.
    for (const control of Object.values(this.form.controls)) {
      if (control.hasError('server')) control.updateValueAndValidity();
    }
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }
    const { team_red, team_blue, arena_map, n } = this.form.getRawValue();
    this.clearRun();
    this.phase.set('running');
    this.startedAt = Date.now();
    // `required` passed, so both sides are picked.
    this.batch.start({ team_red: team_red!, team_blue: team_blue!, arena_map, n }).subscribe({
      next: (run) => {
        this.run.set(run);
        this.pollStatus(run.job_id);
      },
      error: (error: unknown) => {
        this.phase.set('idle');
        this.nonFieldErrors.set(applyApiErrors(this.form, error));
      },
    });
  }

  /** Saves the picked number of Average (`avg`) or Highlight (`outlier`) games of the run. */
  protected save(gameType: 'avg' | 'outlier'): void {
    const run = this.run();
    if (!run) return;
    const n = this.saveCounts.controls[gameType].value;
    this.saving.set(true);
    this.savedRounds.set([]);
    this.saveMessage.set({ tone: 'info', text: 'Starting save…' });
    this.savePoll = this.batch
      .saveGames(run.job_id, {
        team_red: run.team_red_id,
        team_blue: run.team_blue_id,
        arena_map: run.arena_map_id,
        game_type: gameType,
        n,
      })
      .subscribe({
        next: ({ job_id }) => {
          this.saveMessage.set({ tone: 'info', text: `Saving ${n} game(s)…` });
          this.pollSave(job_id);
        },
        error: (error: unknown) => this.saveFailed(apiMessage(error, GENERIC_ERROR_MESSAGE)),
      });
  }

  protected teamLabel(team: TeamListItem): string {
    return team.is_valid_roster ? team.name : `${team.name} (invalid roster)`;
  }

  /** Asks for the job's status; a `running` answer asks again `POLL_MS` later. */
  private pollStatus(jobId: string): void {
    this.poll = this.batch.status(jobId).subscribe({
      next: (status) => this.onStatus(jobId, status),
      error: (error: unknown) => this.fail(apiMessage(error, GENERIC_ERROR_MESSAGE)),
    });
  }

  private onStatus(jobId: string, status: BatchRunStatus): void {
    if (status.partial) this.aggregate.set(status.partial);
    if (status.status === 'error') {
      this.fail(status.error ?? 'Unknown error.');
      return;
    }
    // A pending job reports `total: 0` until it starts: count against the run's size.
    const total = status.total || (this.run()?.n ?? 0);
    this.progress.set({ completed: status.completed, total });
    if (status.status === 'complete') {
      this.elapsed.set((Date.now() - this.startedAt) / 1000);
      this.phase.set('complete');
      this.poll = null;
      return;
    }
    this.poll = timer(POLL_MS).subscribe(() => this.pollStatus(jobId));
  }

  private fail(message: string): void {
    this.runError.set(message);
    this.phase.set('error');
    this.poll = null;
  }

  /** Asks for the save's status; a `running` answer asks again `SAVE_POLL_MS` later. */
  private pollSave(saveJobId: string): void {
    this.savePoll = this.batch.saveStatus(saveJobId).subscribe({
      next: (status) => {
        if (status.status === 'complete') {
          this.saving.set(false);
          this.savedRounds.set(status.round_ids);
          this.saveMessage.set({
            tone: 'success',
            text: `Saved ${status.round_ids.length} game(s).`,
          });
          this.savePoll = null;
        } else if (status.status === 'error') {
          this.saveFailed(`Save failed: ${status.error ?? 'unknown error'}`);
        } else {
          this.savePoll = timer(SAVE_POLL_MS).subscribe(() => this.pollSave(saveJobId));
        }
      },
      error: (error: unknown) => this.saveFailed(apiMessage(error, GENERIC_ERROR_MESSAGE)),
    });
  }

  private saveFailed(message: string): void {
    this.saving.set(false);
    this.saveMessage.set({ tone: 'error', text: message });
    this.savePoll = null;
  }

  /** Forgets the last run's results and saves before a new one starts. */
  private clearRun(): void {
    this.stopPolling();
    this.run.set(null);
    this.progress.set(null);
    this.aggregate.set(null);
    this.runError.set(null);
    this.elapsed.set(null);
    this.saving.set(false);
    this.saveMessage.set(null);
    this.savedRounds.set([]);
  }

  private stopPolling(): void {
    this.poll?.unsubscribe();
    this.poll = null;
    this.savePoll?.unsubscribe();
    this.savePoll = null;
  }
}
