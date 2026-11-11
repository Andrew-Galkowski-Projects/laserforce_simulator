---
name: next-feature
description: >
  Runs the next laserforce_simulator PLAN.md task through Matt Pocock's
  pipeline: pick it, grill-with-docs in rounds (prototype for what talking
  can't settle), to-spec as a GitHub spec issue with agreed seams, to-tickets
  as sub-issues with blocked-by edges, implement-spec with one test-first agent
  per ticket on an integration branch, then the full suite, a browser check,
  two-axis code-review, a PR with a merge-danger body, and a retro. Use when
  the user says "next feature", "next task", "pick up the next thing" in this
  repo, or invokes /next-feature.
---

# next-feature

The engineering skills come from the `mattpocock-skills` plugin (marketplace
`mattpocock`). Two kinds:

- **Callable** through the Skill tool, by full name so `code-review` doesn't
  resolve to the built-in reviewer: `mattpocock-skills:grilling`,
  `domain-modeling`, `prototype`, `tdd`, `code-review`, `pr`,
  `writing-for-agents`.
- **User-invoked** (`disable-model-invocation`), which the Skill tool refuses:
  `grill-with-docs`, `to-spec`, `to-tickets`, `implement-spec`, `retro`. Read
  their `SKILL.md` and follow it in place:
  `~/.claude/plugins/cache/mattpocock/mattpocock-skills/<version>/skills/engineering/<skill>/SKILL.md`
  (`grilling` and `writing-for-agents` sit under `productivity/` instead).

A session sees the plugin only if it was installed before the session
started. "Unknown skill" for a callable one means a stale session: read it from
the path above.

The roadmap is `PLAN.md`; specs and tickets are GitHub Issues:
`docs/agents/issue-tracker.md`. Glossary and ADRs: `docs/agents/domain.md`.
Review standards: `CODING_STANDARDS.md`. Everything in `CLAUDE.md` still
holds. In particular: PowerShell, no `cd` or `-C` before git, never `git add
-A` or a folder pathspec, and ASCII-only console output.

**Branch files.** Rules only some tasks reach live beside this file. Read each one
when its branch applies, at the step it names:

- `port-and-frontend.md` — a Django-to-Angular port, or any `frontend/` change (grill, spec, verify).
- `parallel-tickets.md` — a breakdown of more than one ticket (spec invariants, starting and
  merging implementers, prepping the integration tip).
- `special-tasks.md` — performance or CI time, deploy or serving, a CI job or release ref,
  removal, prototype / spike / proposal, Desktop App.
- `browser-check.md` — Step 7's browser check, when anything a person sees changed.
- `after-pr.md` — Step 9 once the PR is open: CI, the merge, cleanup.

## Step 1: Pick the task

Run `git fetch origin` first. The main checkout is often on someone else's
feature branch and local `main` lags, so **`origin/main` is the truth** for the
whole pipeline: read the plan with `git show origin/main:PLAN.md`, and every
`main` below means `origin/main`.

The target is the first task heading (`### ID` or `#### ID`),
top to bottom, not marked `[DONE]`, `[DEFERRED]` or sitting under a *Parked*
section. A heading whose sub-items are tasks is an umbrella: pick its first
open sub-item. If the user named a task, use that one. A task whose PR has already
merged (check `git log origin/main --oneline` for its ID) but isn't marked `[DONE]`
is bookkeeping, not the next feature: say so and offer to close it out.

Print its ID, title and one sentence, and confirm with the user before going
on. An umbrella item without `(ID)` in its bold title gets one now
(`5c. **Event charts (ANG-06g)**`): the spec and ticket titles use it, and
`move_done_task.py` finds the item by it. Read its PLAN.md section in full, plus the app `CLAUDE.md` index for the
area and the `docs/agents/<app>/` feature doc its pointer names.

If the section already has `Spec: #<n>`, read that issue and skip to Step 4's
end; if the spec already has ticket sub-issues, skip to Step 6.

## Step 2: Worktree

Run one feature per session, started from the main checkout. Parallel features
each get their own session; a pipeline orchestrator launched as an agent
inherits its parent's worktree and cannot enter its own (RV-05 retro).

Enter a worktree (`EnterWorktree`), named for the task in kebab case (e.g.
`ang-01e-league-stats-api`), and `git merge --ff-only origin/main`. A session
already inside a worktree (a retro's, say) is refused a new `name`: run
`git worktree add -b <task> <main checkout>/.claude/worktrees/<task> origin/main`,
then `EnterWorktree` with that `path` (PERF-01 retro). A worktree has no
`db.sqlite3` and no `.env`: copy `laserforce_simulator/.env` from the main
checkout into the worktree's `laserforce_simulator/`, then run
`migrate` before any server or management command. When the task touches
`frontend/`, also run `npm --prefix frontend ci` (a worktree has no
`node_modules`). Run `migrate` again after merging any ticket that adds a migration,
before a browser check. Run any server on `--port 8001` so the owner's 8000
stays theirs, and start `runserver` through the worktree's absolute
`laserforce_simulator/manage.py` path: `stop_worktree_servers.ps1` finds a
server by its worktree path, and a relative one has none (ANG-06-10 kept 8001). In a worktree session, give each shell call one plain command,
with literal values in place of `&&` chains and `$(...)`: the isolation guard
refuses commands it can't verify. Look up a value in one call and pass it
literally in the next.

## Step 3: Grill

`grill-with-docs` is just two skills together, and both can be called:
call the Skill tool twice, for `mattpocock-skills:grilling` and
`mattpocock-skills:domain-modeling`, on the task. Grilling asks in **rounds**:
every frontier question at once, numbered, each with a recommended answer
worded so "yes" accepts it. `AskUserQuestion` takes at most four questions, so
a bigger round goes as numbered text in chat and the owner answers there.
Facts come from subagents reading the code; decisions come from the owner.
A subagent mapping Player stats to the engine starts from the "Sim effect" column in
`docs/lfstats-import.md` and reads code only for what it lacks (LFS-02's agent re-derived it
for 190k tokens).
When the task is a bug or a red check, reproduce it before round 1: the
probe's result is a settled fact in that round, not a question. Match the
probe to the report, the same error and the same order of output, before
round 1: a nearby failure is a different bug (BUG-WV-01's first probe failed
at once with `E_UNEXPECTED`; CI's `E_ABORT` was a hang, and round 1 was re-asked). A performance
task gets the same probe (`special-tasks.md`), and so does a deploy task's running stack; a
port task maps its screens to routes first (`port-and-frontend.md`).
Terms go into `GLOSSARY.md` as they settle; rulings that are hard to reverse
become ADRs in `docs/adr/` (next free number).

The PLAN.md section often already holds answers the owner gave earlier. Read
them and don't re-ask; put them in the first round as settled.

Cover, in the same rounds:

- **Ownership.** Which Account sees and edits what, and where the `manager`
  seam (`accounts/permissions.py`) filters it. Cross-Account rows are 404.
- **Data model.** New models or columns and their migrations.
- **Simulation.** Whether it touches the engine, RNG or ordering. If so, the
  determinism story (seeds, serial-vs-parallel test).
- **API vs HTML.** Whether it lands in the HTML views, `/api/` (ADR-0039
  envelope, Form-bound writes), or both through a shared `*services.py`.
- **Scope.** What is out, written down so nobody builds it.

For a question talking can't settle (a screen layout, a play-loop interaction),
call `mattpocock-skills:prototype`, let the owner pick, and feed the answer
back into the grilling.

Done when the frontier is empty and the owner confirms you understand each
other. Save settled answers under the task's PLAN.md section as you go, so a
crash loses nothing.

## Step 4: Spec

Follow `to-spec`. Agree the **seams** with the owner first: prefer existing
ones, fewest possible, ideally one. In this app that is usually a service
function in a `*services.py` module, or a DRF endpoint tested through the API
client. Every new public name, signature and return shape at the seam goes in
the spec's Implementation Decisions. Every `login_not_required` view the spec
adds lists its hostile inputs (a redirect target, a path) in Testing Decisions:
ANG-10's `app_redirect` spec had none, and review found an open redirect.
A settings module the spec adds gets a Testing Decision that imports it and
pins every value that changes behaviour: DESK-01's smoke test passed with jobs
switched to eager, since the job still completes.
Grep each name the spec cites that already
exists (a model field, a service method) before publishing: ANG-06a's spec said
`currentUser().player` where the field is `self_insert`. For every HTTP assertion
the spec makes (a status, a body text, a header), `curl` the real endpoint once:
ANG-07's spec expected `/` to show `base.html`'s title, and anonymous `/`
redirects to the sign-in page. A test that runs in a CI job or on a release ref
probes the way that job and ref will (`special-tasks.md`). For every user-facing message the spec quotes,
find the line that produces it: ANG-06 item 7 promised "Something went wrong"
where `ApiClient` says "Could not reach the server.". For every field the
spec adds to an API output, grep the tests for it as well (`assertNotIn`,
exact key-set asserts, shared test helpers included) and name each hit in the
spec as a test to update: ANG-06b's full suite found one that pinned
`overall_rating` absent, and ANG-06-15's found `ok_page`
(`matches/tests/league_api_fixtures.py`), which pins paged `meta` to the page
keys exactly.
A frontend decision or a port spec follows `port-and-frontend.md`; tickets that will run in
parallel get the shared invariants in `parallel-tickets.md`; a removal spec gives the
per-class verdicts in `special-tasks.md`.

Before publishing, map every numbered grill decision in `PLAN.md` to the spec
section that carries it. A decision with no section is a missing Implementation
Decision: DESK-03's spec named the no-WebView2 fallback but not decision 2's
"pywebview raises" case, and the ticket shipped an exit-1 box there.

Every guard the spec defines (a filter or pairing rule that ANDs conditions) gets one
Testing Decision fixture per condition, failing that condition alone (`CODING_STANDARDS.md`,
Tests). RV-08's result pairing ANDed four conditions and the spec listed one; review found
the other three untested.
An invariant that every X ends in exactly one Y lists each early `return` on X's
existing code path, each with a fixture: MECH-21's "every Lock ends in one row" missed
`_complete_missile`'s `may_tag` return, so a Chase ban at launch ended the Lock with no row.

A criterion measured over seeded rounds states its number, its seeds, its per-seed
spread and its standard error, taken from a baseline `round_probe` before the spec is published
(`matches/management/commands/CLAUDE.md`). MECH-07's decision 13 said "closer", and all
four tickets were built before anyone saw that three distances missed, that goal sharing
swings from 9% to 24% per round, or that players are not Active on 34-64% of ticks.

A target on anything LFStats measures (shots, uptime, Eliminations, Resupplies) states the
real value beside it, or says it is only a collapse floor. Read per-role averages from the
`baseline` of `lfstats_player_stats` with a `position` breakdown (it needs some player's
name; that player is left out of the baseline). MECH-17's tag targets came from the sim's own
numbers, and three tickets read them as misses when real players fire fewer shots.

Every new rule a target depends on gets one prototype run on the fixture map before the spec
is published. MECH-17's search toward the enemy base was unmeasured and did nothing on
Syracuse, whose base cells are in walls: two rounds in 12 planned no Shots from Commanders,
Heavies or Scouts, and the ticket had to depart from the spec.

Publish it as a GitHub issue titled `<TASK-ID>: <title>`, labelled
`ready-for-agent`. Add `Spec: #<n>` under the task's heading in `PLAN.md`.

## Step 5: Tickets

Follow `to-tickets` on the spec. Vertical tracer-bullet slices (model,
service, view/endpoint, tests), each sized for one fresh session: estimated
tokens / 150k is the ticket count. A ported Angular screen with its spec and
mutation runs costs about 160k tokens (ANG-06-10: one screen per ticket, 157k
and 170k), so count screens before dividing. A ticket that adds API fields, a
service and shared helpers before its screen costs about 230k (ANG-06-17a: 233k
for one small screen). A link-wiring ticket that also changes the seed costs
about 200k (ANG-06-18g: 217k across five screens' specs and the seed tests). An
engine ticket with probe runs and suite triage costs about 300k and an hour (MECH-17: 286k
to 366k).
Deletion and docs-sweep tickets: `special-tasks.md`. A
task small enough for one session gets one ticket.
Don't pad.

Run every number an acceptance criterion names through the spec's rule before
publishing the ticket: ANG-06-13a's #400 said the pool warning shows at 30, a
valid pool size (a multiple of 6, at least 24). Check every test recipe a
ticket quotes (a fake-timer list, a fixture helper) against an existing spec
that does the same: ANG-06-13b's #405 said to fake `setTimeout`, and rxjs
`timer` polls on `setInterval`.

Create each ticket as a sub-issue of the spec with native blocked-by edges:
write each body to a file, list the tickets in dependency order in a JSON file,
and run `python scripts/publish_tickets.py <spec> <tickets.json>` (format in
its header). A body names another ticket as `{{KEY}}` (its JSON key), and the
script swaps in the real `#<number>` once every issue exists. A run that fails half
way is finished by running it again.

A **one-ticket** breakdown publishes no sub-issue: the spec issue carries the
acceptance criteria, the commit comment goes on the spec, and the PR closes the
spec alone (SM5-01's ticket only restated its spec).

Prototype, spike and proposal tasks change the tickets: `special-tasks.md`.

**The owner's approval of the breakdown authorises local commits** on this
task's integration and ticket branches (the `CLAUDE.md` exception). Say so
when asking. Once approved, commit the grill's docs (PLAN.md, GLOSSARY.md,
ADRs) on the integration branch first, each path named.

## Step 6: Implement

The worktree's branch is the **integration branch**. Follow `implement-spec`
against the spec issue.

**A one-ticket breakdown is yours to implement:** write it test-first in the
integration worktree, under the implementer constraints below (the red pairing
for every Implementation Decision included), run `/verify` with `--mode branch`
right before the commit (BUG-SL-01 committed its code unverified), and comment
the commit on the spec issue. Step 8's review still gives it fresh eyes. PERF-03's implementer agent
spent 63k tokens on a 10-line change.

A departure from the spec (a rename, a changed default, an extra UI rule) is a
comment on the spec issue when it is decided, by you or an implementer, so the
Spec reviewer reads it there: DESK-01's four reached the issue only through the
PR body.

**A spec's acceptance run (a probe or measurement of the finished feature) runs
once, after Step 8.** The last ticket checks only that the run starts and
reports every figure. The Spec review then runs on the integration tip, its
fixes land, and the acceptance run follows on the final code. Its out-of-range
figures are posted on the spec before the PR. CAL-03's review fixes changed the
behaviour the 128-game probe had measured, so the 20-minute probe ran twice.

A ticket that changes a documented value (a default, a constant, a limit) or retires
a name (an event verb, a metadata key or value, an API field) greps the docs for each
old one (`git grep -n "<old>" -- ":/*.md"`) and fixes every statement of it in the
same commit: LFS-01 moved the preferred-role boost from 1.2 to 1.0, and three docs
still said ×1.2 until review; MECH-21 retired `"miss"` and `fired / hit / efficiency`,
and `simulation-engine.md` still described them as current until review.

For more tickets, starting implementers, preparing the integration tip and merging each
ticket follow `parallel-tickets.md`.

These constraints bind whoever implements, you on a one-ticket breakdown or each
implementer agent (whose prompt adds `parallel-tickets.md`'s *Implementer-only
constraints*). Check them against each ticket's acceptance criteria: a criterion
overrides a blanket ban, so say which in that ticket's prompt (ANG-06-18g's docs
criterion met a "no docs" constraint and was skipped).

- Follow `mattpocock-skills:tdd` at the spec's agreed seams: one failing
  test, then just enough code, one slice at a time. Tests and code for a
  ticket come from the **same** agent. Test placement is in `CLAUDE.md`.
- Every Implementation Decision gets a test you have seen go **red**: write it
  first, or mutate the code back with `python scripts/mutate.py`, before
  reporting done. Name each pairing in your report. Config-only tickets need this most, because their tests pass
  the moment the setting lands. A cross-Account read uses
  `assert_owner_only` (`laserforce_simulator/testing.py`): written before the
  action exists, it is red, and its owner-gets-200 half keeps it from passing
  on a 404 for everyone. This is the ownership red, since a guard mutation
  is refused (Step 7).
- Give pytest the nested Django dir's config
  (`-c laserforce_simulator/pytest.ini --rootdir laserforce_simulator`). A ticket
  that touches `frontend/` also runs `npm --prefix frontend run build`. A new
  API endpoint also gets an entry in the N+1 registry and runs it:
  `laserforce_simulator/api/tests/test_detail_action_query_counts.py` (a
  detail `@action` GET: a `SCALERS` entry) or `test_list_query_counts.py` (a
  list). ANG-06-9's registry test failed only in the full suite, and its
  scaler then caught a real N+1.
- Write files with Write and Edit, never a heredoc. Glob before creating a
  file, in case the name is taken. Scratch files get a unique prefix (your
  ticket ID).
- One plain command per shell call, with literal values (no `&&` chains or
  `$(...)`): the worktree isolation guard refuses the rest.
- PowerShell. No `cd` or `-C` before git. Enumerate every path you stage: no
  `git add -A`, `.`, `-a` or folders. No `git stash`, `reset`, `restore` or
  `checkout` outside your own worktree.
- Run `python -m black` on the files you touched. Run `makemigrations` for
  model changes and commit the migration with the model.
- **Checkpoint around every long run** (a probe, a `--measure` mutation, the
  full suite: anything past about 5 minutes). Before it, commit what is green.
  After it, comment on the ticket issue: what is committed, what is uncommitted
  and why, the output file's path, and the next step. CAL-03c's session ended
  after two 20-minute acceptance probes with the rule change they tested still
  uncommitted, and the results only in a scratchpad file.
- Right before each commit, run `/verify --mode branch` and fix every FAIL,
  mypy included: CI's lint job fails the PR on a mypy error on a changed line,
  and LFS-01's review spent a pass on 19 of them from four tickets.
- Ownership is filtered on the server through the `manager` seam.
- If you need a server, it goes on a free port, never 8000. Before reporting,
  stop it with `powershell -NoProfile -File scripts/stop_worktree_servers.ps1`
  through the PowerShell tool (it defaults to the current worktree; the Bash
  tool's worktree guard refuses it): stopping the npm wrapper alone leaves node,
  esbuild and dart running, and they block `git worktree remove`.
- Stage and commit in one call, `git add <paths>; if ($?) { git commit -F <file> }`,
  naming the paths `git status --short` lists (a `git mv` or `git rm` has
  staged its old path already, and naming it fails the add).

## Step 7: Verify

Confirm every ticket landed on the integration branch
(`git log origin/main..HEAD --oneline`, `git status`). Then check the agents' work by
hand (`CLAUDE.md`, *Sub-Agent Delegation*): off-by-one in tick math, constant
mismatches, field-name drift between spec and code, missing seam methods.
**Every gap an implementer reports against a spec invariant** ("not covered",
"unlikely", "edge case") gets a red test before merge: it is a test to write,
not a judgement call. (ANG-05: the one real bug was such a reported gap.)

1. **Full suite** on SQLite (`python scripts/run_suite.py`, which prints only
   failures and the count line) and, when `docker info` succeeds, on Postgres
   (`docker start lf-test-pg`, then `python scripts/run_suite.py --postgres`), plus
   `python laserforce_simulator/manage.py makemigrations --check --dry-run`.
   Run `run_suite.py` in the foreground, with a 600 s timeout: a background run can be stopped for low memory while
   the session waits (DESK-03 lost one).
   When `frontend/` changed, run `test:ci` as `port-and-frontend.md` *Verify* says.
   **Python-free diff:** when
   `python .claude/skills/verify/scripts/changed_files.py --base origin/main --mode branch`
   reports empty `python`, `templates` and `migrations` buckets, skip both suite
   runs and `makemigrations` (CI runs them) and report
   `Python suite: CI only (no Python changes)`. Otherwise
   triage every failure:
   - a **regression** from this task: fix the code;
   - a test that encoded the **old contract** on purpose: update it honestly,
     with a comment on why the invariant still holds. Never quietly weaken a
     guard;
   - **pre-existing**: confirm it fails on `origin/main` too, and say so.
   Repeat until 0 failed. Report the exact counts.
2. **Mutation-test** the test that matters most: put the bug back on purpose
   and check the test catches it. Mutate the feature code (a route, a
   formula, a condition; each condition of a spec guard dropped on its own), never an auth or permission guard: the auto-mode
   classifier refuses a run against a removed guard. Run each mutation with
   `python scripts/mutate.py <file> <mutations.json> -- <pytest args>` (JSON
   `[name, old, new]` triples in the scratchpad): it restores the file and
   prints which tests caught each one. A `SURVIVED` line is a test to write;
   a `BROKEN` line (the run failed with no test named: it does not compile)
   is a mutation to rewrite; `BASELINE FAILED` (exit 2, nothing mutated) means a test is red on the
   unchanged code: fix it first. `AMBIGUOUS` / `NOT FOUND`: the old text matches more than once /
   never, and nothing ran; make it unique. Each run is killed after `--timeout`
   seconds (300): `TIMEOUT` counts as caught; `BASELINE TIMED OUT` means the
   tests are too slow to mutate, so narrow them (LFS-01 sat 2 h on one mutant
   that sent a test into a real search before the limit existed). For a seeded
   score, `--measure -- <command>` prints the numbers on the unchanged code and
   under each mutation.
   Frontend code mutates as `port-and-frontend.md` *Verify* says.
3. **Browser check** when anything a person sees changed, and an endpoint check
   for an API-only task: follow `browser-check.md`. A Desktop App change checks
   the real window instead (`special-tasks.md`). A change nobody sees skips it
   and says so.

## Step 8: Review

Call `mattpocock-skills:code-review` with fixed point `origin/main`. The Spec axis
reads the spec issue and its tickets; the Standards axis reads
`CODING_STANDARDS.md` plus Fowler's smells. Give both reviewers the main
checkout's `.venv\Scripts\python.exe` for any Python or pip call, so they can
verify installs and runtime claims. Tell the Spec reviewer to search for
parameterised tests (`it.each`, `@pytest.mark.parametrize`, `subTest`)
before reporting a test missing (ANG-06e's false P1), and both reviewers to
quote the grep or line behind any finding about what a file contains or lacks
(PERF-02's false "durations file predates the split"), and the line that
produces any runtime behaviour a finding claims (DESK-01's false "a PENDING job
fails at once": `celery_state_to_job_status` maps PENDING to `running`). Give the
Standards reviewer the task's settled grill decisions from `PLAN.md` too, so a file a
decision asked for reads as in scope (SM5-02's false "`lfstats-import.md` is
unrelated"). Before showing the reports, drop any finding about a file's contents
that quotes no line (SM5-02's "probably repeats tests", in a file the reviewer never
opened). When the code diff
(`git diff origin/main...HEAD --shortstat -- . ":!*.md" ":!*test_*.py" ":!*.spec.ts"`) is under about 150
changed lines, spawn both reviewers with `model: sonnet`: TEST-01's two default
reviewers spent 138k tokens on 60 lines for one nit. Show both reports. Fix the
findings the owner agrees with in one implementer pass, re-run the affected
tests, and review again only if a fix changed behaviour. A doc fix that states what
code does quotes the code line it read: MECH-21's fix wrote "`missiled` has no
`result`" from a summary, and `event_log.py` still writes `result="hit"`. A fix that changes
what a screen shows re-takes that screen's Step 7 screenshots before Step 9
(ANG-06g's chart font). The fix pass is
the orchestrator's own work, not a ticket: tell its agent to **stage** its
files and report, not commit (the auto-mode classifier refuses its commit).
Copy them onto the integration branch with
`python scripts/copy_staged.py <fix worktree>` (one plain command; a `cp` loop
or `git -C` into that worktree is refused). Review the result with `git diff`,
re-run the frontend / Python checks there, and commit them yourself. Then
remove the fix worktree with the command `copy_staged.py` prints: its staged
files outlive the copy and otherwise sit on disk until someone audits them.
Once the fixes are in, run the spec's acceptance run (Step 6) on the final tip.

## Step 9: PR

Run `/update-docs` so the `docs/agents/` feature docs, the app `CLAUDE.md` index pointers, `PLAN.md` (`[DONE] Shipped
(<date>).` plus what the work found) and `README.md` match the branch. Then `/pr-ready` for its checks, and write the body with
`mattpocock-skills:pr`:

```markdown
## Summary
<the smallest picture of the change: call tree, file tree, mermaid, or diff sketch>

## Evidence
- **Before:** <what happened before, or the failing test>
- **After:** <what happens now>; full suite: <N passed, 0 failed>; browser: <what was read off the page>
- **Screenshots:** <before / after images or a video from `python scripts/pr_assets.py`; required when
  the diff touches templates, CSS, static assets or `frontend/src/` (the `PR evidence` check enforces
  it; see `/pr-ready`). With no visual change, replace this bullet with the line below instead>
No visual change: <reason, on its own line: `scripts/check_pr_evidence.py` accepts nothing else>

## Merge Danger
**Door:** <one-way or two-way>. <migrations? AUTH/ownership changes? determinism?>
**Blast radius:** <one word or phrase>

Closes #<spec>
Closes #<ticket> (one line per ticket; none for a one-ticket breakdown)
```

A migration that drops or rewrites data is a one-way door. Say so plainly.

**Stop and show the owner the commit summary and PR body.** Commit, push and
`gh pr create --body-file` only on their "go" (`CLAUDE.md`, *Git Workflow*).
**Once the PR is open, read `after-pr.md` and run it in order:** CI, the owner's merge,
then the squash check, cleanup, the `main` run and the desktop build. Read it then, even if
you remember it: a compacted session keeps only part of this file.

## Step 10: Retro

Before the owner clears the session, offer `retro`. It only **suggests**
changes to the setup: a navigation pointer, a check instead of a rule, a
`CLAUDE.md` line to cut or move to `CODING_STANDARDS.md`. The owner picks
which to keep, and the kept ones land as their own small PR. Never apply a
retro finding unasked.
