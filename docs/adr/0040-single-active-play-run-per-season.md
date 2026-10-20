# A Season allows one Active play run at a time, guarded server-side

**Status:** Accepted (ANG-01d-2, 2026-10-16). Supersedes ADR-0016 §5 and its "client-side dropdown
disable as the only concurrency guard" decision.

ADR-0016 accepted overlapping play submissions on the grounds that the round-1-vs-round-2 guard in
`simulate_scheduled_round` makes a double submit harmless. Reading the code during the ANG-01d-2
grill showed that claim does not hold: a second submit can re-play a Round on an existing Match, and
two concurrent lookups that both miss can each create a Match. The second task also overwrites
`Season.active_play_job_id`, and whichever task finishes first clears it while the other is still
running. A REST API makes this worse, because an API client has no Play dropdown to disable. So a
Season now has at most one **Active play run**, enforced on the server for both the HTML views and
`/api/`. Every play action, synchronous or asynchronous, is refused while a run is live. The API
answers with 409 `play_in_progress`; the HTML sync views redirect back with a `play_error`.

The guard reuses the existing `Season.active_play_job_id` column, so there is no new `is_playing`
field and no migration. The enqueue path makes up the Celery task id first, writes it and clears
`play_cancel_requested`, and only then calls `apply_async(task_id=…)`. That removes the race where a
fast or eager task cleared the column before the view had written it. A task's `finally` clears the
column only while it still holds that task's own id. "Live" means the column is non-null. We do not
consult `AsyncResult` for this, because an expired Celery result reads as `PENDING` (running)
forever.

## Considered options

- **Keep the client-side disable only (ADR-0016).** Rejected: an API has no client to rely on, and
  the claim that a double submit is harmless turned out to be false.
- **A new `Season.is_playing` flag plus a migration.** Rejected: the job-id column already carries
  the same information once the write order is fixed.
- **Treat a run as live only while its `AsyncResult` state is non-terminal.** Rejected: an expired
  result can never be told apart from a running one.

## Consequences

- A worker that dies without reaching its `finally`, or a message no worker ever consumed, leaves
  the column set. Cancel is the recovery path: `request_play_cancel` always sets the cancel flag,
  and when `AsyncResult(job_id).state` says the job is not running (`PENDING`, `SUCCESS`, `FAILURE`
  or `REVOKED`) it also clears the column with an update filtered on that exact id, so it can never
  clear a newer run. `STARTED` / `RETRY` / `PROGRESS` keep the column and only set the flag.
  `CELERY_TASK_TRACK_STARTED` is on so a picked-up task reads `STARTED` before its first
  `PROGRESS`. A job still queued (also `PENDING`) is released too; the cancel flag makes it return
  at its top-of-task check when a worker finally takes it. This consults `AsyncResult` only on an
  explicit Cancel, never for the "is a run live" guard.
- A race between two synchronous plays that both start before either writes a job id (two
  simultaneous Play One Week POSTs) is **not** covered, because sync plays do not hold the column.
  It is recorded in PLAN.md Deferred Items.

## Tournaments (ANG-01f, 2026-10-19)

A sandbox **Tournament** follows the same rule. Before ANG-01f, Play All enqueued a new bracket drain
on every POST, so two drains could race on the same node. A Tournament now has a nullable
`active_play_job_id` column, which takes **one migration**: unlike the Season, a Tournament had no
job column to reuse. The write order is the one above: make up the task id, write it, then
`apply_async(task_id=…)`. The task's `finally` clears the column only while it still holds its own
id. While the column is set, Play All and the synchronous Play Next are refused: 409
`play_in_progress` in the API, a flashed error in the HTML.

An embedded (in-League) Tournament is never played through the sandbox surface. It is played only
through its Season's play loop, so the Season's guard covers it. The sandbox views and the API answer
every write on an embedded Tournament with 409 `tournament_embedded`.

There is **no Cancel** for a Tournament, so the release path differs. When the guard finds the
column set, it checks `AsyncResult(job_id)`. If the job is terminal (`SUCCESS`, `FAILURE` or
`REVOKED`), it clears the column with an update filtered on that exact id and lets the new play
proceed. `PENDING`, `STARTED`, `RETRY` and `PROGRESS` stay blocked. This still never treats a
`PENDING` result as finished. Consequence: a message that no worker ever consumed reads `PENDING`
forever and keeps that bracket blocked. It is recorded in PLAN.md Deferred Items, and the fix would
be a Tournament Cancel.
