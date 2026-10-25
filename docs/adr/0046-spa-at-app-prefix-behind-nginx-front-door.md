# Until ANG-08, the SPA is served at `/app/` by an nginx front door that proxies everything else to Django

**Status:** Accepted (ANG-07, 2026-10-25). Refines ADR-0042's "ANG-07 serves it from nginx on the
same origin as `/api/`": still the same origin, now under a prefix.

The Django template views stay live until ANG-08, and they own `/`, `/teams/`, `/matches/`,
`/maps/` and `/accounts/`. The Angular routes (`/teams`, `/matches`, `/rounds`, `/maps`, `/login`)
collide with them, and the SPA does not yet have the League, Season, Tournament or registration
screens. So in the Docker stacks an nginx container is the single front door: it serves the built
SPA at `/app/` (built with `--base-href /app/`, deep links falling back to `/app/index.html`) and
proxies every other path to Django (`/api/`, `/admin/`, `/static/`, `/media/`, the template pages
and `/`). Both UIs then share one origin, so there is no CORS and the `Path=/api/token/` refresh
cookie (ADR-0041) still applies. ANG-08 moves the SPA to `/` once the templates are gone.

The base href is a Docker build flag only, so `angular.json`, `environment.prod.ts` and the CI
`frontend` build are unchanged and dev keeps calling Django directly (ADR-0042).

## Considered options

- **The SPA at `/`, with nginx proxying only `/api/`** (what PLAN.md first said). Rejected: the
  template pages become unreachable through nginx, so the public self-hosted URL would lose the
  League screens and registration until ANG-06 covers them.
- **The SPA and Django on separate ports.** Rejected: two origins mean CORS in production, and
  Tailscale Funnel would have to publish a second port.

## Consequences

- **Fly.io stays Django-only.** Fly runs one image per machine, so a sidecar doesn't map onto it.
  Serving the SPA there is a follow-up task.
- The self-hosted Tailscale handler points at nginx, not at Django. nginx keeps the incoming
  `X-Forwarded-Proto` so Django still sees https behind Funnel. That trusts the first hop:
  Tailscale's proxy rewrites the header, and the dev stack is local-only (its directly published
  Django `:8000` trusted the header already).
- Links to `/app/...` shared before ANG-08 break when the SPA moves to `/`, unless ANG-08 adds a
  redirect.
