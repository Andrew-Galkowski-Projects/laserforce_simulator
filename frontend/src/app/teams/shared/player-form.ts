import { Component, computed, effect, inject, input, output, signal } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import {
  FormControl,
  FormGroup,
  NonNullableFormBuilder,
  ReactiveFormsModule,
} from '@angular/forms';
import { MatButton } from '@angular/material/button';
import { MatCheckbox } from '@angular/material/checkbox';
import { MatError, MatFormField, MatLabel } from '@angular/material/form-field';
import { MatInput } from '@angular/material/input';
import { RouterLink } from '@angular/router';
import { map } from 'rxjs';
import { Player, PlayerInput, PlayerStats, Role } from '../../api/players.models';
import { applyApiErrors } from './form-errors';
import { STAT_GROUPS, STAT_KEYS, overallRating } from './player-stats';

/** The five Preferred-role checkboxes, in the order they are emitted. */
const ROLE_OPTIONS: readonly { key: Role; label: string }[] = [
  { key: 'commander', label: 'Commander' },
  { key: 'heavy', label: 'Heavy Weapons' },
  { key: 'scout', label: 'Scout' },
  { key: 'medic', label: 'Medic' },
  { key: 'ammo', label: 'Ammo' },
];

/**
 * The profile fields, in form order: the numbers (blank is `null`, bounded as the Django
 * `PlayerForm` widgets are) and the texts (blank is `''`).
 */
const PROFILE_FIELDS = [
  { key: 'age', label: 'Age', type: 'number', min: 5, max: 100 },
  { key: 'started_playing_age', label: 'Started Playing Age', type: 'number', min: 3, max: 100 },
  { key: 'total_games', label: 'Total Games', type: 'number', min: 0, max: 100000 },
  { key: 'home_site', label: 'Home Site', type: 'text' },
  { key: 'height', label: 'Height', type: 'text' },
] as const;

type ProfileField = (typeof PROFILE_FIELDS)[number];
type ProfileNumber = Extract<ProfileField, { type: 'number' }>['key'];
type ProfileText = Extract<ProfileField, { type: 'text' }>['key'];

const PROFILE_NUMBERS = PROFILE_FIELDS.flatMap((f) =>
  f.type === 'number' ? [f.key] : [],
) as readonly ProfileNumber[];
const PROFILE_TEXTS = PROFILE_FIELDS.flatMap((f) =>
  f.type === 'text' ? [f.key] : [],
) as readonly ProfileText[];

/** A new Player's Stats (the Django `player_add` initial). */
const NEW_STAT = 50;

type StatControls = Record<keyof PlayerStats, FormControl<number | null>>;
type ProfileNumberControls = Record<ProfileNumber, FormControl<number | null>>;
type ProfileTextControls = Record<ProfileText, FormControl<string>>;

/**
 * The Player form shared by Add Player and Edit Player (`PlayerForm`): name, profile,
 * Preferred roles and the 19 Stats, with presets and a live Overall rating.
 *
 * With no `initial` Player (Add), blank profile fields are left out of the emitted
 * `PlayerInput` so the server randomises them. With one (Edit), the form starts from it and
 * every field is emitted (a blank number is `null`, a blank text `''`).
 */
@Component({
  selector: 'app-player-form',
  imports: [
    ReactiveFormsModule,
    RouterLink,
    MatButton,
    MatCheckbox,
    MatError,
    MatFormField,
    MatInput,
    MatLabel,
  ],
  styleUrls: ['./team-form.scss', './player-form.scss'],
  templateUrl: './player-form.html',
})
export class PlayerForm {
  readonly submitLabel = input.required<string>();
  readonly cancelLink = input.required<string>();
  /** The Player being edited, or `null` to add a new one. */
  readonly initial = input<Player | null>(null);
  readonly busy = input(false);
  /** The failure of the last save (`null` before one or after a success). */
  readonly error = input<unknown>(null);
  readonly submitted = output<PlayerInput>();

  protected readonly statGroups = STAT_GROUPS;
  protected readonly roleOptions = ROLE_OPTIONS;
  protected readonly profileFields = PROFILE_FIELDS;

  private readonly fb = inject(NonNullableFormBuilder);
  protected readonly form = new FormGroup({
    name: this.fb.control(''),
    ...(Object.fromEntries(
      PROFILE_NUMBERS.map((k) => [k, new FormControl<number | null>(null)]),
    ) as ProfileNumberControls),
    ...(Object.fromEntries(
      PROFILE_TEXTS.map((k) => [k, this.fb.control('')]),
    ) as ProfileTextControls),
    preferred_roles: this.fb.group(
      Object.fromEntries(ROLE_OPTIONS.map((r) => [r.key, false])) as Record<Role, boolean>,
    ),
    ...(Object.fromEntries(
      STAT_KEYS.map((k) => [k, new FormControl<number | null>(NEW_STAT)]),
    ) as StatControls),
  });
  protected readonly nonFieldErrors = signal<string[]>([]);

  private readonly values = toSignal(
    this.form.valueChanges.pipe(map(() => this.form.getRawValue())),
    { initialValue: this.form.getRawValue() },
  );
  /** The live Overall rating with one decimal, or "—" while a Stat is empty or out of range. */
  protected readonly overall = computed(() => {
    const rating = overallRating(this.values());
    return rating === null ? '—' : rating.toFixed(1);
  });

  constructor() {
    effect(() => {
      const player = this.initial();
      if (player === null) return;
      this.form.setValue({
        name: player.name,
        ...(Object.fromEntries(PROFILE_NUMBERS.map((k) => [k, player[k]])) as Record<
          ProfileNumber,
          number | null
        >),
        ...(Object.fromEntries(PROFILE_TEXTS.map((k) => [k, player[k]])) as Record<
          ProfileText,
          string
        >),
        preferred_roles: Object.fromEntries(
          ROLE_OPTIONS.map((r) => [r.key, player.preferred_roles.includes(r.key)]),
        ) as Record<Role, boolean>,
        ...(Object.fromEntries(STAT_KEYS.map((k) => [k, player[k]])) as Record<
          keyof PlayerStats,
          number
        >),
      });
    });
    effect(() => {
      const error = this.error();
      this.nonFieldErrors.set(error === null ? [] : applyApiErrors(this.form, error));
    });
  }

  protected setAllStats(value: number): void {
    this.form.patchValue(Object.fromEntries(STAT_KEYS.map((k) => [k, value])));
  }

  protected submit(): void {
    const value = this.form.getRawValue();
    const editing = this.initial() !== null;
    const body: PlayerInput = {
      name: value.name,
      preferred_roles: ROLE_OPTIONS.filter((r) => value.preferred_roles[r.key]).map((r) => r.key),
    };
    for (const key of PROFILE_NUMBERS) {
      if (editing || value[key] !== null) body[key] = value[key];
    }
    for (const key of PROFILE_TEXTS) {
      if (editing || value[key].trim() !== '') body[key] = value[key];
    }
    for (const key of STAT_KEYS) {
      body[key] = value[key];
    }
    this.submitted.emit(body);
  }
}
