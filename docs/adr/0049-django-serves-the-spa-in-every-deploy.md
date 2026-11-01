# Django serves the SPA in every deploy; nginx is removed

**Status:** Accepted (ANG-10, 2026-11-01). Supersedes the nginx part of
[ADR-0047](0047-spa-at-root-django-api-and-admin-only.md); its URL split stands.

ADR-0047 left two ways of serving the built Angular app: an nginx front door in the Docker stacks,
and Django (WhiteNoise) in the Desktop App ([ADR-0048](0048-desktop-app-go-pyinstaller-in-process-jobs.md)).
Fly.io had neither. Now Django serves the SPA in every deploy: the self-host stack, the dev compose
stack, Fly.io and the Desktop App. The root `Dockerfile` builds the SPA into the image, WhiteNoise
serves its files from `SPA_ROOT`, and a catch-all view answers every deep link with `index.html`.
`/api/`, `/admin/`, `/static/` and `/media/` stay Django's own; the catch-all never answers them.
nginx (`frontend/Dockerfile` and its template) is deleted, and Tailscale Funnel proxies straight to
Django.

Django also takes over nginx's other jobs: `/app/<path>` 301s to `/<path>` (ADR-0046 links),
`index.html` is `no-cache`, hashed bundle files are cached as immutable, and the bundle is
pre-compressed at build time.

## Considered options

- **Keep nginx for the Docker stacks, WhiteNoise for Fly and the Desktop App.** Rejected: two
  serving paths to keep in step, and the Desktop App already needs the Django one.
- **An nginx process in the Fly machine.** Rejected: a process manager in the image for one deploy
  that is currently off, and still a second path beside the Desktop App's.
- **A second Fly app for the SPA.** Rejected: cross-origin API calls and two deploys for one release.

## Consequences

- One image serves everything; `SPA_ROOT` unset (`runserver`, pytest) means no SPA and `/` is 404.
- nginx's 20 MB request-body cap goes with it; a map upload size limit is a Deferred Item.
- gunicorn's sync workers serve the bundle too. Fine at this scale: the bundle is about 2 MB and
  browsers cache the hashed files.
- The self-host stack is the canonical Hosted deploy; Fly.io is optional and stays off until a
  `FLY_API_TOKEN` secret is set.
