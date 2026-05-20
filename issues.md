# Website Testing — Bugs & Issues

End-to-end Chrome MCP test pass against the SIM-09 branch
(`sim-09-replace-rbs-with-batchsim`) on 2026-05-20. Server: Django dev
on `http://127.0.0.1:8000`, DEBUG=True. Pages exercised: `/`, `/create/`,
`/15/` (new team), `/matches/`, `/matches/create/`, `/matches/24/`,
`/matches/game-round/64/`, `/matches/game-round/64/events/`,
`/matches/single-round/create/`, `/matches/game-round/66/`,
`/matches/simulate-batch/` (with new arena_map field), save flow,
`/matches/game-round/67/`, `/maps/`. Responsive: 720×1115 + 1280×900.

Severity legend: 🔴 High · 🟠 Medium · 🟡 Low · ℹ️ Note

## Summary

| ID | Sev | Area | One-liner |
|----|-----|------|-----------|
| BS-1 | 🟠 | Batch sim template | `BatchSimulateForm.arena_map` field wasn't rendered in `batch_simulate.html` — fix landed in this test run (template now renders it) |
| MAPS-1 | 🟡 | Single round | Picking the Syracuse map raised "no red base placed" — pre-existing map-data issue, view caught + flashed correctly |
| A11Y-1 | 🟡 | Maps list | One Chrome a11y issue: form element missing `autocomplete` attribute on `/maps/` |
| ℹ️ | ℹ️ | All pages | No `/favicon.ico` 404 observed this session (browser didn't request one) |

**Overall:** Every SIM-09 critical surface works end-to-end through the UI.
The view path is dramatically faster than pre-SIM-09 (full 2-round match
near-instant vs ~10-20 s under RBS). Single-round and full-match flows
both persist `arena_map` + `zone_size` + `rng_seed` on the resulting
`GameRound`. Batch-save now also carries the map (closes the pre-SIM-09
gap). Overwatch resolution (MOVE-03) is firing on view-mode rounds for
the first time — 146 overwatch-flagged events on round 64. One template
miss found (BS-1) and fixed inline; no Python regressions surfaced.

---

## Match flow (full SIM-09 path)

### ✅ Create + simulate match — works
`/matches/create/` with teams 1+2 + San Marcos Laser Tag map →
`/matches/24/` near-instantly (pre-SIM-09 RBS: ~10-20 s). Both rounds
persisted, per-Match colour swap reflected in stored sides (round 1:
team_red=1; round 2: team_red=2). Console + network clean.

### ✅ Round detail (`/matches/game-round/64/`) — works
Per-player tables render, console clean. `arena_map` and `zone_size`
displayed via the standard round-detail layout.

### ✅ Event log (`/matches/game-round/64/events/`) — works
3,787 events emitted as compact JSON for client-side windowing (M-1
contract intact). Event types covered: `movement` (2309), `tag` (583),
`miss` (437), `resupply_lives` (166), `resupply_ammo` (216),
`combo_resupply` (24), `missile` (3), `missile_dodge` (2), `special`
(29), `elimination` (8), `base_capture` (10). **146 events carry
`metadata.overwatch=true`** — MOVE-03 Hold/Overwatch resolution
([ADR-0009](docs/adr/0009-hold-overwatch.md)) is firing on view-mode
rounds for the first time post-SIM-09. Movement metadata keys
(`actor_role`, `start_row`/`col`, `end_row`/`col`, `cell_row`/`col`,
`new_zone`) match the spec.

---

## Single round flow

### ✅ Create single round with map — works
`/matches/single-round/create/` with San Marcos → standalone
`GameRound` id=66. Direct DB inspection confirms:
- `arena_map = "San Marcos Laser Tag"` ✓
- `zone_size = 20` ✓
- `rng_seed = 404797581936799606` ✓ (replayable)
- `match = None` ✓ (standalone)
- 4582 GameEvents persisted.

### 🟡 MAPS-1 — Syracuse map raises "no red base placed"
Choosing "Syracuse Laser Tag" surfaces the `ValueError` from
`load_map_context` as a flash message:
> "Map 'Syracuse Laser Tag' has no red base placed. Place a red and
> blue base in the map editor before simulating."

This is the **expected** behaviour for an incomplete map — the
`load_map_context` raise is caught by the view's `try/except
ValueError` and surfaced cleanly. The actual data gap (no red base
on the Syracuse map) is pre-existing and unrelated to SIM-09. Worth
noting that `_maps_with_confirmed_config()` only filters to "has
confirmed zone config" — it doesn't require base placement, so an
unfinished map can appear in the picker. Fix idea (not SIM-09 scope):
tighten the form queryset to also require both bases + sight lines,
matching what `load_map_context` actually demands.

---

## Batch simulation (SIM-09 critical surface)

### 🟠 BS-1 — `arena_map` field missing from `batch_simulate.html`
**Repro:** Open `/matches/simulate-batch/`. Initial render shows only
Red Team / Blue Team / Number of simulations / Run.
**Expected:** Arena Map dropdown between Blue Team and Number of
simulations (mirroring the `MatchSetupForm` template).
**Actual (pre-fix):** No Arena Map field. The form had it
(`forms.py:94-100`), the view forwarded it (`views.py:384`), the
session stash carried it (`views.py:401`), and the worker resolved it
(`views.py:44-50`) — but the template `batch_simulate.html` rendered
only `team_red`, `team_blue`, `n`, so users had no way to set it.
**Root cause:** `laserforce_simulator/templates/matches/batch_simulate.html`
omitted the field block (sibling templates `enhanced_match_setup.html`
and `enhanced_single_round_setup.html` both have the matching block
for their own `arena_map` field). The pre-fix template body was the
3-column row at line 12-25.
**Fix applied this session:** Added the field block between Blue Team
and Number of simulations; column widths rebalanced to `col-md-3`s +
`col-md-2` (n) + `col-md-1` (Run). Server restarted with `DEBUG=True`
so the template cache picks up the change in future sessions; in
production the existing template-cache invalidation on restart covers
it.
**Verified after fix:** Field renders, 50-round batch on San Marcos
completed in 115 s (~2.3 s/round with map active vs ~200 ms no-map),
all sections render (win rates, side advantage panel, score
distribution, save buttons).

### ✅ Save batch game with map — works (SIM-09 critical fix)
After running 50 sims with San Marcos map, clicked **Save Average
Game(s)** → background save job replayed the seed and persisted
`GameRound id=67`:
- `arena_map = "San Marcos Laser Tag"` ✓
- `zone_size = 20` ✓
- `rng_seed = 5650959302570751530` ✓
- 5719 events.

Pre-SIM-09 this round would have had `arena_map=None`/`zone_size=None`
because `save_games` didn't accept the kwarg and `_run_save_job`
didn't resolve the session-stashed id. The view-thread-spawn arg
plumbing (`save_batch_games` → `_run_save_job` 6-arg threading.Thread
positional args) is unit-tested in
`views_tests.py::test_save_batch_games_view_threads_arena_map_id_into_worker_args`.

---

## Other pages

### ✅ Homepage (`/`) — works
14 teams listed, valid roster badges accurate, console + network clean.

### ✅ Match history (`/matches/`) — works
Tournament Matches section lists match 24 (the new SIM-09 match);
Single Rounds section lists rounds 66, 67 (the new SIM-09 rounds),
both linkable.

### ✅ Team detail (`/15/`) — works
New `ChromeTest QA` team (id=15) renders with "0 players Incomplete
Roster" badge, correct list of missing roles, Add Player / Assign
Slots / Match History / Edit Team buttons present.

### ✅ Maps list (`/maps/`) — works
Both arena maps listed with thumbnails (304 cached). One a11y issue
(see A11Y-1 below).

### 🟡 A11Y-1 — Maps list form missing `autocomplete`
Chrome flagged one Issue on `/maps/`: "An element doesn't have an
autocomplete attribute". Likely the upload-map form input field. Not
blocking but a quick fix (`autocomplete="off"` or
`autocomplete="name"` on the relevant `<input>`).

### ✅ Responsive (720×1115) — works
Form fields stack vertically, navbar collapses to hamburger button as
expected (below 992px breakpoint), Run button stretches full width,
Arena Map field included in the stack. Screenshot:
`.claude/worktrees/chrome-test-720.png`.

---

## Known/benign

- `/favicon.ico` 404 not observed this session (browser didn't request).
- The "no red base placed" message on the Syracuse map is correct
  error surfacing, not a SIM-09 bug.
