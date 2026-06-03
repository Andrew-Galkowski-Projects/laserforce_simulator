# Web testing — LG-02a (sandbox single-elimination Tournament)

Date: 2026-06-02
Branch: `lg-02a-sandbox-single-elim`
Scope: smoke-test the new sandbox Tournaments feature at `/tournaments/` — nav
entry, list, create (generate path), seeding, lock-and-build with byes (N=5),
game-by-game play + advancement to champion, plus a regression glance at the
sandbox pages whose nav (`base.html`) was edited.

## Severity legend
- 🔴 critical — broken feature, data loss, crash
- 🟠 warning — visible bug, no data loss
- 🟡 minor — cosmetic / pre-existing nit
- 🔵 environment — host/cache/tooling, not the code under test
- ✅ verified working

## Summary

| Area | Result |
|---|---|
| Sandbox nav `Tournaments` link (`tournaments-nav-link`) present, /tournaments/ 200 | ✅ |
| Tournament list — populated row, `Create Tournament` link | ✅ |
| Create form — name + multi-select existing + generate-count/ppt (no `max` cap; `valuemax=0` is a Chrome a11y default, not an HTML attr — `tournament_create.html:34,39`) | ✅ |
| Create via **generate 5 teams** → tournament 2 created, setup state | ✅ |
| Seeding table editable in setup (seeds 1–5 default by overall-rating desc) | ✅ |
| Lock & Build → state `active`, bracket built | ✅ |
| **N=5 Byes** — 8-slot bracket, top 3 seeds [1][2][3] get round-1 Byes, `[4]v[5]` real match | ✅ |
| Game-by-game `Play Next Match` — sims one Match, advances winner into parent slot | ✅ (matches 48–51) |
| `View match` deep-links to `/matches/<id>/` | ✅ |
| Completion → state `completed`, `Champion: Rogue Recon #2` banner, play button gone | ✅ |
| Completed-bracket render (tournament 1 "Smoke") + champion | ✅ |
| Regression: `/teams/`, `/matches/`, `/matches/simulate-batch/` after nav edit | ✅ 200, console clean |
| Console errors/warnings across all tournament pages | ✅ none |
| Network non-2xx | ✅ none |

## Findings — LG-02a

- 🟡 **Bracket-tier headers read "Round 1 / Round 2 / Round 3".** `templates/matches/tournament_detail.html` labels each **Bracket round** as "Round N". The CONTEXT.md `### Tournaments` glossary explicitly says a Bracket round must **never be shortened to "Round"** (collides with the 15-min game **Round**/`GameRound`), and the seam contract suggested stage labels (Quarterfinal / Semifinal / Final). Cosmetic only — no functional impact — but worth aligning to the locked vocabulary (e.g. "Bracket Round N", or computed stage names). Candidate for the `/code-review` SUGGEST pass.

## Verified flows — LG-02a
- Create (generate 5) → setup → reseed-editable → Lock & Build (3 byes, N=5) → Play Next ×4 (R1 `[4]v[5]`, two semis, final) → `completed` + champion banner. Every step 200, console + network clean.
- Advancement verified node-by-node: match 48 winner Ember (seed 5) → R2 vs [1]; match 49 Hyperion → final; match 50 Rogue → final; match 51 Rogue → champion.

> Test data left in the **gitignored dev `db.sqlite3`** (disposable, ADR-0004): Tournaments 1 ("Smoke", Code-agent smoke test) + 2 ("ChromeTest Cup"), their generated Teams, and Matches 45–51. Not part of the PR.

---

# Web testing — LG-06h (League player page + watch flag)

Date: 2026-06-02
Branch: `lg-06h-league-player-page`
Scope: smoke-test the new league-pinned player page at
`/leagues/<league_id>/players/<player_id>/` (reached from the 8 LG-06f league
screens), the repointed player-name links, the watch flag, and the Regular-Season
stats table (empty + populated), against real league data (League 19 "sample" =
empty Season; League 22 "Per-League Pool A" = played Season).

## Severity legend
- 🔴 critical — broken feature, data loss, crash
- 🟠 warning — visible bug, no data loss
- 🟡 minor — cosmetic / pre-existing nit
- 🔵 environment — host/cache/tooling, not the code under test
- ✅ verified working

## Summary

| Area | Result |
|---|---|
| New page renders (bio, Overall, grouped 19 ratings) | ✅ |
| Potential block shows `—` + "Arrives with LG-05." | ✅ |
| RS table empty-state ("…no recorded Rounds in <league> yet") | ✅ League 19 |
| RS table populated: per-Season row + Career row, Team from Rounds | ✅ League 22 (Season 1 + Career, Zenith Zealots #9, GP 1, 7082) |
| RS table header = Year + Team + 15 stat columns; `.table-responsive`-wrapped | ✅ |
| 5 "coming soon" stubs (Playoffs/Ratings-history/Awards/Salaries/Transactions) | ✅ |
| Header "Career stats (global)" link → `/players/<id>/stats/` | ✅ |
| Watch flag renders in header; click → `POST .../watch-list/toggle/` → 200; flips to `aria-pressed=true` + `.watch-flag-on` | ✅ |
| Player-name links repointed to `/leagues/<lid>/players/<pid>/` (Team Roster, Statistical Feats wrapped from plain text) | ✅ |
| LG-01f sidebar rendered (no active entry) | ✅ |
| Console errors/warnings | ✅ none |
| Network non-2xx (excl. known favicon) | ✅ none |

## Verified flows — LG-06h
- Team Roster (League 19) player names → `/leagues/19/players/<id>/`; clicked Yipsty (522) → page 200, all sections present, console clean, all requests 200.
- Watch flag toggle on the new page: `POST /leagues/19/players/watch-list/toggle/` → 200; button → `aria-pressed=true`, `.watch-flag-on`.
- Populated RS table at `/leagues/22/players/810/`: per-Season ("Season 1", Zenith Zealots #9) + Career rows; per-Season Team derived from the player's actual Rounds (not current `Player.team`).
- Statistical Feats (League 22): previously plain-text player names now wrapped in `/leagues/22/players/<id>/` `<a>` links; zero remaining global `/players/<id>/stats/` links on the screen.

## Findings — LG-06h
- 🟡 **Pre-existing, out of scope (NOT an LG-06h regression).** At ≤720px viewport the page has horizontal page-scroll driven by the wide RS table inside the d-flex sidebar shell (docScrollW ≈1313 > clientW 705). The RS table *is* correctly wrapped in `.table-responsive`; the overflow comes from the flex `main` column not shrinking (missing `min-width:0` flexbox idiom). The existing **Player Stats** screen (`/leagues/22/stats/player-stats/`) overflows identically (1365 > 705) with the same wrapper — so this is a project-wide league-screen shell behavior affecting every wide-table league page equally, not introduced by LG-06h. Logged for a future sidebar-shell responsive pass; not fixed here.
- ✅ No code bugs found in LG-06h. New page, repoints, watch flag, and RS table all behave per the seam contract.

## Teardown — LG-06h
- None required. No teams/matches/rounds created. The watch flag toggle wrote only to the **session** (no DB); flag was left toggled on player 522 (session-only, disposable). Server stopped after testing.

---

# Web testing — LG-06f (Watch List as a full stats view + per-League watch flag)

Date: 2026-06-02
Branch: `lg-06f-watch-list-stats`
Scope: smoke-test the in-row watch flag (instant-fetch toggle) on the league
player screens + the reshaped Watch List screen (Player-Stats columns,
zero-fill, Remove All), against real league data (League 22 "Per-League Pool A").

## Severity legend
- 🔴 critical — broken feature, data loss, crash
- 🟠 warning — visible bug, no data loss
- 🟡 minor — cosmetic / pre-existing nit
- 🔵 environment — host/cache/tooling, not the code under test
- ✅ verified working

## Summary

| Area | Result |
|---|---|
| Watch flag renders on Player Stats / Free Agents / Team Roster | ✅ |
| Flag click → instant red toggle, no page reload | ✅ |
| Toggle endpoint `POST /leagues/22/players/watch-list/toggle/` → 200 | ✅ |
| Toggle endpoint GET → 405 (POST-only guard) | ✅ |
| Watch persists across navigation (per-League session) | ✅ |
| Watch List screen renders watched player in full Player-Stats columns | ✅ |
| Season / Rate / Per-page kit present; NO team filter (per spec) | ✅ |
| Remove All clears the list → correct empty-state notice | ✅ |
| Console errors / warnings | ✅ none |

## Verified flows

- ✅ **Watch flag toggle (Player Stats, League 22).** Clicked the ★ flag on
  Wilson (player 828). Network: a single `POST /leagues/22/players/watch-list/toggle/`
  → `200`, no other requests, no page reload (URL stayed
  `/leagues/22/stats/player-stats/`). Button gained `watch-flag-on`; computed
  color `rgb(220, 53, 69)` (Bootstrap danger red). `GET` on the same endpoint
  returns `405` — POST-only guard holds.
- ✅ **Persistence + Watch List screen.** Navigated to
  `/leagues/22/players/watch-list/`; Wilson rendered with the full Player-Stats
  column set (GP 1, Points 13482, MVP 24.9, …) identical to the Player Stats row,
  flag `pressed`. Header shows "1 player", Season + Rate + Per-page controls, a
  Remove All link, and the per-League session note ("…local to this browser and
  this League, not shared across … other Leagues"). No team filter (matches spec).
- ✅ **Remove All.** Clicked Remove All (`?action=clear`) → list emptied → empty
  notice "Your watch list is empty — open any player table and click the ★ flag
  to start tracking players."
- ✅ **Flag presence + single script partial.** Free Agents: 10 `.watch-flag`
  buttons + exactly 1 `watch-flag` script block. Team Roster: 6 flags + 1 script
  block. No console messages on any page.

## Issues found

- None.

## Teardown

- None required. The watch list is **session-only** (no DB writes); the toggled
  state was cleared via Remove All during testing. No teams/matches/rounds were
  created. Server (pid from this run) stopped after testing.

---

## LG-02a-2 — Sandbox Tournament: CSV import + async play-all (2026-06-02)

Black-box pass over the tournament-detail surfaces added by LG-02a-2.
Severity: 🟢 working · 🟡 minor/environment · 🔴 code bug.

| Area | Result |
|------|--------|
| Tournament create (`/tournaments/create/`) | 🟢 renders + creates (generate 4 teams) |
| Detail **setup** — CSV import form | 🟢 all DOM ids render; import works end-to-end |
| Detail **active** — Play All button | 🟢 renders + fires POST, no JS/console errors |
| Sync **Play Next Match** (shared engine) | 🟢 resolves a match + advances winner |
| Async **Play All** completion in-browser | 🟡 environment-only stall (no Redis) — see note |

### 🟢 CSV participant import (setup state)
`/tournaments/4/` setup page rendered `Import Participants (CSV)` with
`tournament-import-form` / `-file` / `-submit` / `tournament-import-template-link`
(→ `/teams/import/template.csv`). POSTing a 4-team roster CSV returned **302**
and the field grew **4 → 8 participants** (the 4 `ChromeTest Import` teams added)
and **re-seeded by talent** (Zenith Zealots #14 dropped to seed 8). No console
errors, all network 2xx/3xx.

### 🟢 Lock & Build + sync engine
`Lock & Build Bracket` → **Active**, bracket rendered (8 teams, 3 bracket rounds,
standard 1v8/4v5/2v7/3v6 pairing). `Play Next Match` (the refactored
`tournament_engine.play_next_node`, no Celery) resolved node [1]v[8] → **match
55**, `Winner: Ember Enforcers #14`, and the winner **advanced into Bracket
Round 2** — fast (< a few seconds), correct advancement.

### 🟡 Async Play All — environment-only stall (NOT a code bug)
The `Play All` button renders and its POST to `/tournaments/<id>/play-all/`
fires with no JS/console errors. Full async completion could **not** be observed
in-browser: the dev server was run with `LF_CELERY_EAGER=1` (no Celery worker),
but `CELERY_RESULT_BACKEND` points at Redis, which is not running locally. In
eager mode `task.delay()` / `self.update_state(...)` block on the dead Redis
backend, so the eager POST stalled and committed zero nodes.
- **Root cause:** Celery result-backend config + absent Redis in the dev smoke
  setup — not the LG-02a-2 code. The shared engine is proven correct by the sync
  `Play Next Match` path above, and `play_tournament_task` is covered green by
  the `CELERY_TASK_ALWAYS_EAGER` unit tests in
  `matches/tests/test_tournament_tasks.py` (plays-to-champion, stage progress,
  resumable, inactive no-op). In production the POST returns **202** immediately
  and a real worker + Redis runs it off-request.
- **No action required** for LG-02a-2.

✅ Net: every browser-observable LG-02a-2 surface works (import form + flow,
re-seed, lock, Play All render + request, sync engine advancement). The only
gap is async completion, blocked solely by the local no-Redis smoke environment.

---

## LG-02b — Best-of-N series bracket nodes (2026-06-03)

Branch `lg-02b-series-nodes`. Black-box pass over the new Series surfaces:
the create-form `series_length` select, per-node Series score, one-Match-per-step
play, clinch + advancement, and champion crowning.

| Area | Result |
|------|--------|
| `/tournaments/` list, `/tournaments/create/` | 🟢 200, console clean |
| **Series length** select `#tournament-create-series-length` (Best of 1/3/5, default Bo1) | 🟢 renders + persists (created a Bo3) |
| Per-node Series score `#tournament-node-series-score-{r}-{p}` | 🟢 renders `0–0`, updates per Match |
| **One Match per step** (`Play Next Match`) | 🟢 node `0–0 → 0–1 → 1–1 → 2–1`, "Game 1/2/3" links accrue, no advance until clinch |
| **Clinch + advancement** | 🟢 at `2–1` → "Winner: …" + winner fills the Round 2 slot |
| **Champion** | 🟢 state "Completed" + `#tournament-champion-banner` "Champion: Zenith Zealots #14"; final node `1–2` fed by both R1 winners |
| Bracket-tier headers read "Bracket Round N" | 🟢 (the prior LG-02a "Round N" nit is already fixed) |
| Console errors / non-2xx network | 🟢 none on any tournament page |

### 🔵 Async Play All — environment-only stall (NOT an LG-02b regression)
`Play All` (`#tournament-play-all-form`) renders, shows the instant "Starting…"
feedback, and disables the button (LG-02a-2 inline JS works), but the
`POST /tournaments/<id>/play-all/` hangs because the dev server has no Redis
broker / Celery worker running. The async path and its JS are **unchanged by
LG-02b**, and `matches/tests/test_tournament_tasks.py::TestPlayTournamentTaskSeries`
already proves `play_tournament_task` crowns a Bo3 champion under
`CELERY_TASK_ALWAYS_EAGER`. To exercise Play All locally: `LF_CELERY_EAGER=1`
(dev) or Redis + `celery -A laserforce_simulator worker` (prod). No action for
LG-02b.

✅ Net: every browser-observable LG-02b surface works — the full Bo3 lifecycle
(create with series length → lock → per-Match play → per-node Series score →
clinch → advancement → champion) verified end-to-end, console + network clean.

### Teardown — LG-02b
Test data is in the **gitignored dev `db.sqlite3`** (disposable, ADR-0004):
Tournament 4 "ChromeTest Bo3" + its 4 generated teams (Hyperion/Ember/Zenith/
Aurora "#14") + Matches 59–67 + BracketNodes/SeriesMatch rows. Not part of the
PR. (The generated teams are not `ChromeTest`-prefixed, so the prefix-based
teardown script does not target them; left as disposable dev-DB data.)

### 🟢 UPDATE — Play All confirmed working end-to-end (eager mode)
Re-ran with `LF_CELERY_EAGER=1` (settings then force `memory://` broker +
`cache+memory://` result backend + `task_store_eager_result=True` — no Redis
needed): created a 4-team tournament, locked, clicked **Play All** → POST 202 →
inline JS polled `play-status` → `complete` → reload → **State: Completed,
Champion crowned**, all 3 matches resolved with correct stage advancement, zero
console errors. The earlier hang was the eager env var not reaching the detached
server (it ran non-eager → tried Redis). **The `<!DOCTYPE` JSON error a user sees
is the Celery broker being unreachable** (`.delay()` → 500 HTML → JS `r.json()`
fails) — identical to the shipped LG-01d Play Two Months / Until End buttons
(`league_views.py:1569/1588`, no try/except on `.delay()`). Fix: run with
`LF_CELERY_EAGER=1` (dev) or Redis + a Celery worker (prod), per
ADR-0013 / CLAUDE.md "Async execution (Celery)". Not a code defect in LG-02a-2.
