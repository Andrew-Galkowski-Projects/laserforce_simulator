---
name: verify
description: Run black, ruff, the encoding check (CI's lint step), the GLOSSARY avoid-word warning, mypy on changed lines, pytest on changed files, and the frontend lint / Prettier / Vitest when frontend/ changed, and output a single pass/fail summary.
---

Every step of this check is a fixed, repeatable operation — collecting changed
files, running the tools, mapping changes to test targets, formatting a table.
That is a script, not a judgement call, so it lives in
[`scripts/verify.py`](scripts/verify.py).

## Run it

From anywhere in the repo:

```
python .claude/skills/verify/scripts/verify.py
```

Use the venv's python (e.g. `.venv\Scripts\python.exe` on Windows): every
tool runs as `python -m <tool>` from that same interpreter, so the venv does
not need to be on PATH.

The script:

1. Computes changed files via the shared
   [`scripts/changed_files.py`](scripts/changed_files.py) helper (mode
   `working`: vs `HEAD` plus unstaged; on a clean tree it falls back to mode
   `branch`, vs `main`, and the summary's second line names which ran;
   deleted files are skipped).
2. Runs `black --check`, `ruff check` (the narrow F401/F811 set from
   `pyproject.toml`) and `mypy --follow-imports=silent` on the changed `.py`
   files, and `scripts/check_encoding.py` on the changed text files. A step
   with nothing to check is SKIP (counts as pass). mypy keeps only errors on
   lines changed vs `HEAD` (a new untracked file keeps all of them): CI does
   not run mypy, so an older error in a touched file reads `PASS
   pre-existing errors only`, not FAIL.
   `scripts/check_glossary_avoid.py` reads `GLOSSARY.md`'s quoted
   multi-word `_Avoid_:` terms and flags added lines that use them (any case,
   `_` / `-` / camelCase joins). A hit is WARN, which never fails the run:
   rename it, or say in the PR why the word is right there.
   `scripts/check_unused_private.py` (the `dead-code` row) flags a
   module-level `_private` def or class in a changed file that nothing in the
   repo references; an `import` alone is not a use. WARN as well: delete it,
   or say why it stays (reached only through `getattr`).
   `scripts/check_sqlite_json_lookups.py` (the `sqlite` row, also in CI's
   lint step) FAILs on a JSONField `__contains` / `__contained_by` lookup:
   SQLite, the dev database, raises `NotSupportedError` for both. Filter on a
   relation or check the JSON in Python.
3. Derives the pytest targets from the authoritative app→tests mapping in
   `changed_files.py` and runs `pytest -q` against them (full suite only if a
   change matched no mapping; SKIP when no Python or Django template changed).
3b. When anything under `frontend/` changed, runs `npm run lint`,
   `format:check` and `test:ci` in `frontend/` as the `ng-lint`, `prettier`
   and `vitest` rows (SKIP otherwise). A missing `frontend/node_modules` is a
   FAIL that says to run `npm --prefix frontend ci`.
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
