---
name: update-docs
description: Sync all documentation (CLAUDE.md files, PLAN.md, README.md, IMPLEMENTED_FEATURES.md) to reflect the current branch's changes.
---

## Step 1 — Identify what changed

```powershell
git diff main...HEAD --name-only
git diff main...HEAD --stat
git log main...HEAD --oneline
```

Read the full diff to understand what was added, changed, or removed:

```powershell
git diff main...HEAD
```

Categorise changes by app area: `teams/`, `matches/`, `core/`, root-level config, templates, migrations.

## Step 2 — Update app-level CLAUDE.md files

For each changed app area, read the corresponding CLAUDE.md:
- `laserforce_simulator/teams/CLAUDE.md`
- `laserforce_simulator/matches/CLAUDE.md`
- `laserforce_simulator/core/CLAUDE.md`
- `laserforce_simulator/matches/sim_helpers/CLAUDE.md`
- `laserforce_simulator/matches/management/commands/CLAUDE.md`

Update each file to reflect:
- New models, fields, or properties added/removed
- New views, URLs, or forms
- New management commands
- Changed simulation logic, weight functions, or mechanics
- New helper modules or utility functions
- Any architectural notes that changed (e.g. a column converted to a `@property`)

Only update files that are directly affected by the branch changes. Do not rewrite sections that were not touched.

## Step 3 — Update PLAN.md

Read `PLAN.md` in full.

For each completed task visible in the diff:
- Find the matching entry in PLAN.md (match by feature ID, e.g. FIX-01, MAP-01, STAT-02).
- If it is not already marked `- completed`, append `- completed` on the line after its description.
- If the implementation differed from the plan (e.g. a field was added that the plan didn't mention), add a brief `- note: <what changed>` line.

Do NOT add new plan items, modify future phases, or rewrite prose — only mark items done and add implementation notes where relevant.

## Step 4 — Update README.md

Read `README.md` in full.

Update only these sections if the changes warrant it:
- **Features** list: add bullet points for significant new user-visible capabilities. Remove bullets for features that were removed.
- **Tech Stack**: update if a new library was added to `requirements.txt` that is user-relevant (e.g. a new major dependency).
- **Getting Started / Commands**: update if new management commands were added or setup steps changed.

Do not rewrite the introduction or architecture overview unless they are factually wrong.

## Step 6 — Output a change summary

After all edits, print:

```
DOCS UPDATE SUMMARY
════════════════════════════════════════
Files updated:
  • <file> — <one-line description of what changed>
  • ...

PLAN.md tasks marked complete:
  • <ID> — <task name>
  • ...

No changes needed:
  • <file> — <reason>
════════════════════════════════════════
```

If a documentation file did not need updating, list it under "No changes needed" with a brief reason (e.g. "teams/CLAUDE.md — no teams/ files changed").