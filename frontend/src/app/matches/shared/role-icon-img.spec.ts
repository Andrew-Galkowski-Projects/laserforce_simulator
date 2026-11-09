import { TestBed } from '@angular/core/testing';
import { RoleIcon } from './role-icon-img';

describe('RoleIcon', () => {
  async function render(role: string): Promise<HTMLElement> {
    const fixture = TestBed.createComponent(RoleIcon);
    fixture.componentRef.setInput('role', role);
    await fixture.whenStable();
    return fixture.nativeElement as HTMLElement;
  }

  it("sets the img's alt and title to the role's name, for each role", async () => {
    const names: Record<string, string> = {
      commander: 'Commander',
      heavy: 'Heavy',
      scout: 'Scout',
      ammo: 'Ammo',
      medic: 'Medic',
    };
    for (const [role, name] of Object.entries(names)) {
      const img = (await render(role)).querySelector('img')!;
      expect(img.alt).toBe(name);
      expect(img.title).toBe(name);
    }
  });

  it('falls back to the untinted icon where there is no canvas (jsdom)', async () => {
    const img = (await render('heavy')).querySelector('img')!;
    expect(img.getAttribute('src')).toBe('roles/heavy.png');
  });

  describe('with a canvas and the PNG loaded', () => {
    /** An `<img>` that is complete as soon as its `src` is set. */
    class LoadedImage {
      complete = false;
      naturalWidth = 0;
      naturalHeight = 0;
      onload: (() => void) | null = null;
      set src(_url: string) {
        this.complete = true;
        this.naturalWidth = this.naturalHeight = 1;
      }
    }
    const fills: string[] = [];

    beforeEach(() => {
      vi.stubGlobal('Image', LoadedImage);
      vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(
        () =>
          ({
            set fillStyle(v: string) {
              fills.push(v);
            },
            fillRect: () => undefined,
            drawImage: () => undefined,
            getImageData: () => ({ data: new Uint8ClampedArray(4) }),
            putImageData: () => undefined,
          }) as unknown as CanvasRenderingContext2D,
      );
      vi.spyOn(HTMLCanvasElement.prototype, 'toDataURL').mockReturnValue('data:image/png;tinted');
    });

    afterEach(() => {
      vi.unstubAllGlobals();
      vi.restoreAllMocks();
    });

    it("tints the icon to its host's computed --side", async () => {
      const fixture = TestBed.createComponent(RoleIcon);
      fixture.componentRef.setInput('role', 'scout');
      (fixture.nativeElement as HTMLElement).style.setProperty('--side', '#c82814');
      await fixture.whenStable();
      const img = (fixture.nativeElement as HTMLElement).querySelector('img')!;
      expect(img.getAttribute('src')).toBe('data:image/png;tinted');
      expect(fills).toContain('#c82814');
    });
  });

  it('matches the role case-insensitively', async () => {
    const img = (await render('Medic')).querySelector('img')!;
    expect(img.alt).toBe('Medic');
    expect(img.getAttribute('src')).toBe('roles/medic.png');
  });

  it("renders an unknown role's name as text, with no image", async () => {
    const host = await render('sniper');
    expect(host.querySelector('img')).toBeNull();
    expect(host.textContent?.trim()).toBe('sniper');
  });
});
