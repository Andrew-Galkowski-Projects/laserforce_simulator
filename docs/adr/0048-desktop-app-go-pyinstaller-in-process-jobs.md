# The Desktop App is a go: PyInstaller, in-process jobs, SQLite, browser shell

**Status:** Accepted (ANG-09, 2026-11-01). Go with caveats. Adds Desktop-only exceptions to
[ADR-0025](0025-postgresql-canonical-sqlite-dev-only.md) (SQLite) and
[ADR-0047](0047-spa-at-root-django-api-and-admin-only.md) (who serves the SPA). Spec #477,
spike branch `spike/ang-09` (pushed, never merged).

## Context

ANG-09 asked whether the single-player modes (sandbox and single-player career League) can ship
as a **Desktop App**: one download that runs on one machine for one Account, with no
**Hosted** server, no Python or Node install, and no broker, Docker or Postgres. The spike built
a real Windows build from the current code and measured it against a go bar agreed before any
code was written (PLAN.md ANG-09, grill decision 5).

## Decision

**Go, with four caveats:** build on a python.org CPython, not conda (Consequences); OpenCV is
nearly half the build (map upload only); the first release is unsigned, so SmartScreen warns
(Distribution); and sight-line compute has an app bug that blocks new maps on either deploy
(Consequences). The Desktop App is built as:

1. **PyInstaller, one-folder**, from a runtime-only requirements list. The build bundles the
   Django API, the production Angular build and every native dependency (OpenCV, numpy,
   matplotlib, reportlab, Pillow).
2. **A launcher** as the frozen entry point. It calls `multiprocessing.freeze_support()` first,
   keeps its data in `%LOCALAPPDATA%\LaserforceSimulator\` (database, media, secret key, logs),
   runs `migrate` on every launch, creates the one local Account on first run, and serves through
   **waitress** on `127.0.0.1` at a free port. A console window stays open; closing it quits.
3. **Jobs run in-process**: a Celery worker thread consumes the existing `memory://` broker and
   writes results to the in-process cache backend. `.delay()`, `AsyncResult` and the job polling
   endpoints are unchanged, so the Season play loop and batch runs show live progress (eager mode
   would block the request for the whole job). The worker's context manager must stay referenced:
   once it is garbage-collected, its `finally` stops the thread and jobs sit at PENDING.
4. **Sign-in by launch code.** The launcher mints a single-use code and opens the default browser
   at a desktop sign-in URL. That view spends the code, sets the ordinary ANG-03 `refresh_token`
   cookie and redirects to `/`, where the SPA's `restoreSession` signs in through the normal JWT
   path. A reused code is a 403. No SPA auth change.
5. **One SPA build for both deploys.** `accounts/me/` carries `desktop: true` in the Desktop App,
   and the SPA hides the Account screens when it is set: Sign out (the account menu), Change
   password (`/account/password` and its menu entry), and the signed-out `/login` and `/register`
   pages, which become unreachable. Invites live in Django Admin, which the Desktop App does not
   link. The spike hides Sign out only; the rest is follow-up 1.
6. **The default browser is the shell** for the first release. A pywebview (WebView2) window
   worked unfrozen in the spike: the launcher printed `LF_READY 1.11` and the window's self-test
   `LF_WEBVIEW_OK 5.41` (seconds from process start), once the SPA showed the signed-in landing
   page. It is the follow-up (below).

### Desktop-only exceptions

- **SQLite (ADR-0025).** The Desktop App's database is SQLite, with ADR-0025's WAL and
  busy-timeout hardening. One person, one machine: the one writer plus the job worker thread is
  the dev-server case ADR-0025 already supports. Postgres stays canonical for the Hosted deploy.
- **Upgrades (ADR-0004).** If `migrate` fails on an existing database, the launcher moves it to
  `db.sqlite3.<timestamp>.bak`, starts a fresh one and says so. It never rewrites migration
  history. Both paths were tested: a v2 build with one extra migration (a real `ALTER TABLE`)
  upgraded a v1 data folder and kept its League, and the UX-01 `InconsistentMigrationHistory`
  case reset to `.bak` with a notice.
- **Django serves the SPA (ADR-0047).** There is no nginx: WhiteNoise serves the built SPA at `/`
  (`WHITENOISE_ROOT`, index file on), and a catch-all view answers deep links with `index.html`.
  Django still owns `/api/` and `/media/`. ANG-10 made this the method for every deploy
  ([ADR-0049](0049-django-serves-the-spa-in-every-deploy.md)).

## Measurements

Owner's machine, Windows 11, Python 3.11, 2026-11-01. "Unfrozen" is the same launcher and
settings run from source under waitress, standing in for the dev server (`runserver` was not
timed). Times come from `desktop/measure.py` and `desktop/upgrade_check.py` on the spike branch;
sizes from `desktop/build.ps1`. Cold start is timed to the first `GET /` that returns the SPA's
`index.html`; the browser check then showed the signed-in landing page.

| Go bar | Result | |
|---|---|---|
| Zipped one-folder build ~200 MB or less | **99.8 MB** zip (234 MB unpacked) | pass |
| Cold start to a usable screen ~15 s or less | **4.2-5.3 s** first launch, which includes `migrate` on an empty database (4.0 s in the default `%LOCALAPPDATA%` folder); **0.94 s** relaunch; **3.2 s** first launch of an upgrade that applies a migration | pass |
| Single Round no slower than 1.5x | frozen 0.33-0.48 s vs unfrozen 0.28-0.37 s (~1.2x). A Single Round takes no seed, so these are three unseeded Rounds each. Seeded 50-Round batch (`master_seed` 12345): **4.2 s vs 4.3 s** | pass |
| No broker, Docker or Postgres | in-process worker thread + SQLite | pass |
| An upgrade keeps or deliberately resets data without a crash | a v2 build with a new migration kept the League; broken history went to `.bak` with a notice | pass |

Smoke checks in the frozen build:

- **Season play loop:** `play-week` (synchronous) 0.6-0.8 s. `play-two-months` queued on the worker
  thread and finished the Season in 3.9-4.7 s, with progress `1/10` ... `10/10` visible while it ran.
- **Batch of 50:** the process pool starts under `freeze_support` and finishes with progress.
- **Round report PDF:** 200, ~300 KB.
- **Map upload:** upload and OpenCV zone detection work. Sight-line compute returned a 500 with
  both sample maps (Syracuse unfrozen, San Marcos frozen); the Desktop App log has the same
  `KeyError: 0` traceback both times. It is an app bug, not a packaging one (see Consequences),
  so the full-fidelity Round on an uploaded map stays untested.
- **Browser:** opens signed in with no sign-in screen; Sign out hidden; a deep link and a reload
  both land signed in.

Largest bundle parts: OpenCV (`cv2`) 111 MB, Django 22 MB, numpy 26 MB, matplotlib 14 MB,
Pillow 13 MB, the SPA 2 MB. OpenCV only serves map upload and zone detection.

## Considered options

- **Packaging.** *PyInstaller* (chosen): it handled Django once the dynamic imports were listed.
  *Nuitka* (not tried): it compiles to C, which usually means a smaller build and faster start at
  the cost of much longer builds, and Django's string imports would need the same hand-listing.
  Kept as the fallback; PyInstaller hit no blocker. *Briefcase* (paper only, as agreed): builds an
  MSI installer, but it is made for BeeWare apps and documents no Django path, so it would need
  more glue for no gain.
- **Jobs.** *In-process Celery worker thread* (chosen). *Eager mode*: works with no code, but
  blocks the request for a whole Season and shows no progress. *A custom thread-pool dispatcher*:
  replaces Celery calls on both deploys for no gain.
- **Shell.** *Default browser* (chosen first). *pywebview*: a native WebView2 window, ~4 s from
  server ready to a signed-in screen in the spike; the follow-up. *Tauri*: a Rust toolchain plus a
  Python sidecar. *Electron*: adds ~100 MB of Chromium on top of the Python runtime. Both rejected.
- **Sign-in.** *Launch code* (chosen). *A middleware that signs every request in as the local
  Account*: simpler, but any local process could then drive the API.
- **Web server.** *waitress* (chosen; pure Python, runs on Windows). gunicorn does not run on
  Windows; `runserver` is not for serving.

## Distribution

- **Windows first.** The Desktop App ships as a zip for a manual download (a GitHub Release
  asset). There is no auto-updater in the first release; a later "a new version is available"
  check against the Releases API is enough, because ADR-0004 lets an upgrade reset the data.
- **Signing.** An unsigned download gets the SmartScreen "Windows protected your PC" warning until
  it builds up reputation. Options: ship unsigned with a "More info, then Run anyway" note (first
  release); buy an OV or EV code-signing certificate, a yearly cost (our understanding, not checked
  in this spike: Microsoft no longer gives EV certificates instant SmartScreen reputation, so
  neither skips the warm-up); or Azure Trusted Signing, a monthly subscription that signs from CI.
  None of these prices or eligibility rules were checked here; check them when buying. The
  recommendation is unsigned plus the note first, then Trusted Signing.
- **macOS / Linux.** PyInstaller does not cross-build, so each OS needs its own build machine (a CI
  runner). macOS needs an Apple Developer membership to sign and notarise, or Gatekeeper blocks
  the app. Linux needs no signing (a tarball or AppImage). Both are feasible and both are later.

## Consequences

- **Build on a python.org CPython, not conda.** The spike's build Python was conda-based, and
  PyInstaller bundled same-named DLLs found on PATH instead of conda's (`_ssl` and `pyexpat`
  failed to load). The spike's spec swaps them back; a CI build on python.org CPython 3.12 needs no
  swap.
- **Three bundling rules learned:** list the project packages and Django's string-loaded modules
  as hidden imports, with `PYTHONPATH` and `DJANGO_SETTINGS_MODULE` set in the build environment
  (PyInstaller collects in a subprocess that ignores the spec's `pathex`); ship simplejwt's
  `token_blacklist` migration sources (`0010` resolves its own `__file__`); and track the `.spec`
  file, which the stock `.gitignore` entry `*.spec` hides.
- **No default maps in the Desktop App.** `ensure_default_maps` seeds from
  `Screenshots_and_video_examples/`, which is not bundled. A follow-up decides whether to ship them.
- **App bugs found, not Desktop-specific:** sight-line compute after a saved zone config raises
  `KeyError: 0` (`core/map_processing.py` `compute_sight_lines` falls back to the `blocked_edges`
  dict, and `_has_los` indexes it as a grid); `matches/simulation/entrypoints.py` calls
  `logging.basicConfig(level=DEBUG)` at import.
- **The Hosted deploy is unchanged** until a follow-up task merges Desktop App code.

## Follow-up tasks

1. **Desktop App code in `main`, with tests:** the desktop settings and URLconf, the launch-code
   sign-in (single use, `127.0.0.1` only), `desktop` on `accounts/me/`, the SPA hiding Sign out,
   Change password, Register and the invite screens, and an in-app notice for a reset database.
2. **Desktop App build in CI:** a Windows runner on python.org CPython 3.12, a runtime / dev
   requirements split, PyInstaller, and the zip as a Release asset on a tag.
3. **Native window:** pywebview frozen, a tray icon or no-console build, and a single-instance lock.
4. **Distribution:** the download page, signing, an update check, and the decision on default maps.
5. **Bugs:** the sight-line `KeyError` and the import-time DEBUG `basicConfig`.
