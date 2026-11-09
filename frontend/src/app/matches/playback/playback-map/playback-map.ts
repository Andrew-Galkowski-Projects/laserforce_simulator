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
import {
  PlayerGauges,
  Rgb,
  SpecialBar,
  anySpecialRunning,
  gaugeColour,
  hexRgb,
  lapColour,
  playerGauges,
} from './playback-gauges';
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
  /** What the player carries at a tick (RV-07): the arcs' levels, missiles and special bar. */
  gauges: (tick: number) => PlayerGauges;
  /** How many missile pips to draw: the player's `max_missiles` (0 draws none). */
  maxMissiles: number;
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
  /** The arcs' colour scale (RV-07a), as channels for `gaugeColour`. */
  gaugeFull: Rgb;
  gaugeMid: Rgb;
  gaugeEmpty: Rgb;
  /** Under each arc, an unlit missile pip, and a special bar before its first lap. */
  gaugeTrack: string;
  /** The special bar's lap colours (RV-07b), `--lf-special-lap-1` .. `-10`. */
  laps: readonly string[];
  /** A running special's rolling stripes: the near-white base outline, light in both themes. */
  stripe: string;
}

/** How many `--lf-special-lap-*` tokens there are. */
const SPECIAL_LAPS = 10;

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
    gaugeFull: hexRgb(token('--lf-gauge-full')),
    gaugeMid: hexRgb(token('--lf-gauge-mid')),
    gaugeEmpty: hexRgb(token('--lf-gauge-empty')),
    gaugeTrack: token('--lf-gauge-track'),
    laps: Array.from({ length: SPECIAL_LAPS }, (_, i) => token(`--lf-special-lap-${i + 1}`)),
    stripe: token('--lf-map-base-outline'),
  };
}

/** Everything the map paints at one cursor. */
interface Scene {
  routes: readonly Route[];
  zone: number;
  /** The cursor in seconds. */
  t: number;
  /** The real-time clock in ms: the rolling stripes' phase. */
  now: number;
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
  drawMarkers(ctx, scene.routes, scene.zone, scene.t, scene.now, colours);
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
 * Then its gauges (`drawGauges`) and special bar (`drawSpecialBar`), dimmed with the icon.
 */
function drawMarkers(
  ctx: CanvasRenderingContext2D,
  routes: readonly Route[],
  zone: number,
  t: number,
  now: number,
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
    // Still at the icon's alpha, so a deactivated player's gauges dim with it.
    const gauges = route.gauges(t * TICKS_PER_SECOND);
    drawGauges(ctx, [x, y], size, gauges, route.maxMissiles, colours);
    if (gauges.special) {
      const below = route.maxMissiles > 0 ? 0.3 * size : 0.18 * size;
      const look = { side: colours.side[route.side], now };
      drawSpecialBar(ctx, [x, y + 0.56 * size + below], size, gauges.special, look, colours);
    }
  }
  ctx.globalAlpha = 1;
}

/** The lives arc's span, clockwise from just left of the bottom (canvas angles: y grows down). */
const LIVES_FROM = 0.55 * Math.PI;
/** The shots arc's span, counter-clockwise from just right of the bottom. */
const SHOTS_FROM = 0.45 * Math.PI;
/** Each arc sweeps 0.9 pi when full. */
const ARC_SWEEP = 0.9 * Math.PI;

/**
 * One marker's gauges (RV-07a), drawn after its icon at the current alpha: the lives arc on the
 * left and the shots arc on the right, each filling from the bottom over a track arc, then a
 * row of `maxMissiles` pips under them, lit in the missile colour for each missile left. The
 * special bar goes under the pips (`drawSpecialBar`).
 */
function drawGauges(
  ctx: CanvasRenderingContext2D,
  [x, y]: readonly [number, number],
  size: number,
  gauges: PlayerGauges,
  maxMissiles: number,
  colours: Palette,
): void {
  const h = size / 2;
  const radius = 1.12 * h;
  ctx.lineWidth = Math.max(3, 0.1 * size);
  ctx.lineCap = 'butt';
  const arc = (from: number, sweep: number, ccw: boolean, colour: string): void => {
    ctx.beginPath();
    ctx.arc(x, y, radius, from, ccw ? from - sweep : from + sweep, ccw);
    ctx.strokeStyle = colour;
    ctx.stroke();
  };
  const colour = (level: number): string =>
    gaugeColour(level, colours.gaugeFull, colours.gaugeMid, colours.gaugeEmpty);
  arc(LIVES_FROM, ARC_SWEEP, false, colours.gaugeTrack);
  if (gauges.lives > 0) {
    arc(LIVES_FROM, ARC_SWEEP * gauges.lives, false, colour(gauges.lives));
  }
  arc(SHOTS_FROM, ARC_SWEEP, true, colours.gaugeTrack);
  if (gauges.shots > 0) {
    arc(SHOTS_FROM, ARC_SWEEP * gauges.shots, true, colour(gauges.shots));
  }
  if (maxMissiles > 0) {
    const pip = 0.055 * size;
    const gap = 3 * pip;
    const py = y + radius + 0.16 * size;
    const left = x - ((maxMissiles - 1) * gap) / 2;
    const lit = gauges.missiles ?? 0;
    for (let i = 0; i < maxMissiles; i++) {
      ctx.beginPath();
      ctx.arc(left + i * gap, py, pip, 0, Math.PI * 2);
      ctx.fillStyle = i < lit ? colours.missile : colours.gaugeTrack;
      ctx.fill();
    }
  }
}

/** How a special bar's running looks are painted: its player's Side colour and the clock. */
interface BarLook {
  side: string;
  /** The real-time clock in ms (0 under reduced motion): the stripes' phase. */
  now: number;
}

/**
 * One marker's special bar (RV-07b), centred on `[x, cy]`, `1.15 size` wide and
 * `max(4, 0.13 size)` high. At rest: the previous lap's colour (or the track before the first
 * lap) with the current lap's `fill` over it in `lapColour(lap)`. Rapid fire rolls the whole bar
 * over the Side colour; a nuke fills the first `fuse` of the track with the rolling stripes. A
 * tick per completed lap sits above the bar, each in its lap's colour.
 */
function drawSpecialBar(
  ctx: CanvasRenderingContext2D,
  [x, cy]: readonly [number, number],
  size: number,
  bar: SpecialBar,
  look: BarLook,
  colours: Palette,
): void {
  const width = 1.15 * size;
  const height = Math.max(4, 0.13 * size);
  const left = x - width / 2;
  const top = cy - height / 2;
  if (bar.running === 'rapid-fire') {
    drawRolling(ctx, [left, top, width, height], look, colours.stripe);
  } else if (bar.running === 'nuke') {
    ctx.fillStyle = colours.gaugeTrack;
    ctx.fillRect(left, top, width, height);
    if (bar.fuse) {
      drawRolling(ctx, [left, top, width * bar.fuse, height], look, colours.stripe);
    }
  } else {
    ctx.fillStyle = bar.lap >= 1 ? lapColour(bar.lap - 1, colours.laps) : colours.gaugeTrack;
    ctx.fillRect(left, top, width, height);
    if (bar.fill > 0) {
      ctx.fillStyle = lapColour(bar.lap, colours.laps);
      ctx.fillRect(left, top, width * bar.fill, height);
    }
  }
  const tickWidth = Math.max(2, 0.45 * height);
  for (let lap = 0; lap < bar.lap; lap++) {
    ctx.fillStyle = lapColour(lap, colours.laps);
    ctx.fillRect(left + lap * (tickWidth + 2), top - 0.9 * height, tickWidth, 0.7 * height);
  }
}

/**
 * The Side colour with diagonal stripes rolling across it, as the arena scoreboard shows a
 * running special: each stripe 1.4 times the bar height wide, marching 4 stripe widths a second.
 */
function drawRolling(
  ctx: CanvasRenderingContext2D,
  [x, y, w, h]: readonly [number, number, number, number],
  look: BarLook,
  stripeColour: string,
): void {
  ctx.save();
  ctx.beginPath();
  ctx.rect(x, y, w, h);
  ctx.clip();
  ctx.fillStyle = look.side;
  ctx.fillRect(x, y, w, h);
  const stripe = h * 1.4;
  const shift = ((look.now / 1000) * stripe * 4) % (stripe * 2);
  ctx.fillStyle = stripeColour;
  for (let sx = x - stripe * 2 + shift; sx < x + w + stripe; sx += stripe * 2) {
    ctx.beginPath();
    ctx.moveTo(sx, y + h);
    ctx.lineTo(sx + stripe, y + h);
    ctx.lineTo(sx + stripe + h, y);
    ctx.lineTo(sx + h, y);
    ctx.closePath();
    ctx.fill();
  }
  ctx.restore();
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
 * fetch draws no bases and shows no notice. The canvas is redrawn by an effect on the cursor; it
 * has the image's natural size and CSS scales it with the image. While the map is open and a
 * nuke or rapid fire runs at the cursor (`anySpecialRunning`), a `requestAnimationFrame` loop
 * also redraws it each frame on `performance.now()`, so the stripes roll while paused. The loop
 * stops when no special runs, the map closes or the component is destroyed; under
 * `prefers-reduced-motion: reduce` it never starts and the stripes stand at phase 0.
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
        gauges: (tick: number) => playerGauges(p, own, tick),
        maxMissiles: p.max_missiles,
      };
    });
  });

  private readonly baseMarks = computed(() => baseMarkers(this.bases()));

  /** The OS asks for reduced motion: no frame loop, and the stripes stand still. */
  private readonly reducedMotion =
    window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches ?? false;
  /** The real-time clock (ms) the rolling stripes take their phase from; 0 until a frame runs. */
  private readonly now = signal(0);
  /** Whether a frame loop is needed: the map is open and a special runs at the cursor. */
  private readonly animating = computed(
    () =>
      this.open() &&
      !this.reducedMotion &&
      anySpecialRunning(this.map()?.players ?? [], this.snapshots(), this.cursor()),
  );

  constructor() {
    // The frame loop: one frame at a time while `animating`, cancelled when it turns false, on
    // a closed map and on destroy (the effect's cleanup).
    effect((onCleanup) => {
      if (!this.animating()) {
        return;
      }
      const step = (): void => {
        this.now.set(performance.now());
        frame = requestAnimationFrame(step);
      };
      let frame = requestAnimationFrame(step);
      onCleanup(() => cancelAnimationFrame(frame));
    });
    effect(() => {
      const cursor = this.cursor();
      const now = this.now();
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
      const scene = { routes, zone, t: cursor / TICKS_PER_SECOND, now, overlays, bases: marks };
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
