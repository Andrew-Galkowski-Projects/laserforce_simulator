# Development Plan

Organized by phase. Phases 0–2 are prerequisites for later phases; don't skip ahead.
Story IDs from `sm5_user_stories_v2.html` are referenced where applicable.

This file holds only open work. Shipped stories, with their implementation notes, live in
[`PLAN-completed.md`](PLAN-completed.md), grouped by the same phase headings.

---

## Found Issues — UX & Live-Play Fixes (added 2026-06-25)

Surfaced from hands-on use of the running app. Grouped here at the top because they
are cross-cutting UX / live-play defects rather than new feature phases. The
simulation-mechanics bugs from the same review (start-of-game state, cell-occupancy,
goal-balling) were distributed into the **Phase 3 — Simulation Mechanics Backlog**
(see `MECH-15` / `MOVE-05` / `MOVE-06`), and the score-rebase ask into `CAL-01`
there. GEN-01, NAV-01, PLAY-01, DEL-01, CRE-01, CRE-02 and CI-01 are shipped (see
[`PLAN-completed.md`](PLAN-completed.md)); the deferred GEN-02 stub remains here.
PERF-01, PERF-02, PERF-03 and PERF-04 are shipped.

### GEN-02 · [DEFERRED to bottom — see "Parked — deferred compute-tier work" (2026-06-26)]

**Deferred (2026-06-26 grill).** Pushed to the bottom of this plan — too thorny to
build now, no easy path. Two blockers surfaced: (1) a compute-tier "upgrade" can't be
the GEN-01 `ensure_fidelity` pattern — a higher *compute* tier is a genuinely different
game, so an in-place upgrade must either rewrite the scoreboard (retroactive
Standings-shift, the ADR-0029 hazard) or leave an incoherent scoreboard-vs-replay; and
(2) the `scores`-compute statistical model is blocked on baseline `score_averages` data
that doesn't exist yet (entangled with the still-pending CAL-01 re-baseline). Full
write-up parked below.

PERF-01 (the five known N+1 read actions) is **[DONE]**; see `PLAN-completed.md`.

PERF-03 (narrow the Team history Season scan) is **[DONE]**; see `PLAN-completed.md`.

PERF-02 (speed up the CI Python test job) is **[DONE]**; see `PLAN-completed.md`.

PERF-04 (rebalance the CI test shards on CI timings) is **[DONE]**; see `PLAN-completed.md`.

PERF-05 (use a fast password hasher in tests) is **[DONE]**; see `PLAN-completed.md`.

PERF-06 (rebalance the CI shards after PERF-05) is **[DONE]**; see `PLAN-completed.md`.

TEST-01 (keep test media writes out of the real `MEDIA_ROOT`) is **[DONE]**; see `PLAN-completed.md`.

TOOL-01 (script the PLAN.md to PLAN-completed.md move) is **[DONE]**; see `PLAN-completed.md`.

---

## Phase 8 — Angular Frontend Migration

Replaces Django's server-rendered HTML templates with an Angular single-page application (SPA).
Django becomes a pure API backend; Angular handles all UI in the browser. This phase requires Phase 5's
API-02 (REST API) to be complete and deployed (Phase 7) before starting.

**Approach:** migrate one feature area at a time. Django templates remain live until the Angular
equivalent is complete and verified. Django Admin is a permanent exception and is never migrated.

### ANG-06 · Migrate views by feature area

Migrate one area at a time in order of complexity. Each item: build the Angular route/component,
verify feature parity with the existing Django template. The Django template + view stays: removal
is ANG-08 (owner, 2026-10-21 — until ANG-07 serves Angular, production is Django-only, and removing
a URL name raises NoReverseMatch site-wide). "Parity" means the same features, not the same look.
Every component uses the theme chosen in ANG-04b.

1. **[DONE] Teams list & detail** — simple CRUD table + form; good first Angular component to build
   (ANG-06a, in `PLAN-completed.md`)
2. **[DONE] Player add/edit** — stat form with live `overall_rating` preview (ANG-06b, in
   `PLAN-completed.md`)
2b. **[DONE] Generate Players + Import Roster** — the two Teams-list header actions, split out of
   ANG-06a (ANG-06 2b, in `PLAN-completed.md`)
3. **[DONE] Match list & create** — team picker, match creation, results list (ANG-06c, in
   `PLAN-completed.md`)
4. **[DONE] Round detail** — per-player stat table, MVP scores (ANG-06d, in `PLAN-completed.md`)
5. **[DONE] Event timeline** — filtered event log, color-coded by type (ANG-06e, in
   `PLAN-completed.md`)
5b. **[DONE] Playback** — scrubber, play/step/speed, live Side scoreboards, current row, beta map
   (ANG-06f, in `PLAN-completed.md`). RV-04's timeline pips and MECH-15's tick-0 frame attach here.
5c. **[DONE] Event charts** — four Shots / Lives / Points / SP charts with filters and Chart
   overlays (ANG-06g, in `PLAN-completed.md`). RES-05 and RV-04's chart toggle attach here.
6. **Map editor** — most complex: canvas overlay, zone painting, sight-line drag-select (migrate last)
   (ANG-06h). Spec: #355. **Grill settled (owner, 2026-10-25):**
   - **Scope:** the whole `/maps/` area — list (cards, upload, delete) and all three editor modes
     (Zones & Bases, Sight Lines, Heatmap). Routes `/maps` and `/maps/:id/editor`; a "Maps" shell
     nav link after Matches.
   - **Ownership / sim:** none. Maps stay shared reference data (ADR-0038); the heatmap's round
     scoping is already server-side. No engine, RNG or migration change.
   - **API gaps closed:** `ArenaMapSerializer` gains a nullable read-only `confirmed_zone_size` (list
     badge; update any exact key-set asserts). `GET /api/maps/` runs the same default-map seeding
     and image restore as HTML `map_list`, moved into `core/services.py`. New raw, un-enveloped
     `GET /api/maps/<id>/image/` (original image; `/media/` is behind the session gate), fetched
     as a blob into an object URL for list thumbnails and the editor's original-image panel.
   - **Drawing:** one canvas overlay on the processed image plus a pointer-events-none heatmap
     canvas; full redraw per change, coalesced to one `requestAnimationFrame` during drags;
     pointer events.
   - **Structure:** editor state logic ported from the inline JS into pure TS modules with their own
     specs (grid state, rect paint, bases, ramp, bidirectional LOS toggle, spawn-edited flag,
     100-key batch split); one component per mode plus the list, sharing an editor-state service.
     Canvas pixels untested (ANG-06f precedent); specs pin the payloads sent.
   - **Unsaved edits:** confirm before a zone-size change and on leaving the route
     (`canDeactivate`) when dirty (Django silently discards).
   - **Saves:** Save Configuration replaces local state with the PUT response, shows success; the
     UXP-10 missing-base warning stays non-blocking. Sight lines keep 100-key batches (PUT, then
     PATCH); a mid-run failure shows "Saved N of M batches — retry" and stays dirty.
   - **Colours:** the ~25 rgba literals become semantic `--lf-map-*` tokens (light + dark) plus
     `LF_TOKENS`, read via the ANG-06g probe, redrawn on a `matchMedia` theme flip; bases use the
     `data-lf-side` tokens.
   - **List:** upload success navigates to the editor; `required` / `max_length` 100 /
     `invalid_image` errors inline. Delete uses the Teams-list confirm pattern; 409 `map_in_use`
     shows the server message and keeps the card.
   - **Out:** Django template removal (ANG-08), the single-cell LOS endpoint, async compute,
     undo/redo, the deferred ANG-01c validations, `is_public`, any new editor tools.

**Note (ANG-04b, 2026-10-21):** ANG-06 builds to
[ADR-0043](docs/adr/0043-visual-theme-lfstats-layout-and-token-contract.md): layout B (lfstats-like)
structure, the `--lf-*` tokens and `data-lf-side` only (no hex in components), Material kept and
themed, round score = base + a separate muted `(+N elim bonus)`. Candidates left out of ANG-04b
(spec #246) for ANG-06 to scope: the MVP breakdown dialog, the player side sheet, per-player
expandable timelines, momentum and score charts (and choosing a charting library), replay controls
and log search, and position icons (roles stay CMD / HVY / SCT / AMO / MED until then).

**Note (ANG-01 re-slice, 2026-10-14):** the League / Season, League-screens and Tournament surfaces
will have full API coverage from ANG-01d-1 / ANG-01d-2 / ANG-01e / ANG-01f, but they are **not yet planned** as
ANG-06 migration items — add them (and the Accounts screens backed by ANG-01g) here once the six
items above are scoped.

### ANG-07 · Serve Angular from Docker (nginx sidecar)

Once Angular is built (`ng build --configuration production`), serve it via an nginx sidecar service.
nginx serves the Angular static files on port 80 and proxies `/api/` requests to the Django container
on port 8000. Add `nginx.conf` and update `docker-compose.yml` with the `nginx` service.

### ANG-08 · Remove Django template views

Once each Angular view is verified, delete the corresponding Django template file and its
HTML-serving view function. Keep the API endpoint. Update URL routing to remove the old path.
The app should have zero `.html` template files by the end of this phase, except Django Admin
(which is a permanent exception and stays indefinitely).

### ANG-09 · [NOT STARTED] Investigate a standalone single-player executable

**Investigation only — no shipped code.** Find out whether single-player mode (sandbox + single-player
`league` / career mode) can be handed out as one downloadable executable that runs locally, with no
hosted server. The deliverable is a spike plus a go / no-go ADR. If the answer is go, the ADR names the
follow-up tasks. Starts after ANG-08, so the frontend is the built Angular SPA and Django is API-only.

Questions to answer:
- **Packaging:** PyInstaller, Briefcase or Nuitka for the Django + DRF backend and its native
  dependencies. Measure binary size and cold-start time.
- **Background jobs:** batch runs and the Season play loop use Celery + Redis (ADR-0013 / ADR-0016).
  Find an in-process replacement (a thread / process pool or an eager mode), so the user doesn't have to
  run a broker.
- **Shell:** a native window (pywebview, Tauri or Electron) vs. opening the default browser to a
  localhost port. How do the built Angular static files get served (Django + whitenoise, or the shell)?
- **Database:** an embedded SQLite file in the user's app-data folder. This conflicts with ADR-0025
  (PostgreSQL canonical, SQLite dev-only). Decide whether a desktop-only SQLite exception is acceptable,
  and how `migrate` runs on first launch and on upgrade (ADR-0004: simulation data is disposable).
- **Auth:** a single local Account created and signed in automatically. ANG-03 JWT vs. a local bypass.
  Hide the `multiplayer` mode and the Account screens.
- **Runtime:** simulation parallelism under a frozen binary (`multiprocessing.freeze_support`, process
  pools), the bundled map / media files, and where logs go.
- **Distribution:** Windows first. Cover code signing / SmartScreen, auto-update vs. manual download,
  and macOS / Linux feasibility.

## Phase 3.5 — LFstats realism (added 2026-10-21)

Follow-ups to the LFstats player import (#256). The import turns real players' LFstats numbers
into the 19 Player stats. The formulas, the meaning of each stat and the sim oddities found
along the way are in [`docs/lfstats-import.md`](docs/lfstats-import.md). The rebuild tools are
`scripts/lfstats_import/` and the data is `data/lfstats_snapshot_2026-10-21.json`. Scheduled
directly after the Phase 8 Angular work (owner, 2026-10-21); LFS-01 must still be sequenced with
CAL-01. **Open question for both:** run LFS-02 first? Mechanics changes from LFS-02 would
invalidate any LFS-01 tuning done before them.

### LFS-01 · Tune the numbers so imported players reproduce their LFstats results

**Goal:** a simulated imported player's per-position outputs land close to their real LFstats
numbers: accuracy, hit diff, times hit, MVP, uptime, elimination rate, resupplies, nukes,
missiles and rapid fire. Today the formulas are fixed guesses that were never checked against
sim output.

**Known gaps:**
- **Hit chance saturates.** The sim's hit chance is `70 + accuracy - defender.survival`
  (`sim_helpers/shot.py`, clamped 10-95). Imported accuracy is raw LFstats accuracy (median
  77), so a median-vs-median shot hits about 94%, against about 75% in real games.
- **Accuracy varies by position** (scout 66%, medic 90%), but `Player` holds one value.
  Per-role stats need a model change.
- **Placeholder stats:** resource_awareness, stamina, speed and flexibility are written as
  50 for everyone.

**Approach:**
- Build a calibration harness, in the style of `score_averages`: run N seeded rounds with the
  imported teams and print sim vs LFstats per position and metric.
- Decide in the grill which side moves: the import formulas (`compute.py` and the doc,
  together), the sim mechanics, or both.
- Decide the target metrics and tolerances.

**Depends on / interacts with:**
- CAL-01 (Score Calibration re-baseline). Changing mechanics shifts the scores.
- IMPORT-01 (`.tdf` parser). TDF event logs are the planned source for the deferred stats:
  resource_awareness from elimination order and shot outages, stamina from late- vs
  early-game tag rate.

**Acceptance:**
- The harness exists and its results are recorded.
- Tolerances are documented.
- `compute.py`, `docs/lfstats-import.md` and the CSV are updated together.

### LFS-02 · Suggest app changes from Kodama's "How to Improve in SM5" talk

**Goal:** use the talk ([video](https://www.youtube.com/watch?v=SngT6ipJsYU); summary in
`docs/lfstats-import.md`) as a domain reference for where the sim and the Player model differ
from how SM5 is really played. **Output:** a written, prioritised proposal (doc or ADR draft),
not code. Each accepted suggestion becomes its own PLAN entry.

**Starting points found during the import:**
- **Unused stats get real effects.** Six stats are never read by the sim: positioning,
  flexibility, adaptability and the offensive/defensive/midfield synergies. Possible effects:
  - positioning: choosing control zones over neutral ones ("why am I here?");
  - flexibility: body angles and missile dodging (today the dodge comes from survival);
  - adaptability: performance when losing or with unfamiliar teammates.
- **resource_awareness has no effect** (`_observe_lives` is a dead end). The talk's "look at
  your gun" suggests an aggression range set by your own shots and lives, plus retreat
  timing: about 9-13 lives is safe, 6-7 means head home, adjusted for the nukes the enemy
  commander is likely holding.
- **decision_making** means decisiveness in the talk (act within about a second). In the
  sim, `_stress_fail` looks inverted: a higher value raises the support fail chance.
- **Sprinting vs jogging.** Speed trades against awareness, because sprinting misses
  information. Stamina's second-half fade already matches the talk.
- **Communication:**
  - nuke call-outs remove teammates' 1-3 s reaction delay;
  - sharing shots and lives changes how teammates play;
  - calling downs with a location ("commander down at big box").
- **Mechanics:** laying trails while down, stubborn zone control (about 3 resets before
  leaving) and missile-dodging technique.
- **overall_rating weighting.** It is a plain mean of 19 stats; the talk's pie chart puts
  game sense largest and mechanical skill around 5%.

**Overlap to reconcile:** MECH-08 (reset timing), MECH-10 (follow rule), MECH-13 (per-player
information table) and MECH-14 (memory/comms-driven behaviour).

**Acceptance:**
- The proposal is reviewed.
- Each suggestion is mapped to an existing MECH item or a new PLAN entry, or rejected with a
  reason.

---

## Parked — deferred Tournament work

Deprioritised to the end of the plan (maintainer decision, 2026-06-04). The
shared player-pool foundation this builds on — Random Draw (LG-02x-1) — is
shipped; this is the last remaining LG-02 Part-1 slice, intentionally pushed
below all other planned work.

- **LG-02x-2 · [NOT STARTED] Duos / Trios (+ `TournamentSubGroup`) — needs its own
  grill.** The second player-pool slice, deferred from LG-02x-1. **Duos / Trios** —
  players register as **pairs / triples** placed on 6v6 teams alongside other groups,
  with sub-group performance tracked **independently** of the full-team result via a
  new **`TournamentSubGroup` model** (links players as partners within a specific
  tournament) + **per-subgroup stat aggregation**. *Why deferred:* LG-02x-1 shipped the
  single-Player pool (intake + tier-balanced draw + per-Round roles) as the foundation;
  Duos / Trios add a fundamentally different unit — a *bonded sub-group* that must be
  kept together by the draw and have its own stat rollup — which the LG-02x-1
  `TournamentPlayerEntry` (one row per *individual* Player) does not model. Grill the
  sub-group registration + group-aware draw + per-subgroup-stats domain before building;
  it composes the LG-02x-1 draw model rather than replacing it.
- **LG-02 Part2c · [DEFERRED] Mid-season `random_draw` re-draw — own slice.** The only
  remaining Part2c follow-up (the rest of LG-02 is in `PLAN-completed.md`). A pre-selected
  per-League option to **randomize the mid-season tournaments per season**: the
  non-season-ending `tournament` phases that sit before the main `round_robin` + the
  end-of-year tournament are re-drawn (format / seeding) each cycle by `next_season`
  instead of carried forward verbatim. Selected beforehand as a League-level toggle.

---

---

## Phase 5.6 probability features

### PR-01 · Pre-match win probability forecast

`/matches/forecast/?red=<id>&blue=<id>` — triggers 100-sim batch (requires SIM-02 and STAT-02). 
Shows win% per team, projected score range (10th–90th percentile), projected avg survivors, per-player risk flags.

### PR-02 · Roster composition comparison

Two side-by-side roster selectors vs same opponent, each running 100 sims. Side-by-side win%, avg score, avg survivors.
Recommended scenario highlighted with rationale.

### PR-03 · What-if scenario editor

Fork a real `GameRound`, change one variable (swap role, adjust stat, change player), 
re-simulate, show diff vs original. Forked scenario is temporary, not a permanent Match record.

---

---

## Sequencing Summary

Updated 2026-10-21. Phases 0–2, 5, 5.5, 6 and 7 are complete, as are the Found Issues at the
top of this file. Phase 8 is the critical path. Everything else is an unscheduled backlog.

**Current — Phase 8 (Angular Frontend Migration).** The API half (ANG-01..ANG-04) is done, and
so is the look (ANG-04b, ADR-0043) and the client layer (ANG-05). Remaining, in order:

```
ANG-06  Migrate views by feature area (Teams, Players, Matches, Round detail, Event timeline, Playback [done] -> Event charts [done] -> Map editor)   <- NEXT
        + still to scope: League / Season, League screens,
          Tournaments, Accounts
    -> ANG-07  Serve Angular from an nginx sidecar
      -> ANG-08  Remove Django template views (Admin stays)
        -> ANG-09  Standalone single-player executable spike (go / no-go ADR)
          -> LFS-01 / LFS-02  LFstats realism (Phase 3.5, order between them still open)
```

**Backlog — unscheduled, no fixed order.** Each item needs its own grill before it is picked up.
Mechanics items can run in parallel with Phase 8, because they don't touch the frontend. Analytics
items that add pages (RV-04, RES-05, HX-02) should wait for ANG-06, so they are built once, in
Angular.

- **Phase 3 — Simulation Mechanics:** MECH-08..MECH-16, MOVE-05, MOVE-06, MOVE-07, CAL-01, then MECH-07
  last (its design is still open).
- **Phase 4 — Analytics:** RV-04, RES-05, HX-02, HX-03, IMPORT-01.
- **Other:** LG-06, LG-07b, SIM-04, STAT-03, STAT-PROXY-01.
- **Phase 5.6 — Probability features:** PR-01, PR-02, PR-03.

**Parked, below everything:** GEN-02 (compute tiers), LG-02x-2 (Duos / Trios) and the LG-02
mid-season `random_draw` re-draw. See also
**Deferred Items** below.

---

## Deferred Items

The following were explicitly scoped out and should not be implemented until re-evaluated:

- **Mirrored/reflective walls** (MAP-07) — shot-bouncing mechanic; deferred from Phase 1
- **Per-stat-per-role weight tuning** (STAT-02 follow-up) — granular multipliers per stat per role;
  deferred until baseline simulation data exists to inform the values
- **Google/OAuth social login** (UX-01) — deferred from Phase 6; email/password only for now
- **Revoke refresh tokens on password change** (ANG-03) — a password change keeps the Account's
  outstanding refresh tokens valid until they expire (7 days). Blacklisting every `OutstandingToken`
  for the Account on change is the fix; access tokens already expire within 15 minutes
- **Custom domain** — deferred until the project grows; fly.dev subdomain is sufficient for now
- **Goal-recompute throttling** (MOVE-04) — behavioural perf lever (staler goals every *N* ticks);
  out of MOVE-02 scope, opened only if the MOVE-02 path cache alone is insufficient for the
  map-mode perf target
- **Per-fixture map override** (CONF-06 follow-up) — the "with a per-fixture override option" half
  of the original CONF-06 sentence, cut from the shipped slice: a `ScheduleFixture` is a plain
  dataclass regenerated on demand by `generate_schedule`, not a persisted row, so there is nothing
  to hang an override column on. Reopening it needs its own model keyed on synthetic fixture
  identity (Season + matchday + round number + both team ids — the `random_per_round` seed tuple)
  plus an authoring surface to set and clear it; deferred until a concrete need for pinning a
  one-off arena to a single fixture appears
- **Password reset / account recovery** (UX-01 follow-up) — the four `password_reset*` views, URLs
  and templates are deliberately **not** defined, not routed and not linked; they need a mail
  provider the deploy does not have, so they are deferred alongside OAuth. Only login / logout /
  register / password-change shipped. Recovery today is the operator command
  `python laserforce_simulator/manage.py changepassword <email>`
- **League membership, invitations and joining** (UX-01, struck from the original UX-01 wording) —
  the "invite-only / send invitations to specific users" half. UX-01 ships **only** the dormant
  `League.visibility` column (`closed` / `open`, default `closed`) plus one create-League control;
  **nothing reads it**. Reopening it needs a membership model (Account × League with a role), an
  invitation model with an accept/decline surface, a join path for `open` Leagues, and a real read
  of `visibility` in the permission seam — at which point the **Unmanaged-row** rule (NULL = open to
  every Account) must be tightened too. See
  [ADR-0038](docs/adr/0038-accounts-and-uniform-manager-ownership.md)
- **Cross-Account sharing** (UX-01, struck from the original UX-01 wording) — the "read-only access
  to others" half. UX-01 refuses cross-Account access outright (**404, never 403**); a genuine
  sharing feature needs an explicit *viewable by others* marker on the Ownership root plus a
  read-vs-write split in `accounts.permissions.is_owned_by`, which today is a single boolean gate
  covering both. Deferred on the maintainer's call: content is private until an explicit sharing
  feature exists
- **Per-`ArenaMap` `is_public` ownership** (UX-01 follow-up) — `ArenaMap` and the six map-config
  models were deliberately left **outside** the ownership axis (no `manager`, no filtering on any
  `/maps/` route) because `is_default`, `Season.map_mode`, the CONF-06 **Map pools** and
  `rotate_by_matchday` all reference maps across League boundaries, so a privately-owned map would
  silently break another Manager's rotation. A per-map `is_public` flag is the correct end state and
  needs the cross-League referencing rules settled first (what happens to a running rotation when a
  referenced map goes private)
- **Self-insert badge in JS-drawn views** (UX-02 follow-up) — the "You" badge is a server-side
  template tag, so it covers only server-rendered Player names. The round **playback map** and its
  play-synced event feed (`game_round_events.html`), the movement-heatmap roster, the
  `play_week_live.html` live-play feed and every chart build their Player labels in JavaScript from
  `json_script` / `|escapejs` data and show no badge. Reopening it needs `self_insert_player_id`
  shipped to the page (e.g. one `json_script`) plus a per-view JS hook to decorate the matching label
- **Self-insert badge on the PLAY-01 live leaders card** (UX-02 follow-up) — the live-dashboard
  fragments are re-rendered by `matches.league_views._render_live_play_panels` through
  `render_to_string` with **no request**, so the context processor never runs and the badge drops off
  a JS-patched leaders card mid-run (it returns on reload). Fix by passing `request=` (or the
  `self_insert_player_id` key) into that `render_to_string` call, checking the poll endpoint's query
  budget
- **Ownership-filtered head-to-head / career / benchmark corpora** (ANG-01b follow-up) — the HX-03 /
  HX-04 round sets (`_h2h_matches_qs`, `_h2h_rounds_qs`, `_player_h2h_rounds_qs`) and the HX-01 career
  round set are **not ownership-filtered** (the HX-02 benchmark population was scoped by UXP-14), in HTML or the API: a
  foreign Account's Match between two Unmanaged Teams can appear in `detail_list` (scores plus round
  ids, which then 404). Fixing it means passing `user` into the `matches.services` /
  `teams.services` builders and filtering by `owned_match_q` / `owned_game_round_q`; it changes HTML
  output, so it was held back to keep ANG-01b's byte-identical guarantee
- **HTML batch save trusts session team ids** (ANG-01b follow-up) — the HTML `save_batch_games` reads
  the team ids the status poll wrote into the session from its query params, with no ownership check.
  The API save is unaffected (teams in the body, resolved by `resolve_batch_teams`); the HTML path
  could reuse `resolve_batch_teams` the same way
- **Map zone-config shape / bounds checks** (ANG-01c follow-up) — `ZoneConfigForm` has no
  cross-field check that `elevation` matches the `zones` grid shape, and no Form or service checks
  spawn cells, Heavy strong-spot cells or base-sight cells against the grid bounds (an out-of-grid
  pair is stored as-is; `compute_single_cell` already returns `[]` off-grid). Fix with a grid-aware
  `clean()` once the zone grid is resolvable inside the Form
- **Strict `wall_meta` may reject a legacy re-save** (ANG-01c follow-up) — `ZoneConfigForm` accepts
  only `facing` / `height` keys per wall cell, so a stored legacy config carrying any other key fails
  to re-save from the editor (400) until the wall is repainted. Low risk (the editor only writes those
  two keys); relax to ignore-unknown-keys if one ever surfaces
- **No fired check on new-team candidates / reassign** (ANG-01d-1 follow-up) — neither the HTML
  `new_team_picker` / `reassign_team` nor the API `new-team-candidates` / `reassign-team` checks that
  the Manager was actually fired, so a career Manager in good standing can switch teams between
  Seasons. Kept for HTML parity; gate both on the latest `OwnerEvaluation.verdict == "fired"` if it
  matters
- **League team schedule accepts any owned Team** (ANG-01d-1 follow-up) — HTML `team_schedule` and
  `GET /api/leagues/<pk>/teams/<team_id>/schedule/` resolve any Team the requester owns, enrolled in
  the displayed Season or not (an un-enrolled Team gets an empty schedule). Kept for parity; add an
  enrolment check if a 404 is preferred
- **History API does not pin the in-progress Season** (ANG-01d-1 follow-up) — the HTML League
  history repeats the in-progress row at the top of every page; the API lists it once (first item of
  page 1). Deliberate; revisit only if the Angular history screen needs the pinned row
- **`LeagueSerializer.active_season` is one query per League** (ANG-01d-1 follow-up) — acceptable at
  page size 20; prefetch non-completed Seasons if the League list grows
- **A never-consumed Tournament play run stays stuck** (ANG-01f-6 follow-up) — a sandbox
  Tournament's run guard releases `active_play_job_id` only when `AsyncResult` reports `SUCCESS`,
  `FAILURE` or `REVOKED` ([ADR-0040](docs/adr/0040-single-active-play-run-per-season.md),
  Tournaments). A Play All message that no worker ever consumes reads `PENDING` forever, so that
  bracket's play-next and play-all stay a 409 `play_in_progress`. The fix is a Tournament Cancel
  (the Season `request_play_cancel` pattern: set a cancel flag and release a `PENDING` run with an
  update filtered on its id)

---

## Phase 4 — Highlight Surfacing & Chart Overlays (added 2026-05-21, post-RV-02)

Frontend-only follow-ons that reuse data already persisted/logged by earlier work — no new
simulation, no migration. Both build on the existing `game_round_events.html` infrastructure
(M-1 JSON windowing, the SIM-05 playback engine, and the RES-02 `_overlay_plugin` Chart.js v4
vertical-overlay pattern).

### RV-04 · Highlight overlay on the playback timeline + chart toggle

Surface the RV-02 **Highlight** list (`GameRound.highlights_json`) in two more places beyond the
Highlights tab:

- **Playback timeline (SIM-05):** mark each Highlight at its tick on the playback scrubber / event
  timeline (a coloured pip per `kind`, reusing the `OVERLAY_KIND_STYLE` palette extended for the
  RV-02 kinds — `nuke_detonation`, `nuke_cancelled`, `medic_reset`, `first_elimination`,
  `team_elimination`, `scoring_burst`). Clicking a pip jumps playback to that tick;
  the currently-playing Highlight is indicated. No new backend — `highlights_json` is passed to the
  page via `json_script` alongside `events_data`.
- **Chart toggle:** an optional overlay on the four event-page charts (`chart-shots`, `chart-lives`,
  `chart-points`, `chart-sp`) drawing one vertical line per Highlight, coloured by `kind`, label =
  kind + player/team — using the **existing** RES-02 `_overlay_plugin` registration path (inline
  `plugins:` array, `drawOverlays` mutating the closure-captured overlay list). A "Highlights" toggle
  in the chart filter UI mirrors the existing elimination/special/nuke overlay toggles exactly.

**Scope:** read-only/derived; no model change, no migration, no simulator change. Depends on RV-02
(`highlights_json`). **Acceptance:** every Highlight in `highlights_json` appears as a timeline pip
and (when toggled) a chart overlay line at the correct tick; toggling Highlights off restores the
prior chart appearance; clicking a timeline pip scrubs playback to that Highlight.

### RES-05 · Medic-hits overlay on the event-page charts

Add **medic hits** as a toggleable overlay on the four event-page charts, reusing the RES-02
`_overlay_plugin` pattern. The exact definition of "medic hit" is to be pinned during the grill
(candidates: every `tag` row whose **target** is a **Medic**; the **medic-under-fire alert** moments
— a Medic tagged 2× within `MEDIC_ALERT_WINDOW_TICKS`; or hits *landed by* a Medic) — the data is
already in the event log (`tag` rows carry actor/target roles in `metadata`), so this is a
client-side scan + overlay with no backend change. A "Medic hits" toggle joins the existing chart
filter toggles.

**Scope:** frontend-only; no model change, no migration, no simulator change. Depends on RES-02
(chart + overlay-plugin infrastructure). **Acceptance:** toggling "Medic hits" marks each qualifying
event on the charts at the correct tick and toggling it off restores the prior appearance; the
definition chosen in the grill is documented in GLOSSARY.md if it introduces new domain language.

---

## Phase 3 — Simulation Mechanics Backlog (added 2026-05-21)

Mechanics and decision-making items captured from working notes. These extend the MECH / MOVE
families and the role-aware goal selection work (MAP-05). None are scheduled yet — each carries an
open question or design dependency that must be resolved before implementation. Items are ordered
roughly by readiness; MECH-07 (goal-selection rework) is intentionally last because its shape is
still undecided.

### MECH-08 · Reset-timing miss penalty

Players currently have no notion of *when* a downed enemy will turn back on, so they cannot mistime a
shot. Add behaviour where a player attempting to tag a reset target can fire **too early** — before
the target reactivates — and waste the shot. The miss should fall out of imperfect timing rather than
the existing hit-chance roll.

**Open question:** which stats drive the timing estimate? Candidates already on the model —
`game_awareness` (already gates the MECH-02 reset filter), `nuke_awareness`/reaction-style stats, and
possibly a new dedicated stat. Resolve which stat(s) feed the early-fire probability before wiring.

### MECH-09 · Reset re-tag action/goal

For reset handling, lean on the existing LOS infrastructure (MAP-03) and the per-tick candidate
filters rather than the abstract zone check. Add an action/goal so a player actively **looks for a
reset opportunity to re-tag a downed enemy** once it reactivates, using `SightLineConfig` for
eligibility and the appropriate target filters. Pairs with MECH-08 (timing) and builds on the MECH-02
`last_tagged_id` reset-target machinery.

### MECH-10 · Follow rule — cap pursuit of downed players

Medics are dying within ~4 minutes because players follow a downed target indefinitely. Add a
**follow rule**: a player cannot follow a downed player more than **10 squares along the downed
player's path**. The path is modelled as a hallway (corridor spread) that starts at the square where
the player was downed and extends until the player turns back on. Pursuit beyond the 10-square limit
is disallowed, which should give Medics survivable breathing room.

**Open question:** corridor width / spread of the "hallway" and how it interacts with LOS and walls
(MAP-07) still needs pinning.

### MECH-11 · Crouch mechanic + stamina cost

Add a **crouching** mechanic that makes a player un-hittable over a **half wall** (the low-wall type
from MAP-07). To prevent continuous abuse, crouching **drains stamina** — either disallowing
sustained crouch outright, or applying a **movement penalty** when stamina is depleted. Touches the
hit-eligibility path (low walls currently block movement but not sight) and the stamina schedule.

**Open question:** which lever — hard stamina gate vs. movement-penalty-on-empty — and whether
stamina here reuses the existing proportional stamina schedule or needs a separate pool.

### MECH-12 · High-ground / half-wall sight-line falloff formula

Rework the high-ground LOS formula (MAP-09) so elevation does **not** grant a clean look at everything
directly below a half wall. Behaviour: a player on elevation should **not** see the cells directly
below a half wall unless **close to the wall**. The farther the elevated player stands from the half
wall, the more of the near sight lines below the wall are removed; farther still removes more. The
falloff should follow a **triangle-type formula** (sight removed grows with distance from the wall).

**Status:** this is a formula rework of the MAP-09 shoot-over / `SightLineConfig` computation, not a
new subsystem. Lands in `compute_sight_lines` / `_has_los` (the `can_shoot_over_wall` path).

### MECH-13 · Per-player information table (imperfect information)

Players currently act on **perfect information**, which is incorrect — each player should decide using
only what they personally know. Add (or fully wire) a **per-player information table** that informs
decision-making, so choices are made against believed/last-known state rather than ground truth.

**Status:** a per-player view already exists via the MECH-06 `player_memory` dict (transient, staleness
thresholds per role). Unclear how much of decision-making actually consults it today — audit current
usage in goal/target selection, then route remaining perfect-information reads through the table.

### MECH-14 · Memory/comms-driven adaptive role behaviour

Now that memory (MECH-06) and communication are implemented, players should **change what they do**
based on new information they receive, rather than following static role scripts. Concrete behaviours
to encode:

- **Scouts** push in past the Heavy when the Heavy is down.
- **Commander** takes space when the Heavy is down.
- **Ammo** can resupply the Heavy for free when the Commander is down.

These are conditional goal/action overrides keyed off teammate-status memory; they extend the MECH-06
broadcast/memory hooks and feed into the role goal selection (MAP-05 / MECH-07).

### MECH-15 · Persisted start-of-game (tick-0) state event

**Prio: Medium (found 2026-06-25).** The event log has no authoritative opening
frame — it begins at the first action, so the replay/playback surfaces cannot show
where players actually started or their initial resources. Add a **persisted tick-0
`GameEvent`** (a new `event_type`, e.g. `game_start`, added to `EVENT_TYPES` via
migration — the RV-02 `0027 AlterField` precedent) recording, for every player, their
**spawn cell** and **initial resources** (lives / shots / special / missiles). Emitted
via the `EventLog` / `flush_to_db` path so it lands on every save path. Gives
round-playback and the LG-01i live-watch an authoritative opening state instead of
reconstructing it. **Locked (grilled 2026-06-25):** persisted event, **not** a
playback-only derivation. Pairs with the replay system but is logged for *all* rounds.

### MECH-16 · Missile spent twice on a hit (bug)

**Prio: Medium (found 2026-10-23, ANG-06f probe).** A missile is spent at lock
(`combat.start_missile_lock`) and spent **again** when it hits (`_complete_missile`:
`attacker.final_missiles -= 1`, `entrypoints.py` ~L1838). Some players end a round with
`final_missiles == -1`. Spend it once, at lock. Add a regression test that `final_missiles` never goes
below 0 and equals starting missiles minus locks. **Changes seeded outcomes**, because a missile
holder may lock again sooner, so it folds into the `CAL-01` re-baseline. ANG-06f's `round_playback`
mirrors the double spend so its final-state invariant holds. When this lands, drop the extra −1 per
`missiled` hit there too.

### MOVE-05 · Enforce cell occupancy (no two players end a tick on the same cell)

**Prio: Medium (found 2026-06-25).** Players sometimes **end a tick on the same cell
as another player**, which should be impossible. Enforce single-occupancy at the
**destination** cell in the movement path (`BatchSimulator._move_player_in_memory` /
`astar_advance_cached`, `sim_helpers/pathfinding.py`): a player may not finish an
Advance on a cell already occupied by another player — claim/skip the occupied target
and resolve to the nearest free cell along the committed route. **Open questions for
its own grill:** hard block vs allow transient mid-Advance pass-through but forbid
end-of-tick co-occupancy; whether occupancy is enemy-only or also same-team; tie-break
when two players target the same free cell the same tick. Consumes/perturbs movement
resolution → folds into the `CAL-01` re-baseline.

### MOVE-06 · Goal-location noise to reduce balling-up

**Prio: Medium (found 2026-06-25).** Players **ball up** because role-aware goal
selection converges too tightly on the same target cells. Add **noise** to
`choose_goal_cell` (`sim_helpers/pathfinding.py`) so goal locations spread out — e.g.
sample among the top-N candidate cells rather than always taking the argmax, or
perturb the chosen goal within a small radius. Reactive overrides (MECH-04
nuke-reaction, critical-resource, `seek_medic`) stay deterministic; only the
steady-state positioning layer gains jitter. **Consumes RNG** → shifts seeded
outcomes; folds into the `CAL-01` re-baseline (no separate obligation).

### MOVE-07 · Varied pathing + action-choice sweep

**Prio: Medium (found 2026-10-23, ANG-06f grill).** Watching the playback map overlay, the
routes look too standard: every player walks the textbook A* shortest path, so movement is
predictable and samey. Two parts:

- **Pathing variation.** Give routes human-looking variety instead of always the single shortest
  A* route (`sim_helpers/pathfinding.py`, `astar_advance_cached`, **Path commitment**). Candidates:
  pick among near-shortest routes, add per-player cost noise or role/stat-driven preferences
  (cover-hugging, flanking, high ground), and vary the pace along a route. This differs from
  `MOVE-06`, which spreads the *goal* cells; MOVE-07 varies the *route* to a goal.
- **Full sweep of the action-choice logic.** Review the weighted **Action** selection end to end
  (weights, role scripts, carry-over of hide / hold, how memory and comms feed in) for flat or
  unrealistic behaviour, and fix what the sweep finds. This overlaps `MECH-07`'s action-selection
  half and `MECH-14`; settle in this task's grill whether MOVE-07 absorbs them or stays a sweep
  that feeds them.

**Open for its own grill:** how much variation, and from which inputs (RNG noise vs player
Stats); whether Path commitment still holds a route once it has been picked; how to judge
"varied enough" (the playback map overlay plus the movement heatmap). **Consumes RNG** and
shifts seeded outcomes, so it folds into the `CAL-01` re-baseline. The ANG-06f map overlay ships
as **beta** partly so this work can change what it shows.

### CAL-01 · Score Calibration re-baseline

**Prio: Medium (found 2026-06-25).** Rebase the map-model average scores toward the
documented **Score Calibration Targets** (Commander 9,952 / Heavy 6,482 / Scout
5,102 / Ammo 3,242 / Medic 2,282 — `docs/agents/matches/simulation-engine.md`). This is the long-pending
post-MOVE-01 re-baseline and **absorbs** the seeded-outcome deltas from MOVE-05 /
MOVE-06 (and SIM-12) in a single pass — do **not** create separate re-baseline
obligations for those. **Locked (grilled 2026-06-25):** **tune** the existing action
weights / hit-chance / movement constants to converge on the targets; keep the 19-stat
model + role MVP/weight formulas as-is for now. The **deeper rework** the user wants
scheduled later — revisiting action selection, movement selection, and goal selection
themselves — is tracked by `MECH-07` below (extended to cover actions/movement, not
just goals); do that only if calibration tuning alone cannot hit the targets.

**Follow-up (SIM-12 / RV-05 retro, 2026-10-22):** de-flake
`test_strong_team_winpct_not_diluted_by_alternation` (`test_batch_sim.py`). It
asserts one master seed's batch, and has been hand-tuned twice (threshold 58% ->
55%, then seed 7777 -> 2 in SIM-12); six seeds spanned 43-63%. Either assert the
mean across several seeds, or move it behind a `calibration` pytest marker run in
a non-blocking CI job.

### MECH-07 · Role-aware action / movement / goal-selection rework (MAP-05 follow-up)

Make changes to role-aware goal selection (MAP-05) — and, per the 2026-06-25 review,
the broader **action-selection and movement-selection** layers it sits on (the deeper
rework `CAL-01` defers to once calibration tuning is exhausted). Shape is **still
being worked out** — scope and acceptance criteria are deliberately deferred.

**Status:** TBD — intentionally sequenced **last** in this batch until the design is settled.

---

---

## Phase 4 — Individual Performance & PDF Graphs (added 2026-05-22)

Three analytics/export follow-ons. They reuse data already persisted by earlier work (per-player
`PlayerRoundState`, the `GameEvent` log, and the RES-02 SP / shots / lives / points series) and the
RV-03 ReportLab export. **Decision (locked at planning):** charts are rendered **server-side with
matplotlib** (pure-Python, no browser, deterministic) rather than capturing the client-side Chart.js
canvases or printing the page in headless Chrome — keeps the export self-contained and avoids a
browser dependency ahead of the Angular migration, consistent with RV-03's ReportLab rationale. Both
PDF items below share a single matplotlib-to-ReportLab rendering helper.

**Shared prerequisite:** add `matplotlib` to `requirements.txt`. A new helper module
(`matches/sim_helpers/pdf_charts.py`, pure: data series in → PNG bytes / ReportLab `Image` out, no
ORM, no I/O beyond an in-memory buffer) re-plots each chart series with matplotlib using the
`Agg` (non-interactive) backend so it runs headless on the server. The chart **data** is the same
series the events page builds (per-player SP / shots / lives / points over time, sourced from
`GameEvent` rows — RES-02 contract); the helper does not need Chart.js. Charts won't be pixel-identical
to the on-screen Chart.js versions, but carry the same data.

### HX-02 · Individual performance per round page

A **single-round, single-player** drilldown — distinct from HX-01, which aggregates a player's career
across *all* rounds. New page `/matches/game-round/<id>/player/<pid>/` (URL name e.g.
`round_player_detail`), linked from each player row on the round detail scoreboard
(`game_round_detail.html`) and from the round events page. Surfaces that player's performance **within
this one round**: their `PlayerRoundState` stat line (points, MVP, tags made / times tagged, accuracy
%, final lives, resupplies given, missiles landed, specials used, follow-up / reaction shots, combo
resupplies), their personal `GameEvent` timeline filtered to events where they are actor or target,
and their SP / shots / lives curves over the round (the RES-02 series, filtered to this player). If the
round has a movement heatmap (RES-04 `cell_occupancy_json`), embed this player's per-cell occupancy as
a mini-heatmap.

**Depends on:** existing `PlayerRoundState` + `GameEvent` data (no new persistence); reuses RES-01
accuracy, RES-02 SP series, and optionally RES-04 occupancy. **Scope:** read-only/derived — no model
change, no migration, no simulator change. **Acceptance:** the page renders the correct stat line and
event timeline for the given (round, player); a player who has no `PlayerRoundState` on the round
404s; the per-player charts show only that player's series; the round-detail scoreboard links to it.

### HX-03 · Export individual performance as PDF (extends HX-02)

`GET /matches/game-round/<id>/player/<pid>/export/` — a per-player, single-round PDF stat sheet:
header (player name, role, team, round), the stat line, the personal event timeline, and the player's
SP / shots / lives / points graphs rendered server-side via the **same** `pdf_charts.py` helper used by
RV-05 (one rendering path, reused). "[Simulated]" watermark on simulator-generated rounds, matching
RV-03 / RV-05.

**Depends on:** HX-02 (the page + its data assembly) and the RV-05 shared chart helper. **Scope:**
read-only/derived — no model change, no migration, no simulator change. **Acceptance:** the exported
PDF contains the player's stat line, timeline, and graphs for the one round; an absent
(round, player) pairing 404s; the watermark appears for simulated rounds.

### IMPORT-01 · Real-game `.tdf` log parser + import tool

Parse real Laserforce SM5 game logs (the `.tdf` files in `Screenshots_and_video_examples/sample_games/`)
and import them as `GameRound`s, so the app can store and review *actual* games alongside simulated ones.
The `.tdf` format is a **UTF-16, tab-separated, sectioned** export: `;0/info`, `;1/mission` (type, desc,
start, duration), `;2/team` (index, desc, colour), `;3/entity-start` (player/target id, role/battlesuit,
team, member id), and `;4/event` (time, type code, free-form payload) records. Write a pure parser
(`.tdf` bytes → structured rounds + events, no Django/ORM, no I/O) and an import tool (management command
and/or upload view) that maps parsed entities to `Player`/`Team` rows and parsed `;4/event` rows to
`GameEvent` rows, persisting a `GameRound` linked to an **`actual_game_log`** record.

**Provenance contract (locked at RV-03 planning):** a `GameRound` not paired with an `actual_game_log`
is `is_simulated = True` (the RV-03 watermark default); an imported round links to its `actual_game_log`
and is stored with `is_simulated = False` (no watermark). RV-03 adds the `is_simulated` flag now;
IMPORT-01 adds the `actual_game_log` link and is the first writer of `is_simulated = False`.

**Open design questions (resolve in this task's own grill):** the `actual_game_log` model shape (store
raw `.tdf` bytes vs. parsed JSON vs. both); how `;4/event` type codes map onto the simulator's
`GameEvent.event_type` vocabulary (tag / down / resupply / nuke / base-capture — the mapping is the risky
part and likely lossy); how parsed entities reconcile to existing `Player`/`Team` rows (match by member
id? create-on-import?); whether real-game ticks/timestamps (the `;4` `time` field is in different units)
need conversion to the TIME-01 tick model. **Scope:** new persistence (the `actual_game_log` model +
`is_simulated = False` writes) and a migration. **Acceptance:** both sample `.tdf` files parse without
error into a reviewable `GameRound` whose scoreboards/event log render in the existing round views, and the
imported round shows **no** "[Simulated]" watermark on its RV-03 export.

### LG-06 · Phased Season lifecycle (off-season / regular / tournament)

**Merged into LG-02-Part2.** The **LG-02-Part2 grill (2026-06-04)** resolved
that this phased lifecycle **is the same capability** as LG-02-Part2's
season-structure model — off-season / regular / tournament are **phase types**
on the shared `SeasonPhase` model, not a parallel abstraction. Building two
season-structure models would be a mistake. The `SeasonPhase` foundation ships
in **LG-02-Part2a** (done); alternative regular-season formats land per-phase in
**Part2b**; the tournament/playoff phase (subsuming the LG-02 double-elim as the
canonical end-of-Season closer, seeded from regular-season **Standings**) lands
in **Part2c**. See
[ADR-0023](docs/adr/0023-season-phase-composable-structure.md). The remaining
LG-06-specific scope below (the off-season free-agent/roster-clamp behaviour and
the per-phase dashboard branches) is folded into the Part2b/Part2c work.

Replace the current flat `draft → active → completed` Season state machine with a phased
lifecycle that mirrors a sports-league cadence:

1. **Off-season / pre-season** — Free Agents pool open for recruitment; teams may carry a
   variable roster (any size). Roster is **clamped to 10** on the press of the "Start Regular
   Season" button before round play begins.
2. **Regular season** — round-robin (today's `active` behaviour). PLAN backlog: add **alternative
   regular-season formats** beyond single-round-robin (double-round-robin, split-conference,
   stage-based, etc.) — owner picks per Season.
3. **Tournament (playoffs)** — best-of, double-elimination bracket between seeded teams,
   ending with a single champion. Tournament feeds from regular-season Standings. Subsumes
   the LG-02 double-elim format as the canonical end-of-Season closer.

**Dashboard implications (consumes by LG-01c re-visit):** during off-season the dashboard
renders an *unpopulated* preview (teams + players sorted by name); during regular season the
dashboard is fully populated as today; during tournament the dashboard mixes fixed regular-season
stats with live tournament-stats panels; post-tournament shows end-of-tournament stats until the
next off-season starts.

**Out of LG-01c scope** (LG-01c is read-only dashboard against the current 3-state model).
Touches: `Season.state` enum + migration, free-agent ↔ Team move flows, roster-size cap toggle,
tournament bracket model (LG-02 overlap), simulator's `simulate_scheduled_round` phase guard,
dashboard branches per phase.

### LG-07b · Member-nights player-stat filter (DEFERRED follow-up to LG-07a)

**[NOT STARTED — the remaining LG-07 follow-up.]** The per-Season player-stat
**include / exclude / only** member-nights selector — over **Player Stats**, **League
Leaders**, **Statistical Feats**, and the **Team-History Players** tab — was **DEFERRED
out of the LG-07a core slice** (ADR-0033 §"per-Season player-stat screens gain a
member-nights filter"; seam contract §8 scope-out). LG-07a excludes member-night
Matches from **Standings only**; member-night `PlayerRoundState` rows currently flow
into the player-stat screens **unfiltered**. LG-07b adds the include/exclude/only
selector (the LG-06d `?provenance=` filter precedent) so the viewer chooses whether
casual games appear; the global all-time career page (HX-01) stays unaffected
(league-agnostic). No model change is anticipated (the discriminator is the
member-night `season_phase` already stamped on the Match).

### SIM-04 · Simulation confidence display

when we import real data we want to have a confidence level and "elo" skill rating of actual players using all imported games
Per-player data source label ("40 games" vs "Role defaults — no history") on simulation summary. 
Team-level confidence badge: Low (<5 games), Medium (5–20), High (>20). Link to edit stats from confidence panel.

### STAT-03 career stat additions

add mvp and elo over time to career stats

### STAT-PROXY-01 · Rating proxies — MMR, Rank tier, Potential

The LG-01z league screens (Player Ratings, Free Agents, Team Roster, and — once
unblocked — Hall of Fame) reserve columns for three LoL-GM rating concepts we don't yet
model: **MMR**, **Rank tier**, and **Potential**. They currently render a literal `-`
placeholder (see `stats.md`). This task replaces the placeholders with real values:

1. **MMR** — a per-player skill rating. Likely an Elo-style number seeded from
   `overall_rating` and updated from game results (ties into SIM-04's "elo skill rating
   of actual players using all imported games" and STAT-03's "elo over time"). Decide:
   stored field vs. derived; per-Season vs. career.
2. **Rank tier** — a **letter tier** (e.g. S / A / B / C / D, or named bands) derived
   from MMR or `overall_rating` bands. Cosmetic label; thresholds are tunable.
3. **Potential** — a ceiling rating (0–100) per player, paired with `overall_rating`.
   Likely a stored field set at generation / import; drives prospect scouting later.

**Implementation surface:** add the field(s) / derivation, then replace the `-`
placeholder cells on the Player Ratings, Free Agents, and Team Roster templates with the
real values (and make them sortable where it makes sense). Unblocks the **Hall of Fame**
screen's Peak MMR / Peak Overall columns (`stats.md` §11). No simulator-mechanic change;
no Score Calibration re-baseline. Coordinate with SIM-04 (import-driven Elo) so MMR has a
single source of truth.

---

---

## Parked — deferred compute-tier work

Deprioritised to the bottom of the plan (2026-06-26 grill). Pushed below all other
planned work — no easy build path, two hard blockers.

### GEN-02 · [DEFERRED — needs its own grill + resolution of the two blockers below] Three compute tiers mirroring the persistence tiers

**Why deferred (2026-06-26 grill).** Two blockers, neither easy:

1. **The compute-tier "upgrade" is NOT the GEN-01 `ensure_fidelity` pattern.** GEN-01's
   lazy upgrade is faithful *because the re-sim reproduces the identical game* — so
   backfilling detail rows onto the existing scoreboard is coherent and the scoreboard
   is never rewritten ([ADR-0029](docs/adr/0029-persistence-fidelity-tiers-and-faithful-lazy-upgrade.md)
   decision 3 + the equivalence invariant). At GEN-02 a higher **compute** tier produces
   a **genuinely different game** (accepted up front — a `scores`-compute scoreboard
   will not byte-match a `full`-compute one). So an in-place compute upgrade is stuck:
   either **(i)** rewrite the scoreboard to match the new full-compute game →
   retroactively shifts completed-season Standings the instant an old game is clicked
   (the verify-then-degrade failure ADR-0029 rejected, "sharper here"); or **(ii)** keep
   the cheap scoreboard and only add detail rows → you then watch a `full` replay whose
   combat log + movement produce a *different* score than the scoreboard shown above it
   (an incoherent game). The PLAN's "reuse `ensure_fidelity` verbatim / upgrade re-runs a
   higher `compute_tier`" wording does **not** transfer. A candidate resolution (not yet
   accepted): make a cheap-compute season game **terminal for Standings** — its cheap
   scoreboard authoritative forever — and make "watching" a **transient, non-persisted
   `full`-compute re-sim** from the stored `(master_seed + roster_snapshot + map)`, the
   PR-03 fork-and-resim pattern, that never writes back. That sidesteps the hazard but
   abandons in-place compute upgrade and needs its own grill.
2. **The `scores`-compute statistical model is blocked on data that doesn't exist yet.**
   Fitting per-role closed-form distributions needs baseline `score_averages` output, the
   same dependency the deferred per-stat-per-role weight tuning has — and it's entangled
   with the still-pending **CAL-01** Score Calibration re-baseline (GEN-02 open question
   (e) DOES touch calibration, unlike GEN-01). The `combat`-compute tier (the existing
   3-zone fallback) is *largely already built and calibrated*; the `scores`-compute tier
   is a brand-new model that can't be fit until that baseline data lands.

**Likely re-slice when revived:** ship `combat`-compute first (reuse the already-built,
already-calibrated 3-zone fallback path — just the tier selector + skipping
event/movement collection), and defer `scores`-compute (the unbuilt statistical model)
until CAL-01 + baseline batch data exist. The original full write-up follows.

---

**Prio: High (when unblocked).** GEN-01 shipped **persistence** tiers (`scores` ⊂ `combat` ⊂ `full`)
where the tick loop **always runs in full** and the tiers differ only in what
`flush_to_db` writes — so the **same seed reproduces a byte-identical game at every
tier**, and the only saving is skipped DB writes / event-buffer collection.
[ADR-0029](docs/adr/0029-persistence-fidelity-tiers-and-faithful-lazy-upgrade.md)
**explicitly deferred** the genuinely-cheaper path — "a separate statistical model
that would *not* match full-fidelity scores… would break the same-seed-same-scores
guarantee and is a different piece of work." **GEN-02 is that piece of work:** three
**compute** tiers that actually do *less arithmetic* for a game nobody will watch, so
bulk season play (`play_season_task` simulating hundreds of rounds whose only consumer
is Standings) stops paying the full ~200 ms-per-round (no-map) / multiple-× (map) tick
cost when a closed-form scoreboard would do.

**Accepted up front (user decision):** because a cheaper *computation* produces a
*different* game, **the same seed cannot generate all three compute tiers** — a
`scores`-compute scoreboard will not byte-match a `full`-compute one. What GEN-02
guarantees instead is (a) **reproducibility within a tier** — `(seed + roster snapshot
+ map + tier)` deterministically regenerates *that tier's* result, so any cheap result
is auditable/replayable; and (b) **a documented deterministic mapping** from a stored
seed to a higher-tier regeneration of the same matchup (different exact scores, same
distribution — see the calibration anchor below).

**The three compute tiers (mirroring the persistence tiers):**

1. **`scores` compute — closed-form statistical model (no tick loop).** Draw each
   player's final line from per-role distributions parameterised by their boosted
   `roster_snapshot_json` stats + the opponent's relative strength; decide the round
   winner from the aggregate. Microseconds, no movement / no LOS / no A* / no events.
   Produces a scoreboard **only**.
2. **`combat` compute — abstract-zone reduced-spatial model.** The **existing 3-zone
   fallback** (`movement_ctx is None`): a per-tick loop with role-weighted actions +
   zone-adjacency combat but **no per-cell A* and no LOS scan** (the two dominant
   costs). Produces a scoreboard + a who-hit-who combat log, but **no movement trails**
   (there are no cells). **Largely already built and already calibrated** (see below).
3. **`full` compute — the current spatial engine** (MOVE-01..04 per-cell movement →
   LOS → combat). The canonical scoreboard + combat + movement. Unchanged.

**Big de-risk — the middle tier already exists and is the calibration baseline.** The
3-zone fallback is live today (the `movement_ctx is None` path), and the **Score
Calibration Targets** (Commander 9,952 / Heavy 6,482 / … — `docs/agents/matches/simulation-engine.md`) "were
tuned against the non-spatial 3-zone fallback model." So `combat`-compute is mostly
*deliberately reusing an already-calibrated path*, not new mechanics — the work there
is the tier selector + skipping the event/movement collection, not a new simulator.

**Mock concepts investigated (to land on the seed↔tier mapping):**

- **Mock A — independent per-tier seeds.** Each tier draws its own seed; a `scores`
  round and its `full` regeneration are unrelated games. Simplest, but an "upgrade"
  yields a totally different scoreboard — exactly the retroactive-Standings-shift
  failure ADR-0029 rejected for *verify-then-degrade*. **Rejected.**
- **Mock B — shared master seed, per-tier deterministic model.** Store ONE master
  seed; each tier is `Random(master_seed)` feeding *its* model. Same master seed ⇒ each
  tier deterministically reproduces its own result, and the tiers are *anchored*
  (correlated samples of one matchup, not identical games). The mapping is
  `master_seed → {scores_result | combat_result | full_result}`, one deterministic
  function per tier; "upgrade" = re-run a higher-tier model from the stored
  `(master_seed + roster_snapshot_json + arena_map)` — **the GEN-01 `ensure_fidelity`
  pattern, extended from a write-selector to a model-selector.** **Kept** — but see
  Blocker 1 above: this "upgrade" cannot keep the cheap scoreboard *and* show a faithful
  replay, so the in-place framing is unresolved.
- **Mock C — hierarchical conditioning (cheap tier constrains the expensive).** Make
  the spatial sim reproduce the `scores` tier's predetermined scoreboard while filling
  in detail. Recovers same-scores-across-tiers (the persistence-tier property) but needs
  rejection-sampling / biased simulation — *more* expensive than `full`, and it breaks
  calibration. **Rejected** (it re-derives the persistence-tier guarantee and defeats
  the whole compute-savings purpose).
- **Mock D — calibration-bridged tiers.** Independently calibrate **all three** tiers
  to the **same** Score Calibration Targets, so although a given seed differs across
  tiers, the *aggregate distributions agree* — a season simulated at `scores` produces
  Standings statistically indistinguishable from one at `full`. This is the property the
  real use case (bulk season play) actually needs: not per-game identity, but a faithful
  *sample of the same distribution*. **Kept, combined with B.**

**Landing (robust solution = B + D).** A **`compute_tier`** selector
(`scores`/`combat`/`full`) that picks the **model**, orthogonal to GEN-01's
**`fidelity`** selector that picks what gets **written** — the two compose
(bulk season = `compute=scores, persist=scores`; LG-01i live watch =
`compute=full, persist=full`; a lazy upgrade re-runs a higher `compute_tier` from the
stored seed). Reuse GEN-01's `rng_seed` + `roster_snapshot_json` + the
`@transaction.atomic` lazy-resim plumbing verbatim. **All three tiers stay pinned to
the same calibration targets**, so the cheap tiers are faithful samples, not a
different game.

**Open questions for its own grill (NOT pre-resolved here):** (a) the exact `scores`
statistical model + its fitting — **depends on baseline batch data** (`score_averages`
output) the same way the deferred per-stat-per-role weight tuning does (see Blocker 2);
(b) whether `combat`-compute persists at-all-without-movement or folds into GEN-01
`fidelity=combat` (the two axes overlap at that tier and the seam must disambiguate);
(c) the **surface→compute-tier mapping** (bulk season → `scores`; live watch / sandbox
create → `full`; the missile-log / events views trigger a `combat`/`full` *recompute*,
not just a persistence upgrade); (d) whether an "upgrade" stores the *new* tier's
scoreboard or keeps the cheap one and only adds detail — the retroactive-Standings-shift
hazard ADR-0029 names is sharper here because the scoreboards genuinely differ between
tiers (see Blocker 1 — likely resolved by making cheap-compute games terminal for
Standings + a transient full re-sim for watching); (e) a **dedicated re-baseline** of
each tier against the targets (this DOES touch Score Calibration, unlike GEN-01). Needs
a new ADR (the seed↔tier mapping + the two-axis `compute_tier` × `fidelity` model) and a
GLOSSARY.md **Compute tier** term.
