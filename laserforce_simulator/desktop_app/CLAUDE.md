# desktop_app/

The **Desktop App** (GLOSSARY.md; [ADR-0048](../../docs/adr/0048-desktop-app-go-pyinstaller-in-process-jobs.md)),
added by **DESK-01** (spec #486). A plain package, **not** an `INSTALLED_APP`: no models, no
migrations. It is named `desktop_app` so it never collides with the repo-root `desktop/` folder,
which holds the entry point `desktop/main.py` and the DESK-02 build files (`laserforce.spec`,
`build.ps1`, `smoke.py`; see **Build** below).

Run it unfrozen with the main checkout's `.venv` Python:
`python desktop/main.py [--no-browser | --browser] [--selftest-window]` (DESK-03: the App window
needs `pywebview`, from `requirements-desktop.txt`; without it the browser opens).
`LF_DESKTOP_HOME` overrides the data folder (default `%LOCALAPPDATA%\LaserforceSimulator`). The SPA
comes from `frontend/dist/frontend/browser` (run `npm --prefix frontend run build` first); without
it `/` is a 404 but the API works.

## Modules

| Module | What |
|---|---|
| `launcher.py` | `main()`: `quiet_std_streams()` (a `None` `sys.stdout` / `sys.stderr` → `os.devnull`) → `data_dir()`, `logs/` made → `acquire_instance_lock(data)` into `_instance_lock` (on `None`: headless prints `LF_ALREADY_RUNNING`, else `focus_window(WINDOW_TITLE)` or the box `Laserforce Simulator is already running.`; exit 0) → inside one `try`: `prepare_environment(data, spa)` (writes `secret_key` once; sets `LF_DESKTOP_HOME`, `LF_DESKTOP_SPA`, DESK-04's `LF_DESKTOP_MAPS` (frozen `<_MEIPASS>/default_maps`, else the repo's `Screenshots_and_video_examples`) and `LF_DESKTOP_BUILD_INFO` (frozen `<_MEIPASS>/build_info.json`, else removed), `SECRET_KEY`, `DATABASE_URL` = SQLite in the data folder, `DJANGO_SETTINGS_MODULE=desktop_app.settings`, overwriting inherited values) → `django.setup()` → `migrate_or_reset(data, migrate)` → `set_notice(...)` → `local_account()` → `start_job_worker()` → waitress on `127.0.0.1`, port 0 (read back as `effective_port`) → prints `LF_READY <seconds> <launch url>` → `choose_shell(sys.argv, window_available)`: `window` runs waitress on a daemon thread and `run_window(url, selftest=...)` on the main thread (`main` returns when it closes; if it raises, the error is logged and the browser opens on the same waitress thread, or under `--selftest-window` it prints `LF_WINDOW_FAIL <type>: <message>` and returns), `browser` opens the browser then `server.run()`, `none` only `server.run()`. Any exception in the `try`: `report_startup_failure(data, headless=...)` (appends a timestamp line + traceback to `logs/desktop.log`; headless prints it to stderr, else the box `Laserforce Simulator could not start. Details: <log path>`), exit 1. Flags: `--no-browser` (headless, wins), `--browser`, `--selftest-window`. |
| `shell.py` | DESK-03, the **App window** (GLOSSARY). `WINDOW_TITLE = "Laserforce Simulator"`. `choose_shell(argv, window_ok) -> "window" \| "browser" \| "none"` (`--no-browser` → `none`, `--browser` → `browser`, else `window` iff `window_ok()`; `window_ok` is asked only then, and raising → `browser`, logged). `window_available()`: `webview` and `webview.platforms.winforms` import **and** `is_chromium`. `run_window(url, *, selftest=False)`: `ALLOW_DOWNLOADS = True`, `create_window(WINDOW_TITLE, url, width=1280, height=800)`, blocks in `webview.start`. BUG-WV-01: while it runs, a `_StartWatch` handler sits on the `pywebview` logger, and `_watch_start` (the `start` function) waits for `window.events.loaded`. A `pywebview` ERROR starting `INIT_FAILED_PREFIX` (`WebView2 initialization failed`), or no load within `WINDOW_START_TIMEOUT_S` (20 s), closes the window and makes `run_window` raise `WindowStartFailed(reason)`, so the launcher falls back to the browser. The watch returns at once if the person closes the window first. The self-test (only after a load) polls `document.body.innerText` every `SELFTEST_POLL_S` (0.5 s) for `SELFTEST_LIMIT_S` (60 s) for `LANDING_TEXT` (`Sandbox`), prints `LF_WINDOW_OK <s>` (from `PROCESS_START`, this module's import) or `LF_WINDOW_FAIL <first 200 chars>`, and always destroys the window. `webview` is imported inside the functions only. |
| `instance.py` | DESK-03. `acquire_instance_lock(data) -> IO[bytes] \| None`: `<data>/instance.lock` (`a+b`), non-blocking exclusive lock (`msvcrt.locking(..., LK_NBLCK, 1)` on Windows, `fcntl.flock(LOCK_EX \| LOCK_NB)` elsewhere); `None`, with the file closed, when another process holds it. `focus_window(title) -> bool` (`FindWindowW` → `ShowWindow(SW_RESTORE)` → `SetForegroundWindow`; `False` off Windows). `message_box(text)` (`MessageBoxW` titled `WINDOW_TITLE`; stderr off Windows). |
| `launch_code.py` | `mint_launch_code(user) -> str`, `spend_launch_code(code) -> int \| None` (in-process dict under a lock; single use, no expiry), and `DesktopLoginView` at `api/accounts/desktop-login/?code=` (`api_desktop_login`). |
| `notice.py` | `set_notice(text)` / `current_notice()`: the reset notice for the life of the process, read by `accounts/me/` as `desktop_notice`. The only module the Hosted deploy imports. |
| `settings.py` | Star-imports the Hosted settings, then: `DESKTOP_APP = True`, `DEBUG = False`, localhost `ALLOWED_HOSTS`, empty CORS / CSRF origins, `REFRESH_COOKIE_SECURE = False`, `ROOT_URLCONF = "desktop_app.urls"`, `MEDIA_ROOT` in the data folder + `SERVE_MEDIA`, `SPA_ROOT` / `WHITENOISE_ROOT` from `LF_DESKTOP_SPA`, Celery on `memory://` / `cache+memory://` (not eager), a rotating file log at `<home>/logs/desktop.log`. DESK-04: `DESKTOP_BUILD_INFO` from `LF_DESKTOP_BUILD_INFO` (unset: `None`), `DEFAULT_MAPS_DIR` from `LF_DESKTOP_MAPS`, `DESKTOP_UPDATE_TIMEOUT = 5`, `DESKTOP_RELEASES_REPO`. |
| `build_info.py` | DESK-04, the **build record**. `RELEASE_TAG` (`^v(\d+)\.(\d+)\.(\d+)$`, the one plain-tag rule; `desktop/write_build_info.py` re-exports it). `load_build_info() -> BuildInfo` reads `settings.DESKTOP_BUILD_INFO`; a missing path or file is the dev record, and so is an unusable one (not JSON, not an object, a `release` with no version), with one warning: `accounts/me/` reads it on every sign-in. `BuildInfo.label`: `v0.1.0`, `latest abc1234`, `dev abc1234` or `dev`. `accounts/me/` shows it as `desktop_version` (`None` on Hosted). Stdlib-only at module level. |
| `update_check.py` | DESK-04, the **Update check**. `fetch_json(url, timeout)` (the only function tests replace), `check_for_update(info)`: `release` asks `releases/latest` (a newer `RELEASE_TAG` tag), `latest` asks `git/ref/tags/desktop-latest` (a different sha), `dev` asks nothing. Any failure is `None` plus one warning in the desktop log. `cached_update()` runs it once per process under a lock (`reset_update_cache()` for tests). `DesktopUpdateView` at `api/accounts/desktop-update/` (`api_desktop_update`): `{"data": {version, url} \| null}`. |
| `urls.py` | The launch-code route and the update route, then the Hosted `urlpatterns`. Nothing else. |

## Rules that are easy to break

- **Launch code view.** Refuses any `REMOTE_ADDR` but `127.0.0.1` / `::1` **before** spending the
  code (a remote request never burns one); a missing / empty / wrong / spent code is the same plain
  403 `This launch link has been used. Restart the app.` (built by hand on purpose: the browser
  opens the URL, so it is not enveloped). Success is a 302 to the fixed `/` (no `next`) with the
  ordinary ANG-03 refresh cookie (`accounts.token_api_views.set_refresh_cookie`). It is the one
  extra entry in the login-exemption guard's desktop allowlist
  (`accounts/tests/test_login_exempt_surfaces.py`).
- **The job worker must stay referenced** (`launcher._worker`): once its context manager is
  garbage-collected, the worker thread stops and jobs sit at PENDING (ADR-0048). The smoke test
  catches it.
- **`migrate_or_reset`** moves an unupgradable `db.sqlite3` to `db.sqlite3.<YYYYmmdd-HHMMSS>.bak`
  (WAL / SHM deleted) and migrates a fresh one; a missing or empty database that fails re-raises.
  The move happens **after** the `except` block plus a `gc.collect()`: a `connect()` that failed
  half way (a file that is not a database) keeps its sqlite3 handle alive in the traceback, and
  Windows refuses to move an open file (found in the DESK-01 browser check). Never rewrite
  migration history (ADR-0004).
- **The instance lock must stay referenced** (`launcher._instance_lock`), like `_worker`: once the
  file is closed or garbage-collected, the OS releases the lock and a second launch starts a second
  server on the same database. It is taken **before** `migrate`, so two launches never migrate at
  once. The OS frees it when the process dies (crash included); nothing reads the file.
- **No WebView2 means MSHTML, silently.** pywebview falls back to Internet Explorer, which cannot run
  the SPA, without raising. `window_available()` therefore checks
  `webview.platforms.winforms.is_chromium`, not just the import; `False` opens the browser.
- **WebView2 failing to start does not raise either** (BUG-WV-01). pywebview logs one ERROR at once
  (`edgechromium.on_webview_ready`), or hangs with no `loaded` until the window is destroyed (CI's
  `E_ABORT`). `run_window`'s start watch catches both. `test_shell.py` pins the log wording against
  the installed pywebview: an upgrade that rewords it turns CI red. The watch runs on pywebview's
  **non-daemon** thread, so it must end when the window closes, or the process (and its instance
  lock) outlives the window.
- **`--no-browser` never shows UI**: no App window, browser or message box. A second launch prints
  `LF_ALREADY_RUNNING`, a startup failure prints its traceback to stderr. The smoke test and CI
  depend on it (a box would hang a headless runner).
- **`quiet_std_streams()` runs first.** A `--noconsole` build started with no pipe (a double-click)
  has `sys.stdout` / `sys.stderr` = `None`: `print` and `logging` are silent, but Django's
  `OutputWrapper` and Celery call `.write` and would raise. It replaces only `None` streams, so a
  piped `LF_READY` still reaches the smoke.
- **The App window owns the main thread.** In window mode waitress runs on a daemon thread and
  `main` returns when `run_window` does; the Celery worker thread is a daemon too, so closing the
  window quits.
- **The Local Account** is `local@desktop.invalid`, unusable password, not staff.
- **SPA:** `accounts/me/`'s `desktop` hides Sign out and Change password, `hostedOnlyGuard` sends
  `/account/password` home, the Shell hides the Account button while no Self-insert is linked, and
  `desktop_notice` shows as a dismissible alert (`frontend/CLAUDE.md`).

## Build (DESK-02)

The **Desktop build** (GLOSSARY) is made by CI's `desktop` job on `windows-latest` and published by
`desktop-publish` (`docs/agents/ci.md`): the rolling `desktop-latest` pre-release on every push to
`main`, a numbered Release on a `v*` tag.

- **`desktop/build.ps1 -Python <exe> -Out <dir>`** is the one build entry, CI and local. It needs
  a **python.org** CPython (conda bundles the wrong DLLs: ADR-0048) with
  `laserforce_simulator/requirements-desktop.txt` (DESK-03: the runtime file plus pywebview) plus
  `pyinstaller`, and `npm --prefix frontend ci` already run. It sets `PYTHONPATH` / `DJANGO_SETTINGS_MODULE` / `SECRET_KEY` (PyInstaller collects
  in a subprocess that ignores the spec's `pathex`), builds the SPA, runs PyInstaller and writes
  `<Out>/LaserforceSimulator-windows-x64.zip`. DESK-04: first it runs `desktop/write_build_info.py
  <Out>/build_info.json` and names that file in `LF_BUILD_INFO` for the spec. The record comes from
  CI's `GITHUB_REF` / `GITHUB_SHA`: a plain `vX.Y.Z` tag is `release`, `main` is `latest`, anything
  else (a PR, an `-rc` tag, a local build) is `dev`, which makes no Update check.
- **Local build:** the owner's machine has python.org 3.12 at
  `%LOCALAPPDATA%\Programs\Python\Python312\python.exe`. Make a scratchpad venv from it, install
  `requirements-desktop.txt` plus `pyinstaller==6.22.3` (CI's pin), run `build.ps1 -Python <venv
  python> -Out <scratchpad dir>`, then `python desktop/smoke.py <Out>\dist\LaserforceSimulator\LaserforceSimulator.exe
  --window` (DESK-03 retro: 118.2 MB zip, App window ready in 5.05 s).
- **`desktop/laserforce.spec`** is tracked through a `!desktop/laserforce.spec` exception to
  `.gitignore`'s `*.spec`. A new Django app or a string-loaded package goes in its hidden-import
  list, or the frozen build fails at import. It bundles the SPA at `spa` (`spa_dir()`), and
  simplejwt's `token_blacklist` migration sources. It excludes the Hosted-only drivers. DESK-03:
  the `EXE` has **`console=False`** (no console window; a piped child's `print` still reaches the
  pipe, and `quiet_std_streams()` covers a double-click). pywebview / pythonnet come in through
  pyinstaller-hooks-contrib's `webview`, `clr_loader` and `pythonnet` hooks; `webview` joins the
  `collect_submodules` list only if the frozen self-test fails on an import (the spec comment says
  which). DESK-04: it bundles the two `_DEFAULT_MAPS` images (only those, named in the spec) at
  `default_maps`, and `LF_BUILD_INFO`'s file at the root as `build_info.json`; without
  `LF_BUILD_INFO` it stops with a `KeyError` (PyInstaller run without `build.ps1`).
  `test_build_spec.py` runs the spec with stand-ins for PyInstaller and checks both.
- **`desktop/smoke.py`** (stdlib only) holds `run_smoke(command, home, *, check_spa=True) ->
  Timings`. `Timings` is a `NamedTuple` of `launcher_ready_s` and `cold_start_s`. The in-suite test
  runs `main([<python>, desktop/main.py, --summary <file>], check_spa=...)` and passes `check_spa`
  only when `frontend/dist` is built (CI's Python jobs never build it); the CLI as CI runs it always
  checks. It
  raises `SmokeFailure(step, detail)` at the first failed step (`ready`, `spa`, `launch_code`,
  `refresh`, `me`, `update`, `default_maps`, `teams`, `batch`, `report`, `map`, `instance`).
  DESK-04: `me` also needs a non-null `desktop_version`; `update` expects
  `GET /api/accounts/desktop-update/` to be `null` or `{version, url}` (`check_update_reply`, shape
  only: a `latest` build on `main` finds the `desktop-latest` it is about to replace, and requiring
  `null` turned `main`'s desktop job red after DESK-04);
  `default_maps` expects `GET /api/maps/` to list exactly the two default maps, and runs before
  `map` because the defaults are seeded only while no map exists. DESK-03's last step,
  **`instance`** (`_second_launch`), runs the same command with the same env (same data folder)
  while the first still runs: exit 0 and `LF_ALREADY_RUNNING` within `INSTANCE_TIMEOUT_S` (30 s),
  then `GET /` still answers (the `spa` step's rules). In the suite it takes the real lock (`fcntl`
  on Linux, `msvcrt` on Windows); in CI the frozen one. Its CLI is `python desktop/smoke.py <exe>
  [<args>...] [--window] [--zip <zip>] [--summary <md>]`, which exits 0/1. **`--window`**: after a
  passing smoke, `run_window_selftest(<exe> <args> --selftest-window, <fresh temp folder>) ->
  float` returns the `LF_WINDOW_OK` seconds and raises `SmokeFailure("window", ...)` on
  `LF_WINDOW_FAIL`, a non-zero exit or `WINDOW_TIMEOUT_S` (90 s); the summary then gains `| App
  window ready | <s> s |` (`summary_markdown(..., window_s=None)`). BUG-WV-01: a self-test that
  fails with `WINDOW_START_FAILED` (`LF_WINDOW_FAIL WindowStartFailed`, pinned to the exception's
  name by a test) is run **once** more on a fresh folder (`_window_with_retry`; the row then says
  `(after 1 retry)`), since CI's runner sometimes hangs starting WebView2; any other failure, or a
  second one, stays red. Then **`window_failure`** (`run_window_failure_selftest(command, home) ->
  float`) runs the self-test with `WEBVIEW2_USER_DATA_FOLDER` under a regular file, so WebView2
  cannot start for any user, admin included. It expects exit 0 and `LF_WINDOW_FAIL WindowStartFailed`
  within `WINDOW_FAILURE_TIMEOUT_S` (40 s), and adds `| App window failure fallback | <s> s |`. CI's `desktop` job passes
  `--window` on `windows-latest` (the real WebView2 window); never skip it or mark it
  `continue-on-error`. The suite never runs `--window` on the real launcher (it would open a
  window, or the browser where pywebview is missing). A new bundled feature worth guarding gets a
  step here, so both the frozen and the unfrozen smoke run it.
- **Releases (DESK-04):** a plain `vX.Y.Z` tag publishes a numbered **Release** (the public
  download at `releases/latest`); any other `v*` tag (`v0.2.0-rc1`) a pre-release. Both use
  `desktop/RELEASE_NOTES.md` (the download page: install, SmartScreen, data folder, upgrade,
  uninstall) plus GitHub's generated changelog. Edit that file in a PR; it has no placeholders. The
  tag rule in `ci.yml` and `RELEASE_TAG` are twins: change both together. See `docs/agents/ci.md`.
- **Requirements:** `requirements-runtime.txt` is what the app imports at runtime in either
  deploy (the Dockerfile installs only it). DESK-03: `requirements-desktop.txt` is `-r` of it plus
  the exact `pywebview==6.2.1` pin (the Desktop build's file); `requirements.txt` is `-r
  requirements-desktop.txt` plus the dev / test pins, so dev and CI's Linux jobs get pywebview
  (without pythonnet, which is `win32`-only). A new runtime dependency goes in the runtime file; a
  Desktop-only one in the desktop file.

## Tests

`desktop_app/tests/`: `test_launch_code.py` (spend rules and every hostile input against the
desktop URLconf), `test_launcher.py` (`tmp_path` + a fake `migrate`: reset, re-raise, corrupt-file
regression, environment, secret key, Local Account), `test_settings.py` (the desktop settings
module imported fresh), `test_launcher_smoke.py` (loads `desktop/smoke.py` and runs its CLI on
`desktop/main.py` against a temp data folder, one launch of about 8 s plus the `instance` step's
second launch; plus the early-exit `SmokeFailure`, the CLI's exit 1, the summary table, DESK-03's
`_second_launch` against a stand-in local server, and `run_window_selftest` / `--window` /
BUG-WV-01's retry and `window_failure` against fake commands), `test_requirements.py` (the dev → desktop
→ runtime chain, the pywebview pin). DESK-03: `test_shell.py` (`choose_shell`, and a fake `webview`
package in `sys.modules` for `window_available` / `run_window`, whose `start` loads, hangs or logs
the init ERROR: CI's Python jobs run on Linux),
`test_instance.py` (the lock against a real second process and a crashed holder; `fcntl` on Linux,
`msvcrt` on Windows). DESK-04: `test_build_info.py` (`build_info(env, today)` per `GITHUB_REF`, the
CLI, the labels, the shared `RELEASE_TAG`), `test_update_check.py` (the endpoint with a fake
`fetch_json` per channel and failure, the cache, Hosted 404, 401), `test_build_spec.py` (the
spec's default maps and build record). The default maps through `GET /api/maps/` are in
`core/test_map_services.py` (`TestDefaultMapsDir`). And `test_launcher.py`'s `main()` tests (second launch, startup failure, a
real failure through `desktop/main.py`, and the shell dispatch through `_open_shell`).
