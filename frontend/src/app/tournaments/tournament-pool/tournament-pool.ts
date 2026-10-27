import { Component, DestroyRef, computed, inject, input, output, signal } from '@angular/core';
import { takeUntilDestroyed, toObservable } from '@angular/core/rxjs-interop';
import { FormGroup, NonNullableFormBuilder, ReactiveFormsModule } from '@angular/forms';
import { MatButton } from '@angular/material/button';
import { MatError, MatFormField, MatLabel } from '@angular/material/form-field';
import { MatInput } from '@angular/material/input';
import { MatOption, MatSelect } from '@angular/material/select';
import { RouterLink } from '@angular/router';
import { Subject, catchError, combineLatest, of, startWith, switchMap } from 'rxjs';
import { listAll } from '../../api/list-all';
import { Player } from '../../api/players.models';
import { PlayersService } from '../../api/players.service';
import { TeamsService } from '../../api/teams.service';
import { PoolEntry, TournamentDetail } from '../../api/tournaments.models';
import { TournamentsService } from '../../api/tournaments.service';
import { AuthService } from '../../auth/auth.service';
import { apiMessage } from '../../shared/api-message';
import { GENERIC_ERROR_MESSAGE, applyApiErrors } from '../../shared/form-errors';
import { classifyImportErrors } from '../../shared/import-errors';
import { injectNotify } from '../../shared/notify';
import { saveBlob } from '../../shared/save-blob';

/** The filename the browser saves the server's roster template CSV under. */
const TEMPLATE_FILENAME = 'roster_template.csv';

/** A drawable pool: whole Teams of 6, at least 4 of them. */
const PLAYERS_PER_TEAM = 6;
const MIN_POOL = 24;

/** A control's `server` error only clears on a value change, so a resubmit drops it here. */
function clearServerErrors(form: FormGroup): void {
  for (const control of Object.values(form.controls)) {
    if (control.hasError('server')) control.setErrors(null);
  }
}

/** What the section loads for: the Tournament, and whether the add-existing picker is shown. */
interface LoadKey {
  id: number;
  editable: boolean;
}

/**
 * The Tournament detail's Player Pool section (ANG-06-13a T4): the pool table, the size warning
 * and, while the Tournament is in setup and not in a League, the add / generate / import cards and
 * Remove. After every write it reloads the pool and emits `changed`, so the host reloads the
 * detail (`pool_size`).
 */
@Component({
  selector: 'app-tournament-pool',
  imports: [
    MatButton,
    MatError,
    MatFormField,
    MatInput,
    MatLabel,
    MatOption,
    MatSelect,
    ReactiveFormsModule,
    RouterLink,
  ],
  templateUrl: './tournament-pool.html',
  styleUrl: './tournament-pool.scss',
})
export class TournamentPool {
  private readonly tournaments = inject(TournamentsService);
  private readonly players = inject(PlayersService);
  private readonly auth = inject(AuthService);
  private readonly teams = inject(TeamsService);
  private readonly fb = inject(NonNullableFormBuilder);
  private readonly notify = injectNotify();

  /** The Player-pool Tournament. */
  readonly detail = input.required<TournamentDetail>();
  /** Emitted after every successful write, so the host reloads the detail. */
  readonly changed = output<void>();

  /** The Django page's draw rule: not a multiple of 6, or under 24. */
  protected readonly sizeInvalid = computed(() => {
    const size = this.detail().pool_size;
    return size % PLAYERS_PER_TEAM !== 0 || size < MIN_POOL;
  });

  /** Pool controls (the cards and Remove): in setup, and not run by a League. */
  protected readonly editable = computed(
    () => this.detail().state === 'setup' && !this.detail().embedded,
  );

  protected readonly selfId = computed(() => this.auth.currentUser()?.self_insert?.id ?? null);

  /** Every pool entry, in the server's order. */
  protected readonly entries = signal<PoolEntry[]>([]);
  /** Every Player the Account may add, before the pooled ones are left out. */
  private readonly allPlayers = signal<Player[]>([]);
  /** A failed pool or Players load. */
  protected readonly loadError = signal<string | null>(null);

  private readonly reloads = new Subject<void>();

  constructor() {
    const key = computed<LoadKey>(() => ({ id: this.detail().id, editable: this.editable() }), {
      equal: (a, b) => a.id === b.id && a.editable === b.editable,
    });
    const loads = combineLatest([toObservable(key), this.reloads.pipe(startWith(undefined))]);
    const destroyRef = inject(DestroyRef);
    loads
      .pipe(
        switchMap(([{ id }]) => {
          this.loadError.set(null);
          return listAll((page) => this.tournaments.pool(id, { ...page })).pipe(
            catchError(this.failLoad),
          );
        }),
        takeUntilDestroyed(destroyRef),
      )
      .subscribe((entries) => {
        if (entries) this.entries.set(entries);
      });
    loads
      .pipe(
        switchMap(([{ editable }]) =>
          editable
            ? listAll((page) => this.players.list({ ...page })).pipe(catchError(this.failLoad))
            : of([]),
        ),
        takeUntilDestroyed(destroyRef),
      )
      .subscribe((players) => {
        if (players) this.allPlayers.set(players);
      });
  }

  private readonly failLoad = (error: unknown) => {
    this.loadError.set(apiMessage(error, GENERIC_ERROR_MESSAGE));
    return of(null);
  };

  /** The picker's choices: by name, without the Players already in the pool. */
  protected readonly addablePlayers = computed(() => {
    const pooled = new Set(this.entries().map((entry) => entry.player.id));
    return this.allPlayers()
      .filter((player) => !pooled.has(player.id))
      .sort((a, b) => a.name.localeCompare(b.name));
  });

  /** `PoolAddPlayersForm`: the control name matches, so `player_ids` errors land on it. */
  protected readonly addForm = this.fb.group({ player_ids: [[] as number[]] });
  protected readonly adding = signal(false);
  protected readonly addErrors = signal<string[]>([]);

  protected add(): void {
    const ids = this.addForm.controls.player_ids.value;
    if (ids.length === 0 || this.adding()) return;
    this.adding.set(true);
    this.addErrors.set([]);
    clearServerErrors(this.addForm);
    this.tournaments.addPoolPlayers(this.detail().id, ids).subscribe({
      next: (added) => {
        this.adding.set(false);
        this.addForm.reset({ player_ids: [] });
        this.wrote(`Added ${added.length} players.`);
      },
      error: (error: unknown) => {
        this.adding.set(false);
        this.addErrors.set(applyApiErrors(this.addForm, error));
      },
    });
  }

  /** `PoolGenerateForm`: the template's defaults; the inputs carry the Form's bounds. */
  protected readonly generateForm = this.fb.group({ count: [24], mean: [50], std_dev: [15] });
  protected readonly generating = signal(false);
  protected readonly generateErrors = signal<string[]>([]);

  protected generate(): void {
    if (this.generating()) return;
    this.generating.set(true);
    this.generateErrors.set([]);
    clearServerErrors(this.generateForm);
    this.tournaments.generatePool(this.detail().id, this.generateForm.getRawValue()).subscribe({
      next: (made) => {
        this.generating.set(false);
        this.wrote(`Generated ${made.length} players.`);
      },
      error: (error: unknown) => {
        this.generating.set(false);
        this.generateErrors.set(applyApiErrors(this.generateForm, error));
      },
    });
  }

  /** The CSV picked through the hidden input; Import players needs one. */
  protected readonly file = signal<File | null>(null);
  protected readonly importing = signal(false);
  /** A failed template download, or an import error no control owns. */
  protected readonly importErrors = signal<string[]>([]);
  /** `csv_file` errors, shown on the file control. */
  protected readonly fileErrors = signal<string[]>([]);
  /** Row errors, formatted ("Row N (column): ..."), in server order. */
  protected readonly rowErrors = signal<string[]>([]);

  /** Fetches the server's roster template CSV (behind the JWT, so not a plain link). */
  protected downloadTemplate(): void {
    this.importErrors.set([]);
    this.teams.importTemplate().subscribe({
      next: (blob) => saveBlob(blob, TEMPLATE_FILENAME),
      error: (error: unknown) => this.importErrors.set([apiMessage(error, GENERIC_ERROR_MESSAGE)]),
    });
  }

  /** The hidden input's `change`: keep the File and clear the input so the same name re-fires. */
  protected choose(event: Event): void {
    const input = event.target as HTMLInputElement;
    this.file.set(input.files?.[0] ?? null);
    input.value = '';
  }

  protected importCsv(): void {
    const file = this.file();
    if (!file || this.importing()) return;
    this.importErrors.set([]);
    this.fileErrors.set([]);
    this.rowErrors.set([]);
    this.importing.set(true);
    this.tournaments.importPool(this.detail().id, file).subscribe({
      next: (imported) => {
        this.importing.set(false);
        this.file.set(null);
        this.wrote(`Imported ${imported.length} players.`);
      },
      error: (error: unknown) => {
        this.importing.set(false);
        const errors = classifyImportErrors(error);
        this.fileErrors.set(errors.file);
        this.rowErrors.set(errors.rows);
        this.importErrors.set(errors.alert);
      },
    });
  }

  /** True while a Remove is in flight: every Remove button waits for it. */
  protected readonly removing = signal(false);
  protected readonly removeError = signal<string | null>(null);

  /** Removes at once, no confirm (the Django page's one-click Remove). */
  protected remove(entry: PoolEntry): void {
    if (this.removing()) return;
    this.removing.set(true);
    this.removeError.set(null);
    this.tournaments.removePoolPlayer(this.detail().id, entry.player.id).subscribe({
      next: () => {
        this.removing.set(false);
        this.wrote(`Removed ${entry.player.name}.`);
      },
      error: (error: unknown) => {
        this.removing.set(false);
        this.removeError.set(apiMessage(error, GENERIC_ERROR_MESSAGE));
      },
    });
  }

  /** After a successful write: the snackbar, the pool reload and the host's detail reload. */
  private wrote(message: string): void {
    this.notify(message);
    this.reloads.next();
    this.changed.emit();
  }
}
