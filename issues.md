# Web testing — LG-01j (per-Season arena map config)

Date: 2026-05-28
Branch: `lg-01j-per-season-arena-map-options`
Scope: smoke-test the LG-01j surfaces (create-League form gains `map_mode` + `map_pool`; League + Season dashboards render `map_config_label`).

## Severity legend
- 🔴 critical — broken feature, data loss, crash
- 🟠 warning — visible bug, no data loss
- 🟡 minor — cosmetic, copy nit
- 🔵 environment — host/cache/tooling, not the code under test
- ✅ verified working

## Summary

| Severity | Surface | Finding |
|---|---|---|
| 🔵 | `runserver --noreload` | Stale dev-server rendered empty `{{ form.map_mode/pool }}` until the process was killed + `__pycache__` wiped. NOT a code defect — Django test client (`Client().get('/leagues/create/')`) renders the fields correctly against the same code base. Fixed by killing PID + clearing pycache + restarting. |
| ✅ | `/leagues/create/` | Both new fields render with correct DOM ids (`league-create-map-mode`, `league-create-map-pool`), correct options (3 modes + 2 confirmed-config maps), correct defaults (`none`, empty pool). |
| ✅ | Form validation | All 3 mode-vs-pool `clean()` rules fire byte-equal to seam-contract literals: `"Map pool must contain exactly 1 map when Map mode is 'Single map'."`, `"Map pool must contain at least 1 map when Map mode is 'Random per Round'."`, `"Map pool must be empty when Map mode is '3-zone fallback'."`. |
| ✅ | Form happy paths | Mode `none` + empty pool → 302 to `/seasons/<id>/standings/`; mode `single` + 1 map → 302; mode `random_per_round` + 2 maps → 302. All three Leagues + Seasons created. |
| ✅ | `season-dashboard-map-config` | Renders the locked label string for every mode: `"Map: 3-zone fallback (no map)"`, `"Map: Single — Syracuse Laser Tag"` (em-dash U+2014 confirmed byte-equal), `"Map: Random per Round (2 maps: San Marcos Laser Tag, Syracuse Laser Tag)"` (alphabetical sort verified — San Marcos < Syracuse). |
| ✅ | `league-dashboard-map-config` | Same label string surfaces on `/leagues/<id>/` per the parent League's active Season. |
| ✅ | No console errors | Zero console messages across all surfaces walked. |
| ✅ | No network errors | All GETs (`/`, `/teams/`, `/matches/`, `/leagues/`, `/seasons/<id>/standings/`, `/seasons/<id>/schedule/`, `/leagues/<id>/history/`) return 200; no 4xx/5xx. |
| ✅ | Existing surfaces unaffected | Landing, Teams list, Matches list, League list, season standings/schedule/history pages still render correctly under the LG-01h `app_mode` branching with the LG-01f sidebar intact. |
| ✅ | Responsive layout | Form renders correctly at 720×1115 (mobile, navbar collapsed) and 1280×900 (desktop). Screenshots `screenshots/lg01j-create-mobile-720.png` + `screenshots/lg01j-create-desktop-1280.png`. |

## Test data created during this session (not auto-torn-down)

The chrome-web-testing teardown helper expects `ChromeTest`-prefixed Team names, but LG-01b's `_generate_teams` picks Team names from the `teams.constants.TEAM_NAMES` pool — the prefix doesn't match. The League names ARE `ChromeTest LG-01j …` but the helper script doesn't accept a `LeagueNamePrefix`. Created (and left in the dev DB):

- League 16 — "ChromeTest LG-01j none-happy" with Season 15 (`map_mode=none`)
- League 17 — "ChromeTest LG-01j single-happy" with Season 16 (`map_mode=single`, pool = Syracuse)
- League 18 — "ChromeTest LG-01j random-happy" with Season 17 (`map_mode=random_per_round`, pool = both)

To clean up manually: `python laserforce_simulator/manage.py shell --command "from matches.models import League; League.objects.filter(name__startswith='ChromeTest').delete()"` — cascades Season + Match (none) + GameRound (none) + the M2M rows. Teams + Players survive (Team is global per CONTEXT.md). The generated teams use names from TEAM_NAMES + auto-rostered players; no straightforward way to filter them.

## Flows NOT exercised

- **LG-01e Start Next Season carry-forward** — requires a completed Season to drive `next_season`. Unit tests cover this end-to-end (`TestNextSeasonMapConfigCarryForward`).
- **LG-01d Play Week / play_season_task with `arena_map` resolution** — requires Starting the Season + Playing a Matchday. Unit tests cover this (`TestPlaySeasonTaskMapResolution` mocks `simulate_scheduled_round` and asserts the `arena_map=` kwarg propagation; the helper has its own 22-test pure-unit class).
- **Defensive deleted-map fallback** — requires admin-deleting a map after activation. Unit tests cover (`TestResolveFixtureMapMissingMap`, `TestSeasonStartingMapPoolSnapshot.test_defensive_*`).

## Verdict

LG-01j ships without browser-visible defects. Stale-server caching was the only blocker and was environmental, not code-side. All locked-name + locked-label assertions pass byte-equal.
