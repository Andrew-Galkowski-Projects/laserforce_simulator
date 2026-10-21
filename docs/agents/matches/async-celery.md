# matches: Async execution (Celery)

API-03 batch jobs on Celery + Redis, status mapping, polling JSON, settings.
Moved verbatim from `laserforce_simulator/matches/CLAUDE.md`; the app index there points here.

## Async execution (Celery)

**API-03 (May 2026)** retires the SIM-10 / SIM-11 in-process Job dicts (`_BATCH_JOBS`, `_SAVE_JOBS`, `_JOBS_LOCK`, `_run_batch_job`, `_run_save_job`, `_workers_for`) and unifies both UI batch flows (`/matches/simulate-batch/`, `/matches/save-games/`) plus the new REST endpoint pair onto **two Celery `@shared_task`s** backed by **Redis**. This is an **executor swap, not a mechanics change** — `BatchSimulator.run_incremental` / `_aggregate_batch` / `save_games` are untouched, the SIM-07/08/10 internal-determinism contract holds in form (same `master_seed` + Orientation + rosters + map ⇒ identical games at every chunk boundary; serial == parallel; faithful Replay; Celery-vs-direct paths produce identical games under `CELERY_TASK_ALWAYS_EAGER`), and **no Score Calibration re-baseline obligation**. See [ADR-0013](../../adr/0013-async-batch-execution-via-celery-redis.md) and GLOSSARY.md (**Job**, **Job id**, **Job status**, `### Async execution`).

**Tasks (`matches/tasks.py`).** Both are `@shared_task(bind=True)`:
- `simulate_batch_task(self, team_red_id, team_blue_id, n, arena_map_id=None, master_seed=None) -> dict` (pinned `name="matches.simulate_batch"`) — resolves teams + optional `ArenaMap` (stale `arena_map_id` falls back to `None` via `try/except ArenaMap.DoesNotExist`, preserving the SIM-09/10 stale-id semantics), drives `BatchSimulator().run_incremental(..., workers=1)`, emits each snapshot via `self.update_state(state="PROGRESS", meta=snap)` where `snap == {"completed": int, "total": int, "aggregate": dict}`, and on generator exhaustion returns the final `snap["aggregate"]` (matching `BatchSimulator.run()`'s return shape exactly). No `try/finally` (CI-01 removed its `close_old_connections()`; see **CI-01** below).
- `save_games_task(self, team_red_id, team_blue_id, seeds, n, arena_map_id=None) -> dict` (pinned `name="matches.save_games"`) — replays the carried `(seed, flipped)` pairs via `BatchSimulator().save_games(...)` and returns `{"round_ids": [gr.id for gr in game_rounds]}`. Same arena_map fallback as `simulate_batch_task`. **ANG-01b:** gains a keyword-only `manager_id: int | None = None`; when set, every returned round is stamped (`GameRound.objects.filter(id__in=round_ids).update(manager_id=...)`) after `save_games` returns — the simulator is untouched and the positional 5-arg `.apply(args=...)` still works. Both the HTML `save_batch_games` and the API save pass the saver, so **batch-saved rounds are now owned by the saver, not Unmanaged** (behaviour change).

**Worker concurrency model.** Each task is **serial inside its body** (`workers=1` baked in). Horizontal throughput comes from running multiple Celery workers — `celery -A laserforce_simulator worker --concurrency 4` — one concurrency knob (`--concurrency`), not two stacked. The SIM-11 `_workers_for(n)` in-process helper is **retired** as part of this swap; the threshold/cap heuristic was tied to a single-process `ProcessPoolExecutor` and does not apply to broker-distributed tasks. **GEN-01 (2026-06-26) partially revives intra-task parallelism for `simulate_batch_task` ONLY:** the SIM-11 `_workers_for(n)` heuristic (`1` for `n < 50`, else `min(os.cpu_count() or 1, n)` — **all cores**, bounded only by the game count; the old SIM-11 4-cap is dropped since the `n < 50` gate already keeps every test/CI batch serial) is restored in `matches/tasks.py` and threaded as `workers=_batch_workers(n)` into `run_incremental` — so a **single** large batch run uses multiple cores (API-03's `--concurrency` only parallelises *across* tasks, never *within* one run). `_batch_workers(n)` **daemon-guards** it: `multiprocessing.current_process().daemon` ⇒ `1`, because a Celery **prefork** worker runs each task in a daemonic child that **may not spawn subprocesses** (`ProcessPoolExecutor` would raise `AssertionError: daemonic processes are not allowed to have children`). So under prefork the task stays serial (unchanged from API-03, scaling via `--concurrency`); under **EAGER** (task runs in the Django process) or a **`solo`/`threads`** pool (non-daemonic) the run goes multi-core. `n < 50` stays serial regardless (every batch test uses `n ≤ 10`, so all stay serial — no subprocess spawn in CI). Determinism is unchanged — the SIM-10 serial==parallel-at-every-boundary contract holds for any `workers`, so **no Score Calibration re-baseline**. `save_games_task` is **unchanged** (it replays specific avg/outlier seeds serially, small N). See [ADR-0013](../../adr/0013-async-batch-execution-via-celery-redis.md) (GEN-01 addendum).

**View-layer helpers (`matches/views.py`).** Three flat `_`-prefixed module-level helpers (RV-01 pattern) replace the deleted dict reads, all built on `celery.result.AsyncResult`:
- `_celery_state_to_job_status(state: str) -> str` — pure status-mapping helper (truth table below).
- `_build_batch_status_response(async_result, *, team_red_id, team_blue_id, arena_map_id) -> dict` — assembles the SIM-10-shaped polling JSON for a batch job.
- `_build_save_status_response(async_result) -> dict` — same for a save job. **ANG-01b:** renamed public `build_save_status_response` (the underscore name is kept as an alias); the API save-status endpoint returns it verbatim.

**Status mapping (locked at the view boundary — never expose raw Celery states).**

| Celery state | Mapped `status` |
|---|---|
| `PENDING` / `STARTED` / `PROGRESS` / `RETRY` / unknown | `"running"` |
| `SUCCESS` | `"complete"` |
| `FAILURE` / `REVOKED` | `"error"` |

The defensive `"running"` fallback for unknown states keeps the polling UI from breaking on a Celery state it does not recognise.

**Polling JSON — batch flow (`GET /matches/simulate-batch/status/<str:job_id>/?team_red_id=&team_blue_id=&arena_map_id=`, URL name `batch_simulate_status`).** Shape preserved verbatim from SIM-10: `{"status", "completed", "total", "partial", "error", "team_red_id", "team_blue_id", "arena_map_id"}`. Per-key sourcing:
- `status` — `_celery_state_to_job_status(async_result.state)`.
- `completed` / `total` — `async_result.info["completed"]` / `["total"]` on `PROGRESS`; `async_result.result["n"]` on `SUCCESS`; `0` / `0` everywhere else.
- `partial` — `async_result.info["aggregate"]` on `PROGRESS`; `async_result.result` on `SUCCESS` (the final aggregate dict, same shape as `snap["aggregate"]`); `None` elsewhere.
- `error` — `str(async_result.info)` on `FAILURE`/`REVOKED` (Celery exposes the exception instance via `.info`; `str(exc)` matches the pre-API-03 `_BATCH_JOBS[…]["error"]` contract); `None` otherwise.
- `team_red_id` / `team_blue_id` / `arena_map_id` — **carried via query params** on every polling GET (the POST response includes them; the polling JS appends them to every status URL). Celery does not persist task kwargs on the result backend by default and result-backend expiry (1h) would lose them anyway; query-param carry keeps the view stateless and avoids `result_extended=True` backend cost.

**Polling JSON — save flow (`GET /matches/save-batch-status/<str:job_id>/`, URL name `save_batch_status`).** Shape `{"status", "error", "round_ids"}`. `round_ids` is `async_result.result["round_ids"]` on `SUCCESS` and `[]` otherwise. **Vocabulary rename:** the pre-API-03 `_SAVE_JOBS` success status was the string `"done"`; API-03 renames it to `"complete"` for consistency with the batch flow and with the GLOSSARY.md `Job status` term (the polling JS in `batch_simulate.html` for the save branch updates its one string compare from `data.status === "done"` to `=== "complete"`).

**POST response shapes (preserved + new).**
- `POST /matches/simulate-batch/` (UI) → `{"job_id", "team_red_id", "team_red_name", "team_blue_id", "team_blue_name", "arena_map_id", "n"}` — unchanged from SIM-10.
- `POST /matches/save-batch-games/` (UI) → `{"job_id"}` — unchanged.
- `POST /api/simulate-batch/` (REST, **new**) → identical shape to the UI batch POST (same client code can read either).

**Session handover preserved verbatim.** The SIM-10 single-write session guard for `request.session["batch_seeds"]` is unchanged: the FIRST poll observing `status == "complete"` (where `request.session.get("batch_seeds", {}).get("job_id") != job_id`) copies `avg_seeds` / `outlier_seeds` / `team_red_id` / `team_blue_id` / `arena_map_id` plus the `"job_id"` guard marker into the session and sets `request.session.modified = True`; subsequent polls observing `complete` check the guard and skip the write so user-mutations between polls survive. Only the source of `aggregate` changes — it is now read from `async_result.result` instead of `_BATCH_JOBS[job_id]["partial"]`. `save_batch_games` reads the same session shape and is **unchanged** beyond swapping its executor from `threading.Thread(target=_run_save_job)` to `save_games_task.delay(...)`.

**Expiry asymmetry (`CELERY_RESULT_EXPIRES = 3600`, 1 hour).** Polling a Job id whose result has expired resolves to Celery `PENDING` — indistinguishable from a never-submitted id. The status mapping above maps `PENDING` → `"running"`, so the UI polls forever on an expired id. Documented on the GLOSSARY.md `Job id` term; no special-case fallback path in the view layer.

**REST surface (API-03).** Two new APIView subclasses in `matches/api_views.py` mounted off `laserforce_simulator/api_urls.py` after the DRF `DefaultRouter` urls (router only handles ViewSets — `APIView` needs plain `path()` entries):
- `POST /api/simulate-batch/` (URL name `api_simulate_batch`, `SimulateBatchAPIView`) — accepts `{team_red, team_blue, n, arena_map?, master_seed?}` validated by an inline `SimulateBatchRequestSerializer(serializers.Serializer)` (Forms-vs-Serializers is the locked DRF idiom — the UI POST still uses `BatchSimulateForm`). Same-team rejection and `roster_errors` mirror the UI view, returning 400 with `{"detail": "<msg>"}`. Validation errors return DRF's default per-field shape. *(**Superseded by ANG-01a:** both responses are now enveloped — see **REST API → Batch endpoints** above; the Forms-vs-Serializers idiom is overturned for new endpoints by ADR-0039, this serializer is grandfathered.)* `master_seed` is REST-only (UI form has no field for it) and is plumbed for test pinning / scripted-run convenience, **not** a user-facing knob.
- `GET /api/simulate-batch/<str:job_id>/` (URL name `api_simulate_batch_status`, `SimulateBatchStatusAPIView`) — returns the identical polling JSON shape as the UI `batch_simulate_status` endpoint. Same query-param carry pattern.

Both REST views **inherit `AllowAny` from the API-02 `REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"]` default** — no override of `permission_classes` / `authentication_classes` (deferred-auth precedent locked by API-02 and pinned by `TestAPIInheritsAllowAnyPermissions`).

**URL names — preserved + new (full table).**

| URL name | Path | HTTP | Source |
|---|---|---|---|
| `simulate_batch` | `/matches/simulate-batch/` | GET / POST | preserved |
| `batch_simulate_status` | `/matches/simulate-batch/status/<str:job_id>/` | GET | preserved |
| `save_batch_games` | `/matches/save-batch-games/` | POST | preserved |
| `save_batch_status` | `/matches/save-batch-status/<str:job_id>/` | GET | preserved |
| `api_simulate_batch` | `/api/simulate-batch/` | POST | **new (API-03)** |
| `api_simulate_batch_status` | `/api/simulate-batch/<str:job_id>/` | GET | **new (API-03)** |
| `api_save_batch_games` | `/api/simulate-batch/<str:job_id>/save/` | POST | **new (ANG-01b)** |
| `api_save_batch_status` | `/api/simulate-batch/save/<str:save_job_id>/` | GET | **new (ANG-01b)** |

**Template DOM ids unchanged.** `templates/matches/batch_simulate.html` retains every DOM id the polling JS reads (`batch-form`, `batch-progress-container`, `batch-progress-bar`, `batch-progress-label`, `batch-error`, `batch-results`, `batch-results-n`, `batch-elapsed`, the per-team `batch-red-*` / `batch-blue-*` / `batch-side-*` / `batch-avg-*` cells, the `scoreChart` canvas, the `batch-save-games` button, the `avgN` / `outlierN` / `saveStatus` cells, and the `batch-team-red-name` / `batch-team-blue-name` class hooks). JS changes are minimal: the `poll(jobId)` URL appends `?team_red_id=&team_blue_id=&arena_map_id=` (the three values from the POST response), the save-status branch's string compare flips `"done"` → `"complete"`, and the `'not_found'` branch is dropped (the Celery path returns 200 + running for unknown ids, never 404).

**SIM-09 save_games flow seam unchanged.** Only the executor swapped (`save_games_task.delay(...)` replaces `threading.Thread(target=_run_save_job, ...)`); `BatchSimulator.save_games` itself is untouched, persisted side semantics (flipped rounds keep their actual sides on `GameRound.team_red` / `team_blue`) are unchanged, and the `request.session["batch_seeds"]` handover is read in the same shape.

**Settings (`laserforce_simulator/settings.py`).** A `CELERY_*` block appended after `REST_FRAMEWORK`: `CELERY_BROKER_URL` and `CELERY_RESULT_BACKEND` default to `redis://localhost:6379/0` (override via env), `CELERY_RESULT_EXPIRES = 3600`, JSON serialization across the board, `CELERY_TASK_ALWAYS_EAGER = config("LF_CELERY_EAGER", default=False, cast=bool)`, and `CELERY_TASK_EAGER_PROPAGATES = True` so task failures surface as exceptions under EAGER rather than silent `FAILURE` state. Celery app lives in `laserforce_simulator/celery.py` (`celery_app = Celery("laserforce_simulator")` + `config_from_object("django.conf:settings", namespace="CELERY")` + `autodiscover_tasks()`), re-exported from `laserforce_simulator/__init__.py` as `celery_app` so the worker resolves the app.

**Tests.** Run under `CELERY_TASK_ALWAYS_EAGER = True` — no Redis required for tests or CI. `conftest.py` does `os.environ.setdefault("LF_CELERY_EAGER", "1")` at module load (no separate `pytest.ini` env block, no `pytest-celery` dependency). Test files: `matches/tests/test_batch_tasks.py` (direct task-level tests under EAGER) and `matches/tests/test_batch_views.py` (view-level tests for the rewritten UI views + the new REST views). The seven `TestRunIncremental*` / `TestChunkSizeFor` classes in `matches/tests/test_simulation_incremental.py` are **unchanged** — they pin the `BatchSimulator.run_incremental` simulator contract, not the job machinery.

**Local development.** Production requires a running Redis broker and at least one worker — `celery -A laserforce_simulator worker -l info` next to the Django dev server. For devs without Redis: opt-in eager mode via `LF_CELERY_EAGER=1` (sets `CELERY_TASK_ALWAYS_EAGER=True`), which runs every `.delay()` synchronously in the request thread.

**Scope-out (locked, ADR-0013 §scope-out).** No `fly.toml` / `Dockerfile` edit (deployment wiring deferred to a separate deploy task); no CI Redis provisioning (EAGER suffices); no token auth on `/api/` (`AllowAny` inherits from API-02); no `master_seed` UI exposure (REST-only, test convenience); no cancel-in-flight UX (`AsyncResult.revoke` exists but no UI ships); no `Job` persistence past the 1h TTL (no DB row, no cron sweep); no `score_averages` CLI change (the management command stays foreground `BatchSimulator().run(...)`); **no simulation mechanics change**; **no Score Calibration re-baseline**; no new GLOSSARY.md term beyond the three already added (`Job`, `Job id`, `Job status`); no new ADR beyond ADR-0013; no `_aggregate_batch` / `run_incremental` / `run` / `save_games` / `BatchSimulateForm` change.

Seam contract: [`.claude/worktrees/api-03-seam-contract.md`](../../../.claude/worktrees/api-03-seam-contract.md).

## CI-01 Connection cleanup and the close-in-transaction guard

**No task closes its own DB connection.** Celery's Django worker fixup closes it after every
non-eager task, and deliberately skips eager runs. A task-level `close_old_connections()` is
redundant on a worker; under `CELERY_TASK_ALWAYS_EAGER` inside a `TestCase` transaction it closes
the connection every later test on that xdist worker shares. On PostgreSQL that fails the rest of
the run with `InterfaceError: connection already closed`; on SQLite's in-memory test DB the close is
a no-op, which hid the bug for months. The same trap fires from a test that calls
`response.close()` on a streamed response the test client already closed: read
`streaming_content` to exhaustion instead.

**Guard.** `laserforce_simulator.testing.install_close_in_transaction_guard()`, installed by the
root conftest's `pytest_configure`, makes `close()` raise `AssertionError` (message
`CLOSE_IN_TRANSACTION_MESSAGE`) while a connection is inside an atomic block, on every backend, so a
SQLite run catches it. Tests: `laserforce_simulator/tests/test_close_in_transaction_guard.py`.
Running the suite on Postgres locally: root `CLAUDE.md` *Database*.
