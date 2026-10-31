// Season screen spec helpers (ANG-06-18, spec #444). Imported only by *.spec.ts files.
import { TestBed } from '@angular/core/testing';
import { HttpTestingController } from '@angular/common/http/testing';
import { RouterTestingHarness } from '@angular/router/testing';
import { environment } from '../../../../environments/environment';
import { LeagueNav, Season } from '../../../api/leagues.models';
import { leagueNav } from '../../testing/league-fixtures';
import { releaseLeagueChrome } from '../../testing/leagues-loads';
import { season } from './season-fixtures';

const BASE = environment.apiBaseUrl;

/**
 * Answers `SeasonLayout`'s `GET seasons/{id}/` with `body` (the active Season 11 of League 1 by
 * default), and releases the League chrome (`releaseLeagueChrome`: the `nav/` if it is still
 * open, the Play menu's `play-controls/`), then waits for the render. A
 * Season screen's component only exists after this: the layout renders its outlet once the
 * Season has loaded. The screen's own GETs are left for the spec to answer.
 *
 * Typical use, after `await harness.navigateByUrl(seasonUrl('awards'))`:
 * `await releaseSeasonLayout(harness);` then `http.expectOne(.../awards/)`.
 */
export async function releaseSeasonLayout(
  harness: RouterTestingHarness,
  body: Season = season(),
  nav: LeagueNav = leagueNav(),
): Promise<void> {
  releaseLeagueChrome(nav);
  TestBed.inject(HttpTestingController)
    .expectOne((req) => req.method === 'GET' && req.url === `${BASE}/seasons/${body.id}/`)
    .flush({ data: body, meta: {} });
  await harness.fixture.whenStable();
}

/**
 * Every Season screen's own GETs, not the layout's Season GET: `seasons/{id}/<screen>/`, plus the
 * side loads a screen makes beside it (Standings' `leagues/{id}/` for the managed Team, Manage
 * Conferences' `maps/` for the rotation pickers).
 */
const SCREEN_LOADS = [
  new RegExp(
    `^${BASE}/seasons/\\d+/(dashboard|standings|schedule|awards|owner-evaluation|conferences)/$`,
  ),
  new RegExp(`^${BASE}/leagues/\\d+/$`),
  new RegExp(`^${BASE}/maps/$`),
];

/**
 * Releases the Season screens' own GETs, leaving the layout's Season GET and any write for the
 * spec. Use it in a spec that lands on a Season screen but tests something else (the layout, the
 * breadcrumb, routing). Call before `http.verify()`.
 */
export function releaseSeasonScreenLoads(): void {
  TestBed.inject(HttpTestingController).match(
    (req) => req.method === 'GET' && SCREEN_LOADS.some((load) => load.test(req.url)),
  );
}
