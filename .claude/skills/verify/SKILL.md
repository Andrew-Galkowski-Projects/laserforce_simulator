---
name: verify
description: Run black, mypy, and pytest on changed files and output a single pass/fail summary.
---

Every step of this check is a fixed, repeatable operation — collecting changed
files, running three tools, mapping changes to test targets, formatting a table.
That is a script, not a judgement call, so it lives in
[`scripts/verify.py`](scripts/verify.py).

## Run it

From anywhere in the repo:

```
python .claude/skills/verify/scripts/verify.py
```

The script:

1. Computes changed `.py` files via the shared
   [`scripts/changed_files.py`](scripts/changed_files.py) helper (mode
   `working`: vs `HEAD` plus unstaged).
2. Runs `black --check` and `mypy` on those files (skips, marked PASS, if none).
3. Derives the pytest targets from the authoritative app→tests mapping in
   `changed_files.py` and runs `pytest -q` against them (full suite only if a
   change matched no mapping).
4. Prints the canonical `VERIFY SUMMARY` table and exits non-zero iff OVERALL
   is FAIL.

## Your job

Relay the `VERIFY SUMMARY` table verbatim. Do **not** re-derive file lists or
re-run the tools by hand — that is exactly the drift this script exists to
prevent.

The only judgement here is **interpreting failures**: when a step is FAIL, read
the specific error lines the script surfaced, explain the likely cause, and (if
asked) fix it. Do not dump full tool output into the conversation — the table
plus the actionable error lines is enough.
