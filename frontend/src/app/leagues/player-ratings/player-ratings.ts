import { Component, computed, inject } from '@angular/core';
import { RouterLink } from '@angular/router';
import { PlayerRatingRow } from '../../api/league-stats.models';
import { LeagueStatsService } from '../../api/league-stats.service';
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
import { TeamPicker } from '../stats-shared/team-picker';

const CONFIG: PagedStatsQueryConfig = {
  sortKeys: RATING_SORT_COLUMNS.map((c) => c.key),
  defaultSort: 'team',
  defaultDir: 'asc',
  firstDir: 'asc',
  team: true,
  paged: true,
};

/**
 * `/leagues/:id/stats/player-ratings` (spec #417 Player Ratings, `leagues/player_ratings.html`):
 * the live ratings of every Player on the displayed Season's enrolled Teams, sorted and paged on
 * the server. Ratings are live, so there is no Season picker; the no-Season notice reads the
 * League nav's `displayed_season`.
 */
@Component({
  selector: 'app-player-ratings',
  imports: [RouterLink, PlayerCell, TeamPicker, PerPagePicker, StatsPaginator],
  templateUrl: './player-ratings.html',
  styleUrl: './player-ratings.scss',
})
export class PlayerRatings {
  private readonly stats = inject(LeagueStatsService);
  protected readonly nav = inject(LeagueContext).nav;
  protected readonly sortColumns = RATING_SORT_COLUMNS;
  protected readonly statKeys = RATING_STAT_KEYS;
  protected readonly bioHeaders = RATING_BIO_HEADERS;
  protected readonly screen = injectStatsScreen(CONFIG, (id, params) =>
    this.stats.playerRatings(id, params),
  );

  /** I3: the nav has loaded and the League has no Season. */
  protected readonly noSeason = computed(() => {
    const nav = this.nav();
    return nav !== null && nav.displayed_season === null;
  });

  protected count(n: number): string {
    return countOf(n, 'player');
  }

  protected short(value: string, n = 14): string {
    return truncateChars(value, n);
  }

  protected roles(row: PlayerRatingRow): string {
    return row.preferred_roles.join(', ');
  }

  protected potential(row: PlayerRatingRow): string {
    return potentialText(row.potential);
  }

  /** `default:"-"`: a missing number or an empty string shows "-". */
  protected orDash(value: number | string | null): string {
    return orDash(value);
  }
}
