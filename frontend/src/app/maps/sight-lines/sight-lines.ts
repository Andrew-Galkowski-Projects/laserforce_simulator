import { Component, computed, inject, signal } from '@angular/core';
import { MatButton } from '@angular/material/button';
import { MatProgressSpinner } from '@angular/material/progress-spinner';
import { BaseType, CellKey, SightLinesInput, ZoneType } from '../../api/maps.models';
import { MapsService } from '../../api/maps.service';
import { Cell, baseCell, cellKey, keyCells } from '../map-editor/grid';
import { CellPointer, MapFrame, useMapLayer } from '../map-editor/map-canvas';
import { MapEditorStore } from '../map-editor/map-editor.store';
import {
  fillCell,
  paintBases,
  paintCells,
  paintElevation,
  paintGridLines,
  strokeCell,
} from '../map-editor/map-paint';
import { apiMessage } from '../shared/api-message';
import {
  bulkToggleSight,
  bulkToggleStrongSpots,
  isSightWall,
  rectKeys,
  sightBatches,
  sightOf,
  toggleBaseSight,
  toggleSight,
  toggleStrongSpot,
} from './sight-edit';

/** A failed request's text when the server sent no message. */
const SERVER_ERROR = 'Server error';

/** What the Sight Lines mode shows: one Cell's LOS, one Base's tagging Cells, or a ranking. */
export type SightView = 'zone' | BaseType | 'high_los' | 'heavy_spots';

/** The view picker's options, exactly the Django editor's. */
export const SIGHT_VIEWS: readonly { value: SightView; label: string }[] = [
  { value: 'zone', label: 'Zone (click map)' },
  { value: 'red', label: 'Red Base' },
  { value: 'blue', label: 'Blue Base' },
  { value: 'neutral_1', label: 'Neutral 1' },
  { value: 'neutral_2', label: 'Neutral 2' },
  { value: 'neutral_3', label: 'Neutral 3' },
  { value: 'neutral_4', label: 'Neutral 4' },
  { value: 'high_los', label: 'High-LOS Heatmap' },
  { value: 'heavy_spots', label: 'Heavy Strong Spots' },
];

/** A save / compute outcome under the controls. */
interface Alert {
  tone: 'info' | 'success' | 'error';
  text: string;
}

/**
 * Sight Lines mode (ANG-06h-4), ported from the Django editor's Sight Lines branch: the view
 * picker, Zone-view LOS selection and toggling, Base sights, the High-LOS ranking, Heavy Strong
 * spots, Compute, and the batched Save Sight Lines.
 */
@Component({
  selector: 'app-sight-lines-mode',
  imports: [MatButton, MatProgressSpinner],
  templateUrl: './sight-lines.html',
  styleUrl: './sight-lines.scss',
})
export class SightLinesMode {
  protected readonly store = inject(MapEditorStore);
  private readonly maps = inject(MapsService);
  protected readonly views = SIGHT_VIEWS;
  protected readonly view = signal<SightView>('zone');
  /** Zone view's selected Cell. */
  private readonly selected = signal<Cell | null>(null);
  /** The Sight Lines Bulk Edit toggle (drag rectangles). */
  protected readonly bulk = signal(false);
  /** The drag rectangle's Cells, highlighted while dragging. */
  private readonly dragKeys = signal<ReadonlySet<CellKey>>(new Set());
  private drag: { start: Cell; moved: boolean } | null = null;
  /** Set by a drag's release: the click that follows it is not a Cell click. */
  private swallowClick = false;
  protected readonly saving = signal(false);
  protected readonly computing = signal(false);
  /** A compute's progress / outcome, shown in place of the view's hint until the view changes. */
  private readonly notice = signal<string | null>(null);
  protected readonly alert = signal<Alert | null>(null);
  private readonly canvas = useMapLayer({
    draw: (frame) => this.draw(frame),
    pointer: (p) => this.onPointer(p),
    deps: () => [this.view(), this.selected(), this.dragKeys()],
  });

  constructor() {
    this.canvas.cursor.set('pointer');
    // The store keeps a loaded area across mode toggles (and reloads it on a Zone-size change);
    // a failed load is retried on re-entry.
    const status = this.store.sightStatus();
    if (status === 'idle' || status === 'error') this.store.loadSightLines();
  }

  /** The hint beside the controls, as the Django editor words it per view. */
  protected readonly hint = computed(() => {
    const notice = this.notice();
    if (notice) return notice;
    const view = this.view();
    if (view === 'zone') {
      return this.store.sight()?.computed === false
        ? "Not computed yet for this zone size. Click 'Compute Sight Lines'."
        : 'Click a zone cell to view its sight lines. Enable Bulk Edit to drag-select multiple cells.';
    }
    if (view === 'high_los') return 'Heat map: gold = highest LOS count, teal = lowest. Read-only.';
    if (view === 'heavy_spots') {
      return 'Click cells to add/remove Heavy strong-spot candidates. Enable Bulk Edit to drag-select a rectangle.';
    }
    const label = SIGHT_VIEWS.find((v) => v.value === view)?.label;
    return `Showing cells that can tag ${label}. Click to add/remove.`;
  });

  protected pickView(event: Event): void {
    const view = (event.target as HTMLSelectElement).value as SightView;
    this.view.set(view);
    this.notice.set(null);
    this.selected.set(null);
    this.endDrag();
    // Only the Zone and Strong-spots views drag rectangles.
    if (view !== 'zone' && view !== 'heavy_spots' && this.bulk()) this.toggleBulk();
  }

  protected saveSightLines(): void {
    const map = this.store.map();
    const sight = this.store.sight();
    if (!map || !sight) return;
    const batches = sightBatches(this.store.zoneSize(), sight.sightData, sight.baseSights);
    this.saving.set(true);
    this.alert.set(null);
    this.sendBatch(map.id, batches, 0);
  }

  /** Compute Sight Lines: the synchronous server computation (sending unsaved Spawn edits). */
  protected compute(): void {
    const map = this.store.map();
    if (!map) return;
    const edit = this.store.zoneEdit();
    const spawns = edit?.spawnEdited
      ? { red_spawn: keyCells(edit.redSpawn), blue_spawn: keyCells(edit.blueSpawn) }
      : {};
    this.computing.set(true);
    this.alert.set(null);
    this.notice.set('Computing… this may take a moment for large maps.');
    this.maps.computeSightLines(map.id, this.store.zoneSize(), spawns).subscribe({
      next: (result) => {
        this.computing.set(false);
        this.store.commitComputed(result);
        this.selected.set(null);
        this.notice.set(
          'Done! Click a zone cell to see its sight lines. Enable Bulk Edit to drag-select.',
        );
        this.store.loadRanking();
      },
      error: (error: unknown) => {
        this.computing.set(false);
        this.notice.set(null);
        this.alert.set({ tone: 'error', text: `Error: ${apiMessage(error, SERVER_ERROR)}` });
      },
    });
  }

  /** Save Heavy Strong Spots (the save button in the Strong-spots view). */
  protected saveStrongSpots(): void {
    this.saving.set(true);
    this.alert.set(null);
    this.store.saveStrongSpots().subscribe({
      next: (saved) => {
        this.saving.set(false);
        this.alert.set({
          tone: 'success',
          text: `Heavy strong spots saved! (${saved.cells.length} cells)`,
        });
      },
      error: (error: unknown) => {
        this.saving.set(false);
        this.alert.set({ tone: 'error', text: `Error: ${apiMessage(error, SERVER_ERROR)}` });
      },
    });
  }

  /** Sends batch `i` (PUT for the first, PATCH after), then the next; the last one commits. */
  private sendBatch(id: number, batches: SightLinesInput[], i: number): void {
    const total = batches.length;
    if (total > 1) this.alert.set({ tone: 'info', text: `Saving... (${i + 1}/${total})` });
    const request =
      i === 0
        ? this.maps.replaceSightLines(id, batches[i])
        : this.maps.mergeSightLines(id, batches[i]);
    request.subscribe({
      next: (saved) => {
        if (i + 1 < total) {
          this.sendBatch(id, batches, i + 1);
          return;
        }
        this.store.commitSightLines(saved);
        this.saving.set(false);
        this.alert.set({ tone: 'success', text: 'Sight lines saved!' });
      },
      error: (error: unknown) => {
        this.saving.set(false);
        this.alert.set({
          tone: 'error',
          text: `Saved ${i} of ${total} batches — retry. ${apiMessage(error, SERVER_ERROR)}`,
        });
      },
    });
  }

  protected toggleBulk(): void {
    const on = !this.bulk();
    this.bulk.set(on);
    if (!on) this.endDrag();
    this.canvas.cursor.set(on ? 'crosshair' : 'pointer');
  }

  private onPointer({ type, cell }: CellPointer): void {
    if (this.saving()) return;
    if (type === 'down') this.startDrag(cell);
    else if (type === 'move') this.moveDrag(cell);
    else if (type === 'up') this.releaseDrag();
    else this.click(cell);
  }

  /** A drag rectangle: Bulk Edit on, in Zone view with a selection or in Strong-spots view. */
  private startDrag(cell: Cell | null): void {
    if (!cell || !this.bulk()) return;
    const view = this.view();
    if (view === 'zone' ? !this.selected() : view !== 'heavy_spots') return;
    this.drag = { start: cell, moved: false };
    this.dragKeys.set(new Set([cellKey(cell)]));
  }

  private moveDrag(cell: Cell | null): void {
    const drag = this.drag;
    if (!drag || !cell) return;
    if (cellKey(cell) !== cellKey(drag.start)) drag.moved = true;
    this.dragKeys.set(rectKeys(this.zones(), drag.start, cell));
  }

  private releaseDrag(): void {
    const drag = this.drag;
    if (!drag) return;
    const keys = this.dragKeys();
    const selected = this.selected();
    if (drag.moved && keys.size) {
      if (this.view() === 'zone' && selected) {
        this.store.editSightLines((s) => ({
          ...s,
          sightData: bulkToggleSight(s.sightData, selected, keys),
        }));
      } else if (this.view() === 'heavy_spots') {
        this.store.editStrongSpots((spots) => bulkToggleStrongSpots(spots, keys));
      }
      // The click the browser fires after this release is not a Cell click.
      this.swallowClick = true;
    }
    this.endDrag();
  }

  private endDrag(): void {
    this.drag = null;
    this.dragKeys.set(new Set());
  }

  private click(cell: Cell | null): void {
    if (this.drag) return;
    if (this.swallowClick) {
      this.swallowClick = false;
      return;
    }
    if (!cell) return;
    const view = this.view();
    if (view === 'zone') {
      this.clickZone(cell);
    } else if (view === 'heavy_spots') {
      this.store.editStrongSpots((spots) => toggleStrongSpot(spots, this.zones(), cell));
    } else if (view !== 'high_los') {
      this.store.editSightLines((s) => ({
        ...s,
        baseSights: toggleBaseSight(s.baseSights, this.zones(), view, cell),
      }));
    }
  }

  /** The Cell types as edited. */
  private zones(): ZoneType[][] {
    return this.store.zoneEdit()?.zones ?? [];
  }

  private clickZone(cell: Cell): void {
    const zones = this.zones();
    if (isSightWall(zones, cell)) return;
    const selected = this.selected();
    if (!selected) {
      this.selected.set(cell);
    } else if (cellKey(selected) === cellKey(cell)) {
      this.selected.set(null);
    } else {
      this.store.editSightLines((s) => ({
        ...s,
        sightData: toggleSight(s.sightData, zones, selected, cell),
      }));
    }
  }

  /** Each view's overlay over the Cell types, then Elevation, grid lines and Bases on top. */
  private draw(frame: MapFrame): void {
    const edit = this.store.zoneEdit();
    paintCells(frame, { wallMeta: edit?.wallMeta });
    const view = this.view();
    if (view === 'zone') this.drawZone(frame);
    else if (view === 'high_los') drawRanking(frame, this.store.rankedCells() ?? []);
    else if (view === 'heavy_spots') this.drawStrongSpots(frame);
    else this.drawBaseSight(frame, view);
    if (edit?.elevation) paintElevation(frame, edit.elevation);
    paintGridLines(frame);
    if (edit) paintBases(frame, edit.bases);
  }

  /** The selection, the drag rectangle, and the selection's visible / blocked Cells. */
  private drawZone(frame: MapFrame): void {
    const sight = this.store.sight();
    const selected = this.selected();
    if (!selected || !sight) return;
    // One colour per Cell, by priority: selected, dragged, visible, blocked.
    const { visible, blocked } = sightOf(sight.sightData, frame.zones, selected);
    const dragged = this.dragKeys();
    const self = cellKey(selected);
    const p = frame.palette;
    fillKeys(
      frame,
      [...blocked].filter((k) => !dragged.has(k)),
      p.losBlocked,
    );
    fillKeys(
      frame,
      [...visible].filter((k) => !dragged.has(k)),
      p.losVisible,
    );
    fillKeys(
      frame,
      [...dragged].filter((k) => k !== self),
      p.losDrag,
    );
    fillKeys(frame, [self], p.losSelected);
  }

  /** The Strong spots, and the drag rectangle over them. */
  private drawStrongSpots(frame: MapFrame): void {
    fillKeys(frame, (this.store.strongSpots() ?? []).map(cellKey), frame.palette.strongSpot);
    fillKeys(frame, this.dragKeys(), frame.palette.losDrag);
  }

  /** The Cells that can tag Base `type`, the matching team's Spawn cells, and its own Cell. */
  private drawBaseSight(frame: MapFrame, type: BaseType): void {
    const cells = this.store.sight()?.baseSights[type] ?? [];
    fillKeys(frame, cells.map(cellKey), frame.palette.baseSight);
    const edit = this.store.zoneEdit();
    if (edit && type === 'red') fillKeys(frame, edit.redSpawn, frame.palette.spawnRed, 2);
    if (edit && type === 'blue') fillKeys(frame, edit.blueSpawn, frame.palette.spawnBlue, 2);
    const base = edit?.bases[type];
    if (base) strokeCell(frame, baseCell(base, frame.cellSize), frame.palette.baseOutline, 3);
  }
}

/** The High-LOS ranking: the top-ranked Cells in `highLos`, fading to `highLosLow`. */
function drawRanking(frame: MapFrame, ranked: readonly Cell[]): void {
  const last = Math.max(1, ranked.length - 1);
  ranked.forEach((cell, rank) => {
    fillCell(frame, cell, frame.palette.highLosLow);
    frame.ctx.globalAlpha = 1 - rank / last;
    fillCell(frame, cell, frame.palette.highLos);
    frame.ctx.globalAlpha = 1;
  });
}

/** Fills each on-grid Cell of `keys`, `inset` px in from its edges. */
function fillKeys(frame: MapFrame, keys: Iterable<CellKey>, colour: string, inset = 0): void {
  for (const cell of keyCells(keys)) fillCell(frame, cell, colour, inset);
}
