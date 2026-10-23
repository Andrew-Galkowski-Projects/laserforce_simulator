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
import { MapsService } from '../../../api/maps.service';
import { Role } from '../../../api/players.models';
import {
  PlaybackMap as PlaybackMapData,
  PlaybackSnapshot,
  Side,
  TimelinePlayer,
} from '../../../api/rounds.models';
import { ROLE_LABELS } from '../../../teams/shared/roster';
import {
  Keyframe,
  SEC_PER_TICK,
  TRAIL_SEC,
  cellToPixel,
  deactivatedAt,
  positionAt,
  routeKeyframes,
  trailTimes,
} from './playback-routes';

/** One player's marker, ready to draw. */
interface Route {
  side: Side;
  /** CMD / HVY / SCT / AMO / MED. */
  label: string;
  /** 1 / 2 for a Side's two Scouts, else 0. */
  scout: number;
  keyframes: Keyframe[];
  /** Whether the player is deactivated at a tick (dims the marker and that trail segment). */
  off: (tick: number) => boolean;
}

/** Opacity factor for a deactivated player's marker and trail. */
const DIM = 0.35;

/** The colours, read from the tokens at draw time. */
interface Palette {
  side: Record<Side, string>;
  trail: Record<Side, string>;
  surface: string;
  text: string;
  border: string;
  font: string;
}

function readPalette(el: Element): Palette {
  const css = getComputedStyle(el);
  const token = (name: string): string => css.getPropertyValue(name).trim();
  return {
    side: { red: token('--lf-side-red'), blue: token('--lf-side-blue') },
    trail: { red: token('--lf-side-red-dim'), blue: token('--lf-side-blue-dim') },
    surface: token('--lf-surface'),
    text: token('--lf-text'),
    border: token('--lf-border'),
    font: token('--lf-font-body'),
  };
}

/** Draws the trails, then the markers, at `t` seconds; ported from the Django overlay. */
function drawMap(
  ctx: CanvasRenderingContext2D,
  routes: readonly Route[],
  zone: number,
  t: number,
  colours: Palette,
): void {
  const { width: w, height: h } = ctx.canvas;
  ctx.clearRect(0, 0, w, h);
  ctx.globalAlpha = 1;
  ctx.lineWidth = 3;
  ctx.strokeStyle = colours.border;
  ctx.strokeRect(1.5, 1.5, w - 3, h - 3);

  // Comet trails first, so the markers sit on top; older segments are thinner and fainter.
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
        const dim = route.off(s / SEC_PER_TICK) ? DIM : 1;
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

  // Markers: a Side-coloured ring, the role label, and a Scout's number badge.
  const size = Math.max(18, zone * 1.3);
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  for (const route of routes) {
    const [r, c] = positionAt(route.keyframes, t);
    const [x, y] = cellToPixel(r, c, zone);
    ctx.globalAlpha = route.off(t / SEC_PER_TICK) ? DIM : 1;
    ctx.beginPath();
    ctx.arc(x, y, size * 0.6, 0, Math.PI * 2);
    ctx.fillStyle = colours.surface;
    ctx.fill();
    ctx.lineWidth = Math.max(2, size * 0.12);
    ctx.strokeStyle = colours.side[route.side];
    ctx.stroke();
    ctx.fillStyle = colours.text;
    ctx.font = `700 ${Math.round(size * 0.36)}px ${colours.font}`;
    ctx.fillText(route.label, x, y + 0.5);
    if (route.scout) {
      const bx = x + size * 0.42;
      const by = y + size * 0.42;
      const br = size * 0.26;
      ctx.beginPath();
      ctx.arc(bx, by, br, 0, Math.PI * 2);
      ctx.fillStyle = colours.side[route.side];
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
 * - `map`: `RoundPlayback.map`, `null` for a map-less Round (show the "No map" notice).
 * - `players`: every `TimelinePlayer` of the Round (names, Sides, roles), both Sides.
 * - `cursor`: the Playback cursor in ticks (fractional while playing).
 * - `snapshots`: every player's Playback snapshots by player id, and `respawnTicks`
 *   (`RoundPlayback.respawn_ticks`): a Down or eliminated player's marker and trail are dimmed.
 *
 * The map fetches its image only when the toggle is first opened, so a closed map makes no
 * HTTP request; shell specs rely on that. The canvas is redrawn by an effect on the cursor (no
 * animation frame loop); it has the image's natural size and CSS scales it with the image.
 */
@Component({
  selector: 'app-playback-map',
  imports: [MatButton],
  templateUrl: './playback-map.html',
  styleUrl: './playback-map.scss',
})
export class PlaybackMap {
  readonly map = input.required<PlaybackMapData | null>();
  readonly players = input.required<readonly TimelinePlayer[]>();
  readonly cursor = input.required<number>();
  readonly snapshots = input.required<ReadonlyMap<number, readonly PlaybackSnapshot[]>>();
  readonly respawnTicks = input.required<number>();

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
        side: p.team,
        label: ROLE_LABELS[p.role.toLowerCase() as Role] ?? p.role.slice(0, 3).toUpperCase(),
        scout: p.scout,
        keyframes: routeKeyframes(p),
        off: (tick: number) => deactivatedAt(own, tick, respawnTicks),
      };
    });
  });

  constructor() {
    effect(() => {
      const t = this.cursor() * SEC_PER_TICK;
      const zone = this.map()?.zone_size ?? 0;
      const routes = this.routes();
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
      if (ctx) {
        drawMap(ctx, routes, zone, t, readPalette(canvas));
      }
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

  /** Fetches the processed image the first time only. */
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
  }
}
