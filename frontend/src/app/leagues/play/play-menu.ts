import { BreakpointObserver } from '@angular/cdk/layout';
import { Component, DestroyRef, computed, effect, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { MatButton } from '@angular/material/button';
import { MatDialog } from '@angular/material/dialog';
import { MatMenu, MatMenuItem, MatMenuTrigger } from '@angular/material/menu';
import { MatProgressBar } from '@angular/material/progress-bar';
import { NavigationEnd, Router, RouterLink } from '@angular/router';
import { filter, map } from 'rxjs';
import { MemberNightSite } from '../../api/leagues.models';
import { MemberNightDialog, MemberNightDialogData } from './member-night-dialog';
import { PlayAction, PlayRun } from './play-run';

/** The Shell's `ROOMY`: at this width and wider the running label has room. */
const ROOMY = '(min-width: 600px)';

/** `/leagues/<numeric id>` or below; the id is group 1. */
const LEAGUE_URL = /^\/leagues\/(\d+)(?=[/?#]|$)/;

/**
 * The Shell toolbar's Play menu (spec #454), ported from `_partials/topnav_play.html`: the same
 * branches, labels and order, for the URL's League's Displayed Season. Shown only on
 * `/leagues/<id>` and below. While a Job runs it is Stop, "Running… c / t" and a progress bar.
 */
@Component({
  selector: 'app-play-menu',
  imports: [MatButton, MatMenu, MatMenuItem, MatMenuTrigger, MatProgressBar, RouterLink],
  templateUrl: './play-menu.html',
  styleUrl: './play-menu.scss',
})
export class PlayMenu {
  protected readonly run = inject(PlayRun);
  private readonly router = inject(Router);
  private readonly dialog = inject(MatDialog);

  private readonly url = toSignal(
    this.router.events.pipe(
      filter((event) => event instanceof NavigationEnd),
      map((event) => event.urlAfterRedirects),
    ),
    { initialValue: this.router.url },
  );

  /** The URL's League, or null off League routes. */
  protected readonly leagueId = computed(() => {
    const match = LEAGUE_URL.exec(this.url());
    return match ? Number(match[1]) : null;
  });

  protected readonly roomy = toSignal(
    inject(BreakpointObserver)
      .observe(ROOMY)
      .pipe(map((state) => state.matches)),
    { initialValue: false },
  );

  /** I1: every action waits while a request is in flight or a Job is polled. */
  protected readonly locked = computed(() => this.run.busy() || this.run.progress() !== null);

  /** The controls with a Displayed Season (I4); null shows only the disabled "Play". */
  protected readonly controls = computed(() => {
    const controls = this.run.controls();
    return controls?.displayed_season ? controls : null;
  });

  /** The round robin's third item (`topnav_play.html`). */
  protected readonly untilLabel = computed(() => {
    const controls = this.controls();
    if (!controls?.has_following_tournament_phase) return 'Play Until End of Season';
    return controls.following_tournament_is_final ? 'Play Until Playoffs' : 'Play Until Tournament';
  });

  protected readonly progressLabel = computed(() => {
    const progress = this.run.progress();
    return progress && progress.total > 0
      ? `Running… ${progress.completed} / ${progress.total}`
      : 'Running…';
  });

  /** The bar's fill, 0 to 100. */
  protected readonly progressValue = computed(() => {
    const progress = this.run.progress();
    return progress && progress.total > 0 ? (progress.completed / progress.total) * 100 : 0;
  });

  constructor() {
    effect(() => this.run.watch(this.leagueId()));
    // The Shell goes on sign-out: the next Account must not see this one's controls.
    inject(DestroyRef).onDestroy(() => this.run.watch(null));
  }

  protected act(action: PlayAction): void {
    this.run.run(action);
  }

  /** A Live item: plays the manager's game and opens the One Week (Live) screen. */
  protected live(source: 'season' | 'member_night'): void {
    this.run.playLive(source);
  }

  /** Asks which Sites in the member-night dialog; a confirmed choice draws the games. */
  protected setUpMemberNight(sites: MemberNightSite[]): void {
    this.dialog
      .open<MemberNightDialog, MemberNightDialogData, string[]>(MemberNightDialog, {
        data: { sites },
      })
      .afterClosed()
      .subscribe((chosen) => {
        if (chosen?.length) this.run.setUpMemberNight(chosen);
      });
  }
}
