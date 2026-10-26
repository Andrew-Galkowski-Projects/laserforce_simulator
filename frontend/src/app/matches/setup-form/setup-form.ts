import { Component, computed, inject, signal } from '@angular/core';
import {
  FormControl,
  NonNullableFormBuilder,
  ReactiveFormsModule,
  Validators,
} from '@angular/forms';
import { MatButton } from '@angular/material/button';
import { MatError, MatFormField, MatHint, MatLabel } from '@angular/material/form-field';
import { MatOption, MatSelect } from '@angular/material/select';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { Observable } from 'rxjs';
import { listAll } from '../../api/list-all';
import { ArenaMap } from '../../api/maps.models';
import { MapsService } from '../../api/maps.service';
import { MatchType } from '../../api/matches.models';
import { MatchesService } from '../../api/matches.service';
import { RoundsService } from '../../api/rounds.service';
import { TeamListItem } from '../../api/teams.models';
import { TeamsService } from '../../api/teams.service';
import { applyApiErrors } from '../../shared/form-errors';
import { injectNotify } from '../../shared/notify';
import { SetupKind } from '../shared/setup-kind';

/** `Match.MATCH_TYPE_CHOICES`, as Django's `MatchForm` offers them. */
const MATCH_TYPES: readonly { value: MatchType; label: string }[] = [
  { value: 'friendly', label: 'Friendly' },
  { value: 'tournament', label: 'Tournament' },
];

/** Django's wording for a missing required field, as the API sends it for the other forms. */
const REQUIRED_MESSAGE = 'This field is required.';

/**
 * `/matches/new` and `/rounds/new`: one setup form, configured by the route's `data.kind`.
 */
@Component({
  selector: 'app-setup-form',
  imports: [
    ReactiveFormsModule,
    RouterLink,
    MatButton,
    MatError,
    MatFormField,
    MatHint,
    MatLabel,
    MatOption,
    MatSelect,
  ],
  styleUrl: './setup-form.scss',
  templateUrl: './setup-form.html',
})
export class SetupForm {
  private readonly fb = inject(NonNullableFormBuilder);
  private readonly matches = inject(MatchesService);
  private readonly rounds = inject(RoundsService);
  private readonly router = inject(Router);
  private readonly notify = injectNotify();

  protected readonly kind = inject(ActivatedRoute).snapshot.data['kind'] as SetupKind;

  /** The Account's Teams; null until loaded. */
  protected readonly teams = signal<TeamListItem[] | null>(null);
  protected readonly maps = signal<ArenaMap[]>([]);
  /** Loaded, but no Team can play: the form gives way to a link to Teams. */
  protected readonly noValidTeams = computed(() => {
    const teams = this.teams();
    return teams !== null && !teams.some((team) => team.is_valid_roster);
  });

  /** Control names match the API's `MatchForm` / `GameRoundForm`, so field errors map onto them. */
  protected readonly form = this.fb.group({
    // `required` as on Django's `MatchForm` / `GameRoundForm`; cross-field rules stay server-side.
    team_red: new FormControl<number | null>(null, Validators.required),
    team_blue: new FormControl<number | null>(null, Validators.required),
    match_type: ['friendly' as MatchType],
    /** null is the 3-zone fallback. */
    arena_map: new FormControl<number | null>(null),
  });

  protected readonly matchTypes = MATCH_TYPES;
  protected readonly requiredMessage = REQUIRED_MESSAGE;
  /** Errors no control owns (`field: null`, e.g. a `MatchSetupError`), shown above the form. */
  protected readonly nonFieldErrors = signal<string[]>([]);
  /** A simulation is running (the create is synchronous and can be slow). */
  protected readonly busy = signal(false);

  constructor() {
    const teams = inject(TeamsService);
    const maps = inject(MapsService);
    listAll((page) => teams.list({ ...page, ordering: 'name' })).subscribe((items) =>
      this.teams.set(items),
    );
    listAll((page) =>
      maps.list({ ...page, has_confirmed_config: true, ordering: 'name' }),
    ).subscribe((items) => this.maps.set(items));
  }

  /**
   * Posts the setup and opens the result. Only `required` is checked here; cross-field rules
   * (e.g. red !== blue) are the server's.
   */
  protected simulate(): void {
    this.nonFieldErrors.set([]);
    // A control's `server` error only clears on a value change, so drop it here too
    // (re-running the validators keeps `required`).
    for (const control of Object.values(this.form.controls)) {
      if (control.hasError('server')) control.updateValueAndValidity();
    }
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }
    const { team_red, team_blue, match_type, arena_map } = this.form.getRawValue();
    // `required` passed, so both sides are picked.
    const sides = { team_red, team_blue } as { team_red: number; team_blue: number };
    const created: Observable<{ id: number; winner_name: string | null }> =
      this.kind === 'match'
        ? this.matches.create({ ...sides, match_type, arena_map })
        : this.rounds.create({ ...sides, arena_map });
    const area = this.kind === 'match' ? '/matches' : '/rounds';
    // Django's success flash (`create_match` / `create_single_round`), but "Tie game." for a tie.
    const headline = this.kind === 'match' ? 'Match simulated!' : 'Round complete!';
    this.busy.set(true);
    created.subscribe({
      next: (result) => {
        void this.router.navigate([area, result.id]);
        const outcome = result.winner_name !== null ? `${result.winner_name} won!` : 'Tie game.';
        this.notify(`${headline} ${outcome}`);
      },
      error: (error: unknown) => {
        this.busy.set(false);
        this.nonFieldErrors.set(applyApiErrors(this.form, error));
      },
    });
  }

  protected teamLabel(team: TeamListItem): string {
    return team.is_valid_roster ? team.name : `${team.name} (invalid roster)`;
  }
}
