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
