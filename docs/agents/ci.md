# CI: the Python jobs (PERF-02)

`.github/workflows/ci.yml` runs the Python side as four jobs:

```
lint ---------------------------------+
test (shard 1) --+                    |
test (shard 2) --+--> coverage -------+--> test-result --> docker
test (shard 3) --+                    |
test (shard 4) --+                    |
frontend -------------------------------------------------^
```

- **`lint`**: ruff, black, the encoding and SQLite JSON-lookup scans, actionlint,
  `makemigrations --check`, and `migrate` against the job's Postgres. It runs once,
  beside the shards.
- **`test`**: a 4-runner matrix. Each runner runs `pytest --splits 4 --group <n>`
  (`pytest-split`) with the usual `-n auto --dist worksteal`, coverage through
  `sys.monitoring` (`COVERAGE_CORE=sysmon`), and `--durations=25`. It keeps its
  `.coverage` data as the `coverage-shard-<n>` artifact.
- **`coverage`**: downloads the shards' data files, runs `coverage combine`, writes
  `coverage.xml` and uploads to Codecov **once**.
- **`test-result`**: `if: always()`. It is green only when `lint`, every shard and
  `coverage` succeeded. `docker` needs it. If branch protection is ever added,
  require `test-result`, never a shard, so the shard count can change freely.

## The durations file

`laserforce_simulator/.test_durations` balances the shards. A test missing from it
still runs, at the average duration, so a stale file only unbalances the shards.
Regenerate it when one shard's log runs much longer than the others (Postgres, as CI):

```
DATABASE_URL=postgres://laserforce:laserforce@localhost:5433/laserforce_test python -m pytest laserforce_simulator --store-durations --durations-path laserforce_simulator/.test_durations
```

To change the shard count, edit the matrix and `--splits` together: a mismatch
silently skips or repeats groups.

## Measuring a run

`python scripts/ci_timings.py <run-id>` prints every job's time, each shard's
pytest summary, the combined coverage total, and the time from run start to
`test-result`. Run times vary by about 30 s or more, so judge a change on the
slowest of at least two runs.

## Why sharding

The 4-vCPU runner limits the suite, not coverage. Moving to Python 3.12 + sysmon
saved about 45 s of an 11-minute test step. Measurements are on spec #324.
