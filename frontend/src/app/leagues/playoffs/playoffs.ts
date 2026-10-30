import { Component, inject } from '@angular/core';
import { RouterLink } from '@angular/router';
import { PlayoffsParams } from '../../api/league-stats.models';
import { LeagueStatsService } from '../../api/league-stats.service';
import { Bracket } from '../../tournaments/tournament-results/bracket/bracket';
import { LeagueContext } from '../stats-shared/league-context';
import { SeasonPicker } from '../stats-shared/season-picker';
import { StatsApiParams, StatsQueryConfig } from '../stats-shared/stats-query';
import { injectStatsScreen } from '../stats-shared/stats-screen';

/** Playoffs has no sort, Team, rate or paging, and a bracket belongs to one Season. */
const CONFIG: StatsQueryConfig = {
  sortKeys: [],
  defaultSort: '',
  defaultDir: 'asc',
  firstDir: 'asc',
  career: false,
};

/** Only `season`: `statsApiParams` always adds `ordering`, which the action has no param for. */
function playoffsParams(params: StatsApiParams): PlayoffsParams {
  return params.season === undefined ? {} : { season: params.season };
}

/**
 * `/leagues/:id/playoffs` (spec #417 Playoffs, `leagues/playoffs.html`): one section per
 * playoff bracket of the Season, then the Worlds qualification table. No Career option.
 */
@Component({
  selector: 'app-playoffs',
  imports: [Bracket, RouterLink, SeasonPicker],
  templateUrl: './playoffs.html',
  styleUrl: './playoffs.scss',
})
export class Playoffs {
  private readonly stats = inject(LeagueStatsService);
  protected readonly nav = inject(LeagueContext).nav;
  protected readonly screen = injectStatsScreen(CONFIG, (id, params) =>
    this.stats.playoffs(id, playoffsParams(params)),
  );
}
