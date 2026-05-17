---
name: code-review
description: Review all changes on the current branch vs main as a senior developer — covers code quality, test coverage, optimizations, and cleaner code suggestions.
---

## Step 1 — Gather changed files

This is a fixed git operation, not a judgement call. Use the shared helper
(single source of truth, also used by `verify` and `update-docs`):

```
python .claude/skills/verify/scripts/changed_files.py --base main --mode branch
```

The JSON it prints already has the files deduplicated and bucketed:
`python`, `templates`, `migrations`, `other`, plus `pytest_targets` (the
authoritative test mapping — use these in Step 3, do not hand-derive them).

If `nothing_changed` is `true`, output "No changes detected vs main." and stop.

## Step 2 — Read the full diff

```powershell
git diff main...HEAD
```

Read all changed Python files in full with the Read tool so you have complete context — the diff alone misses surrounding code.

## Step 3 — Review each changed file

For every changed Python file, evaluate the following dimensions and collect findings by severity:

### Code Quality
- Naming: are variables, functions, and classes named clearly and consistently with the rest of the file?
- Complexity: are functions doing more than one thing? Flag any function >25 lines that could be split.
- Django patterns: prefer `get_object_or_404`, `select_related`/`prefetch_related`, model methods over view-level logic. Flag raw SQL unless justified.
- Type hints: all public function signatures must have type hints (project rule). Flag any missing.
- Dead code: unused imports, unreachable branches, commented-out blocks.

### Test Coverage
- For every new public function or method, confirm a test exists covering the happy path AND at least one edge case/failure. The relevant test locations are the `pytest_targets` reported by `changed_files.py` in Step 1 (authoritative — do not hand-maintain a mapping here; it drifts).
- For bug fixes, confirm a regression test is present that would have caught the bug.
- Flag any new code paths (branches, exception handlers) with no test coverage.
- Warn if tests use mocks in place of real behavior (project rule: prefer real in-memory objects over mocks).

### Optimizations
- N+1 queries: are querysets inside loops? Suggest `select_related`/`prefetch_related`.
- Repeated computation: values computed multiple times in a loop that could be hoisted.
- Memory: large lists materialised when a generator or queryset slice would do.
- Simulation-specific: tick-loop code is hot — flag any O(n²) or repeated dict lookups that should be cached.

### Cleaner Code
- Suggest list/dict comprehensions where explicit loops are used for simple transforms.
- Suggest `dataclasses` or named tuples for ad-hoc dicts with fixed keys.
- Flag duplicated logic that should be extracted to a helper.
- Suggest more Pythonic idioms (e.g. `any()`/`all()` over manual loops, `zip()`, `enumerate()`).

## Step 4 — Output the review

Present findings in this format:

```
CODE REVIEW — <branch> vs main
════════════════════════════════════════

<file path>
────────────────────────────────────────
[CRITICAL]  <finding — line N>
[WARNING]   <finding — line N>
[SUGGEST]   <finding — line N>

... (repeat per file)

════════════════════════════════════════
SUMMARY
  Files reviewed : N
  Critical       : N  (must fix before merge)
  Warnings       : N  (should fix)
  Suggestions    : N  (nice to have)

VERDICT: APPROVE / REQUEST CHANGES
```

**Severity guide:**
- `CRITICAL` — bug risk, security issue, missing test for new public API, broken type hints that would be caught by mypy
- `WARNING` — N+1 query, missing edge-case test, logic duplication, poor naming
- `SUGGEST` — style/idiom improvement, minor refactor, optional optimization

Keep each finding to one line. Do not quote large blocks of code — reference file and line number only.
If there are no findings for a file, write `  ✓ No issues found.`