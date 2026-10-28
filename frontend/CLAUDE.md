# frontend/ -- the Angular SPA (ANG-04)

The Phase 8 replacement for the Django templates. The stack is fixed by
[ADR-0042](../docs/adr/0042-angular-frontend-stack.md). It has the sign-in and invite-only register
pages, the ADR-0043 layout-B shell (header with the account menu, a Teams sidebar that is a drawer
below 768px) behind `authGuard`, Change password (ANG-06 item 7), the Teams feature routes (ANG-06a, ANG-06b, ANG-06 2b), the
Players area (ANG-06-8: list, player detail, career stats, role benchmarks) and the
Matches area (ANG-06c: Match History, New Match / New Single Round; ANG-06d: Match and Round detail;
ANG-06e-g: Event log, Playback, Charts; ANG-06-10: Missile log, Movement heatmap), the Maps area (ANG-06h: map list and editor) and the
Tournaments area (ANG-06-13a: list, create, detail with participant import and the Player pool), on top of the client
layer from ANG-05: typed API services and the JWT sign-in / silent-refresh flow. See **API and
auth** below.

## Stack

- **Angular 22** (22.2.x), scaffolded with `npx -p @angular/cli@22 ng new`. The CLI is a
  `devDependency`: run it as `npx ng ...` or through the npm scripts, never a global install.
- **Standalone components, zoneless change detection, signals.** No NgModules, no `zone.js`.
- **No SSR.** `ng build` writes plain static files to `dist/frontend/browser/` (served by nginx at
  `/app/` in the Docker stacks: see **Served from Docker** below).
- **Angular Material** (and the CDK), Material 3, themed from the app's `--lf-*` tokens and
  following the OS light/dark setting. See **Theme** below and
  [ADR-0043](../docs/adr/0043-visual-theme-lfstats-layout-and-token-contract.md).
- **Vitest** through the `@angular/build:unit-test` builder (`ng test`), on jsdom.
  `vitest-base.config.mts` (wired by `runnerConfig` in `angular.json`) only raises `testTimeout`
  to 15 s: under the full parallel run a heavy route spec's first render passed 5 s (ANG-06c).
  `src/test-setup.ts` (`setupFiles`) resets the TestBed before each test, so a spec whose
  `afterEach(verify)` throws fails alone instead of failing every later test in its file.
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
npm --prefix frontend exec -- prettier --write frontend/<path>   # one file; path from the repo root
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
    app.routes.ts         'login' + 'register' (signedOutGuard, lazy) | '' -> lazy Shell behind
                          authGuard: '' -> /teams, 'account/password', 'teams' -> lazy
                          teams/teams.routes.ts, 'players' -> lazy players/players.routes.ts,
                          'matches' / 'rounds' -> lazy matches/matches.routes.ts
                          (MATCHES_ROUTES / ROUNDS_ROUTES, one chunk), 'maps' -> lazy
                          maps/maps.routes.ts, 'tournaments' -> lazy
                          tournaments/tournaments.routes.ts | '**' -> ''
    shell/                layout B: header (title, the "Account" menu: My player / Unlink
                          Self-insert when linked, Change password, Sign out; the email, or its
                          initial below 600px) + mat-sidenav (side >= 768px, over drawer below);
                          toolbar colours via mat.toolbar-overrides
    login/                sign-in form; returnUrl honoured only when it is an in-app path;
                          LoginNavigationState / REGISTERED_STATE ("Account created" notice)
    register/             invite-only sign-up (ANG-06 item 7): invite from ?invite=, register
                          then login, /login + REGISTERED_STATE if that sign-in fails
    account/              password-change/ (/account/password: inline notice, resetForm, stays
                          signed in)
    shared/               cross-feature helpers: save-blob.ts (saveBlob: a Blob as a named
                          download; csv-import's template CSV, round-detail's Export PDF),
                          confirm-dialog.ts (ConfirmDialog), form-errors.ts (applyApiErrors,
                          clearServerErrors: drop `server` errors before a resubmit, keeping
                          `required` / `min`; GENERIC_ERROR_MESSAGE, REQUIRED_MESSAGE),
                          count-of.ts (countOf: "1 player" / "2 players"), read-token.ts (readToken: a
                          --lf-* value for canvas; resolveColor: a CSS colour as the browser
                          computes it), notify.ts (injectNotify: the "Close" snackbar),
                          api-message.ts (apiMessage: an ApiError's message or a fallback),
                          line-chart.ts (app-line-chart over Chart.js, ADR-0045: clock or
                          count x-axis; Round charts + career trend),
                          auth-card.scss (the signed-out card: login + register),
                          _alerts.scss (notice / error box mixins), _badges.scss (the
                          "You" badge mixin; tournament-state, the State badge on a
                          [data-state] element), import-errors.ts (classifyImportErrors: a
                          failed CSV import's errors as file / rows / alert), csv-import.ts
                          (CsvImport: one roster-CSV card's file, template download, busy flag
                          and the three error lists; `new CsvImport()` as a component field,
                          then `upload(send, done)`; roster-import, participant-import,
                          tournament-pool), not-found-or-error.ts (notFoundOrError: a
                          404 sets notFound, anything else the apiMessage),
                          head-to-head/ (ANG-06-9: h2h-query.ts URL readers incl. queryId,
                          the cards, per-map table and charts both H2H screens share)
    players/              ANG-06-8, players.routes.ts (PLAYERS_ROUTES): /players (player-list:
                          server-sorted, -paged, URL state ?sort=&dir=&page=&per_page=),
                          /players/benchmarks (role-benchmarks), /players/:id/stats
                          (player-career: totals, per-role benchmark tables, trend chart),
                          /players/head-to-head (player-head-to-head, ANG-06-9);
                          shared/: benchmark-filter.ts (the ?threshold=&display= form +
                          benchmarkParams), benchmark-format.ts, benchmark-page.scss;
                          testing/players-loads.ts (releasePlayersLoads, specs only)
    teams/                teams.routes.ts (TEAMS_ROUTES) + one folder per screen: team-list,
                          team-create, team-detail, team-edit, team-slots, player-add,
                          player-edit, player-detail (ANG-06-8: /teams/:id/players/:playerId,
                          rating-tier Stat bars, Delete, This is me / Unlink),
                          team-head-to-head (/teams/head-to-head) and team-history
                          (/teams/:id/history), both ANG-06-9,
                          team-generate, roster-import;
                          shared/: roster.ts (SLOTS, ROLE_LABELS, bench split),
                          player-stats.ts (STAT_GROUPS, STAT_KEYS, overallRating),
                          player-form.ts + .html (app-player-form: Add / Edit Player),
                          route-team.ts (loadRouteTeam: the :id Team, 404 -> notFound, else
                          loadError), route-player.ts (loadRoutePlayer: the :playerId Player,
                          404 or player.team !== :id -> notFound, else loadError),
                          player-not-found.ts (back to /teams/:id),
                          team-not-found.ts,
                          team-name-form.ts, team-page.scss,
                          team-form.scss; testing/teams-loads.ts (releaseTeamsLoads, specs only)
    matches/              matches.routes.ts: /matches (match-list: Match History, two server-paged
                          tables, each row's teams-cell <a> stretched over the row), /matches/new +
                          /rounds/new (setup-form, route data kind 'match' | 'round'; also uses
                          shared/form-errors; pickers via listAll; on success a notify.ts
                          "Match simulated! / Round complete! <winner> won!" or "Tie game.",
                          ANG-06-11), /matches/batch (batch-simulate/: ANG-06-12, the
                          BatchSimulate run / poll / save screen, score-histogram.ts
                          scoreHistogram + score-histogram-chart.ts, a Chart.js bar chart;
                          api/batch.service.ts), /matches/:id + /rounds/:id (match-detail,
                          round-detail: ANG-06d, layout B, from the Round API's `scoreboard`),
                          /rounds/:id/events (round-events: the Event log, ANG-06e, from
                          `rounds/:id/timeline/`; CDK virtual scroll, client-side filters),
                          /rounds/:id/playback (playback/: ANG-06f, the cursor shell +
                          snapshot-at.ts, playback-scoreboard, playback-feed, the beta
                          playback-map; from `rounds/:id/playback/` + the timeline),
                          /rounds/:id/charts (round-charts/: ANG-06g, event-charts.ts
                          (pure data + OVERLAY_KINDS), chart-colors.ts, the RoundCharts page;
                          the chart itself is shared/line-chart.ts), /rounds/:id/missile-log
                          (missile-log/: ANG-06-10), /rounds/:id/heatmap (round-heatmap/:
                          ANG-06-10, heatmap-filter.ts filteredOccupancy + the map canvas),
                          /rounds/compare
                          (compare-rounds/: ANG-06-9, the stat-delta table + points chart);
                          shared/: clock.ts (tickClock: ticks as m:ss, or mm:ss with pad),
                          role-label.ts (roleLabel: CMD/HVY/...), side-tag.ts (sideTag: R / B),
                          event-groups.ts (EVENT_GROUPS, EventGroupKey, eventGroupOf), result.ts (matchResult / roundResult: winner, "Tie", muted
                          "In progress"), result-chip, route-record.ts (loadRouteRecord),
                          record-not-found, setup-kind; testing/ (releaseMatchesLoads, fixtures,
                          scoreboard-fixtures, timeline-fixtures, playback-fixtures,
                          batch-fixtures, fake-canvas: a
                          no-op 2D context + ResizeObserver for Chart.js specs)
    maps/                 ANG-06h, maps.routes.ts: /maps (map-list: cards, upload, delete; follows
                          every page) and /maps/:id/editor (map-editor/: the shell with the Zone-size
                          picker, mode toggle and canDeactivate guard; map-editor.store.ts
                          (MapEditorStore, one per visit: all three modes' state + per-area dirty
                          flags); map-canvas.ts (useMapLayer: the drawing hook a mode registers;
                          read its header comment first); map-stage.ts, map-paint.ts, map-palette.ts
                          (the --lf-map-* tokens), grid.ts). One folder per mode, each with a pure
                          module beside its component: zones-bases/ (zones.ts), sight-lines/
                          (sight-edit.ts: 100-key batch split), heatmap/ (heat-cells.ts);
                          testing/ (editor-fixtures incl. releaseModeLoads,
                          maps-loads). The Django editor template is the behavioural reference.
    tournaments/          ANG-06-13a, tournaments.routes.ts (TOURNAMENTS_ROUTES): /tournaments
                          (tournament-list: server-paged, newest first, State badge),
                          /tournaments/new (tournament-create: TournamentCreateForm's show / hide
                          rules from one visibleFields predicate), /tournaments/:id
                          (tournament-detail: the host; loads the detail, each section a child
                          that emits `changed` to reload it). Sections: participant-import
                          (preset, setup, sandbox: Import Participants CSV) and tournament-pool
                          (Player pool: the table, add / generate / import cards, Remove);
                          testing/ (detail-fixtures, pool-fixtures incl. releasePoolLoads,
                          tournaments-loads: releaseTournamentsLoads, specs only)
    api/                  api-client.ts (ApiClient, ApiError, Page<T>, ListParams) and one
                          <resource>.service.ts + <resource>.models.ts per resource
                          (teams, players, matches, rounds, maps, batch, tournaments:
                          TOURNAMENT_STATE_LABELS, TEAM_ASSEMBLY_LABELS); testing/ holds shared spec fixtures
                          (player-fixtures; auth-fixtures: USER + signIn() for route specs)
    auth/                 auth.service.ts, auth.interceptor.ts, auth.initializer.ts,
                          auth.guard.ts (authGuard, signedOutGuard, HOME_URL),
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
- **Layout breakpoints:** from 768px the Shell's sidebar sits beside the page (~208px), so the
  content area is ~560px at 768px and ~1070px at 1280px. Size a multi-column layout by that content
  width, not the viewport (ANG-06-8's player detail stacks below 1100px).
- **Font:** JetBrains Mono is self-hosted by `@fontsource/jetbrains-mono` (400 / 500 / 700, in the
  `angular.json` `styles` list). Never add a Google Fonts link or other font CDN.
- **Canvas** (Chart.js, the playback map) ignores CSS: pass it token values read at runtime,
  `--lf-font-body` as well as the colours ([ADR-0045](../docs/adr/0045-chart-js-for-angular-event-charts.md)).
- `theme.spec.ts` compiles the real `styles.scss` into jsdom and reads the computed tokens and
  Material roles on the document root (and `--side*` on `[data-lf-side]` elements) in light and
  dark (it emulates `prefers-color-scheme` by rewriting the media condition, since jsdom matches no
  media feature). It also checks the dark block declares every colour token itself, scans
  every `.scss` under `src/` for colour literals, and holds every text colour token (`--lf-text`,
  `*-text`, `--lf-ev-*`) to 4.5:1 on `--lf-bg` and `--lf-surface` in both modes.

## API and auth

Built in ANG-05 (spec #257). The server contract is ADR-0039 (envelope) and ADR-0041 (JWT).

- **`ApiClient` is the only `HttpClient` user.** Components inject a resource service, never
  `HttpClient`. Methods: `get`, `list`, `post`, `patch`, `put`, `delete`, `upload` (multipart), `getRaw` (a Blob
  with the bearer header and no envelope unwrap: the CSV template, ANG-06 2b) and `getFile`
  (`getRaw` plus the `Content-Disposition` file name as `DownloadedFile`, `null` when absent: the
  Round report, ANG-06-10). Each takes a
  path relative to `apiBaseUrl` (`'teams/'`, `'rounds/7/events/'`, trailing slash as the API routes
  it) and an optional `{ context }`.
- **Envelope unwrapped once:** objects come back as `T`, lists as `Page<T>` (`items` plus `meta`:
  `page`, `page_size`, `count`, `total_pages`), and a 204 as `undefined`. Undefined params are
  dropped from the query; an array param repeats its key (`?scope=a&scope=b`).
- **Every row of a list:** `listAll(fetchPage)` (`api/list-all.ts`) asks for page 1 at
  `page_size=100`, follows `meta.total_pages`, and concatenates the pages; a failing page fails the
  whole list. Pickers and `map-list` use it: `listAll((page) => this.maps.list({ ...page, ordering }))`.
- **Every failure is an `ApiError`** (`status`, `errors: {code, message, field}[]`, never empty).
  Status 0 gives `network_error`; a non-envelope 5xx gives `server_error`. Cross-Account rows come
  back as 404, because ownership is filtered on the server.
- **Models are hand-written and snake_case**, exactly as the DRF serializers send them (there is no
  OpenAPI schema and no mapping layer). Where the list and detail serializers differ, they are
  separate types (`GameRoundListItem` vs `GameRound`). Add a field when the serializer gains one.
- **Services cover only what ANG-06's first screens need.** A League, Tournament or analytics
  service is added by the ANG-06 item that first uses it, as its own `<resource>.service.ts`.
  `AnalyticsService` (ANG-06-9) reads team / player head-to-head, compare rounds and Team match
  history (`teamHistory(id, scopes)`: `null` sends no `scope`, `[]` sends `scope=`).
- **Auth:**
  - `AuthService` keeps the access token **in memory only**. It provides `login`, `logout`,
    `register` (which does not sign in), the `currentUser` / `isSignedIn` signals, a single-flight
    `refresh()`, and `restoreSession()`. ANG-06 item 7 added `changePassword` (204; the token and
    `currentUser` stay), `linkSelfInsert(playerId)` (the returned Account becomes `currentUser`;
    called by player detail's This is me, ANG-06-8) and `unlinkSelfInsert()` (clears `self_insert` on
    `currentUser` only after the 204).
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
  that screen's GETs before `verify()` (`teams/testing/teams-loads.ts`), right after the
  navigation and before any assertion: a failed assertion then can't leave them open too
  (ANG-06-11).
- **Timer-driven code** (a play loop, a timed flash): fake only the timer you drive
  (`vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })`), and only after the first
  render, so zoneless `whenStable` keeps its real `setTimeout` / rAF. Call `vi.useRealTimers()` in
  `afterEach`. Example: `matches/playback/playback.spec.ts`.
- **Live check without the owner's ports:**
  - Run Django with `CORS_ALLOWED_ORIGINS=http://localhost:4201 <python> <worktree>/laserforce_simulator/manage.py runserver 8001`.
    The absolute `manage.py` path lets `scripts/stop_worktree_servers.ps1` find the server: the
    main checkout's `.venv` Python with a relative path carries no worktree path (ANG-06-10).
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

## Served from Docker (ANG-07)

[ADR-0046](../docs/adr/0046-spa-at-app-prefix-behind-nginx-front-door.md): until ANG-08 the Docker
stacks put nginx in front of Django. It serves the SPA at `/app/` and proxies everything else
(`/api/`, the Django pages, `/admin/`, `/static/`) to `${DJANGO_UPSTREAM}`, so the SPA is
same-origin with the API and needs no CORS.

- `frontend/Dockerfile` builds with `--base-href /app/` on the command line. `src/index.html` keeps
  `<base href="/">`, and `angular.json` is untouched, so `ng serve` and the CI build stay at `/`.
  Never write an absolute asset or link path (`src="/..."`): use the Router or a relative path,
  or it breaks under `/app/`.
- `frontend/nginx/default.conf.template` is rendered by the nginx image's envsubst. It sets
  `no-cache` on `index.html` and `immutable` on hashed `js` / `css` / fonts, falls back to
  `/app/index.html` for deep links, and allows 20 MB bodies (map uploads). The proxy forwards
  `Host $http_host` and the incoming `X-Forwarded-Proto`, and resolves the upstream per request.
- `scripts/smoke_nginx.sh` is its test (CI `docker` job; running it locally:
  [`docs/agents/ci.md`](../docs/agents/ci.md)). `docker compose up` serves it on
  <http://localhost:8080/app/>.

## Windows notes

- **Write source files with the editor tools (Write / Edit), never PowerShell `Set-Content` /
  `Out-File`.** PowerShell 5.1 adds a UTF-8 BOM. `scripts/check_encoding.py` (CI and the
  pre-commit hook) scans `.ts` and `.scss` as well as `.html`, `.json`, `.css` and `.js`, and
  fails on a BOM.
- `core.autocrlf` is on, so a Windows checkout has CRLF endings. `.prettierrc` sets
  `endOfLine: auto` so `format:check` passes on both Windows and the Linux CI runner.
- `node_modules/`, `dist/` and `.angular/cache/` are ignored by `frontend/.gitignore`; never
  stage them. The root `.dockerignore` excludes the whole of `frontend/` from the Django image;
  `frontend/.dockerignore` keeps the same three out of the nginx image's build context.
