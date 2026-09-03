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
