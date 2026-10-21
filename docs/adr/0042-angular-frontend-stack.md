# The frontend is a zoneless, standalone Angular 22 SPA with no SSR, tested with Vitest, calling the API directly over CORS

**Status:** Accepted (ANG-04, 2026-10-20).

Phase 8 replaces the Django templates with an Angular single-page app in `/frontend/`. ANG-04 fixes
the stack every later ANG-* task builds on:

- **Angular 22** (latest stable at the time), with the CLI pinned as a `devDependency` and run
  through `npx` / npm scripts, never as a global install. Node is pinned to 24 (`frontend/.nvmrc`).
- **Standalone components, zoneless change detection and signals**, the Angular 22 defaults for a
  new app. There are no NgModules.
- **No server-side rendering.** The app is a static build. ANG-07 serves it from nginx on the same
  origin as `/api/`.
- **Vitest** is the unit-test runner (the CLI default), headless in CI. **ESLint**
  (`angular-eslint`) and **Prettier** are enforced in CI.
- **Angular Material**, Material 3 theme, light/dark following the operating system.
  Refined by ADR-0043 (visual theme and token contract).
- **In dev the app calls Django directly** at `http://localhost:8000/api` from
  `http://localhost:4200`, the CORS-with-credentials contract ANG-02 set up. The base URL is
  `apiBaseUrl` in `src/environments/` (`/api` in production). The two dev origins are the same
  *site*, so ADR-0041's `SameSite=Lax` refresh cookie still travels.

## Considered options

- **NgModules / zone.js change detection.** Rejected: it is the legacy path, and new Angular code
  and docs assume standalone and zoneless.
- **SSR / prerendering.** Rejected: every screen sits behind a login and needs no SEO, and SSR would
  add a Node server to the deploy.
- **Karma or Jest.** Rejected: Karma needs a real browser in CI and is deprecated. Vitest is what
  the CLI generates now.
- **An `ng serve` proxy for `/api/`.** Rejected: it would hide CORS bugs until production. ANG-02
  already made the direct cross-origin path work and tests it.
- **A global `@angular/cli` install.** Rejected: it lets the CLI version on a machine drift from the
  project's version.

## Consequences

- CI gains a `frontend` job (lint, format check, tests, build). The `docker` deploy job waits on it.
- Django code is unaffected until ANG-05 starts calling the API. The Docker image excludes
  `frontend/` until ANG-07 adds the nginx build stage.
- Upgrading Angular majors goes through `ng update`, one major at a time.
