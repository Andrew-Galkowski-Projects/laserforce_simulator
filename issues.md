# Website Testing — Bugs & Issues

Date: 2026-05-23. Server: `runserver --noreload` (http://127.0.0.1:8000). Branch: `hx-03-head-to-head`. Scope: HX-03 head-to-head record surface (`/matches/h2h/` + entry-point links on `/matches/` and `/matches/team/<id>/history/`).

Severity legend: 🔴 High · 🟠 Medium · 🟡 Low · ℹ️ Note · ✅ Working

## Summary

| ID | Sev | Area | One-liner |
|----|-----|------|-----------|
| HX3-1 | ✅ | H2H picker | `/matches/h2h/` → 200; picker form rendered with all locked DOM ids (`h2h-picker-form`, `h2h-select-a`, `h2h-select-b`, `h2h-provenance`, `h2h-from`, `h2h-to`, `h2h-submit`); 2 teams in dropdowns; console + network clean |
| HX3-2 | ✅ | H2H picker preselect | `/matches/h2h/?team_a=16` → 200; mode=picker; `#h2h-select-a` value=`"16"` (Phoenix preselected) — confirms the view fix from triage works in-browser |
| HX3-3 | ✅ | H2H results full | `/matches/h2h/?team_a=16&team_b=17` → 200; all locked DOM ids present (`h2h-match-record` 3-1-0, `h2h-round-record` 10-19-2, `h2h-score-margin` -2521.0, `h2h-team-a-survivors` 2.13, `h2h-team-b-survivors` 2.48, `h2h-top-impactful-a/b`, `h2h-per-map-table`, `h2h-detail-list`); both Chart.js canvases (`h2h-margin-chart`, `h2h-cumulative-wl-chart`) painted (769×480); `h2h-margin-series` json_script parses to 31 valid `[idx, margin]` pairs; `h2h-cumulative-wl-series` parses to 31 valid `[idx, cum]` pairs |
| HX3-4 | ✅ | H2H error mode | `/matches/h2h/?team_a=16&team_b=16` → 200; `#h2h-error-banner` reads `"Pick two different teams to compare."`; results blocks absent |
| HX3-5 | ✅ | Entry point — matches list | `/matches/` renders `<a href="/matches/h2h/">View Head-to-Head</a>` |
| HX3-6 | ✅ | Entry point — team history | Resolved during code-review: `team_match_history` view now builds a deduped `unique_opponents` list; template renders one anchor per opponent in a top "Head-to-Head" card (DOM id `h2h-opponents-bar`); per-row duplicates removed |
| HX3-7 | ✅ | Console / network | Zero console messages and zero non-2xx network requests across all 5 HX-03 surfaces walked |

**Overall:** HX-03 works end-to-end in-browser. Picker, picker-preselection, full-results, error mode, and both entry points all render with the locked DOM and copy. Charts paint with valid JSON data. One minor finding (HX3-6): the team-history per-opponent link renders one anchor per match row instead of one per unique opponent — small contract drift, cosmetic only, will be addressed in code-review.

---

## HX-03 head-to-head record

### ✅ HX3-1 — `/matches/h2h/` picker render
Picker form rendered with the locked DOM ids: `h2h-picker-form` (form), `h2h-select-a` + `h2h-select-b` (each with 2 teams + "-- Select a team --" placeholder), `h2h-provenance` (3 options: All / Real only / Simulated only), `h2h-from`, `h2h-to`, `h2h-submit`. Form `action="/matches/h2h/"`, `method="get"`. Console clean (0 messages). Network: 1 request (the document itself, 200). No favicon noise.

### ✅ HX3-2 — `/matches/h2h/?team_a=16` picker preselection
Page mode is `picker` (no results DOM rendered). `#h2h-select-a` resolved value is `"16"` (Phoenix preselected). `#h2h-select-b` value is `""` (still on the placeholder). Confirms the view fix from triage (resolve Team in picker mode when its id parses cleanly) works in-browser.

### ✅ HX3-3 — `/matches/h2h/?team_a=16&team_b=17` full results
All locked DOM ids present and populated:
- `h2h-match-record`: `"3-1-0"` (W-L-T from Match.winner)
- `h2h-round-record`: `"10-19-2"` (per-Round W-L-T across the unified basket)
- `h2h-score-margin`: `"-2521.0"` (signed per-Round mean from team_a perspective — team_b dominates)
- `h2h-team-a-survivors`: `"2.13"`, `h2h-team-b-survivors`: `"2.48"`
- `h2h-top-impactful-a`: `"Phoenix Commander\n                            MVP 401.8 over 31 rounds"`
- `h2h-top-impactful-b`: `"Vipers Commander\n                            MVP 436.4 over 31 rounds"`
- `h2h-per-map-table` (`<table>`), `h2h-detail-list` (`<table>`)

Both Chart.js canvases present and sized 769×480: `h2h-margin-chart`, `h2h-cumulative-wl-chart`. The two `json_script` blocks parse cleanly: `h2h-margin-series` returns 31 `[round_idx, margin]` pairs (first `[1, 6020]`, last `[31, -9140]`); `h2h-cumulative-wl-series` returns 31 `[round_idx, cum_diff]` pairs (first `[1, 1]`, last `[31, -9]`). Console clean (0 messages).

### ✅ HX3-4 — `/matches/h2h/?team_a=16&team_b=16` error mode
`#h2h-error-banner` rendered with `"Pick two different teams to compare."`. `h2h-match-record` absent (results not rendered). Picker re-renders above the banner.

### ✅ HX3-5 — Entry-point link on `/matches/`
`/matches/` renders exactly one `<a>` with text `"View Head-to-Head"` and `href="/matches/h2h/"`. Click would land on the picker.

### 🟡 HX3-6 — Entry-point links on `/matches/team/16/history/` are duplicated per match row
`/matches/team/16/history/` renders **27** copies of `<a href="/matches/h2h/?team_a=16&team_b=17">vs. Vipers — H2H</a>` — one per match row in the history list. Seam contract `.claude/worktrees/hx-03-seam-contract.md` §Entry points specifies *"for each unique opponent the team has faced, add a 'vs. {opponent} — H2H' link"* (one per **unique** opponent, not per row). The current rendering is noisy but functionally correct (each link navigates to the right team-pair URL). Cosmetic; fix during the HX-03 code-review pass before commit. Template at `laserforce_simulator/templates/matches/team_history.html`.

### ✅ HX3-7 — Console / network
Zero console messages across all 5 HX-03 surfaces walked (`/matches/h2h/`, `?team_a=16`, `?team_a=16&team_b=17`, `?team_a=16&team_b=16`, `/matches/`, `/matches/team/16/history/`). All document loads returned 200; no XHR/fetch failures.

---

## Historic (older runs)

### Original HX-01 entry (preserved for context)

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

---

# HX-04 — Player head-to-head record (2026-05-24)

Date: 2026-05-24. Server: `runserver --noreload` (http://127.0.0.1:8000). Branch: `hx-04-player-head-to-head`. Scope: HX-04 player head-to-head surface (`/matches/h2h/player/`) + career-page entry-point anchor on `/players/<id>/stats/`.

## Summary

| ID | Sev | Area | One-liner |
|----|-----|------|-----------|
| HX4-1 | ✅ | Picker mode | `GET /matches/h2h/player/` → 200; all 8 locked picker DOM ids present (`player-h2h-picker-form`, `-select-a`, `-select-b`, `-role`, `-provenance`, `-from`, `-to`, `-submit`); 12 players in both dropdowns; 5-role dropdown rendered; console + network clean |
| HX4-2 | ✅ | Error mode | `?player_a=78&player_b=78` → 200; `#player-h2h-error-banner` reads `"Pick two different players to compare."`; picker re-rendered above banner; no results blocks present |
| HX4-3 | ✅ | Results mode (populated) | `?player_a=78&player_b=84` → 200; 31 H2H rounds rendered; all locked DOM ids present: `player-h2h-round-record` = "10-19-2", `-score-margin` = "-2521.0", `-tags-a-to-b` = "10.58 / 328", `-tags-b-to-a` = "12.00 / 372"; sections `-per-role-table` (1 row, Commander 31 games), `-per-map-table` (2 rows — San Marcos Laser Tag 24g + No map (3-zone) 7g — confirms `arena_map_id=None` bucket labelled correctly), `-detail-list` (31 reverse-chronological rows with `View` links to `/matches/game-round/<id>/`); both Chart.js canvases (`-margin-chart`, `-cumulative-wl-chart`) rendered; json_script `-margin-series` parses to 31 `[idx, margin]` pairs; `-cumulative-wl-series` parses to 31 `[idx, cum]` pairs ending at `-9` (matches 10-19 W/L diff); console + network clean |
| HX4-4 | ✅ | Empty-history sub-mode | Not separately retested in-browser (every player pair in the seeded DB produced ≥1 H2H round). Pinned by `views_tests.py::test_empty_history_results_mode_with_no_games_notice_200`. |
| HX4-5 | ✅ | Career-page anchor | `/players/78/stats/` renders `<a id="player-h2h-link" href="/matches/h2h/player/?player_a=78">View head-to-head…</a>` |
| HX4-6 | ✅ | Picker → results submit flow | Click "Compare" with player_a=Phoenix Commander pre-selected from career-page anchor + player_b=Vipers Commander chosen → navigates to `?player_a=78&player_b=84&role=&provenance=all&from=&to=` → results mode renders identically to direct URL hit (HX4-3) |
| HX4-7 | ✅ | Console / network | Zero `error`/`warn` console messages across all 5 navigated pages (picker, error, results, career, picker→results submit). All network requests 200 (Bootstrap CSS, Chart.js, Bootstrap JS, SVG data-URLs). No favicon 404. |
| HX4-8 | ✅ | Chart.js init | Both `-margin-chart` and `-cumulative-wl-chart` canvases render Chart.js stepped-line plots without throwing; no `Cannot read property` or `is not a function` errors in console. |

**Overall:** HX-04 renders correctly end-to-end on every contract surface. All 22 locked DOM ids present and populated. The opposite-teams gate, per-Round attribution (`team_color`-based), per-role 'both'-semantic filter, per-Map breakdown with `None` → "No map (3-zone)" bucket, reverse-chronological detail list, and the two Chart.js charts all work as locked. The career-page entry-point anchor is the single discovery surface, as scoped. No bugs logged; no pre-existing-bug surface touched.
