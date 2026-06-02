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
