# CI: the Python jobs (PERF-02)

`.github/workflows/ci.yml` runs the Python side as four jobs, plus the Desktop build (DESK-02):

```
lint ---------------------------------+
test (shard 1) --+                    |
test (shard 2) --+--> coverage -------+--> test-result --+--> docker
test (shard 3) --+                    |                  |
test (shard 4) --+                    |                  +--> desktop-publish (push to main / v* tag)
frontend ----------------------------------------------------^   ^
desktop (windows-latest) ----------------------------------------+
```

It runs on every PR, on pushes to `main` (and a few legacy branches), and on a `v*` tag push.

- **`changes`** (LFS-02 retro): sets `code=false` on a pull request whose diff against its base
  touches only `*.md` files, and `code=true` everywhere else (every push). `frontend` and `desktop`
  run only when `code` is true, and `docker` skips with `frontend`. `lint` and `test` always run,
  because tests read `GLOSSARY.md` and other docs.

- **`lint`**: ruff, black, the encoding and SQLite JSON-lookup scans, actionlint,
  `makemigrations --check`, and `migrate` against the job's Postgres. It runs once,
  beside the shards.
- **`test`**: a 4-runner matrix. Each runner runs `pytest --splits 4 --group <n>`
  (`pytest-split`) with the usual `-n auto --dist worksteal`, coverage through
  `sys.monitoring` (`COVERAGE_CORE=sysmon`), and `--durations=25`, then fails if a
  `laserforce_simulator/media/` dir exists (a test bypassed the temp `MEDIA_ROOT`, TEST-01). It keeps its
  `.coverage` data as the `coverage-shard-<n>` artifact, and its tests' timings
  (`--store-durations --clean-durations`) as `durations-shard-<n>`.
- **`coverage`**: downloads the shards' data files, runs `coverage combine`, writes
  `coverage.xml` and uploads to Codecov **once**.
- **`test-result`**: `if: always()`. It is green only when `lint`, every shard and
  `coverage` succeeded. `docker` needs it. If branch protection is ever added,
  require `test-result`, never a shard, so the shard count can change freely.
- **`docker`**: needs `test-result` and `frontend`. It builds the image (its node stage builds the
  SPA, ADR-0049) and smoke-tests it on Postgres (`curl` on `:8000/admin/login/` until ready), then,
  in the same step while that container is up, runs `bash scripts/smoke_spa.sh` (ANG-10). It
  asserts response bodies and headers on `:8000`: the SPA at `/` with `<base href="/">` and
  `no-cache`, the deep-link fallback, the main bundle `immutable`, gzip and brotli, a font under `/fonts/`
  `immutable`, `/api/` reaching Django (a 401 envelope) and an unknown `/api/` path a 404 that is
  not the SPA, the `/app/<path>` 301, Admin's `/static/` stylesheet, and CSRF / `X-Forwarded-Proto`
  against `/admin/login/`. Run it locally against the image: `docker run -d -p 8000:8000 -e
  SECRET_KEY=x -e DATABASE_URL=sqlite:////tmp/smoke.db <image>`, then `bash scripts/smoke_spa.sh`
  (`BASE=` for another port). On `main` the job then deploys to Fly.io, but only when a
  `FLY_API_TOKEN` secret is set; none is, so those steps skip.
- **`desktop`** (DESK-02, ADR-0048): `windows-latest` with setup-python's python.org CPython 3.12
  (never conda: it bundles the wrong `_ssl` / `pyexpat` DLLs). It installs
  `requirements-desktop.txt` plus the pinned `pyinstaller`, runs `desktop/build.ps1` (the build
  record, SPA build, PyInstaller one-folder from `desktop/laserforce.spec`, zip), then `python desktop/smoke.py <exe>`
  against the frozen exe. The steps are the same `run_smoke` that `desktop_app/tests/test_launcher_smoke.py`
  runs unfrozen, and the job summary gets the sizes and the cold start. It keeps the zip as the
  `desktop-build` artifact for 7 days (download it from a PR's run to try the build). It needs only
  `changes`, so it runs beside the shards.
- **`desktop-publish`** (DESK-02): needs `test-result`, `frontend` and `desktop`, and runs only on a
  push to `main` or a `v*` tag (`contents: write`, job-level `concurrency: desktop-latest`, queued).
  On `main` it force-moves the `desktop-latest` tag to the commit and `gh release upload --clobber`s
  `LaserforceSimulator-windows-x64.zip` onto the rolling pre-release (created on the first run, never
  deleted, so `.../releases/download/desktop-latest/LaserforceSimulator-windows-x64.zip` never 404s).
  If `main` has already moved on, it skips with a notice, and the newer run publishes. On a `v*` tag
  ("Publish a numbered Release", DESK-04) a plain `vX.Y.Z` tag creates a numbered **Release**, the
  public download at `releases/latest`; any other `v*` tag (`v0.2.0-rc1`) creates the same with
  `--prerelease`, so it never becomes `releases/latest`. The notes are `desktop/RELEASE_NOTES.md`
  (`--notes-file`) with GitHub's changelog after them (`--generate-notes`). The tag rule is a twin of
  `RELEASE_TAG` in `desktop_app/build_info.py`: change both together. A red job above means nothing
  is published, so `desktop-latest` keeps the last good build.
- **The build record (DESK-04):** `build.ps1` runs `desktop/write_build_info.py`, which reads
  `GITHUB_REF` / `GITHUB_SHA`. So the zip from a `v1.2.3` tag run is channel `release`, from `main`
  `latest`, and from a PR `dev`. A Release's zip is built by the tag's own run, never copied from
  `main`. The Desktop App reads the record (`desktop_version` in the Help menu) and, unless it is
  `dev`, asks GitHub once per launch for a newer build of its channel (the Update check,
  `desktop_app/CLAUDE.md`).

## The durations file

`laserforce_simulator/.test_durations` balances the shards (pytest-split's default
`duration_based_chunks`, which keeps a test class in one shard). A test missing from it
still runs, at the average duration, so a stale file only unbalances the shards.

Every green shard keeps its own tests' CI timings as the `durations-shard-<n>`
artifact for 30 days (PERF-04). **Refresh** when the slowest shard's pytest is more than
40% over the mean of the shards on two runs in a row: `watch_pr_ci.py` and `ci_timings.py`
print a `shard balance:` warning for each such run.
Also refresh right after a change that shifts a broad class of tests' cost: PERF-05's
test hasher made shard 1 run at a fifth of its prediction, and one slow runner kept the
40% check from firing (PERF-06):

```
python scripts/refresh_test_durations.py <run-id> [<run-id> ...]
```

Pass two or three green runs: each test gets its median across them, because one
runner's timings swing by a minute. When the refresh is the whole change, pass three:
PERF-06's two-run medians predicted the slowest shard 24% lighter, and CI gave about 10%. The script refuses a run that is missing any
shard's artifact. To get more runs of one commit, close and reopen the PR: each
reopen is a new run ID; watch it with `scripts/watch_pr_ci.py <pr> --after-run
<previous run-id>`, since the PR's checks still list the old run at first. Never `gh run rerun` a run you want timings from: the
re-run keeps the ID and drops the earlier attempt's artifacts (PERF-04). Commit the
file in a normal PR. Don't record it locally: a 16-worker machine's proportions don't
match the 4-vCPU runners.

**Limit:** a refresh balances the recorded durations, not the CPU load, and one
runner can run every shard 1.5-2x slow. The batch-sim pool tests pass an explicit
`workers=2` / `workers=4`, and the pool helps: pinned to 4 cores at `-n 4`,
`test_batch_sim.py` took 34 s with the pool and 57 s with its 120-game test serial,
because that test is the long pole. Leave the pool alone (PERF-06 probe).

To change the shard count, edit the matrix and `--splits` together: a mismatch
silently skips or repeats groups.

## Measuring a run

`python scripts/ci_timings.py <run-id>` prints every job's time, each shard's
pytest summary, the combined coverage total, and the time from run start to
`test-result`. Run times vary by about 30 s or more, so judge a change on the
slowest of at least two runs. `--durations` adds each shard's predicted test time
(the committed file) against its measured artifact, with the files that drifted
most: the first read when the shards look unbalanced (PERF-06).

## Spec passes alone, fails in the suite

The Angular test builder runs Vitest without per-file isolation: the specs a
worker runs share one jsdom, in an order set by the worker count. A spec that
leaves something behind (a fake timer, a started frame loop, a stub) breaks a
later spec only when the two land in the same worker, so it shows at CI's 4
vCPUs and not at a laptop's core count (ANG-06-13b: `map-editor`'s canvas host
timed out after `batch-simulate`).

1. Reproduce at 4 cores (PowerShell tool):
   `$env:CI='true'; cmd /c start '""' /b /wait /affinity F npm --prefix frontend run test:ci`.
2. Force one worker with a scratch `frontend/vitest-probe.config.mts` (never
   committed): `defineConfig({ test: { testTimeout: 15000, maxWorkers: 1, fileParallelism: false } })`,
   run as `npm --prefix frontend exec -- ng test --watch=false --runner-config=vitest-probe.config.mts`.
3. Bisect by pairs: the failing spec's folder plus one other folder
   (`--include=src/app/maps/**/*.spec.ts --include=src/app/matches/**/*.spec.ts`),
   then halve the other folder until one spec makes the failing one red.
4. Prove the fix red: `scripts/mutate.py` on the fix, with the pair as the run.

## Why sharding

The 4-vCPU runner limits the suite, not coverage. Moving to Python 3.12 + sysmon
saved about 45 s of an 11-minute test step. Measurements are on spec #324.

## Test password hasher (PERF-05)

The root `conftest.py`'s `pytest_configure` sets `PASSWORD_HASHERS` to
`MD5PasswordHasher` for tests only. Full PBKDF2 on every test password cost about a third
of the summed test time. `settings.py` sets no `PASSWORD_HASHERS`, so production keeps
Django's PBKDF2 default; `laserforce_simulator/tests/test_password_hasher.py` pins both.
