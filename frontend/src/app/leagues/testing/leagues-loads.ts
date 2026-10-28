// Leagues spec helpers. Imported only by *.spec.ts files.
import { TestBed } from '@angular/core/testing';
import { HttpTestingController } from '@angular/common/http/testing';
import { environment } from '../../../environments/environment';

/** The resources a Leagues screen reads on load: its own, plus the create form's map picker. */
const LOADED = ['leagues', 'seasons', 'maps'].map((r) => `${environment.apiBaseUrl}/${r}/`);

/**
 * Releases the GETs a Leagues screen sends when it loads, for specs that only check routing or
 * the shell (each screen's own spec covers what it fetches). Call before `http.verify()`.
 */
export function releaseLeaguesLoads(): void {
  TestBed.inject(HttpTestingController).match(
    (req) => req.method === 'GET' && LOADED.some((prefix) => req.url.startsWith(prefix)),
  );
}
