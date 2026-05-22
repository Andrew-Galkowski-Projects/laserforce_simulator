# Website Testing — Bugs & Issues

Date: 2026-05-22. Server: `runserver --noreload` (PID 4708, http://127.0.0.1:8000). Branch: `rv-03-round-report-pdf`. Scope: RV-03 round-report PDF export (round detail "Export PDF" button + `/matches/game-round/<id>/export/` endpoint) + regression smoke over Teams / Matches / Round detail / Events / Batch Sim / Create Match.

Severity legend: 🔴 High · 🟠 Medium · 🟡 Low · ℹ️ Note

## Summary

| ID | Sev | Area | One-liner |
|----|-----|------|-----------|
| R3-1 | ✅ | Round detail (RV-03) | "Export PDF" button renders on round detail (uid links to `/export/`); no console errors |
| R3-2 | ✅ | PDF export (RV-03) | Single round 109 → 200 `application/pdf`, `filename="round-109-phoenix-vs-vipers.pdf"`, 5582 bytes, `%PDF-` magic |
| R3-3 | ✅ | PDF export (RV-03) | Match round 75 → 200 `application/pdf` ("Round N of 2" label path) |
| R3-4 | ✅ | PDF export (RV-03) | Missing round 999999 → 404 |
| R3-5 | ✅ | PDF export (RV-03) | Responsive: Export PDF button + scoreboard render cleanly at 720×1115 and 1280×900 |
| R3-6 | ℹ️ | PDF export (RV-03) | Browser `POST` → **403** (CSRF middleware blocks the unsafe method before the view's 405 guard). Both reject non-GET; the view test asserts 405 via the CSRF-exempt test client. Expected/secure, not a bug. |
| BS-1 | 🟡 | Batch sim form | `[issue] No label associated with a form field (count: 4)` — **pre-existing** a11y warning, unrelated to RV-03 |

**Overall:** RV-03 works end-to-end in-browser. The export endpoint returns a valid PDF with the correct content-type and attachment filename for both single and match rounds; 404 on missing rounds. The Export PDF button renders correctly on the round detail page at both mobile and desktop widths. No console errors on any RV-03-touched page. Regression smoke (home, matches list, round detail, events charts, batch sim, create match) surfaced no new errors; the lone 🟡 is the pre-existing batch-form a11y warning.

---

## RV-03 round-report PDF export

### ✅ R3-1 — Export PDF button on round detail
`/matches/game-round/109/` renders an "Export PDF" link (→ `/matches/game-round/109/export/`) directly under the round subtitle. Page loads with zero console messages; all static/role-image requests 200.

### ✅ R3-2 / R3-3 / R3-4 — export endpoint
Via in-page `fetch`: round 109 (single) → `200`, `content-type: application/pdf`, `content-disposition: attachment; filename="round-109-phoenix-vs-vipers.pdf"`, 5582 bytes, body starts `%PDF-`. Round 75 (match round) → `200 application/pdf`. Round 999999 (missing) → `404`.

### ℹ️ R3-6 — POST returns 403, not 405
Browser `fetch('/export/', {method:'POST'})` → `403` (Django CSRF middleware rejects the unsafe method before the view's `HttpResponseNotAllowed(["GET"])` runs). The unit test asserts `405` because the Django test client is CSRF-exempt. Both responses reject non-GET; this is expected, secure behaviour — no code change.

### ✅ R3-5 — responsive
Round detail at 720×1115 (navbar collapses to hamburger, Export PDF button below title, tables horizontally scrollable) and 1280×900 (full navbar, full table) both render the Export PDF button and scoreboards cleanly.

---

# (Previous report) RES-04 movement heatmap

Date: 2026-05-21. Server: `python manage.py runserver` (PID 33364, http://127.0.0.1:8000). Branch: `res-04-heatmap`. Scope: RES-04 movement heatmap surfaces (per-round + map-editor multi-round) + smoke pass over Teams / Matches / Match detail / Round detail / Events / Missile log / Batch Sim / Maps / Team detail / Map editor.

Severity legend: 🔴 High · 🟠 Medium · 🟡 Low · ℹ️ Note

## Summary

| ID | Sev | Area | One-liner |
|----|-----|------|-----------|
| H-1 | ✅ | Round heatmap (RES-04) | Populated heatmap renders end-to-end on a freshly simulated map round |
| H-2 | ✅ | Round heatmap (RES-04) | Map-less round shows "No map" notice; filter row + canvas correctly hidden |
| H-3 | ✅ | Round heatmap (RES-04) | Pre-RES-04 round (null `cell_occupancy_json`) renders contract DOM with empty `{}` payload |
| H-4 | ✅ | Round heatmap (RES-04) | Filter dropdowns (player/role/team) repaint canvas live, no console errors |
| H-5 | ✅ | Map editor (RES-04) | New "Heatmap" mode toggles controls, fetches `/maps/<id>/heatmap-data/`, updates round count |
| H-6 | ✅ | Heatmap API (RES-04) | `/maps/<id>/heatmap-data/` returns 200 + reconciles: red_total + blue_total == both_total |
| H-7 | ✅ | Heatmap API (RES-04) | 400 paths: missing `zone_size` and `team_color=purple` both return 400 |
| H-8 | ✅ | Create match | `/matches/create/` with arena_map=San Marcos completes, persists rounds, populates `cell_occupancy_json` |
| ~~RD-1~~ | ~~🟡~~ | ~~Round detail~~ | ~~Round detail page lacks a Missile log link (heatmap link added by RES-04, missile-log link still missing) — **pre-existing, not RES-04 regression**~~ _(fixed)_ |
| ~~BS-1~~ | ~~🟡~~ | ~~Batch sim form~~ | ~~`[issue] No label associated with a form field (count: 4)` — pre-existing a11y warning~~ _(fixed)_ |
| ~~BS-2~~ | ~~🟠~~ | ~~Batch sim (SIM-11 run)~~ | ~~Re-running a batch without reloading throws Chart.js "Canvas is already in use" — breaks polling, shows stale partial results~~ _(fixed)_ |
| ~~MP-1~~ | ~~🟡~~ | ~~Maps list~~ | ~~`[issue] An element doesn't have an autocomplete attribute` on upload form — pre-existing a11y warning~~ _(fixed)_ |

**Overall:** All RES-04 surfaces work end-to-end on a freshly simulated map-aware match. Filter cascade math reconciles (red+blue sums equal both). Pre-RES-04 rounds gracefully render empty heatmaps with no errors. Map-less rounds render the correct "No map" notice. No console errors on any RES-04 page. Smoke pass on the rest of the app surfaces no new regressions; the three 🟡 items are all pre-existing.

---

## Round heatmap `/matches/game-round/<id>/heatmap/`

### ✅ H-1 — populated heatmap renders
Created match 31 (Phoenix vs Vipers on San Marcos Laser Tag, zone_size=20) → rounds 82 + 83. `/matches/game-round/82/heatmap/` returns 200, processed map image loads, canvas dimensions match background (1780×1104), `cell-occupancy-data` carries 12 players × 1187 cells = 17,161 total ticks. Yellow→orange→red gradient renders cleanly over the map.

### ✅ H-2 — map-less round notice
`/matches/game-round/79/heatmap/` (round with `arena_map=None`) → renders `#heatmap-no-map-notice` with text `"No map — heatmap unavailable."`; `#heatmap-canvas` and `#heatmap-filter-row` are absent (template gates correctly on `has_map`).

### ✅ H-3 — pre-RES-04 round graceful empty
`/matches/game-round/81/heatmap/` (pre-RES-04 round on map 4, `cell_occupancy_json=null`) → all contract DOM IDs present (`heatmap-canvas`, `heatmap-bg`, `heatmap-stage`, `heatmap-filter-player/role/team`, `cell-occupancy-data`, `player-roster-data`), JSON script holds `{}`, `window.LF_ZONE_SIZE=20`, player roster (12) renders for the filter dropdowns. Canvas paints nothing — exactly what the no-backfill contract specifies (ADR-0004).

### ✅ H-4 — filter reactivity
Programmatic `change` events on `#heatmap-filter-team` and `#heatmap-filter-role` repaint the canvas live. Pixel-count smoke: both teams ≈ 176k painted pixels, red-only ≈ 124k, scout-only ≈ 50k. No console errors (only a benign Canvas2D `getImageData willReadFrequently` warning emitted by the test script itself, not production).

---

## Map editor heatmap mode `/maps/<id>/editor/`

### ✅ H-5 — Heatmap mode toggle
Clicking the new `mode-heatmap` button shows `#heatmap-controls`, fetches `/maps/4/heatmap-data/?zone_size=20`, and updates `#heatmap-editor-round-count` to `"rounds aggregated: 2"` (matches the two freshly simulated rounds; pre-RES-04 rounds with null JSON are excluded by the `cell_occupancy_json__isnull=False` filter). Existing `mode-zones` and `mode-sight` controls still present and untouched.

---

## Heatmap data endpoint `/maps/<id>/heatmap-data/`

### ✅ H-6 — aggregation math reconciles
Tested against map 4, zone_size=20, after rounds 82 + 83 were simulated:
- `both` (no team filter): 627 cells, total 32,960 ticks across cells.
- `team_color=red`: 486 cells, 13,265 total ticks.
- `team_color=blue`: 506 cells, 19,695 total ticks.
- 13,265 + 19,695 = 32,960 ✅ (red + blue partition sums to both).

### ✅ H-7 — error paths
- `?team_color=purple` → **400** ✅
- missing `zone_size` → **400** ✅
- bogus `map_id` → **404** (covered by view tests, not retested manually).

---

## Create match flow

### ✅ H-8 — end-to-end create-with-map
`/matches/create/` → select Phoenix (red), Vipers (blue), Friendly Match, **arena_map=San Marcos Laser Tag** → click Simulate Match. Page transitions to `/matches/31/`, "Match simulated! Vipers won!" alert, final score 58748–68492, two View Round links (rounds 82 + 83). Both rounds persisted with non-null `cell_occupancy_json` (verified via the multi-round endpoint round_count=2).

---

## Pre-existing issues (not RES-04 regressions)

### ~~🟡 RD-1 — round detail lacks missile-log link~~ _(fixed)_
`templates/matches/game_round_detail.html:276-277` lists "📋 View Event Log" and (post-RES-04) "🗺️ Movement Heatmap" but does **not** link to `missile_log`. The heatmap template itself includes the missile-log link in its top nav row, but round detail does not. **Pre-existing — the missile-log link was never wired into round_detail when RES-03 shipped.** Out of scope for this PR; flagging for a follow-up.

```html
276:    <a href="{% url 'game_round_events' round.id %}" class="btn btn-info">📋 View Event Log</a>
277:    <a href="{% url 'movement_heatmap' round_id=round.id %}" class="btn btn-info">🗺️ Movement Heatmap</a>
```

### ~~🟡 BS-1 — batch sim form a11y~~ _(fixed)_
`/matches/simulate-batch/` emits `[issue] No label associated with a form field (count: 4)`. Pre-existing; not touched by RES-04. Cosmetic.

### ~~🟡 MP-1 — maps list autocomplete~~ _(fixed)_
`/maps/` emits `[issue] An element doesn't have an autocomplete attribute`. Upload form input. Pre-existing; not touched by RES-04. Cosmetic.

### ~~🟠 BS-2 — batch sim re-run reuses Chart.js canvas without destroying it~~ _(fixed)_
Found 2026-05-21 while testing SIM-11 (multi-process batch path) on branch `sim-11-workers-ui-batch`. **Server-side SIM-11 change is not implicated — this is a pre-existing client-side bug in the batch template's polling/render JS.**

Repro: `/matches/simulate-batch/` → run any batch (e.g. n=50) and let it finish → **without reloading the page**, change "Number of simulations" and click Run again. The second run throws:

```
Polling request failed: Error: Canvas is already in use. Chart with ID '0' must be destroyed before the canvas with ID 'scoreChart' can be reused.
```

Effect: polling aborts, the score-distribution chart fails to re-render, and the results panel shows **stale/partial data** (observed "Results — 1 simulations" with the previous run's "completed in 11.13s" still shown while the new n=10 run was in flight). Reloading the page first and running once works fine (verified n=50 and n=10 both render cleanly on a fresh load with no console errors).

Cause: the Chart.js instance bound to `#scoreChart` is created on each run but the prior instance is never `.destroy()`-ed (nor is `Chart.getChart(canvas)` checked) before re-instantiating. Fix: destroy/replace the existing chart instance (and reset the results DOM) at the start of each new run before the first poll repaints the canvas.

Likely lives in the batch template's inline polling script — `templates/matches/batch_simulate.html` (or whatever JS owns `scoreChart`); not yet pinpointed to a line.

---

## Coverage

Pages exercised (all 200, no console errors unless flagged above):
- `/` (homepage / teams list)
- `/16/` (team detail — Phoenix)
- `/matches/` (match list)
- `/matches/create/` (create + simulate, full flow)
- `/matches/31/` (match detail, freshly created)
- `/matches/game-round/82/` (round detail, freshly populated)
- `/matches/game-round/82/events/` (event log)
- `/matches/game-round/82/missile-log/` (RES-03)
- `/matches/game-round/82/heatmap/` (**RES-04 populated**)
- `/matches/game-round/81/heatmap/` (RES-04 pre-RES-04 round, empty)
- `/matches/game-round/79/heatmap/` (RES-04 map-less notice)
- `/matches/simulate-batch/` (batch sim form)
- `/maps/` (maps list)
- `/maps/4/editor/` (**RES-04 Heatmap mode**)
- `/maps/4/heatmap-data/?zone_size=20` + `&team_color=red|blue|purple` and missing-zone variants

Test data created during this run: match **31** (rounds **82** + **83**) on map 4 (San Marcos). Used seeded teams Phoenix (16) and Vipers (17) — no new teams created.
