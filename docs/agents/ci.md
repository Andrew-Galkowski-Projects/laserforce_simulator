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
  `.coverage` data as the `coverage-shard-<n>` artifact, and its tests' timings
  (`--store-durations --clean-durations`) as `durations-shard-<n>`.
- **`coverage`**: downloads the shards' data files, runs `coverage combine`, writes
  `coverage.xml` and uploads to Codecov **once**.
- **`test-result`**: `if: always()`. It is green only when `lint`, every shard and
  `coverage` succeeded. `docker` needs it. If branch protection is ever added,
  require `test-result`, never a shard, so the shard count can change freely.

## The durations file

`laserforce_simulator/.test_durations` balances the shards (pytest-split's default
`duration_based_chunks`, which keeps a test class in one shard). A test missing from it
still runs, at the average duration, so a stale file only unbalances the shards.

Every green shard keeps its own tests' CI timings as the `durations-shard-<n>`
artifact for 30 days (PERF-04). **Refresh** when `ci_timings.py` shows the slowest
shard's pytest more than about 40% over the mean of the shards, on two runs in a row:

```
python scripts/refresh_test_durations.py <run-id> [<run-id> ...]
```

Pass two or three green runs: each test gets its median across them, because one
runner's timings swing by a minute. The script refuses a run that is missing any
shard's artifact. Commit the file in a normal PR. Don't record it locally: a
16-worker machine's proportions don't match the 4-vCPU runners.

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
