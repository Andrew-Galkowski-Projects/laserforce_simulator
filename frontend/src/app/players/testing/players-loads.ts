// Players spec helpers. Imported only by *.spec.ts files.
import { TestBed } from '@angular/core/testing';
import { HttpTestingController } from '@angular/common/http/testing';
import { environment } from '../../../environments/environment';

/**
 * Releases the GETs a Players screen sends when it loads, for specs that only check routing or the
 * shell (each screen's own spec covers what it fetches). Call before `http.verify()`.
 */
export function releasePlayersLoads(): void {
  TestBed.inject(HttpTestingController).match(
    (req) => req.method === 'GET' && req.url.startsWith(`${environment.apiBaseUrl}/players/`),
  );
}
