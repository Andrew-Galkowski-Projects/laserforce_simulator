// Leagues spec helpers. Imported only by *.spec.ts files.
import { TestBed } from '@angular/core/testing';
import { HttpTestingController } from '@angular/common/http/testing';
import { environment } from '../../../environments/environment';

/** The resources a Leagues screen reads on load: its own, plus the create form's map picker. */
const LOADED = ['leagues', 'seasons', 'maps'].map((r) => `${environment.apiBaseUrl}/${r}/`);

/**
 * Releases the GETs a Leagues screen sends when it loads, for specs that only check routing or
 * the shell (each screen's own spec covers what it fetches). Call before `http.verify()`. Every
 * `leagues/...` GET is released, the League nav and the ANG-06-16 and ANG-06-17 (finance)
 * screens' loads included.
 */
export function releaseLeaguesLoads(): void {
  TestBed.inject(HttpTestingController).match(
    (req) => req.method === 'GET' && LOADED.some((prefix) => req.url.startsWith(prefix)),
  );
}

/** The Shell's Play menu GET, sent on every `/leagues/<id>` URL (spec #454). */
const PLAY_CONTROLS = new RegExp(`^${environment.apiBaseUrl}/leagues/\\d+/play-controls/$`);

/**
 * Releases the Play menu's `play-controls/` GETs, for a spec that renders the Shell on a League
 * URL but tests the screen. Call before `http.verify()`.
 */
export function releasePlayControls(): void {
  TestBed.inject(HttpTestingController).match(
    (req) => req.method === 'GET' && PLAY_CONTROLS.test(req.url),
  );
}

/**
 * The ANG-06-16 screens' GETs (spec #428): a Team's roster / schedule / history / history
 * players, the League player page, Free Agents and the Watch List; and the ANG-06-17 finance
 * screens' GETs (spec #439): League Finances and a Team's finances.
 */
const TEAM_PLAYER_LOAD = new RegExp(
  `^${environment.apiBaseUrl}/leagues/\\d+/` +
    '(teams/\\d+/(roster|schedule|history|history/players|finances)|players/\\d+|free-agents|' +
    'watch-list|finances)/$',
);

/**
 * Releases only the League team, player and finance screens' GETs (spec #428 F9, spec #439),
 * leaving the layout's `nav/` and any write (a Watch flag PUT / DELETE, a finances PATCH) for the
 * spec to answer or assert. Use it when a spec lands on one of those screens but tests something
 * else (the layout, the nav, a ★ toggle).
 */
export function releaseLeagueTeamPlayerLoads(): void {
  TestBed.inject(HttpTestingController).match(
    (req) => req.method === 'GET' && TEAM_PLAYER_LOAD.test(req.url),
  );
}
