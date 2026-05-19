# Website Testing — Bugs & Issues

Manual exploratory test of the Laserforce Simulator web app using Chrome
DevTools MCP. Date: 2026-05-18. Server: Django dev server on
`127.0.0.1:8000`. Pages exercised: Teams list / homepage, Create Team,
Add/Edit Player, Assign Slots, Team detail, Matches list, Create
Tournament Match (simulated 1 vs 2), Match detail, Round detail, Event
log, Batch Sim (50 runs + save), Maps list, Map editor (Zones & Sight
Lines modes).

Severity legend: 🔴 High · 🟠 Medium · 🟡 Low · ℹ️ Note

## Summary

| ID   | Sev | Area | One-liner |
|------|-----|------|-----------|
| ~~T-3~~ | ~~🟠~~ | ~~Team detail~~ | ~~"Roster Status" Scout row renders `6 2 slots` (dead placeholder code in template)~~ _(fixed)_ |
| ~~T-4~~ | ~~🟠~~ | ~~Global nav~~ | ~~Navbar has no mobile toggler — unusable layout below 992px~~ _(fixed)_ |
| ~~M-1~~ | ~~🟠~~ | ~~Event log~~ | ~~HTML event log renders the entire log (~20.6k DOM nodes) with no server pagination~~ _(fixed: events emitted once as JSON; client-side windowed timeline + charts/playback decoupled from the DOM)_ |
| ~~T-2~~ | ~~🟡~~ | ~~Teams list~~ | ~~`7/6` players label on a valid roster is misleading~~ _(fixed)_ |
| ~~CT-1~~ | ~~🟡~~ | ~~Assign Slots~~ | ~~Form requires all 6 slots filled at once; no partial save~~ _(fixed)_ |
| ~~CT-2~~ | ~~🟡~~ | ~~Add Player~~ | ~~Profile number fields (Age etc.) have no min/max bounds~~ _(fixed)_ |
| ~~PD-1~~ | ~~🟡~~ | ~~Player detail~~ | ~~Stat category grouping doesn't match documented categories~~ _(fixed)_ |
| ~~PD-2~~ | ~~🟡~~ | ~~Player edit~~ | ~~A11y: missing form label / autocomplete attribute~~ _(fixed)_ |
| ~~M-2~~ | ~~🟡~~ | ~~Match list~~ | ~~Many stale `0-0 Tie` seed matches in history~~ _(fixed)_ |
| ~~BS-1~~ | ~~🟡~~ | ~~Batch Sim~~ | ~~Run ~8× slower than the ~25 ms/round figure in the docs~~ _(fixed)_ |
| ~~T-1~~ | ~~🟡~~ | ~~Global~~ | ~~`/favicon.ico` 404 on every page~~ _(fixed)_ |
| ~~E-1~~ | ~~ℹ️~~ | ~~Setup~~ | ~~3 unapplied migrations on fresh `runserver`~~ _(fixed: README already documents `migrate`; added a `makemigrations --check` CI guard)_ |

**Overall:** every core flow works — creating a team, adding/editing
players, assigning slots, creating & simulating a tournament match,
viewing match/round/event detail, batch simulation + save, and the map
editor all function with no server errors or JS exceptions. Findings are
one template bug (T-3), one responsive-layout gap (T-4), one scalability
concern (M-1), and a set of low-severity polish/UX/a11y items.

---

## Environment / Setup

### ~~ℹ️ E-1 — 3 unapplied migrations on a fresh server start~~ _(fixed)_
`python manage.py runserver` warned: *"You have 3 unapplied
migration(s)... matches"* (`0022_playerroundstate_missile_points`,
`0023_rename_seconds_active...`, `0024_gameround_rng_seed`). The app
misbehaves for anyone who pulls and runs without `migrate` first. Not a
code bug, but worth a setup note / CI guard. (Applied locally before
testing.)

**Fix:** the setup note already exists — `README.md` documents
`python manage.py migrate` before `runserver` (Option A), Docker
auto-migrates via `entrypoint.sh`, and CI already runs
`migrate --noinput`. The missing half was the CI guard: a
`python manage.py makemigrations --check --dry-run` step was added to
`.github/workflows/ci.yml` (before the migrate step) so a model change
shipped without its generated migration now fails CI.

---

## Teams list / Homepage (`/`)

### ~~🟡 T-1 — `favicon.ico` returns 404~~ _(fixed)_
Every page requests `/favicon.ico` → 404 (console resource error).
Cosmetic; add a favicon or a catch route.

### ~~🟡 T-2 — Teams list "7/6" label is misleading~~ _(fixed)_
Team **1** displays `Players: 7/6` with a green **"Valid Roster"**
badge. The detail page clarifies this is **6 active + 1 bench = valid**
(bench players are by design). The roster is correct, but `7/6` reads as
"over capacity": the numerator counts all players (incl. bench) while
the denominator is the 6 active slots — two different totals mixed.
Suggest `6/6 active (+1 bench)` so a valid roster never shows as `7/6`.

---

## Team detail / Player pages

### ~~🟠 T-3 — "Roster Status" Scout row renders "6 2 slots" (leftover placeholder code)~~ _(fixed)_
On every team detail page, the **Scout** row of the *Roster Status*
card shows `6 2 slots` instead of `2 slots`. Root cause is dead
placeholder code in `templates/teams/team_detail.html:112-116`:

```django
{% if role_code == 'scout' %}
    {% with scout_count=0 %}
    {{ active_roster|length }}<!-- placeholder -->
    {% endwith %}
    2 slots
```

`{{ active_roster|length }}` (= active roster size, 6 on a full team) is
printed right before the literal `2 slots`; the `{% with scout_count=0 %}`
block is unused. Confirmed it varies with roster size — a freshly created
team with 0 active players shows `0 2 slots`. Fix: delete the placeholder
lines, leaving just `2 slots`.

### ~~🟡 PD-1 — Stat category grouping on player detail looks jumbled~~ _(fixed)_
On `/<team>/player/<id>/` stats are grouped under headings that don't
match the documented categories (teams/CLAUDE.md). Observed: *Decision
Making* group contains Positioning, Adaptability, **Special Usage**,
**Survival**; *Physical* group contains **Communication**, **Accuracy**,
**Resupply Efficiency**. Per the model these are Role/Team/Physical
stats respectively. Cosmetic but confusing.

### ~~🟡 PD-2 — Accessibility warnings on player edit form~~ _(fixed)_
Console (DevTools Issues) on `/<team>/player/<id>/edit/`: "No label
associated with a form field" and "An element doesn't have an
autocomplete attribute". Minor a11y; add `<label for>` / `autocomplete`.
(Same autocomplete warning also seen on the Maps upload form.)

---

## Create Team flow

### ✅ Works
`/create/` → enter name → team created (success flash). "Add Player"
pre-fills a random profile, stats default 50, "Set All to
Average/Elite" presets work. Player added → lands on Bench as designed.
Edit Player correctly pre-loads existing values and shows a live
"Overall Rating". Assign Slots renders all 6 slot dropdowns.

### ~~🟡 CT-1 — Slots form requires all 6 slots at once; no partial save~~ _(fixed)_
On `/<id>/slots/` every slot `<select>` is `required`, so the browser
blocks submit ("Please select an item in the list.") unless all 6 slots
are filled in one save. A team with < 6 players can never have *any*
slot assigned, and progress can't be saved incrementally. (Side effect:
server-side duplicate-player validation can't be reached via the UI
because the client `required` check fires first.) Consider allowing
partial saves or messaging that 6 players are required first.

### ~~🟡 CT-2 — Profile number inputs have no min/max bounds~~ _(fixed)_
On Add/Edit Player, Age / Started playing age / Total games report
`valuemin=0 valuemax=0` (no real bounds) while the 19 stat fields
correctly use `0–100`. These accept arbitrary/negative numbers. Add
sensible `min`/`max`.

---

## Matches / Match creation

### ✅ Create Match — works end to end
`/matches/` → "New Tournament Match" → Red=1, Blue=2, Tournament →
**simulated successfully** (Match 22, "1 Wins!" 98444-55523). Match
detail, round detail (full per-player performance + resource summary,
no console errors), and event log all render. Team dropdowns correctly
**exclude** the incomplete "ChromeTest QA" team. Elimination bonus
(+10000/round) correctly reflected in totals.

### ~~🟠 M-1 — Event Log page loads the entire log into one DOM (no server pagination)~~ _(fixed)_
`/matches/game-round/<id>/events/` rendered **4533 events → ~20,600
a11y/DOM nodes** for a single 2-team, no-map round. The REST API
`/events/` is paginated (per matches/CLAUDE.md) but the HTML view is
not — every tag/move/miss/resupply row is emitted server-side into
scrollable panels. Longer rounds / map rounds (movement events explode
under MOVE-01) produce far more. Loads without error here, but will
degrade badly (slow render, large response) on big rounds. Recommend
server-side pagination or windowing for the HTML timeline.

**Fix:** the view now emits every event **once** as a compact JSON
list (`events_data` via `json_script`) plus a `players_data` block,
instead of one server-rendered DOM row per event. `game_round_events.html`
renders only a bounded **window** of the timeline client-side
(`WIN = 250` rows, Newer/Older pager) and feeds the same JSON array to
the kill feed (recency-capped at 250), the three charts, and the SIM-05
playback engine — nothing reads the DOM for event data anymore. The
playback engine auto-pages the window onto the current event and
click-on-row still jumps playback. DOM nodes are now bounded (~250 rows)
regardless of round length. JSON shape pinned by
`TestM1EventLogWindowing`.

### ~~🟡 M-2 — Many stale "1 vs 2 — Tie 0–0" matches in history~~ _(fixed)_
The match list shows numerous old `1 vs 2 … Tie / 0 - 0 / Rounds 0-0`
entries (Oct 17 2025). Likely stale seed data, but a `0-0` tie with
`0-0` rounds suggests matches persisted without a successful
simulation. Worth confirming the create-match path can't persist an
empty match when simulation fails.

---

## Batch Sim (`/matches/simulate-batch/`)

### ✅ Works
Ran 50 sims (1 vs 2): win rates, ties, per-team avg score/survivors,
map-side advantage table, score distribution all render correctly.
"Save Average Game(s)" worked and produced a "View Round 61" link.

### ~~🟡 BS-1 — Batch run much slower than documented~~ _(fixed)_
50 sims "completed in 20.54s" (~411 ms/game; ≈2 rounds/game ⇒
~205 ms/round). matches/CLAUDE.md states BatchSimulator runs a round in
"~25 ms" — this is ~8× slower on the no-map 3-zone path. May be an
outdated doc figure or single-worker default; worth confirming the
batch path isn't hitting the ORM or failing to parallelise (a 500-sim
run would extrapolate to ~3.5 min).

---

## Maps (`/maps/`, `/maps/<id>/editor/`)

### ✅ Works
Maps list shows both configured maps (Syracuse, San Marcos) with
Open Editor / Delete / Upload form. Map editor renders Original + B&W
Zone Map with grid overlay, all brush/legend/elevation/spawn tools, and
toggles cleanly between **Zones & Bases** and **Sight Lines** modes
(Compute/Save Sight Lines controls appear) with no console errors.

---

## Global / Responsive

### ~~🟠 T-4 — Navbar has no mobile toggler (unusable layout < 992px)~~ _(fixed)_
`templates/base.html:10` uses `navbar navbar-expand-lg` but there is
**no `navbar-toggler` (hamburger) button and no `collapse
navbar-collapse` wrapper**. Below the Bootstrap `lg` breakpoint (992px —
tablets/phones) the brand and the five nav links stack awkwardly with no
hamburger menu (reproduced at 720px wide). At ≥992px desktop the navbar
is fine. Add the standard Bootstrap toggler + `collapse navbar-collapse`
markup.
