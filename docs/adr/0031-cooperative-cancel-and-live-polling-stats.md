# Cooperative between-fixture cancel + live polling stats for Play Season jobs

**Status:** Accepted (PLAY-01, 2026-06-26)

## Context

[ADR-0013](0013-async-batch-execution-via-celery-redis.md) put every async job
on Celery + Redis and deliberately scoped a cancel-in-flight UX **out** — its
status vocabulary maps Celery `REVOKED` to `error`, but no UI ever revokes.
[ADR-0016](0016-play-season-job-execution-model.md) is more explicit: its
rejected-alternatives section turns down **"Mid-job cancel via
`AsyncResult.revoke`"** on the grounds that killing the worker mid-Round leaves
a half-committed Round; only **between-Round** cancellation is safe, and that
needs cooperative-cancel polling inside the task body. ADR-0016's answer at the
time was "close the tab — the worker finishes the current Round and the Season
stays `active` with whatever it committed."

Two NAV-01 surfaces now make that gap visible. NAV-01
([ADR-0030](0030-play-controls-relocated-to-topnav.md)) relocated all
league-advancement controls to a single `Play ▾` topnav dropdown and shipped
the three async actions (`play_two_months` / `play_until_end` / `play_playoffs`)
with **progress-display only** — it explicitly deferred the Play→Stop swap, the
cancel control, live incremental standings/leaders, and cross-page resumable
progress to PLAY-01. Today those async runs commit each Round atomically and
emit `PROGRESS`, but the poll JS only reloads the page on
`status === "complete"`, so **nothing on screen moves until the whole run
finishes**, and **there is no way to stop a run that is already going**.

PLAY-01 reverses the ADR-0013 / ADR-0016 cancel scope-out. The forcing
questions were (1) *how* a run is stopped without the half-committed-Round
hazard ADR-0016 names, and (2) *what transport* carries the live standings /
leaders during a run. The locked answers were grilled at the seam contract
([`.claude/worktrees/play-01-seam-contract.md`](../../.claude/worktrees/play-01-seam-contract.md)).

## Decision

1. **Cooperative between-fixture cancel via a DB flag — NO `AsyncResult.revoke`.**
   The Season grows a `play_cancel_requested` boolean. A new POST-only
   `play_cancel` view sets it. The Celery task (`play_season_task` and
   `play_playoffs_task`) re-reads the flag **at the top of the task body** (the
   queued-but-not-started case) **and at the top of every fixture / bracket-stage
   iteration** (the running case), via a module-level helper
   `_play_cancel_requested(season_id)`. When the flag is set the task **breaks
   cleanly out of its loop and returns normally**. No `revoke` of any kind is
   used — a non-terminating `revoke` cannot stop an already-running task, and a
   terminating `revoke(terminate=True)` is forbidden by ADR-0016's
   half-committed-Round rule.

2. **The cancel return is a NORMAL return ⇒ Celery SUCCESS ⇒ `complete`.** The
   early return is a plain return value — `{"completed": <k so far>, "total":
   <n>, "cancelled": True}` — so Celery records **SUCCESS** and the existing
   `_celery_state_to_job_status` helper maps it to the locked `complete` status.
   There is **no new status-vocabulary string**. The `cancelled: True` payload
   rides alongside the partial counts purely as an optional UX toast flag; a
   cooperatively-stopped run is, in status terms, an early-completed run.

3. **Resumable because each Round is its own atomic commit.** Per ADR-0016,
   every completed Round is a permanent per-Round atomic commit with no outer
   transaction around the loop. A cooperatively-stopped run therefore leaves all
   already-played Rounds committed, the Season `active`, and the run resumable —
   re-clicking Play picks up from the persisted `played_keys` /
   `find_next_playable_node` cursor exactly as ADR-0016 already guarantees for a
   tab-close or a mid-loop failure.

4. **Live incremental stats ride the EXISTING Celery polling rail.** No new
   transport. The NAV-01 `play_status` poll is extended: `_build_play_status_response`
   keeps its existing 5 keys (`status` / `completed` / `total` / `error` /
   `season_id`) and **adds** `standings` (a server-rendered HTML fragment),
   `leaders` (a 3-key dict of HTML fragments — `points` / `tags` / `ratio`), and
   `cancelled` (a bool). The stats are **recomputed view-side from committed rows
   on every poll** — via the same `compute_standings` / `compute_leaders` helpers
   the dashboards already use, over `Match` / `PlayerRoundState` rows filtered to
   the Season — **not** read from Celery task meta. The poll JS patches the
   dashboard standings/leaders panels by id each poll (existence-guarded, a no-op
   off-dashboard), so the panels reflect exactly what the per-Round commits have
   landed.

5. **`Season.active_play_job_id` persists the in-flight job id.** The async
   enqueue views record the Celery job id on the Season column at enqueue time
   (and clear `play_cancel_requested`); each task's `finally` clears
   `active_play_job_id`. This lets render/poll resume an in-flight run on page
   load — the Play→Stop control swap and the progress display survive a
   navigation or reload, not just the page that submitted. Render/poll treats a
   **terminal** `AsyncResult` as "no active run" regardless of the column, so a
   crashed worker that never cleared it cannot wedge the UI.

6. **Scope: async runs only; no simulation-mechanic change.** Only the three
   async paths (`play_two_months` / `play_until_end` / `play_playoffs`) are
   affected. The sync paths (`start_season`, `play_week`, `play_single_round`,
   `play_week_live`, `next_season`) are untouched. No RNG is consumed in the tick
   loop and no simulator body changes, so there is **no Score Calibration
   re-baseline obligation**.

## Rejected alternatives

**WebSockets / Django Channels for live push.** Push standings/leaders over a
socket instead of polling. Rejected: it requires an ASGI + Channels + deploy
migration (a Channels layer, a second async idiom alongside the ADR-0013 Celery
polling rail, and a new test surface that does not fit the no-Redis
`CELERY_TASK_ALWAYS_EAGER` test story); and it buys **no cancel-latency win**,
because the safe-stop granularity is the **fixture boundary**, not the network
round-trip — a socket would deliver "stop requested" instantly but the task
still only checks between fixtures, so the observed stop latency is identical to
the 500 ms poll. The polling rail already exists, already has the test harness,
and is fixture-boundary-bound regardless. A real-time push transport is deferred
to its own infrastructure item if a future feature needs sub-fixture latency.

**`AsyncResult.revoke(terminate=True)`.** Terminate the worker process mid-run.
Rejected (re-affirming ADR-0016): killing the worker mid-Round leaves a
half-committed Round — the exact hazard ADR-0016's per-Round-atomic-commit
decision exists to avoid. Cooperative between-fixture cancel is the only safe
stop.

**A new `cancelled` status string.** Add a fourth value to the polling status
vocabulary. Rejected: the `running` / `complete` / `error` vocabulary is locked
(LG-01d) and shared verbatim across the batch, save-games, Play Season, and Play
Tournament jobs; widening it for a cosmetic "stopped vs finished" distinction
would force every consumer of all four job kinds to learn a fourth value for no
behavioural gain. A cooperative stop **is** an early SUCCESS — it maps to
`complete`, and the optional `cancelled: true` payload carries the cosmetic
distinction without touching the vocabulary.

## Consequences

- **The ADR-0013 / ADR-0016 cancel scope-out is reversed (cooperatively).** Both
  prior ADRs carry a dated PLAY-01 addendum recording the reversal. The
  **`revoke` rejection still stands** — PLAY-01 stops runs cooperatively, never
  by revoke.

- **No new transport, no new infra dependency.** Live stats reuse the existing
  `play_status` poll; cancel reuses the existing DB + the existing poll. No
  Channels, no ASGI, no socket layer, no new env var, no new Fly.io process.

- **One model change + one migration.** `Season` gains `active_play_job_id` and
  `play_cancel_requested` via `0056_season_play_job_cancel` (2× `AddField`, no
  `RunPython` — existing Seasons take the `null` / `False` defaults per ADR-0004).

- **Stop latency is bounded by the fixture duration, by design.** A Stop request
  is observed at the next fixture-boundary check, so the worst-case latency is
  one Round's wall-clock (~200 ms per Round for realistic Season sizes). This is
  the safe-stop floor; a socket would not lower it.

- **No simulation / determinism interaction.** PLAY-01 is view-context + task
  control-flow + template patching. The simulator, the per-Round atomic-commit
  model, and the seed chain are untouched ⇒ **no Score Calibration re-baseline**.

- **Documented in matches/CLAUDE.md.** The locked names (the two `Season` fields,
  the `play_cancel` view/URL, the extended `_build_play_status_response` JSON,
  the `_play_cancel_requested` helper, the `finally` clear, the topnav Stop swap
  + dashboard-panel patch ids) live in the `## PLAY-01` subsection of
  [`laserforce_simulator/matches/CLAUDE.md`](../../laserforce_simulator/matches/CLAUDE.md).
