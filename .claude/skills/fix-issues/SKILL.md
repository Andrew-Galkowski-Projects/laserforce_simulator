---
name: fix-issues
description: Pull the highest-severity open bug from issues.md, fix it test-first, prove the fix in-browser with Chrome DevTools MCP, strike it through, commit, and loop until no open code bugs remain. Use when the user wants to work through issues.md, fix the logged bug backlog, squash issues.md, clear website-testing findings, or invokes /fix-issues.
---

# Fix Issues

Drain `issues.md` one bug at a time. Each bug: reproduce → test-first fix →
local gate → **Chrome MCP browser gate** → strike → commit. Loop until the
helper reports `NO_OPEN_BUGS`.

The deterministic parts — picking the next bug by severity and striking it in
two places — live in [`scripts/next_bug.py`](scripts/next_bug.py). Do not
eyeball selection or hand-edit the strikethrough; drift there breaks loop
termination.

## Setup (once per invocation)

1. Read `issues.md` for format and current state.
2. Note the current branch. Per the user's standing choice this skill commits
   **one commit per bug on the current branch** — do NOT create branches.
   If the working tree has unrelated uncommitted changes, surface that and
   ask before starting (per-bug commits must stay clean).
3. Apply migrations, then start the dev server in the background (issues.md
   E-1: a fresh server has unapplied migrations):
   `python laserforce_simulator/manage.py migrate` then
   `python laserforce_simulator/manage.py runserver` (background). Confirm
   `127.0.0.1:8000` responds.
4. Open a Chrome DevTools MCP page (ToolSearch the `mcp__chrome-devtools__*`
   tools). Keep server + page alive for the whole loop.

## Per-bug loop

Run `python .claude/skills/fix-issues/scripts/next_bug.py`. If it prints
`NO_OPEN_BUGS`, stop — go to Teardown. Otherwise it prints the bug's ID,
severity, area, one-liner, and full detail section. Then:

1. **Locate** the root cause. Detail sections usually name the
   `file:line` — start there, but confirm in the code.
2. **Reproduce (browser red).** Navigate Chrome MCP to the affected page and
   observe the exact symptom from the detail section. If it does NOT
   reproduce (stale/env), strike it
   `--tag "skipped: not reproducible"`, note it for the report, and
   `continue` the loop — never commit a non-reproducible item.
3. **Test-first** (project TDD is mandatory — see [/tdd](../tdd/SKILL.md)).
   Write ONE failing pytest regression test in the file dictated by
   CLAUDE.md placement rules. Run it; confirm it fails for the right
   reason. For pure template/markup bugs use template/view assertions
   (`assertContains` / `assertNotContains`); if no meaningful unit test is
   feasible, say so explicitly and lean on the browser gate.
4. **Fix** with the minimum change. No refactor while red. No speculative
   extras.
5. **Local gate.** `python .claude/skills/verify/scripts/verify.py`
   (black + pytest on changed files). Must be green before proceeding.
6. **Browser gate (mandatory acceptance).** Reload the affected page in
   Chrome MCP. Confirm the exact symptom is gone and the console / network
   for that page is clean. Still broken → iterate the fix at most twice;
   if still failing, do NOT strike or commit — halt and report the bug
   plus what you tried.
7. **Strike.** `next_bug.py --strike <ID> --tag fixed` (strikes the
   Summary-table row and the detail header in place).
8. **Commit** on the current branch — the code fix, the new test, and the
   `issues.md` strikethrough together:

   ```
   fix(<area>): <ID> — <one-liner>

   <1-2 lines: root cause and the fix. Browser-verified.>

   Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
   ```
9. Loop back to the top.

## Rules

- Never prepend `cd` to any command, especially `git` (project rule). Run
  black/pytest by path argument; the agent worktrees share one `.git`.
- One bug per iteration. One commit per bug. Highest severity first;
  `ℹ️` notes are skipped by the helper by design.
- The browser gate is non-negotiable: no green Chrome MCP check → no strike,
  no commit.

## Teardown

Stop the background dev server. Report a table: bugs **fixed** (ID +
commit subject), **skipped** (ID + why, e.g. not reproducible), and any
**halted** (ID + what failed). Note if `NO_OPEN_BUGS` was reached.
