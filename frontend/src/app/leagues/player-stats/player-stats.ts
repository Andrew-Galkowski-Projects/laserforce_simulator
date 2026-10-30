import { Component, inject } from '@angular/core';
import { RouterLink } from '@angular/router';
import { PlayerStatRow, PlayerStatsParams, SeasonParam } from '../../api/league-stats.models';
import { LeagueStatsService } from '../../api/league-stats.service';
import { countOf } from '../../shared/count-of';
import { PlayerCell } from '../stats-shared/player-cell';
import { SeasonPicker } from '../stats-shared/season-picker';
import { PerPagePicker, StatsPaginator } from '../stats-shared/stats-paginator';
import {
  StatsQueryConfig,
  withPage,
  withPerPage,
  withRate,
  withSeason,
  withSort,
  withTeam,
} from '../stats-shared/stats-query';
import { injectStatsScreen } from '../stats-shared/stats-screen';
import { TeamPicker } from '../stats-shared/team-picker';
import { RATE_OPTIONS, RatePicker } from './rate-picker';

/**
 * A Player Stats stat column: its sort key (the API's, and the `stats` key), header, decimal
 * places, and suffix. Name, Team and GP come first in the template, outside this list.
 */
interface Column {
  key: string;
  label: string;
  places: number;
  suffix?: string;
}

/** `_PLAYER_STATS_COLUMNS` after Name / Team / GP, with `player_stats.html`'s `floatformat`s. */
const STAT_COLUMNS: readonly Column[] = [
  { key: 'points_scored', label: 'Points', places: 0 },
  { key: 'mvp', label: 'MVP', places: 1 },
  { key: 'tags_made', label: 'Tags', places: 0 },
  { key: 'times_tagged', label: 'Tagged', places: 0 },
  { key: 'tag_ratio', label: 'Tag Ratio', places: 2 },
  { key: 'accuracy', label: 'Acc%', places: 1 },
  { key: 'survival', label: 'Survival', places: 0, suffix: 's' },
  { key: 'final_lives', label: 'Lives', places: 0 },
  { key: 'resupplies_given', label: 'Resup', places: 0 },
  { key: 'missiles_landed', label: 'Missiles', places: 0 },
  { key: 'specials_used', label: 'Specials', places: 0 },
  { key: 'follow_up_shots', label: 'Follow-up', places: 0 },
  { key: 'reaction_shots', label: 'Reaction', places: 0 },
  { key: 'combo_resupply_count', label: 'Combo Resup', places: 0 },
];

const CONFIG: StatsQueryConfig = {
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
  // `paged: true` makes `statsApiParams` always send `page` and `page_size`.
  protected readonly screen = injectStatsScreen(CONFIG, (id, params) =>
    this.stats.playerStats(id, params as PlayerStatsParams),
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

  protected cell(row: PlayerStatRow, column: Column): string {
    return (row.stats[column.key] ?? 0).toFixed(column.places) + (column.suffix ?? '');
  }

  protected count(n: number): string {
    return countOf(n, 'player');
  }

  protected pickSeason(season: SeasonParam | null): void {
    this.screen.go(withSeason(this.screen.query(), season));
  }

  protected pickRate(rate: string): void {
    this.screen.go(withRate(this.screen.query(), rate));
  }

  protected pickTeam(team: number | null): void {
    this.screen.go(withTeam(this.screen.query(), team));
  }

  protected pickPerPage(perPage: number): void {
    this.screen.go(withPerPage(this.screen.query(), perPage));
  }

  protected pickPage(page: number): void {
    this.screen.go(withPage(this.screen.query(), page));
  }
}
