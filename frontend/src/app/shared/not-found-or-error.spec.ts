import { signal } from '@angular/core';
import { Observable, of, throwError } from 'rxjs';
import { ApiError } from '../api/api-client';
import { GENERIC_ERROR_MESSAGE } from './form-errors';
import { notFoundOrError } from './not-found-or-error';

/** Runs `source` through the helper; returns the signals and what the stream did. */
function run(source: Observable<number>) {
  const notFound = signal(false);
  const error = signal<string | null>(null);
  const values: number[] = [];
  let completed = false;
  source.pipe(notFoundOrError(notFound, error)).subscribe({
    next: (value) => values.push(value),
    complete: () => (completed = true),
  });
  return { notFound: notFound(), error: error(), values, completed };
}

const apiError = (status: number, message: string): ApiError =>
  new ApiError(status, [{ code: 'x', message, field: null }]);

describe('notFoundOrError', () => {
  it('passes values through untouched', () => {
    expect(run(of(1, 2))).toEqual({
      notFound: false,
      error: null,
      values: [1, 2],
      completed: true,
    });
  });

  it('sets notFound on a 404 and completes empty, with no error message', () => {
    const result = run(throwError(() => apiError(404, 'Not found.')));
    expect(result).toEqual({ notFound: true, error: null, values: [], completed: true });
  });

  it("puts any other ApiError's message in error and completes empty", () => {
    const result = run(throwError(() => apiError(500, 'Database down.')));
    expect(result).toEqual({
      notFound: false,
      error: 'Database down.',
      values: [],
      completed: true,
    });
  });

  it('falls back to the generic message for a non-API failure', () => {
    const result = run(throwError(() => new Error('network')));
    expect(result.error).toBe(GENERIC_ERROR_MESSAGE);
    expect(result.notFound).toBe(false);
  });
});
