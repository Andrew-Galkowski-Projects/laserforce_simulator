import { Component, inject } from '@angular/core';
import { RouterLink } from '@angular/router';
import { SeasonParam, TeamStatRow } from '../../api/league-stats.models';
import { LeagueStatsService } from '../../api/league-stats.service';
import { LeagueContext } from '../stats-shared/league-context';
import { SeasonPicker } from '../stats-shared/season-picker';
import { StatsQueryConfig, withSeason, withSort } from '../stats-shared/stats-query';
import { injectStatsScreen } from '../stats-shared/stats-screen';

/** A Team Stats column: its sort key (the API's), header, and decimal places (null: whole). */
interface Column {
  key: string;
  label: string;
  value: (row: TeamStatRow) => number;
  places: number | null;
}

const num = (key: keyof TeamStatRow, label: string, places: number | null = null): Column => ({
  key,
  label,
  value: (row) => row[key] as number,
  places,
});

/** `team_stats_logic.SORT_KEYS_DISPLAY` after Team, with `team_stats.html`'s formats. */
const COLUMNS: readonly Column[] = [
  num('avg_points_for', 'Avg Pts For', 1),
  num('avg_points_against', 'Avg Pts Against', 1),
  num('avg_margin', 'Avg Margin', 1),
  num('avg_survivors', 'Avg Survivors', 2),
  num('total_tags_landed', 'Tags Landed'),
  num('total_times_tagged', 'Times Tagged'),
  num('base_captures', 'Base Captures'),
  num('missiles_fired', 'Missiles Fired'),
  num('missiles_hit', 'Missiles Hit'),
  num('nukes_fired', 'Nukes Fired'),
  num('nukes_landed', 'Nukes Landed'),
  num('cancelled_nukes', 'Cancelled Nukes'),
];

const CONFIG: StatsQueryConfig = {
  sortKeys: ['team', ...COLUMNS.map((c) => c.key)],
  defaultSort: 'team',
  defaultDir: 'asc',
  firstDir: 'asc',
};

/**
 * `/leagues/:id/stats/team-stats` (spec #417 Team Stats, `leagues/team_stats.html`): one row per
 * enrolled Team over a Season or the League career, every column sortable on the server.
 */
@Component({
  selector: 'app-team-stats',
  imports: [RouterLink, SeasonPicker],
  templateUrl: './team-stats.html',
  styleUrl: './team-stats.scss',
})
export class TeamStats {
  private readonly stats = inject(LeagueStatsService);
  protected readonly nav = inject(LeagueContext).nav;
  protected readonly columns = COLUMNS;
  protected readonly screen = injectStatsScreen(CONFIG, (id, params) =>
    this.stats.teamStats(id, params),
  );

  protected sortParams(key: string) {
    return this.screen.params(withSort(this.screen.query(), key, CONFIG));
  }

  /** The header's arrow: up / down on the active sort, none elsewhere. */
  protected arrow(key: string): string {
    const q = this.screen.query();
    if (key !== q.sort) return '';
    return q.dir === 'asc' ? ' ↑' : ' ↓';
  }

  protected cell(row: TeamStatRow, column: Column): string {
    const value = column.value(row);
    return column.places === null ? String(value) : value.toFixed(column.places);
  }

  protected pickSeason(season: SeasonParam | null): void {
    this.screen.go(withSeason(this.screen.query(), season));
  }
}
