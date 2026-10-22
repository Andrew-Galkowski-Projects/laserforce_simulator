import { Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed, toSignal } from '@angular/core/rxjs-interop';
import { NonNullableFormBuilder, ReactiveFormsModule } from '@angular/forms';
import { MatButton } from '@angular/material/button';
import { MatError, MatFormField, MatHint, MatLabel } from '@angular/material/form-field';
import { MatInput } from '@angular/material/input';
import { MatOption, MatSelect } from '@angular/material/select';
import { RouterLink } from '@angular/router';
import { distinctUntilChanged, map, skip, startWith } from 'rxjs';
import { GenerationResult } from '../../api/teams.models';
import { TeamsService } from '../../api/teams.service';
import { applyApiErrors } from '../shared/form-errors';

interface Choice {
  value: string;
  label: string;
}

const range = (from: number, to: number): Choice[] =>
  Array.from({ length: to - from + 1 }, (_, i) => ({
    value: String(from + i),
    label: String(from + i),
  }));

/** `GenerateLeagueForm.NUM_TEAMS_CHOICES`: 0, 2..20 (no 1) and Random. */
const NUM_TEAMS_CHOICES: readonly Choice[] = [
  { value: '0', label: '0 (free-agent pool)' },
  ...range(2, 20),
  { value: 'random_2_10', label: 'Random (2–10)' },
];

/** `GenerateLeagueForm.TEAM_MODE_PPT_CHOICES`. */
const TEAM_PLAYERS_CHOICES: readonly Choice[] = [
  ...range(6, 9),
  { value: 'random_team', label: 'Random (6–8)' },
];

/** `GenerateLeagueForm.POOL_MODE_PPT_CHOICES`. */
const POOL_PLAYERS_CHOICES: readonly Choice[] = [
  ...range(12, 100),
  { value: 'random_pool', label: 'Random (12–100)' },
];

/** `/teams/generate`: LG-00 generation, the Angular home of Django's Generate Players page. */
@Component({
  selector: 'app-team-generate',
  imports: [
    ReactiveFormsModule,
    RouterLink,
    MatButton,
    MatError,
    MatFormField,
    MatHint,
    MatInput,
    MatLabel,
    MatOption,
    MatSelect,
  ],
  styleUrls: ['../shared/team-page.scss', './team-generate.scss'],
  templateUrl: './team-generate.html',
})
export class TeamGenerate {
  private readonly fb = inject(NonNullableFormBuilder);
  private readonly teams = inject(TeamsService);

  protected readonly numTeamsChoices = NUM_TEAMS_CHOICES;
  protected readonly teamPlayersChoices = TEAM_PLAYERS_CHOICES;
  protected readonly poolPlayersChoices = POOL_PLAYERS_CHOICES;

  /** Control names match `GenerateLeagueForm`, so server field errors map onto them. */
  protected readonly form = this.fb.group({
    num_teams: ['random_2_10'],
    players_per_team: ['random_team'],
    mean: [50],
    std_dev: [15],
  });

  private readonly numTeams = toSignal(this.form.controls.num_teams.valueChanges, {
    initialValue: this.form.controls.num_teams.value,
  });
  /** Pool mode (`num_teams === '0'`) offers 12..100 free agents instead of 6..9 per Team. */
  protected readonly poolMode = computed(() => this.numTeams() === '0');
  protected readonly busy = signal(false);
  protected readonly nonFieldErrors = signal<string[]>([]);
  /** The last run's outcome; while set, the result panel replaces the form. */
  protected readonly result = signal<GenerationResult | null>(null);

  constructor() {
    // A mode change resets the players control to that mode's Random; a change of count
    // within the same mode leaves it alone.
    this.form.controls.num_teams.valueChanges
      .pipe(
        startWith(this.form.controls.num_teams.value),
        map((value) => value === '0'),
        distinctUntilChanged(),
        skip(1),
        takeUntilDestroyed(inject(DestroyRef)),
      )
      .subscribe((pool) =>
        this.form.controls.players_per_team.setValue(pool ? 'random_pool' : 'random_team'),
      );
  }

  protected generate(): void {
    this.busy.set(true);
    this.nonFieldErrors.set([]);
    // A control's `server` error only clears on a value change, so drop it here too.
    for (const control of Object.values(this.form.controls)) {
      if (control.hasError('server')) control.setErrors(null);
    }
    this.teams.generate(this.form.getRawValue()).subscribe({
      next: (result) => {
        this.busy.set(false);
        this.result.set(result);
      },
      error: (error: unknown) => {
        this.busy.set(false);
        this.nonFieldErrors.set(applyApiErrors(this.form, error));
      },
    });
  }

  /** Back to the form with the last run's values untouched. */
  protected generateMore(): void {
    this.result.set(null);
  }
}
