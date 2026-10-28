import { Component, inject, signal } from '@angular/core';
import { FormControl, NonNullableFormBuilder, ReactiveFormsModule } from '@angular/forms';
import { MatButton } from '@angular/material/button';
import { MatCheckbox } from '@angular/material/checkbox';
import { MatError, MatFormField, MatHint, MatLabel } from '@angular/material/form-field';
import { MatInput } from '@angular/material/input';
import { MatOption, MatSelect } from '@angular/material/select';
import { Router, RouterLink } from '@angular/router';
import {
  Difficulty,
  LEAGUE_SPREAD_LABELS,
  LeagueAdvancedCreateInput,
  LeagueSpread,
  MAP_MODE_LABELS,
  MapMode,
} from '../../api/leagues.models';
import { LeaguesService } from '../../api/leagues.service';
import { listAll } from '../../api/list-all';
import { ArenaMap } from '../../api/maps.models';
import { MapsService } from '../../api/maps.service';
import { apiMessage } from '../../shared/api-message';
import {
  GENERIC_ERROR_MESSAGE,
  REQUIRED_MESSAGE,
  applyApiErrors,
  clearServerErrors,
} from '../../shared/form-errors';
import { Choice, DIFFICULTY_CHOICES, DIFFICULTY_HELP } from '../league-create/difficulty';
import { MapRotationComposer, rotationWire } from './map-rotation-composer/map-rotation-composer';
import { PhaseComposer } from './phase-composer/phase-composer';
import { PhaseRow, newPhaseRow, phaseWire } from './phase-wire';

type TeamCount = LeagueAdvancedCreateInput['num_teams'];
type ConferenceCount = NonNullable<LeagueAdvancedCreateInput['number_of_conferences']>;

/** `CreateLeagueForm.NUM_TEAMS_CHOICES`. */
const NUM_TEAMS_CHOICES: readonly Choice<TeamCount>[] = ([4, 8, 12, 16] as const).map((value) => ({
  value,
  label: String(value),
}));

/** `number_of_conferences`'s choices. */
const CONFERENCE_CHOICES: readonly Choice<ConferenceCount>[] = [
  { value: 0, label: 'None (single league)' },
  { value: 2, label: '2' },
  { value: 3, label: '3' },
  { value: 4, label: '4' },
];

/** `LEAGUE_SPREAD_CHOICES`, in select order. */
const SPREAD_CHOICES: readonly Choice<LeagueSpread>[] = (['even', 'tiered', 'steep'] as const).map(
  (value) => ({ value, label: LEAGUE_SPREAD_LABELS[value] }),
);

/** `Season.MAP_MODE_CHOICES`, in select order. */
const MAP_MODE_CHOICES: readonly Choice<MapMode>[] = (
  ['none', 'single', 'random_per_round', 'rotate_by_matchday', 'rotate_by_conference'] as const
).map((value) => ({ value, label: MAP_MODE_LABELS[value] }));

/** `CreateLeagueForm`'s bounds, also shown in the client-side messages. */
const MEAN_MIN = 0;
const MEAN_MAX = 100;
const STD_DEV_MIN = 1;
const STD_DEV_MAX = 40;

/** Today as `YYYY-MM-DD` in local time (`timezone.localdate`, the Form's `start_date` default). */
function today(): string {
  const now = new Date();
  const pad = (n: number): string => String(n).padStart(2, '0');
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

/**
 * `/leagues/new/advanced`: every `CreateLeagueForm` field but the dormant `visibility` and the
 * locked `schedule_format`, porting `create_advanced.html`. The API injects no defaults on this
 * path, so the body carries every field. `phases` and `map_rotation` are controls with no input:
 * the composers' rows are serialised into them on submit, so their server errors land there.
 */
@Component({
  selector: 'app-league-create-advanced',
  imports: [
    ReactiveFormsModule,
    RouterLink,
    MatButton,
    MatCheckbox,
    MatError,
    MatFormField,
    MatHint,
    MatInput,
    MatLabel,
    MatOption,
    MatSelect,
    MapRotationComposer,
    PhaseComposer,
  ],
  styleUrl: './league-create-advanced.scss',
  templateUrl: './league-create-advanced.html',
})
export class LeagueCreateAdvanced {
  private readonly fb = inject(NonNullableFormBuilder);
  private readonly leagues = inject(LeaguesService);
  private readonly router = inject(Router);

  protected readonly numTeamsChoices = NUM_TEAMS_CHOICES;
  protected readonly conferenceChoices = CONFERENCE_CHOICES;
  protected readonly difficultyChoices = DIFFICULTY_CHOICES;
  protected readonly difficultyHelp = DIFFICULTY_HELP;
  protected readonly spreadChoices = SPREAD_CHOICES;
  protected readonly mapModeChoices = MAP_MODE_CHOICES;
  protected readonly requiredMessage = REQUIRED_MESSAGE;
  protected readonly meanMin = MEAN_MIN;
  protected readonly meanMax = MEAN_MAX;
  protected readonly stdDevMin = STD_DEV_MIN;
  protected readonly stdDevMax = STD_DEV_MAX;

  /**
   * Control names match `CreateLeagueForm`, so server field errors map onto them; the bounds
   * (`required`, `maxlength`, `min`, `max`) sit on the template's inputs.
   */
  protected readonly form = this.fb.group({
    league_name: [''],
    manager_team_name: [''],
    difficulty: ['medium' as Difficulty],
    season_name: ['Season 1'],
    start_date: [today()],
    num_teams: [4 as TeamCount],
    number_of_conferences: [0 as ConferenceCount],
    mean: new FormControl<number | null>(50),
    std_dev: new FormControl<number | null>(15),
    league_spread: ['even' as LeagueSpread],
    phases: [''],
    map_mode: ['none' as MapMode],
    map_pool: [[] as number[]],
    map_rotation: [''],
    finance_enabled: [false],
    challenge_fired_luxury_tax: [false],
  });

  /** The phase blocks; the template seeds one round-robin block (:505-508). */
  protected readonly phaseRows = signal<PhaseRow[]>([newPhaseRow()]);
  /** The rotation's map ids in row order; no row to begin with (:588). */
  protected readonly rotationIds = signal<(number | null)[]>([]);

  /** The simulation-ready maps by name, for the pool and the rotation. */
  protected readonly maps = signal<ArenaMap[]>([]);
  protected readonly loadError = signal<string | null>(null);
  protected readonly busy = signal(false);
  protected readonly nonFieldErrors = signal<string[]>([]);

  constructor() {
    const maps = inject(MapsService);
    listAll((page) => maps.list({ ...page, simulation_ready: true, ordering: 'name' })).subscribe({
      next: (items) => this.maps.set(items),
      error: (error: unknown) => this.loadError.set(apiMessage(error, GENERIC_ERROR_MESSAGE)),
    });
  }

  protected create(): void {
    this.nonFieldErrors.set([]);
    clearServerErrors(this.form);
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }
    // The template serialises its composers on submit (:503, :586).
    this.form.patchValue({
      phases: phaseWire(this.phaseRows()),
      map_rotation: rotationWire(this.rotationIds()),
    });
    this.busy.set(true);
    // `required` / `min` / `max` passed, so mean and std_dev are numbers.
    this.leagues.create(this.form.getRawValue() as LeagueAdvancedCreateInput).subscribe({
      next: (league) => void this.router.navigate(['/leagues', league.id]),
      error: (error: unknown) => {
        this.busy.set(false);
        this.nonFieldErrors.set(applyApiErrors(this.form, error));
      },
    });
  }
}
