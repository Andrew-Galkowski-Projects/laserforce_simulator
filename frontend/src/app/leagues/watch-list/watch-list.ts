import { Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { RouterLink } from '@angular/router';
import { MatButton } from '@angular/material/button';
import { MatDialog } from '@angular/material/dialog';
import { filter, switchMap } from 'rxjs';
import { Page, PageMeta } from '../../api/api-client';
import { LeaguePlayersService } from '../../api/league-players.service';
import {
  PlayerStatRow,
  ScopedMeta,
  SeasonOption,
  SeasonScope,
  StatRate,
} from '../../api/league-stats.models';
import { apiMessage } from '../../shared/api-message';
import { ConfirmDialog, ConfirmDialogData } from '../../shared/confirm-dialog';
import { countOf } from '../../shared/count-of';
import { GENERIC_ERROR_MESSAGE } from '../../shared/form-errors';
import { injectNotify } from '../../shared/notify';
import { LeagueContext } from '../stats-shared/league-context';
import { PlayerCell } from '../stats-shared/player-cell';
import { STAT_COLUMNS, StatColumn, statCell } from '../stats-shared/player-columns';
import { RATE_OPTIONS, RatePicker } from '../stats-shared/rate-picker';
import { SeasonPicker } from '../stats-shared/season-picker';
import { PerPagePicker, StatsPaginator } from '../stats-shared/stats-paginator';
import { PagedStatsQueryConfig } from '../stats-shared/stats-query';
import { injectStatsScreen } from '../stats-shared/stats-screen';
import { WatchState } from '../watch/watch-state';

type Loaded = Page<PlayerStatRow, PageMeta & ScopedMeta>;

/** `WatchListParamsForm`: Player Stats' sort keys and rates, no Team. */
const CONFIG: PagedStatsQueryConfig<StatRate> = {
  sortKeys: ['name', 'team', 'games', ...STAT_COLUMNS.map((c) => c.key)],
  defaultSort: 'points_scored',
  defaultDir: 'desc',
  firstDir: 'desc',
  rates: RATE_OPTIONS.map(([value]) => value),
  paged: true,
};

const REMOVE_ALL_DIALOG: ConfirmDialogData = {
  title: 'Remove All',
  message: 'Remove every player from your watch list for this League?',
  confirm: 'Remove All',
};

/**
 * `/leagues/:id/players/watch-list` (spec #428 Watch List, `leagues/watch_list.html`): the
 * Account's watched Players as Player Stats rows, over a Season or the League career, as totals
 * or rates, sorted and paged on the server. The Season and Rate pickers render in every state
 * (loading, error, no Season, empty list); Remove All asks in a `ConfirmDialog`, then
 * `WatchState.clearAll()` and the empty-list state. Un-starring a row (the Player cell's flag)
 * leaves it in place until the next load. `injectStatsScreen` with `waitForSeason`: it waits for
 * the League nav and sends nothing without a displayed Season (I1); I2 is the kit's.
 */
@Component({
  selector: 'app-watch-list',
  imports: [
    RouterLink,
    MatButton,
    SeasonPicker,
    RatePicker,
    PerPagePicker,
    StatsPaginator,
    PlayerCell,
  ],
  templateUrl: './watch-list.html',
  styleUrl: './watch-list.scss',
})
export class WatchList {
  private readonly players = inject(LeaguePlayersService);
  private readonly watch = inject(WatchState);
  private readonly dialog = inject(MatDialog);
  private readonly notify = injectNotify();
  private readonly destroyRef = inject(DestroyRef);
  private readonly context = inject(LeagueContext);
  protected readonly nav = this.context.nav;
  /** I1: the nav has loaded and the League has no Season. */
  protected readonly noSeason = this.context.noSeason;
  protected readonly columns = STAT_COLUMNS;

  protected readonly screen = injectStatsScreen<Loaded, StatRate>(
    CONFIG,
    (id, params) => this.players.watchList(id, params),
    { waitForSeason: true },
  );

  /** The load Remove All emptied: the list reads empty until the next load replaces it. */
  private readonly clearedLoad = signal<Loaded | null>(null);
  protected readonly cleared = computed(
    () => this.clearedLoad() !== null && this.clearedLoad() === this.screen.result(),
  );

  /**
   * The Season picker's scope: the last response's, else (loading, a load error, no Season)
   * one built from the URL and the nav: the displayed Season, plus a `?season=` id the nav
   * can't name, then Career.
   */
  protected readonly scope = computed<SeasonScope>(() => {
    const season = this.screen.query().season;
    if (this.noSeason()) return { selected: season === 'career' ? 'career' : null, options: [] };
    const loaded = this.screen.result()?.meta.season_scope;
    if (loaded) return loaded;
    const options: SeasonOption[] = [];
    const displayed = this.nav()?.displayed_season ?? null;
    if (displayed) options.push({ ...displayed, year: null });
    if (typeof season === 'number' && season !== displayed?.id) {
      options.push({ id: season, name: `Season ${season}`, year: null });
    }
    if (season === 'career') return { selected: 'career', options };
    const id = season ?? displayed?.id;
    return { selected: options.find((o) => o.id === id) ?? null, options };
  });

  /** Asks first; on yes empties the list (`WatchState.clearAll`), else sends nothing. */
  protected removeAll(): void {
    this.dialog
      .open<ConfirmDialog, ConfirmDialogData, boolean>(ConfirmDialog, { data: REMOVE_ALL_DIALOG })
      .afterClosed()
      .pipe(
        filter((yes) => yes === true),
        switchMap(() => this.watch.clearAll()),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe({
        next: () => this.clearedLoad.set(this.screen.result()),
        error: (error: unknown) => this.notify(apiMessage(error, GENERIC_ERROR_MESSAGE)),
      });
  }

  protected cell(row: PlayerStatRow, column: StatColumn): string {
    return statCell(row.stats, column);
  }

  protected count(n: number): string {
    return countOf(n, 'player');
  }
}
