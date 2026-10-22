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
import { STAT_GROUPS, overallRating } from './player-stats';

/** The five Preferred-role checkboxes, in the order they are emitted. */
const ROLE_OPTIONS: readonly { key: Role; label: string }[] = [
  { key: 'commander', label: 'Commander' },
  { key: 'heavy', label: 'Heavy Weapons' },
  { key: 'scout', label: 'Scout' },
  { key: 'medic', label: 'Medic' },
  { key: 'ammo', label: 'Ammo' },
];

const STAT_KEYS: readonly (keyof PlayerStats)[] = STAT_GROUPS.flatMap((g) =>
  g.stats.map((s) => s.key),
);

/** The profile fields: the numbers (blank is `null`) and the texts (blank is `''`). */
const PROFILE_NUMBERS = ['age', 'started_playing_age', 'total_games'] as const;
const PROFILE_TEXTS = ['home_site', 'height'] as const;

/** A new Player's Stats (the Django `player_add` initial). */
const NEW_STAT = 50;

type StatControls = Record<keyof PlayerStats, FormControl<number | null>>;

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
  template: `
    <form [formGroup]="form" (ngSubmit)="submit()" novalidate>
      <div class="player-form__summary">
        <p class="player-form__overall" role="status" aria-live="polite">
          Overall: {{ overall() }}
        </p>
        <div class="player-form__presets">
          <button mat-stroked-button type="button" (click)="setAllStats(50)">
            Set all to Average (50)
          </button>
          <button mat-stroked-button type="button" (click)="setAllStats(90)">
            Set all to Elite (90)
          </button>
        </div>
      </div>

      @for (message of nonFieldErrors(); track $index) {
        <p class="team-form__error" role="alert">{{ message }}</p>
      }

      <mat-form-field appearance="outline">
        <mat-label>Name</mat-label>
        <input matInput formControlName="name" name="name" autocomplete="off" />
        @if (form.controls.name.getError('server'); as message) {
          <mat-error>{{ message }}</mat-error>
        }
      </mat-form-field>

      <fieldset>
        <legend>Profile</legend>
        @if (initial() === null) {
          <p class="player-form__hint">Leave blank to randomise.</p>
        }
        <div class="player-form__grid">
          @for (field of profileFields; track field.key) {
            <mat-form-field appearance="outline">
              <mat-label>{{ field.label }}</mat-label>
              <!-- A static type: Angular picks the number value accessor from it. -->
              @if (field.type === 'number') {
                <input
                  matInput
                  type="number"
                  min="0"
                  step="1"
                  [formControlName]="field.key"
                  [name]="field.key"
                  autocomplete="off"
                />
              } @else {
                <input
                  matInput
                  type="text"
                  [formControlName]="field.key"
                  [name]="field.key"
                  autocomplete="off"
                />
              }
              @if (form.get(field.key)?.getError('server'); as message) {
                <mat-error>{{ message }}</mat-error>
              }
            </mat-form-field>
          }
        </div>
      </fieldset>

      <fieldset formGroupName="preferred_roles">
        <legend>Preferred Roles</legend>
        <div class="player-form__roles">
          @for (role of roleOptions; track role.key) {
            <mat-checkbox [formControlName]="role.key">{{ role.label }}</mat-checkbox>
          }
        </div>
        @if (form.controls.preferred_roles.getError('server'); as message) {
          <p class="player-form__field-error">{{ message }}</p>
        }
      </fieldset>

      @for (group of statGroups; track group.label) {
        <fieldset>
          <legend>{{ group.label }}</legend>
          <div class="player-form__grid">
            @for (stat of group.stats; track stat.key) {
              <mat-form-field appearance="outline">
                <mat-label>{{ stat.label }}</mat-label>
                <input
                  matInput
                  type="number"
                  min="0"
                  max="100"
                  step="1"
                  [formControlName]="stat.key"
                  [name]="stat.key"
                  autocomplete="off"
                />
                @if (form.get(stat.key)?.getError('server'); as message) {
                  <mat-error>{{ message }}</mat-error>
                }
              </mat-form-field>
            }
          </div>
        </fieldset>
      }

      <div class="team-form__actions">
        <button mat-flat-button type="submit" [disabled]="busy()">{{ submitLabel() }}</button>
        <a mat-button [routerLink]="cancelLink()">Cancel</a>
      </div>
    </form>
  `,
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
  protected readonly profileFields: readonly {
    key: (typeof PROFILE_NUMBERS)[number] | (typeof PROFILE_TEXTS)[number];
    label: string;
    type: 'number' | 'text';
  }[] = [
    { key: 'age', label: 'Age', type: 'number' },
    { key: 'started_playing_age', label: 'Started Playing Age', type: 'number' },
    { key: 'total_games', label: 'Total Games', type: 'number' },
    { key: 'home_site', label: 'Home Site', type: 'text' },
    { key: 'height', label: 'Height', type: 'text' },
  ];

  private readonly fb = inject(NonNullableFormBuilder);
  protected readonly form = new FormGroup({
    name: this.fb.control(''),
    age: new FormControl<number | null>(null),
    started_playing_age: new FormControl<number | null>(null),
    total_games: new FormControl<number | null>(null),
    home_site: this.fb.control(''),
    height: this.fb.control(''),
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
        age: player.age,
        started_playing_age: player.started_playing_age,
        total_games: player.total_games,
        home_site: player.home_site,
        height: player.height,
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
    const body: Record<string, unknown> = {
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
    this.submitted.emit(body as PlayerInput);
  }
}
