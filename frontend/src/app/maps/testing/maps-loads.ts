// Maps spec helpers. Imported only by *.spec.ts files.
import { TestBed } from '@angular/core/testing';
import { HttpTestingController } from '@angular/common/http/testing';
import { environment } from '../../../environments/environment';

/**
 * Releases the GETs a Maps screen sends when it loads (the list, its thumbnails, the editor's
 * loads), for specs that only check routing or the shell (each screen's own spec covers what it
 * fetches). Call before `http.verify()`.
 */
export function releaseMapsLoads(): void {
  TestBed.inject(HttpTestingController).match(
    (req) => req.method === 'GET' && req.url.startsWith(`${environment.apiBaseUrl}/maps/`),
  );
}
