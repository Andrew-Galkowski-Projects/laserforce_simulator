# The SPA is served at `/`; Django serves only `/api/`, `/admin/` and `/media/`

**Status:** Accepted (ANG-08, 2026-10-31). Supersedes
[ADR-0046](0046-spa-at-app-prefix-behind-nginx-front-door.md). In the Desktop App, Django
(WhiteNoise) serves the SPA instead of nginx: [ADR-0048](0048-desktop-app-go-pyinstaller-in-process-jobs.md).

ANG-08 deletes every Django template view, so nothing collides with the Angular routes any more.
The nginx front door now serves the built SPA at `/` (no `--base-href /app/`), with deep links
falling back to `/index.html`, and proxies only `/api/`, `/admin/`, `/static/` and `/media/` to
Django. A request for `/app/<path>` gets a 301 to `/<path>`, so links shared under ADR-0046 keep
working. Old Django page paths (`/seasons/<id>/`, `/accounts/login/`, ...) get no per-path
redirect: they reach the SPA and fall into its `**` route (simulation data is disposable,
ADR-0004).

The legacy surface goes as a whole, not only the pages: the JSON, CSV, PDF and POST-redirect
views that only the template JavaScript called each have an `/api/` twin, and keeping them would
leave two write paths to maintain. Django keeps `SessionAuthentication` and
`LoginRequiredMiddleware`, with `LOGIN_URL` pointing at the Admin login, so staff signed in to
Admin can still use the DRF browsable API.

## Considered options

- **Keep the SPA at `/app/`.** Rejected: the prefix existed only to avoid the template routes.
- **Redirect every old Django path to its SPA equivalent.** Rejected: dozens of mappings (Seasons
  move from `/seasons/<id>/` under `/leagues/<id>/seasons/<id>/`) for links to disposable data.
- **Delete only the HTML pages, keep the JSON / redirect views.** Rejected: nothing calls them once
  the templates are gone.
- **Drop `SessionAuthentication` now** (ADR-0041 allows it). Deferred: the browsable API is the
  only way to poke the API without the SPA.

## Consequences

- **Fly.io serves the API and Admin only, with no UI**, until a follow-up task serves the SPA there.
- A Django URL name used in a `reverse()` must be an API, Admin or media route; the API fields that
  reversed HTML names (`detail_url`, `season_url`, `processed_image_url`) are removed.
- CI's docker job and `scripts/smoke_nginx.sh` probe `/admin/login/` for Django readiness, CSRF and
  proxy headers, since `/` is the SPA.
