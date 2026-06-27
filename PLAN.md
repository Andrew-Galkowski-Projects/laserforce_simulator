# Development Plan

Organized by phase. Phases 0–2 are prerequisites for later phases; don't skip ahead.
Story IDs from `sm5_user_stories_v2.html` are referenced where applicable.

---

## Found Issues — UX & Live-Play Fixes (added 2026-06-25)

Surfaced from hands-on use of the running app. Grouped here at the top because they
are cross-cutting UX / live-play defects rather than new feature phases. The
simulation-mechanics bugs from the same review (start-of-game state, cell-occupancy,
goal-balling) were distributed into the **Phase 3 — Simulation Mechanics Backlog**
(see `MECH-15` / `MOVE-05` / `MOVE-06`), and the score-rebase ask into `CAL-01`
there.

### GEN-01 · [DONE] Three persistence-tier game generation off one seed

**Prio: Very High.** Generate a game at one of **three fidelity tiers** off the
**same seed**, choosing the cheapest tier sufficient for the surface that requested
it:

1. **Final scores + scoreboard** — persist the `Match` / `GameRound` /
   `PlayerRoundState` rows only.
2. **+ Who-hit-who** — also persist the combat `GameEvent` log (tag / missile /
   resupply / down / elimination), but **not** movement.
3. **+ Full game with paths** — also persist movement events + the per-Advance
   route / `cell_occupancy_json` for round-playback.

**Locked design — persistence tiers, NOT compute tiers (grilled 2026-06-25).**
Because movement drives combat (LOS / positioning gate who can tag whom),
`BatchSimulator._simulate_round` must run the **full per-tick loop** to produce
*any* scores. The three tiers therefore differ only in **what `_flush_to_db`
writes**, never in what the tick loop computes — so the **same seed reproduces the
identical game at every tier** (the speed win is from skipping DB writes +
movement-route recording, not from skipping simulation). A genuinely fast "scores
only" estimator (a separate, cheaper 3-zone/statistical model that would *not* match
the full-fidelity scores) is **explicitly deferred** to its own later grill — it
would break the same-seed-same-scores guarantee and is a different piece of work.

**Implementation surface:** thread a `fidelity` (or `persist_level`) selector through
`BatchSimulator.simulate_match` / `simulate_single_round_detailed` /
`simulate_scheduled_round` / `save_games` and the league/batch play loops, consumed
by `flush_to_db` (`matches/simulation/persistence.py`) to gate the event-log +
movement-trail + `cell_occupancy_json` + route writes. `rng_seed` is already
persisted, so a tier-1 round stays re-playable at higher fidelity later by
re-simulating the seed. **Open questions for its own grill — RESOLVED as shipped:**
(a) **surface → tier mapping** — the LG-01i live watch (`play_week_live` RR branch +
the live-playoff `play_specific_node`) ships **`full`** (you must see the game you are
watching in the same request); **everything else defaults `scores`** (bulk season
play, sandbox creates, `save_games`) and **lazy-upgrades on view click** — the events
page + heatmap to `full`, the missile log to `combat`, the round-detail scoreboard
stays `scores`. (b) **upgrade in place — YES**, re-sim the stored seed and backfill
the higher-tier rows onto the existing row, made faithful by a **roster-stat
snapshot** (`GameRound.roster_snapshot_json`) so the re-sim reads frozen sim-stats,
not the LG-04-mutated live roster — **no verify-or-degrade, no re-create** (both
alternatives rejected in ADR-0029). (c) **the `GameRound.fidelity` field records the
tier** (`scores` / `combat` / `full`, `default="full"`).

**[DONE] Shipped (2026-06-26).** Persistence-only: the tick loop always runs in full;
the three cumulative tiers `scores` ⊂ `combat` ⊂ `full` differ ONLY in what
`flush_to_db` writes (and, at `scores`, in skipping event-buffer collection), so the
same seed reproduces a byte-identical game at every tier — **no Score Calibration
re-baseline**. Two new `GameRound` fields (migration
`0055_gameround_fidelity_roster_snapshot.py`, dep `0054`, two `AddField`s, no
backfill): `fidelity` (`CharField`, `default="full"` so legacy rows read as `full`)
and `roster_snapshot_json` (the boosted per-side `_SIMULATION_STATS` inputs built from
the in-memory `PlayerState` lists, stored on every tier). A keyword-only
`fidelity: str = "scores"` selector threads through `simulate_match` /
`simulate_single_round_detailed` / `simulate_scheduled_round` / `save_games` /
`_simulate_and_flush_round` / `_flush_to_db` / `persistence.flush_to_db` /
`tournament_engine.play_next_node` / `play_specific_node`; only the LG-01i live call
sites override to `"full"`. `flush_to_db`'s write-blocks are factored into shared
`persistence._write_*` helpers gated on `FIDELITY_RANK`, reused by both the fresh
flush and the lazy-upgrade backfill; at `scores` the sim runs `event_log=None` (no
buffer). `BatchSimulator.ensure_fidelity(game_round, target)` (`@transaction.atomic`,
idempotent) re-sims from `(rng_seed + roster_snapshot_json + arena_map)` and backfills
the missing rows onto the existing row, **never** rewriting the scoreboard /
`PlayerRoundState`. View triggers: `game_round_events` + `movement_heatmap` →
`ensure_fidelity(gr, "full")`, `missile_log` → `"combat"`, `game_round_detail`
unchanged (`scores`). Residual caveat: map edits after a round was played can still
drift its replay (map context is re-derived from the `arena_map` FK, not snapshotted)
— stays under the pre-existing `rng_seed` "map config unchanged" caveat. See
[ADR-0029](docs/adr/0029-persistence-fidelity-tiers-and-faithful-lazy-upgrade.md), the
CONTEXT.md **Persistence fidelity** term, the seam contract
[`.claude/worktrees/gen-01-seam-contract.md`](.claude/worktrees/gen-01-seam-contract.md),
and the **GEN-01 persistence-fidelity tiers** subsection in
[`laserforce_simulator/matches/CLAUDE.md`](laserforce_simulator/matches/CLAUDE.md).

### GEN-02 · [DEFERRED to bottom — see "Parked — deferred compute-tier work" (2026-06-26)]

**Deferred (2026-06-26 grill).** Pushed to the bottom of this plan — too thorny to
build now, no easy path. Two blockers surfaced: (1) a compute-tier "upgrade" can't be
the GEN-01 `ensure_fidelity` pattern — a higher *compute* tier is a genuinely different
game, so an in-place upgrade must either rewrite the scoreboard (retroactive
Standings-shift, the ADR-0029 hazard) or leave an incoherent scoreboard-vs-replay; and
(2) the `scores`-compute statistical model is blocked on baseline `score_averages` data
that doesn't exist yet (entangled with the still-pending CAL-01 re-baseline). Full
write-up parked below. Proceeding to the next item (NAV-01).

### NAV-01 · [DONE] Dedicated `Play ▾` top-nav dropdown

**Prio: High.** The Play actions are currently **dashboard-only**
(`season-dashboard-play-*` / `league-dashboard-play-*` in
`templates/seasons/dashboard.html` / `templates/leagues/dashboard.html`); the top nav
(`templates/base.html`, league mode) has no Play entry. Add a **dedicated `Play ▾`
dropdown** (league mode only, sibling to `League ▾` / `Team ▾` / `Players ▾` /
`Stats ▾` / `Tools ▾` / `Help ▾`) holding the same play actions — One Week / Two
Months / Until End of Season (honouring the existing Part2c "Until Playoffs" /
"Until Tournament" terminal relabel) / Play Single Round (Live) — resolved against the
active Season's cursor via the `core.context_processors.league_nav` resolution chain
(extend `top_bar_links` / `top_bar_dashboard_url`), reusing the existing `play_week` /
`play_two_months` / `play_until_end` / `play_week_live` POST endpoints so Play is
reachable from any league-mode page, not just the dashboard. **Interplay with
PLAY-01:** while a multi-game run is in progress the dropdown's running entry follows
the same Play→Stop swap + progress affordance.

**[DONE] Shipped (2026-06-26).** Relocate-not-duplicate: the league-mode `Play ▾`
topnav dropdown becomes the **SOLE** league-advancement surface — all advancement
controls (Start Season / One Week / Two Months / Until End / One Week Live / Start
Next Season / owner-evaluation entry / Play Single Round / Play Playoffs) move OUT of
both dashboards into the league branch of `templates/base.html`; the dashboards keep
only read-only panels + the View-bracket / View-past-evaluations links + the
`play_error` banner. **League-mode only** (rendered in the `app_mode == "league"`
branch, LG-01k path-prefix). The nav advances the league's **resolved
active/displayed Season** via the `core.context_processors.league_nav` resolution
chain (session `last_league_id` → single-League → fallback; displayed Season =
`league.active_season` → most-recent completed → `None`) — **NOT** the `season_id` in
the URL. The 9 play keys (`action_button_label` / `action_button_state` /
`playoff_phase_active` / `playoff_tournament_id` / `playoff_completed` /
`has_following_tournament_phase` / `following_tournament_is_final` /
`live_preview_available` / `is_career_mode`) are factored OUT of
`matches.league_views._build_dashboard_context` into a new shared module-level helper
`matches.league_views._build_play_controls_context(league, displayed_season) -> dict`;
`league_nav` is EXTENDED (lazy local import, the LG-01f apps-loading-cycle guard) to
call that helper GATED on the league path-prefix and merge its 9 keys plus
`play_displayed_season_id` / `play_league_id` (the reverse-helper ids the topnav forms
need, since the nav has no `season`/`league` template var) — the keys are ABSENT
off-league and on the no-League `_fallback()` path. After the factor-out
`_build_dashboard_context` STOPS emitting the 9 play keys, KEEPS `playoff_tournament_id`
(read-only View-bracket link) + the read-only body keys; `top_bar_links` /
`top_bar_dashboard_url` unchanged. NEW nav DOM ids: toggle `play-nav-link`; wrapper
`topbar-play-dropdown`; items `topbar-play-start-season` / `-one-week` / `-two-months`
/ `-until-end` / `-one-week-live` / `-owner-evaluation` / `-next-season` /
`-play-single-round` / `-play-playoffs`; progress `topbar-play-progress` (+
`.play-progress-spinner` / `.play-progress-label` / `.play-progress-bar`); error
`topbar-play-error`. RETIRED dashboard ids: the full `{season,league}-dashboard-play-*`
advancement set + `-owner-evaluation-link` + `-next-season-form` + `-action-button`
wrapper + both inline poll `<script>` blocks DELETED; `-state-badge` /
`-view-bracket-link` / `-past-evaluations-link` / `-play-error` KEPT (read-only). All
**10 play endpoints reused verbatim** (`start_season` / `play_week` / `play_two_months`
/ `play_until_end` / `play_week_live` / `play_single_round` / `play_playoffs` /
`play_status` / `next_season` / `owner_evaluation`) — they already 302 (sync) / return
202 JSON (async) regardless of request origin, so a topnav submit needs no view tweak;
sync errors still land on the dashboard `play_error` banner. Async actions (Two Months
/ Until End / Play Playoffs) ship **progress-display only** — reuse `play_status` +
`_build_play_status_response` + `_celery_state_to_job_status` verbatim; the inline poll
JS is relocated to ONE copy in the league branch of `base.html` (DOM contract
`interceptAsync` / `startPolling` / `showProgress` / `clearPolling` /
`setDropdownDisabled` / `ensureErrorEl`, re-targeted at the `topbar-play-*` hooks); the
**Play→Stop swap, cancel/revoke, live incremental standings/leaders, and cross-page
resumable progress are DEFERRED to PLAY-01**. **No model change, no migration, no new
routes, no new view functions** — the only code edit beyond templates is the
`_build_play_controls_context` factor-out + the `league_nav` extension; pure
view-context + template relocation, no simulator touch, **no Score Calibration
re-baseline**. See
[ADR-0030](docs/adr/0030-play-controls-relocated-to-topnav.md), the seam contract
[`.claude/worktrees/nav-01-seam-contract.md`](.claude/worktrees/nav-01-seam-contract.md),
and the **NAV-01** subsection in
[`laserforce_simulator/matches/CLAUDE.md`](laserforce_simulator/matches/CLAUDE.md).

### PLAY-01 · [DONE] Live incremental stats + Stop/Cancel for multi-game runs

**Prio: High.** Today `play_two_months` / `play_until_end` enqueue `play_season_task`
(Celery), which commits each Round atomically and emits `PROGRESS`, but the dashboard
inline poll JS only `reload()`s on `status === "complete"` — so **nothing updates
until the whole run finishes**, and there is **no way to stop it mid-run** (ADR-0013
explicitly scoped cancel out). Three parts (all confirmed in scope):

1. **Live incremental stats** — surface progress as each matchday commits: the poll
   endpoint (`play_status`) returns the current partial standings / leaders so the
   dashboard re-renders progressively instead of only on completion.
2. **Stop / cancel** — a control to halt an in-progress run; already-played games
   stay committed (a cooperative stop the task checks between fixtures, plus
   `AsyncResult.revoke`). **Reopens the ADR-0013 cancel scope-out** — record the new
   decision.
3. **Play→Stop button swap** — while a multi-week / month / until-end run is in
   progress, **the Play control itself becomes a Stop/Cancel button** carrying a
   loading spinner with live progress (games played / total), and reverts to Play on
   completion or cancel. Applies on both the dashboard control and the NAV-01 `Play ▾`
   entry.

**Implementation surface:** extend the `play_status` polling JSON with the partial
standings/leaders payload; a cooperative-cancel flag the task body checks between
fixtures + a new cancel view/URL (`AsyncResult.revoke`); the dashboard inline poll JS
(and NAV-01 dropdown) for the Play↔Stop swap + the per-game progress spinner.

**[DONE] Shipped (2026-06-26).** Cooperative between-fixture cancel + live polling
stats — **NO `AsyncResult.revoke`** (the original surface note's `revoke` line is
superseded; revoke would leave a half-committed Round, forbidden by ADR-0016).
**Scope: async runs only** (`play_two_months` / `play_until_end` / `play_playoffs`);
the sync paths are untouched; no simulator/RNG touch ⇒ **no Score Calibration
re-baseline**. **Model:** `Season` grows `active_play_job_id`
(`CharField(max_length=255, null=True, blank=True, default=None)`) +
`play_cancel_requested` (`BooleanField(default=False)`) via migration
`0056_season_play_job_cancel` (2× `AddField`, no `RunPython` — existing Seasons take
the `null`/`False` defaults). **Cancel view:** NEW `matches.league_views.play_cancel(request, season_id)`
— POST-only (405 guard / 404 on missing Season), sets `play_cancel_requested = True`
(`save(update_fields=[...])`), returns `200 {"cancelled": True, "season_id"}`; URL name
`play_cancel`, path `/seasons/<int:season_id>/play-cancel/`. **Extended poll JSON:**
`_build_play_status_response` keeps its 5 keys (`status` / `completed` / `total` /
`error` / `season_id`) and ADDS `standings` (server-rendered HTML fragment), `leaders`
(3-key dict of HTML fragments — `points` / `tags` / `ratio`), `cancelled` (bool, `True`
only when the task returned `cancelled: true` on SUCCESS) — all recomputed **view-side
from committed rows each poll** via `compute_standings` / `compute_leaders` (NOT from
Celery task meta); `_celery_state_to_job_status` reused verbatim, **no new status
string**. **Enqueue edits:** the 3 async views set `active_play_job_id = result.id` +
clear `play_cancel_requested = False` (`save(update_fields=[...])`) before the unchanged
`202 {job_id, season_id}`. **Task control-flow:** NEW module-level helper
`matches.tasks._play_cancel_requested(season_id) -> bool` (single-column exists query,
re-read each call) checked at the task TOP and at the TOP of every fixture /
bracket-stage iteration; on a set flag the task **breaks cleanly and returns normally**
`{"completed", "total", "cancelled": True}` ⇒ Celery SUCCESS ⇒ `complete`; both
`play_season_task` + `play_playoffs_task` clear `active_play_job_id` via
`.update(active_play_job_id=None)` in their existing `finally` (fires on success /
cancel / failure, alongside `django.db.close_old_connections()`). **Render/resume:**
`_build_play_controls_context` adds `active_play_job_id` (flows through
`core.context_processors.league_nav` on the league-prefix path; absent off-league /
fallback). **Templates:** `topnav_play.html` adds the `topbar-play-stop` POST-to-`play_cancel`
control (renders iff `active_play_job_id`); `topnav_play_script.html` resumes polling on
load when `active_play_job_id`, patches the existence-guarded dashboard panels
`{season,league}-dashboard-standings-snippet` / `-leaders-points` / `-leaders-tags` /
`-leaders-ratio`, and wires Stop (fetch-POST, keeps polling until the task returns
`complete` + `cancelled`); NEW shared partials
`templates/_partials/dashboard_standings_snippet.html` +
`dashboard_leaders_snippet.html` single-source the patched markup for both dashboard
variants and the poll-rendered fragments. **Transport:** the existing NAV-01 polling
rail (the `play_status` poll) — **WebSockets/Channels deferred** (no cancel-latency win,
the safe-stop granularity is the fixture boundary not the network round-trip; needs an
ASGI/Channels/deploy migration). See
[ADR-0031](docs/adr/0031-cooperative-cancel-and-live-polling-stats.md) (which reverses
the ADR-0013 / ADR-0016 cancel scope-out — the `revoke` rejection stands), the seam
contract
[`.claude/worktrees/play-01-seam-contract.md`](.claude/worktrees/play-01-seam-contract.md),
and the **PLAY-01** subsection in
[`laserforce_simulator/matches/CLAUDE.md`](laserforce_simulator/matches/CLAUDE.md).

### DEL-01 · Delete League button

**Prio: Low.** No delete-League surface exists outside Django admin. Add a guarded
**Delete League** action — `POST /leagues/<int:league_id>/delete/` with a confirm
step — relying on the existing FK `on_delete` rules to cascade out Seasons /
`SeasonPhase`s / season-scoped Matches (sandbox Matches `SET_NULL` survive). Career
state (`current_team`, `OwnerEvaluation`, `TeamSeasonFinance`) is `CASCADE`/`SET_NULL`
per its model definitions. Mirror the existing league-screen view shell
(405-guard / `get_object_or_404` / redirect to the leagues list).

---

## Phase 5 — Infrastructure & League System

### LG-02 · Tournament formats

**Status: PART 1 sandbox formats all DONE; LG-02x-2 (Duos / Trios) deferred; Part 2 foundation (Part2a) DONE; Part2b (create-League composer + dormant phase columns) DONE; Part2c-1 (RR → single-elimination playoff embed) DONE; Part2c-2 SPINE (multi-RR play loop + `Match.season_phase` FK + cross-phase matchday offsetting) DONE; Part2c-3a (first alternative regular-season format — `double_round_robin` + `Match.leg`, wiring the Part2b dormant per-phase `schedule_format` column end-to-end) DONE; Part2c-3b (dormant per-phase `SeasonPhase.tournament_mode` field) DONE; Part2c-3c (mid-season tournaments — `strength` + `unseeded` build, the `tournament:<mode>` wire token, the standings-only compose-guard relaxation, and the play-loop barrier) DONE; Part2c-3d (per-tournament-block config — the dormant `SeasonPhase.tournament_format` column + the live `tournament_cut` top-N cut, the `tournament[:mode[:cut]]` wire grammar + cut-floor `ValueError`, the one-line build cut slice, and the composer cut input + disabled format select) DONE; Part2c-3e (non-single-elim finals embeds — the dormant `SeasonPhase.tournament_format` column flipped dormant→live so a `tournament` phase builds via ANY of the five formats, the 7 new per-format sub-config columns mirroring `Tournament` (4 series tiers + RR→DE wb/lb advancers + Swiss rounds), the 11-field `tournament:mode:cut:format:fsl:ssl:qsl:esl:wb:lb:swiss` wire grammar + three new shape `ValueError`s, the one-changed `Tournament.objects.create(format=phase.tournament_format, …)` build, and the live composer format picker + sub-config controls) DONE; Part2c-3f (season-linked playoff Match history + weekly playoff pacing — the Team History Overall-tab corpus widened to a `.distinct()` UNION of regular-season + season-embedded playoff rounds via the `match__series_match__node__tournament__season_phases__isnull=False` FK chain plus the filled `playoff_appearances` counter, and weekly playoff pacing via the new `play_next_bracket_round` STAGE drain + phase-aware `play_week`/`play_season_task` on the shared budget; Playoffs screen unchanged, no migration, no re-baseline) DONE; the mid-season `random_draw` build (re-draw the non-season-ending mid-season tournaments per season in `next_season`) is now the ONLY remaining Part2c follow-up, and is DEFERRED.**
Single-elimination (LG-02a), bulk intake + async play-all (LG-02a-2), best-of-N
Series (LG-02b), per-round Series escalation (LG-02b-2), double-elimination /
round-robin / RR→DE / Swiss (LG-02c+), and the Random Draw player pool
(LG-02x-1) are all shipped — their implementation notes now live in
[`PLAN-completed.md`](PLAN-completed.md). The deferred **LG-02x-2 Duos / Trios**
player-pool slice (+ `TournamentSubGroup`) is parked at the end of this plan (see
**Parked — deferred Tournament work** below). **Part 2** (the in-League composer)
is now sliced into **Part2a** (the `SeasonPhase` model + backward-compatible
read-path retrofit — **DONE**), **Part2b** (the League-create composer UI +
the dormant per-phase `schedule_format` + `SeasonPhase → Tournament` columns —
**DONE**), and **Part2c** (the heterogeneous multi-phase play loop + tournament
embed), itself re-sliced into **Part2c-1** (the RR → single-elimination playoff
embed — **DONE**), **Part2c-2** (the SPINE: multi-RR play loop +
`Match.season_phase` FK + cross-phase matchday offsetting — **DONE**), and
**Part2c-3** (the deferred remainder: per-phase format/seeding + mid-season
tournaments + non-single-elim embeds + season-linked playoff Match history —
NOT STARTED). The **LG-02-Part2 grill
(2026-06-04)** resolved that LG-02-Part2 **IS** the **LG-06** phased-lifecycle
model (off-season / regular / tournament = phase types) — see
[ADR-0023](docs/adr/0023-season-phase-composable-structure.md).

The **LG-02 grill (2026-06-02)** split this monolith. A Tournament is a
first-class **persisted, standalone sandbox** object — built and played in the
sandbox `/tournaments/` surface, **decoupled** from League / Season (no routing
through `generate_schedule`) — and the LG-02x player-pool formats (Random Draw /
Duos / Trios) were carved off as their own grill. The work is now sliced into
**Part 1** (sandbox standalone tournaments — LG-02a … LG-02x) and **Part 2** (the
in-League composable season-structure builder). See
[ADR-0019](docs/adr/0019-tournament-bracket-model.md) for the persisted
standalone-sandbox model decision and
[`.claude/worktrees/lg-02a-seam-contract.md`](.claude/worktrees/lg-02a-seam-contract.md)
for the locked LG-02a names.

Bracket rendered as a visual tree; results auto-advance winners (look at theC:\Users\Andrew Galkowski\PycharmProjects\zengm
screenshots in `/Screenshots_and_video_examples/`). Once tournaments are wired
into the League play loop (Part 2), relabel "Until end of season" → "Until
playoffs" (LG-01d ships the former label) and extend the play loop through
tournament completion.

#### Part 1 · Sandbox standalone tournaments

All Part-1 sandbox formats are **shipped** and their full implementation notes
have moved to [`PLAN-completed.md`](PLAN-completed.md): single-elimination
(LG-02a), bulk intake + async play-all (LG-02a-2), best-of-N Series (LG-02b),
per-round Series escalation (LG-02b-2), double-elimination / round-robin / RR→DE
/ Swiss (LG-02c+), and the Random Draw player pool (LG-02x-1). The one remaining
Part-1 slice — **LG-02x-2 (Duos / Trios)** — is deferred and parked at the
end of this plan.

#### Part 2 · In-League composable season structure

The **LG-02-Part2 grill (2026-06-04)** reframed this work. The original framing
("replace the hardcoded `draft → round-robin → playoff` baked into
`generate_schedule`") was inaccurate — `generate_schedule` is a *pure
single-round-robin fixture generator* with no draft/playoff notion; what
actually encodes "a Season is one round-robin" is a **spread assumption** across
the read path (`_is_finished` / `complete_if_finished`, `play_season_task`,
`season_schedule`, the dashboards). The grill resolved that **a Season's
structure is an ordered list of typed `SeasonPhase` rows**, and that **this
phase model IS the LG-06 phased-lifecycle model** — off-season / regular /
tournament are *phase types*, not a parallel abstraction (do not build two
season-structure models). The forward `tournament` phase will hold a
**one-directional `SeasonPhase → Tournament` FK** (Tournament stays
season-agnostic — [ADR-0019](docs/adr/0019-tournament-bracket-model.md)
survives). Recorded in
[ADR-0023](docs/adr/0023-season-phase-composable-structure.md). Part 2 is sliced
into Part2a (done) → Part2b → Part2c:

All Part-2 slices through **Part2c-3f** are **shipped** and their full
implementation notes have moved to [`PLAN-completed.md`](PLAN-completed.md)
(under the LG-02 · Part 2 section): the `SeasonPhase` foundation
(Part2a), the create-League composer UI + per-phase format / Tournament
columns (Part2b), and the Part2c embed sequence — RR →
single-elimination playoff (Part2c-1), the multi-RR play loop +
`Match.season_phase` FK (Part2c-2), `double_round_robin` + `Match.leg`
(Part2c-3a), the dormant `tournament_mode` field (Part2c-3b), mid-season
tournaments (Part2c-3c), per-tournament-block config — live
`tournament_cut` + dormant `tournament_format` (Part2c-3d),
non-single-elim finals embeds — the five-format build + per-format
sub-config (Part2c-3e), and season-linked playoff Match history + weekly
playoff pacing (Part2c-3f). The mid-season `random_draw` re-draw is the
only remaining Part2c follow-up (deferred):

  **(Deferred — own slice, post-Part2c-3)** A pre-selected per-League option to
  **randomize the mid-season tournaments per season**: the non-season-ending
  `tournament` phases that sit before the main `round_robin` + the end-of-year
  tournament are re-drawn (format / seeding) each cycle by `next_season` instead of
  carried forward verbatim. Selected beforehand as a League-level toggle; only
  meaningful once the seeding-mode field + per-tournament-block config above exist.

---

---

## Phase 5.5 — Single-Player Career Mode

A single-user play mode where the user acts as a team manager navigating a league season. This phase
sits between the League system (Phase 5) and full multiplayer (Phase 6).

### SUB-01 · Sub-leagues + per-sub-league rotating map pools

**Re-sliced (2026-06-17, user decision) into THREE sequenced pieces.** The
original monolith — "first-class `SubLeague` model + per-sub-league rotating map
pools" — was too coarse: the *deterministic-rotation map mode* LG-01j deferred
does not actually require a `SubLeague` model, only a Season-level ordered map
list. The pieces (mirroring how LG-02-Part2 was sliced):

1. **[DONE] Season-level `rotate_by_matchday` arena-map mode** — shipped; full
   impl note moved to [`PLAN-completed.md`](PLAN-completed.md). NO `SubLeague` model.
2. **LG-07 · Member nights** — sequenced **NEXT** (already its own PLAN item in
   Phase 3 backlog; just noted here for ordering — the third SUB-01 slice waits
   on it).
3. **[NOT STARTED] Sub-league intra-pool scheduling** — the first-class
   `SubLeague` concept + per-sub-league rotating pools, sequenced **AFTER LG-07**
   (re-scoped below).

- **SUB-01 (piece 1) · [DONE] Season `rotate_by_matchday` arena-map mode.**
  Shipped — full implementation note moved to
  [`PLAN-completed.md`](PLAN-completed.md) (under **SUB-01 · piece 1**). A 4th
  `Season.map_mode` value driving a Season-level author-ordered ArenaMap rotation
  keyed on matchday; satisfies LG-01j's deferred "mode (c)" at the Season level
  (the per-*sub-league* rotation remains the third slice). NO `SubLeague` model.

- **SUB-01 (piece 3) · [NOT STARTED] First-class `SubLeague` + per-sub-league
  rotating map pools — needs its own grill + an ADR.** Sequenced **AFTER LG-07
  member nights** (piece 2). Introduce **sub-leagues** as a first-class domain
  concept: an optional partition of a `Season`'s enrolled Teams into named groups
  (conferences / divisions / pools), modelled as a new `SubLeague` container under
  `Season` with its own `teams` M2M and an ordered list of `ArenaMap`s. Each
  Round's map would then resolve from the **sub-league's** pool by matchday
  (`maps[matchday % len(maps)]`) — the per-sub-league analogue of the Season-level
  rotation piece 1 already ships. The Season-level `rotate_by_matchday` mode does
  **not** subsume this: it rotates one Season-wide list; this slice rotates a
  *different* list per sub-league, which requires the sub-league partition to
  resolve a fixture **unambiguously to one pool** — i.e. **intra-pool vs
  cross-pool fixtures** must be a first-class scheduling concept (a fixture
  between two sub-leagues has no single pool). That schedule-generation
  interaction (intra/cross-pool fixture generation, a sequencing decision LG-02
  also leans on) is the risky core to grill. Carved out of LG-01j on 2026-05-28
  (no `SubLeague` model existed then; the user deferred the introduction until the
  career-mode slice was in place). Depends on **LG-07** (sequenced after member
  nights) and on **CAR-03** (sub-league grouping is most useful once manager-mode
  career play is driving the Season). Adds the **SubLeague** term to CONTEXT.md
  and ships an **ADR for the new model + the intra/cross-pool schedule-generation
  interaction**.

---

---

## Phase 6 — Users and Multiplayer

### UX-01 · User accounts and team ownership

Django auth system (email + password). Open self-registration — anyone can create an account.
Admins can remove user accounts via Django Admin.

Permissions: only team owners can edit their teams/players; read-only access to others.
Users can see the teams, players, leagues, seasons, and tournaments they have created.

League access is **closed by default** (invite-only). League creators can set a league to open
(anyone can join) or send invitations to specific users.

Google/OAuth social login is deferred — see Deferred Items section.

### UX-02 · User–player link

Each user account may be linked to exactly one `Player` record (one-to-one). This link represents
a self-insert — the user's personal profile of what they believe their own stats are or aspire to be.
The linked player is a vanity record and does not automatically appear on any simulated team.

This should look at the screenshots existing within the /Screenshots_and_video_examples/ directory.

---

---

## Phase 8 — Angular Frontend Migration

Replaces Django's server-rendered HTML templates with an Angular single-page application (SPA).
Django becomes a pure API backend; Angular handles all UI in the browser. This phase requires Phase 5's
API-02 (REST API) to be complete and deployed (Phase 7) before starting.

**Approach:** migrate one feature area at a time. Django templates remain live until the Angular
equivalent is complete and verified. Django Admin is a permanent exception and is never migrated.

### ANG-01 · Harden and complete the REST API (prerequisite)

Before building Angular against it, ensure:

- All endpoints needed by the UI exist: teams, players, matches, rounds, events, maps
- Consistent JSON envelope (data, pagination, errors)
- Filtering and pagination on list endpoints
- Proper HTTP error codes (400 for validation, 404 for missing records, etc.)

### ANG-02 · CORS configuration

During development Angular runs on `http://localhost:4200` and Django runs on `http://localhost:8000`.

- Add `django-cors-headers` to `requirements.txt`
- Add `CorsMiddleware` to `MIDDLEWARE` (before `CommonMiddleware`)
- Set `CORS_ALLOWED_ORIGINS = ["http://localhost:4200"]` for dev; production domain added when known

### ANG-03 · JWT authentication

- Add `djangorestframework-simplejwt` to `requirements.txt`
- Add `/api/token/` (login) and `/api/token/refresh/` endpoints
- **Access token** stored in memory (not localStorage — avoids XSS token theft)
- **Refresh token** stored in an httpOnly cookie (survives page refresh without re-login)
- Angular `HttpInterceptor` attaches `Authorization: Bearer <token>` to every API request automatically

### ANG-04 · Angular project scaffold

One-time setup in a `/frontend/` directory at the repo root.

```bash
npm install -g @angular/cli
ng new frontend --routing --style=scss --strict
cd frontend
ng add @angular/material
```

### ANG-05 · Angular API services

One Angular service per Django API resource. Components never call `HttpClient` directly.

```
TeamsService     → GET/POST/PATCH /api/teams/
PlayersService   → GET/POST/PATCH /api/players/
MatchesService   → GET/POST       /api/matches/
RoundsService    → GET            /api/rounds/<id>/
EventsService    → GET            /api/rounds/<id>/events/
MapsService      → GET/POST       /api/maps/
```

### ANG-06 · Migrate views by feature area

Migrate one area at a time in order of complexity. Each item: build the Angular route/component,
verify feature parity with the existing Django template, then remove the Django template + view.

1. **Teams list & detail** — simple CRUD table + form; good first Angular component to build
2. **Player add/edit** — stat form with live `overall_rating` preview
3. **Match list & create** — team picker, match creation, results list
4. **Round detail** — per-player stat table, MVP scores
5. **Event timeline** — filtered event log, color-coded by type (SIM-05 replay controls slot in here)
6. **Map editor** — most complex: canvas overlay, zone painting, sight-line drag-select (migrate last)

### ANG-07 · Serve Angular from Docker (nginx sidecar)

Once Angular is built (`ng build --configuration production`), serve it via an nginx sidecar service.
nginx serves the Angular static files on port 80 and proxies `/api/` requests to the Django container
on port 8000. Add `nginx.conf` and update `docker-compose.yml` with the `nginx` service.

### ANG-08 · Remove Django template views

Once each Angular view is verified, delete the corresponding Django template file and its
HTML-serving view function. Keep the API endpoint. Update URL routing to remove the old path.
The app should have zero `.html` template files by the end of this phase, except Django Admin
(which is a permanent exception and stays indefinitely).

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

```
Phase 0 (Fixes) ← complete
  → Phase 7 (Docker & Deployment) ← do this first; ship the Django template UI to prod early
  → Phase 1 (Map Integration)
    → Phase 2 (Stats Integration)
      → Phase 3 (Simulation Mechanics)
        → Phase 4 (Analytics — most items can run in parallel with Phase 3)
          → Phase 5 (Infrastructure & League)
            → Phase 5.5 (Single-Player Career Mode)
              → Phase 6 (Users and Multiplayer)
                → Phase 8 (Angular Frontend Migration)
                  (requires Phase 5 API-02 REST API)
```

Phase 4 items RES-01 (accuracy %), RES-02 (SP chart), RES-03 (missile log), and SIM-01 (document weights)
are quick wins that can be done any time after Phase 0.

Phase 7 (Deployment) can be done in parallel with any feature phase — re-deploy as features land.

---

## Deferred Items

The following were explicitly scoped out and should not be implemented until re-evaluated:

- **Mirrored/reflective walls** (MAP-07) — shot-bouncing mechanic; deferred from Phase 1
- **Per-stat-per-role weight tuning** (STAT-02 follow-up) — granular multipliers per stat per role;
  deferred until baseline simulation data exists to inform the values
- **Google/OAuth social login** (UX-01) — deferred from Phase 6; email/password only for now
- **Custom domain** — deferred until the project grows; fly.dev subdomain is sufficient for now
- **Goal-recompute throttling** (MOVE-04) — behavioural perf lever (staler goals every *N* ticks);
  out of MOVE-02 scope, opened only if the MOVE-02 path cache alone is insufficient for the
  map-mode perf target

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
definition chosen in the grill is documented in CONTEXT.md if it introduces new domain language.

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

### CAL-01 · Score Calibration re-baseline

**Prio: Medium (found 2026-06-25).** Rebase the map-model average scores toward the
documented **Score Calibration Targets** (Commander 9,952 / Heavy 6,482 / Scout
5,102 / Ammo 3,242 / Medic 2,282 — `matches/CLAUDE.md`). This is the long-pending
post-MOVE-01 re-baseline and **absorbs** the seeded-outcome deltas from MOVE-05 /
MOVE-06 (and SIM-12) in a single pass — do **not** create separate re-baseline
obligations for those. **Locked (grilled 2026-06-25):** **tune** the existing action
weights / hit-chance / movement constants to converge on the targets; keep the 19-stat
model + role MVP/weight formulas as-is for now. The **deeper rework** the user wants
scheduled later — revisiting action selection, movement selection, and goal selection
themselves — is tracked by `MECH-07` below (extended to cover actions/movement, not
just goals); do that only if calibration tuning alone cannot hit the targets.

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

### RV-05 · Round report PDF: chart/graph section (extends RV-03)

Add a **charts section** to the RV-03 PDF (same `GET /matches/game-round/<id>/export/` endpoint — one
PDF = summary + scoreboards + per-player table + resource summary + **graphs**). Render the same four
event-page charts (SP, shots, lives, points over time) server-side via the shared
`pdf_charts.py` helper and embed them as ReportLab `Image` flowables after the existing tables. The
"[Simulated]" watermark on simulator-generated rounds (RV-03) applies to the chart pages too.

**Depends on:** RV-03 (the export endpoint + ReportLab scaffold must land first; RV-05 amends its
scope). **Scope:** read-only/derived — no model change, no migration, no simulator change. **Acceptance:**
the exported PDF contains one rendered graph per event-page chart with the same data as the
on-screen charts; an empty/early-eliminated round degrades gracefully (axis with no series, no crash);
the watermark appears on chart pages for simulated rounds.

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

### SIM-12 · Clamp negative action weights before `random.choices`

Discovered during the SIM-01 grill/review (May 2026). `combat.plan_action` builds the 9-slot weight
vector and feeds it straight to `random.choices` **without clamping per-element negatives to 0**. CPython's
`random.choices` only raises when the *total* weight is ≤ 0 — it does **not** reject an individual negative
weight; instead the negative bucket becomes unreachable in the cumulative-weight bisect **and silently skews
the neighbouring buckets' probabilities**. Several role branches legitimately emit one negative slot today:
Heavy/Commander `only_move` while missiles remain (`25/15 → 5` after the MOVE-03 hold draw, then `−15`
missile cost → `−10`/`−5`), Heavy `only_move` while capturing (`5 − 10 = −5`), and Scout `tag_player` when
shots-critical with no ammo ally (`_SCOUT["seek_no_ammo_tag"]=50` > post-baseline tag `40` → `−10`). So the
action distribution on those ticks is subtly wrong, not crashing. SIM-01 deliberately left this **unfixed**
(it is a behavioural change, not a documentation change) and pinned only the true non-raising invariant
(`test_plan_action_never_raises_*` / `test_plan_action_total_weight_is_positive` in `test_weights.py`).

**Scope:** add a single non-negative clamp on the final weight vector in `plan_action` (e.g.
`weights = [max(0, x) for x in weights]`) immediately before `random.choices`, *after*
`apply_decision_making_spread` and the cooldown/stamina post-processing. Decide in this task's grill whether
the clamp belongs in `plan_action` (one site, covers all roles) or pushed back into the role functions /
helper subtraction sites (more surgical but many sites). **Tests:** convert the role-function-layer
`test_scout_shots_critical_tag_goes_negative_xfail` from `xfail` to a real assertion once the clamp lands at
the right layer (or keep it documenting the raw role-fn output and add a new `plan_action`-layer test that
the vector handed to `random.choices` has **every element ≥ 0**, not just total > 0 — strengthening the
SIM-01 `total > 0` invariant). Also pin the three known negative-emitting branches (Heavy missile, Heavy
capture, Scout shots-critical) so the clamp is regression-guarded per branch.

**This re-baselines seeded outcomes** (the corrected probabilities shift which Action is rolled on the
affected ticks) — fold it into the single pending post-MOVE-01 Score Calibration re-baseline; do **not**
create a separate re-baseline obligation. No migration, no new domain term, no ADR (a one-line clamp is
reversible and unsurprising).

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

### LG-07 · Member night simulator (sandbox mode)

**[NOT STARTED — needs its own grill.]** The deferred **`member_night`** phase
type (declared inert in the `SeasonPhase.PHASE_TYPE_CHOICES` enum since
LG-02-Part2a — see
[ADR-0023](docs/adr/0023-season-phase-composable-structure.md)). A "member
night" is the real-world casual/social play session a laser-tag venue runs
between competitive fixtures — ad-hoc games among whoever shows up, not a
structured round-robin or bracket. As a `SeasonPhase` it would model a sandbox
play window inside a Season's flow (e.g. RR → member night → Tournament): how
participants are gathered, what (if anything) it contributes to **Standings**
(likely nothing — it is social, not ranked), and how its games are simulated and
stored. **Open questions for the grill:** does it produce `season=NULL`
sandbox-style Rounds or season-attached ones; does it touch Standings / career
stats at all; what is its play-loop UI; does it reuse the Random-Draw player-pool
intake (LG-02x-1) for "whoever shows up"; how does the Part2c multi-phase play
loop advance *past* a member-night phase. Until grilled, the enum value stays
documented-but-inert (only `round_robin` resolves fixtures).

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
Calibration Targets** (Commander 9,952 / Heavy 6,482 / … — `matches/CLAUDE.md`) "were
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
CONTEXT.md **Compute tier** term.
