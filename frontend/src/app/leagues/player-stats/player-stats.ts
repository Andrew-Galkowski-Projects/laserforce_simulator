import { Component, inject } from '@angular/core';
import { RouterLink } from '@angular/router';
import { PlayerStatRow, StatRate } from '../../api/league-stats.models';
import { LeagueStatsService } from '../../api/league-stats.service';
import { countOf } from '../../shared/count-of';
import { PlayerCell } from '../stats-shared/player-cell';
import { STAT_COLUMNS, StatColumn, statCell } from '../stats-shared/player-columns';
import { RATE_OPTIONS, RatePicker } from '../stats-shared/rate-picker';
import { SeasonPicker } from '../stats-shared/season-picker';
import { PerPagePicker, StatsPaginator } from '../stats-shared/stats-paginator';
import { PagedStatsQueryConfig } from '../stats-shared/stats-query';
import { injectStatsScreen } from '../stats-shared/stats-screen';
import { TeamPicker } from '../stats-shared/team-picker';

const CONFIG: PagedStatsQueryConfig<StatRate> = {
  sortKeys: ['name', 'team', 'games', ...STAT_COLUMNS.map((c) => c.key)],
  defaultSort: 'points_scored',
  defaultDir: 'desc',
  firstDir: 'desc',
  team: true,
  rates: RATE_OPTIONS.map(([value]) => value),
  paged: true,
};

/**
 * `/leagues/:id/stats/player-stats` (spec #417 Player Stats, `leagues/player_stats.html`): every
 * Player's performance over a Season or the League career, as totals or rates, filtered by Team,
 * sorted and paged on the server.
 */
@Component({
  selector: 'app-player-stats',
  imports: [
    RouterLink,
    SeasonPicker,
    RatePicker,
    TeamPicker,
    PerPagePicker,
    StatsPaginator,
    PlayerCell,
  ],
  templateUrl: './player-stats.html',
  styleUrl: './player-stats.scss',
})
export class PlayerStats {
  private readonly stats = inject(LeagueStatsService);
  protected readonly columns = STAT_COLUMNS;
  protected readonly screen = injectStatsScreen(CONFIG, (id, params) =>
    this.stats.playerStats(id, params),
  );

  protected cell(row: PlayerStatRow, column: StatColumn): string {
    return statCell(row.stats, column);
  }

  protected count(n: number): string {
    return countOf(n, 'player');
  }
}
