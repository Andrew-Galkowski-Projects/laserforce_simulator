import { Component, computed, inject } from '@angular/core';
import { MatAnchor, MatButton } from '@angular/material/button';
import { RouterLink } from '@angular/router';
import { AwardWinner } from '../../../api/leagues.models';
import { SeasonAwards as Awards, RoleKey } from '../../../api/seasons.models';
import { SeasonsService } from '../../../api/seasons.service';
import { AuthService } from '../../../auth/auth.service';
import { SeasonBreadcrumb } from '../season-breadcrumb/season-breadcrumb';
import { SeasonContext } from '../season-context';
import { injectSeasonLoad } from '../season-load';

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
  protected readonly context = inject(SeasonContext);
  private readonly auth = inject(AuthService);
  private readonly seasons = inject(SeasonsService);
  private readonly load = injectSeasonLoad((id) => this.seasons.awards(id));
  /** I4: the signed-in Account's Self-insert Player id, if linked. */
  protected readonly selfId = computed(() => this.auth.currentUser()?.self_insert?.id);
  protected readonly awards = this.load.data;
  protected readonly loadError = this.load.loadError;
  protected readonly rows = computed(() => {
    const loaded = this.awards();
    return loaded ? awardRows(loaded) : [];
  });

  /** I2: re-sends only the awards GET. */
  protected retry(): void {
    this.load.retry();
  }
}
