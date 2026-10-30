import { Component, inject } from '@angular/core';
import { RouterLink } from '@angular/router';
import { MatButton } from '@angular/material/button';
import { Page } from '../../api/api-client';
import { LeaguePlayersService } from '../../api/league-players.service';
import { PlayerRatingRow } from '../../api/league-stats.models';
import { countOf } from '../../shared/count-of';
import { LeagueContext } from '../stats-shared/league-context';
import { PlayerCell } from '../stats-shared/player-cell';
import {
  RATING_BIO_HEADERS,
  RATING_SORT_COLUMNS,
  RATING_STAT_KEYS,
  orDash,
  potentialText,
  truncateChars,
} from '../stats-shared/player-columns';
import { PerPagePicker, StatsPaginator } from '../stats-shared/stats-paginator';
import { PagedStatsQueryConfig } from '../stats-shared/stats-query';
import { injectStatsScreen } from '../stats-shared/stats-screen';

const CONFIG: PagedStatsQueryConfig = {
  sortKeys: RATING_SORT_COLUMNS.map((c) => c.key),
  defaultSort: 'team',
  defaultDir: 'asc',
  firstDir: 'asc',
  paged: true,
};

/** `free_agents.html` keeps MMR and Rank as "-" placeholders after the bio (STAT-PROXY-01). */
const BIO_HEADERS = [
  ...RATING_BIO_HEADERS,
  { key: 'mmr', label: 'MMR', full: 'MMR' },
  { key: 'rank', label: 'Rank', full: 'Rank' },
] as const;

/**
 * `/leagues/:id/players/free-agents` (spec #428 Free Agents, `leagues/free_agents.html`): the
 * displayed Season's free-agent pool with live ratings, in Player Ratings' columns, sorted and
 * paged on the server. URL state is the stats kit's (`?sort=&dir=&page=&per_page=`); no Team or
 * Season picker. `injectStatsScreen` with `waitForSeason`: it waits for the League nav and sends
 * nothing without a displayed Season (I1); a failure `recoverFrom` can't fix is the error box
 * with Retry (I2).
 */
@Component({
  selector: 'app-free-agents',
  imports: [RouterLink, MatButton, PlayerCell, PerPagePicker, StatsPaginator],
  templateUrl: './free-agents.html',
  styleUrl: './free-agents.scss',
})
export class FreeAgents {
  private readonly players = inject(LeaguePlayersService);
  private readonly context = inject(LeagueContext);
  protected readonly nav = this.context.nav;
  /** I1: the nav has loaded and the League has no Season. */
  protected readonly noSeason = this.context.noSeason;
  protected readonly sortColumns = RATING_SORT_COLUMNS;
  protected readonly statKeys = RATING_STAT_KEYS;
  protected readonly bioHeaders = BIO_HEADERS;

  protected readonly screen = injectStatsScreen<Page<PlayerRatingRow>>(
    CONFIG,
    (id, params) => this.players.freeAgents(id, params),
    { waitForSeason: true },
  );

  protected readonly orDash = orDash;
  protected readonly potentialText = potentialText;

  protected count(n: number): string {
    return countOf(n, 'free agent');
  }

  protected short(value: string, n = 14): string {
    return truncateChars(value, n);
  }

  protected roles(row: PlayerRatingRow): string {
    return row.preferred_roles.join(', ');
  }
}
