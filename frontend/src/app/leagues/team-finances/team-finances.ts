import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed, toObservable, toSignal } from '@angular/core/rxjs-interop';
import {
  AbstractControl,
  FormControl,
  FormGroup,
  ReactiveFormsModule,
  ValidationErrors,
} from '@angular/forms';
import { MatButton } from '@angular/material/button';
import { MatError, MatFormField, MatHint, MatLabel } from '@angular/material/form-field';
import { MatInput } from '@angular/material/input';
import { ActivatedRoute, Router } from '@angular/router';
import {
  Observable,
  Subscription,
  catchError,
  combineLatest,
  map,
  of,
  startWith,
  switchMap,
  tap,
} from 'rxjs';
import { TeamRef } from '../../api/analytics.models';
import { ApiError } from '../../api/api-client';
import {
  BudgetLevels,
  INJURY_POLICY_LABELS,
  InjuryPolicy,
  TeamFinances,
  TeamFinancesHeader,
  TeamFinancesUpdate,
} from '../../api/league-finances.models';
import { LeagueFinancesService } from '../../api/league-finances.service';
import { AuthService } from '../../auth/auth.service';
import { apiMessage } from '../../shared/api-message';
import {
  GENERIC_ERROR_MESSAGE,
  REQUIRED_MESSAGE,
  applyApiErrors,
  clearServerErrors,
} from '../../shared/form-errors';
import { moneyK } from '../../shared/money-k';
import { injectNotify } from '../../shared/notify';
import { LeagueContext } from '../stats-shared/league-context';
import { resolveTeamParam, teamQueryParam } from '../stats-shared/team-param';
import { TeamPicker } from '../stats-shared/team-picker';

/** What the screen shows. */
type View =
  /** The nav or the finances are still loading. */
  | { kind: 'loading' }
  /** I1: finances are off (from the nav: no request is sent; or from the response). */
  | { kind: 'disabled' }
  /** I1: the League has no displayed Season (no request is sent). */
  | { kind: 'noSeason' }
  /** I1: no Team resolves (no request is sent), or `?team=` 404s. */
  | { kind: 'noTeam' }
  /** I2: any other load failure; Retry re-sends the request. */
  | { kind: 'error'; message: string }
  | { kind: 'loaded'; data: TeamFinances; options: TeamRef[] };

/** The request the nav and `?team=` call for, or the state that replaces it. */
type Request = { leagueId: number; teamId: number } | View;

type BudgetControl = 'budget_scouting' | 'budget_coaching' | 'budget_facilities' | 'budget_health';

/** The four Budget level rows, in the template's order; Health's hint adds a clause. */
const BUDGET_ROWS: readonly {
  key: keyof BudgetLevels;
  control: BudgetControl;
  label: string;
  extra: string;
}[] = [
  { key: 'scouting', control: 'budget_scouting', label: 'Scouting', extra: '' },
  { key: 'coaching', control: 'budget_coaching', label: 'Coaching', extra: '' },
  { key: 'facilities', control: 'budget_facilities', label: 'Facilities', extra: '' },
  {
    key: 'health',
    control: 'budget_health',
    label: 'Health',
    extra: ' · shortens injury durations',
  },
];

/** Django's `IntegerField` check: a Budget level is a whole number. */
function wholeNumber(control: AbstractControl): ValidationErrors | null {
  const value: unknown = control.value;
  return value === null || value === '' || Number.isInteger(Number(value))
    ? null
    : { wholeNumber: true };
}

/** A full payload (finances on and a Season), as opposed to the three-key header. */
function isFull(data: TeamFinancesHeader | TeamFinances): data is TeamFinances {
  return data.finance_enabled && 'budgets' in data;
}

/**
 * `/leagues/:id/team/finances?team=` (spec #439 Team Finances, `leagues/team_finances.html`): a
 * Team's payroll, Wins / Hype / Revenue / Expenses / Cash tiles, the Expense Settings form, Player
 * Salaries, Player availability and Season history. The Team is `resolveTeamParam` (`?team=`,
 * else the nav's `schedule_team`); the picker sets `?team=`. States in I1 order: the nav loading,
 * finances off, no Season, no Team (none sends a request), then the load.
 */
@Component({
  selector: 'app-league-team-finances',
  imports: [
    ReactiveFormsModule,
    MatButton,
    MatError,
    MatFormField,
    MatHint,
    MatInput,
    MatLabel,
    TeamPicker,
  ],
  templateUrl: './team-finances.html',
  styleUrl: './team-finances.scss',
})
export class LeagueTeamFinances {
  private readonly finances = inject(LeagueFinancesService);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly auth = inject(AuthService);
  private readonly notify = injectNotify();
  private readonly context = inject(LeagueContext);
  protected readonly nav = this.context.nav;
  protected readonly moneyK = moneyK;
  protected readonly budgetRows = BUDGET_ROWS;
  protected readonly injuryPolicies = Object.entries(INJURY_POLICY_LABELS) as [
    InjuryPolicy,
    string,
  ][];

  /**
   * The Expense Settings form; control names are the PATCH body's. `required` / `min` / `max`
   * sit on the template's inputs (the Django widgets'); a Budget level must also be whole. The
   * Ticket price holds its one-decimal text until edited, and is sent as a number.
   */
  protected readonly form = new FormGroup({
    budget_scouting: new FormControl<number | null>(null, wholeNumber),
    budget_coaching: new FormControl<number | null>(null, wholeNumber),
    budget_facilities: new FormControl<number | null>(null, wholeNumber),
    budget_health: new FormControl<number | null>(null, wholeNumber),
    injury_policy: new FormControl<InjuryPolicy>('auto_sub', { nonNullable: true }),
    ticket_price: new FormControl<number | string | null>(null),
  });
  /** I3: a PATCH is open. */
  protected readonly busy = signal(false);
  /** The "Expense settings not saved:" list: unmatched 400 errors and any 409. */
  protected readonly formErrors = signal<string[]>([]);
  private saving: Subscription | null = null;

  /** The signed-in Account's Self-insert Player id (the "You" badge), or null. */
  protected readonly selfId = computed(() => this.auth.currentUser()?.self_insert?.id ?? null);

  private readonly teamQuery = toSignal(this.route.queryParamMap.pipe(map(teamQueryParam)), {
    initialValue: null,
  });
  private readonly reloads = signal(0);

  private readonly request = computed((): Request => {
    const nav = this.nav();
    const leagueId = this.context.leagueId();
    if (nav === null || leagueId === null) return { kind: 'loading' };
    if (!nav.finance_enabled) return { kind: 'disabled' };
    if (nav.displayed_season === null) return { kind: 'noSeason' };
    const teamId = resolveTeamParam(this.teamQuery(), nav);
    return teamId === null ? { kind: 'noTeam' } : { leagueId, teamId };
  });

  protected readonly view = signal<View>({ kind: 'loading' });

  constructor() {
    combineLatest([toObservable(this.request), toObservable(this.reloads)])
      .pipe(
        switchMap(([req]) => ('kind' in req ? of(req) : this.load(req.leagueId, req.teamId))),
        takeUntilDestroyed(),
      )
      .subscribe((view) => {
        if (view.kind === 'loaded') this.fill(view.data);
        this.view.set(view);
      });
  }

  private load(leagueId: number, teamId: number): Observable<View> {
    return this.finances.team(leagueId, teamId).pipe(
      // I4: a Team switch or reload drops a save in flight and any unsaved edits.
      tap({ subscribe: () => this.cancelSave() }),
      map(({ data, meta }): View =>
        isFull(data) ? { kind: 'loaded', data, options: meta.team_options } : { kind: 'disabled' },
      ),
      catchError((error: unknown) =>
        of<View>(
          error instanceof ApiError && error.status === 404
            ? { kind: 'noTeam' }
            : { kind: 'error', message: apiMessage(error, GENERIC_ERROR_MESSAGE) },
        ),
      ),
      startWith<View>({ kind: 'loading' }),
    );
  }

  protected retry(): void {
    this.reloads.update((n) => n + 1);
  }

  /** The picker sets `?team=`; the load follows the URL. */
  protected pickTeam(team: number | null): void {
    void this.router.navigate([], { relativeTo: this.route, queryParams: { team } });
  }

  /** A control's message: its own check in Django's wording, else the server's. */
  protected errorText(control: AbstractControl): string | null {
    if (control.hasError('required')) return REQUIRED_MESSAGE;
    if (control.hasError('min')) {
      return `Ensure this value is greater than or equal to ${control.getError('min').min}.`;
    }
    if (control.hasError('max')) {
      return `Ensure this value is less than or equal to ${control.getError('max').max}.`;
    }
    if (control.hasError('wholeNumber')) return 'Enter a whole number.';
    return control.getError('server') ?? null;
  }

  /** Save: a client-invalid form sends nothing; otherwise PATCH all six fields (I3). */
  protected save(data: TeamFinances, options: TeamRef[]): void {
    const leagueId = this.context.leagueId();
    if (this.busy() || !data.editable || leagueId === null) return;
    this.formErrors.set([]);
    clearServerErrors(this.form);
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }
    const value = this.form.getRawValue();
    const body: TeamFinancesUpdate = {
      budget_scouting: Number(value.budget_scouting),
      budget_coaching: Number(value.budget_coaching),
      budget_facilities: Number(value.budget_facilities),
      budget_health: Number(value.budget_health),
      injury_policy: value.injury_policy,
      ticket_price: Number(value.ticket_price),
    };
    this.busy.set(true);
    this.form.disable();
    this.saving = this.finances.updateTeam(leagueId, data.team.id, body).subscribe({
      next: (saved) => {
        this.busy.set(false);
        this.notify('Expense settings saved.');
        if (isFull(saved)) {
          this.fill(saved);
          this.view.set({ kind: 'loaded', data: saved, options });
        } else {
          this.view.set({ kind: 'disabled' });
        }
      },
      error: (error: unknown) => {
        this.busy.set(false);
        this.form.enable();
        this.formErrors.set(
          error instanceof ApiError && error.status === 409
            ? error.errors.map((e) => e.message)
            : applyApiErrors(this.form, error),
        );
      },
    });
  }

  /** Puts a payload's settings in the form; a Team the Account does not manage is read-only. */
  private fill(data: TeamFinances): void {
    this.formErrors.set([]);
    this.form.reset({
      budget_scouting: data.budgets.scouting,
      budget_coaching: data.budgets.coaching,
      budget_facilities: data.budgets.facilities,
      budget_health: data.budgets.health,
      injury_policy: data.injury_policy,
      ticket_price: data.ticket_price.toFixed(1),
    });
    if (data.editable) this.form.enable();
    else this.form.disable();
  }

  private cancelSave(): void {
    this.saving?.unsubscribe();
    this.saving = null;
    this.busy.set(false);
  }

  /** The Hype bar's fill: `current_hype` clamped to 0..1. */
  protected hypeFraction(hype: number): number {
    return Math.min(Math.max(hype, 0), 1);
  }
}
