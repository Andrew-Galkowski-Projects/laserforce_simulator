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
