import { Component, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import {
  FormControl,
  FormGroup,
  NonNullableFormBuilder,
  ReactiveFormsModule,
} from '@angular/forms';
import { MatButton } from '@angular/material/button';
import { MatError, MatFormField, MatLabel } from '@angular/material/form-field';
import { MatOption, MatSelect } from '@angular/material/select';
import { MatSnackBar } from '@angular/material/snack-bar';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { switchMap } from 'rxjs';
import { ApiError } from '../../api/api-client';
import { Team, TeamSlotsInput } from '../../api/teams.models';
import { TeamsService } from '../../api/teams.service';
import { SLOTS, SlotKey } from '../shared/roster';
import { TeamNotFound } from '../shared/team-not-found';
import { applyApiErrors } from '../team-create/form-errors';

/**
 * The "— empty —" option's value. Material treats a `null` option as a reset option that
 * shows no text, so the empty choice uses 0 (never a Player id) and is sent as `null`.
 */
const EMPTY_SLOT = 0;

type SlotsForm = FormGroup<Record<SlotKey, FormControl<number>>>;

/** `/teams/:id/slots`: assign this Team's players to its six role slots. */
@Component({
  selector: 'app-team-slots',
  imports: [
    ReactiveFormsModule,
    RouterLink,
    MatButton,
    MatError,
    MatFormField,
    MatLabel,
    MatOption,
    MatSelect,
    TeamNotFound,
  ],
  styleUrls: ['../team-create/team-page.scss', './team-slots.scss'],
  templateUrl: './team-slots.html',
})
export class TeamSlots {
  private readonly teams = inject(TeamsService);
  private readonly router = inject(Router);
  private readonly snackBar = inject(MatSnackBar);
  private readonly fb = inject(NonNullableFormBuilder);

  protected readonly slots = SLOTS;
  protected readonly emptySlot = EMPTY_SLOT;
  protected readonly team = signal<Team | null>(null);
  protected readonly form = signal<SlotsForm | null>(null);
  protected readonly notFound = signal(false);
  protected readonly loadError = signal<string | null>(null);
  protected readonly nonFieldErrors = signal<string[]>([]);
  protected readonly busy = signal(false);

  constructor() {
    inject(ActivatedRoute)
      .paramMap.pipe(
        switchMap((params) => this.teams.get(Number(params.get('id')))),
        takeUntilDestroyed(),
      )
      .subscribe({
        next: (team) => {
          this.form.set(this.buildForm(team));
          this.team.set(team);
        },
        error: (error: unknown) => {
          if (error instanceof ApiError && error.status === 404) {
            this.notFound.set(true);
          } else {
            this.loadError.set(error instanceof Error ? error.message : 'Could not load the Team.');
          }
        },
      });
  }

  protected save(team: Team, form: SlotsForm): void {
    this.busy.set(true);
    this.nonFieldErrors.set([]);
    const raw = form.getRawValue();
    const body = Object.fromEntries(
      SLOTS.map(({ key }) => [key, raw[key] === EMPTY_SLOT ? null : raw[key]]),
    ) as unknown as TeamSlotsInput;
    this.teams.setSlots(team.id, body).subscribe({
      next: (saved) => {
        this.snackBar.open('Roster slots updated.', 'Close', { duration: 5000 });
        void this.router.navigate(['/teams', saved.id]);
      },
      error: (error: unknown) => {
        this.busy.set(false);
        this.nonFieldErrors.set(applyApiErrors(form, error));
      },
    });
  }

  private buildForm(team: Team): SlotsForm {
    const controls = Object.fromEntries(
      SLOTS.map(({ key }) => [key, this.fb.control(team[key] ?? EMPTY_SLOT)]),
    ) as Record<SlotKey, FormControl<number>>;
    return new FormGroup(controls);
  }
}
