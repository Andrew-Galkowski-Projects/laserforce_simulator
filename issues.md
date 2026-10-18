# Web testing — ANG-01d-2 (League play loop on a shared service + Active-play-run guards)

Date: 2026-10-16
Branch: `ang-01d-2`
Scope: scoped pass over the HTML play loop only, server on port **8001**. One fresh Account
(`chrometest-ang01d2@example.com`). League 8 `ChromeTest ANG01d2 League` (4-Team Quick, Seasons 9-12)
and League 9 `ChromeTest ANG01d2 MN League` (8-Team Member Nights, Season 13). Team / match / batch /
auth / isolation flows were not run.

Celery: `settings.py:205` makes `CELERY_TASK_ALWAYS_EAGER` true only under pytest or with
`LF_CELERY_EAGER=1`. No Redis and no worker were running. The first half of the pass used the default
dev server (not eager, no broker), so the async paths failed (see M-1 / E-1). The server was then
restarted with `LF_CELERY_EAGER=1` and every async control was exercised for real.

Severity legend: 🔴 High · 🟠 Medium · 🟡 Low · ℹ️ Note

## Summary — ANG-01d-2

| ID | Sev | Area | One-liner |
|----|-----|------|-----------|
| L-4 | 🔴 | Member night Live replay | The "Single Members Game (Live)" watch page renders no playback data and throws a JS TypeError (pre-existing, not a regression) |
| L-5 | 🟠 | Active play run guard | A stale `active_play_job_id` blocks every sync play control, and Cancel cannot clear it, so nothing in the UI recovers the Season |
| ~~L-6~~ | ~~🟡~~ | ~~Async play endpoints~~ | ~~With the broker down, Two Months / Until End / Playoffs / One Week (Live) all return a 500 HTML page (pre-existing)~~ _(fixed (browser gate skipped))_ |
| L-7 | 🟡 | One Week (Live), broker down | The manager's game commits before the enqueue 500s, leaving the week half-played and the Live entry hidden (pre-existing) |
| L-8 | 🟡 | Play poll JS | The 500 ms `setInterval` keeps firing while earlier polls are still pending, so slow or failing polls pile up |
| L-9 | ℹ️ | Error re-renders | A sync play error re-renders the dashboard at the POST URL (for example `/seasons/13/member-night/setup/`) |
| L-10 | 🟡 | Delete League | Member-night drawn Teams (`MN <site> G<n> A/B`) survive the League teardown as orphans |

**Overall:** the ANG-01d-2 work behaves as specified. Re-POSTing Start Season on an active Season
returns 302 and does not re-snapshot (verified with a canary: a Team removed from the Season M2M stayed
in `starting_team_ids_json`). Re-POSTing it on a completed Season returns 302 and does not reopen the
Season. The Active-play-run guard works on every entry point: sync controls redirect with the
"in progress" banner, the async ones return 409 `play_in_progress`, and Cancel returns
`{"cancelled": true}` only while a run is live. With eager Celery, One Week (Live) (RR and playoff),
Two Months, Until End, Through Playoffs and member-night Play All all completed and cleared
`active_play_job_id`. The topnav Play dropdown showed the right controls in every phase. No console
errors anywhere except the L-4 page.

---

## Play loop (4-Team Quick, Seasons 9-12)

### ✅ Start Season + idempotent re-POST — works
Draft dropdown = `topbar-play-start-season` only. Start Season → 302 → `active`, snapshot `[44,45,46,47]`.
Removed Team 47 from `season.teams` (canary), re-POSTed `/seasons/9/start-season/` → 302 (opaqueredirect),
snapshot still `[44,45,46,47]`, no play_error. Restored the M2M. Completed Season 10 re-POST → 302, state
stays `completed`, champion unchanged.

### ✅ Topnav Play dropdown per phase — works
- Draft: Start Season.
- Active RR: One Week / Two Months / Until Playoffs / One Week (Live).
- Live run present: `topbar-play-stop` (→ `play-cancel/`), dropdown hidden, Live entry hidden.
- Playoff cursor: Play Single Round / Play Two Months / Through Playoffs / Play Single Round (Live).
- Member-night cursor: setup form (All Sites + 10 per-Site boxes); after setup, Single / Live / Play All.
- Completed: Start Next Season (→ owner evaluation, career mode).

### ✅ Play One Week (sync) — works
Two clicks → 302 → `Rounds played: 2 / 12`, then `4 / 12`; no console errors; every request 2xx/3xx.

### ✅ One Week (Live), RR and playoff (eager) — works
RR: 302 → `/seasons/9/play-week-live/watch/`, 1314 events, scoreboard + timeline render, step/play
advance the clock (00:00 → 00:13 → 00:14) and the score updates. The background run played the rest of
the matchday (8 rounds total) and `active_play_job_id` was cleared. Playoff (Season 10): the watch page
shows the round toggle with `events-data-1` / `-2`, Round 2 replays, and the other semifinal resolved
in the background.

### ✅ Async controls (eager) — work
Season 9 Through Playoffs → 202 → one `play-status` 200 → reload → COMPLETED. Season 11 Two Months from
matchday 0 → 202 → COMPLETED (6 RR matchdays + 2 bracket stages = budget 8, as Part2c-3f specifies).
Season 12 Until End → 202 → `play-status` reports `complete 2/2` (stage counts) with live-panel
fragments.

### ✅ Active-play-run guard (stale job simulated in the DB) — works
Set `active_play_job_id='chrometest-fake-job-0001'` on Season 9:
- Sync Play One Week → 302 back to the dashboard with the banner "The Season already has a play run in
  progress."; rounds stayed at 5.
- `play-two-months` / `play-until-end` → 409 `{"code":"play_in_progress","job_id":...}`.
- `play-week-live` → 302 with the same banner; nothing committed.
- `play-cancel` → 200 `{"cancelled": true}` and `play_cancel_requested=True`, but the job id stays set
  (see L-5). I cleared it by hand to continue.

### ✅ Playoffs by Single Round (sync) — works
Season 10: after the Live semifinal, one Play Single Round played the final → COMPLETED, champion 44.
Single Round, Play One Week, Two Months and Live on a completed Season → 400 "Season must be active to
play"; `play-playoffs` → 409; GET `play-week` → 405.

### 🟠 L-5 — A stale Active play run locks the Season and the UI cannot release it
ANG-01d-2 now makes the sync controls check `active_play_job_id`
(`matches/league_play_services.py:193-201`, `_require_no_live_run`). When a run's task never clears the
column, every play control is blocked: the worker was killed, or the broker accepted the message but no
worker is running. The only UI action is Stop/Cancel, and that just sets the flag:

```python
updated = Season.objects.filter(
    pk=season.pk, active_play_job_id__isnull=False
).update(play_cancel_requested=True)
```
(`league_play_services.py:111-113`). Only the task's own `finally` ever clears the id
(`matches/tasks.py:526-527`). Repro: set the id to any string, then try One Week (banner), Two Months
(409) and Cancel (`cancelled: true`, still locked). Before this branch, the sync controls ignored the
column. Fix idea: have Cancel (or the guard) release the column when `AsyncResult(job_id)` is
terminal, or when no worker acknowledged it within N seconds. At minimum add an admin/UI "force
release" action.

### ~~🟡 L-6 — The async endpoints 500 when the broker is unreachable (pre-existing)~~ _(fixed (browser gate skipped))_
On the default dev server (no Redis), Two Months / Until End / Through Playoffs / Play All and the
One Week (Live) enqueue raise from `enqueue_play_run` → `task.apply_async(...)`
(`league_play_services.py:98-105`). The function releases the job id and re-raises, and no view catches
it. Observed: `RuntimeError: Retry limit exceeded while trying to reconnect to the Celery result store
backend` → Django 500 page, and the topnav JS then fails to parse JSON. `tournament_views.py:881-884`
already handles this case with `retry=False` and a 503 JSON response. `main` behaved the same (it
called `.delay()` directly), so this is not a regression. The UX is still poor.

### 🟡 L-7 — One Week (Live) commits the manager's game before the enqueue fails (pre-existing)
`play_live` commits the manager's Round first, then enqueues (`league_play_services.py:788-797`). With
the broker down: 500, 5 rounds in the DB (matchday 3 half-played), no watch page. The Live entry then
disappears until One Week finishes the matchday, because `_resolve_live_cursor` finds no manager game
on the half-played matchday. Recoverable, but confusing.

### 🟡 L-8 — Play-status polls pile up
`templates/_partials/topnav_play_script.html:108` polls with `setInterval(…, 500)` and no in-flight
guard. A failing job (500s) or slow responses produce a stack of `[pending]` requests (8+ seen within
seconds). Fix idea: chain `setTimeout` after each response.

### ℹ️ L-9 — Error re-render URL
`_render_season_dashboard_error` returns the dashboard with status 400 at the POST URL, so the address
bar shows `/seasons/13/member-night/setup/` and a reload re-POSTs. Unchanged from before.

---

## Member night (8-Team Member Nights, Season 13)

Member nights are reachable through the template `8_team_member_nights`
(`"round_robin,member_night,tournament"`). `4_team_quick` is `"round_robin,tournament"`
(`matches/league_templates.py`), so it has no member-night phase. Until End (eager) played all 56 RR
rounds and stopped at the member-night barrier.

### ✅ Setup validation + valid setup — works
Empty selection (UI form with every box unchecked, and a fetch) → 400 banner "Pick at least one Site
for the member night." A bogus Site → 400 "Select a valid choice. Nowhere Site is not one of the
available choices." Detroit + San Marcos → 302, 13 unplayed shells (`MN Detroit …` / `MN San Marcos …`),
and the dropdown switches to Single / Live / Play All.

### ✅ Play single + Play All (eager) — works
Single → 302, 1 game completed. Play All → 202 → polls → 13/13 complete, cursor moves to the tournament
phase (bracket built), job id cleared. Afterwards setup / single / Play All → 400 / 400 / 409
("No member night to set up." / "No member-night game to play." / "No member night to play.").

### 🔴 L-4 — The member-night Live watch page is broken
Repro: on a member-night cursor with unplayed games, click **Single Members Game (Live)**. You land on
`/seasons/13/play-week-live/watch/` (200), but there is no scoreboard data, no Round toggle and no
`json_script` blocks, and the console shows `Uncaught TypeError: Cannot read properties of null
(reading 'textContent')`. The game itself did commit (2 games complete). Cause: the session handoff
stores `kind="member_night"` (`league_play_services.py:941`, and `main` did the same at
`league_views.py:1977`). `templates/seasons/play_week_live.html` only branches on `"rr"` and
`"playoff"`:

```django
{% if cursor_kind == "rr" %}            {# line 35 #}
...
{% elif cursor_kind == "playoff" %}     {# line 38 — member_night falls through, nothing emitted #}
```
The JS `else` branch (line 125ff) then reads `getElementById('events-data-1').textContent` on a
missing element. The bug predates this branch (LG-07a); no test covers rendering the watch page with
`kind="member_night"`. Fix: treat `member_night` like `playoff` (two Rounds) at lines 38 and 45, or key
off `preview_round_2`.

---

## Cleanup observation

### 🟡 L-10 — Delete League leaves the member-night drawn Teams behind
After deleting League 9 from its dashboard (the summary said "Teams 9"), 26 `is_draw_team=True` Teams
named `MN Detroit G1 A` … `MN San Marcos G… B` were still in the DB, with no Matches left referencing
them. `_league_teardown_targets` (`matches/league_services.py:1941-1966`) collects only enrolled Teams,
`current_team`, `free_agent_pool` and tournament drawn Teams. Member-night Teams are never enrolled in
the Season, so they are never candidates. Players are borrowed, so no Player rows leak. This is the
same family as the bracket-orphan note further down (CONF-era), but a different cause. Fix idea:
before the Match delete, also collect `Team.objects.filter(is_draw_team=True)` among the
`team_red` / `team_blue` of the League's member-night Matches.

## Environment

### ℹ️ E-1 — Dev server is not eager
Without Redis + a worker, or `LF_CELERY_EAGER=1`, every async play control 500s (L-6). Start the
server with `LF_CELERY_EAGER=1` to test the async play loop locally.

---

# Web testing — ANG-01d-1 (League lifecycle + reads API, league_services extraction)

Date: 2026-10-16
Branch: `ang-01d-1-league-lifecycle-api` (uncommitted working tree)
Scope: scoped pass, server on port **8001**. One fresh Account (`chrometest-ang01d1@example.com`).
League 7 `ChromeTest ANG01d1 League` created via `/leagues/create/advanced/` (8 teams, 2 conferences,
single round-robin phase, 3-zone fallback). Season 7 played to completion with sync One Week POSTs
only, then Season 8 rolled via the owner-evaluation CTA. League deleted via the dashboard delete link
afterwards (summary: 2 Seasons / 13 Matches / 9 Teams / 194 Players). Account left in place.

Severity legend: 🔴 High · 🟠 Medium · 🟡 Low · ℹ️ Note

## Summary — ANG-01d-1

| ID | Sev | Area | One-liner |
|----|-----|------|-----------|
| L-1 | 🟠 | League History (HTML + new API) | Multi-Conference RR-final Season shows a cross-Conference "Champion" although `champion_team` is NULL |
| L-2 | 🟡 | Season standings | `season-standings-th-*` ids duplicated once per Conference table |
| L-3 | ℹ️ | League History | Draft in-progress row shows a Runner-Up / 1st-3rd from all-zero standings |

**Overall:** every flow in scope works. Advanced create with 2 conferences lands on Manage
Conferences pre-filled 4/4; rename + move + save persists (DB 3/5); an invalid save (1-team
conference) shows "Each conference needs at least 2 teams." and writes nothing. The new
`reassign_team` active-Season guard holds in all three states tried. All 17 API GETs return the
`{data, meta}` envelope. No console errors or non-2xx requests on any page. All three findings are
**pre-existing** (code moved verbatim / template untouched on this branch); L-1 is newly *exposed*
through the ANG-01d-1 history API.

---

## Create League (advanced) + Manage Conferences

### ✅ Create with Number of conferences = 2 — works
POST `/leagues/create/advanced/` (8 teams, Conferences 2) redirected to `/seasons/7/conferences/`
with two conferences pre-filled 4/4 (alternating auto-split).

### ✅ Rename + move + save — works
Renamed Conference 1 -> "ChromeTest East", moved Aurora Aces to Conference 2, Save -> "Conferences
saved."; DB: East = 3 teams, Conference 2 = 5 teams. Renaming live-updates every team `<select>`'s
option text (the JS rebuild).

### ✅ Invalid save rejected, nothing written — works
Moved two East teams out (East = 1 team) and renamed Conference 2 to "ChromeTest ShouldNotSave";
POST -> 200 re-render with "Each conference needs at least 2 teams.", submitted values preserved in
the form, DB unchanged (names + memberships identical to the prior save). The shared
`ConferencePartitionForm` path behaves exactly like the old inline validation.

### ✅ Map-rotation composer — visible, works
"+ Add map" on the advanced form adds a `Rotation map` select + Remove (empty — the DB has no
confirmed-config maps). Removed before submit. The per-conference "+ Add map" composers render on
Manage Conferences.

## Dashboards / reads

### ✅ League + Season dashboards — work
Draft, active and completed states render; completed shows "All fixtures played", Rounds 26 / 26,
manager-Conference top-3 snippet, three leaders snippets, `topbar-play-owner-evaluation` entry.

### ✅ Season standings — work
Two per-Conference tables (3 + 5 rows). `?sort=wins&dir=asc` then the W header (now `dir=desc`,
glyph ↓) re-sorted both tables correctly with `rank` frozen; `?sort=team&dir=asc` alphabetical.

### ✅ Schedule / Team schedule / History / Awards — work
`/seasons/7/schedule/` 10 matchdays; `/leagues/7/team_schedule/35/` (sidebar Team > Schedule)
8 completed rows (4 opponents x 2 rounds); History 200 incl. `?per_page=25` and the forgiving
`?per_page=foo&page=99`; only one page of Seasons so no pagination nav (expected). Awards: all 12
`season-awards-*` ids, Finals MVP "—" (no playoff phase).

## Play loop + rollover

### ✅ Full Season via sync controls — works
`topbar-play-start-season` -> ACTIVE; `topbar-play-one-week` x10 -> 26 / 26 and state `completed`
(an 11th click correctly re-renders the dashboard with a 400 play error). Champion NULL (2-Conference
RR-final — CONF-01 rule).

### ✅ Owner evaluation + Start Next Season — works
Verdict RETAINED (wins -0.125, playoffs 0, money 0.0); the `owner-evaluation-cta-start-next` POST to
`/leagues/7/next-season/` redirected to `/seasons/8/` (Season 2, draft, 8 teams, one RR phase,
0 conferences — the rollover does not carry conferences, as documented). Manager not fired, so the
new-team picker path was not reachable.

### ✅ `reassign_team` active-Season guard (new on this branch) — works
POST `/leagues/7/reassign-team/` (fetch + csrftoken cookie) returned a 302 to the in-progress
Season's dashboard and wrote nothing in all three cases:
- Season 7 **draft** (team 36) -> `/seasons/7/`, still 1 Season, `current_team` 35.
- Season 7 **active** (team 36) -> `/seasons/7/`, still 1 Season.
- Season 8 **draft with Season 7 completed** and team 36 in the eligible worst-5 set (the exact
  pre-fix bug shape) -> `/seasons/8/`, still exactly 2 Seasons, `current_team` 35.

## Browsable API (GET)

### ✅ All GETs enveloped — works
200 + `{data, meta}`: `/api/leagues/` (paginated meta), `/templates/` (5), `/leagues/7/`,
`/dashboard/`, `/history/` (paginated), `/teams/35/schedule/`, `/new-team-candidates/`,
`/api/seasons/` (paginated), `/seasons/7/`, `/standings/` (2 conference groups; `?sort=wins&dir=desc`
honoured), `/conferences/` (`editable: false` once active), `/owner-evaluation/`, `/schedule/`,
`/awards/`, `/dashboard/`. `?sort=bogus` -> 400 `{"errors":[{"code":"invalid_choice", ...,
"field":"sort"}], "meta":{}}`. The browsable HTML renderer loads with no console errors.

---

## League History

### 🟠 L-1 — Multi-Conference RR-final Season shows a cross-Conference champion
**Repro:** 2-Conference League, single round-robin phase (no tournament), play to completion.
`Season.champion_team` is correctly NULL (CONF-01: no cross-Conference champion without Worlds), and
`/api/seasons/7/` reports `"champion_team": null` — but `/leagues/7/history/` renders
**Champion = Rogue Recon #2**, Runner-Up Aurora Aces #3, 1st-3rd Rogue Recon / Aurora Aces / Lumen
Legion, and the new `/api/leagues/7/history/` returns the same `"champion": {"id": 39, ...}`. The
"1st place" ranks are whole-Season standings across Conferences whose schedules are disjoint, the
comparison CONF-01 explicitly calls meaningless.
**Expected:** Champion "—" (and probably per-Conference or "—" placings) for a `>= 2`-Conference
Season with a NULL champion.
**Root cause:** `laserforce_simulator/matches/league_services.py:1620-1622` (`build_history_row`)
falls back to whole-Season standings rank 1:
```python
champion = season.champion_team
if champion is None and standings:
    champion = teams_by_id.get(standings[0].team_id)
```
`standings` (line 1610) is `compute_standings` over every completed Match in the Season with no
Conference scoping; runner-up / top-3 (1612-1615, 1624-1625) use the same list. Rendered by
`templates/leagues/history.html:60`.
**Regression?** No — the fallback is identical on `main` (`matches/league_views.py:3013`, the old
`_build_history_row`), moved verbatim. Newly exposed via the ANG-01d-1 history API, so API
consumers inherit it.
**Fix idea:** skip the standings fallback when `season.conferences.count() >= 2` (mirror
`_stamp_champion_for_final_phase`), and either blank the placings or compute them per Conference.

## Season standings

### 🟡 L-2 — Duplicate sort-header DOM ids across Conference tables
On a 2-Conference Season every `season-standings-th-<key>` id (17 of them) appears twice, once per
Conference table. Invalid HTML and ambiguous for `getElementById` / tests.
**Root cause:** `laserforce_simulator/templates/seasons/standings.html:45`
`<th id="season-standings-th-{{ key }}">` inside the per-group loop.
**Regression?** No — template untouched on this branch (last change CONF-05).
**Fix idea:** suffix with the conference id when grouped (`season-standings-th-{{ key }}-{{ group.conference.id }}`)
or keep the plain id on the first table only.

## League History

### ℹ️ L-3 — Draft in-progress row shows placings from all-zero standings
After Start Next Season, the in-progress Season 2 row (0 matches played) shows Runner-Up "Blackout
Bobcats #2" and 1st-3rd "Aurora Aces / Blackout Bobcats / Ember Enforcers" — alphabetical tiebreak of
a zero-filled table. Same `build_history_row` (`league_services.py:1610-1625`). Pre-existing; cosmetic.

## Environment

### ℹ️ E-1 — Registration password similarity
The first registration attempt with a password containing "ChromeTest" was rejected ("too similar
to the email address") — correct Django validation, not a bug; noted for future test runs.

# Web testing — ANG-01c (Map editor API)

Date: 2026-10-15
Branch: `ang-01c-map-editor-api`
Scope: scoped pass, server on port **8001**. One fresh Account; map `ChromeTest Arena` (map 1, a
400x400 canvas-drawn PNG with a border + centre bar) uploaded via the API; guard League 6 / Season 6
created via the shell. All test data deleted afterwards (map via the API, League + Account via shell).

## Summary — ANG-01c
| Area | Result |
|---|---|
| `POST /api/maps/` multipart -> 201, `img_width`/`img_height` 400x400, `has_confirmed_config` false | OK |
| `GET /api/maps/1/zones/?zone_size=50` -> 8x8 grid; `PUT zone-config/` (zones + red/blue bases) -> 200 with the saved-state key set | OK |
| `POST sight-lines/compute/` -> 28 keys; `ranked-cells` 28, `strong-spots` 7 (top quarter), `spawn-cells` 16 red auto-computed, `sight-lines/cell/` 12 visible | OK |
| `PATCH sight-lines/` merge 200; `PUT strong-spots/` round-trips `[[1,1]]` | OK |
| Strict errors: elevation `[[11]]` -> 400 `invalid` "Elevation value out of range [0.0, 10.0]: 11"; `zone_size=5` -> 400 `min_value` | OK |
| `heatmap/` -> `{cell_occupancy: {}, zone_size, rows: 8, cols: 8, round_count: 0}`; `processed-image/` 200 `image/png` (not enveloped) | OK |
| HTML `/maps/1/editor/` loads; its AJAX (`zones`, `processed-image`, `spawn-cells`) 200; **Save Configuration** button -> `POST /maps/1/save/` 200 | OK |
| HTML compute + batched save (replace 10 keys, then merge 18) -> `{"status": "ok"}` x2, stored 28 keys; malformed zones -> 400 `{"status": "error", "message": "zones must be ..."}` | OK |
| Delete guard: draft Season pool references the map -> API `DELETE` 409 `map_in_use`; HTML delete redirects to `/maps/` with "Cannot delete map “ChromeTest Arena”: it is used by a Season that is not completed."; map survives | OK |
| After the Season is completed -> API `DELETE` 204, map 404, image file 404 | OK |
| Console: no errors / warnings / issues on the editor or map list | OK |

No findings.

# Web testing — ANG-01b (Match / Round actions + analytics API)

Date: 2026-10-15
Branch: `ang-01b-match-round-analytics-api`
Scope: scoped pass, server on port **8001** with `LF_CELERY_EAGER=1` (batch + save run inline).
Account A (`chrometest-a-ang01b@`): Teams 30/31/32 (`/api/teams/generate/`), Match 28 (HTML create),
Match 29 + standalone Round 44 (API creates), batch-saved Rounds 40/41 (HTML) and 45 (API).
Account B (`chrometest-b-ang01b@`, isolated context): Teams 33/34, Match 27 (Rounds 36/37), Player 467.

## Summary — ANG-01b
| Area | Result |
|---|---|
| HTML `/matches/create/` picker lists only A's teams (B's 33/34 absent); submit redirects to Match 28 | OK |
| Forged HTML POST to `/matches/single-round/create/` with B's team 33 -> 200 form error "valid choice", B's team name not echoed | OK |
| HTML batch (10 runs) -> status `complete` -> save avg n=2 -> Rounds 40/41 stamped `manager` = A; B gets 404 on `/api/rounds/40/` | OK |
| `POST /api/matches/` 201 (Match 29, manager = A); `POST /api/rounds/` 201 with 12 nested `player_states` (Round 44 manager = A in DB) | OK |
| `GET /api/rounds/38/events/` on a `scores`-tier Match round -> 1296 events (fidelity fix: was empty before ANG-01b) | OK |
| `missile-log/` + `heatmap/` 200 with contract key sets; `report/` 200 `application/pdf`, `attachment; filename="round-38-nova-wolves-vs-astral-bandits.pdf"`, body `%PDF`, not enveloped | OK |
| `/api/rounds/compare/` 200; same id -> 400 `invalid` / `field: null` | OK |
| `/api/teams/head-to-head/` 200; `provenance=bogus` -> 400 `invalid_choice` / `field: "provenance"`; same team -> 400; B's team -> 404 `not_found` | OK |
| `/api/players/head-to-head/`, `/api/players/449/career/`, `/api/players/benchmarks/?display=median` 200; B's Player 467 career -> 404 | OK |
| API batch (n=6) -> `/api/simulate-batch/<job>/save/` outlier n=1 -> poll `/api/simulate-batch/save/<id>/` `complete`, `round_ids: [45]` | OK |
| Unknown job id save -> 409 `job_not_complete`; foreign Team 33 on batch POST and on save -> 400 "Team 33 does not exist." | OK |
| HTML `/matches/h2h/`, `/matches/h2h/player/`, `/matches/compare/` pickers exclude B's rows (compare also lists Unmanaged sandbox Rounds 15-20 — correct); results render; missile log / heatmap / export / career / benchmarks HTML 200 | OK |
| Console: no errors / warnings / issues | OK |

No findings in scope. Note (not a bug): `GameRoundSerializer` does not expose `manager` (pre-existing explicit field list), so a created Round's ownership is only visible via the DB / cross-Account 404.

## Deferred (logged, out of ANG-01b scope — see PLAN.md Deferred Items)
- **ANG01b-D1 · H2H / career / benchmark corpora not ownership-filtered.** `matches/services.py`
  `_h2h_matches_qs` / `_h2h_rounds_qs` / `_player_h2h_rounds_qs`, the career round set and the HX-02
  benchmark population read every row. A foreign Account's Match between two Unmanaged Teams can
  appear in a head-to-head `detail_list` (its round link then 404s). Fix = thread `user` into the
  builders and filter by `owned_match_q` / `owned_game_round_q` (changes HTML output).
- **ANG01b-D2 · HTML `save_batch_games` trusts session team ids.** `matches/views.py::save_batch_games`
  reads `team_red_id` / `team_blue_id` from `request.session["batch_seeds"]`, which the status poll
  copies from its query params without an ownership check. Could reuse `services.resolve_batch_teams`.

# Web testing — ANG-01a (REST API foundation + Team/Player writes)

Date: 2026-10-14
Branch: `ang-01a-api-foundation`
Scope: scoped pass. Two fresh Accounts in isolated contexts (A, B) + an anonymous context.
B: Team 26 `ChromeTest Shared` (commander Player 416 `SecretPlayer`, created via the API).
A: Team 27 `ChromeTest Shared` (roster import), Teams 28/29 (`/teams/generate/`, 7 players each),
12 free agents on the global pool. Server on port **8001**.

## Summary — ANG-01a
| Area | Result |
|---|---|
| API writes in-browser (B): `POST /api/teams/` 201, `POST /api/teams/26/players/` with only `name` 201 (stats 50), `PUT /api/teams/26/slots/` 200 | OK |
| `DELETE /api/teams/26/` -> 405 `method_not_allowed` envelope; `POST /api/teams/` `{}` -> 400 `required` / `field: "name"` | OK |
| Cross-Account (A on B's rows): `GET /api/teams/26/`, `GET` + `DELETE /api/players/416/` -> 404 `not_found` envelope | OK |
| **Roster-import leak fix**: A imports a CSV naming `ChromeTest Shared` + a commander -> new Team 27 for A (created list, no appended list), page never contains `SecretPlayer`; B's Team 26 still has only SecretPlayer in `slot_commander` | OK |
| `/api/teams/import/template/` body byte-equal to `/teams/import/template.csv` | OK |
| `/teams/generate/` teams mode -> Teams 28/29, stamped to A (A 200, B 404 on `/api/teams/28/`); free-agent mode -> 12; cross-field error copy renders | OK |
| `/teams/27/add-player/` prefill (random profile, stats 50) | OK |
| `/players/?sort=overall_rating&dir=desc` order == `/api/players/?ordering=-overall_rating` order | OK |
| Browsable API renders the envelope; `meta` page/page_size/count/total_pages correct | OK |
| `/api/matches/?match_type=bogus` -> 400 `invalid_choice` / `field: "match_type"`; `/api/teams/?page=999` -> 404 `Invalid page.` | OK |
| `/api/maps/` 200 (empty — no maps in the dev DB, so field shape not observed in-browser; covered by `core/test_maps_api.py`) | OK |
| Anonymous `GET /api/teams/` -> 403 `not_authenticated` envelope, no redirect | OK |
| Console: only the deliberate 400/404 and one DRF browsable-page autocomplete notice; all other requests 2xx | OK |

No findings.

# Web testing — UX-02 (Self-insert: Account ↔ Player link)

Date: 2026-10-14
Branch: `ux-02-self-insert`
Scope: scoped pass. Fresh Account; sandbox Team `ChromeTest UX02` (Team 20, Player 282);
League 5 `ChromeTest UX02 League` (4-Team Quick), Season 5, one week played (Rounds 34/35).
Server on port **8001**.

## Summary — UX-02
| Area | Result |
|---|---|
| Owned, unlinked Player detail shows **This is me** | OK |
| Link POST -> 302 back to page, success flash, **You** badge in header, **Unlink** shown | OK |
| Badge on `/teams/20/` roster, `/players/`, `/players/282/stats/` (1 each) | OK |
| Account menu `#account-my-player-link` -> `/teams/20/player/282/` | OK |
| Unlink -> success flash, badge / nav link gone on all four pages, **This is me** back | OK |
| League player page (`/leagues/5/players/<id>/`) link button + badge + Unlink | OK |
| Linking a second Player replaces the first | OK |
| Badge on League roster, league leaders (4 boards), player stats, statistical feats, Season + League dashboards (leaders snippet), Season awards | OK |
| Round box score `/matches/game-round/34/` — 2 badges (scoreboard + resources list); Round 35 (other match) — 0 | OK |
| "My player" for a League Player points at the sandbox `player_detail` (`/teams/21/player/285/`) | OK — per contract |
| `GET /accounts/self-insert/unlink/` -> **405** | OK |
| Console: only the deliberate 405; all other requests 2xx/3xx | OK |

Not observed in-browser (covered by tests instead): `player_ratings` (paginated — the linked Player
was not on page 1), `team_finances` (the 4-Team Quick League has finances off).

### ✅ ~~U3-1 — The "You" badge scales with its heading~~ — FIXED in-slice (2026-10-14)
On the sandbox and League player detail pages the badge sat inside the `<h3>` / `<h1>` name
heading, and Bootstrap's `.badge` is `em`-sized, so it rendered heading-sized. Fixed by wrapping
the tag at those two heading sites only in `<span class="fs-6 align-middle">` (the tag's
`YOU_BADGE_HTML` output is unchanged). Inline in table rows it was already the expected small pill.

### 🟡 U3-2 — [PRE-EXISTING, out of scope] Dashboard leaders-card links 404
`templates/_partials/dashboard_leaders_snippet.html:11` links each leader to
`/players/{{ row.player_id }}/career-stats/`, but the route is `/players/<id>/stats/`
(`teams/player_urls.py`, name `player_career_stats`), so every name on the Season / League
dashboard leaders cards 404s. Predates UX-02 (the href was only re-wrapped, not changed). Fix:
`{% url 'player_career_stats' row.player_id %}`.


---
# Web testing — UX-01 follow-up: register / login / League / full Season

Date: 2026-09-04
Branch: `ux-01-user-accounts` (PR #166)
Scope: a scoped pass, not the full sweep — register a fresh Account, sign out and back
in, create a League from a template, and play a Season end to end (regular season +
playoffs) under the UX-01 login gate. Server on port **8001**. Account
`seasontest@example.com` (id 5); League 3 `ChromeTest Season League` (4-Team Quick),
Season 3, Tournament 1 `Season 1 Playoffs`.

## Summary
| Area | Result |
|---|---|
| Anonymous `/` -> 302 `/accounts/login/?next=/` | OK |
| Register (email + 2x password) creates the Account, auto-logs-in, lands on `/` | OK |
| Explicit sign-out (POST) -> login page; sign back in -> `/`, nav shows the email | OK |
| League create from the `4-Team Quick` template | OK |
| Stamping — League 3, all 4 generated Teams and the per-League free-agent pool carry `manager_id=5` | OK |
| Season Matches carry `manager IS NULL` (they derive via `season -> league`) | OK — per contract |
| Embedded Tournament 1 inherits `manager=5` from its League (ADR-0038 s2.3) | OK |
| Regular season: 6 Matches / 12 Rounds via **Play One Week** x6 | OK |
| Standings internally consistent (MP 3 each, wins sum to 6 Matches, round wins sum to 12) | OK |
| Playoffs: 3 bracket nodes resolved via **Play Single Round** x3; Season -> `completed` | OK |
| Champion crowned (Ember Enforcers, the #1 seed) | OK |
| Console clean, all requests 2xx on the Season dashboard | OK |
| Cross-Account League detail returns **404** | OK |
| **Landing page listed other Accounts' Leagues** | **DEFECT — fixed, see U2-1** |
| Global `Free Agents` singleton was captured by an Account and now 404s | **DEFECT — see U2-2** |

### ~~U2-1 — Landing page leaked every other Account's active League~~ — FIXED (2026-09-04)
`core/views.py::landing` listed `League.objects.filter(state="active")` with **no ownership
scoping**, so any signed-in Account saw every other Account's active League name and Season
status on the home page. Reproduced live: brand-new Account 5 saw Leagues 1 and 2, owned by
Accounts 1 and 4.

The League **detail** page correctly returned 404, so this was a listing leak, not an access
break — but it still contradicts ADR-0038 ("another Account's row is neither listed nor
readable").

Root cause: the UX-01 seam contract scoped the `core` app out on the grounds that `ArenaMap`
has no ownership axis. `landing` lives in `core/views.py` but renders **League** rows, so it
fell through the gap — `core/views.py` was never touched by the branch.

**FIXED** — `landing` now routes through `owned_queryset(...)`, so an Account sees its own
active Leagues plus every **Unmanaged** one. Four regression tests in
`core/tests.py::LandingInProgressLeagueScopingTests` cover own / Unmanaged / other-Account /
absent-from-HTML. Verified in-browser after the fix: only `ChromeTest Season League` renders.

### U2-2 — The global `Free Agents` singleton can be captured by an Account
`get_free_agents_team()` (`teams/models.py:270`) resolves the global sandbox pool by
`get_or_create(name="Free Agents")`, but **nothing reserves that name at create time**.
`_generate_free_agents` is careful never to stamp the singleton, and `claim_unmanaged`
excludes it — but the ordinary team-create path stamps whatever name is submitted.

Observed in the dev database: Team 3 `Free Agents` carries `manager_id=2`, and
`GET /teams/3/` returns **404** for Account 5. The shared sandbox pool is now unreachable
for every Account except 2.

Worse ordering: if the singleton already exists and a second Team named `Free Agents` is
created, `get_or_create` raises `MultipleObjectsReturned`.

Fix (not applied — out of scope for the UX-01 slice, needs a decision): reserve
`FREE_AGENTS_TEAM_NAME` in team-create validation, or resolve the singleton by a dedicated
flag rather than by name.

### U2-3 — [ENVIRONMENT] The async play paths need a Celery worker
`Play Two Months`, `Play Until Playoffs`, `Play Playoffs` and the member-night `Play All`
are intercepted by `templates/_partials/topnav_play_script.html` and POST to views that call
`play_season_task.delay(...)`, returning `202 {job_id}` for the page to poll.

The browser-testing setup starts only `runserver`, no Celery worker, so those buttons appear
to do nothing: the POST returns 202, the season never advances, and the poll never resolves.
Not a product defect and not UX-01-related. The **sync** paths (`Play One Week`,
`Play Single Round`, `Start Season`) work and are what this pass used.


---
# Web testing — UX-01 (user accounts and Manager ownership)

Date: 2026-09-03
Branch: `ux-01-user-accounts`
Scope: the global `LoginRequiredMiddleware` gate, the new email-first `accounts` app
(login / register / logout / password-change), the top-nav auth control, the `manager`
FK on the five **Ownership roots**, the `get_owned_or_404` traversal, cross-Account
isolation, the DRF `IsAuthenticated` + manager-scoped querysets, the dormant
`League.visibility` control, and the deliberate **non**-ownership of `ArenaMap`.
Server on port **8001**. The local `db.sqlite3` was reset for this pass (the
`AUTH_USER_MODEL` swap breaks existing migration history — see ADR-0038 Consequences);
the old file is preserved at `laserforce_simulator/db.sqlite3.pre-ux01.bak`.
Fixture built live through the UI: Account `chrometest@example.com` (user A) +
`userb@example.com` (user B, isolated browser context) + an anonymous context;
League 1 `ChromeTest League` (4 generated Teams + pool Team), Season 1 started and
one week played (2 Matches, 2 Rounds); ArenaMap 3 `ChromeTest Shared Arena`.

## Summary — UX-01
| Area | Result |
|---|---|
| Anonymous hit on any gated page → 302 to `/accounts/login/?next=<path>`, `next` preserved | ✅ |
| Register (email + password + confirm) creates the Account, auto-logs-in, redirects to landing | ✅ |
| All 5 locked `login-*` ids present; all 6 `register-*`; all 5 `password-change-*`; both `password-change-done-*` | ✅ |
| All 7 locked top-nav `account-*` ids present, correct branch — anonymous sees `account-sign-in-link` / `account-register-link`, and **zero** authenticated-only ids leak to anonymous | ✅ |
| Sign-out is POST-only — `GET /accounts/logout/` → **405**; POST → `/accounts/login/`; a gated page then bounces to login with `next` | ✅ |
| Password change round-trips → `/accounts/password-change/done/`; still gated while anonymous (Django's `LoginRequiredMixin`, which no middleware exemption can lift) | ✅ |
| Stamping — all 4 generated Teams (incl. the 3 AI ones), the free-agent pool Team and the League carry `manager_id`; **zero** unmanaged rows after League create | ✅ |
| Season Matches correctly carry `manager_id = NULL` — they *derive* via `season → league`, exactly as seam contract §6.5 specifies | ✅ |
| **Cross-Account isolation** — user B gets **404 (not 403)** on user A's `/leagues/1/`, `/seasons/1/`, `/teams/1/`, `/matches/1/`, `/matches/game-round/1/` and every league screen | ✅ |
| Derived-row traversal `GameRound → Match → Season → League` blocks user B end-to-end | ✅ |
| List pages render 200 for user B with **zero** trace of user A's data (no `ChromeTest`, no `Ember Enforcers`) | ✅ |
| DRF — user A `/api/teams/` sees 5, user B sees **0**, user B `/api/players/` sees **0** | ✅ |
| DRF anonymous → **403 JSON** (`content-type: application/json`, `{"detail":"Authentication credentials were not provided."}`), **not** an HTML 302 | ✅ |
| `ArenaMap` shared as designed — user B, who did not create map 3, sees it in `/maps/` **and** opens `/maps/3/editor/` (200); `ArenaMap` has no `manager` field | ✅ |
| `league-create-visibility` renders on the Advanced screen, defaults `Closed`, options `Closed`/`Open`, help text states it has no effect yet | ✅ |
| All 24 League sidebar screens (the 15 converted `league_screens` modules) return 200 with no traceback | ✅ |
| Core pages 200: `/teams/ /players/ /matches/ /maps/ /tournaments/ /leagues/ /matches/simulate-batch/` and all four create screens | ✅ |
| Play loop under the gate — `start-season` then `play-week` simulated 2 Matches / 2 Rounds; standings render round-level results | ✅ |
| Console clean (zero errors/warnings) and all requests 2xx on the League dashboard | ✅ |
| Responsive — navbar collapses to a hamburger at 720px with the account control inside; no horizontal overflow at 720px or 1280px | ✅ |

**Overall:** no in-slice defects found. The UX-01 surface behaves exactly as
ADR-0038 and the seam contract specify, including the two decisions most at risk of
being got wrong (404-not-403 on cross-Account access, and `ArenaMap` staying shared).
Four observations are logged below: one low-severity in-slice UX nit (U1-1), one
in-slice doc-drift nit (U1-2), and two pre-existing issues left out of scope
(U1-3, U1-4).

### 🟡 U1-1 — Anonymous top-nav advertises links that only bounce to the login page
On `/accounts/login/` and `/accounts/register/` the anonymous navbar still renders the
full app menu — `Teams`, `Players`, `Matches`, `Batch Sim`, `Create Team`, `Maps`,
`Tournaments` — every one of which now 302s straight back to the login page. It is not
a functional break (the gate does the right thing), but it invites a dead click on the
two pages a brand-new visitor sees first.

Source: `laserforce_simulator/templates/base.html` — the section-item includes render
before the `{% if user.is_authenticated %}` branch in
`templates/_partials/topnav_auth.html`, so only the auth control itself is branched.

Fix (deferred — cosmetic, and outside the approved UX-01 scope): wrap the
sandbox/league section items in `{% if user.is_authenticated %}`, leaving the brand,
Tools, Help and the auth control visible to anonymous visitors.

### ✅ ~~U1-2 — Stale `get_object_or_404` references in eight `league_screens` docstrings~~ — FIXED (2026-09-04)
Eight modules under `laserforce_simulator/matches/league_screens/` still describe their
lookup as `get_object_or_404(League, ...)` in the module docstring, though the call was
converted to `get_owned_or_404` in this slice. Prose only — no behavioural effect. The
seam contract enumerated call sites and required everything else stay byte-identical,
so the Code agent correctly left them. **FIXED** — the seven affected module
docstrings now read `get_owned_or_404(League)`, matching the call each one makes.

### ⚪ U1-3 — [PRE-EXISTING, out of scope] `/tools/` and `/help/` return 404
Neither `laserforce_simulator/core/tools_urls.py` nor `core/help_urls.py` declares a
bare `""` index route — both are dropdown menus whose children are real routes, so the
mount points were never valid URLs. Confirmed unrelated to UX-01: `git diff` shows
neither file was touched on this branch. Only worth fixing if a landing page for either
menu is ever wanted.

### ⚪ U1-4 — [PRE-EXISTING, out of scope] Root `CLAUDE.md` names the wrong homepage view
The Architecture section still reads "The root URL serves the `teams` app as the
homepage", stale since LG-01a moved `/` to `core.views.landing`. The UX-01 Docs pass
corrected only the app count on that line, deliberately leaving the unrelated drift.


---

# Web testing — CONF-06 (per-Conference rotating map pools)

Date: 2026-09-03
Branch: `conf-06-conference-map-pools`
Scope: the new `rotate_by_conference` **Map mode** — the Manage Conferences rotation
composer (`/seasons/<id>/conferences/`), the create-League Advanced screen's fifth map
mode and its three guards, the `start_season` activation guard and snapshot, the
read-only rotation list on a non-draft Season, and the real play-loop map resolution.
Server on port **8010** (port 8000 was held by an unrelated Anaconda process serving a
different app — "Campaign Codex"; not ours, not killed). Fixture: Season **66**
(2 Conferences x 4 Teams, `ChromeTest Conf Flow`) plus Season **80** created live
through the Advanced form (`ChromeTest Conf06 Happy`).

## Summary — CONF-06
| Area | Result |
|---|---|
| All 7 locked `manage-conferences-*` rotation DOM ids present; `conference_name` / `conference_rotation` getlists index-aligned | ✅ |
| `+ Add map` builds a row with the confirmed-map options and serializes the hidden input immediately | ✅ |
| Author order preserved end-to-end — `[4, 1]` (non-ascending) survives Save and reload | ✅ |
| Rotation **rehydrates** into composer rows on page load; no re-picking needed | ✅ |
| Empty-rotation guard renders `Each conference needs at least 1 rotation map.` once, not per row | ✅ |
| Bad-token guard renders both verbatim parse errors; server echoes the RAW submitted string | ✅ |
| `start_season` guard blocks activation (HTTP 400) and the Season stays `draft` | ✅ |
| Activation snapshot `starting_map_rotation_ids_json = [4, 1]` in AUTHOR order, while `starting_team_ids_json` stays sorted ascending — both rules correct in the same loop | ✅ |
| Non-draft page is read-only: no form, no rotation inputs, no `+ Add map`; renders `1. San Marcos Laser Tag / 2. Syracuse Laser Tag` in author order | ✅ |
| **Two Conferences, one matchday, different maps** — matchdays 2/4/6: Conference 1 on San Marcos while Conference 2 is on Syracuse | ✅ |
| `_fixture_map_ids` on the live Season → `[4, 1, 1]` — Conferences deduped (8 teams, 2 contributions), map-id duplicates retained | ✅ |
| Play loop actually consults the resolver — played Rounds persisted `arena_map = San Marcos`, not the 3-zone fallback | ✅ |
| Advanced form offers `rotate_by_conference :: Rotate by Conference` as the 5th mode | ✅ |
| All three create-form guards surface together in one submission, verbatim | ✅ |
| Valid `rotate_by_conference` create redirects straight to Manage Conferences (CONF-05 integration) with 2 pre-split Conferences | ✅ |
| New rotation selects carry `aria-label="Rotation map"` | ✅ |
| Console clean on the composer page; all requests 2xx; no horizontal overflow at 720px or 1280px | ✅ |

**Overall:** the CONF-06 surface is correct on real data. One genuine in-slice defect
was found and **fixed in-slice** (C6-1 — the dashboard map label). Three pre-existing
issues were surfaced and left out of scope (C6-2, C6-3, C6-4), plus one cosmetic nit
(C6-5).

### 🔴 C6-1 — [FIXED in-slice] Dashboard map label reported "3-zone fallback" for a `rotate_by_conference` Season

`_build_map_config_label` had no branch for the new fifth mode, so an active Season
running per-Conference rotations fell through to the "unknown enum value" defensive
return and the dashboard displayed:

    Map: 3-zone fallback (no map)

— actively misreporting the Season's configuration while the Rounds were in fact being
played on the Conference rotations. The seam contract never named this helper, so all
three parallel agents missed it; it was caught only by looking at the rendered
dashboard.

`laserforce_simulator/matches/league_views.py:2289` (the defensive tail):

```python
    # Defensive fallback — an unknown enum value (admin-side raw write)
    # surfaces as the 3-zone label rather than crashing the dashboard.
    return "Map: 3-zone fallback (no map)"
```

**Fixed** by adding the sixth label branch, mirroring the `rotate_by_matchday` one but
per Conference (snapshot for active/completed, live list for draft; Conference order by
`ordinal`, map order AUTHOR order). Now renders:

    Map: Rotating per Conference (2 conferences: Conference 1: San Marcos Laser Tag, Syracuse Laser Tag; Conference 2: Syracuse Laser Tag)

Regression guard added: `TestConf06LeagueDashboardRotateByConferenceLabel` in
`matches/tests/test_league_dashboard.py` (3 tests — draft/live, active/snapshot, and
the no-maps case; the first asserts the 3-zone label is **absent**, so it fails against
the old code).

### 🟡 C6-2 — [PRE-EXISTING, out of scope] `ValidationError` renders as a raw Python list repr

Tripping the new activation guard shows the user:

    ["A Season with map mode 'Rotate by Conference' requires at least 2 conferences, each with at least 1 rotation map."]

— brackets and quotes included. `laserforce_simulator/matches/league_views.py:3189`:

```python
        messages_list = getattr(exc, "messages", None) or [str(exc)]
        joined = " ".join(messages_list)
        season.refresh_from_db()
        if "non-completed" in joined or season.state == "active":
            return redirect("season_dashboard", season_id=season.id)
        return _render_season_dashboard_error(request, season, str(exc))
```

The clean `joined` string is computed on the line above and then **not used** for
display — `str(exc)` is passed instead, and `str()` on a `ValidationError` yields the
list repr. This is LG-01d code affecting **every** `ValidationError` on this view (the
pre-existing `teams.count() < 2` guard renders identically); CONF-06 only made it more
reachable by adding a second guard users will actually hit. One-word fix (`str(exc)` →
`joined`) but out of scope for this slice.

### 🟡 C6-3 — [PRE-EXISTING, out of scope] The map picker offers maps the simulator then rejects

Selecting *Syracuse Laser Tag* for a rotation and playing a week fails the whole
matchday with:

    Map 'Syracuse Laser Tag' has no red base point

`matches.forms._maps_with_confirmed_config()` gates only on a confirmed
`MapZoneConfig`, but the simulator additionally requires `MapBaseConfig` rows. In the
dev DB, map 1 (Syracuse) has **0** base configs while map 4 (San Marcos) has 6 — so one
of the two selectable maps is unsimulable, with nothing in the UI to say so. This
affects `single` / `random_per_round` / `rotate_by_matchday` identically; CONF-06
reuses the same picker unchanged. Either the picker should also require base configs,
or the map editor should mark a map incomplete until it has them.

### 🟡 C6-4 — [PRE-EXISTING, out of scope] 10 unlabeled form fields on Manage Conferences

Chrome reports `No label associated with a form field`. All 10 are **CONF-05**
elements — the 2 `manage-conferences-name-{i}` conference-name inputs and the 8
`manage-conferences-team-{id}` assignment selects. None of the CONF-06 additions are
implicated (the new rotation selects carry `aria-label="Rotation map"`, and the hidden
rotation inputs are exempt). Fix would be an `aria-label` on each, in a CONF-05 follow-up.

### 🔵 C6-5 — [COSMETIC, not fixed] Rotation help text repeats under every Conference row

The two-sentence explainer ("Author-ordered rotation for this conference… **Required:**
this Season's map mode needs at least 1 map per conference.") is rendered in full
beneath *each* Conference card — twice at 2 Conferences, four times at the maximum of
4. It would read better once, above the Conference list. Purely presentational; the
per-row `Required:` emphasis is genuinely useful, so this is a judgement call rather
than a defect.

### ✅ Dev-DB note
Season 66 was mutated during the pass (map mode set to `rotate_by_conference`,
rotations authored, activated, one week played) and Season 80 / league
`ChromeTest Conf06 Happy` were created by the Advanced form. Both carry the
`ChromeTest` prefix on their Teams and are teardown-eligible.

---

# Web testing — CONF-03 (Worlds qualification + last-chance qualifier)

Date: 2026-09-03
Branch: `conf-03-worlds-qualification`
Scope: the Playoffs screen (`/leagues/<id>/playoffs/`) for a >= 2-Conference Season —
the new **Last Chance Qualifier** bracket section and the read-only **Worlds
qualification** panel — plus the `play_single_round` dead-click transition. Server on
port **8001**. The fixture was built through the CONF-02 test helpers via
`manage.py shell` (`_built_regional_season("ChromeTest", sizes=[9, 5], cut=4)`) rather
than by simulating a full regular season; the bracket drains DID run the real engine.
Conference 1 has **9** Teams (3 qualifiers, gets a last-chance bracket), Conference 2
has **5** (2 qualifiers, gets none) — so both the last-chance and the no-last-chance
paths are exercised in one Season.

## Summary — CONF-03
| Area | Result |
|---|---|
| 9-Team Conference gets an eager **unseeded** last-chance row (`state=setup`, 0 participants) at phase activation | ✅ |
| 5-Team Conference gets **no** last-chance row | ✅ |
| `tournaments_for_phase` order — regional playoff sorts before its last-chance sibling | ✅ |
| Last-chance section renders with the `-lc` DOM suffix (`league-playoffs-phase-2-1-lc`, `-conference-2-1-lc`, `-stage-2-1-lc`) | ✅ |
| CONF-02 ids unchanged — Conf1 `-2-1`, Conf2 `-2-2`, no `-lc` on the 5-Team Conference | ✅ |
| Worlds panel **absent** while qualification incomplete | ✅ |
| Worlds panel **absent** entirely on a 0-Conference Season, which emits no `-lc` / `-stage-` / `-conference-` ids at all | ✅ |
| **Dead-click hazard**: the click resolving the phase's LAST regional node leaves the playoff controls present and enabled | ✅ |
| That same click seeds the last-chance bracket `setup` -> `active` with 4 participants | ✅ |
| Last-chance field excludes **both** already-qualified Teams | ✅ |
| Phase refuses to advance while the last-chance bracket is undrained (`_phase_complete` False) | ✅ |
| `worlds_qualifiers()` is all-or-`[]` — empty until every bracket has a champion | ✅ |
| `Season.champion_team` stays **NULL** after full qualification | ✅ |
| Tier-first + rate ordering correct across Conferences of unequal size | ✅ |
| Console clean (zero messages); all requests 2xx; no horizontal overflow at 720px | ✅ |

**Overall:** the CONF-03 surface is correct on real data. The dead-click hazard the
seam contract predicted was reproduced as a genuine transition and confirmed fixed.
No functional defect found. One cosmetic nit (C3-1).

### Worlds field as rendered (the live fixture)
Conference 1's regional playoff was won by its **4-seed** (`ChromeTest1x3`), not its
rank-1 Team — so the tier-2 slot correctly fell to rank-1 `ChromeTest1x0`, and the
last-chance field became ranks 2, 3, 5, 6 (`1x1`, `1x2`, `1x4`, `1x5`), excluding both
qualified Teams. That is the champion-is-not-rank-1 branch exercised by accident, which
is the most valuable thing this pass covered.

| Seed | Team | Conference | Provenance |
|---|---|---|---|
| 1 | ChromeTest1x3 | ChromeTestConf1 | Conference champion |
| 2 | ChromeTest2x3 | ChromeTestConf2 | Conference champion |
| 3 | ChromeTest1x0 | ChromeTestConf1 | Regular season |
| 4 | ChromeTest2x0 | ChromeTestConf2 | Regular season |
| 5 | ChromeTest1x2 | ChromeTestConf1 | Last-chance qualifier |

Seed 1 vs 2 demonstrates the **rate** rule doing real work: `1x3` took 15 league points
from 8 matches (1.875/match) and `2x3` took 3 from 4 (0.75/match). Under the rejected
raw-total ordering the 9-Team Conference would still have led, but the two Conferences'
totals were never comparable — 8 games versus 4.

Field size **M = 5**, which is *not* a power of two. This is the CONF-04 hazard the
PLAN entry flags: the Worlds bracket must handle byes or a play-in.

## Findings — CONF-03

### 🔵 C3-1 — [COSMETIC, not fixed] "Last Chance Qualifier" is rendered twice in the same section header
`laserforce_simulator/templates/leagues/playoffs.html` — the last-chance section's
heading already ends in the stage name (`2027 — ChromeTestConf1 Last Chance
Qualifier`, built from the Tournament's own `name`), and the template then renders the
`stage_label` badge (`league-playoffs-stage-2-1-lc`) immediately below it reading
**"Last Chance Qualifier"** again. The Conference name is likewise shown twice (in the
heading and in the `-conference-` subtitle), but that duplication is pre-existing
CONF-02 behaviour and is consistent across all three sections.

Not a defect — the badge is the machine-readable stage discriminator the seam contract
specifies, and it is what keeps a 0/1-Conference Season from rendering any new element
at all. Logged only so a future styling pass can decide whether the badge or the
heading suffix should carry the label. **Out of scope for CONF-03.**

---

# Web testing — CONF-02 (per-Conference regional playoffs)

Date: 2026-09-02
Branch: `conf-02-regional-playoffs`
Scope: the Playoffs screen (`/leagues/<id>/playoffs/`) for a >= 2-Conference Season,
which now renders one bracket per Conference. Server on port **8010**. The
2-Conference fixture was built through the test helpers via `manage.py shell` rather
than by simulating a full regular season (24 rounds, ~5+ minutes of real simulation);
the rendering, DOM ids and console are what the browser pass is actually testing.

## Summary — CONF-02
| Area | Result |
|---|---|
| Regional Season renders TWO bracket sections under one tournament phase | ✅ |
| DOM ids distinct — `league-playoffs-phase-2-1` / `-2-2`, no collision | ✅ |
| Each section headed by its Conference name (`league-playoffs-conference-2-1` / `-2-2`) | ✅ |
| **No cross-Conference pairing** — section `-2-1` holds only Conference-1 teams, `-2-2` only Conference-2 | ✅ |
| Seeds restart at 1 per bracket (`[1]` v `[4]`, `[2]` v `[3]` in each) | ✅ |
| Flat (0-Conference) league keeps un-suffixed ids `league-playoffs-phase-2` / `-4` and emits NO `conference-` ids | ✅ |
| `phase.tournament_id` NULL on the regional phase; `regional_tournaments` count 2 | ✅ |
| Console clean (zero messages); no horizontal overflow | ✅ |

**Overall:** the CONF-02 rendering surface is correct and the byte-identical
guarantee for 0/1-Conference Seasons holds on real data. One real defect was found
during the slice and **fixed in-slice** (C-1); one deliberate scope limit is recorded
for follow-up (C-2).

## Findings — CONF-02

### 🟠 C-1 — [FIXED in-slice] Regional brackets were unreachable from the UI
The seam contract enumerated only three drain callers. It missed the view-layer entry
points, which each guarded on `phase.tournament_id is None` — **always true for a
regional phase**, whose N brackets hang off `regional_tournaments`:

- `matches/league_views.py::play_playoffs` returned **409** on a >= 2-Conference
  Season, so the already-generalised `play_playoffs_task` was unreachable from the
  Play Playoffs button.
- `matches/league_views.py::play_single_round` rendered "No active playoff bracket
  to play."
- `matches/league_views.py::_playoff_cursor_keys` reported the phase inactive, so the
  NAV-01 topnav playoff controls did not render.

All three now resolve through the `Season.tournaments_for_phase(phase)` seam and are
byte-identical for a 0/1-Conference Season. Covered by
`TestRegionalUiEntryPointsReachTheBrackets` +
`TestZeroConferenceUiEntryPointsUnchanged` in
`matches/tests/test_regional_playoffs_drain.py`; the `play_playoffs` test is a true
regression guard (it asserts 202 where the old guard produced 409).

### 🟡 C-2 — [DEFERRED, agreed] Three secondary readers still assume one bracket
Scoped out of CONF-02 deliberately. These still read `phase.tournament` /
`phase.tournament_id` directly and so treat a >= 2-Conference Season as having no
playoff:

1. `_resolve_live_cursor` (`league_views.py` ~1571) — no LG-01i live playoff
   watch for a regional Season.
2. `_finals_mvp_for_season` (~2662) — LG-03 Finals MVP not computed.
3. The CAR-02 playoff-result classifier (~4033) — owner-mood playoff scoring
   treats a regional Season as having missed the playoffs.

None blocks playing or viewing a regional playoff. All three are quality gaps in
surfaces CONF-03 / CONF-04 (Worlds) will revisit, and each has the same one-line
`tournaments_for_phase` shape as the C-1 fixes.

### 🟡 C-4 — `changed_files.py` is blind to untracked files
**Tooling, out of CONF-02 scope.**
`.claude/skills/verify/scripts/changed_files.py --base main --mode branch` buckets only
files git already tracks. On this branch it reported `"migrations": []` even though
`matches/migrations/0058_tournament_regional_linkage.py` exists (untracked), and its
`python` list omitted both new test files. It under-reported the same way on the
CRE-02 branch earlier the same day.

Impact: `/verify` and `/code-review` both consume this helper as the authoritative
change set, so on exactly the branches that ADD a migration or a new test file, those
are the files it cannot see. A workflow trusting the `migrations` bucket to decide
whether to run a migration check would skip it precisely when it matters.

Fix idea: union the tracked diff with `git ls-files --others --exclude-standard` before
bucketing, so newly added files are classified like any other change.

### 🟡 C-3 — Team History does not distinguish regional from Worlds rounds
Not a defect today, recorded so it is not lost: CONF-02 widened
`league_screens/team_history.py` so regional playoff rounds count toward the Overall
tab and `playoff_appearances`. Once CONF-04 lands the Worlds bracket, that screen will
count regional and Worlds appearances identically. Whether they should be
distinguished is a CONF-04 question.

### ✅ Dev-DB note
Migration `0058_tournament_regional_linkage` had to be applied to the dev SQLite DB
before the shell fixture would build (`no such column:
matches_tournament.season_phase_id`) — the exact staleness CLAUDE.md's
"check for unapplied migrations first" rule warns about.

---

# Web testing — CRE-02 (League spread / tiered generation)

Date: 2026-09-02
Branch: `cre-02-tiered-generation`
Scope: the new **League spread** selector on the Advanced create-League form
(`/leagues/create/advanced/`, `#league-create-league-spread`) and the one-click
**League template** chooser (`/leagues/create/`), which must stay Even-only. Server
run on port **8010** (see E-1 below).

## Summary — CRE-02
| Area | Result |
|---|---|
| Advanced form renders `League spread` — label, `form-select`, options Even / Tiered / Steep, **Even preselected** | ✅ |
| Control sits directly after the Stat mean / Stat std-dev row, per the seam-contract insertion point | ✅ |
| Help text explains the ramp and the preserved league average | ✅ |
| Advanced POST with **Steep** + 8 teams -> League + draft Season created, redirect to `/seasons/68/standings/` | ✅ |
| Steep league strength range = **34.68** overall points (predicted ~32 + noise) | ✅ |
| CRE-01 Difficulty still composes — Medium picked rank 5 of 8 (`N//2`) in the Steep league | ✅ |
| Free-agent pool stays **flat** — 153 agents, mean overall 50.22 (untiered, as scoped) | ✅ |
| Template chooser has Difficulty but **no** spread select (Advanced-only decision) | ✅ |
| Chooser create (8-Team Classic) -> Even league, strength range **3.67** (predicted ~4) | ✅ |
| Console clean (zero messages) on both create surfaces and both resulting Standings pages | ✅ |
| Network: all 200s across both flows | ✅ |
| Responsive: no horizontal overflow at 720x1115 (navbar collapses) or 1280x900; select full-width in both | ✅ |

**Overall:** no bugs found in the CRE-02 surface. The Even-vs-Steep contrast measured
in-browser (**3.67** vs **34.68** overall-rating range across 8 teams) is the feature
working exactly as designed, and matches the arithmetic locked in the seam contract.
One pre-existing **environment/tooling** issue was hit; it is logged below and has since been **fixed**.

## Findings — CRE-02

### ✅ Advanced -> Steep -> League + draft Season — works
`ChromeTest CRE02 Steep` (league 58 / season 68): 8 teams, measured means
67.60 / 62.39 / 54.25 / 52.70 / 49.03 / 41.31 / 36.96 / 32.91 against the contract's
predicted ramp 66.0 / 61.4 / 56.9 / 52.3 / 47.7 / 43.1 / 38.6 / 34.0. Manager team
`Rogue Recon #8` at rank 5 of 8 — the correct `N//2` for the default Medium difficulty.

### ✅ Chooser -> Even — works
`ChromeTest CRE02 Chooser Even` (league 59 / season 69): strength range 3.67, i.e. the
pre-CRE-02 noise-only spread, confirming `_template_to_form_data`'s static
`"league_spread": "even"` and the `tier_means=None` Even path.

### ✅ ~~E-1 — `start_test_server.ps1` can report `SERVER_READY` for the wrong server~~ — FIXED (2026-09-02)
**~~Pre-existing, out of CRE-02 scope; environment/tooling only.~~ FIXED in the user-level skill `~/.claude/skills/chrome-web-testing/` — outside this repo, so no code change lands on this branch.** Originally two defects compounded (a third and fourth surfaced while fixing them):

1. The helper launches the server with `Start-Process -FilePath python`, which resolves
   against the *system* PATH rather than the repo `.venv`. On this machine that is
   `C:\Anaconda\python.exe`, which has no Django, so the detached process dies
   immediately (`SERVER_FAILED reason=process-exited-early-code:1` on a free port).
2. The readiness poll only checks that `http://127.0.0.1:<port>/` answers — not that
   *our* app answered. Port 8000 was already held by an unrelated project (an SPA
   titled "Campaign Codex", pid 17244), so the poll accepted that app's HTTP 200 and
   printed `SERVER_READY pid=<already-exited pid>`. The first test pass then drove a
   foreign app and reported the new form control missing.

3. `manage.py` sits under a path containing a space (`Andrew Galkowski`) and was NOT
   quoted inside `Start-Process -ArgumentList`, so python read `C:\Users\Andrew` as the
   script path — a second, independent cause of the same silent early exit.
4. Port 8000 turned out to be **permanently** occupied by a separate project of the
   maintainer's, so the collision was not incidental — the default port itself was wrong.

Repro (pre-fix): occupy port 8000 with any other HTTP server, run the helper, observe
`SERVER_READY` naming a PID that has already exited.

**Resolution — all four fixed and verified.** The default `-Port` is now **8001**; the
launcher resolves `.venv\Scripts\python.exe` relative to `$RepoRoot` (falling back to
`python`) for BOTH `migrate` and `runserver`; the `manage.py` argument is quoted; a
pre-flight `Get-NetTCPConnection` check fails fast naming the occupying pid; and
readiness now asserts the `Laserforce Manager` navbar marker in the response body
instead of accepting any HTTP answer. Failures additionally surface the launch stderr.
Verified live — port occupied ⇒ `exit 1 SERVER_FAILED reason=port-8001-already-in-use-by-pid:…`;
port free ⇒ `exit 0 SERVER_READY pid=… url=http://127.0.0.1:8001/` serving this app.
`REFERENCE.md` updated to document 8001 as the default and why.

---

# Web testing — CONF-05 (Manage Conferences page)

Date: 2026-06-30
Branch: `conf-05-manage-conferences`
Scope: the new draft-Season **Manage Conferences** composer (`manage_conferences`
view + `templates/seasons/manage_conferences.html`) and its draft-only dashboard
entry link — the one surface carrying vanilla JS the unit tests don't exercise.

## Summary — CONF-05
| Area | Result |
|---|---|
| Create-League → draft Season (63) → dashboard shows `season-dashboard-manage-conferences-link` (draft-only) | ✅ |
| `/seasons/63/conferences/` composer renders — 8 enrolled teams, per-team `<select>`, "+ Add conference", Save | ✅ |
| JS: "+ Add conference" + naming ("West"/"East") rebuilds every team `<select>` with options `0:West` / `1:East` | ✅ |
| Assign 4 West / 4 East → Save → 302 back to the composer; partition persisted | ✅ |
| Reloaded composer pre-fills the saved partition (West/East, 4+4) from the DB | ✅ |
| Create-League form shows the **Conferences** dropdown (None / 2 / 3 / 4) | ✅ |
| Create with 8 teams + "2 conferences" → **auto-redirects to `/seasons/66/conferences/`** (composer, not standings) | ✅ |
| Pre-split: "Conference 1" / "Conference 2", teams auto-split evenly 4 / 4 | ✅ |
| **League** dashboard (`/leagues/56/`) shows `league-dashboard-manage-conferences-link` → the composer | ✅ |
| Console clean (no messages) across the whole flow | ✅ |

## Findings — CONF-05
- **No bugs found.** The full in-app flow works end-to-end: dashboard link →
  composer → vanilla-JS conference add/name/assign (selects stay in sync) → Save →
  atomic partition persist → reload pre-fills. Zero console errors. (A demo draft
  league "ChromeTest Conferences" / season 63 with West/East conferences was left
  in the dev SQLite DB — ready to Start Season + play to see per-conference
  standings; delete it via Delete League when done.)

---

# Web testing — CONF-01 (Conference foundation)

Date: 2026-06-29
Branch: `conf-01-conference-foundation`
Scope: the league surfaces CONF-01 touched — the Season **Standings** page
(`season_standings` view + `templates/seasons/standings.html`, rewritten to render
one table per Conference) and the season/league **dashboards**
(`_build_dashboard_context` top-3 snippet). CONF-01 is admin-only (Conferences are
created via Django Admin; no create-League composer yet) and a **zero-Conference
Season is byte-identical to before**, so the browser smoke targets the
zero-Conference regression path on real data (completed league 42 / season 58).

## Summary — CONF-01
| Area | Result |
|---|---|
| League dashboard (`/leagues/42/`) renders — top-3 standings snippet, leaders, next-round, View-bracket, nav | ✅ |
| Season Standings (`/seasons/58/standings/`) zero-Conference renders the single `season-standings-table` byte-identically — full 17-column LG-06g table, all sortable headers, Champion line | ✅ |
| Console clean (no messages) on `/leagues/`, dashboard, and standings | ✅ |
| Network all 2xx (page doc + Bootstrap CDN) on every surface walked | ✅ |

## Findings — CONF-01
- **No bugs found.** The zero-Conference regression path is clean end-to-end: the
  rewritten `season_standings` view + per-group template render the existing single
  table identically (no `season-standings-conference-*` ids emitted when there are
  no Conferences), and the dashboard snippet is unchanged. Zero console errors, zero
  non-2xx requests.
- `/seasons/60/standings/` 404'd — a stale/deleted season id from a prior member-night
  test run, **not** a CONF-01 regression (the route resolves fine for valid seasons,
  e.g. season 58).
- **Multi-Conference rendering not exercised in-browser:** a populated per-Conference
  standings page needs Conferences set on a *draft* Season then played (the play loop
  stamps `Match.conference`); retrofitting Conferences onto an already-completed Season
  via admin would render unrepresentative empty/zero-filled tables. That rendering path
  (stacked per-Conference tables + the `season-standings-conference-{id}` /
  `-conference-name-{id}` DOM ids) is covered by the passing `test_season_views.py`
  view tests, which render the real template through the Django test client with
  Conferences present. No browser-only gap remains for the foundation slice.

## Findings — CONF-04 (Worlds Tournament phase)
- **[Open · cosmetic] Reported STAGE counts shrink when a play run crosses the
  regional -> Worlds phase boundary.** `play_season_task` and `play_playoffs_task`
  resolve `tournaments_for_phase` once and cache it; when the Worlds bracket is
  built mid-run the loops deliberately re-resolve that list (ADR-0037), and
  `_stage_counts()` closes over the same name. The `{"completed", "total"}`
  payload therefore describes the **Worlds bracket alone** rather than the whole
  run, so a "Play Playoffs" click that drains four regional stages plus the
  Worlds final reports `1 / 1` instead of `5 / 5`. The numbers are internally
  consistent (they describe the phase the run ended on) and `completed == total`
  still holds, so nothing is *wrong* on screen — it just under-reports the work
  done. Pinned as intended in the CONF-04 seam contract (section 6.B) and
  asserted that way in `test_regional_playoffs_drain.py`. **Fix, if wanted:**
  carry a running `(completed, total)` accumulator across the re-resolution in
  both tasks and add it to `_stage_counts()`; ~6 lines each, but it also changes
  the mid-run PROGRESS meta, so it wants its own slice rather than riding along
  with the feature.

### Browser pass — CONF-04 (2026-09-03, Chrome DevTools MCP, port 8001)

Season 77 / league 67 ("ChromeTest CONF04 Worlds"): 8 teams, 2 Conferences of 4
(Nevada / California), phases `round_robin,tournament:standings`. Four Teams per
Conference means one qualifier each, so **M = 2** — the small-field case that
required the new `minimum=2` bracket floor. Driven end-to-end over real HTTP
through the synchronous `play-week` endpoint.

| Check | Result |
| --- | --- |
| Worlds phase derived at Start Season (ordinal 3, `tournament_mode="worlds"`) | ✅ |
| `worlds_qualifiers()` empty pre-qualification; phase parks the cursor | ✅ |
| Playoffs screen pre-play: `league-playoffs-phase-2` + `-phase-3-worlds` + `-stage-3-worlds` | ✅ |
| Cursor walk RR (6 clicks) -> regionals (2) -> Worlds (1) | ✅ |
| **Boundary click builds AND drains Worlds** (dead-click fix) | ✅ |
| `Season.champion_team` crowned and rendered ("Champion: Lumen Legion #7") | ✅ |
| Play-control label reads "Play Until Playoffs" (not "…Tournament") | ✅ |
| CONF-02 regional DOM ids unchanged (`-phase-2-1`, `-phase-2-2`, nodes, scores) | ✅ |
| CONF-03 panel `league-playoffs-worlds` coexists with card `league-playoffs-phase-3-worlds` | ✅ |
| Console clean (0 messages) on the Playoffs screen, before and after play | ✅ |
| Network all 2xx; standings / dashboard / playoffs / history all 200 | ✅ |
| Wide (1280x900) and narrow layouts render without horizontal overflow | ✅ |

- **[Fixed in-slice] Worlds pending stub rendered the word "Worlds" twice** — the
  card heading was the bare string `"Worlds"` and the CONF-04 `stage_label` badge
  beside it also read "Worlds". `league_screens/playoffs.py` now uses
  `f"{view_season.name} Worlds"`, mirroring the built card's heading (the
  Tournament's own name), so the heading is stable across the unbuilt -> built
  transition and the badge reads as a label rather than an echo. Test pin updated.
- **[Open · cosmetic, pre-existing CONF-03 placement] The "Worlds qualification"
  table renders BELOW the Worlds bracket**, so a reader sees the result before the
  field that produced it. CONF-03 placed that panel after the bracket loop and
  CONF-04 did not move it. Reordering is a template-only change, deliberately left
  out of this slice's scope.
- **[Environment, not a CONF-04 defect] The async play endpoints 500 locally.**
  `play-until-end`, `play-two-months` and `play-playoffs` enqueue Celery tasks and
  raise `RuntimeError: Retry limit exceeded while trying to reconnect to the Celery
  result store backend` because Redis is not running on this machine. Unrelated to
  this slice (it reproduces for any Season); the synchronous `play-week` /
  `play-single-round` paths — which are the ones CONF-04 changed — were exercised
  instead and are green.

### DEL-01 teardown leaves bracket-only Matches, Tournaments and Teams behind

**[Open · low · pre-existing, NOT CONF-04]** Found while cleaning up CONF-04
browser-test data. Deleting three Leagues through the real Delete League flow
(`POST /leagues/<id>/delete/`) removed the Leagues, Seasons and round-robin
Matches, but left behind:

- the per-Conference **playoff `Tournament` rows** (5 of them),
- their **bracket Matches** (15) and `GameRound`s (30),
- and the **20 Teams** those brackets referenced, including both manager Teams.

Cause is structural: a bracket Match carries `season=NULL` and
`season_phase=NULL` (the LG-02-Part2c-1 decision), so a teardown scoped by
`season` never sees it; and `SeasonPhase.tournament` is `on_delete=SET_NULL`,
so deleting the phase detaches the Tournament rather than removing it. The
orphaned Teams then survive the "delete if orphaned" check because they are
still referenced by those bracket Matches.

Reproduce: create a League with a `tournament` phase, play it to completion,
delete the League, then check `Tournament.objects.count()` and
`Team.objects.filter(...)` for the generated Teams. Nothing user-facing breaks —
the rows are unreachable from the UI — but a dev database accumulates orphans.

**Fix sketch:** in the DEL-01 teardown, before dropping the Season, walk
`SeasonPhase -> tournament` and `phase.regional_tournaments` and delete those
Tournaments (which cascades `BracketNode` / `SeriesMatch`), then delete Matches
reachable through `match__series_match__node__tournament`, then run the existing
orphan-Team sweep.
