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
on. Read its PLAN.md section in full, plus the app `CLAUDE.md` index for the
area and the `docs/agents/<app>/` feature doc its pointer names.

If the section already has `Spec: #<n>`, read that issue and skip to Step 4's
end; if the spec already has ticket sub-issues, skip to Step 6.

## Step 2: Worktree

Enter a worktree (`EnterWorktree`), named for the task in kebab case (e.g.
`ang-01e-league-stats-api`), and `git merge --ff-only origin/main`. A worktree has no
`db.sqlite3` and no `.env`: copy `laserforce_simulator/.env` from the main
checkout into the worktree's `laserforce_simulator/`, then run
`migrate` before any server or management command. When the task touches
`frontend/`, also run `npm --prefix frontend ci` (a worktree has no
`node_modules`). Use the main checkout's
`.venv\Scripts\python.exe` for every Python command (the `.conda` env has no
Django). Run `migrate` again after merging any ticket that adds a migration,
before a browser check. Run any server on `--port 8001` so the owner's 8000
stays theirs. In a worktree session, give each shell call one plain command,
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
When the task is a bug or a red check, reproduce it before round 1: the
probe's result is a settled fact in that round, not a question.
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
the spec's Implementation Decisions. Grep each name the spec cites that already
exists (a model field, a service method) before publishing: ANG-06a's spec said
`currentUser().player` where the field is `self_insert`. For every field the
spec adds to an API output, grep the tests for it as well (`assertNotIn`,
exact key-set asserts) and name each hit in the spec as a test to update:
ANG-06b's full suite found one that pinned `overall_rating` absent.

When tickets will run in parallel, give each parallel ticket its own
module(s) at the seam (e.g. a package with one file per ticket) so
implementers never append to the same file. Shared files caused every merge
conflict in the last run.

Publish it as a GitHub issue titled `<TASK-ID>: <title>`, labelled
`ready-for-agent`. Add `Spec: #<n>` under the task's heading in `PLAN.md`.

## Step 5: Tickets

Follow `to-tickets` on the spec. Vertical tracer-bullet slices (model,
service, view/endpoint, tests), each sized for one fresh session: estimated
tokens / 150k is the ticket count. A task small enough for one session gets
one ticket. Don't pad.

Create each ticket as a sub-issue of the spec with native blocked-by edges,
using the `gh api` fallbacks in `docs/agents/issue-tracker.md` (installed `gh`
predates `--parent`).

A **prototype** task (throwaway samples the owner picks from) adds two kinds of
ticket. Prototype tickets target a `prototype/<task>` branch cut from the
integration branch. It is pushed but never merged, and the PR still says
`Closes #<ticket>` for it. A **pick** ticket, labelled `ready-for-human`,
holds the screenshots and records the owner's choice on the issue. The tickets
that build on that choice are blocked by the pick.

**The owner's approval of the breakdown authorises local commits** on this
task's integration and ticket branches (the `CLAUDE.md` exception). Say so
when asking. Once approved, commit the grill's docs (PLAN.md, GLOSSARY.md,
ADRs) on the integration branch first, each path named.

## Step 6: Implement

The worktree's branch is the **integration branch**. Follow `implement-spec`
against the spec issue. Implementers run in their own worktrees branched from
the integration branch, started one at a time: a worktree created while
another agent is still committing can branch from a stale tip, so confirm
each one's base before starting the next. Before starting tickets that run in
parallel, prepare the integration tip yourself:

- Grep it for every helper two of them will need, and move each into a neutral
  service module; every new action goes in its own ticket's file.
- For each new public seam method, grep what it calls (an `ApiClient` verb, a
  model method). Add anything missing to its shared module first, or the ticket
  that is barred from that file stops half-done (ANG-06a: `ApiClient.put`).
- When an earlier ticket left **stubs** for later tickets to replace, check its
  specs against the real screen: they assert *which* component routed (not
  the stub's text), and a spec that opens another ticket's screen releases
  that screen's requests before `verify()`. Otherwise every parallel ticket
  breaks the earlier specs.

Point each one at the spec issue and its ticket issue rather than pasting them.

Merge each finished ticket branch yourself. Into the branch checked out in your
worktree, use `git merge`. Into a branch checked out nowhere (a
`prototype/<task>` branch), use `python scripts/merge_into_branch.py <target>
<ticket branch>`. It fast-forwards when it can, otherwise writes the merge
commit, and leaves the target alone on a conflict.

Add these constraints to every implementer prompt:

- **First step:** confirm your worktree is based on the integration branch,
  else `git reset --hard <integration branch>` in your own worktree. Agent
  worktrees branch from `origin/main` by default. Then copy
  `laserforce_simulator/.env` from the main checkout into your worktree (Django
  needs its `SECRET_KEY`); it stays uncommitted.
- Follow `mattpocock-skills:tdd` at the spec's agreed seams: one failing
  test, then just enough code, one slice at a time. Tests and code for a
  ticket come from the **same** agent. Test placement is in `CLAUDE.md`.
- Every Implementation Decision gets a test you have seen go **red**: write it
  first, or mutate the code back, before reporting done. Name each pairing in
  your report. Config-only tickets need this most, because their tests pass
  the moment the setting lands.
- Run only your own test files, with pytest run from the nested Django dir
  (`laserforce_simulator/`). The full suite is the orchestrator's job.
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
- Ownership is filtered on the server through the `manager` seam.
- If you need a server, it goes on a free port, never 8000. Before reporting,
  stop it with `powershell -NoProfile -File scripts/stop_worktree_servers.ps1`
  through the PowerShell tool (it defaults to the current worktree; the Bash
  tool's worktree guard refuses it): stopping the npm wrapper alone leaves node,
  esbuild and dart running, and they block `git worktree remove`.
- When tickets run in parallel, they share one MCP Chrome. Open your own page
  with `new_page`, close only that page, and leave `close_mcp_browser.ps1` to
  the orchestrator, who runs it once after the last agent reports. This
  overrides the reuse-the-tab rule in `CLAUDE.md`.
- Commit only on your ticket branch. Merge the integration tip into it before
  reporting done. Comment the commit hash on your ticket issue.

## Step 7: Verify

Confirm every ticket landed on the integration branch
(`git log origin/main..HEAD --oneline`, `git status`). Then check the agents' work by
hand (`CLAUDE.md`, *Sub-Agent Delegation*): off-by-one in tick math, constant
mismatches, field-name drift between spec and code, missing seam methods.
**Every gap an implementer reports against a spec invariant** ("not covered",
"unlikely", "edge case") gets a red test before merge: it is a test to write,
not a judgement call. (ANG-05: the one real bug was such a reported gap.)

1. **Full suite** on SQLite (`python -m pytest laserforce_simulator`) and, when
   `docker info` succeeds, on Postgres as the `CLAUDE.md` *Database* bullet
   "Test suite on Postgres" says, plus
   `python laserforce_simulator/manage.py makemigrations --check --dry-run`.
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
   formula, a condition), never an auth or permission guard: the auto-mode
   classifier refuses a run against a removed guard.
3. **Browser check** through Chrome DevTools MCP when anything a person sees
   changed, following `CLAUDE.md`'s *Browser Sessions* rules (reuse the
   existing tab, close what you open, close the browser when done). Server on 8001;
   for the Angular SPA, `npm --prefix frontend run start:worktree` (4201, API on
   8001) with Django started as `frontend/CLAUDE.md` **API and auth** says.
   Assert on `innerText` or the API response body, not a status code. Check
   from a second Account when ownership changed. An API-only task checks the
   endpoints in the browsable API or with the test client instead, and says
   which. A test-client script runs through `manage.py shell -c
   "exec(open(r'<path>').read())"` with `APIClient(SERVER_NAME="localhost")`:
   outside pytest the default host `testserver` is not in `ALLOWED_HOSTS`.
   Clean up the test data the pass created.

## Step 8: Review

Call `mattpocock-skills:code-review` with fixed point `origin/main`. The Spec axis
reads the spec issue and its tickets; the Standards axis reads
`CODING_STANDARDS.md` plus Fowler's smells. Give both reviewers the main
checkout's `.venv\Scripts\python.exe` for any Python or pip call, so they can
verify installs and runtime claims. Show both reports. Fix the
findings the owner agrees with in one implementer pass, re-run the affected
tests, and review again only if a fix changed behaviour. The fix pass is
the orchestrator's own work, not a ticket: tell its agent to **stage** its
files and report, not commit (the auto-mode classifier refuses its commit).
Read the staged files with Read (`git -C` into its worktree is refused),
copy them onto the integration branch, re-run the frontend / Python checks
there, and commit them yourself.

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
Closes #<ticket> (one line per ticket)
```

A migration that drops or rewrites data is a one-way door. Say so plainly.

**Stop and show the owner the commit summary and PR body.** Commit, push and
`gh pr create --body-file` only on their "go" (`CLAUDE.md`, *Git Workflow*).
Then wait for the PR's CI run in the background:
`gh run list --branch <branch> --json databaseId -q '.[0].databaseId'` gives
the run id, and `gh run watch <run-id> --exit-status` waits on it.
(`gh pr checks --watch` exits early while checks are still pending.) Then
read `gh pr checks <n>`. Every job must have **started**: a
run with no jobs, or one GitHub reports as "a workflow file issue", is a
broken guardrail, not a pass (ANG-04 found CI rejected for months). Triage a
red job as in Step 7 and report it to the owner with the PR link.
Then stop again: the owner reviews and says when to merge. PRs are
**squash-merged**, so after the merge `git fetch origin` and confirm `git diff
<integration tip> origin/main -- <the paths the PR touched>` is empty: the work
is in `main` under a new hash (other PRs that merged meanwhile show up in an
unscoped diff). Then, for every implementer worktree, stop its servers with
`scripts/stop_worktree_servers.ps1 -Worktree <path>` (through the PowerShell
tool), `git worktree remove --force <path>`, and `git branch -D` its branch.
Leave the integration worktree with `ExitWorktree` (`action: "remove"`,
`discard_changes: true`, after stopping its servers too) and delete the remote
branch. If the main checkout is on `main`, `git pull --ff-only` there.
Otherwise run `git fetch origin main:main` (moves the local ref without a
checkout), leave the checkout alone, and tell the owner which branch it is on.

## Step 10: Retro

Before the owner clears the session, offer `retro`. It only **suggests**
changes to the setup: a navigation pointer, a check instead of a rule, a
`CLAUDE.md` line to cut or move to `CODING_STANDARDS.md`. The owner picks
which to keep, and the kept ones land as their own small PR. Never apply a
retro finding unasked.
