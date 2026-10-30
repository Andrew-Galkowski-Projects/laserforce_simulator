import { Component, inject } from '@angular/core';
import { RouterLink } from '@angular/router';
import { FeatRow, SeasonParam } from '../../api/league-stats.models';
import { LeagueStatsService } from '../../api/league-stats.service';
import { LeagueContext } from '../stats-shared/league-context';
import { PlayerCell } from '../stats-shared/player-cell';
import { SeasonPicker } from '../stats-shared/season-picker';
import { PerPagePicker, StatsPaginator } from '../stats-shared/stats-paginator';
import {
  STATS_PAGE_SIZES,
  StatsQueryConfig,
  withPage,
  withPerPage,
  withSeason,
  withSort,
  withTeam,
} from '../stats-shared/stats-query';
import { injectStatsScreen } from '../stats-shared/stats-screen';
import { TeamPicker } from '../stats-shared/team-picker';

/** A header: its sort key (the API's `FEATS_SORT_KEYS`) and label. */
interface Header {
  key: string;
  label: string;
}

/** A box-score column: `stats` key, header, and decimal places (`floatformat`). */
interface BoxColumn extends Header {
  places: number;
}

/** `_FEATS_SORT_KEYS_DISPLAY` before the box score (Round goes last). */
const DESCRIPTORS: readonly Header[] = [
  { key: 'name', label: 'Player' },
  { key: 'role', label: 'Role' },
  { key: 'team', label: 'Team' },
  { key: 'opp', label: 'Opp' },
  { key: 'result', label: 'Result' },
  { key: 'season', label: 'Season' },
  { key: 'feat', label: 'Feats' },
];

const box = (key: string, label: string, places = 0): BoxColumn => ({ key, label, places });

/** `_BOX_SCORE_COLUMNS` (`league_screens/statistical_feats.py`): MVP and Acc% 1 decimal. */
const BOX_SCORE: readonly BoxColumn[] = [
  box('points_scored', 'Points'),
  box('mvp', 'MVP', 1),
  box('tags_made', 'Tags'),
  box('times_tagged', 'Tagged'),
  box('accuracy', 'Acc%', 1),
  box('final_lives', 'Lives'),
  box('resupplies_given', 'Resup'),
  box('missiles_landed', 'Missiles'),
  box('specials_used', 'Specials'),
  box('follow_up_shots', 'Follow-up'),
  box('reaction_shots', 'Reaction'),
  box('combo_resupply_count', 'Combo Resup'),
  box('nuke_detonations', 'Nukes'),
];

const CONFIG: StatsQueryConfig = {
  sortKeys: [...DESCRIPTORS.map((h) => h.key), ...BOX_SCORE.map((c) => c.key), 'round'],
  defaultSort: 'round',
  defaultDir: 'desc',
  // The template's non-active header links `dir=desc`.
  firstDir: 'desc',
  team: true,
  paged: true,
};

/**
 * `/leagues/:id/stats/statistical-feats` (spec #417 Statistical Feats,
 * `leagues/statistical_feats.html`): one row per (Player, Round) that achieved a feat, newest
 * first, every column sortable and paged on the server, then the (unpaged) Team Feats list.
 */
@Component({
  selector: 'app-statistical-feats',
  imports: [RouterLink, SeasonPicker, TeamPicker, PerPagePicker, StatsPaginator, PlayerCell],
  templateUrl: './statistical-feats.html',
  styleUrl: './statistical-feats.scss',
})
export class StatisticalFeats {
  private readonly stats = inject(LeagueStatsService);
  protected readonly nav = inject(LeagueContext).nav;
  protected readonly descriptors = DESCRIPTORS;
  protected readonly boxScore = BOX_SCORE;
  // `paged: true` makes `statsApiParams` always set `page` + `page_size`, but `StatsApiParams`
  // types them optional; the fallbacks only satisfy `StatisticalFeatsParams` and never apply.
  protected readonly screen = injectStatsScreen(CONFIG, (id, params) =>
    this.stats.statisticalFeats(id, {
      ...params,
      page: params.page ?? 1,
      page_size: params.page_size ?? STATS_PAGE_SIZES[0],
    }),
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

  protected cell(row: FeatRow, column: BoxColumn): string {
    const value = row.stats[column.key];
    return value === undefined || value === null ? '—' : value.toFixed(column.places);
  }

  protected pickSeason(season: SeasonParam | null): void {
    this.screen.go(withSeason(this.screen.query(), season));
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
