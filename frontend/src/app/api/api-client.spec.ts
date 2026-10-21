import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../environments/environment';
import { ApiClient, ApiError } from './api-client';

const BASE = environment.apiBaseUrl;

describe('ApiClient', () => {
  let api: ApiClient;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    api = TestBed.inject(ApiClient);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('GETs a path under apiBaseUrl and unwraps the envelope data', async () => {
    const result = firstValueFrom(api.get<{ id: number }>('teams/3/'));
    const req = http.expectOne(`${BASE}/teams/3/`);
    expect(req.request.method).toBe('GET');
    req.flush({ data: { id: 3 }, meta: {} });
    expect(await result).toEqual({ id: 3 });
  });

  it('lists a page: items from data, meta passed through, undefined params dropped', async () => {
    const result = firstValueFrom(
      api.list<{ id: number }>('teams/', { page: 2, page_size: 5, name: 'red', team: undefined }),
    );
    const req = http.expectOne((r) => r.url === `${BASE}/teams/`);
    expect(req.request.method).toBe('GET');
    expect(req.request.params.keys().sort()).toEqual(['name', 'page', 'page_size']);
    expect(req.request.params.get('page')).toBe('2');
    expect(req.request.params.get('name')).toBe('red');
    const meta = { page: 2, page_size: 5, count: 7, total_pages: 2 };
    req.flush({ data: [{ id: 6 }, { id: 7 }], meta });
    expect(await result).toEqual({ items: [{ id: 6 }, { id: 7 }], meta });
  });

  it('passes params on a plain GET too', async () => {
    const result = firstValueFrom(api.get<string[]>('things/', { flag: true, skip: undefined }));
    const req = http.expectOne((r) => r.url === `${BASE}/things/`);
    expect(req.request.params.keys()).toEqual(['flag']);
    expect(req.request.params.get('flag')).toBe('true');
    req.flush({ data: ['x'], meta: {} });
    expect(await result).toEqual(['x']);
  });

  it('POSTs a JSON body and unwraps the created object', async () => {
    const result = firstValueFrom(api.post<{ id: number }>('teams/', { name: 'Red' }));
    const req = http.expectOne(`${BASE}/teams/`);
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual({ name: 'Red' });
    req.flush({ data: { id: 9 }, meta: {} }, { status: 201, statusText: 'Created' });
    expect(await result).toEqual({ id: 9 });
  });

  it('POST resolves with no value on a 204', async () => {
    const result = firstValueFrom(api.post<void>('token/logout/', null));
    const req = http.expectOne(`${BASE}/token/logout/`);
    expect(req.request.body).toBeNull();
    req.flush(null, { status: 204, statusText: 'No Content' });
    expect(await result).toBeUndefined();
  });

  it('PATCHes a JSON body and unwraps the updated object', async () => {
    const result = firstValueFrom(api.patch<{ id: number }>('teams/9/', { name: 'Blue' }));
    const req = http.expectOne(`${BASE}/teams/9/`);
    expect(req.request.method).toBe('PATCH');
    expect(req.request.body).toEqual({ name: 'Blue' });
    req.flush({ data: { id: 9 }, meta: {} });
    expect(await result).toEqual({ id: 9 });
  });

  it('DELETEs and completes with no value on a 204', async () => {
    const result = firstValueFrom(api.delete('players/4/'));
    const req = http.expectOne(`${BASE}/players/4/`);
    expect(req.request.method).toBe('DELETE');
    req.flush(null, { status: 204, statusText: 'No Content' });
    expect(await result).toBeUndefined();
  });

  it('uploads FormData as a multipart POST without a JSON content type', async () => {
    const form = new FormData();
    form.append('name', 'Arena');
    const result = firstValueFrom(api.upload<{ id: number }>('maps/', form));
    const req = http.expectOne(`${BASE}/maps/`);
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toBe(form);
    expect(req.request.headers.has('Content-Type')).toBe(false);
    expect(req.request.detectContentTypeHeader()).toBeNull();
    req.flush({ data: { id: 1 }, meta: {} }, { status: 201, statusText: 'Created' });
    expect(await result).toEqual({ id: 1 });
  });

  describe('errors', () => {
    async function failure(promise: Promise<unknown>): Promise<ApiError> {
      try {
        await promise;
      } catch (err) {
        return err as ApiError;
      }
      throw new Error('expected the call to fail');
    }

    it('maps an envelope 400 to ApiError with its field errors', async () => {
      const result = failure(firstValueFrom(api.post('teams/', { name: '' })));
      const errors = [
        { code: 'required', message: 'This field is required.', field: 'name' },
        { code: 'invalid', message: 'Bad.', field: null },
      ];
      http
        .expectOne(`${BASE}/teams/`)
        .flush({ errors, meta: {} }, { status: 400, statusText: 'Bad Request' });
      const err = await result;
      expect(err).toBeInstanceOf(ApiError);
      expect(err).toBeInstanceOf(Error);
      expect(err.status).toBe(400);
      expect(err.errors).toEqual(errors);
    });

    it('maps a cross-Account 404 on a list to ApiError 404', async () => {
      const result = failure(firstValueFrom(api.list('teams/', { page: 99 })));
      http
        .expectOne((r) => r.url === `${BASE}/teams/`)
        .flush(
          { errors: [{ code: 'not_found', message: 'Invalid page.', field: null }], meta: {} },
          { status: 404, statusText: 'Not Found' },
        );
      const err = await result;
      expect(err.status).toBe(404);
      expect(err.errors[0].code).toBe('not_found');
    });

    it('maps a network failure (status 0) to network_error', async () => {
      const result = failure(firstValueFrom(api.get('teams/')));
      http.expectOne(`${BASE}/teams/`).error(new ProgressEvent('error'), { status: 0 });
      const err = await result;
      expect(err).toBeInstanceOf(ApiError);
      expect(err.status).toBe(0);
      expect(err.errors).toHaveLength(1);
      expect(err.errors[0].code).toBe('network_error');
      expect(err.errors[0].field).toBeNull();
      expect(err.errors[0].message).not.toBe('');
    });

    it('maps a non-JSON 500 to server_error', async () => {
      const result = failure(firstValueFrom(api.delete('players/1/')));
      http
        .expectOne(`${BASE}/players/1/`)
        .flush('<h1>Server Error (500)</h1>', { status: 500, statusText: 'Server Error' });
      const err = await result;
      expect(err.status).toBe(500);
      expect(err.errors).toEqual([expect.objectContaining({ code: 'server_error', field: null })]);
    });

    it('never yields an empty errors list', async () => {
      const result = failure(firstValueFrom(api.patch('teams/1/', {})));
      http
        .expectOne(`${BASE}/teams/1/`)
        .flush({ errors: [], meta: {} }, { status: 400, statusText: 'Bad Request' });
      const err = await result;
      expect(err.status).toBe(400);
      expect(err.errors.length).toBeGreaterThan(0);
    });

    it('also maps upload failures', async () => {
      const result = failure(firstValueFrom(api.upload('maps/', new FormData())));
      http.expectOne(`${BASE}/maps/`).error(new ProgressEvent('error'), { status: 0 });
      expect((await result).errors[0].code).toBe('network_error');
    });
  });
});
