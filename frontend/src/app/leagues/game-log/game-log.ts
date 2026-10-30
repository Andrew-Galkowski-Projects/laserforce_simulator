import { DatePipe } from '@angular/common';
import { Component, inject } from '@angular/core';
import { RouterLink } from '@angular/router';
import { TeamRef } from '../../api/analytics.models';
import { SeasonParam } from '../../api/league-stats.models';
import { LeagueStatsService } from '../../api/league-stats.service';
import { LeagueContext } from '../stats-shared/league-context';
import { SeasonPicker } from '../stats-shared/season-picker';
import { StatsQueryConfig, withSeason, withSort, withTeam } from '../stats-shared/stats-query';
import { injectStatsScreen } from '../stats-shared/stats-screen';
import { TeamPicker } from '../stats-shared/team-picker';

/** A Game Log column: its sort key (the API's) and header, from `_GAME_LOG_SORT_KEYS_DISPLAY`. */
interface Column {
  key: string;
  label: string;
  /** The cell's alignment class: `num` (right), `center`, or none. */
  align: 'num' | 'center' | '';
}

const COLUMNS: readonly Column[] = [
  { key: 'matchday', label: 'Matchday', align: 'num' },
  { key: 'date_played', label: 'Date', align: '' },
  { key: 'team_red', label: 'Red', align: '' },
  { key: 'team_blue', label: 'Blue', align: '' },
  { key: 'score', label: 'Score', align: 'center' },
  { key: 'winner', label: 'Winner', align: '' },
];

const CONFIG: StatsQueryConfig = {
  sortKeys: COLUMNS.map((c) => c.key),
  defaultSort: 'date_played',
  defaultDir: 'asc',
  firstDir: 'asc',
  team: true,
};

/**
 * `/leagues/:id/stats/game-log` (spec #417 Game Log, `leagues/game_log.html`): one row per
 * played Round in a Season or the League career, narrowed to one Team, every column sortable on
 * the server. Red, Blue and Score link to the Round.
 */
@Component({
  selector: 'app-game-log',
  imports: [DatePipe, RouterLink, SeasonPicker, TeamPicker],
  templateUrl: './game-log.html',
  styleUrl: './game-log.scss',
})
export class GameLog {
  private readonly stats = inject(LeagueStatsService);
  protected readonly nav = inject(LeagueContext).nav;
  protected readonly columns = COLUMNS;
  protected readonly screen = injectStatsScreen(CONFIG, (id, params) =>
    this.stats.gameLog(id, params),
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

  /** A Team's name, or "—" when it is missing (I6). */
  protected teamName(team: TeamRef | null | undefined): string {
    return team?.name ?? '—';
  }

  protected pickSeason(season: SeasonParam | null): void {
    this.screen.go(withSeason(this.screen.query(), season));
  }

  protected pickTeam(team: number | null): void {
    this.screen.go(withTeam(this.screen.query(), team));
  }
}
