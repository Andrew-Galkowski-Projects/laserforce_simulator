import { HttpClient, HttpContext, HttpErrorResponse, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, OperatorFunction, catchError, map, pipe, throwError } from 'rxjs';
import { environment } from '../../environments/environment';

/** One entry of the envelope's `errors` list (ADR-0039). */
export interface ApiErrorItem {
  code: string;
  message: string;
  field: string | null;
}

/** Every failed API call: the HTTP status (0 = no response) and a non-empty `errors` list. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly errors: ApiErrorItem[],
  ) {
    super(errors.map((e) => e.message).join(' ') || `API error ${status}`);
    this.name = 'ApiError';
  }
}

export interface PageMeta {
  page: number;
  page_size: number;
  count: number;
  total_pages: number;
  [extra: string]: unknown;
}

export interface Page<T> {
  items: T[];
  meta: PageMeta;
}

/** Query params; an array value repeats its key once per item (`?scope=a&scope=b`). */
export interface ListParams {
  page?: number;
  page_size?: number;
  [filter: string]: string | number | boolean | readonly string[] | undefined;
}

/** Per-request extras, e.g. an `HttpContext` read by the interceptors. */
export interface RequestOptions {
  context?: HttpContext;
}

interface Envelope<T, M = Record<string, unknown>> {
  data: T;
  meta: M;
}

function toHttpParams(params: ListParams = {}): HttpParams {
  let out = new HttpParams();
  for (const [key, value] of Object.entries(params)) {
    if (Array.isArray(value)) {
      for (const item of value) out = out.append(key, item);
    } else if (value !== undefined) {
      out = out.set(key, String(value));
    }
  }
  return out;
}

function envelopeErrors(body: unknown): ApiErrorItem[] | null {
  if (body && typeof body === 'object' && Array.isArray((body as { errors?: unknown }).errors)) {
    return (body as { errors: ApiErrorItem[] }).errors;
  }
  return null;
}

/** Maps an HttpClient failure to an `ApiError`. */
function toApiError(error: HttpErrorResponse): ApiError {
  const status = error.status;
  const errors = envelopeErrors(error.error);
  if (errors && errors.length > 0) {
    return new ApiError(status, errors);
  }
  if (status === 0) {
    return new ApiError(0, [
      { code: 'network_error', message: 'Could not reach the server.', field: null },
    ]);
  }
  return new ApiError(status, [
    { code: 'server_error', message: `The server responded with ${status}.`, field: null },
  ]);
}

/** Turns every HTTP failure into an `ApiError`; anything else is rethrown as-is. */
function apiErrors<T>(): OperatorFunction<T, T> {
  return catchError((error: unknown) =>
    throwError(() => (error instanceof HttpErrorResponse ? toApiError(error) : error)),
  );
}

/**
 * Unwraps a success body (a 204's `null` body unwraps to `undefined`) and turns
 * every HTTP failure into an `ApiError`.
 */
function unwrap<B, T>(project: (body: B) => T): OperatorFunction<B | null, T> {
  return pipe(
    map((body) => (body === null ? (undefined as T) : project(body))),
    apiErrors(),
  );
}

/**
 * The only `HttpClient` user in the app. `path` is relative to
 * `environment.apiBaseUrl` (e.g. `teams/`, `rounds/7/events/`).
 */
@Injectable({ providedIn: 'root' })
export class ApiClient {
  private readonly http = inject(HttpClient);

  get<T>(path: string, params?: ListParams, options: RequestOptions = {}): Observable<T> {
    return this.http
      .get<Envelope<T>>(this.url(path), { params: toHttpParams(params), context: options.context })
      .pipe(unwrap((body) => body.data));
  }

  list<T>(path: string, params?: ListParams, options: RequestOptions = {}): Observable<Page<T>> {
    return this.http
      .get<Envelope<T[], PageMeta>>(this.url(path), {
        params: toHttpParams(params),
        context: options.context,
      })
      .pipe(unwrap((body) => ({ items: body.data, meta: body.meta })));
  }

  post<T>(path: string, body: unknown, options: RequestOptions = {}): Observable<T> {
    return this.http
      .post<Envelope<T>>(this.url(path), body, { context: options.context })
      .pipe(unwrap((res) => res.data));
  }

  patch<T>(path: string, body: unknown, options: RequestOptions = {}): Observable<T> {
    return this.http
      .patch<Envelope<T>>(this.url(path), body, { context: options.context })
      .pipe(unwrap((res) => res.data));
  }

  put<T>(path: string, body: unknown, options: RequestOptions = {}): Observable<T> {
    return this.http
      .put<Envelope<T>>(this.url(path), body, { context: options.context })
      .pipe(unwrap((res) => res.data));
  }

  delete(path: string, options: RequestOptions = {}): Observable<void> {
    return this.http
      .delete<null>(this.url(path), { context: options.context })
      .pipe(unwrap(() => undefined));
  }

  /** Multipart POST: the browser sets the multipart boundary header itself. */
  upload<T>(path: string, formData: FormData, options: RequestOptions = {}): Observable<T> {
    return this.post<T>(path, formData, options);
  }

  /**
   * A non-enveloped GET (e.g. a CSV download behind the JWT): the body as a `Blob`, untouched.
   * Failures still become `ApiError`s; a Blob error body has no envelope, so the status-based
   * fallback applies.
   */
  getRaw(path: string, options: RequestOptions = {}): Observable<Blob> {
    return this.http
      .get(this.url(path), { responseType: 'blob', context: options.context })
      .pipe(apiErrors());
  }

  private url(path: string): string {
    return `${environment.apiBaseUrl}/${path}`;
  }
}
