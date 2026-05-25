# HX-01b Seam Contract

**Branch:** `hx-01b-12-stat-benchmark` (already created).
**Goal:** Extend the HX-01 per-player career page per-role table from 5 stat rows to **15 rows per role actually played** (5 existing HX-01 display stats + 10 net-new HX-02 STAT_KEYS rows). Layout pivots from one wide row-per-role table to **one table per role with one row per stat**.

This contract is the single source of truth for every name, shape, DOM id, signature, formatter, label, file path, and test boundary the three parallel agents (Code / Tests / Docs) must agree on. Anything not in this file is out of scope.

---

## Scope

In:
- Extend `teams/views.py::_build_per_role_overlay` to additively emit a new `stat_rows` list on every per-role row.
- Pivot `templates/teams/player_career_stats.html`: replace the single wide `<table id="career-per-role-table">` with `<section id="career-per-role-table">` containing one `<table id="career-per-role-table-{role}">` per role actually played, each with 15 rows (one per stat). Columns: `Stat | Player value | Mean | Median | Δ | Percentile | n`.
- Append ~6 new TestCase methods to `teams/tests/test_role_benchmarks_view.py`.
- Docs: extend `laserforce_simulator/teams/CLAUDE.md` HX-01 / HX-02 subsections noting the layout pivot and the 15-row spec; mark HX-01b `- completed` in `PLAN.md` with a dense implementation note; patch PLAN line 491's "7 net-new" to "10 net-new" (stale arithmetic).

Out:
- HX-01 pure module (`teams/career_stats.py`) — **not touched**.
- No new pure module, no new cache work, no migration, no ADR, no CONTEXT.md edit.
- No new context key on the view (additive change is on the existing `per_role_with_benchmarks` rows only).
- No URL change, no new view function.

---

## Files in scope (writable)

1. `laserforce_simulator/teams/views.py` — extend `_build_per_role_overlay` to additively emit `stat_rows`. **Keep `benchmarks_by_stat` dict unchanged** (preserves back-compat with `test_per_role_with_benchmarks_contains_benchmarks_by_stat`). No new view function, no new context key on `player_career_stats`.
2. `laserforce_simulator/templates/teams/player_career_stats.html` — replace the single `<table id="career-per-role-table">` block with `<section id="career-per-role-table">` containing one `<table id="career-per-role-table-{role}">` per role actually played, each with 15 rows.
3. `laserforce_simulator/teams/tests/test_role_benchmarks_view.py` — append the new TestCases listed under [Test boundary](#test-boundary).
4. `laserforce_simulator/teams/CLAUDE.md` — docs agent extends the HX-01 / HX-02 subsections.
5. `PLAN.md` — docs agent marks HX-01b `- completed` + patches the "7 net-new" → "10 net-new" arithmetic on line 491.

## Files NOT in scope (must not be modified)

- `laserforce_simulator/teams/career_stats.py` — zero changes (PLAN line 491: "no new pure-module work").
- `laserforce_simulator/teams/role_benchmarks.py` — zero changes.
- `laserforce_simulator/teams/role_benchmarks_cache.py` — zero changes.
- `laserforce_simulator/teams/signals.py` — zero changes.
- `laserforce_simulator/teams/player_urls.py` — zero changes (URL unchanged).
- `laserforce_simulator/laserforce_simulator/urls.py` — zero changes.
- `laserforce_simulator/templates/teams/role_benchmarks.html` — zero changes.
- `laserforce_simulator/teams/tests/test_career_stats.py` — zero changes.
- `laserforce_simulator/teams/tests/test_role_benchmarks.py` — zero changes.
- `laserforce_simulator/teams/tests/test_role_benchmarks_cache.py` — zero changes.
- `CONTEXT.md` — zero changes (no new terms; `Tag ratio` / `Role benchmark` / `Percentile rank` already defined).
- `docs/adr/` — no new ADR (decision is reversible).
- Any `matches/` file — zero changes.
- No new migration.

---

## Public surface changes

**Signature unchanged:** `teams/views.py::_build_per_role_overlay(rounds, per_role, samples_by_key, rounds_in_role_by_role, player_id, min_rounds) -> list[dict]`. Same six positional arguments, same return type. The only change is that each row in the returned list gains an additional key `stat_rows` (described below); `benchmarks_by_stat` is **kept verbatim** for back-compat.

**Truth source for STAT_KEYS:** the 12-tuple at `teams/role_benchmarks.py:18`:

```
STAT_KEYS = (
    "points_scored", "mvp", "tags_made", "times_tagged", "accuracy",
    "final_lives", "resupplies_given", "missiles_landed", "specials_used",
    "follow_up_shots", "reaction_shots", "combo_resupply_count",
)
```

Net-new rows = `STAT_KEYS` minus `{points_scored, accuracy}` (the 2 already overlay-mapped via `_HX01_TO_BENCHMARK_STAT`) = **10 rows**. Plus the 5 HX-01 display rows = **15 total**.

---

## Context shape (per `per_role_with_benchmarks[i]` row)

Each row gains an additive `stat_rows` list (15 entries, frozen order). The legacy `benchmarks_by_stat` dict is preserved unchanged (5 HX-01 keys) for back-compat.

```python
row["stat_rows"] = [
    {
        "key": str,            # one of the 15 keys in the frozen order below
        "label": str,          # one of the 15 labels in the frozen order below
        "player_value": float, # source: see "Player value sources" section
        "benchmark": dict | None,  # None for the 3 HX-01-only rows; dict otherwise
    },
    # 15 entries total, in the frozen order below
]
# row["benchmarks_by_stat"]  → UNCHANGED (5 HX-01 keys), kept for back-compat
```

The `benchmark` dict (when not `None`) has the exact same shape today's `_build_per_role_overlay` returns from `player_position(...)`:

```python
{
    "benchmark_mean":   float | None,
    "benchmark_median": float | None,
    "delta_mean":       float | None,
    "delta_median":     float | None,
    "percentile":       float | None,
    "qualified":        bool,
    "n":                int,
}
```

For the 3 HX-01-only rows (`tag_ratio`, `avg_survival_ticks`, `avg_sp_earned`), the contract value is `benchmark = None` — **not** the all-`None` placeholder dict. The template branches on `is None`.

---

## Frozen 15-entry `stat_rows` order, labels, formatters, benchmark mapping

Order is **locked verbatim**. Pinned by `test_stat_rows_order_is_locked`.

| idx | `key`                    | `label`              | template formatter                       | benchmark stat (in `STAT_KEYS`) |
|----:|--------------------------|----------------------|------------------------------------------|---------------------------------|
|   0 | `avg_points`             | `Avg points`         | `\|floatformat:1`                         | `points_scored`                 |
|   1 | `tag_ratio`              | `Tag ratio`          | `\|floatformat:2`                         | `None` (HX-01-only)             |
|   2 | `avg_survival_ticks`     | `Avg survival`       | `\|div:2\|floatformat:0` + suffix `s`      | `None` (HX-01-only)             |
|   3 | `avg_accuracy_pct`       | `Avg accuracy`       | `\|floatformat:0` + suffix `%`            | `accuracy`                      |
|   4 | `avg_sp_earned`          | `Avg SP earned`      | `\|floatformat:1`                         | `None` (HX-01-only)             |
|   5 | `mvp`                    | `MVP score`          | `\|floatformat:2`                         | `mvp`                           |
|   6 | `tags_made`              | `Tags made`          | `\|floatformat:1`                         | `tags_made`                     |
|   7 | `times_tagged`           | `Times tagged`       | `\|floatformat:1`                         | `times_tagged`                  |
|   8 | `final_lives`            | `Final lives`        | `\|floatformat:1`                         | `final_lives`                   |
|   9 | `resupplies_given`       | `Resupplies given`   | `\|floatformat:1`                         | `resupplies_given`              |
|  10 | `missiles_landed`        | `Missiles landed`    | `\|floatformat:1`                         | `missiles_landed`               |
|  11 | `specials_used`          | `Specials used`      | `\|floatformat:1`                         | `specials_used`                 |
|  12 | `follow_up_shots`        | `Follow-up shots`    | `\|floatformat:1`                         | `follow_up_shots`               |
|  13 | `reaction_shots`         | `Reaction shots`     | `\|floatformat:1`                         | `reaction_shots`                |
|  14 | `combo_resupply_count`   | `Combo resupplies`   | `\|floatformat:1`                         | `combo_resupply_count`          |

**Notes on the order.**
- Rows 0–4 are the 5 HX-01 display stats in their current order (this is also the order in `_HX01_DISPLAY_STAT_KEYS` at `teams/views.py:240`).
- Rows 5–14 are the 10 net-new STAT_KEYS rows. They follow `STAT_KEYS` declaration order **skipping** `points_scored` (already at idx 0 as `avg_points`) and `accuracy` (already at idx 3 as `avg_accuracy_pct`).
- The benchmark stat name column references entries inside `STAT_KEYS`; for HX-01-only rows it is `None` and the `benchmark` field on that row is `None`.

**Mixed-namespace `key` field is intentional.** Rows 0–4 use the HX-01 display-stat names (`avg_points`, `tag_ratio`, `avg_survival_ticks`, `avg_accuracy_pct`, `avg_sp_earned`); rows 5–14 use raw `STAT_KEYS` names. This is locked — DOM ids and benchmark span ids derive from `key` verbatim (see [DOM ids](#dom-ids)).

---

## Player value sources

Pinned by `test_net_new_rows_subject_value_matches_compute_career_stat_for_role`.

**Rows 0–4 (HX-01 5 rows)** — pulled from the existing `summarize_by_role` row dict produced by the HX-01 pure module. No recomputation; the values are already on `row` before `_build_per_role_overlay` runs:

| idx | `key`                  | source field on the row dict |
|----:|------------------------|------------------------------|
|   0 | `avg_points`           | `row["avg_points"]`          |
|   1 | `tag_ratio`            | `row["tag_ratio"]`           |
|   2 | `avg_survival_ticks`   | `row["avg_survival_ticks"]`  |
|   3 | `avg_accuracy_pct`     | `row["avg_accuracy_pct"]`    |
|   4 | `avg_sp_earned`        | `row["avg_sp_earned"]`       |

**Rows 5–14 (10 STAT_KEYS net-new rows)** — call `compute_career_stat_for_role(player_role_rounds, stat)` from `teams/role_benchmarks.py` (same helper that already feeds the benchmark percentile path inside `_build_per_role_overlay`). Using that helper for both the subject value and the population aggregation guarantees the subject value and overlay are identical.

The `player_role_rounds` list passed to `compute_career_stat_for_role` is the **same** `rounds_by_role.get(role, [])` slice already built at the top of `_build_per_role_overlay` (see `teams/views.py:329-331`). Do not reslice or rebuild it.

The `player_value` field on the `stat_rows[i]` dict is a `float` in every case (cast HX-01 row values with `float(...)` if needed for type-uniformity in the contract; in practice HX-01's `career_stats.py` already returns floats).

---

## DOM ids

**Frozen.** Pinned by `test_per_role_table_dom_ids_present_per_role_played`.

| element | id |
|---------|----|
| outer wrapper section | `<section id="career-per-role-table">` |
| one table per role actually played | `<table id="career-per-role-table-{role}">` |
| one stat row inside each per-role table | `<tr id="career-stat-row-{role}-{key}">` |
| each benchmark cell `<span>` (unchanged from HX-02) | `<span id="benchmark-{role}-{key}-{component}">` where `component` ∈ `{mean, median, delta, percentile, n}` |

**Resolution rules:**
- `{role}` is the bare lowercase role string: `commander`, `heavy`, `scout`, `medic`, `ammo`.
- `{key}` is the row's `key` field **verbatim** (mixed namespace: HX-01 display keys for rows 0–4, STAT_KEYS names for rows 5–14). This is locked and intentional.
- The outer `<section id="career-per-role-table">` **preserves the HX-01-locked DOM id as a wrapper** so any cross-feature substring check for `id="career-per-role-table"` continues to match.
- One `<table id="career-per-role-table-{role}">` per role **actually played** (roles the subject has zero rounds in are omitted — same omission rule HX-01 uses for the per-role list).

---

## Empty-state UX

Mirrors today's `avg_points` / `avg_accuracy_pct` cells exactly. Three regimes:

### Below-threshold subject (qualified=False, n>0)

For each of the **12 benchmark-backed rows** (the 2 HX-01-mapped + 10 net-new):
- **mean cell** renders the population mean value.
- **median cell** renders the population median value.
- **n cell** renders the substring `n = {n}` (the `n = ` substring is pinned by existing HX-02 tests).
- **delta cell** renders the substring `— (need {min_rounds}+ rounds)` (locked substring; `min_rounds` is the active threshold value).
- **percentile cell** renders blank.

### HX-01-only rows (3 rows: `tag_ratio`, `avg_survival_ticks`, `avg_sp_earned`)

All 5 benchmark cells (mean / median / delta / percentile / n) render `—`. CSS class `benchmark-na` is applied on the wrapping `<td>` (carrying forward the HX-02 convention for unbenchmarked stats).

### Empty population (n=0 for any `(role, benchmark_stat)`)

- mean / median / delta / percentile cells render `—`.
- n cell renders `n = 0` (substring greppable by tests, same as HX-02 standalone page).

### Qualified subject with non-empty population (happy path)

All 5 benchmark cells render normal numeric values via the existing HX-02 formatting filters; the `qualified=True` branch is unchanged from today's `avg_points` / `avg_accuracy_pct` cell rendering.

---

## Test boundary

**Extends `teams/tests/test_role_benchmarks_view.py` only.** Six new Django `TestCase` methods minimum (agent may add edge-case extras but must not omit any of these six):

| # | test name | what it pins |
|---|-----------|-------------|
| a | `test_stat_rows_is_15_entry_ordered_list_per_role` | At least one role row in `per_role_with_benchmarks` has `stat_rows` with `len(row["stat_rows"]) == 15`. |
| b | `test_stat_rows_order_is_locked` | The 15-entry key sequence is `["avg_points", "tag_ratio", "avg_survival_ticks", "avg_accuracy_pct", "avg_sp_earned", "mvp", "tags_made", "times_tagged", "final_lives", "resupplies_given", "missiles_landed", "specials_used", "follow_up_shots", "reaction_shots", "combo_resupply_count"]` verbatim, for at least one role the player has played. |
| c | `test_hx01_only_rows_carry_none_benchmark` | For rows at indices **1**, **2**, **4** (`tag_ratio`, `avg_survival_ticks`, `avg_sp_earned`), `row["stat_rows"][i]["benchmark"] is None`. |
| d | `test_net_new_rows_subject_value_matches_compute_career_stat_for_role` | For each of the 10 STAT_KEYS rows on a role the subject has played, `stat_rows[i]["player_value"] == compute_career_stat_for_role(player_role_rounds, stat)` (use the same helper; build `player_role_rounds` via the same round-dict assembly the view uses, or fetch via the view's context). |
| e | `test_below_threshold_subject_renders_need_n_rounds_on_all_12_benchmarked_rows` | A single-round subject at `threshold=5` renders the substring `need 5+ rounds` for all 12 benchmark-backed rows in the response body (12 occurrences min — one per row × one role; assert via `response.content.decode().count("need 5+ rounds")` ≥ 12 across all role tables, or scope per-table via parsed DOM). |
| f | `test_per_role_table_dom_ids_present_per_role_played` | Response body contains `id="career-per-role-table-{role}"` for each role the subject has actually played; the wrapper `id="career-per-role-table"` is also present. |

**Existing tests that must stay green (do not modify):**

- `teams/tests/test_role_benchmarks_view.py::test_per_role_with_benchmarks_contains_benchmarks_by_stat` — `benchmarks_by_stat` dict preserved unchanged on every row.
- `teams/tests/test_role_benchmarks_view.py::test_role_benchmarks_link_rendered` — `role-benchmarks-link` substring still present.
- All `teams/tests/test_role_benchmarks.py` — untouched and green.
- All `teams/tests/test_role_benchmarks_cache.py` — untouched and green.
- All `teams/tests/test_career_stats.py` — untouched and green.

**Fixture guidance for the new TestCases:** the existing `setUp` / helper factories in `test_role_benchmarks_view.py` already seed enough `PlayerRoundState` rows across multiple roles to exercise the per-role iteration; reuse them. For test (d), import `compute_career_stat_for_role` directly from `teams.role_benchmarks` — this is **not** a Django-imports-leak violation because the test module is already a Django `TestCase` consumer.

---

## Guardrails / non-scope

- **Branch:** `hx-01b-12-stat-benchmark` — already created. Code, tests, and docs all commit to this branch.
- **Read-only view extension.** No RNG, no simulation, no `_flush_to_db` touch, no SIM-07 / SIM-08 contract interaction, no Score Calibration re-baseline, no model change, no migration.
- **No simulator test should change behaviour.** Any simulator-side test diff is a red flag.
- **No new pure module.** HX-01 `teams/career_stats.py` and HX-02 `teams/role_benchmarks.py` / `teams/role_benchmarks_cache.py` are zero-diff.
- **No new context key on `player_career_stats`.** The 10 HX-01/HX-02 keys already on the context (`player`, `total_rounds`, `career`, `per_role`, `trend`, `has_rounds`, `min_rounds`, `display`, `stat_keys`, `per_role_with_benchmarks`) are unchanged. The new data lives **inside** existing rows on `per_role_with_benchmarks` via the additive `stat_rows` key.
- **No URL change.** `/players/<int:player_id>/stats/` (URL name `player_career_stats`) is unchanged.
- **No CONTEXT.md edit.** No new domain terms — `Tag ratio` / `Role benchmark` / `Percentile rank` are already defined; the 10 net-new stat names are the same `STAT_KEYS` already documented under HX-02.
- **No ADR.** Decision is reversible (the change is a template + view-overlay extension; rolling back is a `git revert`).

---

## Worked example: `stat_rows` for one role row

For a player who has played 7 rounds as Heavy, with `min_rounds=5` (qualified), the `stat_rows` list for the Heavy `per_role_with_benchmarks[i]` row is:

```python
row["stat_rows"] == [
    {"key": "avg_points",           "label": "Avg points",        "player_value": <float>, "benchmark": {...HX-02 dict, n>0, qualified=True...}},
    {"key": "tag_ratio",            "label": "Tag ratio",         "player_value": <float>, "benchmark": None},
    {"key": "avg_survival_ticks",   "label": "Avg survival",      "player_value": <float>, "benchmark": None},
    {"key": "avg_accuracy_pct",     "label": "Avg accuracy",      "player_value": <float>, "benchmark": {...HX-02 dict...}},
    {"key": "avg_sp_earned",        "label": "Avg SP earned",     "player_value": <float>, "benchmark": None},
    {"key": "mvp",                  "label": "MVP score",         "player_value": <float>, "benchmark": {...HX-02 dict...}},
    {"key": "tags_made",            "label": "Tags made",         "player_value": <float>, "benchmark": {...HX-02 dict...}},
    {"key": "times_tagged",         "label": "Times tagged",      "player_value": <float>, "benchmark": {...HX-02 dict...}},
    {"key": "final_lives",          "label": "Final lives",       "player_value": <float>, "benchmark": {...HX-02 dict...}},
    {"key": "resupplies_given",     "label": "Resupplies given",  "player_value": <float>, "benchmark": {...HX-02 dict...}},
    {"key": "missiles_landed",      "label": "Missiles landed",   "player_value": <float>, "benchmark": {...HX-02 dict...}},
    {"key": "specials_used",        "label": "Specials used",     "player_value": <float>, "benchmark": {...HX-02 dict...}},
    {"key": "follow_up_shots",      "label": "Follow-up shots",   "player_value": <float>, "benchmark": {...HX-02 dict...}},
    {"key": "reaction_shots",       "label": "Reaction shots",    "player_value": <float>, "benchmark": {...HX-02 dict...}},
    {"key": "combo_resupply_count", "label": "Combo resupplies",  "player_value": <float>, "benchmark": {...HX-02 dict...}},
]
# AND in parallel (back-compat preserved):
row["benchmarks_by_stat"] == {
    "avg_points":         {...HX-02 dict, same as stat_rows[0]["benchmark"]...},
    "tag_ratio":          {"benchmark_mean": None, "benchmark_median": None, ...},  # all-None placeholder, UNCHANGED
    "avg_survival_ticks": {"benchmark_mean": None, ...},                              # all-None placeholder, UNCHANGED
    "avg_accuracy_pct":   {...HX-02 dict, same as stat_rows[3]["benchmark"]...},
    "avg_sp_earned":      {"benchmark_mean": None, ...},                              # all-None placeholder, UNCHANGED
}
```

The two structures are deliberately not unified — `stat_rows` is the new render driver, `benchmarks_by_stat` exists solely to keep one existing test (and any latent template branches) green.
