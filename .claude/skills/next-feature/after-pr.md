# next-feature: after the PR is open

Read from `SKILL.md` Step 9 the moment `gh pr create` returns, and run it in order. It is a
file of its own so that a compacted session reads it whole: MECH-21's summary cut the end
of `SKILL.md`, and the session watched CI with `gh pr checks` and skipped the squash diff,
the main run, the desktop check and the prune.

## 1. CI

Wait for the PR's CI in the background with
`<main checkout>/.venv/Scripts/python.exe scripts/watch_pr_ci.py <n>`: one plain command that
waits until every check and workflow run has settled, then prints each check,
a `run: <workflow> <id>` line per run (pass that id to `ci_timings.py` and the next
`--after-run`), the mergeable state and `result: pass|fail` (exit 0 / 1; 2 = timeout).
Every job must have **started**: a timeout, a
run with no jobs, or one GitHub reports as "a workflow file issue", is a
broken guardrail, not a pass (ANG-04 found CI rejected for months). Triage a
red job as in Step 7 and report it to the owner with the PR link. When the
owner reports a red or unmergeable PR, run `ci_timings.py <run-id>` (each
shard's pytest line) and `gh pr view <n> --json mergeable,mergeStateStatus`
first: a raw shard log mixes in the Postgres service's expected
`duplicate key` errors (DESK-01).
A task that changes CI time claims its timing as `special-tasks.md` says.

Then stop again: the owner reviews and says when to merge.

## 2. After the merge

Done when every step below has run and its result is reported to the owner.

1. PRs are **squash-merged**, so `git fetch origin` and confirm `git diff
   <integration tip> origin/main -- <the paths the PR touched>` is empty: the work
   is in `main` under a new hash (other PRs that merged meanwhile show up in an
   unscoped diff).
2. For every implementer and fix-pass worktree, stop its servers with
   `scripts/stop_worktree_servers.ps1 -Worktree <path>` (through the PowerShell
   tool), `git -c core.longpaths=true worktree remove -f -f <path>` (a finished agent's lock
   outlives it, and one `--force` refuses a locked worktree; without long paths a frontend
   worktree's `node_modules` fails half way with "Filename too long", leaving the folder
   unregistered: finish it with `Remove-Item -LiteralPath "\\?\<path>" -Recurse -Force`), and
   `git branch -D` its branch and its `worktree-agent-*` branch.
   `python scripts/prune_agent_branches.py --delete` then clears any older agent branches
   already merged into `origin/main`, and lists every `agent-*` worktree still on disk with its
   state: remove each one this pipeline left.
3. Leave the integration worktree with `ExitWorktree` (`action: "remove"`,
   `discard_changes: true`, after stopping its servers too). One entered by
   `path` survives `remove`: `ExitWorktree` with `keep`, then `git -c core.longpaths=true worktree
   remove --force <path>` and `git branch -D <task>`. GitHub deletes the remote branch on
   merge.
4. If the main checkout is on `main`, `git pull --ff-only` there.
   Otherwise run `git fetch origin main:main` (moves the local ref without a
   checkout), leave the checkout alone, and tell the owner which branch it is on.
5. Wait for the `main` run (`gh run list --branch main --workflow CI --limit 1`, then
   `gh run watch <id>`), started after leaving the worktree: a background command keeps its
   working directory, and Windows then refuses to delete the folder (BUG-WV-01).
6. Run `gh release view desktop-latest --json tagName,url,assets`, confirm
   `git ls-remote origin refs/tags/desktop-latest` is `origin/main`'s commit, and give the owner the
   download link (`.../releases/download/desktop-latest/LaserforceSimulator-windows-x64.zip`). A red
   or skipped `desktop-publish` means the Latest desktop build is still the previous merge's: say so
   (DESK-02).

Then go to Step 10 (Retro) in `SKILL.md`.
