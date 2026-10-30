import { Component, inject } from '@angular/core';
import { RouterLink } from '@angular/router';
import { FeatRow } from '../../api/league-stats.models';
import { LeagueStatsService } from '../../api/league-stats.service';
import { LeagueContext } from '../stats-shared/league-context';
import { PlayerCell } from '../stats-shared/player-cell';
import { SeasonPicker } from '../stats-shared/season-picker';
import { PerPagePicker, StatsPaginator } from '../stats-shared/stats-paginator';
import { PagedStatsQueryConfig } from '../stats-shared/stats-query';
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

const CONFIG: PagedStatsQueryConfig = {
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
  protected readonly screen = injectStatsScreen(CONFIG, (id, params) =>
    this.stats.statisticalFeats(id, params),
  );

  protected cell(row: FeatRow, column: BoxColumn): string {
    const value = row.stats[column.key];
    return value === undefined || value === null ? '—' : value.toFixed(column.places);
  }
}
