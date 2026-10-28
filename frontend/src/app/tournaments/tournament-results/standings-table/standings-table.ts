import { Component, computed, input } from '@angular/core';
import { RouterLink } from '@angular/router';
import { CutLabel, TournamentStandingsRow } from '../../../api/tournaments.models';

/** The Cut column's labels for an RR->DE seeding-stage marker. */
export const CUT_LABELS: Readonly<Record<CutLabel, string>> = { wb: 'WB', lb: 'LB', out: 'Out' };

/**
 * The Round robin / Swiss Standings: Rank, Team, MP, W, L, T, Match Pts, RW, TS. With RR->DE cut
 * markers it adds a Cut column (WB / LB / Out) and tints each marked row.
 */
@Component({
  selector: 'app-standings-table',
  imports: [RouterLink],
  templateUrl: './standings-table.html',
  styleUrl: './standings-table.scss',
})
export class StandingsTable {
  readonly rows = input.required<TournamentStandingsRow[]>();
  /** Team id (as a string) -> cut marker; `{}` hides the Cut column. */
  readonly cutLabels = input<Record<string, CutLabel>>({});

  protected readonly showCut = computed(() => Object.keys(this.cutLabels()).length > 0);
  protected readonly cutNames = CUT_LABELS;
}
