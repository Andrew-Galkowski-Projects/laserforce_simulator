import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { ROLE_ICON_URLS, tintPixels, tintedRoleIcon } from './role-icon';

describe('tintPixels', () => {
  /** Black, white, mid-grey and a transparent black pixel, all opaque but the last. */
  const pixels = (): Uint8ClampedArray =>
    Uint8ClampedArray.from([0, 0, 0, 255, 255, 255, 255, 255, 128, 128, 128, 200, 0, 0, 0, 0]);
  const at = (data: Uint8ClampedArray, i: number): number[] => [...data.slice(i * 4, i * 4 + 4)];

  it('turns the black line art into the target colour, alpha unchanged', () => {
    const data = pixels();
    tintPixels(data, [200, 40, 60]);
    expect(at(data, 0)).toEqual([200, 40, 60, 255]);
  });

  it('keeps the white fill white', () => {
    const data = pixels();
    tintPixels(data, [200, 40, 60]);
    expect(at(data, 1)).toEqual([255, 255, 255, 255]);
  });

  it('blends a mid-grey pixel half-way between the target and white', () => {
    const data = pixels();
    tintPixels(data, [0, 100, 255]);
    // l = 384 / 765: channel = target * (1 - l) + 255 * l.
    const l = 384 / 765;
    const blend = (c: number): number => Math.round(c * (1 - l) + 255 * l);
    expect(at(data, 2)).toEqual([blend(0), blend(100), blend(255), 200]);
  });

  it('leaves a transparent pixel untouched', () => {
    const data = pixels();
    tintPixels(data, [200, 40, 60]);
    expect(at(data, 3)).toEqual([0, 0, 0, 0]);
  });
});

describe('ROLE_ICON_URLS', () => {
  it('names one PNG per role, relative to the base href', () => {
    expect(ROLE_ICON_URLS).toEqual({
      commander: 'roles/commander.png',
      heavy: 'roles/heavy.png',
      scout: 'roles/scout.png',
      ammo: 'roles/ammo.png',
      medic: 'roles/medic.png',
    });
  });

  it('has each PNG in the public folder', () => {
    // The spec runs from `frontend/` (`npm run test:ci`) or from the repo root, as theme.spec.ts
    // allows for.
    const roots = [join(process.cwd(), 'public'), join(process.cwd(), 'frontend', 'public')];
    for (const url of Object.values(ROLE_ICON_URLS)) {
      expect(
        roots.some((root) => existsSync(join(root, url))),
        url,
      ).toBe(true);
    }
  });
});

describe('tintedRoleIcon', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('is null while the PNG has not loaded (jsdom loads no images)', () => {
    expect(tintedRoleIcon('medic', '#d33a40')).toBeNull();
  });

  describe('once the PNG has loaded', () => {
    /** An `<img>` that is complete as soon as its `src` is set: 2 x 1 px. */
    class LoadedImage {
      complete = false;
      naturalWidth = 0;
      naturalHeight = 0;
      onload: (() => void) | null = null;
      set src(_url: string) {
        this.complete = true;
        this.naturalWidth = 2;
        this.naturalHeight = 1;
      }
    }

    /** The icon: one black pixel and one white one. */
    const ICON = [0, 0, 0, 255, 255, 255, 255, 255];
    let put: Uint8ClampedArray[];

    beforeEach(() => {
      put = [];
      vi.stubGlobal('Image', LoadedImage);
      vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(function (
        this: HTMLCanvasElement,
      ) {
        let fill = '';
        return {
          set fillStyle(v: string) {
            fill = v;
          },
          get fillStyle() {
            return fill;
          },
          fillRect: () => undefined,
          drawImage: () => undefined,
          // A 1 x 1 read is the colour probe: answer the fill as a pixel ('#rrggbb' only).
          getImageData: (_x: number, _y: number, w: number, h: number) => ({
            data:
              w === 1 && h === 1
                ? Uint8ClampedArray.from([
                    parseInt(fill.slice(1, 3), 16),
                    parseInt(fill.slice(3, 5), 16),
                    parseInt(fill.slice(5, 7), 16),
                    255,
                  ])
                : Uint8ClampedArray.from(ICON),
          }),
          putImageData: (img: { data: Uint8ClampedArray }) => put.push(img.data),
        } as unknown as CanvasRenderingContext2D;
      } as never);
    });

    it("returns the icon tinted to the colour, the image's size", () => {
      const canvas = tintedRoleIcon('scout', '#c82814')!;
      expect(canvas).toBeInstanceOf(HTMLCanvasElement);
      expect([canvas.width, canvas.height]).toEqual([2, 1]);
      expect([...put[0]]).toEqual([200, 40, 20, 255, 255, 255, 255, 255]);
    });

    it('caches per role and colour', () => {
      const first = tintedRoleIcon('ammo', '#102030');
      expect(tintedRoleIcon('ammo', '#102030')).toBe(first);
      expect(tintedRoleIcon('ammo', '#304050')).not.toBe(first);
      expect(tintedRoleIcon('heavy', '#102030')).not.toBe(first);
      expect(put).toHaveLength(3);
    });
  });
});
