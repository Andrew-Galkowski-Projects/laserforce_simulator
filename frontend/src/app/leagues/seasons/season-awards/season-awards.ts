import { Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatAnchor, MatButton } from '@angular/material/button';
import { RouterLink } from '@angular/router';
import { EMPTY, Subject, catchError, startWith, switchMap, tap } from 'rxjs';
import { AwardWinner } from '../../../api/leagues.models';
import { SeasonAwards as Awards, RoleKey } from '../../../api/seasons.models';
import { SeasonsService } from '../../../api/seasons.service';
import { AuthService } from '../../../auth/auth.service';
import { apiMessage } from '../../../shared/api-message';
import { GENERIC_ERROR_MESSAGE } from '../../../shared/form-errors';
import { SeasonBreadcrumb } from '../season-breadcrumb/season-breadcrumb';
import { SeasonContext } from '../season-context';

/** One table row: the award's label, its winner (null when nobody qualified) and the decimals. */
interface AwardRow {
  label: string;
  winner: AwardWinner | null;
  places: number;
}

/** `_KD_ROLE_LABELS` (`league_services.py`), in `ROLE_KEYS` order (`season_awards.py`). */
const KD_ROWS: readonly (readonly [RoleKey, string])[] = [
  ['commander', 'K/D — Commander'],
  ['heavy', 'K/D — Heavy'],
  ['scout', 'K/D — Scout'],
  ['medic', 'K/D — Medic'],
  ['ammo', 'K/D — Ammo'],
];

/** The template's 11 rows, in order, each with its `floatformat` places. */
function awardRows(a: Awards): AwardRow[] {
  return [
    { label: 'Most Points', winner: a.most_points, places: 0 },
    { label: 'Best Accuracy', winner: a.best_accuracy, places: 1 },
    ...KD_ROWS.map(([role, label]) => ({ label, winner: a.kd_by_role[role], places: 2 })),
    { label: 'Best Medic', winner: a.best_medic, places: 0 },
    { label: 'Most Efficient Nuke', winner: a.most_efficient_nuke, places: 2 },
    { label: 'Season MVP', winner: a.season_mvp, places: 1 },
    { label: 'Finals MVP', winner: a.finals_mvp, places: 1 },
  ];
}

/**
 * `/leagues/:id/seasons/:seasonId/awards` (spec #444): the Season's awards, ported from
 * `seasons/awards.html`. A "You" badge marks the signed-in Account's Self-insert Player (I4).
 */
@Component({
  selector: 'app-season-awards',
  imports: [MatAnchor, MatButton, RouterLink, SeasonBreadcrumb],
  templateUrl: './season-awards.html',
  styleUrl: './season-awards.scss',
})
export class SeasonAwards {
  private readonly ctx = inject(SeasonContext);
  private readonly auth = inject(AuthService);
  /** I4: the signed-in Account's Self-insert Player id, if linked. */
  protected readonly selfId = computed(() => this.auth.currentUser()?.self_insert?.id);
  protected readonly awards = signal<Awards | null>(null);
  protected readonly loadError = signal<string | null>(null);
  protected readonly rows = computed(() => {
    const loaded = this.awards();
    return loaded ? awardRows(loaded) : [];
  });
  /** `/leagues/:id/seasons/:seasonId/standings`. */
  protected readonly standingsLink = computed(() => [
    '/leagues',
    this.ctx.leagueId(),
    'seasons',
    this.ctx.seasonId(),
    'standings',
  ]);

  private readonly reloads = new Subject<void>();

  constructor() {
    const seasons = inject(SeasonsService);
    this.reloads
      .pipe(
        startWith(undefined),
        tap(() => this.loadError.set(null)),
        switchMap(() =>
          seasons.awards(this.ctx.seasonId() as number).pipe(
            catchError((failure: unknown) => {
              this.loadError.set(apiMessage(failure, GENERIC_ERROR_MESSAGE));
              return EMPTY;
            }),
          ),
        ),
        takeUntilDestroyed(inject(DestroyRef)),
      )
      .subscribe((loaded) => this.awards.set(loaded));
  }

  /** I2: re-sends only the awards GET. */
  protected retry(): void {
    this.reloads.next();
  }
}
