import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { MATERIAL_ANIMATIONS } from '@angular/material/core';
import { By } from '@angular/platform-browser';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { environment } from '../../../environments/environment';
import { ArenaMap } from '../../api/maps.models';
import { signIn } from '../../api/testing/auth-fixtures';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';
import { MapEditor } from '../map-editor/map-editor';
import { releaseMapsLoads } from '../testing/maps-loads';

const BASE = environment.apiBaseUrl;
const LIST_URL = `${BASE}/maps/`;
const IN_USE = 'This map is used by a Season that is not completed.';

function arenaMap(overrides: Partial<ArenaMap> = {}): ArenaMap {
  return {
    id: 1,
    name: 'Syracuse Laser Tag',
    image: 'http://localhost:8000/media/maps/syracuse.png',
    is_default: true,
    img_width: 1200,
    img_height: 800,
    created_at: '2026-10-01T09:00:00Z',
    has_confirmed_config: true,
    confirmed_zone_size: 40,
    ...overrides,
  };
}

const SYRACUSE = arenaMap();
const WAREHOUSE = arenaMap({
  id: 2,
  name: 'Warehouse',
  image: 'http://localhost:8000/media/maps/warehouse.jpg',
  is_default: false,
  img_width: 640,
  img_height: 480,
  has_confirmed_config: false,
  confirmed_zone_size: null,
});
const BLANK = arenaMap({ id: 3, name: 'Blank', image: null, is_default: false });

const png = (): Blob => new Blob([new Uint8Array([137, 80, 78, 71])], { type: 'image/png' });

function errorBody(code: string, message: string, field: string | null = null): object {
  return { errors: [{ code, message, field }], meta: {} };
}

describe('Map list page', () => {
  let http: HttpTestingController;
  let harness: RouterTestingHarness;
  // jsdom has no object URLs: stub the pair and restore it after each test.
  const url = URL as unknown as Record<string, unknown>;
  const before = { create: url['createObjectURL'], revoke: url['revokeObjectURL'] };
  let create: ReturnType<typeof vi.fn>;
  let revoke: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    let n = 0;
    create = vi.fn(() => `blob:thumb-${++n}`);
    revoke = vi.fn();
    url['createObjectURL'] = create;
    url['revokeObjectURL'] = revoke;
    TestBed.configureTestingModule({
      providers: [
        provideRouter(routes),
        provideHttpClient(withInterceptors([authInterceptor])),
        provideHttpClientTesting(),
        // jsdom fires no animationend, so a dialog would never finish closing.
        { provide: MATERIAL_ANIMATIONS, useValue: { animationsDisabled: true } },
      ],
    });
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    document.querySelector('.cdk-overlay-container')?.remove();
    releaseMapsLoads();
    http.verify();
    url['createObjectURL'] = before.create;
    url['revokeObjectURL'] = before.revoke;
  });

  /** Opens `/maps` and answers the list with `maps` (`null`: a 500). */
  async function open(maps: ArenaMap[] | null = [SYRACUSE, WAREHOUSE]): Promise<void> {
    await signIn();
    harness = await RouterTestingHarness.create();
    const nav = harness.navigateByUrl('/maps');
    await harness.fixture.whenStable();
    const req = http.expectOne((r) => r.url === LIST_URL);
    expect(req.request.method).toBe('GET');
    if (maps === null) {
      req.flush(errorBody('server_error', 'Server error.'), {
        status: 500,
        statusText: 'Server Error',
      });
    } else {
      const meta = { page: 1, page_size: 100, count: maps.length, total_pages: 1 };
      req.flush({ data: maps, meta });
    }
    await nav;
    await harness.fixture.whenStable();
  }

  /** Answers every pending thumbnail GET with a PNG. */
  async function answerThumbs(): Promise<void> {
    for (const req of http.match((r) => /\/maps\/\d+\/image\/$/.test(r.url))) {
      req.flush(png());
    }
    await harness.fixture.whenStable();
  }

  const page = (): HTMLElement => harness.routeNativeElement as HTMLElement;
  const text = (): string => page().textContent?.replace(/\s+/g, ' ') ?? '';
  const routerUrl = (): string => TestBed.inject(Router).url;
  const cards = (): HTMLElement[] => [...page().querySelectorAll<HTMLElement>('article')];
  const card = (name: string): HTMLElement => {
    const found = cards().find((c) => c.querySelector('h2')?.textContent?.includes(name));
    if (!found) throw new Error(`no card for ${name}`);
    return found;
  };
  const cardText = (name: string): string => card(name).textContent?.replace(/\s+/g, ' ') ?? '';
  const dialog = (): HTMLElement | null => document.querySelector('mat-dialog-container');

  function buttonIn(root: ParentNode | null, label: string): HTMLButtonElement {
    const b = [...(root?.querySelectorAll('button') ?? [])].find(
      (el) => el.textContent?.trim() === label,
    );
    if (!b) throw new Error(`no ${label} button`);
    return b;
  }

  describe('cards', () => {
    it('lists the maps oldest first, up to the page-size cap', async () => {
      await signIn();
      harness = await RouterTestingHarness.create();
      const nav = harness.navigateByUrl('/maps');
      await harness.fixture.whenStable();
      const req = http.expectOne((r) => r.url === LIST_URL);
      expect(req.request.params.get('ordering')).toBe('created_at');
      expect(req.request.params.get('page_size')).toBe('100');
      req.flush({ data: [], meta: { page: 1, page_size: 100, count: 0, total_pages: 1 } });
      await nav;
    });

    it('follows every page of the list and renders all its cards', async () => {
      await signIn();
      harness = await RouterTestingHarness.create();
      const nav = harness.navigateByUrl('/maps');
      await harness.fixture.whenStable();
      const meta = { page_size: 100, count: 3, total_pages: 2 };
      const first = http.expectOne((r) => r.url === LIST_URL);
      first.flush({ data: [SYRACUSE, WAREHOUSE], meta: { ...meta, page: 1 } });
      await harness.fixture.whenStable();
      const second = http.expectOne((r) => r.url === LIST_URL);
      expect(second.request.params.get('page')).toBe('2');
      expect(second.request.params.get('ordering')).toBe('created_at');
      expect(second.request.params.get('page_size')).toBe('100');
      second.flush({ data: [BLANK], meta: { ...meta, page: 2 } });
      await nav;
      await harness.fixture.whenStable();
      expect(cards().map((c) => c.querySelector('h2')?.textContent?.trim())).toEqual([
        expect.stringContaining('Syracuse Laser Tag'),
        expect.stringContaining('Warehouse'),
        expect.stringContaining('Blank'),
      ]);
      await answerThumbs();
    });

    it('shows a card per map with its name and pixel size', async () => {
      await open();
      expect(cards().map((c) => c.querySelector('h2')?.textContent?.trim())).toEqual([
        expect.stringContaining('Syracuse Laser Tag'),
        expect.stringContaining('Warehouse'),
      ]);
      expect(cardText('Syracuse Laser Tag')).toContain('1200 × 800 px');
      expect(cardText('Warehouse')).toContain('640 × 480 px');
    });

    it('badges default maps only', async () => {
      await open();
      expect(cardText('Syracuse Laser Tag')).toContain('Default');
      expect(cardText('Warehouse')).not.toContain('Default');
    });

    it('shows "Configured — N px zones" with the confirmed Zone size, else "No zone config"', async () => {
      await open();
      expect(cardText('Syracuse Laser Tag')).toContain('Configured — 40px zones');
      expect(cardText('Syracuse Laser Tag')).not.toContain('No zone config');
      expect(cardText('Warehouse')).toContain('No zone config');
      expect(cardText('Warehouse')).not.toContain('Configured');
    });

    it('links Open Editor to /maps/:id/editor', async () => {
      await open();
      const link = [...card('Warehouse').querySelectorAll('a')].find(
        (a) => a.textContent?.trim() === 'Open Editor',
      );
      expect(link?.getAttribute('href')).toBe('/maps/2/editor');
      link?.click();
      await harness.fixture.whenStable();
      expect(routerUrl()).toBe('/maps/2/editor');
      expect(harness.fixture.debugElement.query(By.directive(MapEditor))).not.toBeNull();
    });

    it('shows "No maps found." for an empty list', async () => {
      await open([]);
      expect(cards()).toEqual([]);
      expect(text()).toContain('No maps found.');
    });

    it('shows a load error instead of the cards when the list fails', async () => {
      await open(null);
      expect(cards()).toEqual([]);
      expect(page().querySelector('[role="alert"]')?.textContent).toContain(
        'Could not load the maps.',
      );
      expect(text()).not.toContain('No maps found.');
    });
  });

  describe('thumbnails', () => {
    it("fetches each map's original image through the API into an object URL", async () => {
      await open();
      const reqs = http.match((r) => /\/maps\/\d+\/image\/$/.test(r.url));
      expect(reqs.map((r) => r.request.url).sort()).toEqual([
        `${BASE}/maps/1/image/`,
        `${BASE}/maps/2/image/`,
      ]);
      expect(reqs.every((r) => r.request.responseType === 'blob')).toBe(true);
      const blobs = reqs.map(() => png());
      reqs.forEach((r, i) => r.flush(blobs[i]));
      await harness.fixture.whenStable();
      expect(create.mock.calls.map((c) => c[0])).toEqual(blobs);
      const img = card('Syracuse Laser Tag').querySelector('img');
      expect(img?.getAttribute('src')).toMatch(/^blob:thumb-\d$/);
      expect(img?.getAttribute('alt')).toBe('Syracuse Laser Tag');
    });

    it('requests no image for a map without one and shows "No image"', async () => {
      await open([BLANK]);
      http.expectNone(`${BASE}/maps/3/image/`);
      expect(card('Blank').querySelector('img')).toBeNull();
      expect(cardText('Blank')).toContain('No image');
    });

    it('shows "No image" when the thumbnail fetch fails', async () => {
      await open([WAREHOUSE]);
      const body = new Blob([JSON.stringify(errorBody('not_found', 'Not found.'))], {
        type: 'application/json',
      });
      http.expectOne(`${BASE}/maps/2/image/`).flush(body, { status: 404, statusText: 'Not Found' });
      await harness.fixture.whenStable();
      expect(card('Warehouse').querySelector('img')).toBeNull();
      expect(cardText('Warehouse')).toContain('No image');
    });

    it('revokes every object URL when the page is left', async () => {
      await open();
      await answerThumbs();
      expect(revoke).not.toHaveBeenCalled();
      await harness.navigateByUrl('/maps/1/editor');
      expect(revoke.mock.calls.map((c) => c[0]).sort()).toEqual(['blob:thumb-1', 'blob:thumb-2']);
    });
  });

  describe('upload', () => {
    const nameInput = (): HTMLInputElement => {
      const el = page().querySelector<HTMLInputElement>('input[formcontrolname="name"]');
      if (!el) throw new Error('no name input');
      return el;
    };
    const fileInput = (): HTMLInputElement => {
      const el = page().querySelector<HTMLInputElement>('input[type="file"]');
      if (!el) throw new Error('no file input');
      return el;
    };

    function typeName(name: string): void {
      nameInput().value = name;
      nameInput().dispatchEvent(new Event('input'));
    }

    function pickFile(file: File | null): void {
      Object.defineProperty(fileInput(), 'files', {
        configurable: true,
        value: file ? [file] : [],
      });
      fileInput().dispatchEvent(new Event('change'));
    }

    async function submit(): Promise<void> {
      buttonIn(page(), 'Upload & Edit').click();
      await harness.fixture.whenStable();
    }

    const imageFile = (): File => new File([png()], 'arena.png', { type: 'image/png' });
    const fieldError = (field: 'name' | 'image'): string =>
      page()
        .querySelector(`[data-field-error="${field}"]`)
        ?.textContent?.replace(/\s+/g, ' ')
        .trim() ?? '';

    it('carries the Django form constraints on its inputs', async () => {
      await open();
      expect(nameInput().required).toBe(true);
      expect(nameInput().getAttribute('maxlength')).toBe('100');
      expect(fileInput().required).toBe(true);
      expect(fileInput().getAttribute('accept')).toBe('image/png,image/jpeg,image/jpg');
    });

    it('POSTs name + image as multipart and lands on the new map editor', async () => {
      await open();
      typeName('Warehouse');
      const file = imageFile();
      pickFile(file);
      await submit();
      const req = http.expectOne((r) => r.url === LIST_URL && r.method === 'POST');
      const body = req.request.body as FormData;
      expect(body).toBeInstanceOf(FormData);
      expect(body.get('name')).toBe('Warehouse');
      expect((body.get('image') as File).name).toBe('arena.png');
      req.flush(
        { data: arenaMap({ id: 9, name: 'Warehouse' }), meta: {} },
        {
          status: 201,
          statusText: 'Created',
        },
      );
      await harness.fixture.whenStable();
      expect(routerUrl()).toBe('/maps/9/editor');
      expect(harness.fixture.debugElement.query(By.directive(MapEditor))).not.toBeNull();
    });

    it('sends nothing and marks both fields required when submitted empty', async () => {
      await open();
      await submit();
      http.expectNone((r) => r.method === 'POST');
      expect(fieldError('name')).toBe('This field is required.');
      expect(fieldError('image')).toBe('This field is required.');
      expect(routerUrl()).toBe('/maps');
    });

    it('sends nothing for a name over 100 characters', async () => {
      await open();
      typeName('x'.repeat(101));
      pickFile(imageFile());
      await submit();
      http.expectNone((r) => r.method === 'POST');
      expect(fieldError('name')).toBe('Ensure this value has at most 100 characters (it has 101).');
    });

    it.each([
      ['name', 'required', 'This field is required.'],
      ['name', 'max_length', 'Ensure this value has at most 100 characters (it has 101).'],
      [
        'image',
        'invalid_image',
        'Upload a valid image. The file you uploaded was either not an image or a corrupted image.',
      ],
    ] as const)('shows a server %s %s error inline on that field', async (field, code, message) => {
      await open();
      typeName('   ');
      pickFile(imageFile());
      await submit();
      http
        .expectOne((r) => r.url === LIST_URL && r.method === 'POST')
        .flush(errorBody(code, message, field), { status: 400, statusText: 'Bad Request' });
      await harness.fixture.whenStable();
      expect(fieldError(field)).toBe(message);
      expect(routerUrl()).toBe('/maps');
      expect(buttonIn(page(), 'Upload & Edit').disabled).toBe(false);
    });

    it('clears the server image error when another file is picked', async () => {
      await open();
      typeName('Warehouse');
      pickFile(imageFile());
      await submit();
      http
        .expectOne((r) => r.method === 'POST')
        .flush(errorBody('invalid_image', 'Upload a valid image.', 'image'), {
          status: 400,
          statusText: 'Bad Request',
        });
      await harness.fixture.whenStable();
      pickFile(imageFile());
      await harness.fixture.whenStable();
      expect(fieldError('image')).toBe('');
    });
  });

  describe('delete', () => {
    async function clickDelete(name: string): Promise<void> {
      buttonIn(card(name), 'Delete').click();
      await harness.fixture.whenStable();
    }

    it('asks first, naming the map and what is lost', async () => {
      await open();
      await clickDelete('Warehouse');
      expect(dialog()?.textContent).toContain(
        'Delete “Warehouse”? This permanently removes its zone config, bases, elevation, walls, ' +
          'and sight lines. Saved game rounds keep their results but lose the map link. ' +
          'This cannot be undone.',
      );
      http.expectNone((r) => r.method === 'DELETE');
    });

    it('sends nothing when the dialog is cancelled', async () => {
      await open();
      await clickDelete('Warehouse');
      buttonIn(dialog(), 'Cancel').click();
      await harness.fixture.whenStable();
      expect(dialog()).toBeNull();
      http.expectNone((r) => r.method === 'DELETE');
      expect(cards()).toHaveLength(2);
    });

    it('DELETEs on confirm, removes the card and revokes its thumbnail', async () => {
      await open();
      await answerThumbs();
      const warehouseSrc = card('Warehouse').querySelector('img')?.getAttribute('src');
      await clickDelete('Warehouse');
      buttonIn(dialog(), 'Delete').click();
      await harness.fixture.whenStable();
      const req = http.expectOne(`${BASE}/maps/2/`);
      expect(req.request.method).toBe('DELETE');
      req.flush(null, { status: 204, statusText: 'No Content' });
      await harness.fixture.whenStable();
      expect(cards().map((c) => c.querySelector('h2')?.textContent)).toEqual([
        expect.stringContaining('Syracuse Laser Tag'),
      ]);
      expect(revoke).toHaveBeenCalledWith(warehouseSrc);
      expect(text()).toContain('Deleted map “Warehouse”.');
    });

    it("keeps the card and shows the server's message when the map is in use (409)", async () => {
      await open();
      await clickDelete('Warehouse');
      buttonIn(dialog(), 'Delete').click();
      await harness.fixture.whenStable();
      http
        .expectOne(`${BASE}/maps/2/`)
        .flush(errorBody('map_in_use', IN_USE), { status: 409, statusText: 'Conflict' });
      await harness.fixture.whenStable();
      expect(cards()).toHaveLength(2);
      expect(page().querySelector('[role="alert"]')?.textContent).toContain(
        `Could not delete “Warehouse”: ${IN_USE}`,
      );
    });
  });
});
