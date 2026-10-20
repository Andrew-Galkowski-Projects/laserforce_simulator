# frontend/ -- the Angular SPA (ANG-04)

The Phase 8 replacement for the Django templates. The stack is fixed by
[ADR-0042](../docs/adr/0042-angular-frontend-stack.md). Right now it is only an app shell: a
Material toolbar titled "Laserforce Simulator", a placeholder home page, and a wildcard route that
redirects to it. It has no API calls yet (those start in ANG-05).

## Stack

- **Angular 22** (22.2.x), scaffolded with `npx -p @angular/cli@22 ng new`. The CLI is a
  `devDependency`: run it as `npx ng ...` or through the npm scripts, never a global install.
- **Standalone components, zoneless change detection, signals.** No NgModules, no `zone.js`.
- **No SSR.** `ng build` writes plain static files to `dist/frontend/browser/` (served by nginx in
  ANG-07).
- **Angular Material**, Material 3 theme (azure / blue palette) in `src/styles.scss`, with
  `color-scheme: light dark` so it follows the OS setting.
- **Vitest** through the `@angular/build:unit-test` builder (`ng test`), on jsdom.
- **ESLint** (`angular-eslint`, `eslint.config.js`) and **Prettier** (`.prettierrc`, print width
  100, single quotes, `endOfLine: auto`). Both are enforced in CI.
- **Node 24** (`.nvmrc`, `engines.node >=24`), npm only, `package-lock.json` committed.

## Commands

Run from the repo root with `--prefix` (no `cd` needed):

```bash
npm --prefix frontend ci                  # install exactly what package-lock.json says
npm --prefix frontend start               # ng serve on http://localhost:4200
npm --prefix frontend test                # Vitest; watches in a terminal, single run when CI is set
npm --prefix frontend run test:ci         # Vitest, one headless run (what CI uses)
npm --prefix frontend run lint            # ng lint
npm --prefix frontend run format:check    # prettier --check .
npm --prefix frontend run format          # prettier --write .
npm --prefix frontend run build           # production build (environment.prod.ts swapped in)
npm --prefix frontend exec -- ng generate component foo   # any other ng command
```

`ng add` / `ng generate` resolve paths against the project in `frontend/`, so
`npm --prefix frontend exec -- ng ...` works from the root.

CI's `frontend` job (`.github/workflows/ci.yml`) runs `npm ci`, `lint`, `format:check`,
`test:ci` and `build`, in parallel with the Python `test` job. The `docker` deploy job needs both.

## Layout

```
src/
  main.ts                 bootstrapApplication(App, appConfig)
  styles.scss             Material 3 theme + global styles
  environments/
    environment.ts        dev:  { production: false, apiBaseUrl: 'http://localhost:8000/api' }
    environment.prod.ts   prod: { production: true,  apiBaseUrl: '/api' }
  app/
    app.ts / app.html     the shell: <mat-toolbar> + <router-outlet>
    app.scss              toolbar primary colour via mat.toolbar-overrides (M3 ignores color="primary")
    app-title.ts          APP_TITLE, shared by the toolbar and the route titles
    app.config.ts         providers (router, global error listeners)
    app.routes.ts         '' -> Home, '**' -> redirect to ''
    home/                 placeholder Home page
```

Specs sit next to the file they cover (`*.spec.ts`). Test what you can see from outside -- the
rendered text, where the router lands -- not component internals. `app.routes.spec.ts` uses
`RouterTestingHarness` with the real `routes`.

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
