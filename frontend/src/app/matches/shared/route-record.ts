// Loads the record for the route's `:id` (shared by the Match and Round detail stubs).
import { Signal, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute } from '@angular/router';
import { EMPTY, Observable, catchError, map, switchMap } from 'rxjs';
import { ApiError } from '../../api/api-client';

/** The route's record and its load state. */
export interface RouteRecord<T> {
  /** The loaded record, or `null` before it arrives (and after a failed load). */
  readonly record: Signal<T | null>;
  /** True once the GET answered 404 (a missing record, or another Account's). */
  readonly notFound: Signal<boolean>;
  /** Any other load failure, as a message for an alert. */
  readonly loadError: Signal<string | null>;
}

/**
 * Fetches `fetch(id)` for the route's `:id` (again whenever the id changes). A 404 sets
 * `notFound`; any other failure sets `loadError` ("Could not load the <noun>."). Call in an
 * injection context.
 */
export function loadRouteRecord<T>(
  noun: string,
  fetch: (id: number) => Observable<T>,
): RouteRecord<T> {
  const ids = inject(ActivatedRoute).paramMap.pipe(map((params) => Number(params.get('id'))));
  return loadRecord(noun, ids, fetch);
}

/**
 * `loadRouteRecord` for any stream of ids: fetches `fetch(id)` for each id `ids` emits, with the
 * same 404 / error states. Call in an injection context.
 */
export function loadRecord<T>(
  noun: string,
  ids: Observable<number>,
  fetch: (id: number) => Observable<T>,
): RouteRecord<T> {
  const record = signal<T | null>(null);
  const notFound = signal(false);
  const loadError = signal<string | null>(null);

  ids
    .pipe(
      switchMap((id) => {
        record.set(null);
        notFound.set(false);
        loadError.set(null);
        return fetch(id).pipe(
          catchError((error: unknown) => {
            if (error instanceof ApiError && error.status === 404) {
              notFound.set(true);
            } else {
              const detail = error instanceof ApiError ? ` ${error.message}` : '';
              loadError.set(`Could not load the ${noun}.${detail}`);
            }
            return EMPTY;
          }),
        );
      }),
      takeUntilDestroyed(),
    )
    .subscribe((loaded) => record.set(loaded));

  return {
    record: record.asReadonly(),
    notFound: notFound.asReadonly(),
    loadError: loadError.asReadonly(),
  };
}
