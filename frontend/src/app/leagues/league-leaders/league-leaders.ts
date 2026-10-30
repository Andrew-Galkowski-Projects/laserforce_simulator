import { Component, inject, signal } from '@angular/core';
import { LeaderBoardRow, LeaderBoards } from '../../api/league-stats.models';
import { LeagueStatsService } from '../../api/league-stats.service';
import { PlayerCell } from '../stats-shared/player-cell';
import { SeasonPicker } from '../stats-shared/season-picker';
import { SortDir, StatsQueryConfig, sortArrow } from '../stats-shared/stats-query';
import { injectStatsScreen } from '../stats-shared/stats-screen';

/** One board: its `LeaderBoards` key, table id, header labels and decimal places. */
interface Board {
  key: keyof LeaderBoards;
  id: string;
  title: string;
  valueLabel: string;
  places: number;
}

/** The two sortable headers: the Player cell (by name) and the value. */
type BoardSortKey = 'player_name' | 'value';

interface BoardSort {
  key: BoardSortKey;
  dir: SortDir;
}

/** `league_leaders.html`'s four boards, in its order. */
const BOARDS: readonly Board[] = [
  { key: 'avg_tags', id: 'leaders-avg-tags', title: 'Average Tags', valueLabel: 'Tags', places: 1 },
  {
    key: 'avg_score',
    id: 'leaders-avg-score',
    title: 'Average Score',
    valueLabel: 'Score',
    places: 1,
  },
  {
    key: 'fewest_tagged',
    id: 'leaders-fewest-tagged',
    title: 'Fewest Times Tagged',
    valueLabel: 'Tagged',
    places: 1,
  },
  { key: 'tag_ratio', id: 'leaders-tag-ratio', title: 'Tag Ratio', valueLabel: 'Ratio', places: 2 },
];

/** The Leaders API takes no sort: only the Season lives in the URL. */
const CONFIG: StatsQueryConfig = {
  sortKeys: [],
  defaultSort: 'rank',
  defaultDir: 'asc',
  firstDir: 'asc',
};

/**
 * `/leagues/:id/stats/league-leaders` (spec #417 League Leaders, `leagues/league_leaders.html`):
 * four top-10 boards over a Season or the League career.
 */
@Component({
  selector: 'app-league-leaders',
  imports: [PlayerCell, SeasonPicker],
  templateUrl: './league-leaders.html',
  styleUrl: './league-leaders.scss',
})
export class LeagueLeaders {
  private readonly stats = inject(LeagueStatsService);
  protected readonly boards = BOARDS;
  protected readonly screen = injectStatsScreen(CONFIG, (id, params) =>
    this.stats.leaders(id, { season: params.season }),
  );

  /** Each board's client-side sort; a board missing here keeps the API's rank order. */
  private readonly sorts = signal<Partial<Record<keyof LeaderBoards, BoardSort>>>({});

  /** `board`'s rows in its sort: `league_leaders._sort_leaderboard`, keyed then by Player id. */
  protected rows(boards: LeaderBoards, board: Board): LeaderBoardRow[] {
    const rows = boards[board.key];
    const sort = this.sorts()[board.key];
    if (!sort) return rows;
    const sign = sort.dir === 'asc' ? 1 : -1;
    return [...rows].sort((a, b) => {
      const x = a[sort.key];
      const y = b[sort.key];
      const byKey = x < y ? -1 : x > y ? 1 : 0;
      return sign * (byKey || a.player_id - b.player_id);
    });
  }

  /** A header click: the active one toggles, another starts ascending (the template's links). */
  protected sortBy(board: Board, key: BoardSortKey): void {
    const current = this.sorts()[board.key];
    const dir: SortDir = current?.key === key && current.dir === 'asc' ? 'desc' : 'asc';
    this.sorts.update((sorts) => ({ ...sorts, [board.key]: { key, dir } }));
  }

  /** The header's arrow: up / down on the board's active sort, none elsewhere. */
  protected arrow(board: Board, key: BoardSortKey): string {
    const sort = this.sorts()[board.key];
    return sort?.key === key ? sortArrow(sort.dir) : '';
  }

  protected value(row: LeaderBoardRow, board: Board): string {
    return row.value.toFixed(board.places);
  }
}
