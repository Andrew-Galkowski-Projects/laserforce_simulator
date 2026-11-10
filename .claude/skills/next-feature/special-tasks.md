# next-feature: task kinds with their own rules

Read by `SKILL.md` for a performance or CI-time task, a deploy or serving task, a CI-job or
release task, a removal task, a prototype, spike or proposal task, and a Desktop App change.

## Performance and CI time

**Grill.** A performance task gets the same probe as a bug: time the suspected cost locally,
with and without it, scaled to the target's core count (CI runners have 4 vCPUs; pin a run to 4 cores,
children included, through the PowerShell tool with `cmd /c start '""' /b /wait
/affinity F <python> -m pytest ...`, as PERF-06 did). PERF-02 built a
ticket on coverage being the cost, and one local timing would have shown the
suite was CPU-bound. A probe for a split or a schedule also checks that each
unit's cost holds wherever it lands: PERF-04 rebalanced on test durations that
doubled in the shard holding the CPU-wide batch-sim pools. Local wall time is
noisy (PERF-05's baseline ran 196 s, then 265 s): write `--junitxml` on both
runs and compare them with `python scripts/compare_junit.py <base> <after>`;
a flat control file (one the change can't touch) shows the gap is real.

**PR.** A task that changes CI time reads each run with
`<main checkout>/.venv/Scripts/python.exe scripts/ci_timings.py <run-id>` (every job,
each shard's pytest line, coverage total, run start to `test-result`) and
claims the **slowest of at least two runs** (PERF-02: 4m57s, then 6m08s).
More runs of one commit come from closing and reopening the PR
(`docs/agents/ci.md`); watch the new run with `watch_pr_ci.py <n> --after-run
<previous run-id>`, or the watcher passes on the previous run's checks (PERF-05).

## Deploy or serving

**Grill.** A deploy or serving task lists what is running before round 1: `docker ps` and
each image's created date, plus one real build's output tree, with each top-level
path checked against Django's prefixes (`api/`, `admin/`, `static/`, `media/`).
ANG-10 found the owner's live stack predated nginx only at the browser check,
and Angular's `media/` fonts colliding with Django's `/media/` only after
round 2.

## CI jobs and release refs

**Spec.** Probe the way each CI job that runs the test will:
list the setup the probe needed (an SPA build, a seed, a service) and check every
such job has it (DESK-02 probed with the SPA built; CI's Python jobs never build it,
and the smoke went red there). Probe each ref it runs on too (a PR, `main`, a `v*`
tag): a step that branches on `GITHUB_REF` names in Testing Decisions how each
branch runs before merge (DESK-04's smoke passed as a PR's `dev` build, and the
first `main` build, `latest`, went red).

## Removal

**Spec.** A **removal** spec (deleting views, modules or test files) gives a
port-or-delete verdict per test class, not per file: ANG-08's verdicts covered
files, and `views_tests.py` went with League play cases no verdict had read.
Before the spec moves a helper out of a module being deleted, grep its callers
outside the deleted code; none means delete it, not move it (ANG-08 moved
`_coerce_sort` / `_coerce_dir` with no caller, and review sent them back).

**Tickets.** A deletion ticket costs about 400k per app area it clears (ANG-08: 352k, 400k and
617k for the League one), so give the League area two tickets. A docs-sweep
ticket edits the docs itself, with no subagents (ANG-08j: 353k plus about 2.4M
across nine prune agents it spawned); say so in its body.

## Prototype, spike and proposal

A **prototype** task (throwaway samples the owner picks from) adds two kinds of
ticket. Prototype tickets target a `prototype/<task>` branch cut from the
integration branch. It is pushed but never merged, and the PR still says
`Closes #<ticket>` for it. A **pick** ticket, labelled `ready-for-human`,
holds the screenshots and records the owner's choice on the issue. The tickets
that build on that choice are blocked by the pick.

A **look** choice (how a screen or the Playback map draws something) is picked inside the
grill instead, with no prototype or pick tickets: build one self-contained page on real data,
publish it as a private Artifact with a bar that switches the options, and iterate on the
owner's picks until the frontier is empty; the spec links it and lists the rejected options
(RV-06, RV-07). For the Playback map, `seed_browser_check --syracuse` then
`export_playback_round --round <id> --out <scratchpad file>` gives the data to inline.

A **spike** task (an investigation whose deliverable is an ADR, no merged code)
is one ticket the orchestrator does itself, on a `spike/<task>` branch cut
from the integration branch, pushed and never merged; the PR merges the docs.
Its measurement scripts write each run to a new timestamped file, and every
number the ADR states cites one of those files, committed on the spike
branch. Before writing the ADR, map each acceptance criterion to the file that
proves it: ANG-09's review found three ADR numbers backed only by an
overwritten JSON or a console line, and two criteria never run.

A **proposal** task (a reviewed doc whose accepted items become PLAN entries, with no
measurement) publishes no spec or ticket issues. The orchestrator writes the doc and the
PLAN entries on the integration branch, and the PR title carries the task ID. Steps 6-8
shrink to that doc, then Step 9 runs as usual (LFS-02).

## Desktop App

**Verify.** A Desktop App **App window** change checks the real window instead of the MCP (the MCP
cannot see a native window): install `laserforce_simulator/requirements-desktop.txt`
in a scratchpad venv, build the SPA, and run `python desktop/smoke.py <venv
python> desktop/main.py --window`.
A Desktop-only SPA change (the `desktop` flag, its notices) runs the unfrozen
launcher, after the SPA build: `python desktop/main.py --no-browser` with
`LF_DESKTOP_HOME` set to a scratchpad folder prints `LF_READY <s> <launch url>`;
open that URL in the MCP tab, which signs it in (DESK-04).
