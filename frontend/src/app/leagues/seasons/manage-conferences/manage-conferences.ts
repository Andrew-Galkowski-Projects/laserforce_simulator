import { Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { MatAnchor, MatButton } from '@angular/material/button';
import { MatFormField } from '@angular/material/form-field';
import { MatInput } from '@angular/material/input';
import { RouterLink } from '@angular/router';
import { Subscription } from 'rxjs';
import { TeamRef } from '../../../api/analytics.models';
import { ApiError } from '../../../api/api-client';
import { listAll } from '../../../api/list-all';
import { ArenaMap } from '../../../api/maps.models';
import { MapsService } from '../../../api/maps.service';
import { ConferenceWrite, ConferencesState } from '../../../api/seasons.models';
import { SeasonsService } from '../../../api/seasons.service';
import { apiMessage } from '../../../shared/api-message';
import { GENERIC_ERROR_MESSAGE } from '../../../shared/form-errors';
import { injectNotify } from '../../../shared/notify';
import { MapRotationComposer } from '../../league-create-advanced/map-rotation-composer/map-rotation-composer';
import { SeasonBreadcrumb } from '../season-breadcrumb/season-breadcrumb';
import { SeasonContext } from '../season-context';

/**
 * One Conference row of the editor. `key` is stable for the row's life, so a Team's choice (a row
 * key) survives another row's removal: the template script's index remap (`rebuildSelects`).
 */
interface ConferenceRow {
  key: number;
  name: string;
  rotation: (number | null)[];
}

/**
 * `/leagues/:id/seasons/:seasonId/conferences` (spec #444, `seasons/manage_conferences.html`):
 * a draft Season's Conference editor (names, Team assignment, per-Conference map rotation), or a
 * started Season's locked partition, read-only.
 */
@Component({
  selector: 'app-manage-conferences',
  imports: [
    MapRotationComposer,
    MatAnchor,
    MatButton,
    MatFormField,
    MatInput,
    RouterLink,
    SeasonBreadcrumb,
  ],
  templateUrl: './manage-conferences.html',
  styleUrl: './manage-conferences.scss',
})
export class ManageConferences {
  private readonly seasons = inject(SeasonsService);
  private readonly mapsService = inject(MapsService);
  private readonly context = inject(SeasonContext);
  private readonly seasonId = this.context.seasonId;
  private readonly destroyRef = inject(DestroyRef);

  protected readonly state = signal<ConferencesState | null>(null);
  /** The picker's simulation-ready maps (editable), or every map (read-only names). */
  protected readonly maps = signal<ArenaMap[] | null>(null);
  protected readonly loadError = signal<string | null>(null);
  /** Which GET failed, so Retry re-sends only that one (I2). */
  private failed: 'conferences' | 'maps' = 'conferences';

  protected readonly rows = signal<ConferenceRow[]>([]);
  /** Each Team's row key, or null for Unassigned. */
  protected readonly assignments = signal<ReadonlyMap<number, number | null>>(new Map());
  private nextKey = 0;

  /** I3: a PUT is open. */
  protected readonly busy = signal(false);
  /** Every message of a failed save, for the alert above the form. */
  protected readonly saveErrors = signal<string[]>([]);
  private readonly notify = injectNotify();

  private loading: Subscription | null = null;
  private saving: Subscription | null = null;

  /** The Team-select option labels, by row: the name, or "Conference <n>" while it is blank. */
  protected readonly options = computed(() =>
    this.rows().map((row, i) => ({
      key: row.key,
      label: row.name.trim() === '' ? `Conference ${i + 1}` : row.name,
    })),
  );

  /**
   * The read-only cards, by ordinal: each Conference's Teams by name and its rotation's map names
   * (the activation snapshot, else the live list), a deleted map dropped.
   */
  protected readonly lockedCards = computed(() => {
    const state = this.state();
    if (state === null) return [];
    const mapNames = new Map((this.maps() ?? []).map((m) => [m.id, m.name]));
    return [...state.conferences]
      .sort((a, b) => a.ordinal - b.ordinal)
      .map((c) => ({
        id: c.id,
        name: c.name,
        teams: state.teams.filter((t) => c.team_ids.includes(t.id)),
        rotation: (c.starting_map_rotation_ids ?? c.map_rotation_ids)
          .map((id) => mapNames.get(id))
          .filter((name): name is string => name !== undefined),
      }));
  });

  /** "Cancel": the Season dashboard. */
  protected readonly dashboardLink = computed(() => [
    '/leagues',
    this.context.leagueId(),
    'seasons',
    this.seasonId(),
  ]);

  constructor() {
    this.loadConferences();
    // Leaving the screen drops an open load or save: no late snackbar.
    this.destroyRef.onDestroy(() => {
      this.loading?.unsubscribe();
      this.saving?.unsubscribe();
    });
  }

  private loadConferences(): void {
    this.loadError.set(null);
    this.loading = this.seasons.conferences(this.seasonId() as number).subscribe({
      next: (state) => {
        this.fill(state);
        this.loadMaps(state.editable);
      },
      error: (error: unknown) => this.fail('conferences', error),
    });
  }

  private loadMaps(editable: boolean): void {
    this.loadError.set(null);
    this.loading = listAll((page) =>
      this.mapsService.list({
        ...page,
        ordering: 'name',
        ...(editable ? { simulation_ready: true } : {}),
      }),
    ).subscribe({
      next: (maps) => this.maps.set(maps),
      error: (error: unknown) => this.fail('maps', error),
    });
  }

  private fail(step: 'conferences' | 'maps', error: unknown): void {
    this.failed = step;
    this.loadError.set(apiMessage(error, GENERIC_ERROR_MESSAGE));
  }

  /** I2: re-sends only the GET that failed. */
  protected retry(): void {
    const state = this.state();
    if (this.failed === 'maps' && state) this.loadMaps(state.editable);
    else this.loadConferences();
  }

  /** The editor's rows and Team choices from a loaded (or saved) state. */
  private fill(state: ConferencesState): void {
    const ordered = [...state.conferences].sort((a, b) => a.ordinal - b.ordinal);
    const rows = ordered.map((c) => ({
      key: this.nextKey++,
      name: c.name,
      rotation: [...c.map_rotation_ids],
    }));
    const byTeam = new Map<number, number | null>(state.teams.map((t) => [t.id, null]));
    ordered.forEach((c, i) => c.team_ids.forEach((id) => byTeam.set(id, rows[i].key)));
    this.rows.set(rows);
    this.assignments.set(byTeam);
    this.state.set(state);
  }

  /** "+ Add conference": an empty row, no map. */
  protected addRow(): void {
    this.rows.update((rows) => [...rows, { key: this.nextKey++, name: '', rotation: [] }]);
  }

  /** "Remove": its Teams go Unassigned; every other Team keeps its Conference. */
  protected removeRow(key: number): void {
    this.rows.update((rows) => rows.filter((r) => r.key !== key));
    this.assignments.update(
      (current) => new Map([...current].map(([team, k]) => [team, k === key ? null : k])),
    );
  }

  /**
   * "Save Conferences": a full replace, each row in order (Teams in `teams` order, the
   * composer's picked maps). I3: the form locks while the PUT is open; a failure keeps every edit.
   */
  protected save(state: ConferencesState): void {
    if (this.busy()) return;
    const assignments = this.assignments();
    const conferences: ConferenceWrite[] = this.rows().map((row) => ({
      name: row.name,
      team_ids: state.teams.filter((t) => assignments.get(t.id) === row.key).map((t) => t.id),
      map_rotation_ids: row.rotation.filter((id): id is number => id !== null),
    }));
    this.saveErrors.set([]);
    this.busy.set(true);
    this.saving = this.seasons
      .replaceConferences(this.seasonId() as number, { conferences })
      .subscribe({
        next: (saved) => {
          this.busy.set(false);
          this.fill(saved);
          this.notify('Conferences saved.');
        },
        error: (error: unknown) => {
          this.busy.set(false);
          this.saveErrors.set(
            error instanceof ApiError
              ? error.errors.map((e) => e.message)
              : [GENERIC_ERROR_MESSAGE],
          );
        },
      });
  }

  protected rename(key: number, name: string): void {
    this.rows.update((rows) => rows.map((r) => (r.key === key ? { ...r, name } : r)));
  }

  protected setRotation(key: number, rotation: (number | null)[]): void {
    this.rows.update((rows) => rows.map((r) => (r.key === key ? { ...r, rotation } : r)));
  }

  protected assign(team: TeamRef, value: string): void {
    const key = value === '' ? null : Number(value);
    this.assignments.update((current) => new Map(current).set(team.id, key));
  }

  protected chosen(team: TeamRef): number | null {
    return this.assignments().get(team.id) ?? null;
  }
}
