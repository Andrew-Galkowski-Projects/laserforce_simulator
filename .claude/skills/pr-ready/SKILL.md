---
name: pr-ready
description: Get the current branch ready for a PR — verify a feature branch, run the full pytest suite, check for missing migrations, run the frontend lint / tests / build when frontend/ changed, draft a PR template, and STOP for explicit user approval before any commit or push. Use when the user says "pr ready", "prep this PR", "ready to commit", "draft a PR", or invokes /pr-ready.
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

## Step 2b - Run the full suite on PostgreSQL (when Docker is available)

CI runs the suite on `postgres:16`, the canonical database. If `docker info`
succeeds, reuse the throwaway container (`docker start lf-test-pg`) or, when
it does not exist yet, create it:

```powershell
docker run -d --name lf-test-pg -e POSTGRES_USER=laserforce -e POSTGRES_PASSWORD=laserforce -e POSTGRES_DB=laserforce_test -p 5433:5432 postgres:16
```

Then run the suite against it through the **Bash tool**:

```bash
DATABASE_URL=postgres://laserforce:laserforce@localhost:5433/laserforce_test python -m pytest laserforce_simulator
```

Report the exact counts, as in Step 2. A failure stops the PR. If Docker is
not available, say so plainly: `Postgres run skipped: Docker unavailable`.

## Step 3 — Check for missing migrations

```powershell
python laserforce_simulator/manage.py makemigrations --check --dry-run
```

- Exit code `0` → no model drift, continue.
- Non-zero → unapplied model changes. Tell the user which app needs a
  migration and stop; do **not** auto-generate it.

## Step 3b — Frontend checks (when `frontend/` changed)

If `git diff main...HEAD --stat` touches `frontend/`, run CI's `frontend`
job locally (`npm --prefix frontend ci` first if `frontend/node_modules` is
missing):

```powershell
npm --prefix frontend run lint
npm --prefix frontend run format:check
npm --prefix frontend run test:ci
npm --prefix frontend run build
```

Report each result, with the Vitest pass/fail counts. Any failure stops the
PR, the same as a red pytest run.

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
- [ ] pytest on Postgres: <N passed, M failed / skipped: Docker unavailable>
- [ ] makemigrations --check: <clean / drift in app X>
- [ ] frontend: lint / format:check / test:ci <N passed> / build (omit if
      `frontend/` is untouched)
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
