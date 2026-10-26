import { WritableSignal } from '@angular/core';
import { EMPTY, MonoTypeOperatorFunction, catchError } from 'rxjs';
import { ApiError } from '../api/api-client';
import { apiMessage } from './api-message';
import { GENERIC_ERROR_MESSAGE } from './form-errors';

/**
 * A screen load's error tail: a 404 sets `notFound`, any other failure puts its message (or the
 * generic one) in `error`; either way the stream completes empty.
 */
export function notFoundOrError<T>(
  notFound: WritableSignal<boolean>,
  error: WritableSignal<string | null>,
): MonoTypeOperatorFunction<T> {
  return catchError((failure: unknown) => {
    if (failure instanceof ApiError && failure.status === 404) notFound.set(true);
    else error.set(apiMessage(failure, GENERIC_ERROR_MESSAGE));
    return EMPTY;
  });
}
