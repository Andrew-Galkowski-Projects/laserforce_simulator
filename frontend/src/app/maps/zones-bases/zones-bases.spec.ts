import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { MATERIAL_ANIMATIONS } from '@angular/material/core';
import { By } from '@angular/platform-browser';
import { provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { MapZones, ZoneConfig, ZoneConfigInput } from '../../api/maps.models';
import { signIn } from '../../api/testing/auth-fixtures';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';
import { stubCanvasContext } from '../../matches/testing/fake-canvas';
import { Cell } from '../map-editor/grid';
import { MapCanvas } from '../map-editor/map-canvas';
import { MapEditor } from '../map-editor/map-editor';
import { MapEditorStore } from '../map-editor/map-editor.store';
import { MapPalette } from '../map-editor/map-palette';
import {
  answerEditorLoads,
  editorMap,
  expectMapRequest,
  flushData,
  flushError,
  mapZones,
  releaseModeLoads,
  zoneConfig,
} from '../testing/editor-fixtures';
import { releaseMapsLoads } from '../testing/maps-loads';
import { ZonesBasesMode } from './zones-bases';

const png = (): Blob => new Blob([new Uint8Array([137, 80, 78, 71])], { type: 'image/png' });

/** The fixture map's opening config, with no Bases placed. */
const noBases = (): ZoneConfig => zoneConfig({ bases: [] });

describe('Zones & Bases mode', () => {
  let http: HttpTestingController;
  let harness: RouterTestingHarness;
  let restoreCanvas: () => void;
  const url = URL as unknown as Record<string, unknown>;
  const before = { create: url['createObjectURL'], revoke: url['revokeObjectURL'] };

  beforeEach(() => {
    url['createObjectURL'] = vi.fn(() => 'blob:image');
    url['revokeObjectURL'] = vi.fn();
    restoreCanvas = stubCanvasContext();
    TestBed.configureTestingModule({
      providers: [
        provideRouter(routes),
        provideHttpClient(withInterceptors([authInterceptor])),
        provideHttpClientTesting(),
        { provide: MATERIAL_ANIMATIONS, useValue: { animationsDisabled: true } },
      ],
    });
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    releaseMapsLoads();
    releaseModeLoads();
    http.verify();
    restoreCanvas();
    url['createObjectURL'] = before.create;
    url['revokeObjectURL'] = before.revoke;
  });

  const page = (): HTMLElement => harness.routeNativeElement as HTMLElement;
  const text = (el: Element | null | undefined = page()): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const stable = (): Promise<void> => harness.fixture.whenStable();
  const editor = () => harness.fixture.debugElement.query(By.directive(MapEditor));
  const store = (): MapEditorStore => editor().injector.get(MapEditorStore);
  const mapCanvas = (): MapCanvas => editor().injector.get(MapCanvas);
  const mode = (): HTMLElement =>
    harness.fixture.debugElement.query(By.directive(ZonesBasesMode)).nativeElement as HTMLElement;
  const overlay = (): HTMLCanvasElement =>
    page().querySelector<HTMLCanvasElement>('canvas.me-overlay') as HTMLCanvasElement;
  const zoneEdit = () => store().zoneEdit()!;

  /** The button labelled `label` in the group labelled `group` (or anywhere in the mode). */
  function button(label: string, group?: string): HTMLButtonElement {
    const scope = group ? mode().querySelector(`[role="group"][aria-label="${group}"]`) : mode();
    const found = [...(scope?.querySelectorAll('button') ?? [])].find((b) => text(b) === label);
    if (!found) throw new Error(`No button "${label}"${group ? ` in ${group}` : ''}`);
    return found;
  }

  async function press(label: string, group?: string): Promise<void> {
    button(label, group).click();
    await stable();
  }

  const pressed = (label: string, group?: string): string | null =>
    button(label, group).getAttribute('aria-pressed');

  async function pick(selector: string, value: string): Promise<void> {
    const select = mode().querySelector<HTMLSelectElement>(selector) as HTMLSelectElement;
    select.value = value;
    select.dispatchEvent(new Event('change'));
    await stable();
  }

  /** Opens the editor on the 3 x 4, 40 px fixture map in Zones & Bases mode. */
  async function open(config: ZoneConfig = noBases(), zones?: MapZones): Promise<void> {
    await signIn();
    harness = await RouterTestingHarness.create();
    const nav = harness.navigateByUrl('/maps/4/editor');
    await stable();
    answerEditorLoads(editorMap(), config, zones);
    await nav;
    await stable();
    expectMapRequest(4, 'processed-image/').flush(png());
    expectMapRequest(4, 'image/').flush(png());
    await stable();
    // Drawn 1:1 on screen: Cell [r, c] is at (c * 40 + 20, r * 40 + 20).
    overlay().getBoundingClientRect = () =>
      ({ left: 0, top: 0, width: 160, height: 120, right: 160, bottom: 120 }) as DOMRect;
  }

  function fire(type: 'pointerdown' | 'pointermove' | 'pointerup' | 'click', [r, c]: Cell): void {
    overlay().dispatchEvent(
      new MouseEvent(type, { clientX: c * 40 + 20, clientY: r * 40 + 20, bubbles: true }),
    );
  }

  /** A click on `cell` as a browser sends it: down, up, click. */
  async function clickCell(cell: Cell): Promise<void> {
    fire('pointerdown', cell);
    fire('pointerup', cell);
    fire('click', cell);
    await stable();
  }

  describe('Bases', () => {
    it('offers a button per Base, and Clear All', async () => {
      await open();
      const group = mode().querySelector('[role="group"][aria-label="Place base"]');
      expect([...(group?.querySelectorAll('button') ?? [])].map((b) => text(b))).toEqual([
        'Red Base',
        'Blue Base',
        'Neutral 1',
        'Neutral 2',
        'Neutral 3',
        'Neutral 4',
      ]);
      expect(button('Clear All Bases')).toBeTruthy();
    });

    it('places the selected Base on the clicked Cell, moves it, and removes it on its Cell', async () => {
      await open();
      await press('Red Base', 'Place base');
      expect(pressed('Red Base', 'Place base')).toBe('true');
      expect(mapCanvas().cursor()).toBe('crosshair');
      await clickCell([1, 2]);
      expect(zoneEdit().bases).toEqual({ red: { x_px: 100, y_px: 60 } });
      expect(store().zoneConfigDirty()).toBe(true);
      await clickCell([2, 0]);
      expect(zoneEdit().bases).toEqual({ red: { x_px: 20, y_px: 100 } });
      await clickCell([2, 0]);
      expect(zoneEdit().bases).toEqual({});
    });

    it('does nothing on a click with no Base or brush armed', async () => {
      await open();
      await clickCell([1, 1]);
      expect(zoneEdit().bases).toEqual({});
      expect(store().zoneConfigDirty()).toBe(false);
    });

    it('disarms the Base when its button is pressed again', async () => {
      await open();
      await press('Blue Base', 'Place base');
      await press('Blue Base', 'Place base');
      expect(pressed('Blue Base', 'Place base')).toBe('false');
      expect(mapCanvas().cursor()).toBe('default');
      await clickCell([1, 1]);
      expect(zoneEdit().bases).toEqual({});
    });

    it('Clear All removes every Base', async () => {
      await open(zoneConfig());
      expect(Object.keys(zoneEdit().bases).sort()).toEqual(['blue', 'red']);
      await press('Clear All Bases');
      expect(zoneEdit().bases).toEqual({});
      expect(store().zoneConfigDirty()).toBe(true);
    });
  });

  describe('wall brushes', () => {
    const WALL = 'Paint wall';

    it('offers None, Low, Windowed, High and Floor, None armed first', async () => {
      await open();
      const group = mode().querySelector(`[role="group"][aria-label="${WALL}"]`);
      expect([...(group?.querySelectorAll('button') ?? [])].map((b) => text(b))).toEqual([
        'None',
        'Low Wall',
        'Windowed Wall',
        'High Wall',
        'Floor',
      ]);
      expect(pressed('None', WALL)).toBe('true');
    });

    it('paints the clicked Cell with the armed wall type', async () => {
      await open();
      await press('Low Wall', WALL);
      expect(mapCanvas().cursor()).toBe('crosshair');
      await clickCell([1, 1]);
      expect(zoneEdit().zones[1]).toEqual([1, 4, 1, 1]);
      await press('Floor', WALL);
      await clickCell([0, 0]);
      expect(zoneEdit().zones[0][0]).toBe(1);
      expect(store().zoneConfigDirty()).toBe(true);
    });

    it('shows the N / S / E / W facing picker only for the Windowed brush, and stores it', async () => {
      await open();
      expect(mode().querySelector('select#zb-facing')).toBeNull();
      await press('Windowed Wall', WALL);
      const facing = mode().querySelector<HTMLSelectElement>('select#zb-facing');
      expect([...(facing?.options ?? [])].map((o) => o.value)).toEqual(['N', 'S', 'E', 'W']);
      await pick('select#zb-facing', 'W');
      await clickCell([2, 1]);
      expect(zoneEdit().zones[2][1]).toBe(5);
      expect(zoneEdit().wallMeta).toEqual({ '2,1': { facing: 'W' } });
      await press('High Wall', WALL);
      expect(mode().querySelector('select#zb-facing')).toBeNull();
      await clickCell([2, 1]);
      expect(zoneEdit().wallMeta).toEqual({});
    });

    it('paints every Cell a drag passes over (Bulk Edit off)', async () => {
      await open();
      await press('High Wall', WALL);
      fire('pointerdown', [1, 0]);
      fire('pointermove', [1, 1]);
      fire('pointermove', [2, 1]);
      fire('pointermove', [2, 2]);
      fire('pointerup', [2, 2]);
      await stable();
      expect(zoneEdit().zones).toEqual([
        [0, 1, 1, 1],
        [0, 0, 1, 1],
        [1, 0, 0, 1],
      ]);
    });

    it('stops painting on release, and swallows the click right after a drag', async () => {
      await open();
      await press('Low Wall', WALL);
      fire('pointerdown', [1, 1]);
      fire('pointermove', [1, 2]);
      fire('pointerup', [1, 2]);
      fire('click', [2, 3]);
      fire('pointermove', [2, 2]);
      await stable();
      expect(zoneEdit().zones[2]).toEqual([1, 1, 1, 1]);
      // Only that one click: the next one paints.
      await clickCell([2, 3]);
      expect(zoneEdit().zones[2]).toEqual([1, 1, 1, 4]);
    });

    it('ignores pointer moves off the grid', async () => {
      await open();
      await press('Low Wall', WALL);
      fire('pointerdown', [1, 1]);
      fire('pointermove', [1, 9]);
      fire('pointerup', [1, 9]);
      await stable();
      expect(zoneEdit().zones[1]).toEqual([1, 4, 1, 1]);
    });

    it('arming a wall brush disarms the Base, and arming a Base disarms the wall brush', async () => {
      await open();
      await press('Red Base', 'Place base');
      await press('Low Wall', WALL);
      expect(pressed('Red Base', 'Place base')).toBe('false');
      await clickCell([1, 1]);
      expect(zoneEdit().bases).toEqual({});
      await press('Red Base', 'Place base');
      expect(pressed('None', WALL)).toBe('true');
      await clickCell([2, 2]);
      expect(zoneEdit().zones[2][2]).toBe(1);
      expect(zoneEdit().bases).toEqual({ red: { x_px: 100, y_px: 100 } });
    });
  });

  describe('Elevation', () => {
    const ELEV = 'Elevation';
    const PAINT_HINT = 'Drag to paint elevation.';
    const RAMP_HINT = 'Paint elevation on two cells first, then click each to fill the rectangle';

    it('offers None / Paint / Ramp; Paint shows exactly 0.0-3.0 in 0.5 steps and its hint', async () => {
      await open();
      const group = mode().querySelector(`[role="group"][aria-label="${ELEV}"]`);
      expect([...(group?.querySelectorAll('button') ?? [])].map((b) => text(b))).toEqual([
        'None',
        'Paint',
        'Ramp',
      ]);
      expect(mode().querySelector('select#zb-elevation')).toBeNull();
      expect(text(mode())).not.toContain(PAINT_HINT);
      await press('Paint', ELEV);
      const values = mode().querySelector<HTMLSelectElement>('select#zb-elevation');
      expect([...(values?.options ?? [])].map((o) => [o.value, text(o)])).toEqual([
        ['0', '0.0'],
        ['0.5', '0.5'],
        ['1', '1.0'],
        ['1.5', '1.5'],
        ['2', '2.0'],
        ['2.5', '2.5'],
        ['3', '3.0'],
      ]);
      expect(text(mode())).toContain(PAINT_HINT);
      expect(text(mode())).toContain('Wall height: add height to wall_meta via JSON.');
      expect(text(mode())).not.toContain(RAMP_HINT);
    });

    it('paints the chosen value by click and by drag', async () => {
      await open();
      await press('Paint', ELEV);
      await pick('select#zb-elevation', '2.5');
      await clickCell([0, 1]);
      expect(zoneEdit().elevation?.[0]).toEqual([0, 2.5, 0, 0]);
      await pick('select#zb-elevation', '1');
      fire('pointerdown', [2, 0]);
      fire('pointermove', [2, 1]);
      fire('pointerup', [2, 1]);
      fire('click', [2, 1]);
      await stable();
      expect(zoneEdit().elevation?.[2]).toEqual([1, 1, 0, 0]);
      expect(store().zoneConfigDirty()).toBe(true);
    });

    it('Ramp fills the rectangle between the two Cells clicked, then starts over', async () => {
      await open();
      await press('Paint', ELEV);
      await pick('select#zb-elevation', '3');
      await clickCell([1, 3]);
      await press('Ramp', ELEV);
      expect(text(mode())).toContain(RAMP_HINT);
      expect(mode().querySelector('select#zb-elevation')).toBeNull();
      await clickCell([1, 0]);
      expect(zoneEdit().elevation?.[1]).toEqual([0, 0, 0, 3]);
      await clickCell([1, 3]);
      expect(zoneEdit().elevation?.[1]).toEqual([0, 1, 2, 3]);
      // A third click starts a new ramp rather than ramping again from [1, 0] over row 1.
      await clickCell([2, 3]);
      expect(zoneEdit().elevation?.[1]).toEqual([0, 1, 2, 3]);
    });

    it('Ramp forgets its first Cell when the brush changes', async () => {
      await open();
      await press('Paint', ELEV);
      await pick('select#zb-elevation', '2');
      await clickCell([0, 0]);
      await press('Ramp', ELEV);
      await clickCell([0, 0]);
      await press('Low Wall', 'Paint wall');
      await press('Ramp', ELEV);
      await clickCell([0, 3]);
      expect(zoneEdit().elevation?.[0]).toEqual([2, 0, 0, 0]);
    });

    it('arming it disarms the wall brush and the Base, and vice versa', async () => {
      await open();
      await press('Low Wall', 'Paint wall');
      await press('Paint', ELEV);
      expect(pressed('None', 'Paint wall')).toBe('true');
      await press('Red Base', 'Place base');
      expect(pressed('None', ELEV)).toBe('true');
      await press('Paint', ELEV);
      expect(pressed('Red Base', 'Place base')).toBe('false');
      await clickCell([1, 1]);
      expect(zoneEdit().zones[1][1]).toBe(1);
      expect(zoneEdit().bases).toEqual({});
    });
  });

  describe('Spawn brushes', () => {
    const SPAWN = 'Spawn brush';
    const keys = (set: ReadonlySet<string>): string[] => [...set].sort();

    it('offers None / Red / Blue / Erase with its hint, and shows the loaded Spawn cells', async () => {
      await open();
      const group = mode().querySelector(`[role="group"][aria-label="${SPAWN}"]`);
      expect([...(group?.querySelectorAll('button') ?? [])].map((b) => text(b))).toEqual([
        'None',
        'Red Spawn',
        'Blue Spawn',
        'Erase Spawn',
      ]);
      expect(text(mode())).toContain(
        'Select spawn brush then click cells. Auto-generated on Compute Sight Lines; manual edits override.',
      );
      // SPAWNS, answered with the grid.
      expect(keys(zoneEdit().redSpawn)).toEqual(['1,0']);
      expect(keys(zoneEdit().blueSpawn)).toEqual(['2,3']);
      expect(zoneEdit().spawnEdited).toBe(false);
    });

    it('paints, toggles and erases Spawn cells by click, marking them edited', async () => {
      await open();
      await press('Red Spawn', SPAWN);
      expect(mapCanvas().cursor()).toBe('crosshair');
      await clickCell([2, 3]);
      expect(keys(zoneEdit().redSpawn)).toEqual(['1,0', '2,3']);
      expect(keys(zoneEdit().blueSpawn)).toEqual([]);
      expect(zoneEdit().spawnEdited).toBe(true);
      await clickCell([1, 0]);
      expect(keys(zoneEdit().redSpawn)).toEqual(['2,3']);
      await press('Erase Spawn', SPAWN);
      await clickCell([2, 3]);
      expect(keys(zoneEdit().redSpawn)).toEqual([]);
    });

    it('paints by click only: a drag with Bulk Edit off paints nothing it passes over', async () => {
      await open();
      await press('Blue Spawn', SPAWN);
      fire('pointerdown', [0, 1]);
      fire('pointermove', [0, 2]);
      fire('pointerup', [0, 2]);
      await stable();
      expect(keys(zoneEdit().blueSpawn)).toEqual(['2,3']);
    });

    it('arming a Spawn brush disarms the wall, Elevation and Base brushes, and vice versa', async () => {
      await open();
      await press('Windowed Wall', 'Paint wall');
      await press('Red Spawn', SPAWN);
      expect(pressed('None', 'Paint wall')).toBe('true');
      await press('Paint', 'Elevation');
      expect(pressed('None', SPAWN)).toBe('true');
      await press('Blue Spawn', SPAWN);
      expect(pressed('None', 'Elevation')).toBe('true');
      await press('Neutral 1', 'Place base');
      expect(pressed('None', SPAWN)).toBe('true');
      await press('Erase Spawn', SPAWN);
      expect(pressed('Neutral 1', 'Place base')).toBe('false');
      await press('Low Wall', 'Paint wall');
      expect(pressed('None', SPAWN)).toBe('true');
    });

    it("a group's None disarms only its own brush", async () => {
      await open();
      await press('Red Spawn', SPAWN);
      await press('None', 'Paint wall');
      expect(pressed('Red Spawn', SPAWN)).toBe('true');
      await press('None', SPAWN);
      expect(pressed('Red Spawn', SPAWN)).toBe('false');
      expect(mapCanvas().cursor()).toBe('default');
    });
  });

  describe('Bulk Edit', () => {
    const keys = (set: ReadonlySet<string>): string[] => [...set].sort();

    async function bulkOn(): Promise<void> {
      await press('Bulk Edit: Off');
    }

    function drag(from: Cell, to: Cell): void {
      fire('pointerdown', from);
      fire('pointermove', to);
      fire('pointerup', to);
      fire('click', to);
    }

    it('toggles between Off and On', async () => {
      await open();
      expect(pressed('Bulk Edit: Off')).toBe('false');
      await bulkOn();
      expect(pressed('Bulk Edit: On')).toBe('true');
      await press('Bulk Edit: On');
      expect(pressed('Bulk Edit: Off')).toBe('false');
    });

    it('paints a wall over the dragged rectangle on release only, in any corner order', async () => {
      await open();
      await press('Low Wall', 'Paint wall');
      await bulkOn();
      fire('pointerdown', [2, 2]);
      fire('pointermove', [1, 1]);
      fire('pointermove', [0, 1]);
      await stable();
      expect(zoneEdit().zones).toEqual(mapZones().zones);
      expect(store().zoneConfigDirty()).toBe(false);
      fire('pointerup', [0, 1]);
      fire('click', [0, 1]);
      await stable();
      expect(zoneEdit().zones).toEqual([
        [0, 4, 4, 1],
        [1, 4, 4, 1],
        [1, 4, 4, 1],
      ]);
    });

    it('swallows only the click right after a rectangle', async () => {
      await open();
      await press('Floor', 'Paint wall');
      await bulkOn();
      fire('pointerdown', [1, 1]);
      fire('pointermove', [1, 2]);
      fire('pointerup', [1, 2]);
      fire('click', [0, 0]);
      await stable();
      expect(zoneEdit().zones[0][0]).toBe(0);
      await clickCell([0, 0]);
      expect(zoneEdit().zones[0][0]).toBe(1);
    });

    it('paints Elevation over the rectangle', async () => {
      await open();
      await press('Paint', 'Elevation');
      await pick('select#zb-elevation', '0.5');
      await bulkOn();
      drag([1, 2], [2, 3]);
      await stable();
      expect(zoneEdit().elevation).toEqual([
        [0, 0, 0, 0],
        [0, 0, 0.5, 0.5],
        [0, 0, 0.5, 0.5],
      ]);
    });

    it('paints Spawn cells over the rectangle without toggling, skipping high walls', async () => {
      await open();
      await press('Red Spawn', 'Spawn brush');
      await bulkOn();
      drag([0, 0], [1, 1]);
      await stable();
      expect(keys(zoneEdit().redSpawn)).toEqual(['0,1', '1,0', '1,1']);
      expect(zoneEdit().spawnEdited).toBe(true);
    });

    it('applies nothing on a press without a move; the click then acts as a click', async () => {
      await open();
      await press('Red Spawn', 'Spawn brush');
      await bulkOn();
      await clickCell([1, 0]);
      // A click toggles the loaded red Spawn cell off (a rectangle would have kept it).
      expect(keys(zoneEdit().redSpawn)).toEqual([]);
    });

    it('leaves Base placement and the Ramp to clicks', async () => {
      await open();
      await bulkOn();
      await press('Blue Base', 'Place base');
      drag([0, 1], [2, 2]);
      await stable();
      expect(zoneEdit().bases).toEqual({ blue: { x_px: 100, y_px: 100 } });
      await press('Paint', 'Elevation');
      await pick('select#zb-elevation', '2');
      await press('Bulk Edit: On');
      await clickCell([0, 3]);
      await press('Bulk Edit: Off');
      await press('Ramp', 'Elevation');
      await clickCell([0, 0]);
      await clickCell([0, 3]);
      expect(zoneEdit().elevation?.[0]).toEqual([0, 0.67, 1.33, 2]);
    });

    it('turning Bulk Edit off drops a rectangle in progress', async () => {
      await open();
      await press('High Wall', 'Paint wall');
      await bulkOn();
      fire('pointerdown', [1, 1]);
      fire('pointermove', [2, 3]);
      await press('Bulk Edit: On');
      fire('pointerup', [2, 3]);
      await stable();
      expect(zoneEdit().zones).toEqual(mapZones().zones);
    });
  });

  describe('drawing', () => {
    /** A 2D context that records the text, fills and strokes it is asked for. */
    function recorder() {
      const calls: string[] = [];
      let fill = '';
      let stroke = '';
      const ctx = new Proxy({} as Record<string | symbol, unknown>, {
        get(target, prop) {
          if (prop === 'fillText') return (t: string) => calls.push(`text:${t}`);
          if (prop === 'fillRect') return () => calls.push(`fill:${fill}`);
          if (prop === 'strokeRect') return () => calls.push(`stroke:${stroke}`);
          if (prop in target) return target[prop];
          return () => undefined;
        },
        set(target, prop, value) {
          if (prop === 'fillStyle') fill = value as string;
          if (prop === 'strokeStyle') stroke = value as string;
          target[prop] = value;
          return true;
        },
      }) as unknown as CanvasRenderingContext2D;
      return { calls, ctx };
    }

    /** Runs the mode's draw once with a palette whose every colour is its own name. */
    function drawOnce(): string[] {
      const { calls, ctx } = recorder();
      const palette = new Proxy({} as MapPalette, { get: (_t, prop) => String(prop) });
      const layer = mapCanvas().layer();
      layer?.draw?.({
        ctx,
        rows: 3,
        cols: 4,
        cellSize: 40,
        width: 160,
        height: 120,
        zones: zoneEdit().zones,
        palette,
      });
      return calls;
    }

    it('draws the Bases with their badges, non-zero Elevation and the Spawn cells', async () => {
      const elevation = [
        [0, 0, 0, 0],
        [0, 1.5, 0, 0],
        [0, 0, 0, 3],
      ];
      await open(
        zoneConfig({
          bases: [
            { type: 'red', x_px: 20, y_px: 60 },
            { type: 'blue', x_px: 140, y_px: 100 },
            { type: 'neutral_3', x_px: 60, y_px: 20 },
          ],
        }),
        mapZones(40, { elevation }),
      );
      const calls = drawOnce();
      const texts = calls.filter((c) => c.startsWith('text:'));
      expect(texts).toEqual(['text:1.5', 'text:3', 'text:R', 'text:B', 'text:N3']);
      expect(calls).toContain('fill:sideRed');
      expect(calls).toContain('fill:sideBlue');
      expect(calls).toContain('fill:baseNeutral');
      expect(calls.filter((c) => c === 'fill:spawnRed')).toHaveLength(1);
      expect(calls.filter((c) => c === 'fill:spawnBlue')).toHaveLength(1);
      expect(calls.filter((c) => c === 'fill:elevation')).toHaveLength(2);
    });

    it("outlines the Ramp's first Cell, and highlights a Bulk Edit rectangle while dragged", async () => {
      await open();
      expect(drawOnce()).not.toContain('stroke:rampAnchor');
      await press('Ramp', 'Elevation');
      await clickCell([1, 1]);
      expect(drawOnce()).toContain('stroke:rampAnchor');

      await press('Low Wall', 'Paint wall');
      expect(drawOnce()).not.toContain('stroke:rampAnchor');
      await press('Bulk Edit: Off');
      fire('pointerdown', [0, 1]);
      fire('pointermove', [1, 2]);
      expect(drawOnce().filter((c) => c === 'fill:losDrag')).toHaveLength(4);
      fire('pointerup', [1, 2]);
      expect(drawOnce()).not.toContain('fill:losDrag');
    });

    it('redraws when only mode-local state changes (Ramp anchor, Bulk rectangle)', async () => {
      await open();
      const nextFrame = (): Promise<void> =>
        new Promise((resolve) => requestAnimationFrame(() => resolve()));
      const settle = async (): Promise<void> => {
        for (let i = 0; i < 2; i++) {
          TestBed.tick();
          await nextFrame();
        }
      };
      await press('Ramp', 'Elevation');
      await settle();
      const draw = vi.spyOn(mapCanvas().layer()!, 'draw');
      fire('click', [1, 1]);
      await settle();
      expect(draw).toHaveBeenCalledTimes(1);

      await press('Low Wall', 'Paint wall');
      await press('Bulk Edit: Off');
      await settle();
      draw.mockClear();
      fire('pointerdown', [0, 1]);
      await settle();
      expect(draw).toHaveBeenCalledTimes(1);
      fire('pointerup', [0, 1]);
      await settle();
    });
  });

  describe('Save Configuration', () => {
    const save = (): HTMLButtonElement => button('Save Configuration');
    const status = (): string => text(mode().querySelector('[role="status"]'));
    const alert = (): string => text(mode().querySelector('[role="alert"]'));

    function expectSave(): { body: ZoneConfigInput; req: ReturnType<typeof expectMapRequest> } {
      const req = expectMapRequest(4, 'zone-config/', 'PUT');
      return { body: req.request.body as ZoneConfigInput, req };
    }

    it('PUTs the Zone size, Cell types and Bases, then shows exactly what the server saved', async () => {
      await open();
      await press('Red Base', 'Place base');
      await clickCell([1, 0]);
      await press('Blue Base', 'Place base');
      await clickCell([1, 3]);
      await press('Low Wall', 'Paint wall');
      await clickCell([0, 1]);
      save().click();
      await stable();
      expect(save().disabled).toBe(true);
      const { body, req } = expectSave();
      const zones = mapZones().zones.map((row) => [...row]);
      zones[0][1] = 4;
      expect(body).toEqual({
        zone_size: 40,
        zones,
        bases: [
          { type: 'red', x_px: 20, y_px: 60 },
          { type: 'blue', x_px: 140, y_px: 60 },
        ],
      });
      // The server's answer wins: here it stores a Neutral it was never sent.
      flushData(
        req,
        zoneConfig({
          zones,
          wall_meta: { '0,2': { facing: 'S' } },
          bases: [
            { type: 'red', x_px: 20, y_px: 60 },
            { type: 'blue', x_px: 140, y_px: 60 },
            { type: 'neutral_1', x_px: 60, y_px: 20 },
          ],
        }),
      );
      await stable();
      expect(status()).toBe('Configuration saved! (40px zones, 3 base(s))');
      expect(store().zoneConfigDirty()).toBe(false);
      expect(zoneEdit().wallMeta).toEqual({ '0,2': { facing: 'S' } });
      expect(Object.keys(zoneEdit().bases).sort()).toEqual(['blue', 'neutral_1', 'red']);
      expect(save().disabled).toBe(false);
    });

    it('warns, after a save that still goes through, when a team Base is missing', async () => {
      await open();
      await press('Red Base', 'Place base');
      await clickCell([1, 0]);
      save().click();
      await stable();
      const { req } = expectSave();
      flushData(req, zoneConfig({ bases: [{ type: 'red', x_px: 20, y_px: 60 }] }));
      await stable();
      expect(status()).toBe(
        'Configuration saved (40px zones, 1 base(s)) - place a blue base before using this map in ' +
          "a match. It won't be offered in match, round or League map pickers until then.",
      );
      expect(store().zoneConfigDirty()).toBe(false);
    });

    it("words the warning for both team Bases from the server's saved Bases", async () => {
      await open(zoneConfig());
      await press('Clear All Bases');
      save().click();
      await stable();
      const { body, req } = expectSave();
      expect(body.bases).toEqual([]);
      flushData(req, zoneConfig({ bases: [] }));
      await stable();
      expect(status()).toContain('(40px zones, 0 base(s)) - place a red and a blue base before');
    });

    it('shows a failed save with the server message and keeps the edits unsaved', async () => {
      await open();
      await press('High Wall', 'Paint wall');
      await clickCell([2, 2]);
      save().click();
      await stable();
      flushError(expectSave().req, 400, 'invalid', 'zones: wrong shape.');
      await stable();
      expect(alert()).toBe('Could not save the configuration. zones: wrong shape.');
      expect(status()).toBe('');
      expect(store().zoneConfigDirty()).toBe(true);
      expect(zoneEdit().zones[2][2]).toBe(0);
      expect(save().disabled).toBe(false);
    });

    it('saves loaded wall_meta (height kept), Elevation and unedited spawns back untouched', async () => {
      const elevation = [
        [0, 0, 0, 0],
        [0, 1.5, 0, 0],
        [0, 0, 0, 0],
      ];
      const wallMeta = { '0,0': { facing: 'E' as const, height: 2 } };
      const zones = mapZones(40, { wall_meta: wallMeta, elevation });
      zones.zones[0][0] = 5;
      await open(noBases(), zones);
      await press('Low Wall', 'Paint wall');
      await clickCell([2, 3]);
      save().click();
      await stable();
      const { body, req } = expectSave();
      expect(body.wall_meta).toEqual(wallMeta);
      expect(body.elevation).toEqual(elevation);
      expect(body.zones?.[0][0]).toBe(5);
      expect('red_spawn' in body).toBe(false);
      expect('blue_spawn' in body).toBe(false);
      flushData(req, zoneConfig());
      await stable();
    });

    it('ignores canvas edits while the save is in flight, so the response loses none', async () => {
      await open();
      await press('Low Wall', 'Paint wall');
      await clickCell([0, 1]);
      save().click();
      await stable();
      const { req } = expectSave();
      const before = structuredClone(zoneEdit().zones);
      await clickCell([2, 2]);
      fire('pointerdown', [1, 1]);
      fire('pointermove', [1, 2]);
      fire('pointerup', [1, 2]);
      await stable();
      expect(zoneEdit().zones).toEqual(before);
      await press('Red Base', 'Place base');
      await clickCell([1, 0]);
      expect(zoneEdit().bases).toEqual({});
      await press('Red Spawn', 'Spawn brush');
      await clickCell([0, 0]);
      expect(zoneEdit().spawnEdited).toBe(false);
      http.expectNone(() => true);
      flushData(req, zoneConfig({ bases: [] }));
      await stable();
      expect(store().zoneConfigDirty()).toBe(false);
    });

    it('sends the loaded Spawn cells, as edited, once a Spawn brush was used', async () => {
      await open();
      await press('Blue Spawn', 'Spawn brush');
      await clickCell([0, 2]);
      save().click();
      await stable();
      const { body, req } = expectSave();
      expect(body.red_spawn).toEqual([[1, 0]]);
      expect(body.blue_spawn).toEqual([
        [2, 3],
        [0, 2],
      ]);
      flushData(req, zoneConfig());
      await stable();
    });
  });
});
