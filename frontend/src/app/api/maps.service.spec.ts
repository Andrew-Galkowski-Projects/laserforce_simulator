import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../environments/environment';
import { authInterceptor } from '../auth/auth.interceptor';
import { ApiError } from './api-client';
import { ArenaMap } from './maps.models';
import { MapsService } from './maps.service';

const BASE = environment.apiBaseUrl;

const MAP: ArenaMap = {
  id: 2,
  name: 'Warehouse',
  image: 'http://localhost:8000/media/maps/warehouse.png',
  is_default: false,
  img_width: 800,
  img_height: 600,
  created_at: '2026-10-21T12:00:00Z',
  has_confirmed_config: true,
};

describe('MapsService', () => {
  let maps: MapsService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(withInterceptors([authInterceptor])),
        provideHttpClientTesting(),
      ],
    });
    maps = TestBed.inject(MapsService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('lists Maps with filters and ordering', async () => {
    const result = firstValueFrom(
      maps.list({ has_confirmed_config: true, is_default: false, ordering: '-created_at' }),
    );
    const req = http.expectOne((r) => r.url === `${BASE}/maps/`);
    expect(req.request.method).toBe('GET');
    expect(req.request.params.get('has_confirmed_config')).toBe('true');
    expect(req.request.params.get('is_default')).toBe('false');
    expect(req.request.params.get('ordering')).toBe('-created_at');
    const meta = { page: 1, page_size: 20, count: 1, total_pages: 1 };
    req.flush({ data: [MAP], meta });
    expect(await result).toEqual({ items: [MAP], meta });
  });

  it('gets one Map', async () => {
    const result = firstValueFrom(maps.get(2));
    const req = http.expectOne(`${BASE}/maps/2/`);
    expect(req.request.method).toBe('GET');
    req.flush({ data: MAP, meta: {} });
    expect(await result).toEqual(MAP);
  });

  it('surfaces an unknown Map as ApiError 404', async () => {
    const result = firstValueFrom(maps.get(99)).catch((e: unknown) => e);
    http
      .expectOne(`${BASE}/maps/99/`)
      .flush(
        { errors: [{ code: 'not_found', message: 'Not found.', field: null }], meta: {} },
        { status: 404, statusText: 'Not Found' },
      );
    const err = (await result) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(404);
  });

  it('uploads a Map as multipart FormData (name + image), not JSON', async () => {
    const image = new File([new Uint8Array([137, 80, 78, 71])], 'warehouse.png', {
      type: 'image/png',
    });
    const result = firstValueFrom(maps.upload({ name: 'Warehouse', image }));
    const req = http.expectOne(`${BASE}/maps/`);
    expect(req.request.method).toBe('POST');
    const body = req.request.body as unknown;
    expect(body).toBeInstanceOf(FormData);
    const form = body as FormData;
    expect(form.get('name')).toBe('Warehouse');
    const sent = form.get('image') as File;
    expect(sent).toBeInstanceOf(File);
    expect(sent.name).toBe('warehouse.png');
    expect(req.request.headers.has('Content-Type')).toBe(false);
    expect(req.request.detectContentTypeHeader()).toBeNull();
    req.flush({ data: MAP, meta: {} }, { status: 201, statusText: 'Created' });
    expect(await result).toEqual(MAP);
  });

  it('deletes a Map (204 resolves to undefined)', async () => {
    const result = firstValueFrom(maps.delete(2));
    const req = http.expectOne(`${BASE}/maps/2/`);
    expect(req.request.method).toBe('DELETE');
    req.flush(null, { status: 204, statusText: 'No Content' });
    expect(await result).toBeUndefined();
  });

  it('reports a Map still used by a Season as ApiError 409', async () => {
    const result = firstValueFrom(maps.delete(2)).catch((e: unknown) => e);
    http.expectOne(`${BASE}/maps/2/`).flush(
      {
        errors: [
          {
            code: 'map_in_use',
            message: 'This map is used by a Season that is not completed.',
            field: null,
          },
        ],
        meta: {},
      },
      { status: 409, statusText: 'Conflict' },
    );
    const err = (await result) as ApiError;
    expect(err.status).toBe(409);
    expect(err.errors[0].code).toBe('map_in_use');
  });
});
