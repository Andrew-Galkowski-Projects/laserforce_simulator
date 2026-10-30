import { DestroyRef, Signal, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import {
  EMPTY,
  Observable,
  Subject,
  catchError,
  combineLatest,
  of,
  startWith,
  switchMap,
  tap,
} from 'rxjs';
import { apiMessage } from '../../shared/api-message';
import { GENERIC_ERROR_MESSAGE } from '../../shared/form-errors';
import { notFoundOrError } from '../../shared/not-found-or-error';
import { SeasonContext } from './season-context';

/** A Season screen's own GET, as `injectSeasonLoad` exposes it. */
export interface SeasonLoad<T> {
  /** The last answer; null until the first one (a reload keeps the previous answer meanwhile). */
  readonly data: Signal<T | null>;
  /** The failed GET's message (the server's, else the generic one); null while loading or loaded. */
  readonly loadError: Signal<string | null>;
  /** A 404 answered, with `splitNotFound` only; false otherwise. */
  readonly notFound: Signal<boolean>;
  /** I2: re-sends only this GET (with the latest `params`), clearing the error first. */
  retry(): void;
}

export interface SeasonLoadOptions<P> {
  /** Re-fetch on each emission, passing it to `fetch` (e.g. the URL's sort). No fetch before the first. */
  params?: Observable<P>;
  /** A 404 sets `notFound` instead of `loadError` (a screen with its own not-found text). */
  splitNotFound?: boolean;
}

/**
 * The load + Retry chain every Season screen's GET shares (spec #444 I2): fetches
 * `fetch(seasonId)` for the layout's Season (`SeasonContext`, never null inside a screen) once at
 * construction, and again on each `params` emission or `retry()`; a newer fetch cancels an older
 * one. A failure shows its message in `loadError` (or, with `splitNotFound`, a 404 sets
 * `notFound`) and the stream lives on for Retry. Call it in a Season screen's injection context:
 *
 *   private readonly load = injectSeasonLoad((id) => inject(SeasonsService).awards(id));
 *   protected readonly awards = this.load.data;
 *
 * Unsubscribes when the screen is destroyed.
 */
export function injectSeasonLoad<T>(
  fetch: (seasonId: number) => Observable<T>,
  options?: { splitNotFound?: boolean },
): SeasonLoad<T>;
export function injectSeasonLoad<T, P>(
  fetch: (seasonId: number, params: P) => Observable<T>,
  options: SeasonLoadOptions<P>,
): SeasonLoad<T>;
export function injectSeasonLoad<T, P>(
  fetch: (seasonId: number, params: P) => Observable<T>,
  options: SeasonLoadOptions<P> = {},
): SeasonLoad<T> {
  const context = inject(SeasonContext);
  const data = signal<T | null>(null);
  const loadError = signal<string | null>(null);
  const notFound = signal(false);
  const reloads = new Subject<void>();
  const params = options.params ?? of(undefined as P);

  combineLatest([params, reloads.pipe(startWith(undefined))])
    .pipe(
      tap(() => {
        loadError.set(null);
        notFound.set(false);
      }),
      switchMap(([latest]) =>
        fetch(context.requireSeasonId(), latest).pipe(
          options.splitNotFound
            ? notFoundOrError<T>(notFound, loadError)
            : catchError((failure: unknown) => {
                loadError.set(apiMessage(failure, GENERIC_ERROR_MESSAGE));
                return EMPTY;
              }),
        ),
      ),
      takeUntilDestroyed(inject(DestroyRef)),
    )
    .subscribe((loaded) => data.set(loaded));

  return {
    data: data.asReadonly(),
    loadError: loadError.asReadonly(),
    notFound: notFound.asReadonly(),
    retry: () => reloads.next(),
  };
}
