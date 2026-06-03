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
