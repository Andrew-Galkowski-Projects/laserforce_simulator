---
name: update-docs
description: Sync all documentation (app CLAUDE.md indexes, docs/agents feature docs, PLAN.md, README.md) to reflect the current branch's changes.
---

## Step 1 — Identify what changed

The file inventory is a fixed git operation — use the shared helper (single
source of truth, also used by `verify`), so the bucketing
matches that skill exactly:

```
python .claude/skills/verify/scripts/changed_files.py --base main --mode branch
```

Its JSON gives the deduplicated change set already bucketed into `python`,
`templates`, `migrations`, `other`. If `nothing_changed` is `true`, report
"No changes vs main" and stop.

Then read the human context the helper deliberately leaves to judgement:

```powershell
git diff main...HEAD --stat
git log main...HEAD --oneline
git diff main...HEAD
```

Categorise the changed files by app area (`teams/`, `matches/`, `core/`,
root-level config, templates, migrations) using the buckets above.

## Step 2 — Update app docs (feature doc + index pointer)

App `CLAUDE.md` files are auto-loaded whenever an agent touches that app, so
they are **indexes**: what the app is, where things live, project-wide rules,
and one pointer line per feature doc. Feature detail lives in
`docs/agents/<app>/<file>.md`.

For each changed app area, read its index:
- `laserforce_simulator/teams/CLAUDE.md`
- `laserforce_simulator/matches/CLAUDE.md` (index for `docs/agents/matches/`)
- `laserforce_simulator/core/CLAUDE.md`
- `laserforce_simulator/accounts/CLAUDE.md`
- `laserforce_simulator/matches/sim_helpers/CLAUDE.md`
- `laserforce_simulator/matches/management/commands/CLAUDE.md`

Then write the feature notes:
- **Feature detail goes in a feature doc.** Use the index's pointer lines to
  pick the `docs/agents/<app>/<file>.md` that owns the area and append a
  `## <TASK-ID> <title>` section there (models / fields, views, URLs, forms,
  DOM ids, mechanics, locked names, scope-out). No owning doc fits ⇒ create a
  new `docs/agents/<app>/<topic>.md` with a `# <app>: <topic>` heading. Links
  in a doc are relative to `docs/agents/<app>/` (repo root is `../../../`).
- **The index gets at most one pointer line per new doc**, trigger first:
  `- <features a reader would search for> — \`docs/agents/<app>/<file>.md\``.
  An existing doc only needs its pointer's trigger list extended. Change the
  index body only for a new module location, service layer, or project-wide
  rule (e.g. the **API conventions**).
- Apps without a `docs/agents/<app>/` folder still keep notes in their
  `CLAUDE.md`; once one passes a few hundred lines, split it the same way.

Only update docs directly affected by the branch changes. Do not rewrite sections that were not touched.

## Step 3 — Update PLAN.md

Read `PLAN.md` in full.

A task that is a numbered item under an umbrella heading (ANG-06's `5c. **Title (ID)**`)
moves with the same script.

For each completed task visible in the diff:
- Find the matching entry in PLAN.md (match by feature ID, e.g. FIX-01, MAP-01, STAT-02).
- Write its shipped note: a few sentences on what shipped and, where the implementation differed from the plan (e.g. a field the plan didn't mention), what changed.
- Then move the finished section with `python scripts/move_done_task.py <ID> "<shipped note>"` (through the Bash tool, which keeps the quotes; `--date YYYY-MM-DD` for a past merge date). It marks the heading `[DONE]`, inserts `**[DONE] Shipped (<date>).** <note>`, appends the section under the same `##` heading in `PLAN-completed.md` (adding it if missing), leaves a one-line pointer and drops the ID from the **Sequencing Summary**. Fix by hand each line it prints a `WARNING` for (a range or prose mention it won't edit). `PLAN.md` holds open work only (see CLAUDE.md, **Issue tracker**).

Do NOT add new plan items, modify future phases, or rewrite prose — only mark items done and add implementation notes where relevant.

## Step 4 — Update README.md

Read `README.md` in full.

Update only these sections if the changes warrant it:
- **Features** list: add bullet points for significant new user-visible capabilities. Remove bullets for features that were removed.
- **Tech Stack**: update if a new library was added to `requirements.txt` that is user-relevant (e.g. a new major dependency).
- **Getting Started / Commands**: update if new management commands were added or setup steps changed.

Do not rewrite the introduction or architecture overview unless they are factually wrong.

## Step 5 — Resolve forward references

Docs written before the task promise its arrival ("stays 403 until ANG-03",
"ANG-03 will add…"); once it ships they are stale. Search every Markdown file,
`CODING_STANDARDS.md` and the ADRs included:

```
git grep -n "<TASK-ID>" -- ":/*.md"
```

Rewrite each forward reference to the shipped state. When the task changed a
documented rule (a status code, a default, a limit), also `git grep` the old
value in the docs and correct every statement of the old rule. Leave history
alone: an ADR's context paragraph, PLAN.md grill records, `[DONE]` notes.

Done when the grep shows no line that still describes the task as future.

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