---
name: pr-ready
description: Get the current branch ready for a PR — verify a feature branch, run the full pytest suite, check for missing migrations, draft a PR template, and STOP for explicit user approval before any commit or push. Use when the user says "pr ready", "prep this PR", "ready to commit", "draft a PR", or invokes /pr-ready.
---

This skill is a hand-off checkpoint, not an autopilot. Run the checks, draft the
PR body, then **stop**. The user must say "go" before you run `git commit` or
`git push` — see the project rule in `CLAUDE.md` under "Git Workflow".

## Step 1 — Verify on a feature branch

```powershell
git branch --show-current
```

- If the branch is `main`, **do not commit there.** Create a feature branch
  from the current working tree:
  ```powershell
  git switch -c <feature-name>
  ```
  Pick a short kebab-case name that reflects the change (ask the user if it
  isn't obvious from the diff). Re-run `git branch --show-current` to confirm.
- If already on a non-`main` branch, continue.

Do **not** prepend `cd` to any git command (project rule — see `CLAUDE.md`
"Tooling": a stray `cd`-then-git sequence has corrupted the working tree
before).

## Step 2 — Run the full pytest suite

```powershell
python -m pytest laserforce_simulator
```

Report the exact pass/fail counts (e.g. `877 passed, 0 failed`), not a vague
"tests pass". If anything fails, surface the failing test names and stop —
the PR is not ready until the suite is green.

## Step 3 — Check for missing migrations

```powershell
python laserforce_simulator/manage.py makemigrations --check --dry-run
```

- Exit code `0` → no model drift, continue.
- Non-zero → unapplied model changes. Tell the user which app needs a
  migration and stop; do **not** auto-generate it.

## Step 4 — Draft the PR template

Gather the diff context:

```powershell
git status
git diff main...HEAD --stat
git log main..HEAD --oneline
```

Output the PR body in this format (do not write it to a file — just print it
in the response so the user can copy/edit):

```
PR TITLE: <short imperative, <70 chars>

## Summary
- <1–3 bullets on the WHY, not a file-by-file recap>

## Test plan
- [ ] pytest: <N passed, M failed>
- [ ] makemigrations --check: <clean / drift in app X>
- [ ] <any manual verification the change needs — UI flows, management
      commands, etc. Omit if purely internal.>

## Files changed
<paste `git diff --stat` output verbatim>
```

Keep the summary focused on intent. The reviewer can read the diff for the
"what"; the PR body's job is the "why".

## Step 5 — STOP

End the response with an explicit prompt for approval, e.g.:

> Ready to commit and push. Say "go" to proceed, or tell me what to change.

Do **not** run `git add`, `git commit`, or `git push` in this turn. Wait for
the user's explicit go-ahead in their next message.
