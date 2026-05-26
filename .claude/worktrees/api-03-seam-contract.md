# API-03 — Async batch simulation via Celery + Redis (seam contract)

API-03 ships the REST async endpoint pair `POST /api/simulate-batch/` (returns `{job_id, …}`) and `GET /api/simulate-batch/<job_id>/` (returns the polling JSON), and **unifies the UI batch + save flows onto the same Celery execution path**. The SIM-10 / SIM-11 in-process Job dicts (`_BATCH_JOBS`, `_SAVE_JOBS`, `_JOBS_LOCK`, `_run_batch_job`, `_run_save_job`, `_workers_for`) are retired and rebuilt on two `@shared_task`s — `simulate_batch_task` (drives `BatchSimulator.run_incremental`, emits each snapshot via `self.update_state(state="PROGRESS", meta=snapshot)`, returns the final aggregate) and `save_games_task` (replays carried `(seed, flipped)` pairs through `BatchSimulator.save_games`, returns `{"round_ids": […]}`). All three POST entry points (`/matches/simulate-batch/`, `/api/simulate-batch/`, `/matches/save-games/`) enqueue Celery tasks; all four polling endpoints read the same `AsyncResult(job_id)`. The polling JSON keeps the SIM-10 `running | complete | error` vocabulary unchanged (mapped at the view boundary), the `request.session["batch_seeds"]` handover guard is preserved verbatim, and `BatchSimulator.run_incremental` / `_aggregate_batch` / `save_games` / `score_averages` are untouched — this is an **executor swap**, not a mechanics change. The SIM-07/SIM-08/SIM-10 determinism contract holds (same `master_seed` + Orientation + rosters + map ⇒ identical games at every chunk boundary; serial == parallel; faithful Replay), Celery-vs-direct paths produce identical games under `CELERY_TASK_ALWAYS_EAGER`. **No Score Calibration re-baseline.**

---

## 1. Locked decisions (cross-reference [ADR-0013](../../docs/adr/0013-async-batch-execution-via-celery-redis.md))

The grilling session settled the following; do **not** re-derive:

- **Unify on Celery.** SIM-10/SIM-11 in-process Job dicts are retired in the same PR. Both UI flows (`/matches/simulate-batch/`, `/matches/save-games/`) and the new REST POST go through the same two `@shared_task`s.
- **Two tasks.** `simulate_batch_task(team_red_id, team_blue_id, n, arena_map_id, master_seed)` and `save_games_task(team_red_id, team_blue_id, seeds, n, arena_map_id)`. Both `@shared_task(bind=True)`.
- **Workers = 1 inside the task.** Each task calls `BatchSimulator.run_incremental(..., workers=1)` — serial inside the task body. Horizontal throughput comes from running multiple Celery workers (`celery -A laserforce_simulator worker --concurrency 4`). One concurrency knob, not two stacked.
- **EAGER in tests.** `CELERY_TASK_ALWAYS_EAGER = True` in pytest. No Redis required for tests or CI. `CELERY_TASK_EAGER_PROPAGATES = True` so task failures surface as exceptions, not silent state.
- **SIM-10 polling JSON shape preserved verbatim.** `{status, completed, total, partial, error, team_red_id, team_blue_id, arena_map_id}` for batch; `{status, error, round_ids}` for save. UI poll JS keeps working unchanged.
- **Status vocabulary mapped at the view boundary.** `running | complete | error` — never raw Celery states (`PENDING`, `SUCCESS`, …) in the JSON.
- **1h Job expiry.** `CELERY_RESULT_EXPIRES = 3600`. A polled-after-expiry id resolves to `PENDING` → `"running"` (indistinguishable from a never-submitted id; documented on the `Job id` CONTEXT.md term).
- **Retire `_BATCH_JOBS` / `_SAVE_JOBS` / `_JOBS_LOCK` / `_run_batch_job` / `_run_save_job` / `_workers_for`.** No second async idiom carried forward.
- **Code + settings + docs scope only.** No `fly.toml` change. No `Dockerfile` change. No CI Redis provisioning. No token auth on `/api/`. No `master_seed` UI/API exposure (test-only via task kwarg).
- **AllowAny inherits.** REST views inherit `REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"] = ["rest_framework.permissions.AllowAny"]` from API-02. POST without auth succeeds (mirrors the deferred-auth precedent locked in API-02).

---

## 2. Files added (NEW)

| Path | Purpose |
|---|---|
| `laserforce_simulator/laserforce_simulator/celery.py` | Celery app definition (`celery_app = Celery("laserforce_simulator")`) + `app.config_from_object("django.conf:settings", namespace="CELERY")` + `app.autodiscover_tasks()`. |
| `laserforce_simulator/matches/tasks.py` | Two `@shared_task(bind=True)` definitions: `simulate_batch_task` and `save_games_task`. |
| `laserforce_simulator/matches/tests/test_api03_tasks.py` | Direct task-level tests (run under `CELERY_TASK_ALWAYS_EAGER`). |
| `laserforce_simulator/matches/tests/test_api03_views.py` | View-level tests for the rewritten UI views + the new REST views. |

---

## 3. Files modified (EDIT)

| Path | Summary of edits |
|---|---|
| `laserforce_simulator/laserforce_simulator/__init__.py` | Add `from .celery import app as celery_app` and `__all__ = ("celery_app",)` so the worker can resolve the app. |
| `laserforce_simulator/laserforce_simulator/settings.py` | Append the `CELERY_*` config block (§7). No other change. |
| `laserforce_simulator/laserforce_simulator/api_urls.py` | Append two `path()` entries after `urlpatterns = router.urls` (DRF `DefaultRouter` only handles ViewSets; `APIView` needs `path()`): `path("simulate-batch/", SimulateBatchAPIView.as_view(), name="api_simulate_batch")` and `path("simulate-batch/<str:job_id>/", SimulateBatchStatusAPIView.as_view(), name="api_simulate_batch_status")`. Add the two-import line for the new views. |
| `laserforce_simulator/matches/urls.py` | **URL names + paths unchanged** (`simulate_batch`, `batch_simulate_status`, `save_batch_games`, `save_batch_status`). View bodies are rewritten on Celery; routing stays. |
| `laserforce_simulator/matches/views.py` | Delete `_SAVE_JOBS`, `_BATCH_JOBS`, `_JOBS_LOCK`, `_run_save_job`, `_run_batch_job`, `_workers_for`. Delete the `threading` / `uuid` imports if unused elsewhere. Add three view-layer helpers (§5). Rewrite `simulate_batch`, `batch_simulate_status`, `save_batch_games`, `save_batch_status` against `AsyncResult` + the new tasks. The `session["batch_seeds"]` single-write handover guard logic in `batch_simulate_status` is preserved verbatim (just re-sourced from `async_result.result` / `async_result.info` instead of `_BATCH_JOBS[job_id]`). |
| `laserforce_simulator/matches/api_views.py` | Add `SimulateBatchAPIView` (POST → enqueue + return `{job_id, …}`) and `SimulateBatchStatusAPIView` (GET → polling JSON). Both inherit DRF defaults (`AllowAny`, `SessionAuthentication`). |
| `laserforce_simulator/requirements.txt` | Add `celery[redis]>=5.3` line. |
| `laserforce_simulator/matches/CLAUDE.md` | Rewrite the SIM-10 `batch_simulate_status` subsection: drop the `_BATCH_JOBS` / `_SAVE_JOBS` / `_JOBS_LOCK` references; describe the Celery-task seam, `AsyncResult` polling, EAGER test mode, 1h expiry, the four URL names + the two REST URL names, the preserved polling JSON shape, the unchanged template DOM ids. Add an `## Async execution (Celery)` heading or fold into SIM-10. |
| `laserforce_simulator/matches/tests/views_tests.py` | Delete the seven classes in §6.1. Rewrite the four ARENA_MAP plumbing tests in §6.2 against `save_games_task` + `simulate_batch_task` (the arena_map flow is preserved; the seam changes from `_run_save_job` → `save_games_task.delay`). |
| `laserforce_simulator/matches/tests/test_sim10_incremental.py` | **Unchanged.** All seven `TestRunIncremental*` + `TestChunkSizeFor` classes survive — they pin the `run_incremental` simulator contract, not the job machinery (§6.3). |
| `PLAN.md` | Mark API-03 completed. Add the dense `- note:` block in house style. |
| `CONTEXT.md` | **Already done by the grill** — the `### Async execution` section + Job / Job id / Job status terms + the Celery mention on **Batch run** are in place. No further edit. |
| `docs/adr/0013-async-batch-execution-via-celery-redis.md` | **Already written.** No further edit. |

---

## 4. Locked names — Celery + tasks

### 4.1 Celery app

`laserforce_simulator/celery.py`:

```python
import os
from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "laserforce_simulator.settings")

celery_app = Celery("laserforce_simulator")
celery_app.config_from_object("django.conf:settings", namespace="CELERY")
celery_app.autodiscover_tasks()
```

`laserforce_simulator/__init__.py`:

```python
from .celery import app as celery_app

__all__ = ("celery_app",)
```

### 4.2 Tasks (`matches/tasks.py`)

```python
from celery import shared_task

@shared_task(bind=True, name="matches.simulate_batch")
def simulate_batch_task(
    self,
    team_red_id: int,
    team_blue_id: int,
    n: int,
    arena_map_id: int | None = None,
    master_seed: int | None = None,
) -> dict:
    """Drive BatchSimulator.run_incremental, emit each snapshot via
    self.update_state(state="PROGRESS", meta=snap), return the final
    aggregate dict on success.
    """
    ...

@shared_task(bind=True, name="matches.save_games")
def save_games_task(
    self,
    team_red_id: int,
    team_blue_id: int,
    seeds: list[list[int | bool]],
    n: int,
    arena_map_id: int | None = None,
) -> dict:
    """Replay carried (seed, flipped) pairs through BatchSimulator.save_games,
    return {"round_ids": [<int>, ...]}.
    """
    ...
```

**Pinned task `name=` strings** (must match the strings, not the dotted-module path — the project may move the module later): `"matches.simulate_batch"` and `"matches.save_games"`.

**Stale arena_map handling.** Both tasks resolve `arena_map_id` via `ArenaMap.objects.get(...)` inside a `try/except ArenaMap.DoesNotExist` that falls back to `None` — preserving the SIM-09 / SIM-10 stale-id semantics (`test_run_save_job_stale_arena_map_id_treated_as_none` expectation, rewritten for the task).

**Connection cleanup.** Both tasks end in a `finally: django.db.close_old_connections()` block — mirrors the `_run_batch_job` / `_run_save_job` `finally` in the retired code.

**`simulate_batch_task` progress emit.** Inside the `for snap in BatchSimulator().run_incremental(...)` loop, call `self.update_state(state="PROGRESS", meta=snap)` with the raw snapshot dict (already `{"completed": int, "total": int, "aggregate": dict}`). On generator exhaustion `return snap["aggregate"]` — the final aggregate, **not** the whole snapshot dict (this matches `BatchSimulator.run()`'s return shape and what `_aggregate_batch` returns).

**`save_games_task` return.** `return {"round_ids": [gr.id for gr in game_rounds]}`. Errors propagate as the task's exception (caught by `AsyncResult.state == "FAILURE"`).

### 4.3 View-layer helpers (`matches/views.py`)

```python
def _celery_state_to_job_status(state: str) -> str:
    """Map a Celery native state to the public SIM-10 vocabulary."""
    ...

def _build_batch_status_response(
    async_result,
    *,
    team_red_id: int | None,
    team_blue_id: int | None,
    arena_map_id: int | None,
) -> dict:
    """Build the polling JSON dict for a batch job from an AsyncResult."""
    ...

def _build_save_status_response(async_result) -> dict:
    """Build the polling JSON dict for a save job from an AsyncResult."""
    ...
```

These replace the deleted `_BATCH_JOBS` / `_SAVE_JOBS` dict reads. They are module-level (`_`-prefixed flat helpers, RV-01 pattern). Pure beyond the `AsyncResult` they receive — testable in isolation.

**`team_red_id` / `team_blue_id` / `arena_map_id` source for batch.** Celery does not persist task args on the result backend by default. The view re-reads them from `async_result.kwargs` if `result.expires` carries them — but the safest, no-leak approach is the **session stash + URL query carry-forward fallback**: the POST response includes `team_red_id` / `team_blue_id` / `arena_map_id`, and the polling JS sends them as URL query params on every subsequent `GET /matches/simulate-batch/status/<job_id>/?team_red_id=...&team_blue_id=...&arena_map_id=...`. The view reads them off `request.GET` and threads them into `_build_batch_status_response`. **Locked: query-param carry.** No new session-state, no DB lookup. JS change: when calling `poll(jobId)`, append `?team_red_id=...&team_blue_id=...&arena_map_id=...` to the URL using the values returned by the POST. Pinned in §8.1.

### 4.4 REST views (`matches/api_views.py`)

```python
from rest_framework import serializers, status, views
from rest_framework.response import Response

class SimulateBatchAPIView(views.APIView):
    """POST /api/simulate-batch/  → enqueue simulate_batch_task, return
    {job_id, team_red_id, team_red_name, team_blue_id, team_blue_name,
     arena_map_id, n}.
    """
    def post(self, request): ...

class SimulateBatchStatusAPIView(views.APIView):
    """GET /api/simulate-batch/<job_id>/  → polling JSON, identical shape
    to /matches/simulate-batch/status/<job_id>/.
    """
    def get(self, request, job_id: str): ...
```

**Authentication / permissions.** Both views inherit the REST_FRAMEWORK defaults (`SessionAuthentication`, `AllowAny`). Do not override `permission_classes` or `authentication_classes`. This is the API-02 deferred-auth precedent; documented in §13 scope-out and pinned by `TestAPIInheritsAllowAnyPermissions`.

**DRF input validation.** Use a `serializers.Serializer` subclass (NOT `BatchSimulateForm` — Forms-vs-Serializers is the locked DRF idiom):

```python
class SimulateBatchRequestSerializer(serializers.Serializer):
    team_red = serializers.IntegerField(min_value=1)
    team_blue = serializers.IntegerField(min_value=1)
    n = serializers.IntegerField(min_value=1, max_value=500)
    arena_map = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    master_seed = serializers.IntegerField(required=False, allow_null=True)
```

Defined inline in `api_views.py` (single-use). Validation failures return HTTP 400 with DRF's default error shape (`{<field>: ["<msg>", ...]}`). Same-team rejection (`team_red == team_blue`) and `roster_errors` checks live in `SimulateBatchAPIView.post` (mirrors `simulate_batch` UI view's checks), returning 400 with `{"detail": "<msg>"}`. The REST view's POST returns the same shape as the UI POST (§8.2). `master_seed` is accepted on the REST POST only (not the UI POST — UI form has no field for it); kept for test pinning and scripted runs (ADR-0013 rationale).

### 4.5 URL names — preserved and new

| URL name | Path | HTTP | Source |
|---|---|---|---|
| `simulate_batch` | `/matches/simulate-batch/` | GET (form) / POST (enqueue) | preserved |
| `batch_simulate_status` | `/matches/simulate-batch/status/<str:job_id>/` | GET | preserved |
| `save_batch_games` | `/matches/save-batch-games/` | POST | preserved |
| `save_batch_status` | `/matches/save-batch-status/<str:job_id>/` | GET | preserved |
| `api_simulate_batch` | `/api/simulate-batch/` | POST | **new** |
| `api_simulate_batch_status` | `/api/simulate-batch/<str:job_id>/` | GET | **new** |

**Mounting note.** `/api/simulate-batch/...` lives in `laserforce_simulator/api_urls.py` *after* `urlpatterns = router.urls`:

```python
urlpatterns = router.urls + [
    path("simulate-batch/", SimulateBatchAPIView.as_view(), name="api_simulate_batch"),
    path("simulate-batch/<str:job_id>/",
         SimulateBatchStatusAPIView.as_view(), name="api_simulate_batch_status"),
]
```

DRF `DefaultRouter` only registers ViewSets; `APIView` subclasses need plain `path()` entries appended.

---

## 5. Status mapping table

`_celery_state_to_job_status(state: str) -> str` — exact truth table:

| Input (Celery state) | Output |
|---|---|
| `"PENDING"` | `"running"` |
| `"STARTED"` | `"running"` |
| `"PROGRESS"` | `"running"` |
| `"SUCCESS"` | `"complete"` |
| `"FAILURE"` | `"error"` |
| `"REVOKED"` | `"error"` |
| `"RETRY"` (defensive — Celery emits this when a task is retried) | `"running"` |
| anything else (defensive fallback) | `"running"` |

The fallback to `"running"` is deliberate: the polling UI does not break on an unknown state (it just keeps polling).

**Expiry asymmetry (locked, CONTEXT.md `Job id`).** Polling a Job id whose result has expired (1h TTL) resolves to Celery `PENDING` — indistinguishable from a never-submitted id. The mapping above maps `PENDING` → `"running"`, so the UI polls forever on an expired id. The CONTEXT.md entry documents this. No new fallback path.

---

## 6. Test classes — DELETED / PRESERVED / NEW

### 6.1 DELETED (test classes retired with `_BATCH_JOBS` / `_SAVE_JOBS`)

All in `laserforce_simulator/matches/tests/views_tests.py`. Names verified by grep on the current branch (line numbers as of the contract date):

| Line | Class | Reason |
|---|---|---|
| 761 | `TestSim10SimulateBatchPostReturnsJson` | Tests the in-process Job dict POST contract; rewritten in `test_api03_views.py` as `TestSimulateBatchPostUIReturnsJobId`. |
| 840 | `TestSim10BatchSimulateStatusShape` | Tests the `_BATCH_JOBS` dict shape; rewritten as `TestBatchSimulateStatusEager`. |
| 897 | `TestSim10BatchSimulateStatusLifecycle` | Tests the daemon-thread lifecycle; rewritten as `TestBatchSimulateStatusEager` (state transitions under EAGER). |
| 970 | `TestSim10BatchSimulateStatusErrorPath` | Tests `_BATCH_JOBS[…]["status"] == "error"`; rewritten as `TestBatchSimulateStatusError`. |
| 1014 | `TestSim10BatchSimulateStatusNotFound` | Tests the `_BATCH_JOBS` `not_found` branch; the Celery equivalent is `TestBatchSimulateStatusUnknownJobId` (PENDING → running with completed=0). |
| 1031 | `TestSim10SessionHandoverWritesOnceOnComplete` | Tests the SIM-10 session guard against the `_BATCH_JOBS` dict; rewritten as `TestSessionHandoverPreservedOnComplete`. |
| 1161 | `TestSim11WorkersFor` | Tests `_workers_for(n)` — the helper is **deleted** (workers=1 always inside the task). No replacement. |
| 1218 | `TestSim11RunBatchJobPassesWorkers` | Tests `_run_batch_job` threading `workers` into `run_incremental` — both `_run_batch_job` and `_workers_for` are deleted. No replacement. |

### 6.2 REWRITTEN against Celery (TestSim09BatchArenaMapPlumbing class)

`TestSim09BatchArenaMapPlumbing` at line 472 mixes tests of the SIM-09 arena_map plumbing with tests of the SIM-10 `_run_save_job` daemon. The arena_map plumbing is the load-bearing contract; the daemon is retired. Per-method handling:

| Method | Action |
|---|---|
| `test_run_save_job_threads_arena_map_to_save_games` | Rewrite as `test_save_games_task_threads_arena_map_to_save_games` — call `save_games_task.apply(args=(...))` under EAGER, assert `save_games` received `arena_map`. Lives in `test_api03_tasks.py::TestSaveGamesTaskWithMap`. |
| `test_run_save_job_none_arena_map_id_passes_none` | Rewrite analogously in `TestSaveGamesTaskWithMap`. |
| `test_save_batch_games_view_threads_arena_map_id_into_worker_args` | Rewrite as `test_save_batch_games_enqueues_save_games_task_with_arena_map` in `test_api03_views.py::TestSaveBatchGamesPost` — patch `save_games_task.delay` and assert it is called with the session-stashed `arena_map_id`. |
| `test_run_save_job_stale_arena_map_id_treated_as_none` | Rewrite analogously in `TestSaveGamesTaskWithMap`. |
| Any other method in this class testing batch_simulate_status / session handover with arena_map | Folded into `TestBatchSimulateStatusEager` (asserts `arena_map_id` is carried through the polling response correctly). |

The class can be deleted in full once the rewrites land; the rewrites live in the new `test_api03_*` files for organisational clarity.

### 6.3 PRESERVED unchanged in `test_sim10_incremental.py`

The whole file is unchanged. Verified classes (re-read):

- `TestChunkSizeFor`
- `TestRunIncrementalSnapshotShape`
- `TestRunIncrementalFinalEqualsRun`
- `TestRunIncrementalSerialEqualsParallelAtEveryBoundary`
- `TestRunIncrementalNZero`
- `TestRunIncrementalFailFast`
- `TestRunIncrementalDriveRun`

These pin the `BatchSimulator.run_incremental` invariants (chunk-size table, partial-equals-final, serial == parallel at every boundary, fail-fast, n=0, run-drives-incremental). They test the **simulator**, not the job machinery. They keep passing without change.

### 6.4 NEW

#### `matches/tests/test_api03_tasks.py`

| Class | Pins |
|---|---|
| `TestSimulateBatchTaskHappyPath` | Calling `simulate_batch_task.apply(args=(red.id, blue.id, 2, None, 42))` under EAGER returns a `Task` whose `.result` is the final aggregate dict (the `_aggregate_batch` output for the 2 games); `.state == "SUCCESS"`. |
| `TestSimulateBatchTaskProgressUpdates` | A `before_task_publish` / `task_received` / `task_success` Celery signal sequence under EAGER fires `update_state(state="PROGRESS", meta=snap)` at least once for `n=2` and the meta payload matches the `run_incremental` snapshot shape (`{completed, total, aggregate}`). Spy on `self.update_state` by patching `Task.update_state` on the bound task. |
| `TestSimulateBatchTaskWithMap` | `arena_map_id=<real id>` resolves the ArenaMap and threads it as `arena_map=` into `run_incremental`. Stale `arena_map_id` (no such row) falls back to `None` (mirrors retired `_run_batch_job` semantics). |
| `TestSimulateBatchTaskDeterminism` | Same `master_seed` → identical `.result` aggregate across two invocations. |
| `TestSimulateBatchTaskFailFast` | Patching `_simulate_round` to raise `ValueError` causes the task to surface `FAILURE` (under `CELERY_TASK_EAGER_PROPAGATES`, the exception propagates as a Python exception out of `.apply()`; test uses `pytest.raises(ValueError)`). |
| `TestSaveGamesTaskHappyPath` | `save_games_task.apply(args=(red.id, blue.id, [(12345, False)], 1, None))` returns `.state == "SUCCESS"` with `.result == {"round_ids": [<int>]}`; `GameRound.objects.get(id=<int>)` exists. |
| `TestSaveGamesTaskWithMap` | Same as above with a real `arena_map_id`; persisted `GameRound.arena_map_id` matches. Also covers the `arena_map_id=None` and stale-id branches (consolidates the four rewritten tests from §6.2). |
| `TestSaveGamesTaskInvalidTeam` | `team_red_id=9_999_999` raises `Team.DoesNotExist` from `Team.objects.get(...)`; task state is `FAILURE` (or under `EAGER_PROPAGATES=True`, raises out of `.apply()`). |

#### `matches/tests/test_api03_views.py`

| Class | Pins |
|---|---|
| `TestSimulateBatchPostUIReturnsJobId` | POST `/matches/simulate-batch/` returns 200 + `{job_id, team_red_id, team_red_name, team_blue_id, team_blue_name, arena_map_id, n}`; `job_id` is a non-empty string. Patches `simulate_batch_task.delay` to assert the call args. |
| `TestBatchSimulateStatusEager` | Under EAGER, a status poll after the POST shows `status="complete"`, `completed == n`, `partial == final aggregate` (matching `BatchSimulator.run(...).` for the same `master_seed`), `team_red_id` / `team_blue_id` / `arena_map_id` echoed from query params. |
| `TestBatchSimulateStatusError` | A task that raised propagates as `status="error"` with `error == str(exc)` (mapped at the view boundary, raw `FAILURE` is not exposed). |
| `TestBatchSimulateStatusUnknownJobId` | GET `/matches/simulate-batch/status/<bogus-id>/` returns 200 with `{status: "running", completed: 0, total: 0, partial: null, error: null, team_red_id: null, team_blue_id: null, arena_map_id: null}` — Celery `PENDING` for an unknown id maps to `"running"`, locked. (No more `{"status": "not_found"}, status=404` branch — that was the `_BATCH_JOBS` dict miss.) |
| `TestSaveBatchGamesPost` | POST `/matches/save-batch-games/` returns 200 + `{job_id}`. Patches `save_games_task.delay` to assert the seeds / arena_map_id from `request.session["batch_seeds"]` are threaded into the task call. Empty-session 400 branch + missing-seeds 400 branch preserved. |
| `TestSaveBatchStatusEager` | Under EAGER, a status poll after a real `save_games_task` run returns `{status: "complete", error: null, round_ids: [<int>, ...]}`. (Shape matches the SIM-10 `_SAVE_JOBS` `done` dict exactly — locked: legacy `"done"` maps from `SUCCESS` here.) |
| `TestSimulateBatchAPIPost` | POST `/api/simulate-batch/` with `{"team_red": ..., "team_blue": ..., "n": 2}` returns 200 + `{job_id, team_red_id, team_red_name, team_blue_id, team_blue_name, arena_map_id, n}`. Same-team rejection returns 400. Invalid id returns 400 from serializer validation. |
| `TestSimulateBatchAPIStatusEager` | Under EAGER, GET `/api/simulate-batch/<job_id>/` returns the same JSON shape as the UI `batch_simulate_status` endpoint (locked: identical shape). |
| `TestSimulateBatchAPIStatusUnknownJobId` | GET `/api/simulate-batch/<bogus-id>/` returns 200 with the running-with-nulls shape (same as the UI endpoint — locked). |
| `TestCeleryStateMappingHelper` | Exhaustive truth table of `_celery_state_to_job_status` (§5). Pure-unit, no DB. |
| `TestSessionHandoverPreservedOnComplete` | The SIM-10 session guard is preserved: the FIRST poll observing `complete` writes `request.session["batch_seeds"]` with the `job_id` guard marker; subsequent polls observing `complete` skip the write (so user-mutations between polls survive). Locked: re-uses the SIM-10 session shape exactly. |
| `TestAPIInheritsAllowAnyPermissions` | POST `/api/simulate-batch/` from an unauthenticated client succeeds (returns 200, not 401/403) — documents the API-02 deferred-auth precedent and prevents accidental future regression. |

---

## 7. Settings keys (additions to `laserforce_simulator/settings.py`)

Append after the existing `REST_FRAMEWORK` block:

```python
# --- API-03: Celery + Redis (ADR-0013) ---
CELERY_BROKER_URL = config(
    "CELERY_BROKER_URL", default="redis://localhost:6379/0"
)
CELERY_RESULT_BACKEND = config(
    "CELERY_RESULT_BACKEND", default="redis://localhost:6379/0"
)
CELERY_RESULT_EXPIRES = 3600  # 1 hour, per PLAN.md
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = "UTC"
CELERY_TASK_ALWAYS_EAGER = config(
    "LF_CELERY_EAGER", default=False, cast=bool
)
CELERY_TASK_EAGER_PROPAGATES = True
```

**Pytest configuration.** `pytest.ini` (or `pyproject.toml` if used) needs `LF_CELERY_EAGER=1` in its env block, OR `conftest.py` sets `os.environ.setdefault("LF_CELERY_EAGER", "1")` before Django settings are loaded. **Locked: `conftest.py` env-var set** (simpler, no separate file). Add to `laserforce_simulator/conftest.py` (or `matches/tests/conftest.py` — either works, the env var is read at settings-load time):

```python
import os
os.environ.setdefault("LF_CELERY_EAGER", "1")
```

---

## 8. Polling JSON shape — the locked seam

### 8.1 Batch flow (`GET /matches/simulate-batch/status/<job_id>/?team_red_id=<int>&team_blue_id=<int>&arena_map_id=<int|>`)

**View body (sketch):**

```python
def batch_simulate_status(request, job_id):
    async_result = AsyncResult(job_id)
    team_red_id = _int_or_none(request.GET.get("team_red_id"))
    team_blue_id = _int_or_none(request.GET.get("team_blue_id"))
    arena_map_id = _int_or_none(request.GET.get("arena_map_id"))

    response = _build_batch_status_response(
        async_result,
        team_red_id=team_red_id,
        team_blue_id=team_blue_id,
        arena_map_id=arena_map_id,
    )

    # SIM-10 session handover guard — preserved verbatim from the
    # pre-API-03 view. The "first poll observing complete" semantics
    # are unchanged; only the source of `aggregate` is now
    # async_result.result instead of _BATCH_JOBS[job_id]["partial"].
    if response["status"] == "complete":
        existing = request.session.get("batch_seeds") or {}
        if existing.get("job_id") != job_id:
            agg = response.get("partial") or {}
            request.session["batch_seeds"] = {
                "job_id": job_id,
                "team_red_id": team_red_id,
                "team_blue_id": team_blue_id,
                "arena_map_id": arena_map_id,
                "avg_seeds": agg.get("avg_seeds", []),
                "outlier_seeds": agg.get("outlier_seeds", []),
            }
            request.session.modified = True

    return JsonResponse(response)
```

**Locked response shape:**

```python
{
    "status": "running" | "complete" | "error",  # mapped from Celery state
    "completed": int,    # from snap["completed"] when PROGRESS/SUCCESS, else 0
    "total": int,        # from snap["total"] when PROGRESS/SUCCESS, else 0
    "partial": dict | None,  # see below
    "error": str | None,     # str(async_result.info) when FAILURE, else None
    "team_red_id": int | None,
    "team_blue_id": int | None,
    "arena_map_id": int | None,
}
```

**`partial` source table:**

| Celery state | `partial` source |
|---|---|
| `PENDING` | `None` |
| `STARTED` | `None` |
| `PROGRESS` | `async_result.info["aggregate"]` (the snapshot's aggregate dict — emitted via `self.update_state(state="PROGRESS", meta=snap)`) |
| `SUCCESS` | `async_result.result` (the final aggregate dict — same shape as `snap["aggregate"]` for the last snapshot) |
| `FAILURE` | `None` (the `error` key carries the diagnostic) |
| `REVOKED` | `None` |

**`completed` / `total` source table:**

| Celery state | `completed` | `total` |
|---|---|---|
| `PENDING` | `0` | `0` |
| `STARTED` | `0` | `0` |
| `PROGRESS` | `async_result.info["completed"]` | `async_result.info["total"]` |
| `SUCCESS` | `async_result.result["n"]` (final aggregate has `n` == total games run) | `async_result.result["n"]` |
| `FAILURE` | `0` | `0` |
| `REVOKED` | `0` | `0` |

**`error` source:** `str(async_result.info)` when state is `FAILURE` or `REVOKED`, else `None`. Under Celery, `async_result.info` is the exception instance for `FAILURE`; `str(exc)` matches the pre-API-03 `_BATCH_JOBS[…]["error"]` contract exactly.

### 8.2 Save flow (`GET /matches/save-batch-status/<job_id>/`)

**Locked response shape** (matches SIM-10 `save_batch_status` exactly):

```python
{
    "status": "running" | "complete" | "error",  # mapped; SUCCESS → "complete"
    "error": str | None,
    "round_ids": list[int],  # async_result.result["round_ids"] on SUCCESS, [] otherwise
}
```

**Mapping note.** The pre-API-03 `_SAVE_JOBS` dict used `"done"` (not `"complete"`) for the success case (see `views.py` line 65). **Decision: rename to `"complete"`** for vocabulary consistency with the batch flow. The save-status polling JS in `batch_simulate.html` (lines 357 onwards, around the `pollStatus` save-job branch) reads `data.status === "done"` — this becomes `data.status === "complete"` in the same PR. Verified by reading the template — the save-job block is small and the change is local. **If preserving `"done"` is preferred** for zero-JS-change, see §15 Open questions.

---

## 9. POST response shapes

### 9.1 UI `POST /matches/simulate-batch/`

```python
{
    "job_id": "<celery_task_id>",
    "team_red_id": int,
    "team_red_name": str,
    "team_blue_id": int,
    "team_blue_name": str,
    "arena_map_id": int | None,
    "n": int,
}
```

Unchanged from the current SIM-10 contract.

### 9.2 UI `POST /matches/save-batch-games/`

```python
{"job_id": "<celery_task_id>"}
```

Unchanged.

### 9.3 REST `POST /api/simulate-batch/`

```python
{
    "job_id": "<celery_task_id>",
    "team_red_id": int,
    "team_red_name": str,
    "team_blue_id": int,
    "team_blue_name": str,
    "arena_map_id": int | None,
    "n": int,
}
```

Identical shape to the UI POST (locked — same client code can read either).

**Request body (DRF):**

```json
{
    "team_red": <int>,
    "team_blue": <int>,
    "n": <int 1..500>,
    "arena_map": <int|null>,        // optional
    "master_seed": <int|null>       // optional, test-only
}
```

Validation errors → 400 with DRF's default per-field error shape.
Same-team / invalid-roster errors → 400 with `{"detail": "<msg>"}`.

---

## 10. Template — `templates/matches/batch_simulate.html`

**Locked promise: the template is NOT modified except for the polling URL construction.**

DOM ids the polling JS reads (preserved unchanged so the UI keeps working):

- `batch-form`, `batch-progress-container`, `batch-progress-bar`, `batch-progress-label`, `batch-error`, `batch-results`, `batch-results-n`, `batch-elapsed`
- `batch-red-win-pct`, `batch-blue-win-pct`, `batch-ties`, `batch-red-ties-secondary`, `batch-blue-ties-secondary`, `batch-red-wins`, `batch-blue-wins`, `batch-red-wins-mirror`, `batch-blue-wins-mirror`
- `batch-avg-red-score`, `batch-avg-blue-score`, `batch-avg-red-survivors`, `batch-avg-blue-survivors`
- `batch-side-advantage`, `batch-red-side-win-pct`, `batch-blue-side-win-pct`, `batch-red-side-wins`, `batch-blue-side-wins`, `batch-avg-red-side-score`, `batch-avg-blue-side-score`, `batch-side-ties`
- `scoreChart` (canvas), `batch-save-games`, `avgN`, `outlierN`, `saveStatus`
- CSS class hooks: `batch-team-red-name`, `batch-team-blue-name`, `batch-red-wins-mirror`, `batch-blue-wins-mirror`

**JS changes (minimal — locked):**
1. `poll(jobId)` URL becomes `STATUS_URL_BASE + jobId + '/?team_red_id=' + ... + '&team_blue_id=' + ... + '&arena_map_id=' + ...` (the three values stashed on POST response).
2. If §8.2 chooses to rename `"done"` → `"complete"` on the save-job polling branch, update the one string compare in the save-status poller. (See §15 Open questions.)
3. Drop the `'not_found'` branch in `poll` (the Celery path returns 200 + running, never 404).

All other JS logic — `paintResults(aggregate)`, `buildHistogram`, `updateProgress`, the histogram + side-advantage paint paths — is unchanged because the aggregate dict shape it consumes is unchanged.

---

## 11. Determinism contract

**The SIM-07 / SIM-08 / SIM-10 internal-determinism contract is preserved unchanged.**

- Same `master_seed` + Orientation + rosters + map ⇒ identical games at every chunk boundary.
- Serial == parallel at every chunk boundary (`run_incremental` invariant — untouched).
- Faithful Replay holds (`BatchSimulator.replay_round` untouched).
- Celery-vs-direct execution path produces **identical games** when `CELERY_TASK_ALWAYS_EAGER = True` (the task is just `BatchSimulator().run_incremental(...)` in the same process).
- Celery-vs-direct produces **identical aggregate output** when not EAGER (the task is the same code path; only the executor differs).

No mechanic, no constant, no event ordering, no event metadata, and no persisted column changes. **No Score Calibration re-baseline.** This is locked by the §13 scope-out and by the preservation of `BatchSimulator.run_incremental` and `_aggregate_batch`.

---

## 12. Scope-out (locked — do not touch)

- **No `fly.toml` change.** ADR-0013 mentions the `processes = ["app", "worker"]` and Upstash secret addition as deployment context — they are deferred to a separate deploy task. The PR for API-03 does **not** edit `fly.toml`.
- **No `Dockerfile` change.** Worker entrypoint variant deferred to the deploy task.
- **No CI Redis provisioning.** Tests run under `CELERY_TASK_ALWAYS_EAGER = True`. The `ci.yml` workflow needs no broker.
- **No token auth on `/api/`.** API-02 deferred token auth; API-03 inherits `AllowAny`. Locked.
- **No `master_seed` UI exposure.** Plumbed through `simulate_batch_task` signature for test pinning only. The UI POST does not accept it; the REST POST accepts it but the OpenAPI/docs surface is intentionally minimal — it's a test/scripted-run convenience, not a user-facing knob.
- **No cancel-in-flight UX.** Celery supports `AsyncResult(id).revoke(terminate=True)`, but no UI surface ships in API-03.
- **No persisting Job past 1h.** `CELERY_RESULT_EXPIRES = 3600` is the contract. No `Job` model, no DB row, no cron sweep.
- **No `score_averages` CLI change.** The management command does not enqueue a Celery task; it stays a direct foreground `BatchSimulator().run(...)` caller. (Verified — the command lives in `matches/management/commands/score_averages.py` and only touches `BatchSimulator.run`.)
- **No simulation mechanics change.** Executor swap only. No Score Calibration re-baseline.
- **No new CONTEXT.md term** beyond the three already added (`Job`, `Job id`, `Job status`). The `Batch run` term mentions Celery; no edit needed.
- **No new ADR** beyond ADR-0013.
- **No `_aggregate_batch` change.** Stays a `BatchSimulator` `@staticmethod`.
- **No `BatchSimulator.run_incremental` signature change.** The Celery task is the caller, not a wrapper that mutates the signature.
- **No `BatchSimulator.run` change** (still the consumer of `run_incremental`, unchanged).
- **No `BatchSimulator.save_games` change.**
- **No `BatchSimulateForm` change.** (The UI POST still uses the Form for input validation; only the REST POST uses a Serializer.)

---

## 13. House-style invariants (CLAUDE.md)

- **No `cd` ever** — git runs from the repo root.
- **No destructive git commands** (`git stash`, `git checkout --`, `git reset --hard`, `git restore .`, `git clean -f`) — these have corrupted agent worktrees before.
- **PowerShell shell** — no bash-isms (`&&`, `;` chaining, `$VAR` env-var, `2>/dev/null`).
- **Black-format every changed Python file** (`python -m black laserforce_simulator/<paths>`).
- **No Unicode box-drawing or non-ASCII** in scripts — Windows cp1252 will crash. ASCII only (`+--+`, `|`, `+--+`).
- **Type hints on every new function signature.**
- **Tests use the `make_team_with_slots(prefix)` helper from `matches/tests/conftest.py`** for Team / Player fixtures.

---

## 14. Locked names — final lookup table

| Surface | Name |
|---|---|
| Celery app variable | `celery_app` (in `laserforce_simulator/celery.py`) |
| `__init__.py` re-export | `from .celery import app as celery_app` |
| Task 1 callable | `simulate_batch_task` |
| Task 1 `name=` | `"matches.simulate_batch"` |
| Task 2 callable | `save_games_task` |
| Task 2 `name=` | `"matches.save_games"` |
| Helper 1 | `_celery_state_to_job_status(state: str) -> str` |
| Helper 2 | `_build_batch_status_response(async_result, *, team_red_id, team_blue_id, arena_map_id) -> dict` |
| Helper 3 | `_build_save_status_response(async_result) -> dict` |
| REST view 1 | `SimulateBatchAPIView(views.APIView)` |
| REST view 2 | `SimulateBatchStatusAPIView(views.APIView)` |
| Request serializer | `SimulateBatchRequestSerializer(serializers.Serializer)` (in `api_views.py`, inline single-use) |
| URL name (REST POST) | `api_simulate_batch` |
| URL name (REST GET) | `api_simulate_batch_status` |
| URL path (REST POST) | `/api/simulate-batch/` |
| URL path (REST GET) | `/api/simulate-batch/<str:job_id>/` |
| URL name (UI POST) | `simulate_batch` (preserved) |
| URL name (UI status GET) | `batch_simulate_status` (preserved) |
| URL name (UI save POST) | `save_batch_games` (preserved) |
| URL name (UI save GET) | `save_batch_status` (preserved) |
| Tests file (tasks) | `matches/tests/test_api03_tasks.py` |
| Tests file (views) | `matches/tests/test_api03_views.py` |
| `requirements.txt` line | `celery[redis]>=5.3` |
| Env var (broker) | `CELERY_BROKER_URL` (default `redis://localhost:6379/0`) |
| Env var (backend) | `CELERY_RESULT_BACKEND` (default `redis://localhost:6379/0`) |
| Env var (test/dev eager) | `LF_CELERY_EAGER` (cast bool, default `False`) |
| Job expiry constant | `CELERY_RESULT_EXPIRES = 3600` |
| Status vocabulary | `"running"` / `"complete"` / `"error"` (CONTEXT.md `Job status`) |

---

## 15. Open questions

The following points were not fully settled by the inputs and need a human decision before the Tests / Code / Docs agents diverge. If the parent prefers a default, the contract proposes one — flagged inline.

1. **Save-status vocabulary rename: `"done"` → `"complete"`.**
   The retired `_SAVE_JOBS` dict used `"done"` (see `views.py` line 65) for the success case. The batch flow uses `"complete"`. The contract proposes **renaming to `"complete"`** for vocabulary consistency (CONTEXT.md `Job status` says `complete`, not `done`) and updating the one save-status JS string compare in the same PR. **Alternative:** keep `"done"` on the save flow for zero-JS-change and have `_build_save_status_response` map `SUCCESS → "done"` while `_build_batch_status_response` maps `SUCCESS → "complete"`. **Default if no answer:** rename to `"complete"` (consistency wins over a 1-line JS diff).

2. **`team_red_id` / `team_blue_id` / `arena_map_id` carry-forward on the polling endpoint.**
   Celery does not persist task args on the result backend in a documented way. The contract proposes **query-param carry** (POST response includes them, polling JS appends them to every GET). **Alternative 1:** stash on `request.session` keyed by `job_id` (clutters session; survives page reload). **Alternative 2:** store on `async_result.kwargs` if Celery is configured to persist them (`result_extended = True` setting — adds backend cost). **Default if no answer:** query-param carry (simplest, no extra Celery config, no session bloat). The REST endpoint follows the same pattern (`GET /api/simulate-batch/<job_id>/?team_red_id=...`).

3. **Where the EAGER env-var set lives.**
   The contract proposes `conftest.py` (`os.environ.setdefault("LF_CELERY_EAGER", "1")` at module load). **Alternative:** add `env =` lines to `pytest.ini`. **Default if no answer:** `conftest.py` (no new file convention, mirrors existing pytest setup).

4. **`pytest-celery` plugin?**
   The ADR explicitly rejects the `celery_worker` fixture path. The contract assumes **no `pytest-celery` dependency** (EAGER mode is sufficient). Confirming: do **not** add `pytest-celery` to `requirements.txt`. If a future test needs the real broker path, that's a separate decision.

5. **`run_incremental` PROGRESS emit cadence.**
   The contract says "emit each snapshot via `self.update_state(state='PROGRESS', meta=snap)`" — that's once per chunk boundary (≈50 emits per run per `_chunk_size_for`). Celery's Redis backend supports this cadence trivially. **No throttle proposed.** If broker round-trip cost ever shows up in profiling, a throttle is additive and reversible.
