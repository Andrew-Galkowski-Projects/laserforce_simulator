import { Component, DestroyRef, computed, effect, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatButtonModule } from '@angular/material/button';
import { BASE_TYPES, BaseType, Facing } from '../../api/maps.models';
import { Cell, cellsInRect } from '../map-editor/grid';
import { CellPointer, MapFrame, useMapLayer } from '../map-editor/map-canvas';
import { MapEditorStore, toBases } from '../map-editor/map-editor.store';
import {
  fillCell,
  paintBases,
  paintCells,
  paintElevation as paintElevationCells,
  paintGridLines,
  paintSpawns as paintSpawnCells,
  strokeCell,
} from '../map-editor/map-paint';
import { apiMessage } from '../../shared/api-message';
import {
  SaveMessage,
  SpawnBrush,
  WallType,
  clearBases,
  clickSpawn,
  paintElevation,
  paintSpawns,
  paintWall,
  placeBase,
  rampElevation,
  saveBody,
  saveMessage,
} from './zones';

const BASE_LABELS: Record<BaseType, string> = {
  red: 'Red Base',
  blue: 'Blue Base',
  neutral_1: 'Neutral 1',
  neutral_2: 'Neutral 2',
  neutral_3: 'Neutral 3',
  neutral_4: 'Neutral 4',
};

/** The wall brushes, in the Django editor's button order. */
const WALL_BRUSHES: { type: WallType; label: string }[] = [
  { type: 4, label: 'Low Wall' },
  { type: 5, label: 'Windowed Wall' },
  { type: 0, label: 'High Wall' },
  { type: 1, label: 'Floor' },
];

const FACINGS: Facing[] = ['N', 'S', 'E', 'W'];

/** The Elevation value picker: exactly 0.0-3.0 in 0.5 steps, as the Django editor. */
const ELEVATION_VALUES = [0, 0.5, 1, 1.5, 2, 2.5, 3];

type ElevationTool = 'paint' | 'ramp';

/** The one armed brush: arming any brush disarms every other (the template's rule). */
type Brush =
  | { kind: 'none' }
  | { kind: 'base'; type: BaseType }
  | { kind: 'wall'; type: WallType }
  | { kind: 'elevation'; tool: ElevationTool }
  | { kind: 'spawn'; brush: SpawnBrush };

const NONE: Brush = { kind: 'none' };

const sameCell = (a: Cell, b: Cell): boolean => a[0] === b[0] && a[1] === b[1];

/**
 * Zones & Bases mode (ANG-06h-3), ported from the Django editor's inline script: place the six
 * Bases, paint walls / Elevation / Spawn cells by click, freehand drag or a Bulk Edit rectangle,
 * and Save Configuration. State lives in `MapEditorStore`; the edits are the pure `zones.ts`.
 */
@Component({
  selector: 'app-zones-bases-mode',
  imports: [MatButtonModule],
  templateUrl: './zones-bases.html',
  styleUrl: './zones-bases.scss',
})
export class ZonesBasesMode {
  protected readonly store = inject(MapEditorStore);

  protected readonly baseTypes = BASE_TYPES;
  protected readonly baseLabels = BASE_LABELS;
  protected readonly wallBrushes = WALL_BRUSHES;
  protected readonly facings = FACINGS;
  protected readonly elevationValues = ELEVATION_VALUES;
  protected readonly elevationTools: { tool: ElevationTool | null; label: string }[] = [
    { tool: null, label: 'None' },
    { tool: 'paint', label: 'Paint' },
    { tool: 'ramp', label: 'Ramp' },
  ];
  protected readonly spawnBrushes: { brush: SpawnBrush | null; label: string }[] = [
    { brush: null, label: 'None' },
    { brush: 'red', label: 'Red Spawn' },
    { brush: 'blue', label: 'Blue Spawn' },
    { brush: 'erase', label: 'Erase Spawn' },
  ];

  protected readonly brush = signal<Brush>(NONE);
  /** The Windowed brush's aperture direction. */
  protected readonly facing = signal<Facing>('N');
  /** The Elevation Paint value. */
  protected readonly elevationValue = signal(0);
  /** The Ramp tool's first Cell, until the second is clicked. */
  protected readonly rampStart = signal<Cell | null>(null);

  protected readonly saving = signal(false);
  /** The last save's success / warning message. */
  protected readonly message = signal<SaveMessage | null>(null);
  protected readonly saveError = signal<string | null>(null);
  private readonly destroyRef = inject(DestroyRef);

  protected readonly selectedBase = computed(() => {
    const b = this.brush();
    return b.kind === 'base' ? b.type : null;
  });
  protected readonly wallType = computed(() => {
    const b = this.brush();
    return b.kind === 'wall' ? b.type : null;
  });
  protected readonly elevationTool = computed(() => {
    const b = this.brush();
    return b.kind === 'elevation' ? b.tool : null;
  });
  protected readonly spawnBrush = computed(() => {
    const b = this.brush();
    return b.kind === 'spawn' ? b.brush : null;
  });
  /** Whether a press starts a paint drag: a wall brush or Elevation Paint. */
  private readonly dragPaints = computed(
    () => this.wallType() !== null || this.elevationTool() === 'paint',
  );

  /** Bulk Edit: a drag with the active brush paints the rectangle it spans, on release. */
  protected readonly bulk = signal(false);
  /** The Bulk Edit rectangle being dragged (drawn highlighted; empty when none). */
  private readonly bulkCells = signal<Cell[]>([]);

  /** A freehand paint drag in progress (Bulk Edit off): where it started, and if it moved. */
  private paintDrag: { start: Cell; moved: boolean } | null = null;
  /** A Bulk Edit rectangle drag in progress. */
  private bulkDrag: { start: Cell; moved: boolean } | null = null;
  /** Swallow the click the browser sends right after a drag's release. */
  private swallowClick = false;

  private readonly canvas = useMapLayer({
    draw: (frame) => this.draw(frame),
    pointer: (p) => this.pointer(p),
    deps: () => [this.rampStart(), this.elevationTool(), this.bulkCells()],
  });

  constructor() {
    effect(() => {
      this.canvas.cursor.set(this.brush().kind === 'none' ? 'default' : 'crosshair');
    });
  }

  protected toggleBase(type: BaseType): void {
    this.arm(this.selectedBase() === type ? NONE : { kind: 'base', type });
  }

  protected armWall(type: WallType | null): void {
    if (type !== null) this.arm({ kind: 'wall', type });
    else if (this.brush().kind === 'wall') this.arm(NONE);
  }

  protected armElevation(tool: ElevationTool | null): void {
    if (tool !== null) this.arm({ kind: 'elevation', tool });
    else if (this.brush().kind === 'elevation') this.arm(NONE);
  }

  protected armSpawn(brush: SpawnBrush | null): void {
    if (brush !== null) this.arm({ kind: 'spawn', brush });
    else if (this.brush().kind === 'spawn') this.arm(NONE);
  }

  protected pickFacing(event: Event): void {
    this.facing.set((event.target as HTMLSelectElement).value as Facing);
  }

  protected pickElevation(event: Event): void {
    this.elevationValue.set(Number((event.target as HTMLSelectElement).value) || 0);
  }

  /** Bulk Edit On / Off; turning it off drops a rectangle in progress. */
  protected toggleBulk(): void {
    this.bulk.update((on) => !on);
    if (!this.bulk()) {
      this.bulkDrag = null;
      this.bulkCells.set([]);
    }
  }

  protected clearBases(): void {
    this.store.editZones(clearBases);
  }

  /**
   * Save Configuration: PUTs the area; the store then holds exactly what the server saved, and
   * the message counts its Bases. A failure shows the server's message and stays unsaved.
   */
  protected save(): void {
    const state = this.store.zoneEdit();
    if (!state || this.saving()) return;
    const zoneSize = this.store.zoneSize();
    this.saving.set(true);
    this.message.set(null);
    this.saveError.set(null);
    this.store
      .saveZoneConfig(saveBody(state, zoneSize))
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (saved) => {
          this.saving.set(false);
          this.message.set(saveMessage(saved.zone_size ?? zoneSize, toBases(saved.bases)));
        },
        error: (error: unknown) => {
          this.saving.set(false);
          this.saveError.set(`Could not save the configuration. ${apiMessage(error)}`.trimEnd());
        },
      });
  }

  /** Arms `brush`, disarming the rest; any brush change forgets the Ramp's first Cell. */
  private arm(brush: Brush): void {
    this.brush.set(brush);
    this.rampStart.set(null);
  }

  /** Ignored while a save is in flight: its response replaces the edit state wholesale. */
  private pointer({ type, cell }: CellPointer): void {
    if (this.saving()) return;
    switch (type) {
      case 'down':
        if (cell) this.down(cell);
        break;
      case 'move':
        if (cell) this.move(cell);
        break;
      case 'up':
        this.up();
        break;
      case 'click':
        if (this.swallowClick) {
          this.swallowClick = false;
          return;
        }
        if (cell) this.click(cell);
        break;
    }
  }

  private down(cell: Cell): void {
    if (this.bulk() && (this.dragPaints() || this.spawnBrush() !== null)) {
      this.bulkDrag = { start: cell, moved: false };
      this.bulkCells.set([cell]);
      return;
    }
    if (!this.dragPaints()) return;
    this.paintDrag = { start: cell, moved: false };
    this.paintCells([cell]);
  }

  private move(cell: Cell): void {
    const bulk = this.bulkDrag;
    if (bulk) {
      if (!sameCell(cell, bulk.start)) bulk.moved = true;
      this.bulkCells.set(cellsInRect(bulk.start, cell));
      return;
    }
    const drag = this.paintDrag;
    if (!drag) return;
    if (!sameCell(cell, drag.start)) drag.moved = true;
    this.paintCells([cell]);
  }

  private up(): void {
    const bulk = this.bulkDrag;
    if (bulk) {
      this.bulkDrag = null;
      const cells = this.bulkCells();
      this.bulkCells.set([]);
      if (bulk.moved && cells.length) {
        this.applyRect(cells);
        this.swallowClick = true;
      }
      return;
    }
    const drag = this.paintDrag;
    if (!drag) return;
    this.paintDrag = null;
    if (drag.moved) this.swallowClick = true;
  }

  /** A released Bulk Edit rectangle: the armed paint or Spawn brush over every Cell in it. */
  private applyRect(cells: Cell[]): void {
    const spawn = this.spawnBrush();
    if (spawn !== null) this.store.editZones((s) => paintSpawns(s, cells, spawn));
    else this.paintCells(cells);
  }

  private click(cell: Cell): void {
    const brush = this.brush();
    if (brush.kind === 'wall' || (brush.kind === 'elevation' && brush.tool === 'paint')) {
      this.paintCells([cell]);
    } else if (brush.kind === 'elevation') {
      const start = this.rampStart();
      if (!start) {
        this.rampStart.set(cell);
      } else {
        this.store.editZones((s) => rampElevation(s, start, cell));
        this.rampStart.set(null);
      }
    } else if (brush.kind === 'spawn') {
      this.store.editZones((s) => clickSpawn(s, cell, brush.brush));
    } else if (brush.kind === 'base') {
      const cellSize = this.store.grid()?.cell_size;
      if (cellSize) this.store.editZones((s) => placeBase(s, brush.type, cell, cellSize));
    }
  }

  /** Applies the armed paint brush (wall or Elevation Paint) to `cells`. */
  private paintCells(cells: Cell[]): void {
    const type = this.wallType();
    if (type !== null) {
      const facing = this.facing();
      this.store.editZones((s) => paintWall(s, cells, type, facing));
    } else if (this.elevationTool() === 'paint') {
      const value = this.elevationValue();
      this.store.editZones((s) => paintElevation(s, cells, value));
    }
  }

  /** The template's `renderZonesAndBases`, layer by layer. */
  private draw(frame: MapFrame): void {
    const state = this.store.zoneEdit();
    paintCells(frame, { wallMeta: state?.wallMeta });
    if (state?.elevation) paintElevationCells(frame, state.elevation);
    const rampStart = this.rampStart();
    if (rampStart && this.elevationTool() === 'ramp') {
      strokeCell(frame, rampStart, frame.palette.rampAnchor, 3);
    }
    for (const cell of this.bulkCells()) {
      frame.ctx.globalAlpha = 0.35;
      fillCell(frame, cell, frame.palette.losDrag);
      frame.ctx.globalAlpha = 1;
    }
    paintGridLines(frame);
    if (state) {
      paintSpawnCells(frame, state.redSpawn, state.blueSpawn);
      paintBases(frame, state.bases);
    }
  }
}
