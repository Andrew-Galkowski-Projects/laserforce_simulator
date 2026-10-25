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
  `sys.monitoring` (`COVERAGE_CORE=sysmon`), and `--durations=25`, then fails if a
  `laserforce_simulator/media/` dir exists (a test bypassed the temp `MEDIA_ROOT`, TEST-01). It keeps its
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
shard's pytest more than about 40% over the mean of the shards, on two runs in a row.
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

## Why sharding

The 4-vCPU runner limits the suite, not coverage. Moving to Python 3.12 + sysmon
saved about 45 s of an 11-minute test step. Measurements are on spec #324.

## Test password hasher (PERF-05)

The root `conftest.py`'s `pytest_configure` sets `PASSWORD_HASHERS` to
`MD5PasswordHasher` for tests only. Full PBKDF2 on every test password cost about a third
of the summed test time. `settings.py` sets no `PASSWORD_HASHERS`, so production keeps
Django's PBKDF2 default; `laserforce_simulator/tests/test_password_hasher.py` pins both.
