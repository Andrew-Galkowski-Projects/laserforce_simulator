import { Component, DestroyRef, computed, inject, input, output, signal } from '@angular/core';
import { takeUntilDestroyed, toObservable } from '@angular/core/rxjs-interop';
import { FormControl, NonNullableFormBuilder, ReactiveFormsModule } from '@angular/forms';
import { MatButton } from '@angular/material/button';
import { MatError, MatFormField, MatLabel } from '@angular/material/form-field';
import { MatInput } from '@angular/material/input';
import { MatOption, MatSelect } from '@angular/material/select';
import { RouterLink } from '@angular/router';
import { Subject, catchError, combineLatest, of, startWith, switchMap } from 'rxjs';
import { listAll } from '../../api/list-all';
import { Player } from '../../api/players.models';
import { PlayersService } from '../../api/players.service';
import { PoolEntry, PoolGenerateInput, TournamentDetail } from '../../api/tournaments.models';
import { TournamentsService } from '../../api/tournaments.service';
import { AuthService } from '../../auth/auth.service';
import { apiMessage } from '../../shared/api-message';
import { countOf } from '../../shared/count-of';
import { CsvImport } from '../../shared/csv-import';
import {
  GENERIC_ERROR_MESSAGE,
  REQUIRED_MESSAGE,
  applyApiErrors,
  clearServerErrors,
} from '../../shared/form-errors';
import { injectNotify } from '../../shared/notify';
import { poolSizeInvalid } from '../pool-size';

/** What the section loads for: the Tournament, and whether the add-existing picker is shown. */
interface LoadKey {
  id: number;
  editable: boolean;
}

/**
 * The Tournament detail's Player Pool section (ANG-06-13a T4): the pool table, the size warning
 * and, while the Tournament is in setup and not in a League, the add / generate / import cards and
 * Remove. ANG-06-13b-1 adds Draw teams / Re-roll draw and the Drawn Teams cards with Set tier,
 * read from the same pool load. After every write it reloads the pool and emits `changed`, so the
 * host reloads the detail (`pool_size`, `is_drawn`, the drawn participants).
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
  private readonly fb = inject(NonNullableFormBuilder);
  private readonly notify = injectNotify();

  /** The Player-pool Tournament. */
  readonly detail = input.required<TournamentDetail>();
  /** Emitted after every successful write, so the host reloads the detail. */
  readonly changed = output<void>();

  protected readonly sizeInvalid = computed(() => poolSizeInvalid(this.detail().pool_size));

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
        this.wrote(`Added ${countOf(added.length, 'player')}.`);
      },
      error: (error: unknown) => {
        this.adding.set(false);
        this.addErrors.set(applyApiErrors(this.addForm, error));
      },
    });
  }

  /**
   * `PoolGenerateForm`: the template's defaults. The Form's bounds sit on the template's inputs,
   * whose attribute validators also check them here.
   */
  protected readonly generateForm = this.fb.group({
    count: new FormControl<number | null>(24),
    mean: new FormControl<number | null>(50),
    std_dev: new FormControl<number | null>(15),
  });
  protected readonly generating = signal(false);
  protected readonly generateErrors = signal<string[]>([]);
  protected readonly requiredMessage = REQUIRED_MESSAGE;

  protected generate(): void {
    if (this.generating()) return;
    this.generateErrors.set([]);
    clearServerErrors(this.generateForm);
    if (this.generateForm.invalid) {
      this.generateForm.markAllAsTouched();
      return;
    }
    this.generating.set(true);
    // Valid, so `count` (required) is a number; an emptied Mean / Std dev goes as null.
    const { count, mean, std_dev } = this.generateForm.getRawValue();
    const body: PoolGenerateInput = { count: count as number, mean, std_dev };
    this.tournaments.generatePool(this.detail().id, body).subscribe({
      next: (made) => {
        this.generating.set(false);
        this.wrote(`Generated ${countOf(made.length, 'player')}.`);
      },
      error: (error: unknown) => {
        this.generating.set(false);
        this.generateErrors.set(applyApiErrors(this.generateForm, error));
      },
    });
  }

  /** The Import players card: the file, template download, busy flag and error lists. */
  protected readonly csv = new CsvImport();

  protected importCsv(): void {
    const id = this.detail().id;
    this.csv.upload(
      (file) => this.tournaments.importPool(id, file),
      (imported) => this.wrote(`Imported ${countOf(imported.length, 'player')}.`),
    );
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

  protected readonly drawing = signal(false);
  protected readonly drawError = signal<string | null>(null);

  /** Draw teams, or Re-roll draw once drawn: the host's reload brings the drawn Teams. */
  protected draw(): void {
    if (this.drawing() || this.sizeInvalid()) return;
    const reroll = this.detail().is_drawn;
    this.drawing.set(true);
    this.drawError.set(null);
    this.tournaments.draw(this.detail().id).subscribe({
      next: () => {
        this.drawing.set(false);
        this.wrote(reroll ? 'Draw re-rolled.' : 'Teams drawn.');
      },
      error: (error: unknown) => {
        this.drawing.set(false);
        this.drawError.set(apiMessage(error, GENERIC_ERROR_MESSAGE));
      },
    });
  }

  /**
   * The Drawn Teams cards: each participant Team with its pool entries, in the detail's order
   * (the server sends participants by seed).
   */
  protected readonly drawnTeams = computed(() => {
    const entries = this.entries();
    return this.detail().participants.map(({ team }) => ({
      team,
      entries: entries.filter((entry) => entry.drawn_team?.id === team.id),
    }));
  });

  /** One Tier control per pool entry (player id -> control), rebuilt from each pool load. */
  protected readonly tierControls = computed(() => {
    const controls = new Map<number, FormControl<number | null>>();
    for (const entry of this.entries()) {
      controls.set(entry.player.id, new FormControl<number | null>(entry.tier));
    }
    return controls;
  });

  /** Player ids whose Set tier is in flight. */
  protected readonly tierSaving = signal<ReadonlySet<number>>(new Set());
  /** Player id -> that row's last Set tier error. */
  protected readonly tierErrors = signal<Readonly<Record<number, string>>>({});

  protected setTier(entry: PoolEntry): void {
    const id = entry.player.id;
    const control = this.tierControls().get(id);
    if (!control || this.tierSaving().has(id)) return;
    this.tierErrors.update((errors) => {
      const next = { ...errors };
      delete next[id];
      return next;
    });
    if (control.invalid) {
      control.markAsTouched();
      return;
    }
    const tier = control.value as number;
    this.tierSaving.update((ids) => new Set(ids).add(id));
    const done = () =>
      this.tierSaving.update((ids) => {
        const next = new Set(ids);
        next.delete(id);
        return next;
      });
    this.tournaments.editDrawEntry(this.detail().id, id, { tier }).subscribe({
      next: () => {
        done();
        this.wrote(`Set ${entry.player.name} to Tier ${tier}.`);
      },
      error: (error: unknown) => {
        done();
        this.tierErrors.update((errors) => ({
          ...errors,
          [id]: apiMessage(error, GENERIC_ERROR_MESSAGE),
        }));
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
