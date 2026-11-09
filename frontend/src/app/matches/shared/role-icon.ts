// The five SM5 role icons (RV-06): 64 x 64 PNGs in `public/roles/`, black line art on a white
// fill, tinted at runtime to a Side colour for the Playback map, scoreboard and feed.
import { Signal, signal } from '@angular/core';
import { Role } from '../../api/players.models';

/** Each role's icon, relative to the base href (so every deploy serves it the same way). */
export const ROLE_ICON_URLS: Readonly<Record<Role, string>> = {
  commander: 'roles/commander.png',
  heavy: 'roles/heavy.png',
  scout: 'roles/scout.png',
  ammo: 'roles/ammo.png',
  medic: 'roles/medic.png',
};

/**
 * Tints RGBA pixel data in place: each pixel with alpha > 0 takes
 * `channel = target * (1 - l) + 255 * l`, `l = (r + g + b) / 765`. Black line art becomes `rgb`,
 * the white fill stays white, anti-aliased edges blend between the two; alpha is unchanged.
 */
export function tintPixels(data: Uint8ClampedArray, rgb: readonly [number, number, number]): void {
  for (let i = 0; i < data.length; i += 4) {
    if (data[i + 3] === 0) continue;
    const l = (data[i] + data[i + 1] + data[i + 2]) / 765;
    data[i] = rgb[0] * (1 - l) + 255 * l;
    data[i + 1] = rgb[1] * (1 - l) + 255 * l;
    data[i + 2] = rgb[2] * (1 - l) + 255 * l;
  }
}

const images = new Map<Role, HTMLImageElement>();
const tinted = new Map<string, HTMLCanvasElement>();
const loads = signal(0);

/**
 * Counts the role PNGs loaded so far. A caller that draws `tintedRoleIcon` reads it so it draws
 * again once an icon that was still loading (`null`) arrives.
 */
export const roleIconLoads: Signal<number> = loads.asReadonly();

/** The role's PNG once it has loaded; starts the load on the first call. */
function loadedImage(role: Role): HTMLImageElement | null {
  let img = images.get(role);
  if (!img) {
    img = new Image();
    img.onload = () => loads.update((n) => n + 1);
    img.src = ROLE_ICON_URLS[role];
    images.set(role, img);
  }
  return img.complete && img.naturalWidth > 0 ? img : null;
}

/** A CSS colour (any syntax the browser paints, `oklch()` included) as sRGB, via a 1 px fill. */
function toRgb(colour: string): [number, number, number] | null {
  const probe = document.createElement('canvas');
  probe.width = probe.height = 1;
  const ctx = probe.getContext('2d', { willReadFrequently: true });
  if (!ctx) return null;
  ctx.fillStyle = colour;
  ctx.fillRect(0, 0, 1, 1);
  const [r, g, b] = ctx.getImageData(0, 0, 1, 1).data;
  return [r, g, b];
}

/**
 * The role's icon tinted to a CSS colour, cached per role and colour; `null` until the PNG has
 * loaded (read `roleIconLoads` to hear when it does) or where no 2D canvas exists.
 */
export function tintedRoleIcon(role: Role, colour: string): HTMLCanvasElement | null {
  const key = `${role}|${colour}`;
  const hit = tinted.get(key);
  if (hit) return hit;
  const img = loadedImage(role);
  if (!img) return null;
  const rgb = toRgb(colour);
  const canvas = document.createElement('canvas');
  canvas.width = img.naturalWidth;
  canvas.height = img.naturalHeight;
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  if (!rgb || !ctx) return null;
  ctx.drawImage(img, 0, 0);
  const pixels = ctx.getImageData(0, 0, canvas.width, canvas.height);
  tintPixels(pixels.data, rgb);
  ctx.putImageData(pixels, 0, 0);
  tinted.set(key, canvas);
  return canvas;
}
