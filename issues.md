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

### ℹ️ A3-9 — Dev needs Redis or EAGER
Running `python manage.py runserver 8000 --noreload` without either (a) a Redis broker reachable at `localhost:6379` or (b) `LF_CELERY_EAGER=1` in the env returns 500 on POST `/matches/simulate-batch/` with `redis.exceptions.ConnectionError: Error 10061 connecting to localhost:6379`. This is the documented behaviour from ADR-0013 — Celery + Redis is the production execution path, and the local-dev story requires either spinning up Redis or opting into the in-process EAGER fallback. After setting `LF_CELERY_EAGER=1` and restarting, every flow above worked first try. `settings.py` automatically swaps both broker and backend to in-memory (`memory://` / `cache+memory://`) whenever EAGER is on, so EAGER dev mode needs no Redis whatsoever.
