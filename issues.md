# Web testing — LG-02-Part2c-2 (multi-RR play loop + Match.season_phase FK)

Date: 2026-06-06
Branch: `lg-02-part2c-2-multi-rr`
Scope: read-path smoke of the surfaces this backend slice touches (no template/URL/DOM
changes). Confirmed the rewritten `scheduled_fixtures()` (flat concatenation via the new
`scheduled_fixtures_by_phase()`), `current_phase()`, and `_build_dashboard_context` still
render existing data byte-identically. Multi-RR play-loop logic itself is covered by the
green integration suite (3852 passed); a persistent multi-RR league was deliberately NOT
created in-browser (create spawns global random-named teams the teardown can't clean).

## Summary — LG-02-Part2c-2
| Area | Result |
|---|---|
| `/leagues/create/` + Part2b "+ Add block" composer render; console/network clean (one pre-existing a11y label issue, unrelated) | ✅ |
| League dashboard `/leagues/22/` — `Play Next`, top standings, "Next round: Matchday 2 · Round 1", "Rounds played: 2/12", leaders all render (driven by changed `current_phase()`/`scheduled_fixtures()`/`_build_dashboard_context`); console clean | ✅ |
| Season schedule `/seasons/21/schedule/` — single-RR season renders matchdays 1–6 with continuous weekly dates (05-31 → 07-05), R1 in MD 1–3 / R2 in MD 4–6 = byte-identical `scheduled_fixtures()` (offset-0 single-phase path); console clean | ✅ |
| No regression in the chokepoint rewrite on existing data; no data created (read-only views) | ✅ |

## Findings — LG-02-Part2c-2
- No bugs found. The one console a11y "issue" on `/leagues/create/` (form field without a label) is **pre-existing** and unrelated (Part2c-2 touched no templates).

---

# Web testing — LG-02-Part2c-1 (RR → single-elimination playoff embed)

Date: 2026-06-05
Branch: `lg-02-part2c-1-rr-playoff-embed`
Scope: black-box pass over the new league/season dashboard playoff controls — the
phase cursor, the conditional "Until Playoffs" relabel, the auto-built
standings-seeded playoff bracket, "Play Single Round" / "Play Playoffs" / "View
bracket", plus a regression smoke of the non-tournament dashboard path. Exercised
end-to-end: created an RR→Tournament League via the composer, started + drained the
RR (sync One Week), confirmed the auto-build, and advanced one playoff match.

## Severity legend
- 🔴 BLOCKER — broken/500/data loss · 🟠 HIGH — wrong behaviour · 🟡 MED — minor
  functional gap · 🔵 LOW — cosmetic/a11y · ✅ working flow

## Summary — LG-02-Part2c-1
| Area | Result |
|---|---|
| Homepage + existing **single-RR** active league dashboard (`/leagues/22/`) render; "Play Next" only, **no** playoff buttons, label stays "Until End of Season" (no tournament phase) — non-tournament path not regressed | ✅ |
| `/leagues/create/` composer accepts an RR→**Tournament** composition (preceding-RR guard passes since RR is first); creates League 28 / Season 27 | ✅ |
| Draft + active RR-phase season dashboard (`/seasons/27/`) render, console + network clean | ✅ |
| Play-Next dropdown shows **"Play Until Playoffs"** (conditional relabel fires because a tournament phase follows) | ✅ |
| Draining the RR to 12/12 (sync `play_week`) auto-builds the playoff Tournament (id 15) on RR completion — no manual build click | ✅ |
| Bracket `/tournaments/15/` renders **Active**, **seeded by final RR Standings** ([1] Ember Enforcers = rank-1 9pts, 1v4 / 2v3 single-elim) | ✅ |
| Tournament-phase season dashboard renders all 6 playoff DOM ids (`season-dashboard-play-single-round-form`/`-submit`, `-play-playoffs-form`/`-submit`/`-progress`, `-view-bracket-link`); View-bracket → `/tournaments/15/` | ✅ |
| **"Play Single Round"** (sync) advances **exactly one** node — [1] Ember beat [4] Aurora 6–0, advanced to the final; 2v3 stays unplayed; 302 back to dashboard | ✅ |
| Console clean on every page; network all 2xx/3xx (no favicon hit observed) | ✅ |

## Findings — LG-02-Part2c-1
- 🔵 LOW (a11y, **pre-existing**, NOT Part2c-1) — `/leagues/create/` logs one console
  `[issue] No label associated with a form field (count: 1)`. Present on the LG-01j/Part2b
  create form independent of this slice (Part2c-1 added no fields to that form). Out of scope;
  left logged.
- ℹ️ "Play Playoffs" (async Celery `play_playoffs_task`) was **not exercised in-browser** —
  the smoke env has no Redis broker / Celery worker, so the `.delay()` enqueue + 1s poll
  would spin. Its drain→champion→Season-completion path is covered by the EAGER integration
  test `matches/tests/test_season_playoffs.py::TestPlayPlayoffsTask`. Button + form + progress
  DOM ids all render correctly.
- ✅ No new bugs. All touched surfaces render and behave per the seam contract.

---

# Web testing — LG-02-Part2b (create-League phase composer + dormant phase columns)

Date: 2026-06-05
Branch: `lg-02-part2b`
Scope: black-box pass over the new vanilla-JS "+" composer on `/leagues/create/`
(add/remove blocks, hidden-field wire serialization), the two `SeasonPhase`
persistence paths, the empty-composer default, the no-RR form rejection, the
extended `SeasonPhaseAdmin` columns, and a regression smoke of the surrounding
league pages. Read-path is unchanged this slice (the chokepoint still plays the
first `round_robin` phase only), so no play-loop/standings behaviour was exercised.

## Severity legend
- 🔴 BLOCKER — broken/500/data loss · 🟠 HIGH — wrong behaviour · 🟡 MED — minor
  functional gap · 🔵 LOW — cosmetic/a11y · ✅ working flow

## Summary — LG-02-Part2b
| Area | Result |
|---|---|
| `/leagues/create/` renders; composer ids `league-create-phases-composer` / `-add-block` / `-phases` / `-member-night-note` all present; default single `round_robin` row | ✅ |
| "+ Add block" appends a row; row gets `league-create-phase-row-{i}` / `-phase-type-{i}` / `-phase-format-{i}` | ✅ |
| Setting a row to **Tournament** hides its format select + shows the `phase-tournament-pending` "build coming in a later release" flag (RR rows keep it hidden) | ✅ |
| Hidden `#league-create-phases` serializes to comma wire format — `round_robin` / `round_robin,tournament` / `tournament` / `` (empty after removing all) | ✅ |
| Submit **RR→Tournament** → League+Season 25; phases persist `ord1 round_robin fmt='single_round_robin' tournament=None` + `ord2 tournament fmt=None tournament=None` | ✅ |
| Submit **empty composer** → Season 26; defaults to a single `ord1 round_robin fmt='single_round_robin'` phase (Part2a equivalence) | ✅ |
| Submit **Tournament-only (zero RR)** → form re-renders with exact error `composition must contain at least one round-robin phase`; **no League/Season created** (atomicity) | ✅ |
| `/admin/matches/seasonphase/` changelist `list_display` now shows `Schedule format` + `Tournament` columns with correct values (`single_round_robin` / `-`) | ✅ |
| Regression smoke: `/leagues/`, `/leagues/<id>/history/`, `/seasons/<id>/standings/` all 200, console + network clean | ✅ |

## Findings — LG-02-Part2b
- 🔵→✅ **RESOLVED** LOW (a11y) — the two JS-built composer selects had **no
  associated `<label>` / `aria-label`**, so Chrome reported "No label associated
  with a form field" on `/leagues/create/`. Fixed by adding
  `aria-label="Phase type"` (`templates/leagues/create.html` `typeSelect`) and
  `aria-label="Schedule format"` (`formatSelect`) to the JS-cloned rows.
  Cosmetic/a11y only; the composer was fully functional throughout.
- ℹ️ Note (not a Part2b issue) — `/admin/` emits a Django-admin built-in
  "Deprecated feature used" console issue; unrelated to this task's code.

## Result — ✅ ALL FUNCTIONAL CHECKS CLEAN
Every browser-observable Part2b surface works end-to-end: the composer builds and
serializes ordered phase rows, both creation paths persist the correct
`SeasonPhase` rows (tournament FK always NULL per slice), the empty default and
zero-RR rejection behave exactly per the seam contract's `ValueError` strings, and
the admin surfaces the two new columns. Only finding is one low-severity a11y
label gap on the new composer selects. Dev-DB test data (Seasons 25/26 + their
ChromeTest leagues/teams, superuser `chrometest`) is disposable (ADR-0004).

---

# Web testing — LG-02-Part2a (SeasonPhase foundation)

Date: 2026-06-05
Branch: `lg-02-part2a`
Scope: focused smoke of the Part2a-touched surface — the `SeasonPhase`
fixture-resolution chokepoint retrofit (intended zero user-visible change).
Created a League (now auto-creates a `round_robin` `SeasonPhase`), loaded Season
Standings / Schedule / Season dashboard, ran Start Season, played One Week
(`play_week` → `Season.scheduled_fixtures()` → `simulate_scheduled_round`), and
loaded the Team Schedule page.

## Result — ✅ ALL CLEAN, no issues found
- ✅ `/leagues/create/` → League+Season created, `round_robin` SeasonPhase row added; redirect to draft Standings renders the 4-team preview.
- ✅ `/seasons/<id>/standings/` (draft preview) — renders, no console/network errors.
- ✅ `/seasons/<id>/schedule/` — full 12-fixture double-leg round-robin renders (chokepoint == prior `generate_schedule` output).
- ✅ `/seasons/<id>/` season dashboard — draft (Start Season) and active (Play Next, round-progress `0/12`→`2/12`, leaders) branches both render.
- ✅ Start Season → Play One Week — 2 fixtures simulated via the retrofitted `play_week`/`scheduled_fixtures()` path; standings + leaders populate; next round advances. No 500/console/network errors.
- ✅ `/leagues/<id>/team_schedule/<team>/` — Upcoming (from `scheduled_fixtures()`) + Completed (played MD1 R1, score + round-detail link) render correctly.
- Cleanup: League/Season/SeasonPhase/4 teams/2 matches/2 rounds deleted; server stopped.

---

# Web testing — LG-02x-1 (Random Draw player-pool tournament)

Date: 2026-06-04
Branch: `lg-02x-1-random-draw`
Scope: smoke-test the new `team_assembly == "random_draw"` flow — the create-form
`Team assembly` + `Role assignment (per Round)` selects (JS toggle) + the RR→DE
`wb/lb` combo, the detail-page pool/draw surface (generate a pool, divisibility
notice, draw, re-roll, lock), and the per-Round dynamic-role hook firing through a
real `simulate_match` (`Play Next`). Preset tournaments smoke-checked for regression.

## Severity legend
- 🔴 BLOCKER — broken/500/data loss · 🟠 HIGH — wrong behaviour · 🟡 MED — minor
  functional gap · 🔵 LOW — cosmetic/a11y · ✅ working flow

## Summary — LG-02x-1 Random Draw

| Area | Result |
|---|---|
| Create form: `Team assembly` select (Preset / Random draw); selecting Random draw reveals `Role assignment (per Round)` via JS toggle | ✅ |
| RR→DE `Bracket format` reveals the `wb/lb` advancers combo (`4 WB, 0 LB` default) | ✅ |
| Create (random_draw + RR→DE + role mode) → tournament 14, `setup` | ✅ |
| Detail pool stage: `Player Pool (0)`, divisibility notice, generate/add-existing/CSV-import forms, `Draw teams` **disabled** while pool empty | ✅ |
| Generate pool (24) → `Player Pool (24)`, 24 entry rows, `Draw teams` **enabled**, invalid-notice gone | ✅ |
| `Draw teams` → 4 tier-balanced drawn teams (named `Draw Team N`), draw table + per-team sections + **re-roll** + lock buttons | ✅ |
| `Lock & Build Bracket` → stage badge **"Seeding stage"**, state Active, RR crosstable + standings render | ✅ |
| **`Play Next` → per-Round dynamic-role hook fires through `simulate_match`**; match recorded (Draw Team 2: 1W/6 match pts), 9 crosstable match links | ✅ |
| Preset double-elim detail (`/tournaments/7/`) renders, **pool section correctly hidden**, bracket shown — no regression | ✅ |
| Console errors | ✅ none across create / pool / draw / lock / play-next |
| Network non-2xx | ✅ none (all document + Bootstrap CDN 200) |

## Findings — LG-02x-1 Random Draw

- 🔴→✅ **RESOLVED (caught by pytest, fixed pre-browser-run):** rendering a
  `random_draw` tournament's detail page during the **pool stage** (format
  `round_robin_double_elim`, 0 participants pre-draw) 500'd —
  `_build_rr_crosstable` called `generate_schedule([])` → `ValueError`. Fixed by an
  early `if len(team_ids) < 2: return []` guard in
  `laserforce_simulator/matches/tournament_views.py::_build_rr_crosstable`
  (the 6 failing `test_tournament_views` tests now pass; full suite green).
- 🔵 LOW (a11y): the random_draw detail page reports `No label associated with a
  form field (count: 3)` — the new pool generate/import inputs lack explicit
  `<label for>`. Cosmetic; not functional. `templates/matches/tournament_detail.html`.
- 🔵 LOW (UX, by design): the create form permits `team_assembly=random_draw` with
  ANY `format` (the orthogonal-field decision). A user could pick random_draw +
  single-elim; the canonical flow is RR→DE. Not a bug — flexibility — but the help
  text doesn't say RR→DE is the intended bracket.
- 🔵 LOW (UX): on a random_draw tournament the legacy LG-02a-2 **"Import
  Participants (CSV)"** team-import surface still renders alongside the new pool
  intake — redundant for a player-pool tournament (the team-import path expects
  preset teams). Cosmetic redundancy, harmless.

## Teardown — LG-02x-1

- Test data in the **gitignored dev `db.sqlite3`** (disposable, ADR-0004):
  Tournament 14 "ChromeTest RandomDraw" + its 24 generated pool Players (on the
  Free Agents Team) + 4 `Draw Team N` teams (`is_draw_team=True`) +
  TournamentParticipant/BracketNode/Match rows from the one played match. Not part
  of the PR. Server to be stopped after testing.

---

# Web testing — LG-02c (Swiss tournaments)

Date: 2026-06-04
Branch: `lg-02c-swiss`
Scope: smoke-test the new `Swiss` `Tournament.format` — the create-form Swiss
option + `swiss_rounds` input + the JS toggle hiding the series-length / rrde
controls, the seed-fold round 1 built at lock, the **deferred per-round build**,
Buchholz-ordered standings, and champion crowning. Walked create → lock →
sync `Play Next` ×4 → completed, against existing dev teams.

## Summary — LG-02c Swiss

| Area | Result |
|---|---|
| Create form offers **Swiss**; selecting it reveals `#tournament-create-swiss-rounds` and hides the 4 `*-series-length` selects + the rrde-combo | ✅ |
| Create (4 existing teams, `swiss_rounds=0` → auto 2) → tournament 8, setup | ✅ |
| Setup detail: `Swiss Rounds` heading + zero-filled `Standings` + editable `Seeding` | ✅ |
| Lock & Build → stage badge **"Swiss stage"**, state **Active**, R1 seed-fold `[1]v[3]` / `[2]v[4]`, each `Bo1` `0–0` | ✅ |
| `Play Next Match` (sync) resolves one Match at a time; `Game N` deep-links to `/matches/<id>/`, Series score + standings update | ✅ |
| **Deferred R2 build**: after R1's last node, Round 2 auto-appears pairing the two R1 winners + the two losers, **avoiding rematches** (greedy ranked-sweep) | ✅ |
| **Champion** crowned only after the final round's last Match → state **Completed**, `Champion: Echo Eagles` via the reused `tournament-champion-banner` | ✅ |
| **Buchholz** tiebreak exercised: Onyx Owls ranked above Phoenix on equal 3 pts (Onyx opponents summed 9 vs Phoenix 3) | ✅ |
| Console errors/warnings | ✅ none |
| Network non-2xx | ✅ none (document + Bootstrap CDN all 200) |

## Findings — LG-02c Swiss

- No bugs. Every browser-observable Swiss surface behaves per the seam contract:
  the create toggle, R1 fold, the recurring deferred round build with rematch
  avoidance, Buchholz-ordered standings, and Standings-leader champion crowning
  all correct. Single/double-elim / RR / RR→DE renders unaffected.

## Teardown — LG-02c Swiss

- Test data in the **gitignored dev `db.sqlite3`** (disposable, ADR-0004):
  Tournament 8 "ChromeTest Swiss Cup" (existing teams — no new teams created) +
  Matches 159–162 + its BracketNode/SeriesMatch rows. Not part of the PR. Server
  stopped after testing.

---

# Web testing — LG-02c (round-robin → double-elimination)

Date: 2026-06-03
Branch: `lg-02c-rr-double-elim`
Scope: smoke-test the LG-02c RR→double-elim format — the `Round robin → Double
elimination` option + `rrde_combo` select on `/tournaments/create/`, the
seeding-stage standings with WB/LB/OUT cut markers + stage badge, the deferred
double-elim finals build, and play draining both stages to a champion; plus a
regression pass over single-elim / double-elim / round-robin on the shared
create/detail templates.

## Summary — LG-02c RR→DE

| Area | Result |
|---|---|
| Create form offers `Round robin → Double elimination`; `#tournament-create-rrde-combo` reveals (JS toggle) with the 6 combos `4/0…16/8` | ✅ |
| Create RRDE (8 existing teams, combo `4/2`) → tournament 8, setup; lock → "Seeding stage" badge | ✅ |
| Seeding standings cut markers exact: 4×`cut-wb` (ranks 1–4), 2×`cut-lb` (5–6), 2×`cut-out` (7–8) | ✅ |
| Play All (EAGER) drained both stages (~90s); deferred DE finals built; state → "Completed", `Champion: Echo Eagles` | ✅ |
| Finals tree renders all three sections — WB 3 nodes, LB 4, GF 2 (= 9 finals nodes, the expected `wb=4` count); seeding tables still shown | ✅ |
| No console errors, no non-2xx network requests across the flow | ✅ |
| Regression: single-elim (id 9) keeps legacy `#tournament-bracket`; double-elim (10) shows `#tournament-bracket-winners`; round-robin (11) shows `#tournament-rr-crosstable`; stage badge only on RRDE | ✅ |

## Bugs found & FIXED in this run

- **[High → fixed] Deferred DE finals never advanced winners (finals stalled, no champion).**
  `tournament_engine.py::play_next_node` flattened *all* `tournament.nodes`
  (including the resolved round-robin Seeding nodes) before `advance_winner`,
  which matches on `(bracket_round, position)` bracket-type-blind. An RR node at
  e.g. `(1,0)` (matchday 1) shadowed the WB-R1 node `(winners,1,0)`, returned an
  empty mutation (`advances_to=None`), and the `if win_muts` guard skipped the
  slot fill. Fix: `.exclude(bracket_type="round_robin")` on the elim-block
  flatten (step 8) and in `_collapse_drop_byes`. Surfaced by the Tests agent's 7
  red engine/task tests; all green after the fix.
- **[Low → fixed] Django `{# #}` comments rendered as literal text on the detail page.**
  Three multi-line `{# … #}` comments in `templates/matches/tournament_detail.html`
  (lines 86–89, 165–167, 215–216) printed verbatim on the page — Django's `{# #}`
  is single-line only. Fix: converted all three to `{% comment %} … {% endcomment %}`.
  Verified gone after a server restart + reload.

---

# Web testing — LG-02c (round-robin tournaments)

Date: 2026-06-03
Branch: `lg-02c-round-robin`
Scope: smoke-test the LG-02c round-robin surfaces — the `Round robin` option on
the `/tournaments/create/` format select (and the four series-length selects
hiding when chosen), the crosstable + standings render at `/tournaments/<id>/`
(instead of a bracket tree), and champion crowning only after every game.

## Summary — LG-02c round robin

| Area | Result |
|---|---|
| Create form — format select offers `Round robin`; the four `*-series-length` selects hide (`display:none`) when chosen | ✅ |
| Create RR (4 existing teams) → tournament 8, setup state | ✅ |
| Detail page renders `tournament-rr-crosstable` (N×N) + `tournament-rr-standings`, NOT a bracket tree | ✅ |
| Lock & Build → state `active`; crosstable shows `—` in unplayed off-diagonal cells, blank diagonal | ✅ |
| Drove 12 synchronous `Play Next` POSTs (= 4-team double round-robin: 3 opp × 2 legs × 4 / 2) → state `Completed` | ✅ |
| Champion crowned ONLY after all 12 games (not on the first resolved node) | ✅ |
| `tournament-champion-banner` shows `Champion: Phoenix` (Standings leader, 12 pts) | ✅ |
| Crosstable home/away mapping: leg1 → cell[a][b], leg2 → cell[b][a] (e.g. Phoenix↔Vipers fills both [P][V] and [V][P]) | ✅ |
| MP=6 per team, standings ordered by Pts→RW→TS | ✅ |
| Regression: `/matches/`, `/tournaments/` list render, no console errors | ✅ |

## Findings — LG-02c round robin

- **[FIXED this pass] Standings "Team" column rendered the raw `team_id`, not the
  team name.** `templates/matches/tournament_detail.html:135` rendered
  `{{ row.team_id }}` (`StandingsRow` carries only `team_id`). Fixed by pairing
  each row with its `Team` in `tournament_views.py::_detail_context`
  (`rr_standings = [(row, team_by_id.get(row.team_id)) ...]`, the LG-01
  `rows_with_teams` precedent) and unpacking `{% for row, team in rr_standings %}`
  in the template with a `team.name` (fallback `row.team_id`). Verified in-browser:
  standings now show Echo Eagles / Onyx Owls / Phoenix / Vipers. Test
  `test_rr_standings_context_has_one_row_per_team` updated to the `(row, team)`
  shape + strengthened to assert `row.team_id == team.id`.
- **[NOT A BUG — environment] `Play All` (async) hangs at "Starting…"** on the
  dev test server because no Celery worker / Redis broker is running. The inline
  poll JS correctly shows "Starting…" and polls `play-status`; the task simply is
  never consumed. Same dependency as elim `Play All`. Synchronous `Play Next`
  drains the bracket correctly. No code defect.

---

# Web testing — LG-02c (double-elimination tournaments)

Date: 2026-06-03
Branch: `lg-02c-double-elimination`
Scope: smoke-test the LG-02c surfaces — the new `Bracket format` select on
`/tournaments/create/`, the three-section double-elim render (Winners / Losers /
Grand Final) at `/tournaments/<id>/`, byes, the Drop into the losers bracket, the
bracket-reset Grand Final, and a champion; plus a single-elim regression check
that legacy DOM ids are unchanged.

## Summary — LG-02c

| Area | Result |
|---|---|
| Create form — `Bracket format` select (id `tournament-create-format`) offers Single / Double elimination | ✅ |
| Create (generate 6) double-elim → tournament 5, setup state, 6 participants | ✅ |
| Lock & Build → state `active`; three containers `tournament-bracket-{winners,losers,grand-final}` render; no legacy `tournament-bracket` | ✅ |
| DE node ids namespaced: `tournament-node-{winners,losers,grand_final}-{round}-{position}` (e.g. `…-grand_final-5-0` = GF1, `…-grand_final-6-0` = GF2) | ✅ |
| WB byes for N=6 (size 8): top 2 seeds show `Bye` / `Winner: …`, only 2 of 4 R1 nodes carry a Series | ✅ |
| Drove 11 synchronous `Play Next` POSTs → state flips to `Completed` | ✅ |
| Drop path: champion = Hyperion Hunters (seed 6) came up through the **Losers bracket** | ✅ |
| Bracket reset: LB champ won GF1 → GF2 (round 6) was **played** (not inert), LB champ won the reset → champion; `tournament-champion-banner` shows `Champion: Hyperion Hunters #14` | ✅ |
| Single-elim regression (tournament 6, locked): legacy `tournament-bracket` container + legacy node ids `tournament-node-{round}-{position}`, no `bracket_type` prefix, no "Winners Bracket" heading | ✅ |
| Console errors/warnings | ✅ none |
| Network non-2xx | ✅ none |

## Findings — LG-02c

- No bugs. All LG-02c surfaces behave per the seam contract: format select,
  three-section render, byes, Drop, and the conditional bracket-reset Grand Final
  (GF2 played because the LB champion won GF1) all correct; single-elim render is
  byte-unchanged.
- Note (pre-existing, NOT a LG-02c bug): the async `Play All` button stuck at
  "Starting…" because no Celery worker was running in the dev session — the
  LG-02a-2 async play-all path needs a broker+worker. The synchronous `Play Next`
  path drains the bracket fine. Unrelated to this task.

---

# Web testing — LG-02b-2 (per-Bracket-round Series escalation)

Date: 2026-06-03
Branch: `lg-02b-2-series-escalation`
Scope: smoke-test the LG-02b-2 surfaces — the four new series-length selects on
`/tournaments/create/` and the per-non-bye-node `Bo{n}` labels on the bracket
tree at `/tournaments/<id>/`, end-to-end (create generated 8-team field → set an
escalating ladder Final=Bo5 / Semi=Bo3 / QF=Bo1 / Earlier=Bo1 → lock & build →
verify per-depth labels → play one Match).

## Summary — LG-02b-2

| Area | Result |
|---|---|
| Create form — four selects render, DOM ids `tournament-create-{final,semifinal,quarterfinal,earlier}-series-length`, names map, all default `Best of 1` | ✅ |
| Old single `tournament-create-series-length` id removed (`getElementById` → null) | ✅ |
| Create (generate 8) + escalating ladder → tournament 4, setup state | ✅ |
| Lock & Build → state `active`, 7-node bracket built | ✅ |
| Per-node `Bo{n}` labels match depth: R1 (QF, depth 2) ×4 → `Bo1`; R2 (SF, depth 1) ×2 → `Bo3`; R3 (Final, depth 0) → `Bo5` (DOM ids `tournament-node-series-length-{br}-{pos}`) | ✅ |
| Existing series-score elements `tournament-node-series-score-{br}-{pos}` still render (`0–0`) | ✅ |
| `Play Next Match` on a Bo1 QF node → clinches on the first Match (`0–1`), engine reads `node.series_length` | ✅ |
| Console errors/warnings across create + detail | ✅ none |
| Network non-2xx | ✅ none |
| Bracket-tier headers now read "Bracket Round N" (the LG-02a 🟡 nit below is already resolved) | ✅ |

## Findings — LG-02b-2

- No bugs. All LG-02b-2 surfaces behave per the seam contract. Screenshot:
  `.claude/worktrees/lg-02b-2-bracket-escalation.png`.

---

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
