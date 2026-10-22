// Matches spec helpers. Imported only by *.spec.ts files.
import { TestBed } from '@angular/core/testing';
import { HttpTestingController } from '@angular/common/http/testing';
import { environment } from '../../../environments/environment';

/** Every resource a Matches-area screen GETs when it loads (the list, the form, the stubs). */
const LOADED = ['matches/', 'rounds/', 'teams/', 'maps/'].map(
  (path) => `${environment.apiBaseUrl}/${path}`,
);

/**
 * Releases the GETs a Matches-area screen sends when it loads, for specs that only check routing
 * or the shell (each screen's own spec covers what it fetches). Call before `http.verify()`.
 */
export function releaseMatchesLoads(): void {
  TestBed.inject(HttpTestingController).match(
    (req) => req.method === 'GET' && LOADED.some((prefix) => req.url.startsWith(prefix)),
  );
}
