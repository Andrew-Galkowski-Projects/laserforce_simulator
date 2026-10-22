// Loads the Team for the route's `:id` (shared by Team detail, Edit Team and Assign Slots).
import { DestroyRef, Signal, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute } from '@angular/router';
import { EMPTY, Subject, catchError, combineLatest, startWith, switchMap, tap } from 'rxjs';
import { ApiError } from '../../api/api-client';
import { Team } from '../../api/teams.models';
import { TeamsService } from '../../api/teams.service';

/** The route's Team and its load state. */
export interface RouteTeam {
  /** The loaded Team, or `null` before it arrives (and after a failed first load). */
  readonly team: Signal<Team | null>;
  /** True once `GET teams/{id}/` answered 404 (a missing Team, or another Account's). */
  readonly notFound: Signal<boolean>;
  /** Any other load failure, as a message for an alert. */
  readonly loadError: Signal<string | null>;
  /** `GET teams/{id}/` again, then `done` once the fresh Team is shown. */
  reload(done?: () => void): void;
}

/** The alert text for a non-404 load failure. */
function loadErrorMessage(error: unknown): string {
  return error instanceof ApiError
    ? `Could not load the Team. ${error.message}`
    : 'Could not load the Team.';
}

/**
 * Fetches the Team for the route's `:id` (again whenever the id changes or `reload` is called).
 * A 404 sets `notFound`; any other failure sets `loadError`. `beforeShow` runs on each loaded
 * Team just before `team` is set (e.g. to build a form for it). Call in an injection context.
 */
export function loadRouteTeam(beforeShow?: (team: Team) => void): RouteTeam {
  const teams = inject(TeamsService);
  const team = signal<Team | null>(null);
  const notFound = signal(false);
  const loadError = signal<string | null>(null);
  const reloads = new Subject<(() => void) | undefined>();

  combineLatest([inject(ActivatedRoute).paramMap, reloads.pipe(startWith(undefined))])
    .pipe(
      switchMap(([params, done]) =>
        teams.get(Number(params.get('id'))).pipe(
          tap((t) => {
            beforeShow?.(t);
            team.set(t);
            done?.();
          }),
          catchError((error: unknown) => {
            if (error instanceof ApiError && error.status === 404) {
              notFound.set(true);
            } else {
              loadError.set(loadErrorMessage(error));
            }
            return EMPTY;
          }),
        ),
      ),
      takeUntilDestroyed(inject(DestroyRef)),
    )
    .subscribe();

  return {
    team: team.asReadonly(),
    notFound: notFound.asReadonly(),
    loadError: loadError.asReadonly(),
    reload: (done) => reloads.next(done),
  };
}
