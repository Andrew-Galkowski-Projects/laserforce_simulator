# desktop_app/

The **Desktop App** (GLOSSARY.md; [ADR-0048](../../docs/adr/0048-desktop-app-go-pyinstaller-in-process-jobs.md)),
added by **DESK-01** (spec #486). A plain package, **not** an `INSTALLED_APP`: no models, no
migrations. It is named `desktop_app` so it never collides with the repo-root `desktop/` folder,
which holds the entry point `desktop/main.py` and the DESK-02 build files (`laserforce.spec`,
`build.ps1`, `smoke.py`; see **Build** below).

Run it unfrozen with the main checkout's `.venv` Python: `python desktop/main.py [--no-browser]`.
`LF_DESKTOP_HOME` overrides the data folder (default `%LOCALAPPDATA%\LaserforceSimulator`). The SPA
comes from `frontend/dist/frontend/browser` (run `npm --prefix frontend run build` first); without
it `/` is a 404 but the API works.

## Modules

| Module | What |
|---|---|
| `launcher.py` | `main()`: `data_dir()` → `prepare_environment(data, spa)` (writes `secret_key` once; sets `LF_DESKTOP_HOME`, `LF_DESKTOP_SPA`, `SECRET_KEY`, `DATABASE_URL` = SQLite in the data folder, `DJANGO_SETTINGS_MODULE=desktop_app.settings`, overwriting inherited values) → `django.setup()` → `migrate_or_reset(data, migrate)` → `set_notice(...)` → `local_account()` → `start_job_worker()` → waitress on `127.0.0.1`, port 0 (read back as `effective_port`). Prints `LF_READY <seconds> <launch url>`, opens the browser unless `--no-browser`. |
| `launch_code.py` | `mint_launch_code(user) -> str`, `spend_launch_code(code) -> int \| None` (in-process dict under a lock; single use, no expiry), and `DesktopLoginView` at `api/accounts/desktop-login/?code=` (`api_desktop_login`). |
| `notice.py` | `set_notice(text)` / `current_notice()`: the reset notice for the life of the process, read by `accounts/me/` as `desktop_notice`. The only module the Hosted deploy imports. |
| `settings.py` | Star-imports the Hosted settings, then: `DESKTOP_APP = True`, `DEBUG = False`, localhost `ALLOWED_HOSTS`, empty CORS / CSRF origins, `REFRESH_COOKIE_SECURE = False`, `ROOT_URLCONF = "desktop_app.urls"`, `MEDIA_ROOT` in the data folder + `SERVE_MEDIA`, `SPA_ROOT` / `WHITENOISE_ROOT` from `LF_DESKTOP_SPA`, Celery on `memory://` / `cache+memory://` (not eager), a rotating file log at `<home>/logs/desktop.log`. |
| `urls.py` | The launch-code route, then the Hosted `urlpatterns`. Nothing else. |

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
  `laserforce_simulator/requirements-runtime.txt` plus `pyinstaller`, and `npm --prefix frontend ci`
  already run. It sets `PYTHONPATH` / `DJANGO_SETTINGS_MODULE` / `SECRET_KEY` (PyInstaller collects
  in a subprocess that ignores the spec's `pathex`), builds the SPA, runs PyInstaller and writes
  `<Out>/LaserforceSimulator-windows-x64.zip`.
- **`desktop/laserforce.spec`** is tracked through a `!desktop/laserforce.spec` exception to
  `.gitignore`'s `*.spec`. A new Django app or a string-loaded package goes in its hidden-import
  list, or the frozen build fails at import. It bundles the SPA at `spa` (`spa_dir()`), and
  simplejwt's `token_blacklist` migration sources. It excludes the Hosted-only drivers.
- **`desktop/smoke.py`** (stdlib only) holds `run_smoke(command, home, *, check_spa=True) ->
  Timings`. `Timings` is a `NamedTuple` of `launcher_ready_s` and `cold_start_s`. The in-suite test
  runs `main([<python>, desktop/main.py, --summary <file>], check_spa=...)` and passes `check_spa`
  only when `frontend/dist` is built (CI's Python jobs never build it); the CLI as CI runs it always
  checks. It
  raises `SmokeFailure(step, detail)` at the first failed step (`ready`, `spa`, `launch_code`,
  `refresh`, `me`, `teams`, `batch`, `report`, `map`). Its CLI is `python desktop/smoke.py <exe>
  [<args>...] [--zip <zip>] [--summary <md>]`, which exits 0/1. A new bundled feature worth guarding gets a step
  here, so both the frozen and the unfrozen smoke run it.
- **Requirements:** `requirements-runtime.txt` is what the app imports at runtime in either
  deploy (the Dockerfile and the Desktop build install it); `requirements.txt` is `-r` of it plus
  the dev / test pins. A new runtime dependency goes in the runtime file.

## Tests

`desktop_app/tests/`: `test_launch_code.py` (spend rules and every hostile input against the
desktop URLconf), `test_launcher.py` (`tmp_path` + a fake `migrate`: reset, re-raise, corrupt-file
regression, environment, secret key, Local Account), `test_settings.py` (the desktop settings
module imported fresh), `test_launcher_smoke.py` (loads `desktop/smoke.py` and runs its CLI on
`desktop/main.py` against a temp data folder, one launch of about 8 s; plus the early-exit
`SmokeFailure`, the CLI's exit 1 and the summary table), `test_requirements.py` (the runtime / dev
split).
