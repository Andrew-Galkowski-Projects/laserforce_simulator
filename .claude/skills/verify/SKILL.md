---
name: verify
description: Run black, mypy, and pytest on changed files and output a single pass/fail summary.
---

1. Identify changed Python files by running:
   ```
   git diff --name-only HEAD
   ```
   Also include any unstaged changes:
   ```
   git diff --name-only
   ```
   Combine and deduplicate. Filter to `.py` files only.

2. **Black** — check formatting on changed files (do not auto-fix):
   ```
   black --check <changed .py files>
   ```
   If no changed `.py` files, skip and mark as PASS.

3. **Mypy** — type-check changed files only:
   ```
   mypy <changed .py files>
   ```
   If no changed `.py` files, skip and mark as PASS.

4. **Pytest** — determine affected test modules:
   - For each changed file, derive the corresponding test file using these mappings:
     - `matches/**` → `matches/tests/simulation_tests.py` and `matches/tests.py`
     - `teams/**` → `teams/tests.py`
     - `core/**` → `core/tests.py`
   - If a changed file IS a test file, include it directly.
   - Deduplicate. Run only the derived test files:
     ```
     pytest <test files> -q
     ```
   - If no mapping can be determined, run the full suite: `pytest -q`

5. Output a single summary table:

   ```
   VERIFY SUMMARY
   ──────────────────────────────
   black    PASS / FAIL  <brief note if failed>
   mypy     PASS / FAIL  <brief note if failed>
   pytest   PASS / FAIL  <N passed, N failed>
   ──────────────────────────────
   OVERALL  PASS / FAIL
   ```

   Do not dump full tool output into the conversation — only include the table plus any specific error lines needed to act on failures.