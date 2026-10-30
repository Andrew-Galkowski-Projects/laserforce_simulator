// Leagues spec helpers. Imported only by *.spec.ts files.
import { TestBed } from '@angular/core/testing';
import { HttpTestingController } from '@angular/common/http/testing';
import { environment } from '../../../environments/environment';

/** The resources a Leagues screen reads on load: its own, plus the create form's map picker. */
const LOADED = ['leagues', 'seasons', 'maps'].map((r) => `${environment.apiBaseUrl}/${r}/`);

/**
 * Releases the GETs a Leagues screen sends when it loads, for specs that only check routing or
 * the shell (each screen's own spec covers what it fetches). Call before `http.verify()`. Every
 * `leagues/...` GET is released, the League nav and the ANG-06-16 screens' loads included.
 */
export function releaseLeaguesLoads(): void {
  TestBed.inject(HttpTestingController).match(
    (req) => req.method === 'GET' && LOADED.some((prefix) => req.url.startsWith(prefix)),
  );
}

/**
 * The ANG-06-16 screens' GETs (spec #428): a Team's roster / schedule / history / history
 * players, the League player page, Free Agents and the Watch List.
 */
const TEAM_PLAYER_LOAD = new RegExp(
  `^${environment.apiBaseUrl}/leagues/\\d+/` +
    '(teams/\\d+/(roster|schedule|history|history/players)|players/\\d+|free-agents|watch-list)/$',
);

/**
 * Releases only the League team and player screens' GETs (spec #428 F9), leaving the layout's
 * `nav/` and any write (a Watch flag PUT / DELETE) for the spec to answer or assert. Use it when a
 * spec lands on one of those screens but tests something else (the layout, the nav, a ★ toggle).
 */
export function releaseLeagueTeamPlayerLoads(): void {
  TestBed.inject(HttpTestingController).match(
    (req) => req.method === 'GET' && TEAM_PLAYER_LOAD.test(req.url),
  );
}
