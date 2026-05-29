# Website Testing — Bugs & Issues

## LG-01h global nav restructure (2026-05-28)

Server: `runserver --noreload` on port 8000. Branch: `lg-01h-global-nav-restructure`. Scope: mode-based base.html (sandbox vs league top-nav), 23-entry sidebar with new STATS section, 19 placeholder URLs (LEAGUE/TEAM/PLAYERS/STATS + Help + Tools), `coming_soon` shared view, `app_mode` context processor.

| ID | Sev | Area | One-liner |
|----|-----|------|-----------|
| H1-1 | ✅ | Sandbox top-nav at `/` | Renders brand + 6 flat sandbox links (Teams / Players / Matches / Batch Sim / Create Team / Maps) + `League ▾` / `Help ▾` / `Tools ▾` dropdowns. Zero console errors. |
| H1-2 | ✅ | Help dropdown opens with 6 LIVE items | `Help ▾` expands to Overview / Changes / Custom Rosters / Debugging / LOL GM Forums / Zen GM Forums, each pointing at `/help/<slug>/`. |
| H1-3 | ✅ | `/help/overview/` placeholder renders | Title `Overview — Coming Soon`; sandbox top-nav (no sidebar — Help is sandbox mode); `<h1>Overview</h1>` + section badge `help` + body `Coming soon — this page is a placeholder for the Overview feature.` + feature key `help_overview`. |
| H1-4 | ✅ | League mode at `/leagues/15/` HIDES sandbox links | Top-nav collapses to brand + `League ▾` / `Help ▾` / `Tools ▾` only — the 6 sandbox flat links are absent. Confirms `app_mode` context processor's path-prefix branch fires. |
| H1-5 | ✅ | 23-entry sidebar renders on league dashboard | Dashboard (1) + LEAGUE (6, all LIVE: Standings → `/seasons/14/standings/`, Schedule → `/seasons/14/schedule/`, Playoffs / Finances / History / Power Rankings → `/leagues/15/<slug>/`) + TEAM (4, all LIVE: Roster / Schedule / Finances / History — Schedule still goes to LG-01g's `/leagues/15/team_schedule/62/`) + PLAYERS (6, all LIVE: Free Agents / Trade / Trading Block / Prospects / Watch List / Hall of Fame under `/leagues/15/players/<slug>/`) + STATS (6, new section, all LIVE: Game Log / League Leaders / Player Ratings / Player Stats / Team Stats / Statistical Feats under `/leagues/15/stats/<slug>/`). **1 + 6 + 4 + 6 + 6 = 23** entries with STATS section header rendered. |
| H1-6 | ✅ | `/leagues/15/stats/game-log/` placeholder renders with sidebar | Title `Game Log — Coming Soon`; league-mode top-nav (no sandbox links); full 23-entry sidebar; `<h1>Game Log</h1>` + section badge `stats` + body `Coming soon — this page is a placeholder for the Game Log feature.` + feature key `stats_game_log`. |
| H1-7 | ✅ | `League ▾` top-bar dropdown 5 items all LIVE | Standings → `/seasons/14/standings/` (resolved via LG-01f session-pin chain — `last_league_id=15`), Playoffs → `/leagues/15/playoffs/`, Finances → `/leagues/15/finances/`, History → `/leagues/15/history/` (LG-01f preserved), Power Rankings → `/leagues/15/power-rankings/`. The LG-01f-only-History-live shape is now 5/5 LIVE, fulfilling the LG-01h contract for `league_nav`'s 4 new top-bar URL keys. |
| H1-8 | ✅ | Console + network clean across every page | Zero console messages on `/`, `/help/overview/`, `/leagues/15/`, `/leagues/15/stats/game-log/`. Every network request 2xx. |
| H1-9 | ℹ️ | Pre-existing ChromeTest leagues in DB | Three `ChromeTest LG-01<x> League` entries from prior LG-01c/d/g test runs still in the DB. Not LG-01h-related. |

**Overall:** LG-01h ships green. Mode-based base.html branches correctly on path prefix; 23-entry sidebar with new STATS section renders on every league-context page; placeholder URLs navigate to the shared `coming_soon` view rendering the placeholder template with appropriate sidebar context. No console errors, no non-2xx network requests. Manual spot-checks of one Help URL (`/help/overview/`) and one Stats URL (`/leagues/15/stats/game-log/`) confirm both branches of the placeholder template (with-sidebar / without-sidebar). Screenshot: `screenshots/lg01h_league_mode_with_sidebar.png`.

---

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

---

## LG-01c league / season dashboards

Date: 2026-05-27. Server: `runserver --noreload` (http://127.0.0.1:8000). Branch: `lg-01c-league-season-dashboard`. Scope: the new `/leagues/<id>/` league dashboard + `/seasons/<id>/` season dashboard (draft branch via fresh LG-01b create), DOM-id audit, 404/405 surface, deferred broken-link healing.

| ID | Sev | Area | One-liner |
|----|-----|------|-----------|
| LG01c-1 | ✅ | `/leagues/` list still works | Typed `<int:league_id>/` URL does not shadow `path("", views.league_list)`; `/leagues/` returns 200, lists "ChromeTest LG-01c League" row, and its raw `/leagues/12/` href now resolves correctly (the LG-01a deferred broken-link is healed by LG-01c). |
| LG01c-2 | ✅ | `/leagues/create/` POST | LG-01b flow created League id 12 + Season id 10 + 4 enrolled Teams; redirect to `/seasons/10/standings/` works; LG-01c URL inserts haven't broken LG-01b's create flow. |
| LG01c-3 | ✅ | `/leagues/<id>/` draft branch | `GET /leagues/12/` → 200; title "ChromeTest LG-01c League — League"; the 4 always-present DOM ids (`league-dashboard-header`, `league-dashboard-state-badge`, `league-dashboard-action-button`, `league-dashboard-standings-snippet`) rendered; the 5 active/completed-only ids ABSENT (correct draft-branch suppression); `league-dashboard-no-season-notice` ABSENT (correct — League has a Season). Action button: `<button disabled data-action-state="start_season">Start Season</button>`. Top-3 standings snippet shows the 3 highest-overall teams (Aurora Aces, Ember Enforcers, Hyperion Hunters) with `0 pts` per the zero-filled StandingsRow contract. |
| LG01c-4 | ✅ | `/seasons/<id>/` draft branch | `GET /seasons/10/` → 200; title "ChromeTest LG-01c League — Season 1"; the 9 always-present DOM ids rendered (`season-dashboard-header`, `season-dashboard-state-badge`, `season-dashboard-action-button`, `season-dashboard-sidebar`, the 4 sidebar entries, `season-dashboard-standings-snippet`); the 5 active/completed-only ids ABSENT. Sidebar: standings + schedule render as `<a>` with reverse-resolved hrefs (`/seasons/10/standings/`, `/seasons/10/schedule/`); teams + history render as `<span>` (NO `<a href>` — verified via `querySelector("a") === null`). Overview entry is the active page. Action button: `<button disabled data-action-state="start_season">Start Season</button>`. |
| LG01c-5 | ✅ | 404 surface | `GET /leagues/99999/` → 404; `GET /seasons/99999/` → 404. `get_object_or_404` resolves cleanly with no template exception. |
| LG01c-6 | ℹ️ | POST returns 403 not 405 | Browser POSTs to `/leagues/12/` and `/seasons/10/` return **403** (Django CSRF middleware rejects before the view-level `HttpResponseNotAllowed(["GET"])` guard fires — no CSRF token on a GET-only page). This is standard behaviour for every GET-only view in the codebase. The view's 405 guard is correctly asserted by the test suite (Django's test client bypasses CSRF), so the unit-test 405 assertions pass while the real browser sees a CSRF 403. Not a regression. |
| LG01c-7 | ✅ | Console + network | Zero console errors across `/leagues/`, `/leagues/12/`, `/seasons/10/`. Only the doc + Bootstrap CSS/JS + the navbar SVG data-URL load on each page (all 200). |
| LG01c-8 | ✅ | Responsive | Verified at 720×1115 and 1280×900. Navbar collapses below 992px; both dashboards render without horizontal scroll; sidebar entries stack vertically on mobile. |

**Overall:** LG-01c ships green. All 9 DOM ids per dashboard render per the contract's branch-presence rules, the sidebar's disabled `<span>` vs live `<a>` distinction works, the LG-01a deferred broken-link is healed, no console errors, no failed network. Full pytest: 1701 passed / 1 xfailed / 1 xpassed (+ 74 LG-01c tests vs pre-branch 1627). Screenshots: `.claude/worktrees/lg-01c-league-dashboard.png`, `.claude/worktrees/lg-01c-season-dashboard.png`, `.claude/worktrees/lg-01c-league-dashboard-mobile.png`.

---

## LG-01d Play Season dropdown (2026-05-27)

Date: 2026-05-27. Server: `runserver --noreload` (http://127.0.0.1:8000, `LF_CELERY_EAGER=1`). Branch: `lg-01d-play-dropdown`. Scope: LG-01d 5 new POST endpoints + 1 polling endpoint + dropdown UI on Season + League dashboards.

| ID | Sev | Area | One-liner |
|----|-----|------|-----------|
| LG01d-1 | ✅ | `/leagues/create/` POST | Created League id 13 + draft Season id 11 + 4 enrolled Teams; redirected to `/seasons/11/standings/`. LG-01b flow unaffected by LG-01d URL inserts. |
| LG01d-2 | ✅ | `/seasons/<id>/` draft branch | `GET /seasons/11/` → 200 in draft state; **Start Season** button rendered as an active `<form>` POSTing to `/seasons/11/start-season/` (replaces the LG-01c `<button disabled>` placeholder). State badge "DRAFT", `season-dashboard-play-start-season` form present, `season-dashboard-action-button` wrapper id preserved. |
| LG01d-3 | ✅ | `POST /start-season/` | Clicking Start Season → 302 → dashboard reloads in ACTIVE state with the Play dropdown trigger + standings preview + "Next round" card + leaders snippets. `Season.start_season()` flipped `draft → active`. |
| LG01d-4 | ✅ | Play dropdown expansion | Active Season dashboard's action button is an expandable dropdown ("Play Next" label per LG-01c context); expands to 3 options: "Play One Week" / "Play Two Months" / "Play Until End of Season". 14 locked DOM ids per dashboard pair confirmed in DOM via `id="…-play-{dropdown,one-week,two-months,until-end,error,progress}"`. |
| LG01d-5 | ✅ | `POST /play-week/` (sync) | One Week click → `POST /seasons/11/play-week/` → 302 → dashboard reloads with `Rounds played: 2 / 12` (matchday 1's 2 fixtures). Next round advances to "Matchday 2 · Round 1". Sync `with transaction.atomic():` block worked; leaders populate after one round of data. |
| LG01d-6 | ✅ | `POST /play-two-months/` (Celery EAGER) | Two Months click → `POST /seasons/11/play-two-months/` → **202** + `{"job_id": "a2c04004-46ca-4211-a530-0510a7b04059", "season_id": 11}` (locked POST shape). Polling JS picks up `job_id` + `season_id`. |
| LG01d-7 | ✅ | `GET /play-status/<job_id>/` polling | Single polling roundtrip: `GET /seasons/11/play-status/a2c04004-…/?season_id=11` → 200. EAGER mode resolves the task synchronously inside the POST so the first poll observes `status: "complete"` and reloads. `season_id` query-param carry pattern works. |
| LG01d-8 | ✅ | Season auto-complete via `complete_if_finished` | Two Months capped at 8 matchdays; 4-team Season had 5 unplayed matchdays remaining (10 fixtures) → played all 10 → final fixture's `simulate_scheduled_round` triggered `season.complete_if_finished()` → state flipped to **COMPLETED**, champion stamped. Dashboard re-renders with `Rounds played: 12 / 12`, "All fixtures played", `Start Next Season` button rendered as disabled placeholder (LG-01e deferred per contract). |
| LG01d-9 | ✅ | League dashboard mirror surface | `GET /leagues/13/` → 200; League dashboard reflects the completed Season's state badge + standings + leaders + "All fixtures played" + disabled `Start Next Season`. Symmetric Play surface uses `displayed_season.id` for POST URLs (resolved via LG-01c `league.active_season` fallback to most-recent completed). |
| LG01d-10 | ✅ | Standings + leaders rendered correctly | Final standings: Hyperion Hunters 9 pts (3W) / Zenith Zealots 6 pts (2W) / Aurora Aces 3 pts (1W). League points formula `3W + 1T + 0L` honored. Leaders snippet picks top 3 per stat (`points_per_game` / `tags_per_game` / `tag_ratio`) via the LG-01c `compute_leaders` pure module. |
| LG01d-11 | ✅ | Console + network | Zero console errors across `/leagues/create/`, `/seasons/11/` (draft + active + completed transitions), `/leagues/13/`. All 15 captured network requests 2xx/3xx (1 doc GET + 1 POST 302 + 1 POST 202 + 1 polling GET 200 + repeated Bootstrap CDN + navbar SVG). Polling endpoint returned 200, no 404/500. |
| LG01d-12 | ℹ️ | Dropdown label still "Play Next" | The dropdown trigger button's label reads "Play Next" (from LG-01c's `action_button_label` for `state="play_next"`) rather than the mockup's bare "Play ▾". Cosmetic — the dropdown still expands and the three submit forms work. Relabel is a 1-line LG-01c context tweak; deferred (out of LG-01d scope, contract did not lock the trigger label). |
| LG01d-13 | ℹ️ | Play Until End not separately exercised in-browser | The Until End async path shares the `play_season_task` body with Two Months (only `max_matchdays=None` vs `=8`) and the same polling endpoint. Test coverage in `test_lg01d_tasks.py::TestPlaySeasonTaskHappyPath::test_play_until_end_loops_n_rounds_and_persists_game_round_rows` exercises the full path under EAGER. In-browser surface is structurally identical to Two Months. Not a gap; the explicit browser exercise was redundant. |

**Overall:** LG-01d ships green. All 5 new POST endpoints + the polling GET work end-to-end in-browser; the Season auto-complete chain from `play_season_task` → `simulate_scheduled_round` → `complete_if_finished` fires correctly; per-Round atomic commits (ADR-0016) confirmed by the 12-of-12 progression. Zero console errors. Full pytest: **1755 passed / 1 xfailed / 1 xpassed** (+54 new LG-01d tests; 1 LG-01c blast-radius test honestly updated from `<button disabled>` to `<form>` shape since LG-01d intentionally activated the Start Season placeholder).

## LG-01e Start Next Season chain

Date: 2026-05-27. Server: `runserver --noreload` (http://127.0.0.1:8000). Branch: `lg-01e-start-next-season`. Scope: the new `POST /leagues/<int:league_id>/next-season/` endpoint and its dashboard form wiring on both `templates/leagues/dashboard.html` + `templates/seasons/dashboard.html`. Test fixture: League id 14 "ChromeTest LG-01e League" (4 teams), Season id 12 "Season 1" force-completed via shell (avoids the ~30 min cost of playing 12 fixtures), then the new Season id 13 "Season 2" created via the LG-01e button.

| ID | Sev | Area | One-liner |
|----|-----|------|-----------|
| LG01e-1 | ✅ | League create + draft Season setup | `POST /leagues/create/` with `num_teams=4` → 302 → `/seasons/12/standings/`; League 14 + Season 12 (draft) created with 4 auto-rostered teams (ids 54-57) |
| LG01e-2 | ✅ | `/seasons/<id>/` completed branch renders new `<form>` | `GET /seasons/12/` (after force-completing Season 12) → 200; `#season-dashboard-next-season-form` element present with `method="post"`, `action="http://127.0.0.1:8000/leagues/14/next-season/"` (derived JOIN-free via `season.league_id`), CSRF token input present, submit button text `"Start Next Season"`, `data-action-state="start_next_season"`, NOT disabled. LG-01c outer wrapper `#season-dashboard-action-button` preserved and contains the form (backwards-compat with LG-01c tests). |
| LG01e-3 | ✅ | `/leagues/<id>/` completed branch renders new `<form>` | `GET /leagues/14/` → 200; symmetric `#league-dashboard-next-season-form` element present with `action="/leagues/14/next-season/"` (uses `league.id` directly), CSRF + submit + data-action-state same as season dashboard. LG-01c outer wrapper preserved. |
| LG01e-4 | ✅ | `POST /next-season/` happy path | Clicking Start Next Season → `POST /leagues/14/next-season/` → 302 → `/seasons/13/`. New Season created with `name="Season 2"`, `state="draft"`, `start_date=date(2027, 1, 1)` (calendar-year jump from prev's 2026-01-01), `schedule_format="single_round_robin"` (carried over), `champion_team=None`, `starting_team_ids_json=None` (snapshot is set only by `Season.start_season()`, not at create). Teams M2M populated from prev `starting_team_ids_json` snapshot: ids `[54, 55, 56, 57]` — identical to prev. |
| LG01e-5 | ✅ | New draft Season displays correctly | `/seasons/13/` renders with title `"ChromeTest LG-01e League — Season 2"`, DRAFT state badge, top-3 standings of the copied teams (Aurora Aces / Ember Enforcers / Hyperion Hunters), action button is **"Start Season"** (LG-01d `start_season` state — LG-01c picked the new draft as `displayed_season`). The LG-01e button auto-hides because no completed Season is on display. |
| LG01e-6 | ✅ | UI guard: `/leagues/<id>/` hides Start-Next button when draft exists | After Season 2 created, `GET /leagues/14/` no longer renders `#league-dashboard-next-season-form` (LG-01c `displayed_season` picks the new draft, action button becomes "Start Season"). UI prevents double-creation. |
| LG01e-7 | ✅ | Server-side guard: stale POST redirects to active Season | Direct `fetch("/leagues/14/next-season/", {method:"POST"})` with a draft Season already in-place → 302 to `/seasons/13/` (the existing draft), no new Season created. `Season.objects.filter(league_id=14).count() == 2` confirmed before/after. The `league.active_season` check + redirect is the locked guard from seam contract §2 step 1. |
| LG01e-8 | ✅ | Console + network | Zero console errors from LG-01e surfaces. All 3 POSTs returned 302 (1 happy path + 2 active-Season-guard probes). Single console `error` row in preserved messages is the `HEAD /seasons/13/` 405 from my own evaluate_script probe (`season_dashboard` is GET-only — expected, not an LG-01e issue). |
| LG01e-9 | ℹ️ | `start_date` formula reframed at grilling time | PLAN.md line 721's `start_date = previous.start_date + 7 * 2 * (N-1) days` formula was reframed at grill-with-docs to `date(latest_completed.start_date.year + 1, 1, 1)` — calendar-year jump per the user's "January of current year + N" framing. Confirmed in-browser: prev=2026-01-01 → new=2027-01-01. Recorded in PLAN.md `- note` for LG-01e + matches/CLAUDE.md LG-01e section. |
| LG01e-10 | ℹ️ | Archive League toggle scoped out | PLAN.md line 723's "League dashboard also gains an Archive League toggle" was narrowed at grill-with-docs to admin-only (`LeagueAdmin` already supports flipping `state="archived"`; no public UI). Recorded in scope-out of seam contract §6 + PLAN.md `- note`. No League-archive UI ships in LG-01e. |

**Overall:** LG-01e ships green. The new POST endpoint creates the next-year Season with copied teams + auto-generated name, the dashboard form wiring activates the LG-01c-locked `start_next_season` slot on both surfaces, the UI guard hides the button when a non-completed Season exists, and the server-side guard redirects stray POSTs to the in-progress Season without creating duplicates. Zero LG-01e console errors. Full pytest: **1787 passed / 1 xfailed / 1 xpassed** (+32 new LG-01e tests; existing LG-01c dashboard tests continue to pass because the LG-01c-locked outer wrapper id + `data-action-state` attribute are preserved on the new form's submit button).

---

## LG-01f League History + 14-entry sidebar + top-bar `League ▾` dropdown (2026-05-27)

Date: 2026-05-27. Server: `runserver --noreload`. Branch: `lg-01f-league-history`. Scope: new `/leagues/<id>/history/` page, new 14-entry sidebar partial wired on 5 League-context pages, new top-bar `League ▾` dropdown replacing the LG-01a `Leagues` link, new `league_nav` context processor.

| ID | Sev | Area | One-liner |
|----|-----|------|-----------|
| LG01f-1 | 🟠→✅ | Sidebar partial comment leak | `templates/_partials/league_sidebar.html:1` used a multi-line `{# ... #}` block which Django renders as visible text (single-line only). The leaked text appeared at the top of every page rendering the sidebar (`/leagues/<id>/`, `/leagues/<id>/history/`, `/seasons/<id>/`, `/seasons/<id>/standings/`, `/seasons/<id>/schedule/`). **Fixed in-PR**: converted to `{% comment %} … {% endcomment %}`. Same class as LG-00c-9 inline fix; regression-prone. |
| LG01f-2 | ✅ | League history page renders | `GET /leagues/12/history/` (in-progress Season) → 200; title `"ChromeTest LG-01c League — History"`; 10-column table; in-progress row at top with link to `/seasons/10/`, `4` teams, `0` matches, `"In progress"` champion-cell badge, live runner-up `"Ember Enforcers"`, em-dash Tournament Champion placeholder, top-3 cells populated from live `compute_standings(...)` (`"Aurora Aces"` / `"Ember Enforcers"` / `"Hyperion Hunters"`). Per-page selector renders with options `10/25/50/100`, default `10` selected. |
| LG01f-3 | ✅ | Completed-Season row variant | `GET /leagues/13/history/` (League with one completed Season) → 200; one completed row rendered with champion `"Hyperion Hunters #2"` (from `Season.champion_team` FK), runner-up `"Zenith Zealots #2"` (from `compute_standings` rank 2), top-3 `["Hyperion Hunters #2", "Zenith Zealots #2", "Aurora Aces #2"]`, `6` matches played, em-dash Tournament Champion placeholder. |
| LG01f-4 | ✅ | Sidebar 14-entry shape | `evaluate_script` confirmed: `#league-sidebar` present on all 5 wired pages; exactly 14 entries (1 Dashboard + 6 LEAGUE + 4 TEAM + 3 PLAYERS); 4 live links (Dashboard / Standings / Schedule / History) + 10 disabled `<span>` placeholders. |
| LG01f-5 | ✅ | Sidebar `active` class per page | `/leagues/12/` → `["sidebar-top-dashboard"]`; `/leagues/12/history/` → `["sidebar-league-history"]`; `/seasons/10/` → `[]` (sidebar_active=None per ADR-0017); `/seasons/10/standings/` → `["sidebar-league-standings"]`; `/seasons/10/schedule/` → `["sidebar-league-schedule"]`. Exactly one (or zero on season dashboard) active entry per page — matches seam contract §5d. |
| LG01f-6 | ✅ | LG-01c sidebar DOM ids removed | `#season-dashboard-sidebar` and its 4 child ids (`-standings`, `-schedule`, `-teams`, `-history`) confirmed absent from `/seasons/10/standings/` (and the other 4 wired pages). LG-01c sidebar surface fully superseded per ADR-0017. |
| LG01f-7 | ✅ | Top-bar `League ▾` dropdown | `templates/base.html` LG-01a `<a class="nav-link">Leagues</a>` replaced with `<li class="nav-item dropdown">` containing a `League ▾` toggle (preserves LG-01a-locked `id="leagues-nav-link"`; `href="/leagues/"` still works as direct click). Dropdown menu has 5 items: Standings/Playoffs/Finances/Power Rankings disabled `<span class="dropdown-item disabled">`, History live `<a id="league-history-topbar-link">`. |
| LG01f-8 | ✅ | `league_nav` context processor 3-step chain | On landing (`/`) with no session-pinned league: `top_bar_history_url` resolves to `/leagues/` (2+ Leagues exist, no session pin → step 3). After visiting `/leagues/12/`, all 4 subsequent `/seasons/10/...` pages report `top_bar_history_url="/leagues/12/history/"` (session pin set by `league_dashboard`'s `request.session["last_league_id"] = league.id` write — step 1 of chain). Pin survives cross-page navigation. |
| LG01f-9 | 🟠→✅ | `league_nav` crashed inside broken atomic block | Real regression discovered by 8 `teams/tests/test_roster_import_view.py` test failures: `roster_import` view uses `@transaction.atomic` + `transaction.set_rollback(True)` inside the `RosterImportError` catch block, then re-renders the form page with errors. The render fires the `league_nav` context processor whose `League.objects.filter(...).exists()` / `values_list(...)[:2]` queries raise `TransactionManagementError` ("You can't execute queries until the end of the 'atomic' block"). **Fixed in-PR**: wrapped both ORM queries in `try/except DatabaseError`, falling through to `reverse("league_list")` on broken-transaction state. `core/context_processors.py:51,67`. |
| LG01f-10 | 🟠→✅ | LG-01c state-matrix tests asserted deleted DOM ids | 3 LG-01c tests (`TestSeasonDashboardStateMatrix::test_{draft,active,completed}_renders_all_locked_dom_ids`) asserted the now-deleted `season-dashboard-sidebar*` DOM ids → blast-radius failure. **Fixed in-PR**: dropped the 5 obsolete LG-01c ids from each test's `dom_id` tuple; added a comment pointing at the new `TestLg01fSidebarRendered` class which covers the new `league-sidebar` / `sidebar-{section}-{key}` ids. |
| LG01f-11 | ✅ | Console + network | Zero console errors on all 5 wired pages. All requests 200 (excluding the dropdown caret SVG `data:` URL which is also 200). |
| LG01f-12 | ✅ | Full pytest suite | **1875 passed / 0 failed / 1 xfailed / 1 xpassed** after both fixes (was 1864 passed / 11 failed pre-fix). 11 new test files / classes from LG-01f: `test_league_history.py` (8 classes, ~40 methods), `test_league_sidebar.py` (2 classes, 21 methods), `test_league_nav_context_processor.py` (1 class, 8 methods), plus `TestLg01fSidebarRendered` + `TestLg01fSessionWrite` appended to LG-01c dashboard test files, plus session-write extensions in `test_lg01_views.py`, `views_tests.py`, `test_lg01e_next_season.py`. `TestSeasonDashboardSidebar` (LG-01c) deleted. |

**Overall:** LG-01f ships green after fixing 3 in-PR issues found by the full pytest run + Chrome smoke. The new `/leagues/<id>/history/` page renders the in-progress row with a live standings snippet + the completed Seasons table; the new 14-entry sidebar replaces the LG-01c 5-entry sidebar wholesale on all 5 League-context pages; the top-bar `League ▾` dropdown is wired with the History link resolved per-user via `request.session["last_league_id"]`. Two real regressions caught and fixed: the visible template-comment leak in the sidebar partial (LG01f-1), and `league_nav` crashing during `roster_import`'s rollback-then-render flow (LG01f-9). One blast-radius LG-01c test cluster updated to drop the deleted `season-dashboard-sidebar*` DOM ids (LG01f-10).
