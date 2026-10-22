// Loads the Player for the route's `:playerId` on the Team `:id` (Edit Player).
import { DestroyRef, Signal, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute } from '@angular/router';
import { EMPTY, catchError, switchMap, tap } from 'rxjs';
import { ApiError } from '../../api/api-client';
import { Player } from '../../api/players.models';
import { PlayersService } from '../../api/players.service';

/** The route's Player and its load state. */
export interface RoutePlayer {
  /** The route's `:id`: the Team the Player must belong to. */
  readonly teamId: Signal<number>;
  /** The loaded Player, or `null` before it arrives (and after a failed load). */
  readonly player: Signal<Player | null>;
  /**
   * True once `GET players/{playerId}/` answered 404 (a missing Player, or another Account's),
   * or the Player is on another Team.
   */
  readonly notFound: Signal<boolean>;
  /** Any other load failure, as a message for an alert. */
  readonly loadError: Signal<string | null>;
}

/** The alert text for a non-404 load failure. */
function loadErrorMessage(error: unknown): string {
  return error instanceof ApiError
    ? `Could not load the Player. ${error.message}`
    : 'Could not load the Player.';
}

/**
 * Fetches the Player for the route's `:playerId` (again whenever the params change). A 404,
 * or a Player whose `team` is not the route's `:id`, sets `notFound`; any other failure sets
 * `loadError`. Call in an injection context.
 */
export function loadRoutePlayer(): RoutePlayer {
  const players = inject(PlayersService);
  const teamId = signal(0);
  const player = signal<Player | null>(null);
  const notFound = signal(false);
  const loadError = signal<string | null>(null);

  inject(ActivatedRoute)
    .paramMap.pipe(
      switchMap((params) => {
        const id = Number(params.get('id'));
        teamId.set(id);
        return players.get(Number(params.get('playerId'))).pipe(
          tap((p) => {
            if (p.team === id) {
              player.set(p);
            } else {
              notFound.set(true);
            }
          }),
          catchError((error: unknown) => {
            if (error instanceof ApiError && error.status === 404) {
              notFound.set(true);
            } else {
              loadError.set(loadErrorMessage(error));
            }
            return EMPTY;
          }),
        );
      }),
      takeUntilDestroyed(inject(DestroyRef)),
    )
    .subscribe();

  return {
    teamId: teamId.asReadonly(),
    player: player.asReadonly(),
    notFound: notFound.asReadonly(),
    loadError: loadError.asReadonly(),
  };
}
