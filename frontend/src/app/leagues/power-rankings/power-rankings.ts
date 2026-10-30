import { Component, inject } from '@angular/core';
import { RouterLink } from '@angular/router';
import { PowerRankingRow } from '../../api/league-stats.models';
import { LeagueStatsService } from '../../api/league-stats.service';
import { LeagueContext } from '../stats-shared/league-context';
import { SeasonPicker } from '../stats-shared/season-picker';
import { StatsQueryConfig } from '../stats-shared/stats-query';
import { injectStatsScreen } from '../stats-shared/stats-screen';

/** A numeric Power Rankings column: its sort key (the API's), header and decimal places. */
interface Column {
  key: keyof PowerRankingRow;
  label: string;
  places: number;
}

/** `power_rankings_logic.SORT_KEYS_DISPLAY` after Rank and Team, with the template's formats. */
const COLUMNS: readonly Column[] = [
  { key: 'mean_rating', label: 'Mean rating', places: 1 },
  { key: 'win_pct', label: 'Win %', places: 3 },
  { key: 'avg_score_diff', label: 'Avg score diff', places: 1 },
  { key: 'power_score', label: 'Power score', places: 3 },
];

const CONFIG: StatsQueryConfig = {
  sortKeys: ['rank', 'team', ...COLUMNS.map((c) => c.key)],
  defaultSort: 'rank',
  defaultDir: 'asc',
  firstDir: 'asc',
};

/**
 * `/leagues/:id/power-rankings` (spec #417 Power Rankings, `leagues/power_rankings.html`): the
 * Teams ranked by power score over a Season or the League career, every column sortable on the
 * server, under the template's explainer.
 */
@Component({
  selector: 'app-power-rankings',
  imports: [RouterLink, SeasonPicker],
  templateUrl: './power-rankings.html',
  styleUrl: './power-rankings.scss',
})
export class PowerRankings {
  private readonly stats = inject(LeagueStatsService);
  protected readonly nav = inject(LeagueContext).nav;
  protected readonly columns = COLUMNS;
  protected readonly screen = injectStatsScreen(CONFIG, (id, params) =>
    this.stats.powerRankings(id, params),
  );

  protected cell(row: PowerRankingRow, column: Column): string {
    return (row[column.key] as number).toFixed(column.places);
  }
}
