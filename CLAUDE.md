# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Stack

- Primary language: Python
- Use type hints for all function signatures
- Run linting/formatting (black) after edits
- Validate YAML/HTML changes before committing

## Tooling

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
- When piping into `manage.py shell`, write a UTF-8 temp file and invoke
  `manage.py shell -c "$(Get-Content tmp.py -Raw)"` (or pass the path via
  `--command`) to avoid BOM and interactive-shell issues.

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
```

CI runs `pytest` with coverage and uploads to Codecov (see `.github/workflows/ci.yml`). Python version is 3.11.

## Database

- **PostgreSQL is canonical** (production, CI, Fly.io deploy); **SQLite is a guarded dev-only convenience**, used automatically when `DATABASE_URL` is unset. See [ADR-0025](docs/adr/0025-postgresql-canonical-sqlite-dev-only.md).
- **Local Postgres:** `docker compose up` (the `postgres:16` service in `docker-compose.yml` sets `DATABASE_URL` to it). **Zero-setup dev:** leave `DATABASE_URL` unset to fall back to `db.sqlite3`.
- `dbshell` and the management commands above run against whichever backend `DATABASE_URL` selects.

## Git Workflow

- **ALWAYS create a feature branch BEFORE making commits for new work.** Never
  commit directly to `main`. If you find yourself on `main` with changes,
  branch first (`git switch -c <feature-name>`) and only then commit.
- **ALWAYS pause for user approval before `git commit` or `git push`.** Output
  a PR template/summary first (summary, test counts, files changed) and wait
  for explicit "go" before writing to the repo or remote.
- **Never use `cd` (or `-C`) before git commands.** Run `git` directly from the
  session cwd — see `## Tooling` above for the full rationale (a stray
  `cd`-then-git sequence has corrupted the working tree before).

## Test-Driven Development

This project follows TDD. Before implementing any new feature or fixing a bug:

1. **Write the test first.** Add a failing test that describes the expected behavior. Run it to confirm it fails for the right reason.
2. **Implement the minimum code** to make the test pass. Don't add more than needed.
3. **Refactor** if needed, keeping all tests green.

**Test placement:**
- `matches/tests/simulation_tests.py` — simulator logic, game events, round outcomes
- `matches/tests.py` — match/round model behavior, views
- `teams/tests.py` — team/player model behavior, views
- `core/tests.py` — map processing, zone detection, sight line computation

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

- When dispatching parallel agents (code / tests / docs), **define the seam
  contract upfront** as a written artifact: every new method name, signature,
  dataclass field, and return shape. Share it with all agents so the test
  agent and code agent cannot disagree on names.
- **After agents return, manually verify their work — do not assume
  completeness.** Triage failing tests yourself rather than trusting an
  agent's self-report. Check for the failure modes that have bitten us before:
  off-by-one in tick math, constant mismatches in formulas (e.g. MVP weights),
  field-name drift between docs and code, missing methods on the seam.

## Architecture

Django 5.2 app that simulates competitive laser tag (Laserforce) matches. The root URL serves the `teams` app as the homepage. Three Django apps: `teams`, `matches`, and `core`.

### Data Model Hierarchy

```
Match (2 rounds, winner by rounds then points)
  └── GameRound (1 of the 2 rounds; 15-minute simulation)
        ├── PlayerRoundState (one per player, tracks all resources/stats)
        └── GameEvent (chronological log of every in-game action)
```

### App Guides

- [`laserforce_simulator/teams/CLAUDE.md`](laserforce_simulator/teams/CLAUDE.md) — Team/Player models, roster rules, `/teams/` URLs
- [`laserforce_simulator/matches/CLAUDE.md`](laserforce_simulator/matches/CLAUDE.md) — Match/GameRound models, simulation engine, role mechanics, `/matches/` URLs
- [`laserforce_simulator/core/CLAUDE.md`](laserforce_simulator/core/CLAUDE.md) — Map editor, zone/LOS processing, `/maps/` URLs