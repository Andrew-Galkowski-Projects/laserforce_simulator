import { Injectable, inject, signal } from '@angular/core';
import { Observable, map, tap } from 'rxjs';
import { LeaguePlayersService } from '../../api/league-players.service';
import { apiMessage } from '../../shared/api-message';
import { GENERIC_ERROR_MESSAGE } from '../../shared/form-errors';
import { injectNotify } from '../../shared/notify';
import { LeagueContext } from '../stats-shared/league-context';

/**
 * The Account's Watch flags in the open League (spec #428 F3). `LeagueLayout` provides it beside
 * `LeagueContext`, so it lives as long as the League visit and every screen inside shares it.
 *
 * - `watched(playerId, seed)`: the state this page last saved for the Player, else `seed` (the
 *   row's `watched`). Every flag reads it, so a toggle re-colours each flag for that Player.
 * - `pending(playerId)`: a request for the Player is in flight (the flag is disabled).
 * - `toggle(playerId, current)`: PUT (watch) when `current` is false, else DELETE. On success the
 *   new state is recorded; on failure nothing changes and the server's message is shown in the
 *   snackbar. A toggle while one is pending is ignored.
 * - `clearAll()`: Remove All. On success every Player reads as unwatched (until toggled again);
 *   on failure the Observable errors and the caller shows the message.
 *
 * Only `app-watch-flag` calls `toggle`, and only the Watch List calls `clearAll` (I4).
 */
@Injectable()
export class WatchState {
  private readonly players = inject(LeaguePlayersService);
  private readonly leagueId = inject(LeagueContext).leagueId;
  private readonly notify = injectNotify();
  private readonly saved = signal<ReadonlyMap<number, boolean>>(new Map());
  private readonly inFlight = signal<ReadonlySet<number>>(new Set());
  /** After a Remove All, a Player this page has not toggled since reads as unwatched. */
  private readonly cleared = signal(false);

  watched(playerId: number, seed: boolean): boolean {
    return this.saved().get(playerId) ?? (this.cleared() ? false : seed);
  }

  pending(playerId: number): boolean {
    return this.inFlight().has(playerId);
  }

  toggle(playerId: number, current: boolean): void {
    const id = this.leagueId();
    if (id === null || this.pending(playerId)) return;
    this.setPending(playerId, true);
    const request = current
      ? this.players.unwatch(id, playerId)
      : this.players.watch(id, playerId).pipe(map(() => undefined));
    request.subscribe({
      next: () => {
        this.saved.update((m) => new Map(m).set(playerId, !current));
        this.setPending(playerId, false);
      },
      error: (error: unknown) => {
        this.setPending(playerId, false);
        this.notify(apiMessage(error, GENERIC_ERROR_MESSAGE));
      },
    });
  }

  clearAll(): Observable<void> {
    return this.players.clearWatchList(this.leagueId() ?? 0).pipe(
      tap(() => {
        this.saved.set(new Map());
        this.cleared.set(true);
      }),
    );
  }

  private setPending(playerId: number, on: boolean): void {
    this.inFlight.update((s) => {
      const next = new Set(s);
      if (on) next.add(playerId);
      else next.delete(playerId);
      return next;
    });
  }
}
