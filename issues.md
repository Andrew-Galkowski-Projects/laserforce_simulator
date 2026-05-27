# Website Testing — Bugs & Issues

Date: 2026-05-25. Server: `runserver --noreload` (http://127.0.0.1:8000, `LF_CELERY_EAGER=1`). Branch: `api-03-async-batch-celery`. Scope: API-03 async batch + save flows via Celery + the new REST endpoints.

Severity legend: 🔴 High · 🟠 Medium · 🟡 Low · ℹ️ Note · ✅ Working

## Summary

| ID | Sev | Area | One-liner |
|----|-----|------|-----------|
| A3-1 | ✅ | UI batch POST | `POST /matches/simulate-batch/` (Phoenix vs Vipers, n=10) → 200 + `{job_id, team_red_id, team_red_name, team_blue_id, team_blue_name, arena_map_id, n}` JSON |
| A3-2 | ✅ | UI batch polling | `GET /matches/simulate-batch/status/<job_id>/?team_red_id=…&team_blue_id=…` → 200 + the locked 8-key polling JSON; `status="complete"`, `completed=10`, `total=10`, `partial` carries full `_aggregate_batch` shape (`n`, `red_wins`, `blue_wins`, `ties`, `red_win_pct`, `blue_win_pct`, `avg_red_score`, `avg_blue_score`, `red_scores`, `blue_scores`, `avg_seeds` as `[seed, flipped]` pairs, `outlier_seeds`, `side_advantage`) |
| A3-3 | ✅ | Batch UI render | Page DOM populated post-poll: `#batch-results` shows "Results — 10 simulations / completed in 0.10s / 40.0% Phoenix win rate / 4 W / 0 T / 6"; `#batch-red-win-pct` = "40.0"; `#batch-blue-win-pct` = "60.0"; `#batch-progress-container` shows "10/10 Complete in 0.1s"; `#batch-error` hidden; `#batch-save-games` panel visible |
| A3-4 | ✅ | Save POST | `POST /matches/save-batch-games/` (game_type=avg, n=5) → 200 + `{job_id}` |
| A3-5 | ✅ | Save polling | `GET /matches/save-batch-status/<job_id>/` → 200 + `{status: "complete", error: null, round_ids: [110, 111, 112, 113, 114]}` — **`"complete"` not `"done"` confirms the API-03 rename in-browser**, and 5 `GameRound` rows actually persisted |
| A3-6 | ✅ | REST POST | `POST /api/simulate-batch/` `{team_red: 16, team_blue: 17, n: 10}` → 200 + identical shape to UI POST (`job_id` + team ids/names + `arena_map_id` + `n`) |
| A3-7 | ✅ | REST polling | `GET /api/simulate-batch/<job_id>/?team_red_id=…&team_blue_id=…` → 200 + **identical 8-key shape to UI status endpoint** (`status` / `completed` / `total` / `partial` / `error` / `team_red_id` / `team_blue_id` / `arena_map_id`); seam contract §8.1 locked promise upheld |
| A3-8 | ✅ | Console / network | After server restart with `LF_CELERY_EAGER=1`: zero console errors, every XHR 200 across all 6 API-03 surfaces |
| A3-9 | ℹ️ | Dev requires Redis OR EAGER | `runserver` without `LF_CELERY_EAGER=1` AND without a Redis broker at `localhost:6379` returns 500 on POST (`redis.exceptions.ConnectionError`). Expected per ADR-0013 (broker is a production operational dependency). Documented in matches/CLAUDE.md async-execution section; settings.py also auto-flips to in-memory broker/backend whenever EAGER is on so dev never needs Redis. |

**Overall:** API-03 ships green. All four UI endpoints (POST/GET batch + POST/GET save) plus the two new REST endpoints (POST/GET) return the locked JSON contract from the seam document. The `"done"` → `"complete"` save-status rename is visible in-browser. UI polling JS picked up the query-param carry-forward, the JSON polling endpoint resolves the job from `AsyncResult(job_id)` correctly, and the in-process Celery `_BATCH_JOBS` / `_SAVE_JOBS` dicts are fully retired. No console errors, no non-2xx network requests once the dev server has either Redis or EAGER mode.

---

## API-03 async batch + save flows (Celery + Redis)

### ✅ A3-1 — UI batch POST returns locked JSON
`POST /matches/simulate-batch/` form-encoded `team_red=16&team_blue=17&n=10`. Server: 200 with `Content-Type: application/json`. Body keys exactly: `job_id`, `team_red_id`, `team_red_name="Phoenix"`, `team_blue_id`, `team_blue_name="Vipers"`, `arena_map_id=null`, `n=10`. Seam contract §9.1 shape.

### ✅ A3-2 — UI batch polling returns 8-key shape
`GET /matches/simulate-batch/status/eb831c4a-…/?team_red_id=16&team_blue_id=17`. Body has exactly the 8 keys pinned by seam contract §8.1: `status="complete"`, `completed=10`, `total=10`, `partial=<dict>`, `error=null`, `team_red_id=16`, `team_blue_id=17`, `arena_map_id=null`. `partial.avg_seeds` is a list of `[seed, flipped]` two-element lists, confirming the SIM-08 Orientation contract is intact under Celery. `partial.side_advantage` has all 8 documented sub-keys (`red_side_wins`, `blue_side_wins`, `side_ties`, `red_side_win_pct`, `blue_side_win_pct`, `avg_red_side_score`, `avg_blue_side_score`, `n`).

### ✅ A3-3 — Batch UI renders complete state
After the polling JS observes `complete`, the page DOM transitions correctly: `#batch-results` reveals the results panel (40.0% / 60.0% win rates), `#batch-progress-container` reads "10/10 Complete in 0.1s", and `#batch-save-games` reveals the save sub-form. `#batch-error` stays hidden. The 3 minimal JS edits described in the seam contract §10 work as intended (POST-response stash → query-param carry on poll URL; no `not_found` branch needed).

### ✅ A3-4 — Save POST returns job_id
`POST /matches/save-batch-games/` form-encoded `game_type=avg&n=5`. Server: 200 + `{"job_id": "a1055bcf-…"}`. Session-stashed seeds from A3-2 flow into the save task.

### ✅ A3-5 — Save polling returns `"complete"` (not `"done"`)
`GET /matches/save-batch-status/a1055bcf-…/`. Body: `{status: "complete", error: null, round_ids: [110, 111, 112, 113, 114]}`. Verified `"complete"` literal — the API-03 vocabulary rename (per ADR-0013) is live in production code, not just tests. `GameRound` rows 110-114 persisted to the DB (5 saves of the avg-bucket seeds).

### ✅ A3-6 — REST POST returns same shape as UI POST
`POST /api/simulate-batch/` with JSON body `{team_red: 16, team_blue: 17, n: 10}`. Returns 200 + identical shape to UI POST: `job_id`, `team_red_id=16`, `team_red_name="Phoenix"`, `team_blue_id=17`, `team_blue_name="Vipers"`, `arena_map_id=null`, `n=10`. CSRF token from cookie threaded through; no auth header needed (AllowAny inherits from REST_FRAMEWORK defaults per API-02).

### ✅ A3-7 — REST polling matches UI polling
`GET /api/simulate-batch/b5be61a1-…/?team_red_id=16&team_blue_id=17`. Body has **the same 8 keys** as the UI status endpoint: `status="complete"`, `completed=10`, `total=10`, `partial` with the full aggregate (8 partial keys observed), `error=null`, team/map ids echoed from query params. The seam contract §8.1 "identical shape across both surfaces" promise is upheld in-browser.

### ✅ A3-8 — Console + network clean
After the EAGER-mode restart, every API-03 XHR returned 200. Zero `error`/`warn` console messages on the batch-simulate page during the full POST → poll → save → poll cycle. No favicon noise.

## LG-00 player generation flow

Date: 2026-05-26. Server: `runserver --noreload` (http://127.0.0.1:8000). Branch: `lg-00-player-generation`. Scope: the new `/teams/generate/` surface, the Free Agents pool, the team-list filter, and cross-field form validation.

| ID | Sev | Area | One-liner |
|----|-----|------|-----------|
| LG00-1 | ✅ | Entry point | `/teams/` renders the new `generate-players-link` "Generate Players" button in the header, sibling to "Create New Team" |
| LG00-2 | ✅ | Form render | `GET /teams/generate/` 200 — all 4 fields present (`generate-players-num-teams` 22 options incl. `Random (2–10)`, `generate-players-per-team` 98 options spanning 6–9 + 12–100 + both random markers, `generate-players-mean` default 50, `generate-players-std-dev` default 15, `generate-players-submit` Generate button) |
| LG00-3 | ✅ | Teams branch | `POST num_teams=3 / players_per_team=6 / mean=50 / std_dev=15` → 200 confirmation page lists 3 created Teams (Onyx Owls #18, Echo Eagles #19, Tempest Titans #20), all with clickable detail links, zero console errors, only CDN network requests |
| LG00-4 | ✅ | Free-agents branch | `POST num_teams=0 / players_per_team=20` → 200 confirmation page shows "Created 20 free-agent players" + LG-00c deferral notice; no `Created teams` section rendered (correct — empty list suppressed by template `{% if %}`) |
| LG00-5 | ✅ | Free Agents filter | After creating free-agents, `/teams/` body still does NOT contain `"Free Agents"` — `Team.objects.regular()` excludes the system Team correctly (verified via `evaluate_script` body scan) |
| LG00-6 | ✅ | Cross-field validation (pool side) | `POST num_teams=0 / players_per_team=8` → 200, body contains literal `"Players per team must be 12–100"` (en-dash); form re-renders, no Teams or Players created |
| LG00-7 | ✅ | Cross-field validation (team side) | `POST num_teams=5 / players_per_team=50` → 200, body contains `"Players per team must be 6–9"` (en-dash); form re-renders, no Teams or Players created |
| LG00-8 | ℹ️ | URL prefix quirk | Team detail links rendered as `href="/<id>/"` not `href="/teams/<id>/"` — the root urlconf includes `teams.urls` twice (at `/teams/` and at `""`); Django's `reverse('team_detail')` picks the LAST registered prefix (the homepage include). `test_post_response_is_confirmation_page_with_team_links` was updated to use `reverse()` rather than hardcoding `/teams/<id>/`. Behaviour-correct (both URLs still serve the same view); test was over-specific |

**Overall:** LG-00 ships green. All seven planned checkpoints pass in the real browser; the form, the two output modes, the free-agents filter, and the cross-field error strings all match the seam contract verbatim. No regressions in console / network. One blast-radius test was updated honestly (LG00-8) — see §8 Step 8 triage above for the diff.

---

### ℹ️ A3-9 — Dev needs Redis or EAGER
Running `python manage.py runserver 8000 --noreload` without either (a) a Redis broker reachable at `localhost:6379` or (b) `LF_CELERY_EAGER=1` in the env returns 500 on POST `/matches/simulate-batch/` with `redis.exceptions.ConnectionError: Error 10061 connecting to localhost:6379`. This is the documented behaviour from ADR-0013 — Celery + Redis is the production execution path, and the local-dev story requires either spinning up Redis or opting into the in-process EAGER fallback. After setting `LF_CELERY_EAGER=1` and restarting, every flow above worked first try. `settings.py` automatically swaps both broker and backend to in-memory (`memory://` / `cache+memory://`) whenever EAGER is on, so EAGER dev mode needs no Redis whatsoever.

---

## LG-00b roster import from CSV (2026-05-26)

Date: 2026-05-26. Server: `runserver --noreload` (http://127.0.0.1:8000). Branch: `lg-00b-roster-import`. Scope: the LG-00b roster-import surface — `/import/`, `/import/template.csv`, and the team_list entry-point link.

### Summary

| ID | Sev | Area | One-liner |
|----|-----|------|-----------|
| LG00b-1 | ✅ | Team list entry point | "Import Roster" link present on `/teams/` between "Generate Players" and "Create New Team"; reverses to `/import/` (teams app is mounted at root) |
| LG00b-2 | ✅ | Import form page | `GET /import/` 200; all locked DOM ids present (`roster-import-form`, `roster-import-file`, `roster-import-submit`, `roster-import-template-link`); zero console errors; zero network failures |
| LG00b-3 | ✅ | Template CSV download | `GET /import/template.csv` 200 + `Content-Type: text/csv` + `Content-Disposition: attachment; filename="roster_template.csv"`; header row = 28 columns in `ALL_COLUMNS` order with capital-O `Offensive_synergy`; 2 example data rows (Red Phoenix Alice commander + Bob scout with quoted `"scout,medic"` cell) |
| LG00b-4 | ✅ | Empty submit | Submitting the form with no file selected triggers the browser-native `required` validation ("Please select a file"); no POST sent |
| LG00b-5 | ✅ | Malformed CSV → row error | POST with `role=captain` → 200, `roster-import-errors` ul rendered, `roster-import-error-1-role` `<li>` id, message `"role 'captain' is not one of ['ammo', 'commander', 'heavy', 'medic', 'scout']"` |
| LG00b-6 | ✅ | Template upload → happy path | POST the downloaded template CSV → 200, confirmation page with `roster-import-confirm-summary` ("Imported **2** players across **2** rows") and `roster-import-confirm-teams-list` containing `<a href="/22/">Red Phoenix</a>`; `/22/` team detail renders both players with Alice in `slot_commander` and Bob in `slot_scout_1` |
| LG00b-7 | ✅ | Slot collision rejection | Re-uploading the same template CSV against the now-existing Red Phoenix → 200, `roster-import-error-1-role` rendered with the locked-clarity message `"Team 'Red Phoenix' slot 'slot_commander' already filled by player 'Alice'"`; no new players written |
| LG00b-8 | 🟡 | Unique-name DB backstop | A row whose `(team, name)` matches an **existing** Player on an existing Team would NOT be caught by `_check_db_slot_collisions` (the pre-check only verifies slot FK occupancy, not name uniqueness). Such a row reaches `_apply_roster` and triggers an `IntegrityError` from the `Player.unique_together = ["team", "name"]` constraint; `@transaction.atomic` correctly rolls back so no partial state persists, but the user sees a Django 500 page rather than a friendly row-level `roster-import-error-N-name` rejection. Out of scope per seam contract §12 ("`unique_together` enforces this at the DB layer as a hard backstop") — flagging for future polish. Reproducer: pre-create `(Red Phoenix, Alice)` with a non-Commander role so the slot pre-check passes, then POST a CSV with a row whose `(team, name) == (Red Phoenix, Alice)`. |
| LG00b-9 | ℹ️ | Seam-contract drift on URL prefix | The seam contract states "Full URL: `/teams/import/`" in §6/§13, but the project's `urls.py` mounts the `teams` app at `""` not `"teams/"` (every other teams URL renders as `/`, `/create/`, `/generate/` etc). Actual URL is `/import/`. The URL **name** (`import_roster`) and the `path("import/", ...)` entry are both contract-correct — the contract only mis-describes the URL prefix. Functionally fine; reverse works; nav link works. |

**Overall:** LG-00b ships green. All 7 planned smoke-test walks pass in the real browser; the form, the template-download companion view, the per-row error rendering, the happy-path team creation + slot assignment, and the slot-collision rejection all match the seam contract verbatim. Zero console errors and zero non-2xx network requests across the entire walk. Two informational findings logged: LG00b-8 (DB-layer unique-name backstop is the contract's deliberate punt; produces a 500 instead of a row error when triggered) and LG00b-9 (the contract's `/teams/import/` URL prefix wording is off by the project's missing `teams/` mount prefix; the URL itself works correctly).

---

## LG-00c sortable players tab (2026-05-26)

Date: 2026-05-26. Server: `runserver --noreload` (http://127.0.0.1:8765). Branch: `lg-00c-sortable-players-tab`. Scope: the new `/players/` index page — server-side sort via `?sort=&dir=asc|desc`, HX-02 forgiving-fallback validation, pagination (50/page), Players nav link in base.html.

### Summary

| ID | Sev | Area | One-liner |
|----|-----|------|-----------|
| LG00c-1 | ✅ | Default render | `GET /players/` → 200; 116 players total, 23 columns, 3 pages, active "Team ↑" header, zero console errors, zero non-2xx network requests |
| LG00c-2 | ✅ | Nav link | "Players" anchor present in `base.html` immediately after "Teams" with id `player-list-nav-link`; reachable from every page |
| LG00c-3 | ✅ | `overall_rating` desc | `?sort=overall_rating&dir=desc` → annotation orders correctly; first row Alice/Red Phoenix at 68.3; active header "Overall ↓", flip-href `?sort=overall_rating&dir=asc` |
| LG00c-4 | ✅ | Capital-O alias | `?sort=offensive_synergy&dir=desc` (lowercase URL key) sorts on the capital-O `Offensive_synergy` ORM field; Bayani top at 90; active "Offensive Syn ↓" |
| LG00c-5 | ✅ | Python-branch `preferred_roles` | `?sort=preferred_roles&dir=asc` → players whose `preferred_roles == ["ammo"]` cluster first (joined string `"ammo"`); within the cluster the secondary `name` tiebreak orders `Anubis ☥`, `Bromatized`, `Dark Knight`; active "Preferred Roles ↑" |
| LG00c-6 | ✅ | Name + team links | Name cell anchors `<a href="/players/<id>/stats/">`; team cell anchors `<a href="/<team_id>/">` (teams app mounted at root — same `/<id>/` shape LG-00b found in LG00-8 / LG00b-9); Free Agents team links normally with no special-case |
| LG00c-7 | 🟠 | **Pagination links carry uncoerced invalid params** | `GET /players/?sort=BOGUS&dir=SIDEWAYS&page=2` — view coerces correctly (active header is "Team ↑" reflecting the asc fallback), but the pagination Previous/Next hrefs read `?sort=BOGUS&dir=SIDEWAYS&page=1` / `&page=3`. Header flip-links are clean (built from `querystring_without_sort_dir_page`); only pagination is affected (built from `querystring_without_page`, which preserves the raw uncoerced values). View is safe (re-coerces every request), but the URLs propagate the rubbish until the user changes sort. Fix: rebuild the page-link querystring from the coerced `sort` + `direction` rather than `request.GET.copy()`. |

**Overall:** LG-00c ships green except for LG00c-7. The view's sort/coerce/paginate machinery is correct; the issue is purely a URL-hygiene bug in how the template's page-link querystring is built. Zero console errors, zero non-2xx requests across all 5 URL variants tested. Fixing LG00c-7 inline before commit.

### Responsive smoke (LG-00c, follow-up)

| ID | Sev | Area | One-liner |
|----|-----|------|-----------|
| LG00c-8 | 🟠 | Layout at wide viewports | At 1920px, the 23-col table was clamped to 1272px by `base.html`'s `.container.mt-4` PLUS a redundant inner `<div class="container mt-4">` in `player_list.html` — ~600px of viewport wasted, table forced into a horizontal-scroll window even though it would have fit. Root cause: nested `.container` capped at Bootstrap's 1320px xxl max-width. Fix: removed the redundant inner container; applied `margin-left: calc(-50vw + 50%); margin-right: calc(-50vw + 50%); padding: 0 1rem;` to the `.table-responsive` so it breaks out of the outer container to span the full viewport. The calc no-ops at small viewports (resolves to ~0 when container width ≈ viewport width). Verified at 720/800/1280/1920/2560px: zero wasted space at every size; table fits at 2560 without scroll, scrolls horizontally only when needed at narrower widths. |
| LG00c-9 | 🟡 | Template-comment leak | My initial fix used a multi-line `{# ... #}` block which Django renders literally (single-line only — multi-line needs `{% comment %}...{% endcomment %}`). The opening `{# 23 stat columns need ~1975px; break ...` rendered as visible text under the player count. Fix: collapsed to a one-line `{# ... #}` comment. Surfaced by post-fix screenshot — pure visual bug, no test would have caught it. |

**Overall (post-follow-up):** LG-00c ships green. Two follow-up findings from manual responsive smoke; both fixed inline. Re-ran full pytest after the template restructure → 1480 passed (no regressions; the 28 LG-00c tests assert response substrings/context keys, not container nesting, so the restructure is transparent to them).

### Per-page dropdown (LG-00c, follow-up)

| ID | Sev | Area | One-liner |
|----|-----|------|-----------|
| LG00c-10 | ✅ | Per-page selector | New `?per_page=10\|25\|50\|100` query param + `<select>` dropdown above the table; default 10 (was 50). HX-02-style forgiving-fallback — invalid / out-of-whitelist / non-int values silently coerce to default. Auto-submit on change (`onchange="this.form.submit()"`) with `<noscript>` Apply button fallback. Dropdown carries `sort` + `dir` through hidden inputs so re-paginating does NOT reset the user's column ordering. `per_page` survives across page navigation (in `querystring_without_page`) AND across column-header re-sorts (in `querystring_without_sort_dir_page`). Verified in-browser at 1600px: default `?per_page=10` renders 10 rows / 12 pages; dropdown switch to 50 auto-submits to `?sort=team&dir=asc&per_page=50` rendering 50 rows / 3 pages; Next-link carries `per_page=50`; Name-column re-sort link carries `per_page=50`. Invalid `?per_page=BOGUS&sort=BOGUS&dir=SIDEWAYS` URL → all three coerce to defaults; pagination links read `?per_page=10&sort=team&dir=asc&page=N`. 9 new tests (5 pure-unit on `_coerce_per_page` truth table + 4 view tests on default-10 / each-size-renders / select-marks-active / per-page-carries-in-links); 1 existing test renamed (`test_default_pagination_is_10_per_page` was `test_pagination_renders_50_per_page`); 2 existing tests updated to either pass `per_page=50` (preserves their 51-player fixture intent) or shrink the fixture to 15 (page-1-only assertions). Full pytest: 1489 passed (was 1480 → +9). |

---

## LG-01a mode picker landing + /leagues/ list

Date: 2026-05-26. Server: `runserver --noreload` (http://127.0.0.1:8000). Branch: `lg-01a-mode-picker-landing`. Scope: new `/` landing (mode picker + in-progress League cards) and `/leagues/` index (active + archived tables, Create button).

| ID | Sev | Area | One-liner |
|----|-----|------|-----------|
| LG01a-1 | ✅ | `/` empty state | `GET /` with zero Leagues → 200; 3 mode cards render (Sandbox / Single-player League / Multiplayer); `in-progress-leagues` section omitted entirely (no empty notice); zero console errors; only the doc + Bootstrap CSS/JS network requests (all 200). |
| LG01a-2 | ✅ | `/leagues/` empty state | `GET /leagues/` with zero Leagues → 200; "Leagues" h1, "Create League" anchor present, "No Leagues yet." notice present; active/archived tables omitted; zero console errors. |
| LG01a-3 | ✅ | Landing populated | Seeded 3 ChromeTest Leagues (Alpha id=6 active w/ Season 1; Bravo id=7 active no Season; Charlie id=8 archived). `GET /` shows "In Progress" section with Bravo card FIRST and Alpha SECOND (sorted `-id` confirmed in DOM). Bravo subtitle reads "No active season"; Alpha subtitle reads "Season: Season 1". Both cards link to `/leagues/<id>/` (deferred broken — LG-01c). Charlie (archived) NOT rendered on landing. |
| LG01a-4 | ✅ | `/leagues/` populated | `GET /leagues/` renders Active table (Bravo, then Alpha — `-id` sort) AND Archived table (Charlie). Each row name links to `/leagues/<id>/`. Empty notice absent. `state-badge` cells visible per row. |
| LG01a-5 | ✅ | Navbar brand → landing | Navbar `⚡ Laserforce Manager` brand href is `/` (verified via snapshot). Was `/teams/` pre-LG-01a. |
| LG01a-6 | ✅ | Navbar Leagues nav link | New `<a id="leagues-nav-link">Leagues</a>` is the FIRST item in `navbar-nav` (before Teams). Verified on every page visited. |
| LG01a-7 | ✅ | Sandbox card click | Click on `mode-card-sandbox` navigated to `/teams/` (matches `{% url 'team_list' %}` reverse — `team_list` correctly resolves to `/teams/` now that the duplicate-mount-at-root has been removed). |
| LG01a-8 | ✅ | Multiplayer card disabled | `mode-card-multiplayer` rendered as a NON-anchor (no `<a>` wrapping it in the a11y snapshot — only a heading), visible "Coming soon" badge present, `aria-disabled="true"` in markup, visibly greyed via `opacity-50`. Clicking does nothing (no href). |
| LG01a-9 | ℹ️ | Deferred `/leagues/<id>/` 404 | `GET /leagues/6/` → 404 cleanly (no template exception, no console errors). Known broken until LG-01c lands — scope-acknowledged in the seam contract. |
| LG01a-10 | ℹ️ | Deferred `/leagues/create/` 404 | `GET /leagues/create/` → 404 cleanly. Known broken until LG-01b lands — scope-acknowledged. |
| LG01a-11 | ✅ | Responsive mobile | 720×1115 screenshot: navbar collapses to hamburger toggler; 3 mode cards stack vertically; "In Progress" cards stack vertically. No layout overflow, no horizontal scroll. |

**Overall:** LG-01a ships green. Landing + `/leagues/` index both render the empty AND populated states cleanly; `-id` sort verified in DOM order; mode-card click + navbar surfaces work; the two intentionally-deferred links (`/leagues/<id>/` to LG-01c, `/leagues/create/` to LG-01b) 404 cleanly without server-side exceptions, matching the seam-contract scope decision. Full pytest pre-smoke: 1602 passed, 1 xfailed, 1 xpassed (no regressions; +22 new LG-01a tests vs the pre-branch 1580).
