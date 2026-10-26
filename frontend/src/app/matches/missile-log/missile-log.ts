import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { toObservable, toSignal } from '@angular/core/rxjs-interop';
import { MatAnchor } from '@angular/material/button';
import { RouterLink } from '@angular/router';
import { catchError, map, of, switchMap } from 'rxjs';
import { RoundMissileLog } from '../../api/rounds.models';
import { RoundsService } from '../../api/rounds.service';
import { apiMessage } from '../../shared/api-message';
import { GENERIC_ERROR_MESSAGE } from '../../shared/form-errors';
import { RecordNotFound } from '../shared/record-not-found';
import { roleLabel } from '../shared/role-label';
import { loadRouteRecord } from '../shared/route-record';

/** The Missile log payload's load: loaded, or failed with the server's message. */
type LogState = { status: 'loaded'; log: RoundMissileLog } | { status: 'error'; message: string };

/** `hit / fired * 100` as the Django page showed it: one decimal place and `%`. */
export function efficiencyLabel(efficiency: number): string {
  return `${efficiency.toFixed(1)}%`;
}

/**
 * `/rounds/:id/missile-log` (ANG-06-10): the Round's **Missile log**, its fired / hit / efficiency
 * summary and one row per **Missiled event**, Friendly-fire rows highlighted. The Round loads
 * first (for the heading and the not-found case), then its Missile log.
 */
@Component({
  selector: 'app-missile-log',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [MatAnchor, RecordNotFound, RouterLink],
  templateUrl: './missile-log.html',
  styleUrl: './missile-log.scss',
})
export class MissileLog {
  private readonly rounds = inject(RoundsService);

  protected readonly load = loadRouteRecord('Round', (id) => this.rounds.get(id));
  protected readonly roleLabel = roleLabel;
  protected readonly efficiencyLabel = efficiencyLabel;

  /** The Missile log for the loaded Round; `null` until it answers (the loading state). */
  private readonly logState = toSignal(
    toObservable(this.load.record).pipe(
      switchMap((round) =>
        round === null
          ? of(null)
          : this.rounds.missileLog(round.id).pipe(
              map((log): LogState => ({ status: 'loaded', log })),
              catchError((error: unknown) =>
                of<LogState>({
                  status: 'error',
                  message: apiMessage(error, GENERIC_ERROR_MESSAGE),
                }),
              ),
            ),
      ),
    ),
    { initialValue: null },
  );

  /** The loaded Missile log, or `null` while it loads or after it failed. */
  protected readonly log = computed(() => {
    const state = this.logState();
    return state?.status === 'loaded' ? state.log : null;
  });

  /** A failed Missile log load's message, for the alert box. */
  protected readonly logError = computed(() => {
    const state = this.logState();
    return state?.status === 'error' ? state.message : null;
  });
}
