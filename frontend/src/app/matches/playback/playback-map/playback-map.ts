import {
  Component,
  DestroyRef,
  ElementRef,
  computed,
  effect,
  inject,
  input,
  signal,
  viewChild,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatButton } from '@angular/material/button';
import { MapBase } from '../../../api/maps.models';
import { MapsService } from '../../../api/maps.service';
import {
  PlaybackMap as PlaybackMapData,
  PlaybackSnapshot,
  Side,
  TimelineEvent,
} from '../../../api/rounds.models';
import { Role } from '../../../api/players.models';
import { TICKS_PER_SECOND } from '../../shared/clock';
import { iconRole, roleIconLoads, tintedRoleIcon } from '../../shared/role-icon';
import { MARKER_DIM, markerLook, markerSize } from './playback-markers';
import {
  BaseMarker,
  CaptureBurst,
  Overlays,
  TagLine,
  baseMarkers,
  overlaysAt,
} from './playback-overlays';
import {
  Keyframe,
  TRAIL_SEC,
  cellToPixel,
  deactivatedAt,
  positionAt,
  routeKeyframes,
  trailTimes,
} from './playback-routes';

/** One player's marker, ready to draw. */
interface Route {
  id: number;
  side: Side;
  /** The marker's role icon; `null` for a role with no icon (drawn as a plain dot). */
  role: Role | null;
  /** 1 / 2 for a Side's two Scouts, else 0. */
  scout: number;
  keyframes: Keyframe[];
  /** Whether the player is deactivated at a tick (dims the marker and that trail segment). */
  off: (tick: number) => boolean;
}

/** The colours, read from the tokens at draw time. */
interface Palette {
  side: Record<Side, string>;
  trail: Record<Side, string>;
  /** A deactivated player's marker. */
  muted: string;
  surface: string;
  border: string;
  font: string;
  missile: string;
  resupply: string;
  baseNeutral: string;
  baseOutline: string;
}

/** The map's colours, from the tokens as `el` computes them. */
export function readPalette(el: Element): Palette {
  const css = getComputedStyle(el);
  const token = (name: string): string => css.getPropertyValue(name).trim();
  return {
    side: { red: token('--lf-side-red'), blue: token('--lf-side-blue') },
    trail: { red: token('--lf-side-red-dim'), blue: token('--lf-side-blue-dim') },
    muted: token('--lf-map-marker-off'),
    surface: token('--lf-surface'),
    border: token('--lf-border'),
    font: token('--lf-font-body'),
    missile: token('--lf-ev-missile'),
    resupply: token('--lf-ev-resupply'),
    baseNeutral: token('--lf-map-base-neutral'),
    baseOutline: token('--lf-map-base-outline'),
  };
}

/** Everything the map paints at one cursor. */
interface Scene {
  routes: readonly Route[];
  zone: number;
  /** The cursor in seconds. */
  t: number;
  overlays: Overlays;
  bases: readonly BaseMarker[];
}

/** Draws bases, trails, Tag lines and bursts, then markers; ported from the Django overlay. */
function drawMap(ctx: CanvasRenderingContext2D, scene: Scene, colours: Palette): void {
  const { width: w, height: h } = ctx.canvas;
  ctx.clearRect(0, 0, w, h);
  ctx.globalAlpha = 1;
  ctx.setLineDash([]);
  ctx.lineWidth = 3;
  ctx.strokeStyle = colours.border;
  ctx.strokeRect(1.5, 1.5, w - 3, h - 3);
  drawBases(ctx, scene.bases, scene.zone, colours);
  drawTrails(ctx, scene.routes, scene.zone, scene.t, colours);
  drawOverlays(ctx, scene.overlays, scene.zone, colours);
  drawMarkers(ctx, scene.routes, scene.zone, scene.t, colours);
}

/** Each base as a diamond (half-width 0.9 zone) in its Side or neutral colour, lettered. */
function drawBases(
  ctx: CanvasRenderingContext2D,
  bases: readonly BaseMarker[],
  zone: number,
  colours: Palette,
): void {
  const half = 0.9 * zone;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.lineJoin = 'round';
  for (const base of bases) {
    ctx.beginPath();
    ctx.moveTo(base.x, base.y - half);
    ctx.lineTo(base.x + half, base.y);
    ctx.lineTo(base.x, base.y + half);
    ctx.lineTo(base.x - half, base.y);
    ctx.closePath();
    ctx.fillStyle = base.kind === 'neutral' ? colours.baseNeutral : colours.side[base.kind];
    ctx.fill();
    ctx.lineWidth = 3;
    ctx.strokeStyle = colours.baseOutline;
    ctx.stroke();
    ctx.fillStyle = colours.baseOutline;
    ctx.font = `700 ${Math.round(0.9 * half)}px ${colours.font}`;
    ctx.fillText(base.letter, base.x, base.y + 1);
  }
}

/** Each player's comet trail. */
function drawTrails(
  ctx: CanvasRenderingContext2D,
  routes: readonly Route[],
  zone: number,
  t: number,
  colours: Palette,
): void {
  // Older segments are thinner and fainter.
  ctx.lineJoin = 'round';
  ctx.lineCap = 'round';
  for (const route of routes) {
    ctx.strokeStyle = colours.trail[route.side];
    let prev: [number, number] | null = null;
    for (const s of trailTimes(t)) {
      const [r, c] = positionAt(route.keyframes, s);
      const xy = cellToPixel(r, c, zone);
      if (prev) {
        const age = (t - s) / TRAIL_SEC;
        const dim = route.off(s * TICKS_PER_SECOND) ? MARKER_DIM : 1;
        ctx.globalAlpha = (0.8 * (1 - age) + 0.05) * dim;
        ctx.lineWidth = Math.max(1.5, zone * (0.08 + 0.12 * (1 - age)));
        ctx.beginPath();
        ctx.moveTo(prev[0], prev[1]);
        ctx.lineTo(xy[0], xy[1]);
        ctx.stroke();
      }
      prev = xy;
    }
  }
  ctx.globalAlpha = 1;
}

/** How each Tag line kind is stroked: colour, width factor, opacity factor, dashed. */
function lineStyle(
  line: TagLine,
  colours: Palette,
): { colour: string; width: number; opacity: number; dashed: boolean } {
  switch (line.kind) {
    case 'miss':
      return { colour: colours.side[line.side], width: 0.6, opacity: 0.4, dashed: true };
    case 'missile-hit':
      return { colour: colours.missile, width: 1.2, opacity: 1, dashed: false };
    case 'missile-miss':
      return { colour: colours.missile, width: 1, opacity: 1, dashed: true };
    case 'resupply':
      return { colour: colours.resupply, width: 1, opacity: 1, dashed: false };
    default:
      return { colour: colours.side[line.side], width: 1, opacity: 1, dashed: false };
  }
}

/** The Tag lines (trimmed short of the icons, with a muzzle flare), then the capture bursts. */
function drawOverlays(
  ctx: CanvasRenderingContext2D,
  overlays: Overlays,
  zone: number,
  colours: Palette,
): void {
  const iconTrim = markerSize(zone) * 0.4;
  ctx.lineCap = 'round';
  for (const line of overlays.lines) {
    const style = lineStyle(line, colours);
    const dx = line.to[0] - line.from[0];
    const dy = line.to[1] - line.from[1];
    const length = Math.hypot(dx, dy);
    const ux = dx / length;
    const uy = dy / length;
    // The far end stops at the target's icon, or at a captured base's diamond.
    const toTrim = line.kind === 'capture' ? 0.9 * zone : iconTrim;
    // Two players closer than the trims: the flare sits half-way and no line is drawn.
    const lead = Math.min(iconTrim, length / 2);
    const start: [number, number] = [line.from[0] + ux * lead, line.from[1] + uy * lead];
    const end: [number, number] = [line.to[0] - ux * toTrim, line.to[1] - uy * toTrim];
    ctx.globalAlpha = line.alpha * style.opacity;
    ctx.strokeStyle = style.colour;
    ctx.fillStyle = style.colour;
    const width = Math.max(1.5, 0.14 * zone * style.width);
    if (length > iconTrim + toTrim) {
      ctx.lineWidth = width;
      ctx.setLineDash(style.dashed ? [width * 3, width * 2.5] : []);
      ctx.beginPath();
      ctx.moveTo(start[0], start[1]);
      ctx.lineTo(end[0], end[1]);
      ctx.stroke();
      ctx.setLineDash([]);
    }
    drawFlare(ctx, start, Math.atan2(uy, ux), zone * (0.9 + 0.5 * line.alpha));
    if (line.kind === 'resupply') {
      drawPlus(ctx, end, Math.max(4, 0.45 * zone), Math.max(2, 0.16 * zone));
    }
  }
  for (const burst of overlays.bursts) {
    drawBurst(ctx, burst, zone, colours);
  }
  ctx.globalAlpha = 1;
}

/** A muzzle flare: a 10-point star fanned along `heading` from the acting end. */
function drawFlare(
  ctx: CanvasRenderingContext2D,
  at: readonly [number, number],
  heading: number,
  r: number,
): void {
  ctx.beginPath();
  ctx.moveTo(at[0], at[1]);
  for (let i = 0; i < 10; i++) {
    const angle = heading + (i - 4.5) * 0.22;
    const radius = i % 2 === 0 ? r : 0.45 * r;
    ctx.lineTo(at[0] + Math.cos(angle) * radius, at[1] + Math.sin(angle) * radius);
  }
  ctx.closePath();
  ctx.fill();
}

/** The "+" at a resupply's receiving end. */
function drawPlus(
  ctx: CanvasRenderingContext2D,
  at: readonly [number, number],
  arm: number,
  width: number,
): void {
  ctx.lineWidth = width;
  ctx.beginPath();
  ctx.moveTo(at[0] - arm, at[1]);
  ctx.lineTo(at[0] + arm, at[1]);
  ctx.moveTo(at[0], at[1] - arm);
  ctx.lineTo(at[0], at[1] + arm);
  ctx.stroke();
}

/** A capture's 8 radial lasers at 45 degree steps, growing as they fade. */
function drawBurst(
  ctx: CanvasRenderingContext2D,
  burst: CaptureBurst,
  zone: number,
  colours: Palette,
): void {
  const r0 = 1.1 * zone;
  const r1 = zone * (2 + 2.5 * (1 - burst.alpha));
  ctx.globalAlpha = burst.alpha;
  ctx.strokeStyle = colours.side[burst.side];
  ctx.lineWidth = Math.max(2, 0.18 * zone);
  ctx.beginPath();
  for (let k = 0; k < 8; k++) {
    const angle = (k * Math.PI) / 4;
    const [cx, cy] = [Math.cos(angle), Math.sin(angle)];
    ctx.moveTo(burst.at[0] + cx * r0, burst.at[1] + cy * r0);
    ctx.lineTo(burst.at[0] + cx * r1, burst.at[1] + cy * r1);
  }
  ctx.stroke();
}

/**
 * Draws each player's marker at `t` seconds: the role icon centred on the player, tinted in the
 * Side colour (grey and dimmed while deactivated), with no ring, and a Scout's number badge at
 * its bottom-right in the same colour. A plain dot stands in until the icon's PNG has loaded.
 */
function drawMarkers(
  ctx: CanvasRenderingContext2D,
  routes: readonly Route[],
  zone: number,
  t: number,
  colours: Palette,
): void {
  const size = markerSize(zone);
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  for (const route of routes) {
    const [r, c] = positionAt(route.keyframes, t);
    const [x, y] = cellToPixel(r, c, zone);
    const look = markerLook(route.side, route.off(t * TICKS_PER_SECOND), colours);
    ctx.globalAlpha = look.alpha;
    const icon = route.role ? tintedRoleIcon(route.role, look.colour) : null;
    if (icon) {
      ctx.drawImage(icon, x - size / 2, y - size / 2, size, size);
    } else {
      ctx.beginPath();
      ctx.arc(x, y, size * 0.25, 0, Math.PI * 2);
      ctx.fillStyle = look.colour;
      ctx.fill();
    }
    if (route.scout) {
      const bx = x + size * 0.36;
      const by = y + size * 0.36;
      const br = size * 0.17;
      ctx.beginPath();
      ctx.arc(bx, by, br, 0, Math.PI * 2);
      ctx.fillStyle = look.colour;
      ctx.fill();
      ctx.fillStyle = colours.surface;
      ctx.font = `700 ${Math.round(br * 1.4)}px ${colours.font}`;
      ctx.fillText(String(route.scout), bx, by + 0.5);
    }
  }
  ctx.globalAlpha = 1;
}

/**
 * The beta Playback map: the arena's processed image with each player's marker moving along
 * their recorded route, and a 16 s trail. Collapsed behind a "Show map (beta)" toggle.
 *
 * Inputs:
 * - `map`: `RoundPlayback.map`, `null` for a map-less Round (show the "No map" notice). Its
 *   `players` carry each marker's Side, role and route.
 * - `cursor`: the Playback cursor in ticks (fractional while playing).
 * - `snapshots`: every player's Playback snapshots by player id, and `respawnTicks`
 *   (`RoundPlayback.respawn_ticks`): a Down or eliminated player's marker and trail are dimmed.
 * - `events`: the timeline's events, drawn as Tag lines and capture bursts (`overlaysAt`).
 *
 * The map fetches its image and its zone config (for the bases) only when the toggle is first
 * opened, so a closed map makes no HTTP request; shell specs rely on that. A failed zone-config
 * fetch draws no bases and shows no notice. The canvas is redrawn by an effect on the cursor (no
 * per-paint render loop); it has the image's natural size and CSS scales it with the image.
 */
@Component({
  selector: 'app-playback-map',
  imports: [MatButton],
  templateUrl: './playback-map.html',
  styleUrl: './playback-map.scss',
})
export class PlaybackMap {
  readonly map = input.required<PlaybackMapData | null>();
  readonly cursor = input.required<number>();
  readonly snapshots = input.required<ReadonlyMap<number, readonly PlaybackSnapshot[]>>();
  readonly respawnTicks = input.required<number>();
  readonly events = input.required<readonly TimelineEvent[]>();

  private readonly maps = inject(MapsService);
  private readonly destroyRef = inject(DestroyRef);

  /** Collapsed at first. */
  protected readonly open = signal(false);
  /** The arena image's object URL, once fetched. */
  protected readonly imageUrl = signal<string | null>(null);
  /** The image fetch failed, or the fetched image would not decode. */
  protected readonly failed = signal(false);
  /** The `<img>` has loaded, so its natural size is known. */
  protected readonly loaded = signal(false);
  /** The map editor's bases, once the zone config arrives; none when it fails. */
  private readonly bases = signal<readonly MapBase[]>([]);
  private requested = false;

  private readonly image = viewChild<ElementRef<HTMLImageElement>>('image');
  private readonly canvas = viewChild<ElementRef<HTMLCanvasElement>>('canvas');

  /** Every player's keyframed route, built once per map. */
  private readonly routes = computed<Route[]>(() => {
    const snapshots = this.snapshots();
    const respawnTicks = this.respawnTicks();
    return (this.map()?.players ?? []).map((p) => {
      const own = snapshots.get(p.id) ?? [];
      return {
        id: p.id,
        side: p.team,
        role: iconRole(p.role),
        scout: p.scout,
        keyframes: routeKeyframes(p),
        off: (tick: number) => deactivatedAt(own, tick, respawnTicks),
      };
    });
  });

  private readonly baseMarks = computed(() => baseMarkers(this.bases()));

  constructor() {
    effect(() => {
      const cursor = this.cursor();
      const zone = this.map()?.zone_size ?? 0;
      const routes = this.routes();
      const events = this.events();
      const bases = this.bases();
      const marks = this.baseMarks();
      // Redraw when a role icon's PNG arrives, so a paused map swaps its dots for icons.
      roleIconLoads();
      const img = this.image()?.nativeElement;
      const canvas = this.canvas()?.nativeElement;
      if (!this.loaded() || !img || !canvas || !zone || !img.naturalWidth) {
        return;
      }
      if (canvas.width !== img.naturalWidth || canvas.height !== img.naturalHeight) {
        canvas.width = img.naturalWidth;
        canvas.height = img.naturalHeight;
      }
      const ctx = canvas.getContext('2d');
      if (!ctx) {
        return;
      }
      const keyframes = new Map(routes.map((r) => [r.id, r.keyframes]));
      const at = (id: number, tick: number): readonly [number, number] | null => {
        const kf = keyframes.get(id);
        return kf ? cellToPixel(...positionAt(kf, tick / TICKS_PER_SECOND), zone) : null;
      };
      const overlays = overlaysAt(events, cursor, at, bases);
      const scene = { routes, zone, t: cursor / TICKS_PER_SECOND, overlays, bases: marks };
      drawMap(ctx, scene, readPalette(canvas));
    });
    this.destroyRef.onDestroy(() => {
      const src = this.imageUrl();
      if (src) {
        URL.revokeObjectURL(src);
      }
    });
  }

  protected toggleOpen(): void {
    this.open.update((o) => !o);
    // A reopened panel renders a new <img>, which loads afresh.
    this.loaded.set(false);
    if (this.open()) {
      this.fetchImage();
    }
  }

  /** Fetches the processed image and the zone config (the bases) the first time only. */
  private fetchImage(): void {
    const map = this.map();
    if (this.requested || !map) {
      return;
    }
    this.requested = true;
    this.maps
      .processedImage(map.arena_map.id)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (blob) => this.imageUrl.set(URL.createObjectURL(blob)),
        error: () => this.failed.set(true),
      });
    this.maps
      .zoneConfig(map.arena_map.id)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (config) => this.bases.set(config.bases ?? []),
        // No bases, and no notice: the rest of the map still plays.
        error: () => this.bases.set([]),
      });
  }
}
