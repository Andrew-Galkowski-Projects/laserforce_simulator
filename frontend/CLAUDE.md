# frontend/ -- the Angular SPA (ANG-04)

The Phase 8 replacement for the Django templates. The stack is fixed by
[ADR-0042](../docs/adr/0042-angular-frontend-stack.md). It has a sign-in page, the ADR-0043
layout-B shell (header with the signed-in email and Sign out, a Teams sidebar that is a drawer
below 768px) behind `authGuard`, the Teams feature routes (ANG-06a, ANG-06b, ANG-06 2b) and the
Matches area (ANG-06c: Match History, New Match / New Single Round; ANG-06d: Match and Round detail), on top of the client
layer from ANG-05: typed API services and the JWT sign-in / silent-refresh flow. See **API and
auth** below.

## Stack

- **Angular 22** (22.2.x), scaffolded with `npx -p @angular/cli@22 ng new`. The CLI is a
  `devDependency`: run it as `npx ng ...` or through the npm scripts, never a global install.
- **Standalone components, zoneless change detection, signals.** No NgModules, no `zone.js`.
- **No SSR.** `ng build` writes plain static files to `dist/frontend/browser/` (served by nginx in
  ANG-07).
- **Angular Material** (and the CDK), Material 3, themed from the app's `--lf-*` tokens and
  following the OS light/dark setting. See **Theme** below and
  [ADR-0043](../docs/adr/0043-visual-theme-lfstats-layout-and-token-contract.md).
- **Vitest** through the `@angular/build:unit-test` builder (`ng test`), on jsdom.
  `vitest-base.config.mts` (wired by `runnerConfig` in `angular.json`) only raises `testTimeout`
  to 15 s: under the full parallel run a heavy route spec's first render passed 5 s (ANG-06c).
- **ESLint** (`angular-eslint`, `eslint.config.js`) and **Prettier** (`.prettierrc`, print width
  100, single quotes, `endOfLine: auto`). Both are enforced in CI.
- **Node 24** (`.nvmrc`, `engines.node >=24`), npm only, `package-lock.json` committed.

## Commands

Run from the repo root with `--prefix` (no `cd` needed):

```bash
npm --prefix frontend ci                  # install exactly what package-lock.json says
npm --prefix frontend start               # ng serve on http://localhost:4200
npm --prefix frontend run start:worktree  # agent worktrees: :4201, API on Django :8001
npm --prefix frontend test                # Vitest; watches in a terminal, single run when CI is set
npm --prefix frontend run test:ci         # Vitest, one headless run (what CI uses)
npm --prefix frontend run lint            # ng lint
npm --prefix frontend run format:check    # prettier --check .
npm --prefix frontend run format          # prettier --write .
npm --prefix frontend run build           # production build (environment.prod.ts swapped in)
npm --prefix frontend exec -- ng generate component foo   # any other ng command
```

Agents reading test output: `NO_COLOR=1 npm --prefix frontend run test:ci 2>&1 | tail -5` gives a
plain summary; without `NO_COLOR` the ANSI codes defeat `grep`.

`ng add` / `ng generate` resolve paths against the project in `frontend/`, so
`npm --prefix frontend exec -- ng ...` works from the root.

**A background dev server:** to know when it's up, poll the port with
`until curl -s localhost:<port> >/dev/null; do sleep 1; done`. Grepping its log for `Local:`
doesn't work, because the line carries ANSI colour codes. To stop it, run
`powershell -NoProfile -File scripts/stop_worktree_servers.ps1` through the PowerShell tool (it
defaults to the current worktree; pass `-Worktree <path>` for another). Stopping the npm wrapper
alone leaves node, esbuild and dart running.

CI's `frontend` job (`.github/workflows/ci.yml`) runs `npm ci`, `lint`, `format:check`,
`test:ci` and `build`, in parallel with the Python `test` job. The `docker` deploy job needs both.

## Layout

```
src/
  main.ts                 bootstrapApplication(App, appConfig)
  styles.scss             global styles: uses the theme partials below
  styles/
    _tokens.scss          the --lf-* token contract, light + dark (colour literals: here + below)
    _sides.scss           [data-lf-side='red'|'blue'] -> --side, --side-text, --side-bg, --side-dim
    _material.scss        mat.theme + mat.theme-overrides from the tokens
    _theme-colors.scss    generated M3 tonal palettes (ng generate @angular/material:theme-color)
  environments/
    environment.ts        dev:  { production: false, apiBaseUrl: 'http://localhost:8000/api' }
    environment.prod.ts   prod: { production: true,  apiBaseUrl: '/api' }
    environment.worktree.ts  agent worktrees: { apiBaseUrl: 'http://localhost:8001/api' } (start:worktree)
  app/
    app.ts                the root: a bare <router-outlet> (/login renders without the shell)
    app-title.ts          APP_TITLE, shared by the header and the route titles
    app.config.ts         providers (router, HttpClient + authInterceptor, auth initializer)
    app.routes.ts         'login' (signedOutGuard, lazy) | '' -> lazy Shell behind authGuard:
                          '' -> /teams, 'teams' -> lazy teams/teams.routes.ts,
                          'matches' / 'rounds' -> lazy matches/matches.routes.ts
                          (MATCHES_ROUTES / ROUNDS_ROUTES, one chunk) | '**' -> ''
    shell/                layout B: header (title, email, Sign out) + mat-sidenav (side >= 768px,
                          over drawer below); toolbar colours via mat.toolbar-overrides
    login/                sign-in form; returnUrl honoured only when it is an in-app path
    shared/               cross-feature helpers: save-blob.ts (saveBlob: a Blob as a named
                          download; roster-import's template CSV, round-detail's Export PDF)
    teams/                teams.routes.ts (TEAMS_ROUTES) + one folder per screen: team-list,
                          team-create, team-detail, team-edit, team-slots, player-add,
                          player-edit, team-generate, roster-import (import-errors.ts:
                          classifyImportErrors sorts a failed import's errors);
                          shared/: roster.ts (SLOTS, ROLE_LABELS, bench split),
                          player-stats.ts (STAT_GROUPS, STAT_KEYS, overallRating),
                          player-form.ts + .html (app-player-form: Add / Edit Player),
                          route-team.ts (loadRouteTeam: the :id Team, 404 -> notFound, else
                          loadError), route-player.ts (loadRoutePlayer: the :playerId Player,
                          404 or player.team !== :id -> notFound, else loadError),
                          player-not-found.ts (back to /teams/:id),
                          notify.ts (injectNotify: the "Close" snackbar), team-not-found.ts,
                          team-name-form.ts, form-errors.ts (applyApiErrors, GENERIC_ERROR_MESSAGE), team-page.scss,
                          team-form.scss; testing/teams-loads.ts (releaseTeamsLoads, specs only)
    matches/              matches.routes.ts: /matches (match-list: Match History, two server-paged
                          tables, each row's teams-cell <a> stretched over the row), /matches/new +
                          /rounds/new (setup-form, route data kind 'match' | 'round'; also uses
                          teams/shared/form-errors), /matches/:id + /rounds/:id (match-detail,
                          round-detail: ANG-06d, layout B, from the Round API's `scoreboard`),
                          /rounds/:id/events (round-events: the Event log, ANG-06e, from
                          `rounds/:id/timeline/`; CDK virtual scroll, client-side filters);
                          shared/: event-groups.ts (EVENT_GROUPS, eventGroupOf), result.ts (matchResult / roundResult: winner, "Tie", muted
                          "In progress"), result-chip, route-record.ts (loadRouteRecord),
                          record-not-found, setup-kind; testing/ (releaseMatchesLoads, fixtures,
                          scoreboard-fixtures, timeline-fixtures)
    api/                  api-client.ts (ApiClient, ApiError, Page<T>, ListParams) and one
                          <resource>.service.ts + <resource>.models.ts per resource
                          (teams, players, matches, rounds, maps); testing/ holds shared spec fixtures
                          (player-fixtures; auth-fixtures: USER + signIn() for route specs)
    auth/                 auth.service.ts, auth.interceptor.ts, auth.initializer.ts,
                          auth.guard.ts (authGuard, signedOutGuard),
                          auth.tokens.ts (TOKEN_PATH, SKIP_AUTH_REFRESH), user.ts
    theme/                theme-tokens.ts (LF_TOKENS) + theme.spec.ts
```

Specs sit next to the file they cover (`*.spec.ts`). Test what you can see from outside -- the
rendered text, where the router lands -- not component internals. `app.routes.spec.ts` uses
`RouterTestingHarness` with the real `routes`.

## Theme

The look is fixed by [ADR-0043](../docs/adr/0043-visual-theme-lfstats-layout-and-token-contract.md):
the lfstats-like layout B as the structure for screens, palette B colours in dark mode, palette A
colours in light mode, all-mono JetBrains Mono.

- **Tokens live in `src/styles/_tokens.scss`** on `:root`: light by default, dark under
  `@media (prefers-color-scheme: dark)`. The list of names is `LF_TOKENS` in
  `src/app/theme/theme-tokens.ts`; what each one means is in ADR-0043.
- **No colour literal in any `.scss` but `_tokens.scss` and the generated `_theme-colors.scss`.**
  Use a token, or derive a tint with `color-mix(in srgb, var(--side) 20%, transparent)`. A new
  token goes in `_tokens.scss` (light and dark) and in `LF_TOKENS`.
- **Sides:** put `data-lf-side="red"` / `"blue"` on a team container and read `--side`,
  `--side-text`, `--side-bg`, `--side-dim` inside it. Nothing inside picks red or blue itself.
  Keep the R / B badge next to Side colour for colour-blind users.
- **Material** follows the tokens: `_material.scss` generates every `--mat-sys-*` role and points
  every colour role but error / shadow / scrim at `--lf-*` (a token or a `color-mix` of tokens),
  so no component needs local `--mat-sys-*` patches. Density is left at Material's default
  (a layout concern for ANG-06). Data screens (team strips, stat tables, the log) are hand-rolled.
- **Font:** JetBrains Mono is self-hosted by `@fontsource/jetbrains-mono` (400 / 500 / 700, in the
  `angular.json` `styles` list). Never add a Google Fonts link or other font CDN.
- `theme.spec.ts` compiles the real `styles.scss` into jsdom and reads the computed tokens and
  Material roles on the document root (and `--side*` on `[data-lf-side]` elements) in light and
  dark (it emulates `prefers-color-scheme` by rewriting the media condition, since jsdom matches no
  media feature). It also checks the dark block declares every colour token itself and scans
  every `.scss` under `src/` for colour literals.

## API and auth

Built in ANG-05 (spec #257). The server contract is ADR-0039 (envelope) and ADR-0041 (JWT).

- **`ApiClient` is the only `HttpClient` user.** Components inject a resource service, never
  `HttpClient`. Methods: `get`, `list`, `post`, `patch`, `delete`, `upload` (multipart) and `getRaw` (a Blob
  with the bearer header and no envelope unwrap: the CSV template, ANG-06 2b). Each takes a
  path relative to `apiBaseUrl` (`'teams/'`, `'rounds/7/events/'`, trailing slash as the API routes
  it) and an optional `{ context }`.
- **Envelope unwrapped once:** objects come back as `T`, lists as `Page<T>` (`items` plus `meta`:
  `page`, `page_size`, `count`, `total_pages`), and a 204 as `undefined`. Undefined params are
  dropped from the query.
- **Every failure is an `ApiError`** (`status`, `errors: {code, message, field}[]`, never empty).
  Status 0 gives `network_error`; a non-envelope 5xx gives `server_error`. Cross-Account rows come
  back as 404, because ownership is filtered on the server.
- **Models are hand-written and snake_case**, exactly as the DRF serializers send them (there is no
  OpenAPI schema and no mapping layer). Where the list and detail serializers differ, they are
  separate types (`GameRoundListItem` vs `GameRound`). Add a field when the serializer gains one.
- **Services cover only what ANG-06's first screens need.** A League, Tournament or analytics
  service is added by the ANG-06 item that first uses it, as its own `<resource>.service.ts`.
- **Auth:**
  - `AuthService` keeps the access token **in memory only**. It provides `login`, `logout`,
    `register` (which does not sign in), the `currentUser` / `isSignedIn` signals, a single-flight
    `refresh()`, and `restoreSession()`.
  - `authInterceptor` adds `Bearer` to `apiBaseUrl` calls except `token/*`, and sets
    `withCredentials` only on `token/*`, where the refresh cookie's `Path=/api/token/` applies.
  - On a 401 it does one shared refresh and replays the request once. A failed refresh, or a second
    401, clears the session and navigates to `/login?returnUrl=<url>`; if the person is already on
    `/login`, it keeps that URL.
  - A request whose context sets `SKIP_AUTH_REFRESH` gets its 401 back as-is. The `accounts/me/`
    call made by login and restore uses this, so a failed restore never redirects.
  - The initializer runs `restoreSession()` once at bootstrap.
- **Tests:** use `provideHttpClient(withInterceptors([authInterceptor]))` plus
  `provideHttpClientTesting()` / `HttpTestingController`. Assert the request (URL, method, body,
  headers) and what the caller receives. `teams.service.spec.ts` is the template for a service
  spec, and `auth.interceptor.spec.ts` for the flow.
- **Route specs:** assert the routed screen with
  `fixture.debugElement.query(By.directive(Screen))`. A failing `toBeInstanceOf` / `toEqual` on a
  component instance makes Vitest print its whole object graph and run Node out of memory, with
  no test summary. A spec that lands on a screen it isn't testing (shell, login, routes) releases
  that screen's GETs before `verify()` (`teams/testing/teams-loads.ts`).
- **Live check without the owner's ports:**
  - Run Django with `CORS_ALLOWED_ORIGINS=http://localhost:4201 manage.py runserver 8001`.
  - Run `npm --prefix frontend run start:worktree`. Its `worktree` configuration serves on 4201
    and swaps in `environment.worktree.ts`, which points at `:8001`. No committed file is edited.
  - In dev mode, reach a service from DevTools through `ng.ɵgetInjectorProviders`.

## The `apiBaseUrl` rule

`environment.apiBaseUrl` is the **only** place an API host may appear. Services build every URL
from it (`${environment.apiBaseUrl}/teams/`); never hard-code `localhost:8000` or `/api` anywhere
else. The production build swaps `environment.ts` for `environment.prod.ts` through
`fileReplacements` in `angular.json`.

In dev the app calls Django directly, cross-origin, with no `ng serve` proxy (ADR-0042). Django's
CORS allow-list (ANG-02) has exactly `http://localhost:4200`, so open the app on `localhost`, not
`127.0.0.1`. Both dev origins are the same site, so the `SameSite=Lax` refresh cookie (ADR-0041)
still travels.

## Windows notes

- **Write source files with the editor tools (Write / Edit), never PowerShell `Set-Content` /
  `Out-File`.** PowerShell 5.1 adds a UTF-8 BOM. `scripts/check_encoding.py` (CI and the
  pre-commit hook) scans `.ts` and `.scss` as well as `.html`, `.json`, `.css` and `.js`, and
  fails on a BOM.
- `core.autocrlf` is on, so a Windows checkout has CRLF endings. `.prettierrc` sets
  `endOfLine: auto` so `format:check` passes on both Windows and the Linux CI runner.
- `node_modules/`, `dist/` and `.angular/cache/` are ignored by `frontend/.gitignore`; never
  stage them. The root `.dockerignore` excludes the whole of `frontend/` until ANG-07.
