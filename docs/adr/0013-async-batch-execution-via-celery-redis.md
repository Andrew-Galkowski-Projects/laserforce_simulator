# Async batch execution via Celery + Redis (replaces in-process Job dicts)

**Status:** Accepted (API-03, 2026-05-24)

## Context

SIM-10 / SIM-11 (May 2026) shipped an in-process async path for the
`/matches/simulate-batch/` UI. A POST starts a daemon thread that drives
`BatchSimulator.run_incremental(...)` and writes each ~50-snapshot progress
update into a module-level `_BATCH_JOBS: dict` guarded by `_JOBS_LOCK`. A
companion `batch_simulate_status` view polls that dict. The save-games flow
(`/matches/save-games/`) followed the same pattern: `_run_save_job` daemon
thread → `_SAVE_JOBS: dict` → `save_batch_status` view. Both Job stores are
lost on every server restart and exist only in the web process's memory.

API-03 (PLAN.md Phase 5 — "Infrastructure & League System") adds a *REST*
async batch endpoint at `POST /api/simulate-batch/` returning a `job_id`,
with `GET /api/simulate-batch/<job_id>/` polling status. Jobs expire after
**1 hour**. PLAN.md names Celery + Redis (Upstash on Fly.io) as the
execution model.

This forces a choice that PLAN.md does not explicitly settle: API-03 could
ship as a second, parallel async path next to SIM-10's in-process one — two
job idioms for the same workload — or it could be the unification point that
moves both UI flows onto the new broker. The choice is hard to reverse once
the UI is committed in either direction.

## Decision

Adopt **Celery + Redis as the single async-job execution path** for the
project. Retire the SIM-10 / SIM-11 in-process Job dicts (`_BATCH_JOBS`,
`_SAVE_JOBS`, `_JOBS_LOCK`, `_run_batch_job`, `_run_save_job`, `_workers_for`)
and rebuild both flows on top of two Celery tasks:

- `simulate_batch_task(team_red_id, team_blue_id, n, arena_map_id,
  master_seed)` — drives `BatchSimulator.run_incremental(...)` and emits each
  yielded snapshot via `self.update_state(state="PROGRESS", meta=snapshot)`;
  returns the final aggregate dict on success.
- `save_games_task(team_red_id, team_blue_id, seeds, n, arena_map_id)` —
  replays the carried `(seed, flipped)` pairs and persists each round via
  `BatchSimulator().save_games(...)`; returns `{"round_ids": […]}`.

Three POST entry points enqueue these tasks: `POST /matches/simulate-batch/`
(UI), `POST /api/simulate-batch/` (REST), and `POST /matches/save-games/`
(UI). All three return `{"job_id": <celery_task_id>, …}` and the polling
endpoints read the same `AsyncResult(job_id)`.

### Worker concurrency model

Each Celery task runs `BatchSimulator.run_incremental(..., workers=1)` —
serial inside the task body. Horizontal throughput comes from running
multiple Celery worker processes (`celery -A laserforce_simulator worker
--concurrency 4`). One concurrency knob (Celery's `--concurrency`), not
two stacked (Celery × inner `ProcessPoolExecutor`).

### Status vocabulary mapping

The polling JSON keeps SIM-10's three-value vocabulary `running | complete |
error`. A view-layer helper `_celery_state_to_job_status(state)` maps Celery
native states at the boundary: `PENDING` / `STARTED` / `PROGRESS` →
`"running"`; `SUCCESS` → `"complete"`; `FAILURE` / `REVOKED` → `"error"`.
Both the UI and REST polling endpoints return the mapped vocabulary
identically, so existing UI poll JS works unchanged.

### Job expiry

`CELERY_RESULT_EXPIRES = 3600` (1 hour, PLAN.md). After expiry, polling a
known-good Job id returns Celery `PENDING` (indistinguishable from a never-
submitted id) — mapped to `"running"` by the vocabulary helper. The CONTEXT
entry for **Job id** records this asymmetry.

### Test and local-dev story

`CELERY_TASK_ALWAYS_EAGER = True` in the test settings — tasks run
synchronously in-process during pytest. **No Redis required for tests or
CI.** Local dev requires Redis + a `celery -A laserforce_simulator worker`
process running; an optional `LF_CELERY_EAGER=1` env var sets eager mode in
dev too so developers without a local Redis can still exercise the views.
Documented in the matches/CLAUDE.md async-execution subsection.

### Production deployment

`CELERY_BROKER_URL` and `CELERY_RESULT_BACKEND` are read from environment
variables (`decouple.config`), defaulting to the same Upstash Redis URL
(both broker and result backend share one Redis). Fly.io secrets carry the
Upstash credential. A second Fly process (`processes = ["app", "worker"]` in
`fly.toml`) runs the Celery worker.

## Rejected alternatives

**Keep API-03 as a parallel Celery path; leave UI on the in-process
`_BATCH_JOBS` dict.** Smallest change to the UI. Rejected because the
project would carry *two* idioms for the same async-job concept indefinitely
— every future async surface would have to pick one, and the two patterns
would drift (job-state shape, status vocabulary, error semantics, expiry
behaviour). The "Async execution" domain term in CONTEXT.md would have to
caveat itself with "in-process for UI, Celery for API."

**Hybrid: route small `n` (< 50) through in-process threading, large `n`
through Celery.** Avoids broker spawn cost on tiny batches. Rejected because
the threshold becomes a permanent branch in every async surface; the second
code path resurrects the very `_BATCH_JOBS` pattern this ADR is retiring;
and the SIM-11 measurement that motivated `_workers_for` was about
`ProcessPoolExecutor` *spawn cost on Windows*, not about Celery broker
round-trips, which are sub-millisecond on a warm Redis connection.

**Use the `celery_worker` pytest fixture against a real broker in tests.**
End-to-end coverage of serialisation, retry, worker boot. Rejected because
it makes pytest depend on Redis being installed (failing CI without it),
slows the suite per-task, and `CELERY_TASK_ALWAYS_EAGER` already exercises
the task body and the view ↔ task seam under the same view-test harness the
SIM-10 tests already use.

**Use Celery native task states (`PENDING` / `STARTED` / `SUCCESS` / …) in
the polling JSON.** More accurate semantics; matches the AsyncResult API
directly. Rejected because the existing UI poll JS (shipped by SIM-10) only
knows `running | complete | error`; switching vocabularies forces a UI
rewrite for zero behavioural gain. The mapped contract is intentionally
narrow — three values cover every UI branch.

**Keep `workers=_workers_for(n)` inside the Celery task body (nested
ProcessPoolExecutor under Celery worker).** Preserves SIM-11's per-batch
threshold. Rejected because Celery already provides horizontal scaling via
`--concurrency`; running an inner pool inside each Celery worker doubles
process memory, compounds Windows fork-cost (already the reason for the
`n < 50` threshold), and gives two concurrency knobs that interact
non-obviously. One knob is simpler to reason about and to size on Fly.io.

**Drop `master_seed` from the task signature** (UI doesn't expose it, REST
could omit it). Rejected: keeping it preserves the SIM-10 `_run_batch_job`
signature and the SIM-07 reproducibility contract, and the REST endpoint
benefits from being able to pin a master seed for tests / scripted runs.

## Consequences

**Operational dependency.** The project now requires a Redis broker in
production. Server downtime where Redis is unreachable means new Jobs can't
be enqueued and existing Job ids can't be polled (foreground rounds via
`simulate_match` / `simulate_single_round_detailed` are unaffected — they
don't touch Celery). Fly.io's Upstash add-on provides the managed Redis;
the free tier (10k commands/day) covers expected developer + light
production usage.

**Lost-on-restart semantics replaced by 1-hour TTL.** Pre-API-03, a server
restart wiped both `_BATCH_JOBS` and `_SAVE_JOBS` — every in-flight job
disappeared. Post-API-03, Job results survive web-process restarts (they
live in Redis, not Python memory) but expire after 1 hour regardless. A
polled-after-expiry Job id is indistinguishable from a never-submitted one;
the CONTEXT.md **Job id** entry documents this.

**Two new env vars and one new Fly.io process.** `CELERY_BROKER_URL` and
`CELERY_RESULT_BACKEND` are required in production (Fly.io secrets). The
Dockerfile gains a Celery worker entrypoint variant; `fly.toml` adds
`processes = ["app", "worker"]` so the worker runs alongside the web
process.

**SIM-10 / SIM-11 view-layer tests rewritten.** `test_sim10_incremental.py`
keeps its `run_incremental` *invariants* (chunk-size table, partial-equals-
final, serial == parallel at every boundary, fail-fast, n=0) — those test
the simulator, not the job machinery. Its `views_tests.py` extensions for
`_BATCH_JOBS` lifecycle / session handover are rewritten against the new
Celery-backed views (with `CELERY_TASK_ALWAYS_EAGER = True`).

**`save_batch_status` and `batch_simulate_status` view bodies are
rewritten** to read `AsyncResult(job_id)` instead of `_BATCH_JOBS[job_id]` /
`_SAVE_JOBS[job_id]`. Their URL paths, response shapes, and the
`request.session["batch_seeds"]` session-handover guard are **unchanged** —
the public contract on those endpoints is preserved verbatim so the polling
UI keeps working without JS changes.

**No simulation behaviour change.** This ADR moves the *executor* of the
existing `BatchSimulator.run_incremental` and `BatchSimulator.save_games`
calls; it does not touch their bodies. The SIM-07 / SIM-08 / SIM-10
determinism contract holds in form (same `master_seed` + Orientation +
rosters + map ⇒ identical games; serial == parallel at every chunk
boundary; faithful **Replay**). **No Score Calibration re-baseline** is
triggered (mirrors the SIM-10 / SIM-11 precedent).

## GEN-01 addendum (2026-06-26) — daemon-guarded intra-task parallelism

This ADR set `simulate_batch_task` to `workers=1` and pushed scaling to the
Celery `--concurrency` knob. That parallelises *across* tasks (many runs at
once) but **never within a single run** — so one user clicking "simulate 100"
stays single-core regardless of `--concurrency`. GEN-01 revives the SIM-11
`_workers_for(n)` heuristic (`1` for `n < 50`, else `min(os.cpu_count() or 1,
n)` — all cores, bounded only by the game count) for `simulate_batch_task` so
a single large run uses every core. The SIM-11 4-worker cap is dropped: the
`n < 50` gate already keeps every test/CI batch serial, so the cap only ever
throttled real user runs.

The revival is **guarded**, not a blanket reversal. A Celery **prefork** worker
runs each task in a *daemonic* child process, and a daemonic process may not
spawn its own children (`ProcessPoolExecutor` ⇒ `AssertionError: daemonic
processes are not allowed to have children`). `_batch_workers(n)` therefore
returns `1` whenever `multiprocessing.current_process().daemon` is true. Net
effect by execution context:

- **Prefork worker (e.g. Fly.io/Linux prod):** daemonic child ⇒ `workers=1`,
  unchanged from this ADR; horizontal scaling stays `--concurrency`.
- **EAGER (dev without Redis) / `solo` / `threads` pool:** non-daemonic ⇒ the
  run goes multi-core for `n ≥ 50`.

`save_games_task` is untouched (serial replay of a few avg/outlier seeds).
Determinism is unaffected — the SIM-10 serial==parallel-at-every-boundary
guarantee holds for any `workers` value, so **no Score Calibration
re-baseline**. The decision is reversible (a helper + one `workers=` argument),
so no new ADR — this addendum records the partial, guarded reversal.
