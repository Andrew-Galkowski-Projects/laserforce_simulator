# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Stack

- Primary language: Python
- Use type hints for all function signatures
- Run linting/formatting (black) after edits
- Validate YAML/HTML changes before committing

## Tooling

- Python: use the main checkout's `.venv\Scripts\python.exe`, also from a
  worktree (worktrees have no `.venv`). The `python` on PATH is the `.conda`
  env, which has no Django or pytest.
- Shell: PowerShell on Windows (use PowerShell-compatible commands, not bash-isms like `&&`)
- Path separators: prefer forward slashes or `os.path.join` in Python
- **Never prepend `cd` to any command — git especially.** The shell already
  starts at the repo root (`...\laserforce_simulator`), which is where `.git`
  lives. A `cd` in a compound command can trigger a permission prompt, clutters
  output, and — because the agent worktrees share this `.git` — a `cd`-then-git
  sequence has already corrupted the working tree once (a stray `git stash pop`
  reverted in-progress edits). Run `git` **directly, with no `cd` and no `-C`**:
  `git status`, `git diff`, `git commit …` all just work from the cwd.
- For commands that must run from a subdirectory, pass the path to the tool
  instead of `cd`-ing:
  - black: `python -m black laserforce_simulator` (target path as argument).
  - pytest / manage.py: the Django project and `pytest.ini` live in the nested
    `laserforce_simulator/laserforce_simulator/`. Invoke via the path —
    `python laserforce_simulator/manage.py <cmd>` — or, when pytest config
    discovery requires it, run the test tool from that nested dir as a last
    resort (never `cd` before `git`).

## Windows Shell Conventions

- Avoid Unicode box-drawing or non-ASCII characters in scripts and console
  output; the Windows cp1252 console will crash. Use ASCII equivalents
  (`+--+`, `|`, `+--+`) instead of `┌─┐│└─┘`.
- PowerShell 5.1 strips inner quotes from native-command arguments and adds a
  BOM to text it pipes or `Set-Content`s. So: run a script through the **Bash
  tool** with `python laserforce_simulator/manage.py shell -c "exec(open(r'<path>').read())"`.
  Commit with `git commit -F <message-file>`, and edit source files with Write / Edit.
  `shell < tmp.py` runs only one statement at a time on Windows, because Django skips
  piped stdin on win32. Any function or `try` block in the script breaks, and the earlier
  statements still run.
- **Do not push large content through a shell heredoc.** Past roughly a few tens
  of KB the call fails with `ENAMETOOLONG: name too long, uv_spawn`, because the
  whole command arrives as one process argument. This bites when adding a long
  CLAUDE.md / PLAN.md section. Insert the block with the Edit tool instead,
  anchored on the heading that follows it (old = that heading, new = the block
  plus the heading): Edit has no size limit, and a worktree session's guard
  refuses the shell splice anyway. Line endings need no care: `core.autocrlf`
  is on and the index stores LF, so a CRLF/LF flip never reaches a commit.

## Browser Sessions (Chrome DevTools MCP)

- **Leave no stray tabs behind.** Chrome starts with one tab already open, and
  `close_page` refuses to close the last open page, so every extra `new_page`
  tends to leave a tab behind after the session ends.
- **Start with `list_pages` and reuse the existing tab** — drive it with
  `navigate_page` / `select_page`. Only call `new_page` when you genuinely need
  two pages open at once (e.g. a cross-Account isolation check).
- **File paths go under the session scratchpad.** `take_screenshot`'s
  `filePath` and `upload_file`'s `filePaths` are refused anywhere else
  (worktrees, Downloads).
- **Close every page you opened with `new_page`** via `close_page` as soon as you
  are done with it, and again before you report results (also when the pass
  fails or is cut short). Finish with `list_pages` to confirm only the original
  tab remains.
- **Close the browser when the pass is done:**
  `powershell -NoProfile -File scripts/close_mcp_browser.ps1`, through the
  PowerShell tool (a worktree session's Bash guard refuses it). The MCP keeps
  its Chrome open until the session ends, and that open browser blocks other
  agents. The script closes only this session's MCP Chrome; the next MCP call
  launches a fresh one.

## Commands

All commands run from the `laserforce_simulator/` subdirectory (where `manage.py` lives):

```bash 
# Development server
python manage.py runserver

# Apply migrations
python manage.py migrate

# Create new migrations after model changes
python manage.py makemigrations

# Run all tests with coverage
pytest

# Run a single test file
pytest matches/tests/simulation_tests.py

# Run a specific test class or method
pytest matches/tests/simulation_tests.py::ClassName::method_name

# Batch-simulate N rounds and print average scores per role
python manage.py score_averages --rounds 50

# Score averages for specific teams
python manage.py score_averages --rounds 100 --team-red "Team A" --team-blue "Team B"

# Analyse events from a completed DB round
python manage.py game_analysis --round <id>

# UX-01: stamp every Unmanaged row (manager IS NULL) on all five Ownership
# roots to one Account. Idempotent; a second run reports 0 for every model.
python manage.py claim_unmanaged --user <email>

# UX-01: password reset is NOT implemented (no mail provider) -- this is the
# only account-recovery path.
python manage.py changepassword <email>
```

Frontend commands: [`frontend/CLAUDE.md`](frontend/CLAUDE.md).

CI runs `pytest` with coverage and uploads to Codecov (see `.github/workflows/ci.yml`). Python version is 3.11.

## Database

- **PostgreSQL is canonical** (production, CI, Fly.io deploy); **SQLite is a guarded dev-only convenience**, used automatically when `DATABASE_URL` is unset. See [ADR-0025](docs/adr/0025-postgresql-canonical-sqlite-dev-only.md).
- **Local Postgres:** `docker compose up` (the `postgres:16` service in `docker-compose.yml` sets `DATABASE_URL` to it). **Zero-setup dev:** leave `DATABASE_URL` unset to fall back to `db.sqlite3`.
- **Test suite on Postgres (what CI runs):** start a throwaway container once with `docker run -d --name lf-test-pg -e POSTGRES_USER=laserforce -e POSTGRES_PASSWORD=laserforce -e POSTGRES_DB=laserforce_test -p 5433:5432 postgres:16` (later: `docker start lf-test-pg`), then run, via the Bash tool, `DATABASE_URL=postgres://laserforce:laserforce@localhost:5433/laserforce_test python -m pytest laserforce_simulator`.
- `dbshell` and the management commands above run against whichever backend `DATABASE_URL` selects.
- **⚠ `AUTH_USER_MODEL` deployment hazard (UX-01).** UX-01 set `AUTH_USER_MODEL = "accounts.User"` *after* the `auth` / `admin` migrations had already been applied against `auth.User`, so **any database created before UX-01** raises `InconsistentMigrationHistory: Migration admin.0001_initial is applied before its dependency accounts.0001_initial` on the next `migrate`. **CI and the test suite are unaffected** — the test database is built fresh every run, so `pytest` goes green while the dev `db.sqlite3` and the Fly.io Postgres both break; do not read a green suite as proof the deploy is healthy. The approved recovery is a **fresh database** ([ADR-0004](docs/adr/0004-simulation-data-is-disposable.md) — simulation data is disposable): delete `db.sqlite3` locally / re-provision the Fly.io Postgres, then `migrate` and `createsuperuser`. It is explicitly **not** a data migration, a migration-history rewrite, or a `--fake` shim. See [ADR-0038](docs/adr/0038-accounts-and-uniform-manager-ownership.md) and `README.md`.

## Git Workflow

- **ALWAYS create a feature branch BEFORE making commits for new work.** Never
  commit directly to `main`. If you find yourself on `main` with changes,
  branch first (`git switch -c <feature-name>`) and only then commit.
- **ALWAYS pause for user approval before `git commit` or `git push`.** Output
  a PR template/summary first (summary, test counts, files changed) and wait
  for explicit "go" before writing to the repo or remote.
  **One exception (`/implement-spec`):** when the user approves a `/to-tickets`
  breakdown, that approval covers local commits on that feature's integration
  branch and its ticket branches (never `main`). Pushing, opening the PR and
  merging to `main` still wait for an explicit "go".
- **Never use `cd` (or `-C`) before git commands.** Run `git` directly from the
  session cwd — see `## Tooling` above for the full rationale (a stray
  `cd`-then-git sequence has corrupted the working tree before).
- **UI changes need screenshots or a video in the PR body.** Any PR touching
  `templates/`, CSS, static assets or `frontend/src/` must show the result: a
  before / after screenshot, or a short video for a flow. The `PR evidence`
  workflow (`scripts/check_pr_evidence.py`) fails the PR otherwise; a line
  `No visual change: <reason>` is the only opt-out. Capture with the Chrome
  DevTools MCP (`take_screenshot` with `filePath`, or `screencast_start` /
  `screencast_stop`), then run `python scripts/pr_assets.py <files>`: it
  publishes them to the never-merged `pr-assets` branch and prints the markdown
  to paste. Publishing pushes, so it waits for the same "go" as the PR.

## Test-Driven Development

This project follows TDD. Before implementing any new feature or fixing a bug:

1. **Write the test first.** Add a failing test that describes the expected behavior. Run it to confirm it fails for the right reason.
2. **Implement the minimum code** to make the test pass. Don't add more than needed.
3. Repeat one slice at a time (one test, then just enough code to pass it). Refactoring belongs to review (`mattpocock-skills:code-review`), not to the red-green loop.

**Test placement:**
Put a test beside the existing tests for the module it covers. Glob first, because the folders are
the record:
- **Per-app:** `<app>/tests/` or `<app>/tests.py`. For example, `matches/tests/simulation_tests.py` holds
  simulator logic and `matches/tests/test_api_*.py` holds the matches API.
- **Project-wide API behaviour** (envelope, CORS, auth headers): `laserforce_simulator/api/tests/`.
- **Test harness** (root `conftest.py`, `laserforce_simulator/testing.py`): `laserforce_simulator/tests/`.

**What to test:**
- Every new public function or method gets at least one test covering the happy path and one covering an edge case or failure mode.
- New Django views get tests for both success responses and invalid input.
- Bug fixes must include a regression test that would have caught the bug.

**Simulation tests** use fixed random seeds (`random.seed(42)`) or inject deterministic player stats to keep results reproducible — avoid asserting on exact point totals from unseeded runs.

**Do not** write tests that only verify mocks return what you told them to return. Prefer testing real behavior with lightweight in-memory objects or Django's `TestCase` with a test database.

## Testing & Verification

- After implementing a feature, run the full `pytest` suite before reporting
  completion. Report exact pass/fail counts (e.g. "877 passed, 0 failed"), not
  a vague "tests pass".
- For changes affecting determinism (RNG, parallelism, ordering), add an
  explicit serial-vs-parallel determinism test that asserts equal outputs from
  both code paths given the same seed.
- Before running analysis or management commands (`game_analysis`,
  `score_averages`, etc.), check for unapplied migrations with
  `python laserforce_simulator/manage.py makemigrations --check --dry-run` and
  run `migrate` if needed — stale schema has caused silent run failures.

## Sub-Agent Delegation

- **Parallelise by ticket, never by layer.** Each implementer agent owns one
  vertical ticket and writes both its tests and its code, test-first. Never
  split a ticket into separate Code and Tests agents. Agree the seams up front
  in the `/to-spec` spec (every new public name, signature and return shape at
  the seam), and point every agent at the spec issue so no two agents disagree
  on names.
- **After agents return, manually verify their work — do not assume
  completeness.** Triage failing tests yourself rather than trusting an
  agent's self-report. Check for the failure modes that have bitten us before:
  off-by-one in tick math, constant mismatches in formulas (e.g. MVP weights),
  field-name drift between docs and code, missing methods on the seam.

## Agent skills

Engineering skills come from the `mattpocock-skills` plugin (auto-updating);
project-specific skills live in `.claude/skills/`. The feature pipeline is run
by `/next-feature`: grill, spec, tickets, implement, review, PR, retro.

### Issue tracker

Specs and tickets are GitHub Issues (`gh`); `PLAN.md` stays the roadmap and
links each task to its spec issue. See `docs/agents/issue-tracker.md`.

**`PLAN.md` holds open work only.** When a task ships (marked `[DONE]`), move its
whole section, with its implementation notes, out of `PLAN.md` into `PLAN-completed.md`
under the same `##` phase heading (add the heading if it's missing). Leave at most a
one-line pointer behind, and drop any done IDs from `PLAN.md`'s **Sequencing Summary**.

### Triage labels

The five default roles (`needs-triage`, `needs-info`, `ready-for-agent`,
`ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `GLOSSARY.md` + `docs/adr/` at the repo root. See
`docs/agents/domain.md`.

### Coding standards

The `/code-review` Standards reviewer reads `CODING_STANDARDS.md`.

## Architecture

Django 5.2 app that simulates competitive laser tag (Laserforce) matches. The root URL serves the `teams` app as the homepage. Four Django apps: `accounts`, `teams`, `matches`, and `core`.

### Data Model Hierarchy

```
Match (2 rounds, winner by rounds then points)
  └── GameRound (1 of the 2 rounds; 15-minute simulation)
        ├── PlayerRoundState (one per player, tracks all resources/stats)
        └── GameEvent (chronological log of every in-game action)
```

### App Guides

Django templates live in `laserforce_simulator/templates/<app>/`, not per app.
The test harness is `laserforce_simulator/laserforce_simulator/testing.py`.

- [`laserforce_simulator/accounts/CLAUDE.md`](laserforce_simulator/accounts/CLAUDE.md) — the `accounts` app (UX-01): the custom `User` (the **Account**), the `manager` ownership seam in `accounts/permissions.py`, the `/accounts/` auth URLs, and `claim_unmanaged`
- [`laserforce_simulator/teams/CLAUDE.md`](laserforce_simulator/teams/CLAUDE.md) — Team/Player models, roster rules, `/teams/` URLs
- [`laserforce_simulator/matches/CLAUDE.md`](laserforce_simulator/matches/CLAUDE.md) — index for Match/GameRound, the simulation engine, Leagues, tournaments and the API; feature detail in `docs/agents/matches/`
- [`laserforce_simulator/core/CLAUDE.md`](laserforce_simulator/core/CLAUDE.md) — Map editor, zone/LOS processing, `/maps/` URLs, `/api/maps/` (list, multipart upload, delete, editor actions)
- [`frontend/CLAUDE.md`](frontend/CLAUDE.md) — the Angular 22 SPA (ANG-04, ADR-0042): npm commands, stack, the `apiBaseUrl` rule, the Windows BOM note

### REST API

`/api/` (DRF + `django-filter`, routed in `laserforce_simulator/api_urls.py`) follows
[ADR-0039](docs/adr/0039-rest-api-envelope-and-form-bound-writes.md): one `{"data", "meta"}` /
`{"errors", "meta"}` envelope, writes bind the existing Django `Form`, cross-Account rows are 404.
API conventions and the per-module service layout: see
[`laserforce_simulator/matches/CLAUDE.md`](laserforce_simulator/matches/CLAUDE.md) **API conventions**
and **Service layer**. ANG-01 is complete. The Accounts endpoints (`/api/accounts/`, ANG-01g) are
documented in [`laserforce_simulator/accounts/CLAUDE.md`](laserforce_simulator/accounts/CLAUDE.md).