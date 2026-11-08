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

### ANG-06 · [DONE] Migrate views by feature area

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
6. **[DONE] Map editor** (ANG-06h, in `PLAN-completed.md`).

Items 7-20 (planned 2026-10-25, owner-approved). Each is frontend parity work like 1-6, with its own
grill. Order is by dependency, then complexity. ANG-01 covers most of their API, but not all: item 9's
Team match history had no endpoint. A check on 2026-10-26 (ANG-06-9 retro) found every screen in
10-20 has an endpoint, and four only partly cover their template. Each gap is noted on its item
below (13b, 14, 16); the item's grill confirms it.

7. **[DONE] Accounts screens** (ANG-06-7, in `PLAN-completed.md`).
8. **[DONE] Player pages** (ANG-06-8, in `PLAN-completed.md`).
9. **[DONE] Head-to-head & history** (ANG-06-9, in `PLAN-completed.md`).
10. **[DONE] Round extras** (ANG-06-10, in `PLAN-completed.md`).
11. **[DONE] Enhanced setup / detail check** (ANG-06-11, in `PLAN-completed.md`).
12. **[DONE] Batch simulate** (ANG-06-12, in `PLAN-completed.md`).
13a. **[DONE] Tournaments: list, create, participant pool** (ANG-06-13a, in `PLAN-completed.md`).
13b. **[DONE] Tournament detail** (ANG-06-13b, in `PLAN-completed.md`).
14. **[DONE] League core** (ANG-06-14, in `PLAN-completed.md`).
15. **[DONE] League stats** (ANG-06-15, in `PLAN-completed.md`).
16. **[DONE] League team & player** (ANG-06-16, in `PLAN-completed.md`).
17. **[DONE] League finances** (ANG-06-17, in `PLAN-completed.md`).
18. **[DONE] Season screens** (ANG-06-18, in `PLAN-completed.md`).
19. **[DONE] Season play loop** (ANG-06-19, in `PLAN-completed.md`).
20. **[DONE] Landing & nav** (ANG-06-20, in `PLAN-completed.md`).

Settled (owner, 2026-10-25): ANG-08 stays blocked until 7-20 are all `[DONE]` (no per-area
template removal in between); Phase 3.5 (LFS-01 / LFS-02) waits until after all of ANG-06.

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
items above are scoped. *Done 2026-10-25: items 7-20 above.*

ANG-07 (serve Angular from Docker) is **[DONE]**; see `PLAN-completed.md`.

ANG-08 (remove Django template views) is **[DONE]**; see `PLAN-completed.md`.

ANG-09 (Investigate a standalone single-player executable) is **[DONE]**; see `PLAN-completed.md`.

ANG-10 (serve the SPA from Django in every deploy) is **[DONE]**; see `PLAN-completed.md`.

DESK-01 (desktop App code in `main`, with tests) is **[DONE]**; see `PLAN-completed.md`.

DESK-02 (Desktop App build in CI) is **[DONE]**; see `PLAN-completed.md`.

DESK-03 (Desktop App native window) is **[DONE]**; see `PLAN-completed.md`.

DESK-04 (Desktop App distribution) is **[DONE]**; see `PLAN-completed.md`.

DESK-05 (sign the Desktop build) is **[DEFERRED]**; see *Deferred Items*.

BUG-SL-01 (Sight-line compute fails after a saved zone config) is **[DONE]**; see `PLAN-completed.md`.

## Phase 3.5 — LFstats realism (added 2026-10-21)

Follow-ups to the LFstats player import (#256). The import turns real players' LFstats numbers
into the 19 Player stats. The formulas, the meaning of each stat and the sim oddities found
along the way are in [`docs/lfstats-import.md`](docs/lfstats-import.md). The rebuild tools are
`scripts/lfstats_import/` and the data is `data/lfstats_snapshot_2026-10-21.json`. Scheduled
directly after the Phase 8 Angular work (owner, 2026-10-21). LFS-01 shipped first (2026-11-03);
mechanics changes from LFS-02 or CAL-01 call for re-running its `lfstats_calibrate` harness.
Re-run on 2026-11-06 after the Syracuse scale fix (20 px squares at 1 ft, from 30 px / 5 ft):
baseline 14/36 cells; the support-survival switches T, H, M, A (`sim_helpers/support_survival.py`)
became the defaults at 15/36, objective 0.144; the real side of every cell is committed as
`data/lfstats_cell_targets.json` (`lfstats_calibrate --split targets`). Results are in
`docs/lfstats-import.md`. The three follow-up constants were re-run on top of the switches on
2026-11-06 (`docs/lfstats-import.md`, "Nukes and missiles"): the Commander nuke gate at 20 SP (#558)
is the default, with the Commander hiding while the fuse runs (`sim_helpers/nuke_arming.py`) and,
from 2026-11-07, the activation telling the enemy team only that a nuke is going off (the old
broadcast of the Commander's cell is gone; "Real nukes" in `docs/lfstats-import.md`); the
Downed option Stay weight 40 (#559) and the missile bump 30 (#560) moved no cell and stay as they
were. Where missiles died was a double spend on a hit (MECH-16, fixed) and the lock's 3-tick
exposure: a third of completed locks are dropped at impact because a participant was Downed.
The TDF event logs of the 165 Internationals games (`docs/lfstats-import.md`, "When Commanders
fire") then gave four more rules, all in `sim_helpers/nuke_arming.py` and shipped 2026-11-07: no
support resupplies an arming Commander, bar a per-nuke slip that lands about 1% of nukes (the
sim's own Medic and Ammo were 45% of its nuke cancels; in the arena 0.4% of nukes are resupplied
in the fuse),
each nuke draws its SP threshold from the real SP-at-activation table above a 20 SP floor, the
nuke weight is boosted just after the Commander comes back from a Down, and every Down ends the
nuke in `record_down` (a missile Down used to leave it armed).

### CAL-02 · Tags and times tagged in line with the arena (owner, 2026-11-07)

The owner, after the nuke work: bring tags and times tagged more in line between the sim and the
actual game. The sim's Commander tags and is tagged far less than the real one, which is also where
its SP income comes from (about 54 SP a Commander-round against 67 in the arena; a nuke costs 20),
so the remaining nuke gap is this gap. Latest calibration (LFS-01 Test set, 3 rounds, 2026-11-07,
`docs/lfstats-import.md`) and the counter (`times tagged` and `tags` per player-round):

| Role | Sim times hit | Real times hit | Sim hit diff | Real hit diff | Sim tags a round | Real tags a game (shots hit) |
|---|---|---|---|---|---|---|
| Commander | 29.7 | 59.1 | 1.87 | 0.98 | 42 | about 70 |
| Heavy | 32.8 | 47.8 | 1.35 | 0.97 | 37 | about 53 |
| Scout | 25.5 | 33.8 | 1.13 | 1.12 | 25 | about 53 |
| Ammo | 15.4 | 23.2 | 0.75 | 1.10 | 11 | about 74 |
| Medic | 15.7 | 10.2 | 0.16 | 1.06 | 2 | about 62 |

Real tags are LFstats SM5 shots hit (last 365 days, 10+ games at the position); real times hit and
hit diff are the Test-set cells. Every role but the Medic is tagged too little and tags too little;
the Medic is tagged too much and tags almost never. Things to settle, in order: (1) whether a
planned Shot is a shot fired and how many land (the CAL-01 shots table under Phase 3 has the
per-role shot counts); (2) the Commander's and Heavy's hit diff, which say the 3-hit roles win too many of their
exchanges, against the TDF finding that 76% of real 3-hit Downs by a 1-hit player's three tags come
without the 3-hit player tagging back (9.3 such Downs a game; `docs/lfstats-import.md`, "Real
nukes"); (3) support tags, the Medic above all (`support_survival` hides them; the arena's Medic
shoots 62 times a game and is hit 10); (4) engagement range and the time players spend with no
target in sight (`round_probe`, the Action numbers). Measure with `lfstats_calibrate --split test`
(the times-hit and hit-diff cells), the scratchpad-style counter (tags / times tagged / SP a round)
and the TDF scripts for who-tagged-whom shapes (`docs/lfstats-import.md`, "Real nukes"). Keep the
Still / squares targets of MECH-17 and the nuke numbers of CAL #558 while moving these.

LFS-01 (Tune the numbers so imported players reproduce their LFstats results) is **[DONE]**; see `PLAN-completed.md`.

LFS-02 (Suggest app changes from Kodama's "How to Improve in SM5" talk) is **[DONE]**; see `PLAN-completed.md`.

---

## SM5 talk follow-ups

Accepted LFS-02 suggestions, in priority order. Each row and its code facts are in
`docs/proposals/lfs-02-sm5-talk.md`. Every entry that changes the sim changes seeded outcomes:
it folds into the **CAL-01** re-baseline, and LFS-01's `lfstats_calibrate` harness is re-run
after it.

SM5-01 (`_stress_fail` rewards weak supports) is **[DONE]**; see `PLAN-completed.md`.

SM5-02 (Downed players can still fire) is **[DONE]**; see `PLAN-completed.md`.

### SM5-03 · Resource-aware aggression (resource_awareness)

resource_awareness has no effect: `_observe_lives` (`simulation/round_loop.py`) writes the
enemy's lives into memory and nothing reads them back. Two parts:
- **(a) Own resources.** Replace the flat 30% lives/shots retreat rule (`_apply_seek_ally`,
  the `pathfinding.py` medic/ammo goals) with the talk's bands: about 9-13 lives is safe and
  6-7 means head home, adjusted for the nukes the enemy Commander is likely holding.
  resource_awareness sets how reliably a player follows the bands.
- **(b) Enemy resources.** Read the remembered enemy lives back, so a player prefers
  low-lives targets and finishes Eliminations.

**Open questions:** the exact bands per role; what "home" is (medic, own base); how the
likely-nukes estimate is made. Reads memory, so it composes with MECH-13. The importer writes
resource_awareness = 50 for everyone until the TDF route exists.

### SM5-04 · Missile dodge from flexibility

flexibility is never read. Move the missile dodge (`simulation/entrypoints.py`) from
`min(20, survival / 5)` to `min(20, flexibility / 5)`; survival keeps only the hit roll. Add
flexibility to `_SIMULATION_STATS` and the `PlayerRoundState` forwarding properties. The
importer writes flexibility = 50 for everyone; `times_hit_by_missile` per game is the candidate
source.

### SM5-05 · Losing-state modifier (adaptability)

adaptability is never read. While the score broadcast says a player's team is behind, low
adaptability costs hit chance and decision sharpness; high adaptability holds steady. Matches
the importer's formula (MVP in losses). **Open questions:** the size of the penalty, and
whether the deficit's size scales it.

### SM5-06 · Control-zone positioning (positioning)

`positioning` sets how sharply a player draws a goal (MOVE-06), and MECH-07 defines Control cells
and the Control value rule. What is left is how far forward to play, set from a player's own shots
and lives. **Open question:** how "how far forward" shares work with SM5-03(a). Once zones are defined, the three zone synergies (rejected for
LFS-02) are candidate modifiers here.

### SM5-07 · Nuke call-outs and reaction delay (communication)

Today every enemy learns the Commander's cell the tick a nuke starts (`round_loop.py`), whatever
anyone's communication, and the reaction roll `(ga + pa) / 200` has no delay. In the talk an
announced nuke removes teammates' 1-3 s reaction delay. Unseen nukes start with a reaction
delay of 2-6 ticks; a teammate who sees the Commander and passes the communication roll makes a
**Call-out**, which removes it. The fuse is 8-14 ticks, so the delay decides whether a
cancel is possible.

### SM5-08 · Sprint vs jog

**Prio: Low.** No pace choice exists: cells per tick come only from speed
(`sim_helpers/pathfinding.py`). Add one per goal: a sprint moves full speed with a lower chance
of a memory update; a jog moves about 60% speed with full awareness. decision_making or
game_awareness picks the pace. **Open questions:** which stat picks; whether stamina limits
sprinting (MECH-11 also spends stamina).

### SM5-09 · Weight `overall_rating` by skill area

`overall_rating` is a plain mean of all 19 stats (`teams/models.py`, plus the DB versions in
`teams/services.py` and `matches/league_screen_services.py`). Weight it by the talk's six
areas: game sense largest, mechanical skill about 5%. It changes salary, seeding, draws and
development, not the sim. **Open question:** the other areas' shares, which
`docs/lfstats-import.md` does not record.

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

Updated 2026-11-03. Phases 0–2, 5, 5.5, 6, 7 and 8 are complete, as are the Found Issues at the
top of this file. Everything below the scheduled list is an unscheduled backlog.

**Done — Phase 8 (Angular Frontend Migration).** The API half (ANG-01..ANG-04), the look (ANG-04b,
ADR-0043), the client layer (ANG-05), every screen (ANG-06), the Docker front door (ANG-07), the
template removal (ANG-08: the SPA is served at /, ADR-0047) and Django serving the SPA in every
deploy (ANG-10, ADR-0049). Phase 3.5 (LFS-01, LFS-02) followed it.

**Current — CAL-02 (tags and times tagged, Phase 3.5), then the SM5 talk follow-ups** (from
LFS-02's proposal), in order:

```
CAL-02          Tags and times tagged in line with the arena (owner, 2026-11-07)
SM5-03..SM5-09  SM5 talk follow-ups (from LFS-02)
```

**Later — Phase 3.6 Learned player AI:** AI-01..AI-06; MECH-07, which they build on, has shipped.

**Backlog — unscheduled, no fixed order.** Each item needs its own grill before it is picked up.
Mechanics items can run in parallel with Phase 8, because they don't touch the frontend. Analytics
items that add pages (RV-04, RES-05, HX-02) should wait for ANG-06, so they are built once, in
Angular.

- **Phase 3 — Simulation Mechanics:** MECH-08, MECH-09, MECH-11..MECH-15, MOVE-05, SIM-13, SIM-14,
  CAL-01.
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
- **DESK-05 · Sign the Desktop build with Azure Trusted Signing** (deferred by the owner,
  2026-11-02: not paying for Azure Trusted Signing for now). Split from DESK-04 (decision 2). Sign
  the `.exe` in CI with Azure Trusted Signing so SmartScreen reputation builds on the publisher, not
  on each zip. Needs the owner's Azure subscription and identity validation first; check prices and
  eligibility then (ADR-0048 Distribution)
- **Editor saves drop blocked edges** (BUG-SL-01 grill, 2026-11-02) — a zone-config PUT that sends
  `zones` (every SPA editor save) stores `blocked_edges: {}`, so sight lines computed afterwards
  ignore the image's sub-cell wall edges, while an auto-detected save keeps them. Keeping the
  detected edges on an edited save would change sight lines on every edited map
- **Last blocked edge before the target is ignored** (BUG-SL-01 grill, 2026-11-02) — `_has_los`
  returns `True` on reaching the target cell before checking the edge it just crossed, so a blocked
  edge next to the target never blocks: on a 5x3 floor with `1,1,e` blocked, (1,0) sees (1,2) but
  not (1,3). Fixing it changes sight lines, Rankings and Heavy strong spots on every map with
  non-empty blocked edges; pair it with *Editor saves drop blocked edges* above
- **Map upload size limit** (ANG-10, 2026-11-01) — nginx capped request bodies at 20 MB; with
  Django serving every deploy directly, gunicorn and Django set no cap on file uploads. A size check
  in the map upload form is the fix
- **Revoke refresh tokens on password change** (ANG-03) — a password change keeps the Account's
  outstanding refresh tokens valid until they expire (7 days). Blacklisting every `OutstandingToken`
  for the Account on change is the fix; access tokens already expire within 15 minutes
- **Play All 500 without Redis** (found in the ANG-06-13b browser check, 2026-10-28) — with no Redis,
  `POST tournaments/<id>/play-all/` (and the HTML play-all) returns an unhandled 500: `apply_async`
  raises redis `ConnectionError` from the result backend, and `enqueue_play_all` catches only kombu's
  `OperationalError`, so the claim is never released and the 503 `broker_unavailable` never shows.
  Catch the backend's connection error too (and check the batch / Season enqueues for the same gap)
- **Custom domain** — deferred until the project grows; fly.dev subdomain is sufficient for now
- **Serve the Angular SPA on Fly.io** (ANG-07 follow-up, ADR-0046) — ANG-07's nginx front door runs
  only in the two compose stacks. Fly runs one image per machine, so a sidecar doesn't map onto it;
  Fly stays Django-only until a task puts nginx and gunicorn in one image (or a second Fly app)
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
roughly by readiness.

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

**From LFS-02:** stubborn zone control. A player holding a spot keeps resetting the enemy there
about 3 times before leaving, instead of moving on after one Down.

MECH-10 (Follow rule — a Chaser cannot tag the player they chased) is **[DONE]**; see `PLAN-completed.md`.

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

- **Ammo** can resupply the Heavy for free when the Commander is down.
- **Down call-outs with a location** (from LFS-02): "commander down at big box" sends teammates
  to finish the downed enemy. Memory entries already carry `downed` / `reset_window` status;
  nothing reads it yet.
- **Shared shots and lives** (from LFS-02): a teammate's call of their own shots and lives
  changes how the others play (cover a low-lives ally, send the ammo).

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

MECH-16 (Missile spent twice on a hit) is **[DONE]**; see `PLAN-completed.md`.

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

MOVE-08 (Downed players move off) is **[DONE]**; see `PLAN-completed.md`.

MOVE-06 (Goal-location noise to reduce balling-up) is **[DONE]**; see `PLAN-completed.md`.

MOVE-07 (Varied pathing + action-choice sweep) is **[DONE]**; see `PLAN-completed.md`.

### SIM-13 · Tick-native cooldowns on `PlayerRoundState`, plus a literal check

**Prio: Medium (found 2026-11-04, SM5-02 retro).** `PlayerRoundState.is_active_at` /
`is_taggable_at` (`matches/models.py` ~L580-607) still use the seconds-era `< 8` / `< 4`, and
their docstring says only `ResourceBasedSimulator` reaches them, but `BatchSimulator` is the
sole engine. The weight tests pass `PlayerRoundState` rows straight to the role functions, so
`test_weights.py`'s Downed tests run on 8 / 4 rather than the engine's `RESPAWN_TICKS` (16) /
`NOT_TARGETABLE_TICKS` (8). Use the tick constants (or delete the methods if nothing but tests
reaches them) and fix the docstring. SM5-02 found the same seconds-era bug in the resupply
queue (`<= 7`) by hand. Then add `scripts/check_tick_literals.py` beside
`check_sqlite_json_lookups.py`: it fails on `last_downed_time` compared with a bare number
literal. Wire it into `/verify` and CI's lint step. It goes red on `models.py` until the fix lands,
so land both in one PR.

### SIM-14 · De-flake `test_strong_team_winpct_not_diluted_by_alternation`

**Prio: Medium (SIM-12 / RV-05 retro, 2026-10-22; again in the SM5-02 retro).** The test in
`test_batch_sim.py` asserts one master seed's batch. Its threshold or seed was hand-tuned in
SIM-12 (58% -> 55%, seed 7777 -> 2) and again in SM5-02 (seed 2 -> 3); both times six seeds
showed seed noise (43-63%, then a mean of 57.9%). Every engine change can trip it. Either
assert the mean across several seeds against the 55% floor, or move it behind a `calibration`
pytest marker run in a non-blocking CI job.

### CAL-01 · Score Calibration re-baseline

**Check the Down rate here (MECH-07, 2026-11-05).** On `origin/main`, 6 seeded Syracuse rounds, the
share of ticks a player is not Active: Scout 64.4%, Heavy 62.8%, Commander 55.6%, Ammo 52.7%,
Medic 34.2%. That limits how far Modes can move players, so it is worth settling before Mode tuning.

**Compare shots and time not Active with LFStats (owner, MECH-17, 2026-11-05).** Sim numbers are
planned Shots on seeded Syracuse rounds with default stats, partway through MECH-17, so re-measure them.
Real numbers are LFStats SM5, last 365 days, players with 10+ games at the position. Real shots fired is
estimated as average shots hit / average accuracy, as LFStats has no shots-fired average.

| Role | Real shots fired | Sim planned Shots | Real not Active | Sim not Active |
|---|---|---|---|---|
| Commander | about 83 | 93.3 | 50% | 55.8% |
| Heavy | about 62 | 84.9 | 46% | 59.4% |
| Scout | about 71 | 87.7 | 51% | 65.3% |
| Ammo | about 93 | 33.0 | 39% | 61.1% |
| Medic | about 69 | 7.1 | 25% | 60.8% |

The biggest gaps are support shots (Ammo about a third of real, Medic about a tenth) and time not Active.
Check first that a planned Shot means a shot fired, and that the two not-Active definitions match: LFStats
uptime may leave out time after Elimination, and the sim's number includes it.

On the finished MECH-17 code (seeds 1-12): planned Shots Commander 99.5, Heavy 107.5, Scout 69.6, Ammo 48.2,
Medic 5.2; not Active 45.0%, 58.0%, 65.3%, 63.9%, 81.9%, in that order. Also check:

- **Eliminations.** Sim over 24 rounds, real in brackets: Commander 21% (32%), Heavy 46% (39%), Scout 56% (42%),
  Ammo 81% (43%), Medic 100% (72%). No hide rule tried brought Medics much below 100% without giving up
  the Still target.
- **Scout tags.** Hold baselines left a Scout's `only_move` above `tag_player`, so `only_move` takes the
  decision-making boost. Setting it back to 50 gave 81 planned Shots, with Heavy Still at 41%.
- **Misses from MECH-17:** Commander Still 23.1 (target 20), Commander squares 23.3 (target 21), Scout tags 69.6
  on seeds 1-12 and 71.5 on seeds 1-24 (floor 70).

**Prio: Medium (found 2026-06-25).** Rebase the map-model average scores toward the
documented **Score Calibration Targets** (Commander 9,952 / Heavy 6,482 / Scout
5,102 / Ammo 3,242 / Medic 2,282 — `docs/agents/matches/simulation-engine.md`). This is the long-pending
post-MOVE-01 re-baseline and **absorbs** the seeded-outcome deltas from MOVE-05 /
MOVE-06 (and SIM-12) in a single pass — do **not** create separate re-baseline
obligations for those. **Locked (grilled 2026-06-25):** **tune** the existing action
weights / hit-chance / movement constants to converge on the targets; keep the 19-stat
model + role MVP/weight formulas as-is for now. The **deeper rework** the user wants
scheduled later — revisiting action selection, movement selection, and goal selection
themselves — shipped for goals and movement in `MECH-07`, and for actions in `MECH-17`.

MECH-07 (Role-aware action / movement / goal-selection rework) is **[DONE]**; see `PLAN-completed.md`.

MECH-17 (Action-choice sweep) is **[DONE]**; see `PLAN-completed.md`.

---

## Phase 3.6 — Learned player AI (added 2026-11-04)

Long-term goal (owner, 2026-11-04): teach an AI / neural net to play the sim with a genetic
algorithm. Sequenced after MECH-10, MOVE-06/07 and MECH-07. Each task needs its own grill.

### AI-01 · Close the rule loopholes a learner would exploit

A learner exploits every bug. Prerequisites: MECH-10 (follow rule), MECH-16 (missile spent
twice), MOVE-05 (cell occupancy) and MECH-13 (imperfect information; without it the policy
learns from ground truth no real player has). This task is the audit that they are done and
that nothing else hands out free points.

### AI-02 · Player controller seam

Separate decision-making from the engine: an observation (own resources, memory of enemies,
teammates' states, candidate cells with features) and a decision (Mode, candidate-cell scores,
optionally action weights). The scripted role logic becomes one controller; A* keeps doing
the movement. Deterministic for a given seed, so Replay and serial == parallel still hold.

### AI-03 · Headless throughput budget

Time a headless round and size the training budget (e.g. 50 policies x 20 games = 1,000 rounds
per generation; SIM-14 shows seed noise needs many games per fitness). Reuse
`parallel_worker.py`.

### AI-04 · Evolve the scripted policy's parameters

A GA or CMA-ES over the ~20-50 numeric parameters of the scripted Mode policy. Cheap,
interpretable, and it proves the training loop end to end.

### AI-05 · Neuroevolution of a small network

Evolve a small MLP (fixed-topology weights with CMA-ES / evolution strategies, or NEAT) behind
the AI-02 seam. Self-play against a hall of fame of past champions plus scripted bots, across
several maps and seeds; one policy per role or role as an input.

**Open for the grill:** the fitness goal. A strong AI (win rate, score) and a realistic AI
(matching LFstats per-role distributions) pull apart; a pure win-rate learner will not play
like real players.

### AI-06 · Ship the trained controller

Store weights as a file, run inference in plain numpy (keeps the Desktop build free of
PyTorch), offer it as a per-player or per-team controller option, and record the design in an
ADR.

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
