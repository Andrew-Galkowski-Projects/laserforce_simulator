import { TestBed } from '@angular/core/testing';
import { Observable, Subject, of, throwError } from 'rxjs';
import { ApiError } from '../../api/api-client';
import { GENERIC_ERROR_MESSAGE } from '../../shared/form-errors';
import { LeagueContext } from '../stats-shared/league-context';
import { SeasonContext } from './season-context';
import { SeasonLoad, SeasonLoadOptions, injectSeasonLoad } from './season-load';

const notFound = new ApiError(404, [{ code: 'not_found', message: 'Not found.', field: null }]);
const serverError = new ApiError(500, [{ code: 'error', message: 'Server broke.', field: null }]);

/** A fetch whose calls are recorded and whose answers the test pushes, one Subject per call. */
function scriptedFetch<P = void>() {
  const calls: { seasonId: number; params: P; answer: Subject<string> }[] = [];
  const fetch = (seasonId: number, params: P): Observable<string> => {
    const answer = new Subject<string>();
    calls.push({ seasonId, params, answer });
    return answer;
  };
  return { calls, fetch };
}

describe('injectSeasonLoad (spec #444 I2)', () => {
  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [LeagueContext, SeasonContext] });
    TestBed.inject(LeagueContext).leagueId.set(1);
    TestBed.inject(SeasonContext).seasonId.set(11);
  });

  function load<T, P>(
    fetch: (seasonId: number, params: P) => Observable<T>,
    options?: SeasonLoadOptions<P>,
  ): SeasonLoad<T> {
    return TestBed.runInInjectionContext(() =>
      injectSeasonLoad(fetch, options as SeasonLoadOptions<P>),
    );
  }

  it('fetches the context Season at once and exposes the answer', () => {
    const { calls, fetch } = scriptedFetch();
    const season = load(fetch);
    expect(calls.map((c) => c.seasonId)).toEqual([11]);
    expect(season.data()).toBeNull();

    calls[0].answer.next('loaded');
    expect(season.data()).toBe('loaded');
    expect(season.loadError()).toBeNull();
    expect(season.notFound()).toBe(false);
  });

  it("puts a failure's message in loadError; retry() clears it and re-sends only that GET", () => {
    const { calls, fetch } = scriptedFetch();
    const season = load(fetch);
    calls[0].answer.error(serverError);
    expect(season.loadError()).toBe('Server broke.');
    expect(season.data()).toBeNull();

    season.retry();
    expect(season.loadError()).toBeNull();
    expect(calls.length).toBe(2);
    calls[1].answer.next('second try');
    expect(season.data()).toBe('second try');
  });

  it('shows the generic message for a non-API failure', () => {
    const season = load(() => throwError(() => new Error('network')));
    expect(season.loadError()).toBe(GENERIC_ERROR_MESSAGE);
  });

  it('treats a 404 as any other failure unless splitNotFound is set', () => {
    const season = load(() => throwError(() => notFound));
    expect(season.loadError()).toBe('Not found.');
    expect(season.notFound()).toBe(false);
  });

  it('with splitNotFound, a 404 sets notFound (not loadError) and retry() clears it', () => {
    let answer: Observable<string> = throwError(() => notFound);
    const season = load(() => answer, { splitNotFound: true });
    expect(season.notFound()).toBe(true);
    expect(season.loadError()).toBeNull();

    answer = of('found');
    season.retry();
    expect(season.notFound()).toBe(false);
    expect(season.data()).toBe('found');
  });

  it('re-fetches on each params emission, and retry() re-sends the latest params', () => {
    const { calls, fetch } = scriptedFetch<string>();
    const params = new Subject<string>();
    const season = load(fetch, { params });
    expect(calls.length).toBe(0);

    params.next('sort=a');
    params.next('sort=b');
    expect(calls.map((c) => c.params)).toEqual(['sort=a', 'sort=b']);
    calls[1].answer.error(serverError);

    season.retry();
    expect(calls.map((c) => [c.seasonId, c.params])).toEqual([
      [11, 'sort=a'],
      [11, 'sort=b'],
      [11, 'sort=b'],
    ]);
    expect(season.loadError()).toBeNull();
  });

  it('keeps the last data while a retry is in flight', () => {
    const { calls, fetch } = scriptedFetch();
    const season = load(fetch);
    calls[0].answer.next('first');
    season.retry();
    expect(season.data()).toBe('first');
  });
});
