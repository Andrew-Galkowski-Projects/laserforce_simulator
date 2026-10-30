import { Component, computed, inject } from '@angular/core';
import { RouterLink } from '@angular/router';
import { PlayerRatingRow } from '../../api/league-stats.models';
import { LeagueStatsService } from '../../api/league-stats.service';
import { countOf } from '../../shared/count-of';
import { LeagueContext } from '../stats-shared/league-context';
import { PlayerCell } from '../stats-shared/player-cell';
import { PerPagePicker, StatsPaginator } from '../stats-shared/stats-paginator';
import { PagedStatsQueryConfig } from '../stats-shared/stats-query';
import { injectStatsScreen } from '../stats-shared/stats-screen';
import { TeamPicker } from '../stats-shared/team-picker';

/** A sortable header: the API sort key, its abbreviation and its full name (the `title`). */
interface SortColumn {
  key: string;
  label: string;
  full: string;
}

/** `RATING_SORT_KEYS_DISPLAY` (`league_views.py`), in its order. */
const SORT_COLUMNS: readonly SortColumn[] = [
  { key: 'name', label: 'Name', full: 'Name' },
  { key: 'team', label: 'Team', full: 'Team' },
  { key: 'preferred_roles', label: 'Roles', full: 'Preferred Roles' },
  { key: 'overall_rating', label: 'Ovr', full: 'Overall' },
  { key: 'player_awareness', label: 'PlAw', full: 'Player Awareness' },
  { key: 'game_awareness', label: 'GmAw', full: 'Game Awareness' },
  { key: 'resource_awareness', label: 'RsAw', full: 'Resource Awareness' },
  { key: 'decision_making', label: 'Dec', full: 'Decision Making' },
  { key: 'positioning', label: 'Pos', full: 'Positioning' },
  { key: 'stamina', label: 'Sta', full: 'Stamina' },
  { key: 'speed', label: 'Spd', full: 'Speed' },
  { key: 'flexibility', label: 'Flx', full: 'Flexibility' },
  { key: 'adaptability', label: 'Adp', full: 'Adaptability' },
  { key: 'communication', label: 'Comm', full: 'Communication' },
  { key: 'teamwork', label: 'Team', full: 'Teamwork' },
  { key: 'offensive_synergy', label: 'OffSyn', full: 'Offensive Synergy' },
  { key: 'defensive_synergy', label: 'DefSyn', full: 'Defensive Synergy' },
  { key: 'midfield_synergy', label: 'MidSyn', full: 'Midfield Synergy' },
  { key: 'resupply_synergy', label: 'RsupSyn', full: 'Resupply Synergy' },
  { key: 'resupply_efficiency', label: 'RsupEff', full: 'Resupply Efficiency' },
  { key: 'accuracy', label: 'Acc', full: 'Accuracy' },
  { key: 'survival', label: 'Surv', full: 'Survival' },
  { key: 'special_usage', label: 'SpcUse', full: 'Special Usage' },
  { key: 'potential', label: 'Pot', full: 'Potential' },
];

/** The 19 rating stats: the sortable columns between Ovr and Pot. */
const STAT_KEYS: readonly string[] = SORT_COLUMNS.slice(4, -1).map((c) => c.key);

/** The non-sortable bio headers after Pot (MMR and Rank are dropped, STAT-PROXY-01). */
const BIO_HEADERS = [
  { key: 'age', label: 'Age', full: 'Age' },
  { key: 'home_site', label: 'Site', full: 'Home Site' },
  { key: 'height', label: 'Ht', full: 'Height' },
  { key: 'games', label: 'GP', full: 'Games Played' },
  { key: 'started', label: 'Start', full: 'Started Playing Age' },
] as const;

const CONFIG: PagedStatsQueryConfig = {
  sortKeys: SORT_COLUMNS.map((c) => c.key),
  defaultSort: 'team',
  defaultDir: 'asc',
  firstDir: 'asc',
  team: true,
  paged: true,
};

/** Django's `truncatechars:n`: `n` characters at most, the last an ellipsis when cut. */
function truncate(value: string, n: number): string {
  return value.length > n ? `${value.slice(0, n - 1)}…` : value;
}

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
  protected readonly sortColumns = SORT_COLUMNS;
  protected readonly statKeys = STAT_KEYS;
  protected readonly bioHeaders = BIO_HEADERS;
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
    return truncate(value, n);
  }

  protected roles(row: PlayerRatingRow): string {
    return row.preferred_roles.join(', ');
  }

  protected potential(row: PlayerRatingRow): string {
    return row.potential === null ? '—' : row.potential.toFixed(1);
  }

  /** `default:"-"`: a missing number or an empty string shows "-". */
  protected orDash(value: number | string | null): string {
    return value === null || value === '' ? '-' : String(value);
  }
}
