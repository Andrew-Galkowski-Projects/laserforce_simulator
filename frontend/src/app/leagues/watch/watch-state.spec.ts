import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  TestRequest,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { MatSnackBar } from '@angular/material/snack-bar';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../../environments/environment';
import { LeagueContext } from '../stats-shared/league-context';
import { watchResult } from '../testing/league-players-fixtures';
import { failure } from '../testing/league-stats-fixtures';
import { WatchState } from './watch-state';

const BASE = environment.apiBaseUrl;

describe('WatchState (spec #428 F3)', () => {
  let state: WatchState;
  let http: HttpTestingController;
  let snack: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting(), LeagueContext, WatchState],
    });
    TestBed.inject(LeagueContext).leagueId.set(4);
    state = TestBed.inject(WatchState);
    http = TestBed.inject(HttpTestingController);
    snack = vi.spyOn(TestBed.inject(MatSnackBar), 'open');
  });

  afterEach(() => http.verify());

  function expectOne(method: string, path: string): TestRequest {
    const req = http.expectOne((r) => r.url === `${BASE}/leagues/4/${path}`);
    expect(req.request.method).toBe(method);
    return req;
  }

  it("reads a Player's row seed until the page toggles them", () => {
    expect(state.watched(25, true)).toBe(true);
    expect(state.watched(25, false)).toBe(false);
    expect(state.pending(25)).toBe(false);
  });

  it('watches an unwatched Player with PUT, pending until the server answers', () => {
    state.toggle(25, false);
    expect(state.pending(25)).toBe(true);
    expect(state.watched(25, false)).toBe(false);
    expectOne('PUT', 'watch-list/25/').flush({ data: watchResult(true), meta: {} });
    expect(state.pending(25)).toBe(false);
    // Every flag for the Player re-reads the new state, whatever its row's seed.
    expect(state.watched(25, false)).toBe(true);
    expect(state.watched(25, true)).toBe(true);
    expect(state.watched(26, false)).toBe(false);
  });

  it('unwatches a watched Player with DELETE', () => {
    state.toggle(25, true);
    expectOne('DELETE', 'watch-list/25/').flush({ data: watchResult(false), meta: {} });
    expect(state.watched(25, true)).toBe(false);
  });

  it("leaves the state on a failure and shows the server's message", () => {
    state.toggle(25, false);
    expectOne('PUT', 'watch-list/25/').flush(...failure(400, 'The watch list is full.'));
    expect(state.watched(25, false)).toBe(false);
    expect(state.pending(25)).toBe(false);
    expect(snack).toHaveBeenCalledWith('The watch list is full.', 'Close', { duration: 5000 });
  });

  it('shows a fallback message for a failure with no server message', () => {
    state.toggle(25, true);
    expectOne('DELETE', 'watch-list/25/').error(new ProgressEvent('error'));
    expect(state.watched(25, true)).toBe(true);
    expect(snack).toHaveBeenCalledWith('Could not reach the server.', 'Close', {
      duration: 5000,
    });
  });

  it('sends one request for a Player while one is pending', () => {
    state.toggle(25, false);
    state.toggle(25, false);
    expectOne('PUT', 'watch-list/25/').flush({ data: watchResult(true), meta: {} });
  });

  it('clearAll empties the list with DELETE watch-list/ and unwatches every Player', async () => {
    state.toggle(26, false);
    expectOne('PUT', 'watch-list/26/').flush({ data: watchResult(true), meta: {} });
    const done = firstValueFrom(state.clearAll(), { defaultValue: undefined });
    expectOne('DELETE', 'watch-list/').flush({ data: { removed: 2 }, meta: {} });
    await done;
    expect(state.watched(25, true)).toBe(false);
    expect(state.watched(26, true)).toBe(false);
    // A later toggle still wins.
    state.toggle(25, false);
    expectOne('PUT', 'watch-list/25/').flush({ data: watchResult(true), meta: {} });
    expect(state.watched(25, false)).toBe(true);
  });

  it('clearAll fails without changing the state, leaving the message to the caller', async () => {
    const done = firstValueFrom(state.clearAll()).catch((e: unknown) => e);
    expectOne('DELETE', 'watch-list/').flush(...failure(500, 'Database is down.'));
    expect(((await done) as Error).message).toBe('Database is down.');
    expect(state.watched(25, true)).toBe(true);
  });
});
