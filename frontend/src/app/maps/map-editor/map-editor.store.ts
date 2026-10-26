// The map editor's state: one instance per editor visit (provided by the `MapEditor` component).
import { DestroyRef, Injectable, computed, inject, signal } from '@angular/core';
import { Observable, Subscription, catchError, forkJoin, of, tap } from 'rxjs';
import { ApiError } from '../../api/api-client';
import {
  ArenaMap,
  BaseType,
  CellKey,
  CellPair,
  ComputedSightLines,
  MapBase,
  MapHeatmap,
  MapZones,
  SightLines,
  SpawnCells,
  StrongSpots,
  WallMeta,
  ZoneConfig,
  ZoneConfigInput,
  ZoneType,
} from '../../api/maps.models';
import { MapsService } from '../../api/maps.service';
import { apiMessage } from '../../shared/api-message';
import { PixelPoint, cellKeys } from './grid';

/** The Zone-size picker's options, exactly the Django editor's. */
export const ZONE_SIZES = [20, 30, 40, 50, 60, 80, 100] as const;

/** The Zone size a map without a confirmed config opens at. */
export const DEFAULT_ZONE_SIZE = 50;

export type EditorMode = 'zones' | 'sight-lines' | 'heatmap';

/** An area's load state: `idle` until a mode first asks for it. */
export type LoadStatus = 'idle' | 'loading' | 'ready' | 'error';

/** The three areas that save separately, each with its own dirty flag. */
export type DirtyArea = 'zoneConfig' | 'sightLines' | 'strongSpots';

/** The placed Bases by type (a type is absent while unplaced). */
export type Bases = Partial<Record<BaseType, PixelPoint>>;

/** The Zones & Bases area: what Save Configuration sends. */
export interface ZoneEdit {
  /** The Cell types, a copy of the loaded grid's (edit it, never `grid().zones`). */
  zones: ZoneType[][];
  /** Windowed facing (and any JSON-set `height`), kept untouched where not repainted. */
  wallMeta: WallMeta;
  /** null until loaded with the grid or first painted. */
  elevation: number[][] | null;
  bases: Bases;
  redSpawn: ReadonlySet<CellKey>;
  blueSpawn: ReadonlySet<CellKey>;
  /** True once the user painted / erased a Spawn cell: only then are spawns saved. */
  spawnEdited: boolean;
}

/** The Sight Lines area: what Save Sight Lines sends. */
export interface SightEdit {
  /** `"r,c"` -> the keys it sees (`{}` when never computed). */
  sightData: Record<CellKey, CellKey[]>;
  /** False when the server had no Sight lines for this Zone size ("Not computed yet"). */
  computed: boolean;
  baseSights: Record<BaseType, CellPair[]>;
}

/** The editor's whole state: the map, the Zone size and mode, and one slice per area. */
@Injectable()
export class MapEditorStore {
  private readonly maps = inject(MapsService);

  // --- The map and its Zone size ---------------------------------------------------------

  private readonly _map = signal<ArenaMap | null>(null);
  private readonly _status = signal<'loading' | 'ready' | 'not-found' | 'error'>('loading');
  private readonly _loadError = signal<string | null>(null);
  private readonly _zoneSize = signal<number>(DEFAULT_ZONE_SIZE);
  private readonly _mode = signal<EditorMode>('zones');

  readonly map = this._map.asReadonly();
  /** The map and config load: `not-found` for an unknown id, `error` for anything else. */
  readonly status = this._status.asReadonly();
  readonly loadError = this._loadError.asReadonly();
  readonly zoneSize = this._zoneSize.asReadonly();
  readonly mode = this._mode.asReadonly();

  // --- Zones & Bases area ----------------------------------------------------------------

  private readonly _grid = signal<MapZones | null>(null);
  private readonly _gridStatus = signal<LoadStatus>('idle');
  private readonly _zoneEdit = signal<ZoneEdit | null>(null);
  /** The Bases as last loaded or saved: a discarding Zone-size change restores them. */
  private savedBases: Bases = {};

  /** The loaded grid as the server sent it (geometry, auto-detected / saved Cell types). */
  readonly grid = this._grid.asReadonly();
  readonly gridStatus = this._gridStatus.asReadonly();
  readonly zoneEdit = this._zoneEdit.asReadonly();

  // --- Sight Lines area ------------------------------------------------------------------

  private readonly _sight = signal<SightEdit | null>(null);
  private readonly _sightStatus = signal<LoadStatus>('idle');
  private readonly _rankedCells = signal<CellPair[] | null>(null);
  private readonly _strongSpots = signal<CellPair[] | null>(null);

  readonly sight = this._sight.asReadonly();
  readonly sightStatus = this._sightStatus.asReadonly();
  /** High to low LOS count. */
  readonly rankedCells = this._rankedCells.asReadonly();
  readonly strongSpots = this._strongSpots.asReadonly();

  // --- Heatmap area ----------------------------------------------------------------------

  private readonly _heatmap = signal<MapHeatmap | null>(null);
  private readonly _heatmapStatus = signal<LoadStatus>('idle');
  private readonly _heatmapSide = signal<'red' | 'blue' | null>(null);

  readonly heatmap = this._heatmap.asReadonly();
  readonly heatmapStatus = this._heatmapStatus.asReadonly();
  /** The Side filter of the last heatmap load (null: both). */
  readonly heatmapSide = this._heatmapSide.asReadonly();

  // --- Dirty flags -----------------------------------------------------------------------

  private readonly _dirty = signal<Record<DirtyArea, boolean>>({
    zoneConfig: false,
    sightLines: false,
    strongSpots: false,
  });

  readonly zoneConfigDirty = computed(() => this._dirty().zoneConfig);
  readonly sightLinesDirty = computed(() => this._dirty().sightLines);
  readonly strongSpotsDirty = computed(() => this._dirty().strongSpots);
  /** Any unsaved edit: a Zone-size change or leaving the editor asks first. */
  readonly dirty = computed(() => Object.values(this._dirty()).some(Boolean));

  private openSub?: Subscription;
  private gridSub?: Subscription;
  private sightSub?: Subscription;
  private rankingSub?: Subscription;
  private heatmapSub?: Subscription;

  constructor() {
    inject(DestroyRef).onDestroy(() => {
      for (const sub of [
        this.openSub,
        this.gridSub,
        this.sightSub,
        this.rankingSub,
        this.heatmapSub,
      ]) {
        sub?.unsubscribe();
      }
    });
  }

  /** Loads map `id` and its config, then the grid at the confirmed Zone size (or 50). */
  open(id: number): void {
    this.openSub?.unsubscribe();
    this._status.set('loading');
    this._loadError.set(null);
    this._map.set(null);
    this.resetAreas();
    this.openSub = forkJoin([this.maps.get(id), this.maps.zoneConfig(id)]).subscribe({
      next: ([map, config]) => {
        this._map.set(map);
        this.savedBases = toBases(config.bases);
        this._zoneSize.set(map.confirmed_zone_size ?? DEFAULT_ZONE_SIZE);
        this._status.set('ready');
        this.loadGrid();
      },
      error: (error: unknown) => {
        if (error instanceof ApiError && error.status === 404) {
          this._status.set('not-found');
        } else {
          this._loadError.set(`Could not load the map. ${apiMessage(error)}`.trimEnd());
          this._status.set('error');
        }
      },
    });
  }

  setMode(mode: EditorMode): void {
    this._mode.set(mode);
  }

  /**
   * Switches to `size`, discarding every unsaved edit (the caller confirms first while
   * `dirty()`): reloads the grid, and every other area a mode had loaded, at the new size.
   */
  changeZoneSize(size: number): void {
    const reloadSight = this._sightStatus() !== 'idle';
    const reloadHeatmap = this._heatmapStatus() !== 'idle';
    const side = this._heatmapSide();
    this._zoneSize.set(size);
    this.resetAreas();
    this.loadGrid();
    if (reloadSight) this.loadSightLines();
    if (reloadHeatmap) this.loadHeatmap(side ?? undefined);
  }

  setDirty(area: DirtyArea, dirty: boolean): void {
    this._dirty.update((flags) => ({ ...flags, [area]: dirty }));
  }

  // --- Zones & Bases ---------------------------------------------------------------------

  /** Applies `edit` (a pure function) to the Zones & Bases area and marks it dirty. */
  editZones(edit: (state: ZoneEdit) => ZoneEdit): void {
    const state = this._zoneEdit();
    if (!state) return;
    this._zoneEdit.set(edit(state));
    this.setDirty('zoneConfig', true);
  }

  /**
   * Save Configuration: PUTs `body`; on success the area becomes exactly what the server saved
   * and is clean. On failure the edits stay, still dirty, and the `ApiError` is re-thrown.
   */
  saveZoneConfig(body: ZoneConfigInput): Observable<ZoneConfig> {
    const map = this.requireMap();
    return this.maps.replaceZoneConfig(map.id, body).pipe(
      tap((saved) => {
        this.savedBases = toBases(saved.bases);
        this._zoneEdit.update((state) => ({
          zones: saved.zones ?? state?.zones ?? [],
          wallMeta: saved.wall_meta,
          elevation: saved.elevation,
          bases: this.savedBases,
          redSpawn: cellKeys(saved.red_spawn),
          blueSpawn: cellKeys(saved.blue_spawn),
          spawnEdited: false,
        }));
        this._map.update((m) =>
          m ? { ...m, has_confirmed_config: true, confirmed_zone_size: saved.zone_size } : m,
        );
        this.setDirty('zoneConfig', false);
      }),
    );
  }

  // --- Sight Lines -----------------------------------------------------------------------

  /** GETs the Sight lines, ranked Cells and Strong spots for the Zone size (clean after). */
  loadSightLines(): void {
    const map = this.requireMap();
    const size = this._zoneSize();
    this.sightSub?.unsubscribe();
    this._sightStatus.set('loading');
    this.sightSub = forkJoin([
      this.maps.sightLines(map.id, size),
      this.maps.rankedCells(map.id, size),
      this.maps.strongSpots(map.id, size),
    ]).subscribe({
      next: ([sight, ranked, spots]) => {
        this.commitSightLines(sight);
        this._rankedCells.set(ranked.ranked_cells);
        this._strongSpots.set(spots.cells);
        this.setDirty('strongSpots', false);
        this._sightStatus.set('ready');
      },
      error: () => this._sightStatus.set('error'),
    });
  }

  /** Applies `edit` to the Sight Lines area and marks it dirty. */
  editSightLines(edit: (state: SightEdit) => SightEdit): void {
    const state = this._sight();
    if (!state) return;
    this._sight.set(edit(state));
    this.setDirty('sightLines', true);
  }

  /** Takes a server response (a GET, or the last batch saved) as the area's clean state. */
  commitSightLines(saved: SightLines): void {
    this._sight.set({
      sightData: saved.sight_data ?? {},
      computed: saved.sight_data !== null,
      baseSights: saved.base_sights,
    });
    this.setDirty('sightLines', false);
  }

  /** Takes a compute result as the Sight lines (Base sights and dirty flags untouched). */
  commitComputed(result: ComputedSightLines): void {
    this._sight.update((state) =>
      state ? { ...state, sightData: result.sight_data, computed: true } : state,
    );
  }

  /** Reloads the ranked Cells and Strong spots (the server re-derives them on a compute). */
  loadRanking(): void {
    const map = this.requireMap();
    const size = this._zoneSize();
    this.rankingSub?.unsubscribe();
    this.rankingSub = forkJoin([
      this.maps.rankedCells(map.id, size),
      this.maps.strongSpots(map.id, size),
    ]).subscribe(([ranked, spots]) => {
      this._rankedCells.set(ranked.ranked_cells);
      this._strongSpots.set(spots.cells);
      this.setDirty('strongSpots', false);
    });
  }

  /** Applies `edit` to the Strong spots and marks them dirty. */
  editStrongSpots(edit: (cells: CellPair[]) => CellPair[]): void {
    this._strongSpots.update((cells) => edit(cells ?? []));
    this.setDirty('strongSpots', true);
  }

  /** Save Strong spots: PUTs them; clean on success, still dirty (re-thrown) on failure. */
  saveStrongSpots(): Observable<StrongSpots> {
    const map = this.requireMap();
    const body = { zone_size: this._zoneSize(), cells: this._strongSpots() ?? [] };
    return this.maps.replaceStrongSpots(map.id, body).pipe(
      tap((saved) => {
        this._strongSpots.set(saved.cells);
        this.setDirty('strongSpots', false);
      }),
    );
  }

  // --- Heatmap ---------------------------------------------------------------------------

  /** GETs the heatmap for the Zone size, filtered to `side` (omitted: both Sides). */
  loadHeatmap(side?: 'red' | 'blue'): void {
    const map = this.requireMap();
    this.heatmapSub?.unsubscribe();
    this._heatmapSide.set(side ?? null);
    this._heatmapStatus.set('loading');
    this.heatmapSub = this.maps.heatmap(map.id, this._zoneSize(), side).subscribe({
      next: (data) => {
        this._heatmap.set(data);
        this._heatmapStatus.set('ready');
      },
      error: () => {
        this._heatmap.set(null);
        this._heatmapStatus.set('error');
      },
    });
  }

  // --- Internals -------------------------------------------------------------------------

  /** GETs the grid and the Spawn cells at the Zone size into a fresh Zones & Bases area. */
  private loadGrid(): void {
    const map = this.requireMap();
    this.gridSub?.unsubscribe();
    this._gridStatus.set('loading');
    const noSpawns: SpawnCells = { red_spawn: [], blue_spawn: [] };
    this.gridSub = forkJoin([
      this.maps.zones(map.id, this._zoneSize()),
      this.maps.spawnCells(map.id).pipe(catchError(() => of(noSpawns))),
    ]).subscribe({
      next: ([grid, spawns]) => {
        this._grid.set(grid);
        this._zoneEdit.set({
          zones: grid.zones.map((row) => [...row]),
          wallMeta: grid.wall_meta ?? {},
          elevation: grid.elevation ?? null,
          bases: this.savedBases,
          redSpawn: cellKeys(spawns.red_spawn),
          blueSpawn: cellKeys(spawns.blue_spawn),
          spawnEdited: false,
        });
        this._gridStatus.set('ready');
      },
      error: () => this._gridStatus.set('error'),
    });
  }

  /** Drops every area's state and dirty flag (a new map, or a new Zone size). */
  private resetAreas(): void {
    for (const sub of [this.gridSub, this.sightSub, this.rankingSub, this.heatmapSub]) {
      sub?.unsubscribe();
    }
    this._grid.set(null);
    this._gridStatus.set('idle');
    this._zoneEdit.set(null);
    this._sight.set(null);
    this._sightStatus.set('idle');
    this._rankedCells.set(null);
    this._strongSpots.set(null);
    this._heatmap.set(null);
    this._heatmapStatus.set('idle');
    this._dirty.set({ zoneConfig: false, sightLines: false, strongSpots: false });
  }

  private requireMap(): ArenaMap {
    const map = this._map();
    if (!map) throw new Error('MapEditorStore: no map is open');
    return map;
  }
}

/** A config's Bases by type. */
export function toBases(bases: readonly MapBase[]): Bases {
  return Object.fromEntries(bases.map((b) => [b.type, { x_px: b.x_px, y_px: b.y_px }])) as Bases;
}
