import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed, toSignal } from '@angular/core/rxjs-interop';
import { FormControl, NonNullableFormBuilder, ReactiveFormsModule } from '@angular/forms';
import { MatButton } from '@angular/material/button';
import { MatError, MatFormField, MatHint, MatLabel } from '@angular/material/form-field';
import { MatInput } from '@angular/material/input';
import { MatOption, MatSelect } from '@angular/material/select';
import { Router, RouterLink } from '@angular/router';
import { combineLatest, startWith } from 'rxjs';
import { listAll } from '../../api/list-all';
import { TeamListItem } from '../../api/teams.models';
import { TeamsService } from '../../api/teams.service';
import {
  RoleAssignmentMode,
  RrdeCombo,
  SeriesLength,
  TeamAssembly,
  TournamentCreateInput,
  TournamentFormat,
} from '../../api/tournaments.models';
import { TournamentsService } from '../../api/tournaments.service';
import { apiMessage } from '../../shared/api-message';
import { GENERIC_ERROR_MESSAGE, applyApiErrors } from '../../shared/form-errors';

interface Choice<T> {
  value: T;
  label: string;
}

/** `Tournament.FORMAT_CHOICES`, labelled as `tournament_create.html` labels them. */
const FORMAT_CHOICES: readonly Choice<TournamentFormat>[] = [
  { value: 'single_elimination', label: 'Single elimination' },
  { value: 'double_elimination', label: 'Double elimination' },
  { value: 'round_robin', label: 'Round robin' },
  { value: 'round_robin_double_elim', label: 'Round robin → Double elimination' },
  { value: 'swiss', label: 'Swiss' },
];

/** `Tournament.TEAM_ASSEMBLY_CHOICES`. */
const ASSEMBLY_CHOICES: readonly Choice<TeamAssembly>[] = [
  { value: 'preset', label: 'Preset teams' },
  { value: 'player_pool', label: 'Player pool draw' },
];

/** `Tournament.ROLE_ASSIGNMENT_CHOICES`. */
const ROLE_MODE_CHOICES: readonly Choice<RoleAssignmentMode>[] = [
  { value: 'random', label: 'Random per team per Round' },
  { value: 'per_tier', label: 'Per-tier bijection (both teams)' },
];

/** `RRDE_COMBOS`, in select order; the first is the Form's default. */
const RRDE_CHOICES: readonly Choice<RrdeCombo>[] = [
  { value: '4/0', label: '4 WB, 0 LB' },
  { value: '4/2', label: '4 WB, 2 LB' },
  { value: '8/0', label: '8 WB, 0 LB' },
  { value: '8/4', label: '8 WB, 4 LB' },
  { value: '16/0', label: '16 WB, 0 LB' },
  { value: '16/8', label: '16 WB, 8 LB' },
];

/** `_SERIES_LENGTH_CHOICES`. */
const SERIES_CHOICES: readonly Choice<SeriesLength>[] = [
  { value: 1, label: 'Best of 1' },
  { value: 3, label: 'Best of 3' },
  { value: 5, label: 'Best of 5' },
];

type SeriesField =
  | 'final_series_length'
  | 'semifinal_series_length'
  | 'quarterfinal_series_length'
  | 'earlier_series_length';

const SERIES_FIELDS: readonly Choice<SeriesField>[] = [
  { value: 'final_series_length', label: 'Final series length' },
  { value: 'semifinal_series_length', label: 'Semifinal series length' },
  { value: 'quarterfinal_series_length', label: 'Quarterfinal series length' },
  { value: 'earlier_series_length', label: 'Earlier rounds series length' },
];

/** `TournamentCreateForm.generate_ppt`'s `min_value` (a full 6-player Roster). */
const MIN_GENERATED_PLAYERS = 6;

/**
 * `/tournaments/new`: the Create Tournament form, porting `tournament_create.html` and
 * `TournamentCreateForm`. A hidden field's control is disabled, so it neither blocks the submit
 * nor goes in the body: the server then applies its default.
 */
@Component({
  selector: 'app-tournament-create',
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
  styleUrl: './tournament-create.scss',
  templateUrl: './tournament-create.html',
})
export class TournamentCreate {
  private readonly fb = inject(NonNullableFormBuilder);
  private readonly tournaments = inject(TournamentsService);
  private readonly router = inject(Router);

  protected readonly formatChoices = FORMAT_CHOICES;
  protected readonly assemblyChoices = ASSEMBLY_CHOICES;
  protected readonly roleModeChoices = ROLE_MODE_CHOICES;
  protected readonly rrdeChoices = RRDE_CHOICES;
  protected readonly seriesChoices = SERIES_CHOICES;
  protected readonly seriesFields = SERIES_FIELDS;
  protected readonly minGeneratedPlayers = MIN_GENERATED_PLAYERS;

  /**
   * Control names match `TournamentCreateForm`, so server field errors map onto them. The
   * Django bounds (`required`, `maxlength`, `min`) sit on the template's inputs, whose attribute
   * validators also check them here.
   */
  protected readonly form = this.fb.group({
    name: [''],
    teams: [[] as number[]],
    generate_count: new FormControl<number | null>(0),
    generate_ppt: new FormControl<number | null>(MIN_GENERATED_PLAYERS),
    team_assembly: ['preset' as TeamAssembly],
    role_assignment_mode: ['random' as RoleAssignmentMode],
    format: ['single_elimination' as TournamentFormat],
    swiss_rounds: new FormControl<number | null>(0),
    rrde_combo: ['4/0' as RrdeCombo],
    final_series_length: [1 as SeriesLength],
    semifinal_series_length: [1 as SeriesLength],
    quarterfinal_series_length: [1 as SeriesLength],
    earlier_series_length: [1 as SeriesLength],
  });

  private readonly format = toSignal(this.form.controls.format.valueChanges, {
    initialValue: this.form.controls.format.value,
  });
  private readonly assembly = toSignal(this.form.controls.team_assembly.valueChanges, {
    initialValue: this.form.controls.team_assembly.value,
  });

  /** The show / hide rules of the template's `tournamentCreateToggle` / `...AssemblyToggle`. */
  protected readonly showSwissRounds = computed(() => this.format() === 'swiss');
  protected readonly showRrdeCombo = computed(() => this.format() === 'round_robin_double_elim');
  protected readonly showSeries = computed(
    () => this.format() !== 'round_robin' && this.format() !== 'swiss',
  );
  protected readonly playerPool = computed(() => this.assembly() === 'player_pool');

  /** The valid-roster Teams by name; null until loaded. */
  protected readonly teams = signal<TeamListItem[] | null>(null);
  protected readonly loadError = signal<string | null>(null);
  protected readonly busy = signal(false);
  /** Errors no control owns, shown in the alert above the form. */
  protected readonly nonFieldErrors = signal<string[]>([]);

  constructor() {
    const teams = inject(TeamsService);
    listAll((page) => teams.list({ ...page, ordering: 'name' })).subscribe({
      next: (items) => this.teams.set(items.filter((team) => team.is_valid_roster)),
      error: (error: unknown) => this.loadError.set(apiMessage(error, GENERIC_ERROR_MESSAGE)),
    });

    // Picking a Player-pool draw preselects RR -> DE (a Random Draw), as the template does.
    this.form.controls.team_assembly.valueChanges
      .pipe(takeUntilDestroyed())
      .subscribe((assembly) => {
        if (assembly === 'player_pool') {
          this.form.controls.format.setValue('round_robin_double_elim');
        }
      });

    // A hidden control is disabled: it is left out of `form.value` and of the validity check.
    const controls = this.form.controls;
    combineLatest([
      controls.format.valueChanges.pipe(startWith(controls.format.value)),
      controls.team_assembly.valueChanges.pipe(startWith(controls.team_assembly.value)),
    ])
      .pipe(takeUntilDestroyed())
      .subscribe(([format, assembly]) => {
        const preset = assembly === 'preset';
        const series = format !== 'round_robin' && format !== 'swiss';
        this.toggle('teams', preset);
        this.toggle('generate_count', preset);
        this.toggle('generate_ppt', preset);
        this.toggle('role_assignment_mode', !preset);
        this.toggle('swiss_rounds', format === 'swiss');
        this.toggle('rrde_combo', format === 'round_robin_double_elim');
        for (const field of SERIES_FIELDS) this.toggle(field.value, series);
      });
  }

  protected create(): void {
    this.nonFieldErrors.set([]);
    // A control's `server` error only clears on a value change, so drop it here too.
    for (const control of Object.values(this.form.controls)) {
      if (control.hasError('server')) control.updateValueAndValidity();
    }
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }
    this.busy.set(true);
    // `form.value` leaves out the disabled (hidden) controls.
    this.tournaments.create(this.form.value as TournamentCreateInput).subscribe({
      next: (tournament) => void this.router.navigate(['/tournaments', tournament.id]),
      error: (error: unknown) => {
        this.busy.set(false);
        this.nonFieldErrors.set(applyApiErrors(this.form, error));
      },
    });
  }

  private toggle(name: keyof typeof this.form.controls, shown: boolean): void {
    const control = this.form.controls[name];
    if (shown && control.disabled) control.enable({ emitEvent: false });
    if (!shown && control.enabled) control.disable({ emitEvent: false });
  }
}
