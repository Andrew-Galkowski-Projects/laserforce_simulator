import { BreakpointObserver } from '@angular/cdk/layout';
import { Component, DestroyRef, inject, signal } from '@angular/core';
import { takeUntilDestroyed, toSignal } from '@angular/core/rxjs-interop';
import { MatAnchor, MatButton } from '@angular/material/button';
import { ActivatedRoute, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { Subject, combineLatest, map, startWith, switchMap, tap } from 'rxjs';
import { LeagueNav } from '../../api/leagues.models';
import { LeaguesService } from '../../api/leagues.service';
import { notFoundOrError } from '../../shared/not-found-or-error';
import {
  LEAGUE_NAV_ENTRIES,
  LEAGUE_NAV_SECTION_LABELS,
  LeagueNavEntry,
  LeagueNavSection,
} from '../league-nav';
import { SoonBadge } from '../soon-badge/soon-badge';
import { LeagueContext } from '../stats-shared/league-context';

/** At this width and wider the League nav is a column beside the page (as the Shell's). */
const WIDE = '(min-width: 768px)';

/** One nav group: its heading (null for the top entry) and entries. */
interface NavGroup {
  section: LeagueNavSection;
  heading: string | null;
  entries: LeagueNavEntry[];
}

/** `LEAGUE_NAV_ENTRIES` split into its sections, keeping their order. */
const GROUPS: readonly NavGroup[] = LEAGUE_NAV_ENTRIES.reduce<NavGroup[]>((groups, entry) => {
  const last = groups.at(-1);
  if (last?.section === entry.section) last.entries.push(entry);
  else
    groups.push({
      section: entry.section,
      heading: LEAGUE_NAV_SECTION_LABELS[entry.section],
      entries: [entry],
    });
  return groups;
}, []);

/**
 * `/leagues/:id/...` (spec #409 F4): loads the League's nav and shows it beside the routed
 * screen; below 768px it collapses behind a "League menu" toggle. A 404 shows not-found and any
 * other failure the error box with Retry, both in place of the screen.
 */
@Component({
  selector: 'app-league-layout',
  imports: [MatAnchor, MatButton, RouterLink, RouterLinkActive, RouterOutlet, SoonBadge],
  templateUrl: './league-layout.html',
  styleUrl: './league-layout.scss',
  // The routed screens read the loaded nav from here (League name, displayed Season).
  providers: [LeagueContext],
})
export class LeagueLayout {
  protected readonly nav = inject(LeagueContext).nav;
  protected readonly notFound = signal(false);
  protected readonly loadError = signal<string | null>(null);
  protected readonly groups = GROUPS;
  /** Below 768px: whether the "League menu" toggle has the nav open. */
  protected readonly menuOpen = signal(false);
  protected readonly wide = toSignal(
    inject(BreakpointObserver)
      .observe(WIDE)
      .pipe(map((state) => state.matches)),
    { initialValue: false },
  );

  private readonly reloads = new Subject<void>();

  constructor() {
    const leagues = inject(LeaguesService);
    combineLatest([inject(ActivatedRoute).paramMap, this.reloads.pipe(startWith(undefined))])
      .pipe(
        tap(() => {
          this.notFound.set(false);
          this.loadError.set(null);
        }),
        switchMap(([params]) =>
          leagues
            .nav(Number(params.get('id')))
            .pipe(notFoundOrError<LeagueNav>(this.notFound, this.loadError)),
        ),
        takeUntilDestroyed(inject(DestroyRef)),
      )
      .subscribe((loaded) => this.nav.set(loaded));
  }

  protected retry(): void {
    this.reloads.next();
  }

  protected toggleMenu(): void {
    this.menuOpen.update((open) => !open);
  }
}
