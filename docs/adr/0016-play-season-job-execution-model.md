# Play Season job execution model (per-Round atomic commits, shared task body)

**Status:** Accepted (LG-01d, 2026-05-27)

## Context

LG-01d ships the **Play dropdown** that turns the LG-01c read-only Season +
League dashboards into a write surface. Four Play action variants land at
once — **Start Season**, **Play One Week**, **Play Two Months**, **Play
Until End of Season** — plus a single polling endpoint that serves both
async runs. This is the first time the LG-01 league surface drives a
multi-Round execution loop, and it lands on top of the
[ADR-0013](0013-async-batch-execution-via-celery-redis.md) Celery + Redis
infrastructure rather than the deprecated in-process Job dicts.

- LG-01d ships 4 Play action variants — Start Season, Play One Week, Play
  Two Months, Play Until End of Season — plus their polling endpoint.
- The 2 async actions (Play Two Months, Play Until End of Season) execute
  via Celery, mirroring the API-03 batch/save executor swap. The 2 sync
  actions execute inline in the request thread.
- The two async actions share a near-identical body — load Season,
  materialise fixtures via `generate_schedule(...)`, compute `played_keys`
  from persisted `GameRound`s, select to-play fixtures via the new pure
  `select_play_fixtures` helper, loop calling
  `BatchSimulator.simulate_scheduled_round(...)`. The **only** difference
  between them is how many matchdays they span: `max_matchdays=8` for Play
  Two Months, `max_matchdays=None` for Play Until End of Season.
- The sync actions are short enough to run inline in the request thread —
  Start Season is a single state flip (`draft → active`); Play One Week
  is at most `N/2` Rounds for a Season of `N` teams (the matchday width),
  bounded by the ~200 ms-per-Round BatchSimulator cost.

The forcing question is how the async loop commits. An N=8 Play Until End
run is 56 Rounds spanning 14 matchdays — does the task body wrap the loop
in one `@transaction.atomic`, or does each Round commit independently?
Both shapes leave a partially-completed Season `active` and resumable on
re-click; the difference is whether mid-loop failure rolls back every
previously-completed Round or leaves them in place. The wrong choice
bakes a different operational story into both worker logs and the
dashboard polling UX.

## Decision

1. **Per-Round atomic commits inside the Celery task.** The
   `play_season_task` body has **NO outer `@transaction.atomic` wrapper**.
   `BatchSimulator.simulate_scheduled_round` is already
   `@transaction.atomic` (LG-01) so each Round is its own transactional
   unit. A mid-loop exception propagates out of the task body (Celery
   records `FAILURE`); every Round that completed before the exception
   survives because it was its own atomic commit. This is the
   load-bearing decision the ADR records.

2. **Resumable mid-job.** Because every completed Round is a permanent
   atomic commit, the user can re-click any Play action (One Week / Two
   Months / Until End) on a partially-completed Season and the next
   click resumes from where the previous run left off. The
   `select_play_fixtures` pure module re-reads `played_keys` on every
   invocation, so each click is fully idempotent at the Round level —
   nothing in the task body remembers a previous run's progress beyond
   what is already persisted as `GameRound` rows.

3. **One shared Celery task body parameterised by `max_matchdays: int |
   None`.** `max_matchdays=8` for Play Two Months;
   `max_matchdays=None` for Play Until End of Season. The two HTTP
   endpoints (`play_two_months`, `play_until_end`) differ only by the
   kwarg value passed to `play_season_task.delay(season_id,
   max_matchdays=…)`. The task is registered as
   `name="matches.play_season"` — one Celery task name in the worker
   logs.

4. **One shared polling endpoint** (`GET
   /seasons/<id>/play-status/<job_id>/`) for both async tasks. The
   polling JSON shape `{status, completed, total, error, season_id}` is
   the same for both; the URL kwarg names the `job_id` and `season_id`
   so the client can correlate poll responses to in-page Season context.
   Status vocabulary maps to the API-03 `running / complete / error`
   precedent via the shared `_celery_state_to_job_status` helper —
   consumed verbatim, no rename, no fork.

5. **Side-agnostic Match find-or-create at the simulator boundary.** The
   LG-01 `simulate_scheduled_round` handles all the per-Round
   concurrency / idempotency at the DB layer — the task body does not
   need additional locking. A double-submit race (user clicks Play
   twice rapidly) wastes CPU on a redundant second call but does not
   corrupt state: the second submission's first Round resolves to the
   same Match row as the first submission's last Round, the existing
   round-1-vs-round-2 guard catches the duplicate, and subsequent
   Rounds proceed from the new tail of `played_keys`.

## Consequences

- **Partial-completion is a feature, not a bug.** Mid-job failure (a
  roster validation error on the 5th of 12 Rounds, a worker OOM, a
  redeploy mid-run) leaves the Season `active` with the first 4 Rounds
  played and the remaining 8 unplayed. The dashboard renders the
  partial standings; the user re-clicks Play and the Season completes.
  This is the operational story the worker logs and dashboard UX are
  optimised for.

- **Polling clients tolerate `PENDING` indefinitely.** A `job_id` whose
  Celery result expired (the 1h TTL — `CELERY_RESULT_EXPIRES = 3600`
  inherited from API-03) resolves to `PENDING` ⇒ `"running"` via the
  shared vocabulary helper. A naively-implemented client would poll
  forever on an expired id, but the dashboard JS sidesteps this by
  reloading the page on `data.status === "complete"`, so an expired
  `job_id` never appears in a normal flow (the task always finishes
  within a few seconds for realistic Season sizes).

- **One Celery task name** (`matches.play_season`) visible in the
  Celery worker logs and `--queues` config alongside the API-03
  `matches.simulate_batch` / `matches.save_games` task names. No new
  queue, no new worker process.

- **No `simulate_match` / `simulate_scheduled_round` change.** The
  existing 2-Round-atomic sandbox simulator entry point is untouched;
  the LG-01 per-Round entry point is consumed verbatim — LG-01d is
  pure orchestration. **No Score Calibration re-baseline obligation.**

## Rejected alternatives

**Outer-atomic task body** (`@shared_task` body wrapped in `with
transaction.atomic():`). Rejected because a 56-Round Play Until End run
would either (a) succeed all-or-nothing — a multi-minute transaction
holding ORM locks the whole time, with rollback cost dominating the
wall-clock on the very last Round's failure — or (b) succeed silently
mid-rollback if the ORM session was reset between Rounds. Neither is
operationally desirable; the user-facing experience of "your 50 played
Rounds vanished because Round 51 had a roster error" is the worst of
both worlds.

**Two separate task functions** (`play_two_months_task` +
`play_until_end_task`). Rejected — the two bodies would be 99%
duplicate code; parameterising on `max_matchdays: int | None` is one
line different at the call site (the view's `.delay()` call) and zero
lines different inside the loop. The pure-module
`select_play_fixtures(fixtures, played_keys, max_matchdays)` already
encapsulates the only branching the difference requires.

**`ScheduleEntry`-row-locking approach** (acquire a DB lock on the next
unplayed fixture row, simulate, commit, repeat). Rejected because
[ADR-0015](0015-schedule-on-demand-no-fixture-rows.md) pinned the
no-fixture-rows approach — fixtures are derived on demand from the
pure `generate_schedule` module. There is no `ScheduleEntry` table to
lock against. The Side-agnostic Match find-or-create at the simulator
boundary is the only locking the design needs.

**Mid-job cancel via `AsyncResult.revoke`.** Rejected for LG-01d — the
UI complexity (a "Cancel run" button that gracefully terminates between
Rounds) is deferred. Killing the worker mid-Round would leave a
half-committed Round state; only between-Round cancellation is safe,
and that needs cooperative-cancel polling inside the task body. The
"resumable mid-job" property of the per-Round commit decision means a
user who wants to stop a long run can simply close the tab — the
worker finishes its current Round and the Season stays `active` with
whatever it managed to commit.

**Client-side dropdown disable as the only concurrency guard +
server-side `Season.state` lock.** The contract goes with client-side
disable only — overlapping submissions are accepted by the server and
resolved at the DB layer (Side-agnostic Match find-or-create makes the
second submission's first Round a duplicate of the first submission's
last Round, which the find-or-create harmlessly picks up; subsequent
Rounds proceed). Wasted CPU only on a double-submit race; no data
corruption possible. A server-side `Season.state` lock would require a
new `Season.is_playing` field + migration + a state-restoration story
when a worker dies mid-run — far more machinery than the actual
concurrency surface justifies.
