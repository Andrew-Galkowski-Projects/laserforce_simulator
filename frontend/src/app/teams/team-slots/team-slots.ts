import { Component, inject, signal } from '@angular/core';
import {
  FormControl,
  FormGroup,
  NonNullableFormBuilder,
  ReactiveFormsModule,
} from '@angular/forms';
import { MatButton } from '@angular/material/button';
import { MatError, MatFormField, MatLabel } from '@angular/material/form-field';
import { MatOption, MatSelect } from '@angular/material/select';
import { Router, RouterLink } from '@angular/router';
import { Team, TeamSlotsInput } from '../../api/teams.models';
import { TeamsService } from '../../api/teams.service';
import { applyApiErrors } from '../shared/form-errors';
import { injectNotify } from '../shared/notify';
import { SLOTS, SlotKey } from '../shared/roster';
import { loadRouteTeam } from '../shared/route-team';
import { TeamNotFound } from '../shared/team-not-found';

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
  styleUrls: ['../shared/team-page.scss', './team-slots.scss'],
  templateUrl: './team-slots.html',
})
export class TeamSlots {
  private readonly teams = inject(TeamsService);
  private readonly router = inject(Router);
  private readonly fb = inject(NonNullableFormBuilder);
  private readonly notify = injectNotify();

  protected readonly slots = SLOTS;
  protected readonly emptySlot = EMPTY_SLOT;
  protected readonly form = signal<SlotsForm | null>(null);
  private readonly load = loadRouteTeam((team) => this.form.set(this.buildForm(team)));
  protected readonly team = this.load.team;
  protected readonly notFound = this.load.notFound;
  protected readonly loadError = this.load.loadError;
  protected readonly nonFieldErrors = signal<string[]>([]);
  protected readonly busy = signal(false);

  protected save(team: Team, form: SlotsForm): void {
    this.busy.set(true);
    this.nonFieldErrors.set([]);
    const raw = form.getRawValue();
    const slot = (key: SlotKey): number | null => (raw[key] === EMPTY_SLOT ? null : raw[key]);
    const body: TeamSlotsInput = {
      slot_commander: slot('slot_commander'),
      slot_heavy: slot('slot_heavy'),
      slot_scout_1: slot('slot_scout_1'),
      slot_scout_2: slot('slot_scout_2'),
      slot_medic: slot('slot_medic'),
      slot_ammo: slot('slot_ammo'),
    };
    this.teams.setSlots(team.id, body).subscribe({
      next: (saved) => {
        this.notify('Roster slots updated.');
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
