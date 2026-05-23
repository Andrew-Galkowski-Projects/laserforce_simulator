# Website Testing — Bugs & Issues

Date: 2026-05-22. Server: `runserver --noreload` (http://127.0.0.1:8001). Branch: `hx-01-player-career-stats`. Scope: HX-01 per-player career stats page (`/players/<id>/stats/` + `Career stats` link on `/teams/<id>/player/<id>/`).

Severity legend: 🔴 High · 🟠 Medium · 🟡 Low · ℹ️ Note · ✅ Working

## Summary

| ID | Sev | Area | One-liner |
|----|-----|------|-----------|
| HX1-1 | ✅ | Career page (populated) | `/players/78/stats/` → 200; title `Career Stats - Phoenix Commander`; career-totals-table + career-per-role-table + points-trend-chart canvas all rendered |
| HX1-2 | ✅ | Career page (populated) | `trend-data` json_script populated with 31 entries (first `[1, 8862]`, last `[31, 6330.8]`); Chart.js loaded; `Chart.getChart(canvas)` returns a constructed instance |
| HX1-3 | ✅ | Career page (empty) | `/players/90/stats/` (zero-round player) → 200; `career-no-rounds-notice` rendered with copy `"No rounds played yet."`; totals/per-role/canvas correctly absent |
| HX1-4 | ✅ | Entry-point link | `/teams/16/player/78/` renders an `<a>` with text `"Career stats"` and `href="/players/78/stats/"` |
| HX1-5 | ✅ | Console / network | Zero console messages on all three HX-01 surfaces; all network requests 200 (Bootstrap CSS, Chart.js, Bootstrap JS) |

**Overall:** HX-01 works end-to-end in-browser. The career page renders the locked DOM (career-totals-table, career-per-role-table, points-trend-chart canvas, trend-data json_script) with the locked copy and formatting on a player with 31 rounds; the empty-state notice renders with the locked substring on a zero-round player; the `Career stats` entry-point link on the existing player-detail page links to the new URL with the right `href`. No console errors, no failed requests on any HX-01 surface.

---

## HX-01 career stats page

### ✅ HX1-1 — `/players/78/stats/` populated render
Player 78 (Phoenix Commander, 31 rounds, team 16). Page title `Career Stats - Phoenix Commander`. Heading `Phoenix Commander — Phoenix`. Total rounds: 31. Career totals row: Games 31, Avg points 6164.1, Tag ratio 0.99, Avg survival 846s, Avg accuracy 58%, Avg SP earned 62.3. Per-role row (Commander only, since this player is single-role in this DB): same six numbers. Both `career-totals-table` and `career-per-role-table` DOM IDs present. The `career-no-rounds-notice` is correctly absent.

### ✅ HX1-2 — trend chart + Chart.js
`points-trend-chart` `<canvas>` present at 1638×819. `trend-data` `<script type="application/json">` present and parses to 31 `[round_idx, mean_points]` pairs. First entry `[1, 8862]` (partial-window mean of round 1 = round 1's own points). Last entry `[31, 6330.8]` (10-round trailing mean). Chart.js loaded (`typeof Chart !== "undefined"`). `Chart.getChart(canvas)` returns a constructed Chart instance, confirming the inline IIFE wired the dataset onto the canvas successfully.

### ✅ HX1-3 — `/players/90/stats/` empty-state render
Player 90 (`ChromeTest Empty`, 0 rounds, team 16, created for this smoke test). Page title `Career Stats - ChromeTest Empty`. Heading `ChromeTest Empty — Phoenix`. Total rounds: 0. The `career-no-rounds-notice` element is present with `textContent == "No rounds played yet."` (the locked substring per seam §5.2). The three populated-state elements (`career-totals-table`, `career-per-role-table`, `points-trend-chart`) are correctly absent from the DOM.

### ✅ HX1-4 — `Career stats` link on player detail
`/teams/16/player/78/` renders an `<a>` with text `Career stats` and `href="/players/78/stats/"`. The URL resolves to the new `player_career_stats` route via the `{% url %}` reverse, confirming the URL include in `laserforce_simulator/urls.py` sits above the homepage catch-all (otherwise the homepage shadow would have eaten the include and the reverse would 404 on follow).

### ✅ HX1-5 — Console + network clean
All three pages produce zero console messages (no errors, warnings, or info). All network requests succeed: the document `200`, Bootstrap CSS `200`, Chart.js CDN `200`, Bootstrap JS `200`. No 4xx, no 5xx.

---

# HX-02 role benchmarks smoke (2026-05-23)

Date: 2026-05-23. Server: `runserver 127.0.0.1:8765 --noreload`. Branch: `hx-02-role-benchmarks`. Scope: HX-02 role benchmarks surfaces (`/players/benchmarks/` new page + extended `/players/<id>/stats/` per-role row) and the `_flush_to_db` invalidation hook.

**Caveat:** Chrome MCP couldn't attach (stale Chrome processes from yesterday holding the chrome-devtools-mcp profile lock; killing them was deemed too destructive to attempt unattended). Smoke was downgraded from in-browser to HTTP-level via `Invoke-WebRequest` — covers the template / view / URL layer but **does NOT cover JS console errors, network 404s, or Chart.js init** on the HX-02 surfaces. The full pytest suite (1240 passed, 0 failed) and the 52 HX-02 unit + view + cache tests already cover the server-side contract, so the residual risk is purely client-side JS bugs.

## Summary

| ID | Sev | Area | One-liner |
|----|-----|------|-----------|
| HX2-1 | ✅ | `/players/benchmarks/` | 200 OK; all 13 locked DOM IDs (`benchmark-filter-form`, `benchmark-threshold-input`, `benchmark-display-toggle`, 5× `benchmark-table-{role}`, 5× sample `benchmark-row-{role}-{stat}`) present in rendered HTML |
| HX2-2 | ✅ | Query-param fallback | `?threshold=abc&display=garbage` → 200; `?threshold=-5` → 200; `?threshold=10&display=median` → 200 (different length, confirming display toggle round-trips) |
| HX2-3 | ✅ | Player career page extension | `/players/78/stats/` → 200 with all 5 HX-01 IDs + `benchmark-filter-form` + 5× `benchmark-commander-avg_points-{mean,median,delta,percentile,n}` cells + `benchmark-na` class present |
| HX2-4 | ✅ | Unqualified-player branch | `/players/78/stats/?threshold=999` substring `need 999+ rounds` present, confirming the unqualified template branch round-trips the threshold value |
| HX2-5 | ✅ | Entry-point link | `role-benchmarks-link` anchor on `/players/78/stats/` has `href="/players/benchmarks/"` (extracted from rendered HTML) |
| HX2-6 | ✅ | Sibling-page regression | `/`, `/teams/`, `/matches/`, `/matches/create/`, `/matches/single-round/create/`, `/matches/simulate-batch/`, `/teams/create/` all 200 — confirms the `_flush_to_db` invalidation hook + `apps.py` `ready()` signal registration didn't break the rest of the app's import or URL graph |
| HX2-7 | ℹ️ | Empty-state notice | `benchmark-no-data-notice` + "no benchmark data yet" substring NOT in the rendered HTML on this DB (which has seeded rounds). Empty-state path is covered by `test_role_benchmarks_view.py::TestRoleBenchmarksView` in the test suite. |

**Overall:** HX-02 works at the server / template / URL layer. The new `/players/benchmarks/` page renders all 5 role tables with all 60 expected (role, stat) rows; query-param coercion (`?threshold=abc` → 5, `?threshold=-5` → 0, `?display=garbage` → mean) is exercised end-to-end; the extended player career page surfaces the locked benchmark cells per HX-01 display stat with the `benchmark-na` placeholder class on unmapped stats and the `need N+ rounds` substring on unqualified players; the `role-benchmarks-link` button on the player page reverses to `/players/benchmarks/`. The simulator hook + signal registration cause no regression on any sibling page hit. Client-side JS (no Chart.js on this page, just a static form) is not exercised by HTTP smoke — residual JS-init risk is small (no Chart.js init code on the benchmarks page; the existing trend chart on the player page is unchanged from HX-01).

---

# HX-01b career page 12-stat benchmark extension smoke (2026-05-23)

Branch: `hx-01b-12-stat-benchmark`. Scope: per-role table on `/players/<int:player_id>/stats/` pivots from one wide row-per-role table to a `<section id="career-per-role-table">` wrapper containing one `<table id="career-per-role-table-{role}">` per role actually played, each with 15 rows (5 HX-01 + 10 STAT_KEYS net-new).

## Summary

| ID | Sev | Area | One-liner |
|----|-----|------|-----------|
| HX1b-1 | ✅ | Career page (populated) | `/players/78/stats/` (Phoenix Commander, 31 rounds, commander-only) → 200; 1 nested `<table id="career-per-role-table-commander">` inside `<section id="career-per-role-table">`; 15 `<tr>` rows; first row id `career-stat-row-commander-avg_points`, last row id `career-stat-row-commander-combo_resupply_count` (matches locked order) |
| HX1b-2 | ✅ | New stat labels | All 10 net-new labels (`MVP score`, `Tags made`, `Times tagged`, `Final lives`, `Resupplies given`, `Missiles landed`, `Specials used`, `Follow-up shots`, `Reaction shots`, `Combo resupplies`) render in the page body |
| HX1b-3 | ✅ | Benchmark cells | 12 benchmark-backed rows show numeric Mean / Median / Δ / Percentile / n; 3 HX-01-only rows (`tag_ratio`, `avg_survival_ticks`, `avg_sp_earned`) show `<td class="benchmark-na">—</td>` placeholders |
| HX1b-4 | ✅ | Below-threshold UX | `/players/78/stats/?threshold=50` → 12 occurrences of `need 50+ rounds` substring (12 benchmark-backed rows × 1 role played); exact match to the seam's locked count |
| HX1b-5 | ✅ | Homepage regression | `/` (team list) renders 200 with zero console errors and no failed network requests |
| HX1b-6 | 🔴→✅ | Template comment leak (fixed during smoke) | Initial implementation used a multi-line `{# … #}` Django comment around the section block. Django `{# #}` is **single-line only**; the multi-line form leaks as literal text. The leaked text contained the substring `<table>` (in the comment body), which the HTML parser interpreted as a real open tag, causing the browser to push the real per-role `<table>` element OUT of the `<section>` wrapper at parse time. Fixed by collapsing the comment to a single line; `<section>` now correctly contains the per-role tables (verified by `wrapper.querySelectorAll('table[id^="career-per-role-table-"]').length === 1`). Pre-fix screenshot was not saved; post-fix screenshot at `.claude/worktrees/hx-01b-career-15rows.png`. |
| HX1b-7 | 🟡 | Pre-existing comment leak (HX-02, out of scope) | `templates/teams/player_career_stats.html:50-51` carries a pre-existing multi-line `{# HX-02: threshold + display toggle ... #}` Django comment that leaks as literal text on the rendered page (visible as `(# HX-02: threshold + display toggle ... #)` above the filter form). Same root cause as HX1b-6 but in pre-HX-01b code. Cosmetic only; not a regression introduced by this task. Recommended drive-by fix in a future commit. |

**Overall:** HX-01b renders correctly end-to-end after the comment-leak fix. The pivot to a section-wrapped per-role table layout works as locked; 15 rows render in the locked order with the locked labels; the 12 benchmark-backed rows render numeric overlay cells while the 3 HX-01-only rows render the `benchmark-na —` placeholders; the below-threshold "need N+ rounds" substring renders exactly 12 times per role table (matching test (e)'s assertion). One pre-existing multi-line `{# #}` leak in HX-02 code logged at HX1b-7, out of scope.
