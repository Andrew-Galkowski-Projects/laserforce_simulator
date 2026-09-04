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

### DEL-01 · [DONE] Delete League button

**Prio: Low.** No delete-League surface exists outside Django admin. Add a guarded
**Delete League** action — `POST /leagues/<int:league_id>/delete/` with a confirm
step — relying on the existing FK `on_delete` rules to cascade out Seasons /
`SeasonPhase`s / season-scoped Matches (sandbox Matches `SET_NULL` survive). Career
state (`current_team`, `OwnerEvaluation`, `TeamSeasonFinance`) is `CASCADE`/`SET_NULL`
per its model definitions. Mirror the existing league-screen view shell
(405-guard / `get_object_or_404` / redirect to the leagues list).

**[DONE] Shipped (2026-06-28).** Grilling the one-line cascade assumption surfaced
three gaps and reframed DEL-01 as a **full teardown** of all data a career-mode
(`League.mode == "league"`) League owns, identified by **PK/FK identity, never by
name**, in one atomic block — see
[ADR-0032](docs/adr/0032-delete-league-full-teardown.md). `Match.season` is
**SET_NULL**, so the league's played Matches (regular-season + embedded-tournament
bracket, the latter reached via `series_match__node__tournament`) are **explicitly
deleted** rather than orphaned into the sandbox match list (`GameRound` →
`GameEvent` / `PlayerRoundState` cascade clean). Season-embedded playoff
**Tournaments** (collected via `SeasonPhase.tournament` before the cascade nulls
the link) are torn down with their participants / nodes / series. The generated
competitive **Teams + free-agent pool + their Players** — which have no League FK
(`current_team` / `free_agent_pool` SET_NULL, `Season.teams` M2M; `Player.team`
CASCADE) — are deleted under a **post-teardown zero-reference guard** keyed on
PK/FK (`red_matches` / `blue_matches` / `enrolled_seasons` / `managed_in_leagues`
/ `free_agent_pool_for`), leaving behind anything still referenced (safe over
complete). Correctness rests on the one-context ownership invariant now in the
CONTEXT.md **Team** / **Player** entries (a Team + its Players belong to exactly
one context, never shared). Ships as a GET-confirm / POST-teardown view
(`matches.league_views.league_delete` + the `_teardown_league` helper), gated by
`_is_career_league` (`HttpResponseBadRequest` 400 on non-`league` mode), with both
entry points (`league-dashboard-delete-link` + per-row
`league-list-delete-link-{id}`, gated `league.mode == "league"`), a confirm
template (`templates/leagues/league_confirm_delete.html`), URL name `league_delete`,
redirect to `league_list`, and `matches/tests/test_league_delete.py`. **No model
change, no migration, no simulator / RNG touch, no Score Calibration re-baseline.**
See [ADR-0032](docs/adr/0032-delete-league-full-teardown.md), the CONTEXT.md
**Team** / **Player** one-context ownership invariant, the seam contract
[`.claude/worktrees/del-01-seam-contract.md`](.claude/worktrees/del-01-seam-contract.md),
and the **DEL-01 delete league** subsection in
[`laserforce_simulator/matches/CLAUDE.md`](laserforce_simulator/matches/CLAUDE.md).

### CRE-01 · [DONE] League template chooser + Advanced screen + Difficulty

**Prio: Medium.** The create-League surface
(`matches.league_views.league_create` / `CreateLeagueForm` / `templates/leagues/create.html`)
is a single long form exposing every knob — league/season/manager names, team
count, the phase composer (RR / double-RR / tournament / member-night, with
per-tournament mode / cut / format / series / wb-lb / swiss sub-config), map
mode + pool + rotation, the finance + luxury-tax toggles, and stat mean/std-dev.
This is the right surface for power users but overwhelming as the default
entry point. Add a **preset chooser** as the new default and **relocate the
current full form to an "Advanced" screen**.

**Routing (locked, user decision).** `/leagues/create/` becomes the **preset
chooser** (the new landing the `league-create-link` nav target keeps pointing
at, no nav edit); the current full form moves to **`/leagues/create/advanced/`**
(new URL name e.g. `league_create_advanced`), reachable via an "Advanced setup"
link on the chooser. The existing `league_create` view + `CreateLeagueForm` +
`templates/leagues/create.html` are **renamed/relocated, not rewritten** — the
advanced screen is today's form verbatim at the new URL.

**Preset model (locked, user decision).** A **dropdown of 4–5 named presets**
modelled on ZenGM's `lol.zengm.com/new_league` **"game type"** dropdown: pick a
preset → its baked-in config populates → one **Create League** button. Each
preset is a **server-side named config** (a hardcoded constants table for the
first slice — NOT user-savable) that resolves to the **same field bundle +
phase-composer wire tokens** the advanced `CreateLeagueForm` already consumes, so
preset creation reuses the **existing `league_create` creation path verbatim**
(generate teams → League + draft Season → enrol → `SeasonPhase` rows →
baseline ratings/finance) with **no new creation logic**. Open question for its
own grill: whether the chosen preset creates immediately in one click vs lands
on the Advanced form **pre-filled** for review — the ZenGM "game type" pattern is
one-click-from-dropdown, so default to **one-click create** with the Advanced
screen available for full control.

**First-slice preset set (candidate — pin during grill).** Spanning both the
team-count axis (4 / 8 / 16) and the ruleset axis the user picked (Quick Play /
Classic / Career / Double-RR):

1. **4-Team Quick** — 4 teams, single round-robin + single-elim playoff, no
   finance. Fastest casual season.
2. **8-Team Classic** — 8 teams, single round-robin + single-elim playoff, no
   finance.
3. **8-Team Career** — 8 teams, `finance_enabled` on, name-your-team
   (`current_team` / CAR-01), RR + playoffs, owner firing (CAR-02) in play.
4. **8-Team Double-RR** — 8 teams, `double_round_robin` regular season +
   single-elim playoff, no finance.
5. **8-Team Member Nights** — 8 teams, RR with an interleaved `member_night`
   phase (LG-07a) + playoff.

**Deferred preset — 16-Team Conferences.** A 16-team preset partitioned into
**conferences** depends on the first-class **`SubLeague`** concept +
intra/cross-pool scheduling, which is **SUB-01 piece 3 (NOT STARTED, deferred)**.
Until SUB-01 lands, either omit this preset or ship a flat 16-team variant (no
conferences). Pin during the CRE-01 grill; do **not** build conferences here.

**Implementation surface.** New `league_create_presets` view + `/leagues/create/`
template (the dropdown + Create button); a `LEAGUE_PRESETS` constants table
mapping each preset name → the `CreateLeagueForm` field values + the
`phase_composer` token string (`round_robin:<fmt>` / `tournament:<...>` /
`member_night`) + map-mode/finance flags; rename the current view/URL/template
to the `*_advanced` names; an "Advanced setup" link on the chooser and a "Use a
preset" link back. **No model change, no migration, no simulator touch, no Score
Calibration re-baseline** — pure view/template + a presets config table reusing
the shipped creation path. Reuses `phase_composer.parse_phase_composition`
(presets feed it the same wire grammar). Adds tests for the chooser routing, each
preset's resolved League/Season/phase shape end-to-end (no `_generate_teams`
mock), and that the relocated advanced form still creates as before.

**[DONE] Shipped (2026-06-30).** The first-slice "preset" framing shipped under
the grilled **League template** name (the CONTEXT.md **League template** /
**League difficulty** terms). **Routing:** `/leagues/create/` now serves the
**template chooser** (view `matches.league_views.league_create`, URL name
`league_create` **unchanged** — so the raw `league-create-link` nav target needs
no edit) and today's full form relocates **verbatim** to
`/leagues/create/advanced/` (NEW view `league_create_advanced`, NEW URL name
`league_create_advanced`, inserted between the `create/` and `<int:league_id>/`
patterns). **Templates table:** NEW module `matches/league_templates.py` ships a
frozen `LeagueTemplate(key, label, num_teams, phases, finance_enabled=False,
challenge_fired_luxury_tax=False, mean=50, std_dev=15, map_mode="none")` dataclass
+ a **5-row** `LEAGUE_TEMPLATES` tuple (`LEAGUE_TEMPLATES_BY_KEY` lookup) — server
constants, NOT persisted / NOT user-savable / NO `League` back-reference: `4_team_quick`
(4, `"round_robin,tournament"`), `8_team_classic` (8, `"round_robin,tournament"`),
`8_team_career` (8, `"round_robin,tournament"`, `finance_enabled=True`),
`8_team_double_rr` (8, `"round_robin:double_round_robin,tournament"`), and
`8_team_member_nights` (8, `"round_robin,member_night,tournament"`) — the 16-Team
Conferences preset stays deferred behind SUB-01 piece 3. **Difficulty:** a NEW
**transient** `CreateLeagueForm.difficulty` `ChoiceField` (`DIFFICULTY_CHOICES =
(("easy","Easy"),("medium","Medium"),("hard","Hard"))`, `initial="medium"`,
`required=False`, widget id `league-create-difficulty`, inserted after
`manager_team_name` / before `season_name`), shared by both the chooser-assembled
form and the Advanced form and consumed only at create time — **NO `League.difficulty`
field, NO migration**. **Selection rule:** difficulty picks WHICH generated team
becomes the manager's `current_team` by strength rank — the new
`_rank_teams_by_strength(teams)` (mean active-roster `overall_rating` DESC, `team_id`
ASC, the **same** ranking `_seed_team_budgets_by_strength` now calls after being
refactored to reuse it) indexed `{easy: 0 (strongest), medium: N//2, hard: N-1
(weakest)}` via `_pick_manager_team(created_teams, difficulty)` (falsy ⇒ `"medium"`).
This **supersedes the CAR-01 alphabetical-first auto-pick** (`sorted(..., key=name)[0]`
is gone); a non-blank `manager_team_name` still **renames** whichever team difficulty
picked (the two compose). **Shared creation path:** today's `league_create` body is
extracted **verbatim** (only the manager pick swapped) into ONE
`@transaction.atomic _create_league_and_season(form) -> Season` helper that BOTH the
chooser POST and the Advanced POST call — no duplicated/new creation logic; the
chooser POST resolves the chosen `LeagueTemplate` and assembles form data via
`_template_to_form_data(template, *, league_name, difficulty)` so it reuses the
Advanced validation + `phase_composer.parse_phase_composition` verbatim. **Templates:**
the old `templates/leagues/create.html` content moves to `create_advanced.html`
(action/back-link/heading + a `{{ form.difficulty }}` row adjusted); `create.html`
becomes the new chooser (`league-create-template` / `-league-name` / `-difficulty`
/ `-submit` / `-advanced-link`). **Test migration:** existing full-field-set POSTs
that exercised the form move to `reverse("league_create_advanced")` (the chooser POST
no longer accepts the raw full field set), and `TestCar01ManagerTeamName`'s two
blank-name "alphabetical-first" fallback tests are rewritten to assert the default
**Medium** strength pick (rank `N//2`). **Scope-out:** no model change, no migration,
no simulator/RNG touch, no Score Calibration re-baseline. CRE-01 is the
*selection-based* difficulty; the *generation-based* power-tiered complement is the
sibling **CRE-02** below (shipped 2026-09-02 as **League spread**). See the seam
contract
[`.claude/worktrees/cre-01-seam-contract.md`](.claude/worktrees/cre-01-seam-contract.md),
the CONTEXT.md **League template** / **League difficulty** terms, and the **CRE-01
league templates + difficulty** subsection in
[`laserforce_simulator/matches/CLAUDE.md`](laserforce_simulator/matches/CLAUDE.md).

### CRE-02 · [DONE] Tiered expected-finish team generation

**Prio: Low (was deferred for its own grill; that grill ran and it shipped — see the
note below).** The dramatic complement to CRE-01's
*selection-based* **League difficulty**. Today CRE-01 generates all N teams from
one stat distribution and merely re-picks which equal team the manager gets, so
Easy↔Hard differ only by the modest random spread among equally-generated teams.
CRE-02 makes the **league itself power-tiered**: generate the N teams from
**different starting stat distributions keyed to an expected finishing order** —
a projected #1 seed generated from a stronger mean, the projected wooden-spoon
team from a weaker mean, a monotonic gradient between — so the league has a real
preseason favourite/underdog structure. The manager is then assigned a team by
the chosen difficulty (Easy → a top-projected team, Hard → a bottom-projected
team), making "hard mode" a genuinely weaker roster relative to the field rather
than a coin-flip among equals.

**Open questions for its own grill (all resolved — see the shipped note below):** the
gradient shape (linear mean ramp from
`mean+Δ` to `mean−Δ`? geometric? a fixed tier table?) and Δ magnitude vs the
existing `std_dev`; whether the gradient is a per-League-difficulty knob or a
fixed league property; how it composes with the **`map_mode`** / finance /
phase-composition template fields; whether the expected order is surfaced anywhere
(a preseason power-ranking) or stays implicit; and whether it threads through the
existing `_generate_teams` (a per-team mean override) or a new generator entry
point. Folds into the same create surface as CRE-01 (the template chooser + the
Advanced form). Likely a real **`_generate_teams`** signature change (a per-team
mean vector) — confirm during the grill whether that re-baselines any
generation-dependent test fixtures.

**[DONE] Shipped (2026-09-02).** The grill answered every open question above and the
work shipped under the grilled **League spread** name (the CONTEXT.md **League
spread** term, plus an amended **League difficulty** entry). **Gradient shape:**
linear and **mean-preserving** — `tier_mean(i) = clamp(mean + Δ − 2Δ·i/(N−1), 0.0,
100.0)` for 0-based `i` in generation order, index 0 the strongest tier and index N−1
the weakest; not geometric, not a fixed tier table. **Δ is a fixed League property,
not a per-difficulty knob** — a NEW **transient** `CreateLeagueForm.league_spread`
`ChoiceField` (`LEAGUE_SPREAD_CHOICES = (("even","Even"),("tiered","Tiered"),
("steep","Steep"))`, `initial="even"`, `required=False`, widget id
`league-create-league-spread`, declared immediately after the CRE-01 `difficulty`
field so the two transient create-time selectors sit together) resolving through the
NEW `teams.player_generator.LEAGUE_SPREAD_DELTAS = {"even": 0.0, "tiered": 8.0,
"steep": 16.0}`. **Δ sized against `std_dev`:** a Team's mean active-roster
`overall_rating` averages 114 i.i.d. stat draws, so its own SD is only ≈1.4 even at
`std_dev=15` — an 8-team League's expected best-to-worst gap is ~4 points at Even
versus ~16 at Tiered and ~32 at Steep, which is what makes Easy↔Hard a genuinely
different roster relative to the field instead of a coin-flip among equals.
**Expected order is NOT surfaced — ONE ORDERING ONLY:** the tier a Team was drawn
from is a *generation input*, never persisted, never queried, never rendered, so
there is **no "projected finishing order" concept and no preseason power-ranking
screen**; after generation the League still has exactly one ordering, the measured
strength rank `_rank_teams_by_strength` (mean active-roster `overall_rating` DESC,
`team_id` ASC), which `_pick_manager_team` keeps reading unchanged. **Threading:** the
existing `_generate_teams` — **not** a new generator entry point — gains an
**additive keyword-only** `tier_means: list[float] | None = None` appended last, so
all three call sites stay source-compatible and only the league-create one is edited.
`tier_means=None` (the default) is **byte-identical to pre-CRE-02** generation
(identical RNG consumption; Even passes `None`, **not** an all-equal list); a list of
length `num_teams` draws Team `i`'s players — bench players 7+ included — at
`tier_means[i]`; a length mismatch raises `ValueError` **before any ORM write**. NEW
pure `teams/player_generator.py::compute_tier_means(num_teams, mean, delta) ->
list[float]` (no RNG / no I/O / no Django, so the LG-00 `TestNoDjangoImportsLeaked`
guard still holds; degenerate `delta == 0` **or** `num_teams < 2` ⇒ `[float(mean)] *
num_teams`, doubling as the `N−1` divide-by-zero guard); `_build_player_kwargs`'s
`mean` type hint widens `int` → `float` (its only change — the body is untouched).
**Composition with the other create fields:** none — the spread is orthogonal to
`map_mode` / finance / phase composition and to the **League template** table, and it
**re-baselines no generation-dependent fixture**: the ramp is symmetric about `mean`,
so the League's average talent is unchanged at every spread (caveat: the clamp is
applied *after* the ramp, so mean-preservation holds only where the ramp stays inside
`[0, 100]` — at an extreme `mean` the clamped end flattens and the average is pulled
toward the middle). **Create surface:** the CRE-01 sole creation path
`_create_league_and_season` builds the vector immediately before the `_generate_teams`
call — `spread = cleaned.get("league_spread") or "even"` → `delta =
LEAGUE_SPREAD_DELTAS.get(spread, 0.0)` → `tier_means = compute_tier_means(
cleaned["num_teams"], cleaned["mean"], delta) if delta else None` — two independent
layers (`or "even"` for falsy, the dict `.get` default for unknown-but-truthy) making
any bad value harmless, and the `if delta else None` step is what guarantees the
byte-identical Even path. The selector lives on the **Advanced form ONLY**
(`templates/leagues/create_advanced.html` gains one `<div class="mb-3">` block after
the mean / std-dev row); the one-click template chooser always creates an **Even**
League — `_template_to_form_data` gains the static key `"league_spread": "even"` with
its **signature unchanged** (the chooser has no spread selector, so the value is a
constant, not caller data) and `LeagueTemplate` gains **no** field.
**Scope-out (locked).** **No model change, no migration, no ADR** — the tier is a
generation input, never persisted, and a transient form field + a pure function + an
additive kwarg is reversible. Competitive Teams only: `_generate_free_agents` is
untouched and the League's free-agent pool is still drawn at the flat `mean`.
Create-time only: `next_season` / `_develop_league_for_new_season` do **not** re-apply
the spread — **LG-04 development** owns Team strength thereafter. `_pick_manager_team`
/ `_rank_teams_by_strength` / `_seed_team_budgets_by_strength` and the LG-00
`draw_stats` / `draw_preferred_roles` / `assign_slots` surface are all zero-diff. **No
simulator / RNG-into-the-sim change and no Score Calibration re-baseline.**
`templates/leagues/create.html` (the chooser) is not edited; nothing new is exposed on
the REST API, serializers or admin. **Tests:** `TestComputeTierMeans` appended to
`teams/tests/test_player_generator.py` (pure `unittest`), NEW
`teams/tests/test_generate_teams_tiered.py` (`TestGenerateTeamsTierMeans`) and NEW
`matches/tests/test_league_spread.py` (`TestCre02LeagueSpreadFormField` /
`TestCre02AdvancedCreateWithSpread` / `TestCre02TemplateFormDataIsAlwaysEven`).
`_create_league_and_season` builds an **unseeded** `random.Random()`, so **no
view-layer test asserts anything about strength** — view tests cover form validity,
HTTP status, object counts and DOM ids only, and every strength assertion lives at the
`_generate_teams` layer with an injected `rng=random.Random(42)`, asserting a
direction/magnitude gap rather than an exact point total. See the seam contract
[`.claude/worktrees/cre-02-seam-contract.md`](.claude/worktrees/cre-02-seam-contract.md),
the CONTEXT.md **League spread** term (and the amended **League difficulty** entry),
the **CRE-02 tiered generation** subsection in
[`laserforce_simulator/teams/CLAUDE.md`](laserforce_simulator/teams/CLAUDE.md), and the
**CRE-02 league spread** subsection in
[`laserforce_simulator/matches/CLAUDE.md`](laserforce_simulator/matches/CLAUDE.md).

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

### SUB-01 · Conferences + per-Conference rotating map pools

**Re-sliced (2026-06-17, user decision) into THREE sequenced pieces.** The
original monolith — "first-class `SubLeague` model + per-sub-league rotating map
pools" — was too coarse: the *deterministic-rotation map mode* LG-01j deferred
does not actually require a partition model, only a Season-level ordered map
list. The pieces (mirroring how LG-02-Part2 was sliced):

1. **[DONE] Season-level `rotate_by_matchday` arena-map mode** — shipped; full
   impl note moved to [`PLAN-completed.md`](PLAN-completed.md). NO partition model.
2. **LG-07 · Member nights** — **[DONE] LG-07a core slice shipped** (its own PLAN
   item in Phase 3 backlog; noted here for ordering — piece 3 was sequenced after
   it). Only the deferred **LG-07b** player-stat filter remains, which did not block
   piece 3.
3. **Conference epic (was "Sub-league intra-pool scheduling")** — the first-class
   partition concept + per-partition rotating pools. The CONF-01 grill
   (2026-06-29) **reframed** this around the maintainer's actual target — the ZenGM
   **worlds** game type (multiple regional leagues, each its own regular season; top
   finishers feed a cross-region Worlds tournament) — and **renamed the canonical
   term "Sub-league" → Conference** (term now retired; see ADR-0034 / CONTEXT.md).
   It is a **multi-slice epic**, sequenced CONF-01..CONF-06 below.

- **SUB-01 (piece 1) · [DONE] Season `rotate_by_matchday` arena-map mode.**
  Shipped — full implementation note moved to
  [`PLAN-completed.md`](PLAN-completed.md) (under **SUB-01 · piece 1**). A 4th
  `Season.map_mode` value driving a Season-level author-ordered ArenaMap rotation
  keyed on matchday; satisfies LG-01j's deferred "mode (c)" at the Season level
  (the per-*Conference* rotation is CONF-06). NO partition model.

#### SUB-01 piece 3 → Conference epic (CONF-01..CONF-06)

A **Conference** is an optional partition of a `Season`'s enrolled Teams into named,
**disjoint** competitive groups (the worlds-style regional leagues — e.g. *California*,
*Nevada*). A Season has **zero** Conferences (the default — one implicit all-Teams group,
byte-identical to a flat Season) or **two or more**. The key insight that retired the
original PLAN's "intra-pool vs cross-pool" risk: Conferences play **intra-Conference only**
during the regular season, so every fixture is *always* within exactly one Conference and the
cross-pool map-resolution ambiguity never arises. Depended on **LG-07** (satisfied — LG-07a
shipped) and is most useful alongside **CAR-03** manager-mode career play.

- **CONF-01 · Conference foundation. [DONE] Shipped (2026-06-29).** The epic's
  foundation: a new `Conference` partition model (`season` FK CASCADE, `name`, `ordinal`,
  `teams` M2M, an activation snapshot `starting_team_ids_json`, `Meta.ordering=["ordinal"]` +
  `uniq_season_conference_ordinal`); a `Match.conference` nullable discriminator FK
  (`SET_NULL`, stamped on the Round-1 create like `Match.season_phase` / `Match.leg`);
  `Season.ordered_conferences()` / `_scheduled_conference_partitions()` /
  `conference_by_team_id()`; `scheduled_fixtures_by_phase()` generating **one round-robin per
  Conference** overlaid in **parallel** on the shared Matchday calendar (phase span = the
  largest Conference's span); `start_season()` snapshotting each Conference's team ids;
  `_stamp_champion_for_final_phase` leaving `champion_team` **NULL** (but still flipping
  `state="completed"`) for a `>= 2`-Conference RR-final Season — no cross-Conference champion
  until Worlds; a keyword-only `simulate_scheduled_round(..., conference=...)` stamp threaded
  from the three play-loop sites via `conference_by_team_id()`; **per-Conference Standings**
  (`season_standings` renders one ranked table per Conference, new DOM ids
  `season-standings-conference-{id}` / `-conference-name-{id}`, the zero-Conference single
  `season-standings-table` preserved byte-identically); a manager-Conference dashboard top-3
  snippet; `ConferenceAdmin` (admin-created — composer deferred to CONF-05); and migration
  `0057_conference_match_conference` (CreateModel → AddField, no `RunPython` / backfill).
  Invariants: a **zero-Conference Season is byte-identical to today**, and **no Score
  Calibration re-baseline** (no simulation-mechanic change — only which `Match` a Round
  attaches to + a discriminator). See
  [ADR-0034](docs/adr/0034-conference-partition.md), the seam contract
  [`.claude/worktrees/conf-01-seam-contract.md`](.claude/worktrees/conf-01-seam-contract.md),
  and the **CONF-01** subsection in
  [`laserforce_simulator/matches/CLAUDE.md`](laserforce_simulator/matches/CLAUDE.md).

- **CONF-02 · Per-Conference regional playoffs. [DONE] Shipped (2026-09-02).** After each
  Conference's regular season completes, that Conference gets **its own** seeded playoff
  bracket — a **Regional playoff**. The grill established that this slice is also a **live bug
  fix**, which is the single most important thing to know about it:
  `Season._final_standings_for_phase` computed Standings over the whole Season with **no
  Conference scoping** and `activate_pending_tournament_phase` carried **no Conference guard**,
  so a `>= 2`-Conference Season with a trailing `tournament` phase built **one cross-Conference
  playoff** — a direct contradiction of the intra-Conference invariant ADR-0034 established.
  CONF-02 corrects that as well as adding the feature. **The shape:** one `tournament`
  `SeasonPhase` now builds **N first-class `Tournament` rows, one per Conference**, each seeded
  from its own Conference's final Standings (Match corpus scoped by the CONF-01
  `Match.conference` discriminator FK), each drained through the **verbatim-unchanged** bracket
  engine (`lock_and_build`, `play_next_bracket_round`, `play_next_node`, all five formats), each
  crowning one **Conference champion**. **Linkage — two additive nullable FKs on `Tournament`**:
  `season_phase` (→ `SeasonPhase`, `SET_NULL`, `related_name="regional_tournaments"`, so a phase
  can enumerate its own brackets) and `conference` (→ `Conference`, `SET_NULL`), migration
  `0058_tournament_regional_linkage` (exactly two `AddField`s, **no `RunPython` / no backfill**
  per [ADR-0004](docs/adr/0004-simulation-data-is-disposable.md)). **`SeasonPhase.tournament` is
  UNCHANGED** and still holds the single Season-wide bracket of a 0/1-Conference Season, so the
  two storage paths never both hold a row for one phase and a non-empty `regional_tournaments`
  *is* the "this phase went regional" discriminator. **Seeding:**
  `_final_standings_for_phase` / `_seed_order_for_phase` each gain an **additive
  `conference=None` parameter** (`None` = today's Season-wide behaviour, byte-identical — a
  parameter, not a duplicate sibling method, so the two paths cannot drift); `strength` /
  `unseeded` take their team ids from `Conference.starting_team_ids_json`. **All three
  `tournament_mode` values split** in a `>= 2`-Conference Season — no exception clause to the
  intra-Conference invariant. **New `Season` seam:** `_build_tournament_for_phase(phase,
  conference=None)` (builds exactly one bracket; `tournament_cut` applies **per Conference**,
  seeds restart at 1 in each bracket, an empty order builds nothing), the **public**
  `tournaments_for_phase(phase)` (regional rows in Conference-ordinal order, else the single
  embed, else `[]`) that every drain caller and the Playoffs screen goes through so nobody
  re-implements the fallback, and `_tournament_phase_complete(phase)` — **the phase does not
  advance until every regional bracket has drained**. **`Season.champion_team` stays NULL** for
  a `>= 2`-Conference Season (CONF-01's rule, unchanged, now through the playoff phase as well):
  a Conference champion is **not** a Season champion, and no top-N placement ranking is invented
  (that is CONF-03's job). **Drain:** the engine is untouched and its three callers
  (`tasks.play_playoffs_task`, `play_season_task`'s tournament tail, `league_views.play_week`)
  loop over `tournaments_for_phase(phase)`, so **one week / one budget unit advances every
  Conference's bracket by one stage in parallel** — the same shared-Matchday overlay rule
  CONF-01 gave the round-robins (California and Nevada play their semifinals the same week).
  Progress counts aggregate across the N brackets; return shapes are unchanged. **Playoffs
  screen** (`league_screens/playoffs.py` + `templates/leagues/playoffs.html`) renders **one entry
  per regional Tournament** headed by its Conference name (`league-playoffs-conference-{key}`),
  with two new context keys — `conference` and a `key` DOM-id discriminator (`"<ordinal>"`
  unscoped / `"<ordinal>-<conf ordinal>"` regional) — replacing `bracket.phase.ordinal` in the
  five template ids, so **every existing 0/1-Conference id stays byte-identical**. **Team History
  fixed too** (a post-approval amendment that pulled the gap into scope rather than deferring
  it): `league_screens/team_history.py::_build_overall_context` gains a third `Q` term on the
  Round corpus and a two-term `Q` OR on `playoff_appearances`, both keyed on the new
  `season_phase` FK **alongside** the untouched `season_phases` chain, so a regional playoff is
  no longer misfiled as standalone-sandbox play; `championships` is deliberately **not** touched
  (a Conference champion is not a Season champion) and the load-bearing `.distinct()` survives on
  both. **Naming hazard worth carrying forward:** `Tournament.season_phase` (the new forward FK)
  vs `Tournament.season_phases` (the pre-existing reverse manager of `SeasonPhase.tournament`) —
  one character apart, opposite directions. Invariants: **a 0/1-Conference Season is
  byte-identical** in rows, reads, champion stamping and rendered DOM ids (the `>= 2` predicate
  is what gates every new path), and **no Score Calibration re-baseline** — no simulation
  mechanic changes, only how many `Tournament` rows a tournament phase produces and which Match
  corpus seeds them. Tests: `matches/tests/test_regional_playoffs.py` +
  `matches/tests/test_regional_playoffs_drain.py` (new) and an appended class in
  `matches/tests/test_league_playoffs.py`. **Scope-out:** no CONF-03 top-N Worlds qualification,
  no CONF-04 Worlds bracket, no CONF-06 per-Conference map pools, no placement /
  elimination-depth / ranking API on `Tournament`, no create-League Conference-composer change,
  and **no refactor of the CONF-01 `season_standings` view** onto the model-side derivation (the
  two Conference-scoped queries coexist for now; consolidating them is a follow-up rather than a
  widening of this slice's blast radius). See
  [ADR-0035](docs/adr/0035-regional-playoffs-one-tournament-per-conference.md), the seam contract
  [`.claude/worktrees/conf-02-seam-contract.md`](.claude/worktrees/conf-02-seam-contract.md), the
  CONTEXT.md **Regional playoff** / **Conference champion** terms, and the **CONF-02** subsection
  in [`laserforce_simulator/matches/CLAUDE.md`](laserforce_simulator/matches/CLAUDE.md).

- **CONF-03 · Worlds qualification. [DONE] Shipped (2026-09-03).** The slice that settles **who
  goes to Worlds**, and the one that retires the old "top-N-per-Conference" framing: the grill
  established that a single-elimination bracket cannot meaningfully rank its losers, so "top N" had
  no source for `N > 1` and had to be *defined* rather than *read*. **The rule: how many qualifiers
  a Conference sends is a function of its SIZE** — `len(conference.starting_team_ids_json or [])`,
  the activation snapshot fixed at Start Season — **2-4 Teams → 1, 5-8 → 2, 9 or more → 3**, and
  deliberately **not** affected by `SeasonPhase.tournament_cut`, so a top-N cut can shrink a region's
  bracket without changing how many Teams that region sends. **Each slot has a fixed provenance:**
  tier 1 is the **Conference champion** (that Conference's Regional playoff `Tournament.champion`),
  tier 2 the Conference's best regular-season **Standings** finisher not already qualified (rank 1,
  or rank 2 when rank 1 also won the bracket — the same Team never fills two slots), tier 3 the
  winner of a **Last-chance qualifier** bracket. **The no-Regional-playoff fallback has TWO causes,
  both covered:** a Conference of **2-3 Teams** is below `MIN_BRACKET_PARTICIPANTS` (= 4) so its seed
  order is too short to build, *and* a phase whose **`tournament_cut` is set below 4** truncates even
  a large Conference's order below the same floor — in both cases the Conference still sends exactly
  one qualifier, its Standings **rank 1**, with `PROVENANCE_REGULAR_SEASON` but **tier 1**
  (`QUALIFIER_TIER_CHAMPION`), so **no Conference is ever unrepresented at Worlds**. Critically the
  fallback predicate is **not** a bare `regional is None` — that is also true mid-regular-season
  before the phase is built at all, which would emit a complete, plausible-looking Worlds field
  before a single playoff Match; it fires only when `phase.regional_tournaments.exists()` is `True`,
  making tier 1 a **three-branch** decision (regional row / phase-not-built ⇒ `[]` / genuine
  fallback), and `exists()` is used rather than re-deriving size because one read covers both causes.
  **Cross-region seeding is tier-first, then regular-season RATE** — `tier` ASC, then
  `league_points / matches_played` DESC, `round_wins / matches_played` DESC,
  `total_score / matches_played` DESC, `team_id` ASC, every input taken off the Team's
  **Conference-scoped** `StandingsRow` — **rate rather than raw totals** because Conferences differ
  in size and so play different numbers of games (a 12-team Conference's 11-game total is not
  comparable to a 5-team Conference's 4-game total); locked degenerate rule, `matches_played <= 0` ⇒
  **all three rates are `0.0`**, no division attempted, no sentinel, no exception. **State: exactly
  one new column.** `Tournament.qualifier_stage` (`CharField(max_length=16, blank=True, default=""`,
  choices `""` / `"regional_playoff"` / `"last_chance"`; `max_length=16` is exact), migration
  `0059_tournament_qualifier_stage` — **one `AddField`, no `RunPython`, no backfill** per
  [ADR-0004](docs/adr/0004-simulation-data-is-disposable.md) — and `Tournament` still has **no
  `class Meta`**. **LOCKED read rule:** every read tests **only** `qualifier_stage == "last_chance"`;
  everything else, the un-backfilled `""` on a pre-0059 CONF-02 regional row included, is a Regional
  playoff when `conference_id` is set and not a qualifier bracket at all when it is NULL — so
  `== "regional_playoff"` as a positive test, `!= ""`, and `.exclude(qualifier_stage="")` are all
  **forbidden**. **The Last-chance qualifier row is created EAGERLY and UNSEEDED** at phase
  activation (`Tournament` in `state="setup"` with **zero** participants and **zero** nodes,
  `lock_and_build()` not yet called) — a state the bracket engine had never held, and **load-bearing
  rather than a bug**: `_tournament_phase_complete` requires `all(t.state == "completed")` so the
  phase already refuses to advance for free, and `find_next_playable_node()` returns `None` on a
  node-less bracket so both drain loops already skip it harmlessly. Eager, not lazy, because
  `tasks.play_playoffs_task` and `play_season_task` each resolve `tournaments_for_phase(phase)`
  **once and cache it** — a row created mid-drain would be invisible to the loop that must play it,
  and the phase would report complete before the bracket existed. **New `Season` seam:** private
  `_final_tournament_phase()` (only the **highest-ordinal** tournament phase qualifies; a mid-season
  one still builds regional brackets exactly as CONF-02 does) and
  `_build_last_chance_tournament(phase, conference)`; **public**
  `seed_pending_last_chance_brackets(phase) -> int` (`@transaction.atomic`, returns how many brackets
  it seeded, **idempotent** — `0` before a champion exists, a positive count on the seeding call, `0`
  thereafter; it excludes both already-qualified Teams from the field, seeds `1..4`, and *deletes* a
  short-field row rather than deadlocking the phase) and `worlds_qualifiers() ->
  list[WorldsQualifier]`. **Four hook sites, seed-then-continue semantics, engine untouched:**
  `play_playoffs_task` seeds on a zero-progress pass and **`continue`s** (so the PLAY-01 cancel check
  re-runs) instead of breaking; `play_season_task`'s `_drain_one_stage()` seeds and retries the stage
  **once, inside the helper**, so **one budget unit is still one stage** and both loop bodies stay
  untouched; `league_views.play_single_round` seeds **unconditionally** before its redirect, which is
  what stops it becoming a **dead click** (the click that resolves the last regional node leaves that
  bracket `completed` and its sibling `setup`, a transient the dashboard helper reads as *neither*
  active nor completed); and `activate_pending_tournament_phase` seeds **inside and before** the
  CONF-02 idempotence guard's `return`, turning re-activation into a **recovery hook** that runs after
  every scheduled Round. In all four the cached `tournaments_for_phase` list is deliberately **not**
  re-resolved and no cached `.state` is re-read — `tournament.nodes` is re-queried on every call, so
  the same cached instance that was node-less early plays correctly later. **NEW pure module
  `matches/worlds.py`** on the `standings.py` / `bracket.py` precedent — **no Django, no ORM**, import
  allowlist `dataclasses` + `typing`, pinned by a `TestNoDjangoImportsLeaked` subprocess guard —
  holding the frozen `WorldsQualifier` dataclass (`team_id`, `team_name`, `conference_id`,
  `conference_name`, `tier`, `provenance`, the four regular-season rate *inputs*, `seed = 0`, plus a
  `provenance_label` property), the tier/provenance constants and `PROVENANCE_LABELS`,
  `LAST_CHANCE_FIELD_SIZE = 4`, and `qualifier_count_for_size` / `first_unqualified` /
  `last_chance_field` / `order_worlds_qualifiers` (which stamps `seed` 1..M into a **new** list via
  `dataclasses.replace`, exactly as `compute_standings` stamps `rank`). **The Worlds field is derived
  on demand and never persisted**, and `worlds_qualifiers()` is **all-or-`[]`**: it returns `[]` the
  moment any required bracket is missing its champion or any Conference that should have a
  Last-chance row has none, never a partial list — because CONF-04 builds its bracket straight off
  this list and a 5-of-7 field is indistinguishable from a complete one at the call site, and because
  it gives the UI exactly one branch (`{% if worlds_qualifiers %}`, so there is deliberately no
  `worlds_ready` boolean). **Playoffs screen:** two new bracket keys (`stage`, `stage_label`), one
  changed (`pending` becomes `tournament.state == "setup"`, byte-identical for every pre-CONF-03 row
  because `lock_and_build()` always flips `state` to `"active"` first), the CONF-02 `key` gaining a
  **`-lc` suffix on the Last-chance entry only**, a `league-playoffs-stage-{key}` badge that renders
  *only* there, a Last-chance branch in the pending alert (the `{% else %}` keeps the old text
  verbatim), and the read-only Worlds panel (`league-playoffs-worlds` / `-worlds-table` /
  `-worlds-row-<seed>` / `-worlds-team-<seed>` / `-worlds-conference-<seed>` /
  `-worlds-provenance-<seed>`), **absent entirely** for a 0/1-Conference Season. **Naming hazards
  worth carrying forward:** `Tournament.season_phase` (forward FK) vs `Tournament.season_phases`
  (pre-existing reverse manager) — one character apart, opposite directions; `PROVENANCE_LAST_CHANCE`
  and `qualifier_stage`'s `"last_chance"` **share a string value while being different axes** (a
  qualifier's provenance vs a bracket's stage) and neither may stand in for the other; and
  `matches/worlds.py` **must never import `matches/models.py`** (tier and provenance are separate
  axes on purpose — the 2-3-Team fallback is exactly where they disagree). Invariants: a **0- or
  1-Conference Season is byte-identical** in rows, reads, champion stamping and rendered DOM ids (no
  `last_chance` row, `seed_pending_last_chance_brackets` returns `0`, `worlds_qualifiers()` returns
  `[]`, no Worlds panel); a **Conference of 8 or fewer Teams is byte-identical to CONF-02** in rows
  and DOM ids; **every CONF-02 DOM id is unchanged**; **`Season.champion_team` stays NULL** for a
  `>= 2`-Conference Season — **this slice crowns nothing**; the phase never advances early; and **no
  Score Calibration re-baseline** (no simulation mechanic changes — only one discriminator column,
  one extra `Tournament` row per large Conference, and a pure derivation module). Tests:
  `matches/tests/test_worlds_qualification.py` (the pure module with zero DB plus the
  `worlds_qualifiers()` derivation, including the **premature-field regression guard** — a
  `>= 2`-Conference Season whose Conferences are *all* 5-8 Teams, with the tournament phase unbuilt,
  must return `[]`, the one Season shape where nothing else in the derivation would) and
  `matches/tests/test_last_chance_qualifier.py` (build / seed / gate cycle, idempotence, the
  `tournaments_for_phase` ordering, the dead-click pin, and both byte-identity pins) — new — plus
  appended classes in `matches/tests/test_regional_playoffs_drain.py` and
  `matches/tests/test_league_playoffs.py`. **Scope-out:** no cross-Conference Worlds `Tournament` row
  or phase (CONF-04), no bye / play-in handling for a non-power-of-two field, no placement /
  elimination-depth ranking API on `Tournament`, no per-Conference map pools (CONF-06), **no
  persisted qualifier table**, no `SeasonPhase` or `Conference` model change, no dashboard change
  (the `setup`-is-neither-active-nor-completed transient is closed by the four hook sites, **not** by
  treating `setup` as active — that would break the 0/1-Conference byte-identity), and no filter on
  the sandbox `/tournaments/` list (an unseeded Last-chance row showing there in `setup` state is
  accepted, per ADR-0036). See
  [ADR-0036](docs/adr/0036-worlds-qualification-size-tiered-with-last-chance-bracket.md), the seam
  contract
  [`.claude/worktrees/conf-03-seam-contract.md`](.claude/worktrees/conf-03-seam-contract.md), the
  CONTEXT.md **Worlds** / **Worlds qualifier** / **Last-chance qualifier** terms, and the **CONF-03**
  subsection in
  [`laserforce_simulator/matches/CLAUDE.md`](laserforce_simulator/matches/CLAUDE.md).

- **CONF-04 · The Worlds Tournament phase. [DONE] Shipped (2026-09-03).** The slice that closes
  the epic competitively: a Season with **two or more** Conferences grows a **derived,
  never-authored fifth-mode `SeasonPhase`** — Worlds — carrying a **single Season-wide bracket**
  built straight off CONF-03's `Season.worlds_qualifiers()`, drained through the **untouched**
  bracket engine, and finally **crowning the Season champion** that CONF-01, CONF-02 and CONF-03
  each deliberately left NULL. **Worlds is a phase, not a third bracket on the regional phase.**
  The grill rejected hanging a third `Tournament` off the *existing* final tournament phase
  (discriminated by a new `qualifier_stage="worlds"`) on two counts: it lands inside
  `phase.regional_tournaments`, which `tournaments_for_phase` orders by `conference__ordinal`, and
  the Worlds row's `conference` is NULL — which sorts **first on SQLite and last on PostgreSQL**,
  making the bracket's drain and render position **backend-dependent** in a project whose two
  supported backends are exactly those ([ADR-0025](docs/adr/0025-postgresql-canonical-sqlite-dev-only.md));
  and it forces a rewrite of `_stamp_champion_for_final_phase`, whose `if regional: … return` is the
  very line encoding "a Conference champion is not a Season champion". A fourth
  `SeasonPhase.phase_type` value was rejected as too broad — every site keyed on
  `phase_type == "tournament"` would need a parallel branch, and each is a place for the two kinds
  of tournament phase to drift apart; `tournament_mode` already **is** the "what flavour of
  tournament phase is this?" axis. **State: exactly one choices-only value.**
  `SeasonPhase.TOURNAMENT_MODE_CHOICES` gains a fifth entry `("worlds", "Worlds")`, migration
  `0060_alter_seasonphase_tournament_mode` — **one `AlterField`, choices-only, no `RunPython`, no
  backfill, no second migration** (it has no database-level effect on either backend), per
  [ADR-0004](docs/adr/0004-simulation-data-is-disposable.md). **No `Tournament` column is added**
  and **there is deliberately NO `qualifier_stage == "worlds"`**: CONF-03's locked read rule — every
  read tests `== "last_chance"` and nothing else — survives untouched, because the Worlds bracket is
  identified from **`phase.tournament_mode`** (the PHASE flavour), never from anything on the
  Tournament row. **The phase is DERIVED, never authored** — no composer wire token, no create-form
  control, no admin surface. New private `Season._ensure_worlds_phase() -> SeasonPhase | None`
  (idempotent; creates nothing unless `>= 2` Conferences **and** at least one persisted non-`worlds`
  `tournament` phase exists **and** no Worlds phase does; ordinal is `max + 1`, every column written
  explicitly and exhaustively so the row cannot drift on a default change) and
  `_worlds_phase() -> SeasonPhase | None`, **the one resolver** that `_ensure_worlds_phase`, the
  build seam and the owner-mood classifier all go through so nobody re-implements the
  `tournament_mode == "worlds"` scan. **Two creation hooks:** `start_season()` appends it — the
  **earliest moment at which every input is frozen** (the Conference partition, snapshotted just
  above; the phase composition, authored at create) — and creating it *lazily* when qualification
  first resolves was rejected for a race, since `complete_if_finished()` gates on
  `ordered_phases()[-1]` and would flip the Season to `completed` with a NULL champion at the very
  instant the regional phase finished; `activate_pending_tournament_phase()` calls it **again** as
  the first of its two new statements, the same **recovery-hook** role CONF-03 gave that method, so
  a Season **already active** when this shipped gains its Worlds phase on its next scheduled Round
  with an identical row rather than through a data migration (an already-**completed** Season stays
  completed and championless). `_run_season_rollover` **skips** it (one `continue`, the first
  statement of the phase-copy loop): the rollover carries no Conferences forward, so a copied Worlds
  phase would land on a flat Season whose `worlds_qualifiers()` returns `[]` forever and strand it
  at `active` — and because the Worlds phase always holds the **highest** ordinal, skipping it
  leaves the copied ordinals contiguous from 1, so **no renumbering is needed and none may be
  added**. **`Season._final_tournament_phase()` is narrowed to skip `worlds`-mode phases** — one
  `continue`, and after this slice the method means **"the final NON-Worlds tournament phase"**,
  i.e. the Regional-playoff phase, which is exactly what both callers want; without it, appending
  Worlds silently redirects qualification at the **Worlds phase's own bracket**, empty before the
  build and self-referential after it. `complete_if_finished` does **not** call it (it uses
  `ordered_phases()[-1]`), so champion stamping is unaffected. **New public
  `Season.build_pending_worlds_bracket() -> bool`** (`@transaction.atomic`) — the idempotent build
  seam, `True` **iff** it built on that call and `False` in every other case (no Worlds phase;
  already built; prior phase incomplete; qualification not ready), gating in exactly that order and
  then creating the `Tournament`, one `TournamentParticipant` per qualifier at **`seed=q.seed`**
  (the stamped 1..M value, **not** `position + 1` — the enumerate index would silently work today
  and break the instant anything re-orders the list), `lock_and_build(minimum=2)`, and the
  `phase.tournament` wire; the whole thing is atomic, so a failure leaves **no** partial bracket and
  the next hook-site call retries cleanly. Its `>= 2` qualifier guard is **defensive, not a live
  branch** — a legitimate `>= 2`-Conference Season always yields at least one qualifier per
  Conference — and exists only so admin-mangled data cannot reach `lock_and_build` with a
  one-participant field. **The Worlds `Tournament` row is deliberately FLAT, and that is what makes
  champion stamping work unedited:** named `f"{season.name} Worlds"` (**no em-dash**, mirroring the
  flat `"… Playoffs"` shape rather than CONF-02's `"… — <Conference> Playoffs"`), format the literal
  `"single_elimination"`, and **both CONF-02 linkage columns LEFT UNSET** (`season_phase` and
  `conference` NULL, `qualifier_stage` at its `""` default) — structurally identical to the closing
  playoff of a flat 0-Conference Season. Three existing behaviours therefore carry it with **no
  change at all**: `tournaments_for_phase` returns `[phase.tournament]` (or `[]` while unbuilt);
  `_tournament_phase_complete` requires that single bracket be `"completed"` and `bool([])` is
  `False`, so **the Season parks on an unbuilt Worlds phase instead of completing championless**;
  and `_stamp_champion_for_final_phase` sees an empty `regional` list, **skips** the CONF-02
  early return and runs its existing single-bracket branch. **Neither `complete_if_finished` nor
  `_stamp_champion_for_final_phase` is edited.** **The bracket floor becomes a keyword-only
  `minimum`, and only Worlds passes 2:** `build_bracket`, `build_double_elim_bracket` (which
  **forwards** it to its inner `build_bracket`) and `Tournament.lock_and_build` each swap their
  hard-coded `len(participants) < 4` for `< minimum`, keyword-only so no positional call site can
  drift, with the `ValueError` / `ValidationError` text left **literally** `"A tournament requires
  at least 4 participants."` even when `minimum != 4` (existing tests and callers pin the string,
  and the only non-4 caller's own guard makes it unreachable). `build_rr_de_finals_bracket` and
  every other caller keep the default and are byte-identical. **PLAN.md's open "byes or a play-in
  round" question was already answered:** `build_bracket` has handled arbitrary `N >= 4` since LG-02a
  by rounding up to the next power of two and giving the top `size − N` seeds pre-resolved bye
  nodes, so 5, 7 and 9 need no new mechanism and a play-in round would have been a second, redundant
  one. What PLAN.md did **not** anticipate is a field **below** four: two Conferences of 2-4 Teams
  send one qualifier each, so `M = 2`, and an 8-Team 2-Conference League is exactly what CONF-05's
  create form produces by default — skipping Worlds there would mean **the most common Conference
  setup a user can create silently never crowns anyone**, and a bracket-less walkover would invent a
  second champion-stamping path and play no Worlds Match at all. `n = 2` yields one node, which *is*
  the Worlds final; `n = 3` yields a size-4 bracket with seed 1 byeing into it. **Five hook sites,
  and the ONE deliberate phase-boundary crossing:** making Worlds its own phase puts it behind a
  boundary the drain loops do not cross (both tasks resolve `current_phase()` and cache
  `tournaments_for_phase(phase)` **once**, and bracket Matches bypass `simulate_scheduled_round`, so
  the activation hook goes quiet the moment the regular season ends — left alone, "Play whole
  season" would stop with the regionals drained and Worlds unbuilt). Following CONF-03's
  seed-then-continue precedent rather than restructuring the loops (an outer per-phase loop would
  re-thread PLAY-01's cancel checks and the stage-budget arithmetic through a new level of nesting,
  for a generality nothing yet needs): `play_playoffs_task` builds in its stall branch and, on
  `True`, **re-resolves `phase` and `tournaments`** and `continue`s; `play_season_task`'s
  `_drain_one_stage()` does the same behind a `nonlocal` and retries the stage once — because it
  fires only when nothing else progressed, **one budget unit is still one stage** and both loop
  bodies stay untouched; `league_views.play_single_round` builds before `complete_if_finished()`,
  which is what stops the regionals-finishing click leaving the cursor on an unbuilt Worlds phase
  whose `tournaments_for_phase` is `[]` (the next click would 400 and the user could never start
  Worlds); `play_playoffs` builds **before `phase = season.current_phase()`** and not merely before
  the guard, because the build writes `phase.tournament` on its **own** freshly-loaded instance and
  a `phase` read earlier carries a stale `tournament_id is None` — a build-after-read would still
  409; and `activate_pending_tournament_phase` runs ensure-then-build as its **first two
  statements**, which also closes a structural hazard (building first sets `phase.tournament_id`, so
  the existing idempotence guard fires and the `>= 2`-Conference branch can never fan regional
  brackets out onto the Worlds phase — **the ordering is the guard**, no `tournament_mode` check was
  added). Known and intended consequence: `_stage_counts()` closes over the same `tournaments` name,
  so the reported `{"completed", "total"}` **shrinks** at the phase boundary — the counts describe
  the **current** phase, matching CONF-02's contract, so tests assert the final return and the
  terminal DB state and **never** PROGRESS monotonicity. **Owner mood is classified TWO-TIER, and
  `matches/owner_mood.py` is NOT touched.** `_classify_playoffs_for_team` picks the first
  `tournament` phase whose `tournament_id` is set; a regional phase leaves that NULL, so a
  `>= 2`-Conference career League has always scored `("none", 0, 0)` on the playoff axis — and the
  Worlds phase, which **does** set it, would have switched that axis on **by accident**, charging
  every non-qualifier the full `"missed"` penalty regardless of how far it got in its own region.
  The entire fix lives in the classifier, whose signature, return triple and four result strings are
  unchanged: one additive `>= 2`-Conference branch (the `else` arm is today's body **verbatim**),
  `num_rounds` becoming the Team's whole possible path — the rounds of its own Conference's Regional
  playoff **plus** the rounds of the Worlds bracket — and `rounds_won` the distinct rounds it won
  across both. **Count per bracket, then ADD — never union the raw `bracket_round` integers**, since
  the two brackets number independently from 1 and a set union would collapse two wins into one.
  Winning Worlds still returns `"champion"`; being cut from the regional bracket still returns
  `"missed"`; a Team in **no** Conference returns the neutral `"none"` rather than the `-0.2`
  `"missed"` penalty, a **pinned defensive choice** because broken data must not fire a Manager; and
  **the Last-chance bracket is excluded from both the numerator and the denominator** so a Team
  cannot ride it past the maximum path its Conference offers. `compute_playoffs_delta` is consumed
  verbatim — its `"seeded"` branch is already depth-proportional, so the longer path yields the
  intended ladder (Worlds champion > Worlds finalist > Conference champion > regional finalist >
  first-round exit) **for free**; per-tier constants were rejected because CAR-02's values are
  faithful to ZenGM's `updateOwnerMood` and a new ladder would have no such source, and summing two
  independent per-bracket deltas was rejected because it doubles the axis range against a
  `FIRE_THRESHOLD` tuned for one bracket. **Playoffs screen, with `templates/leagues/playoffs.html`
  NOT edited:** both derivations gain a Worlds branch evaluated **first** (the Worlds Tournament's
  `conference` is `None` and would otherwise fall into the Season-wide branch), giving `key`
  `"<ord>-worlds"`, `stage` `"worlds"` and `stage_label` `"Worlds"` — and the pending stub gets the
  same treatment plus `name` `"Worlds"`, so a phase's DOM id is **stable across the unbuilt → built
  transition** and a `>= 2`-Conference Season shows a "Worlds" pending section from the moment the
  Season starts. Every id falls out of the existing `{{ bracket.key }}` interpolations and the
  existing `{% if bracket.stage_label %}` badge block renders the Worlds badge for free
  (`league-playoffs-phase-<ord>-worlds` and friends); the Worlds stub deliberately reaches the
  existing pending alert's `{% else %}` text rather than getting a branch of its own. **DOM-id
  hazard:** `id="league-playoffs-worlds"` is **already taken** by CONF-03's Worlds *qualification*
  table — the CONF-04 bracket **section** is `league-playoffs-phase-<ord>-worlds`, and the two
  coexist on the same page. Finally `_playoff_cursor_keys`'s `following_tournament_is_final` is
  **generalised** from "the next tournament phase *is* the last phase" to "**nothing but tournament
  phases follows it**", verified to reproduce today's result on both pre-CONF-04 phase shapes;
  without it, appending Worlds would flip a season-ending playoff back to the mid-season "Until
  Tournament" label. **Naming hazards worth carrying forward:** `tournament_mode == "worlds"` (the
  PHASE flavour) vs `qualifier_stage` (the BRACKET stage) — different models, different questions,
  and there is no `"worlds"` stage; `_final_tournament_phase()` now meaning "the final **non**-Worlds
  tournament phase" and **no longer** being `ordered_phases()[-1]` for a `>= 2`-Conference Season;
  the builder kwarg `minimum` (default `4`) vs `MIN_BRACKET_PARTICIPANTS` (`models.py:18`, value
  `4`) which are **not** wired together because `bracket.py` is a pure module that must not import
  `models.py`; the Worlds Tournament having `season_phase_id is None` **and** a non-empty
  `season_phases`; and `build_pending_worlds_bracket` (returns `bool`) vs
  `seed_pending_last_chance_brackets` (returns `int`) sitting side by side at four of the same hook
  sites — both are correct as written and must not be "harmonised". Invariants: a **0/1-Conference
  Season is byte-identical** in rows, reads, champion stamping and rendered DOM ids (no Worlds
  phase, no Worlds `Tournament`, no `-worlds` substring anywhere on the screen); **every CONF-02 /
  CONF-03 DOM id is unchanged**; every existing `build_bracket` / `build_double_elim_bracket` /
  `lock_and_build` caller **keeps `minimum=4`** with unchanged error strings; **`owner_mood.py`, the
  bracket engine, `matches/worlds.py`, `complete_if_finished`, `_stamp_champion_for_final_phase` and
  the Playoffs template are all untouched**; and **no Score Calibration re-baseline** (the only
  shifts are one choices-only `AlterField`, one derived `SeasonPhase` row and one `Tournament` row
  per `>= 2`-Conference Season). Tests: `matches/tests/test_worlds_tournament.py` (new — phase
  derivation and its three gates, the recovery hook, the build seam's idempotence, `seed=q.seed`
  asserted against a field where the enumerate index would differ, the `M = 2` one-node and `M = 3`
  bye paths end to end, the champion, the flat-row shape, the `play_playoffs` 202 and
  `play_single_round` non-dead-click pins, `lock_and_build(minimum=…)`, both byte-identity pins, and
  the narrowing asserted **only** through `worlds_qualifiers()`) plus one appended class each in
  `matches/tests/test_regional_playoffs_drain.py` (both task hook sites), `test_season_playoffs.py`
  (parks unbuilt, crowns on completion), `test_owner_evaluations_writer.py` (the decision table
  through the writer), `test_league_next_season.py` (the rollover skip) and `test_bracket.py` (the
  `minimum=` kwarg, keyword-only, DE forwarding, unchanged message). **Scope-out:** no per-Conference
  map pools (CONF-06), no placement / elimination-depth ranking API on `Tournament`, no persisted
  qualifier or Worlds-standings table, no composer wire token or authoring UI for the Worlds phase,
  no template edit, and **no data migration or `RunPython` backfill of any kind**. See
  [ADR-0037](docs/adr/0037-worlds-is-a-derived-season-phase.md), the seam contract
  [`.claude/worktrees/conf-04-seam-contract.md`](.claude/worktrees/conf-04-seam-contract.md), the
  CONTEXT.md **Worlds** / **Worlds phase** / **Season phase** terms, and the **CONF-04** subsection
  in [`laserforce_simulator/matches/CLAUDE.md`](laserforce_simulator/matches/CLAUDE.md).

- **CONF-05 · [DONE] Shipped (2026-06-30) — draft-Season Manage Conferences page.**
  An in-app authoring surface (not Django admin) for partitioning a draft Season's enrolled
  Teams into Conferences before Start Season — replacing the original "create-League composer"
  framing with a dedicated draft-Season page (the maintainer's Option B). A single-page
  vanilla-JS composer (the LG-02b precedent) names Conferences and assigns each enrolled Team
  via a per-team `<select>` kept in sync by small JS; one Save replaces the Season's Conference
  rows atomically. The locked partition rule: Conferences are OPTIONAL (zero ⇒ flat Season),
  and when present must form a **full disjoint partition** (every enrolled Team in exactly one
  Conference) with **≥2 Teams per Conference**. Validated by the pure
  `matches.league_views._validate_conference_partition(names, team_to_conf_idx,
  enrolled_team_ids) -> (errors, normalized)` (error strings `"Conference names cannot be
  empty."` / `"Every team must be assigned to a conference."` / `"Each conference needs at
  least 2 teams."`). View `manage_conferences(request, season_id)` (GET+POST, else 405; 404 on
  missing Season; writes `last_league_id`) renders the editable composer while `draft` and a
  **read-only frozen partition** once active/completed (membership is snapshotted at Start
  Season — a non-draft POST is **400**); an empty submission clears all Conferences. URL name
  `manage_conferences`, path `/seasons/<int:season_id>/conferences/` (`matches/season_urls.py`,
  before `standings/`). Template `templates/seasons/manage_conferences.html` (DOM ids
  `manage-conferences-form` / `-add` / `-name-{i}` / `-team-{team_id}` / `-submit` / `-errors`
  / `-readonly` / `-empty`); draft-only entry links `season-dashboard-manage-conferences-link`
  + `league-dashboard-manage-conferences-link` on the season + league dashboards (gated
  `season_mode == "draft"`). **Create-flow integration:** `CreateLeagueForm` gains a
  `number_of_conferences` dropdown (`None`/`2`/`3`/`4`, DOM id
  `league-create-number-of-conferences`) with a `num_teams >= 2 * N` guard; when `N > 0`,
  `league_create` pre-creates `N` Conferences with the generated Teams auto-split evenly
  (round-robin) and **redirects straight to `manage_conferences`** instead of `season_standings`,
  so conference setup happens before the dashboard. **No model change,
  no migration, no simulator touch, no Score Calibration re-baseline** — pure view + template
  on top of the CONF-01 Conference model. Tests: `matches/tests/test_manage_conferences.py`
  (pure validator + view GET/POST/405/404/read-only + the dashboard-link gate) +
  `test_league_create.py` (the `number_of_conferences` form rule + create→pre-split→redirect).
  See the
  **CONF-05 manage conferences** subsection in
  [`laserforce_simulator/matches/CLAUDE.md`](laserforce_simulator/matches/CLAUDE.md).

- **CONF-06 · Per-Conference rotating map pools. [DONE] Shipped (2026-09-03).** The epic's closing
  slice and the map lens the CONF-01 grill sketched: a **5th `Season.map_mode` value**
  `rotate_by_conference` (label `Rotate by Conference`, appended as the last `MAP_MODE_CHOICES`
  tuple — `Season.clean()` derives `valid_map_modes` from the choices, so it needed no edit) under
  which each **Conference** carries its own author-ordered `ArenaMap` rotation and a regular-season
  fixture resolves its map from **its own** Conference's rotation — "Nevada games on the Nevada
  map", unambiguous because regular-season fixtures are always intra-Conference. The per-partition
  analogue of SUB-01's Season-level `rotate_by_matchday`, one level down on the CONF-01 partition.
  **Model:** TWO new `Conference` fields mirroring the SUB-01 Season-level pair —
  `map_rotation_ids_json` (LIVE, written by the Manage Conferences Save) and
  `starting_map_rotation_ids_json` (the activation snapshot, written by `Season.start_season()`
  inside the **existing** per-Conference loop — for every Season with Conferences regardless of
  `map_mode`, so the loop stays mode-agnostic), both `JSONField(null=True, blank=True,
  default=None)` with `None` = pre-authoring / pre-activation and `[]` = authored-empty (consumers
  read `(x or [])`). Author order is **PRESERVED, never `sorted()`** in either (`list(...)` — the
  deliberate divergence from CONF-01's `starting_team_ids_json`), because the matchday index keys
  directly into the ordered list. Migration
  `matches/migrations/0061_conference_map_rotation.py` (dep
  `0060_alter_seasonphase_tournament_mode`), **3 ops** — 2× `AddField` plus the
  choices-only `AlterField` Django emits for the widened `Season.map_mode` (non-schema on
  both backends, kept in the same file after the AddFields) — **no `RunPython` / no backfill**
  (ADR-0004 — pre-CONF-06 Conferences take `None` and never reach the new branch).
  **Resolver:** `matches.tasks._resolve_fixture_map` gains a **4th keyword-with-default argument**
  `conf_by_team: dict[int, Conference] | None = None` (backwards compatibility is load-bearing —
  all 37 existing 3-argument call sites in the suite keep working unchanged, `None` is treated as
  `{}`) plus a `rotate_by_conference` branch placed after the `rotate_by_matchday` one: look up
  `fixture.team_a_id`, read `ids = (conf and conf.starting_map_rotation_ids_json) or []`,
  defensively `return None` on empty, else `return pool_by_id.get(ids[fixture.matchday %
  len(ids)])`. **NO RNG** — the branch never constructs a `random.Random`, so the SIM-07 seed chain
  is untouched; every failure shape (missing `conf_by_team`, unmapped team, `None`/`[]` snapshot,
  an `ArenaMap` deleted after activation) returns `None` rather than raising. The index uses the
  **1-based `matchday` with the modulo applied directly**, byte-mirroring the shipped
  `rotate_by_matchday` formula: with a 2-map rotation matchday 1 resolves `ids[1]` and matchday 2
  resolves `ids[0]`. That is correct-by-contract, **NOT an off-by-one** — do not "fix" it to
  `(matchday - 1) % len(ids)`. New pure helper `matches.tasks._fixture_map_ids(season,
  conf_by_team=None) -> list[int]` unions Season pool ids + Season rotation ids + each first-seen
  Conference's rotation ids (Conferences deduped by `conf.id` since one Conference appears once per
  member team; map ids deliberately NOT deduped — harmless for `in_bulk`) into the single argument
  for the play loops' one `ArenaMap.objects.in_bulk`. New pure form helper
  `matches.forms.parse_rotation_ids(raw, valid_map_ids) -> tuple[list[int], list[str]]` (author
  order, duplicates kept, one error string per offending token, blank tokens skipped silently)
  extracted verbatim from the old inline `CreateLeagueForm.clean` loop and now shared by the create
  form and the Manage Conferences POST; its two error strings `"Map rotation contains an invalid
  id."` / `"Map rotation contains an unknown map id."` are unchanged. **Three play-loop call sites**
  each reordered to build `conf_by_team = season.conference_by_team_id()` **above** the `in_bulk`,
  swap the `in_bulk` argument for `_fixture_map_ids(season, conf_by_team)`, and pass
  `conf_by_team=conf_by_team` to the resolver: `tasks.play_season_task`,
  `league_views.play_week`, and `league_views.play_week_live` (the manager live-game RR branch).
  **Four guards** enforce the mode's shape — **≥2 Conferences** (Conferences are 0 or ≥2 by the
  CONF-01 partition rule) each with **≥1 rotation map**: (1) `CreateLeagueForm.clean` via
  `add_error` (NOT `raise`, so all three surface in one submission) — `"Map pool must be empty when
  Map mode is 'Rotate by Conference'."` on `map_pool`, `"Map rotation must be empty when Map mode
  is 'Rotate by Conference'."` on `map_rotation`, `"Map mode 'Rotate by Conference' requires at
  least 2 conferences."` on `number_of_conferences`; (2) the Manage Conferences Save, page-level
  `"Each conference needs at least 1 rotation map."` (appended once, only under this mode); (3)
  `Season.start_season()`, `ValidationError` `"A Season with map mode 'Rotate by Conference'
  requires at least 2 conferences, each with at least 1 rotation map."`; and (4) the rollover
  downgrade below. **Authoring** rides the CONF-05 page rather than a new screen: a per-row hidden
  `conference_rotation` input fed by a vanilla-JS composer (the `league-create-map-rotation`
  precedent), DOM ids `manage-conferences-rotation-{i}` / `-rotation-composer-{i}` /
  `-rotation-add-{i}` / `-rotation-row-{i}-{j}` / `-rotation-select-{i}`, the confirmed-map payload
  `manage-conferences-confirmed-maps`, and the non-draft read-only list
  `manage-conferences-readonly-rotation-{i}` — every existing CONF-05 DOM id survives unchanged, and
  `_validate_conference_partition` keeps its exact signature and strings. The rotation is therefore
  editable **only while the Season is `draft`**, inheriting the page's frozen-partition behaviour (a
  non-draft POST still 400s). **Rollover downgrade:** `_run_season_rollover` creates the next Season
  with `map_mode` forced to `"none"` when the completed Season was `rotate_by_conference`, carrying
  every other mode verbatim — the rollover carries no Conferences forward, so a carried mode would
  find an empty `conf_by_team` and resolve `None` for *every* fixture all season, i.e. a silently
  map-less Season; the 3-zone fallback is the honest landing. **Scope-out:** the **per-fixture map
  override** is DEFERRED (see Deferred Items — fixtures are not persisted rows); bracket Matches
  (Regional playoff / Worlds) never carry an `arena_map` at all, so this mode governs
  **regular-season fixtures only** — out of scope **by construction, not by omission**; no
  `SeasonAdmin` surface for the rotation; no Conference (or rotation) carry-through `next_season`
  beyond the downgrade; **no new ADR** (reversible model fields + a deterministic helper branch,
  mirroring SUB-01's no-ADR rationale); and **no Score Calibration re-baseline** — the new branch is
  unreachable without the new mode and `_fixture_map_ids` reduces to the old `(pool or []) +
  (rotation or [])` expression when there are no Conferences, so every existing seeded outcome stays
  byte-identical. Tests extend **five existing files, no new test file**:
  `_build_map_config_label` gains a **6th branch** so the dashboard reports
  `Map: Rotating per Conference (N conferences: …)` instead of falling through to the
  ladder's defensive `Map: 3-zone fallback (no map)` return — the label otherwise lied
  about a Season that was really running per-Conference rotations (caught in the browser
  pass, not the seam contract; one `ArenaMap` query for the whole label, author order,
  regression class `TestConf06LeagueDashboardRotateByConferenceLabel`). Tests:
  `matches/tests/test_season_map_config.py` (the pure branch incl. the 1-based-modulo pin and every
  defensive-`None` path, `_fixture_map_ids`, `parse_rotation_ids`), `test_conference.py`
  (author-order snapshot + the activation guard), `test_manage_conferences.py` (composer GET, POST
  save in submitted order, both guard re-renders, non-draft read-only + 400),
  `test_league_next_season.py` (the downgrade, other modes still verbatim) and
  `test_league_create.py` (the three form guards + a valid submission). See the seam contract
  [`.claude/worktrees/conf-06-seam-contract.md`](.claude/worktrees/conf-06-seam-contract.md), the
  CONTEXT.md **Map mode** / **Map pool** / **Per-fixture map resolution** / **Conference** terms, and
  the **CONF-06** subsection in
  [`laserforce_simulator/matches/CLAUDE.md`](laserforce_simulator/matches/CLAUDE.md).

---

---

## Phase 6 — Users and Multiplayer

### UX-01 · [DONE] User accounts and team ownership

Django auth system (email + password). Open self-registration — anyone can create an account.
Admins can remove user accounts via Django Admin.

Permissions: only team owners can edit their teams/players; ~~read-only access to others~~
**[STRUCK by the UX-01 grill — cross-Account access is refused *outright*: another Account's row is
neither listed nor readable, and a lookup for one raises **404, never 403**, so it is
indistinguishable from a row that does not exist. An Account sees only its own rows plus
**Unmanaged rows**. See [ADR-0038](docs/adr/0038-accounts-and-uniform-manager-ownership.md) and seam
contract §0.3.]**
Users can see the teams, players, leagues, seasons, and tournaments they have created.

~~League access is **closed by default** (invite-only). League creators can set a league to open
(anyone can join) or send invitations to specific users.~~
**[STRUCK by the UX-01 grill — League membership, joining and invitations are **deferred** (see
Deferred Items). Only a **dormant** `League.visibility` column plus one create-League control ship;
nothing reads it this slice. Seam contract §0.3 / §13.]**

Google/OAuth social login is deferred — see Deferred Items section.

**[DONE] Shipped (2026-09-03).** The slice that turns the implicit single local user into a real
**Account** and gives every row an owner. **New fourth Django app `accounts`** holding a custom
`accounts.User` (`AbstractUser` with `username = None`, a `unique=True` `email` as `USERNAME_FIELD`,
`REQUIRED_FIELDS = []`, and an email-keyed `UserManager` whose `create_user` / `create_superuser`
both take `email` first), wired via `AUTH_USER_MODEL = "accounts.User"`. Registered in Django Admin
as an email-keyed `UserAdmin` — `django.contrib.auth.admin.UserAdmin`'s fieldsets cannot be reused
verbatim once `username` is gone — so **account removal is the built-in delete action**, the PLAN
requirement. The obvious word for the new relationship was already taken: this project's **Owner** is
the fictional boss of [ADR-0026](docs/adr/0026-manager-firing-owner-mood.md) (`OwnerEvaluation`,
`owner_mood.py`), **never a login and deliberately unrenamed**, so ADR-0038 widened **Manager**
instead — *the Account a row belongs to*.
**Ownership model:** a nullable `manager` FK (`settings.AUTH_USER_MODEL`, `null=True`, `blank=True`,
`on_delete=models.SET_NULL`) on **exactly five Ownership roots** — `teams.Team`
(`related_name="teams"`), `matches.League` (`"leagues"`), `matches.Tournament` (`"tournaments"`),
`matches.Match` (`"matches"`) and `matches.GameRound` (`"game_rounds"`) — under one rule: **a row is
a root exactly when its parent FK is null**. Team / League / Tournament have no parent FK and are
therefore always roots; a `Match` is a root only while `season_id IS NULL` (a *sandbox* Match) and a
`GameRound` only while `match_id IS NULL` (a *standalone* Round). Every other row derives its Manager
by traversing its non-null parent FK. `SET_NULL` is load-bearing and was chosen over both
alternatives: `CASCADE` would let one Admin delete silently destroy a League's whole
Season/Match/Event history, `PROTECT` would make an Account undeletable and contradict the PLAN
requirement above — deleting an Account instead **demotes its rows to Unmanaged**, which matches the
project's settled posture on every other user-facing FK (`League.current_team`,
`Tournament.champion`, `Match.season`, `*.winner`). **`Tournament` stays its own root even when
`season_phase` is set** (a CONF-02 regional / CONF-04 Worlds bracket embedded in a Season): the
`season_phase` FK is *not* an ownership parent, and the invariant "an embedded Tournament's Manager
equals its League's Manager" is held by **propagation at creation**, not by traversal — the three
`Season._build_*` sites stamp `manager=self.league.manager`. **`core.ArenaMap` and all six
map-config models are deliberately NOT Ownership roots** — no `manager` column, no queryset filter,
and all 14 `get_object_or_404(ArenaMap, …)` sites in `core/views.py` are left byte-for-byte
unchanged — because `is_default`, `Season.map_mode`, the CONF-06 per-Conference **Map pools** and
`rotate_by_matchday` all reference maps **across League boundaries**, so a privately-owned map would
silently break another Manager's rotation. Maps stay shared reference data behind the login gate:
authenticated, unfiltered. A per-`ArenaMap` `is_public` flag is the correct end state and was
**deferred** rather than invented here (see Deferred Items).
**The gate:** global `LoginRequiredMiddleware` (Django 5.1+) inserted immediately after
`AuthenticationMiddleware`, rather than ~220 per-view decorators, with `@login_not_required` on an
exemption list of **exactly eleven** surfaces — login / logout / register / password-change /
password-change-done, plus the four DRF ViewSets and the two batch API views. Nothing else is exempt,
including every `/maps/` route. `process_view` receives the *resolved* view, so an `include()` cannot
be wrapped; CBVs take `login_not_required(SomeView.as_view(...))` at the URLconf or
`@method_decorator(login_not_required, name="dispatch")` on the class (`View.as_view()` copies
`dispatch.__dict__` onto the returned callable). The API is exempt from the *middleware* but **not**
from auth: `REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"]` moves `AllowAny` → `IsAuthenticated`, so an
anonymous API call gets a **403 JSON** body instead of the middleware's HTML **302** to the login
page — which is what an API client needs — and logging out of the browser session still locks the API.
All four viewsets additionally gain a manager-scoped `get_queryset()` override while **keeping** the
class-level `queryset` attribute (the DRF router needs it for basename introspection); no router
registration, basename or URL name moves.
**The permission seam** is `accounts/permissions.py`, whose entire public surface is `ROOT_MODELS`,
`ownership_root`, `is_owned_by`, `get_owned_or_404`, `owned_queryset`, `owned_match_q`,
`owned_game_round_q`, `stamp_manager` and `manager_or_none`. Ownership resolution is a
**bounded loop** over a module-private `_PARENT_FIELD` table (model → the *field name* of its
ownership parent): a row is its own root when it carries `manager` **and** either has no parent entry
or that parent FK is NULL; a model absent from the table and carrying no `manager` (an `ArenaMap`)
has **no ownership axis** and resolves to `None`. `get_owned_or_404(Model, request, **lookup)` is the
substitution target — `get_object_or_404` plus the gate, `request` inserted as the **second
positional** argument so every conversion is one mechanical edit, returning **the resolved row, not
its root**, and raising `Http404` (never `PermissionDenied`) on a cross-Account hit. **83 of the
project's 97 `get_object_or_404` call sites** were converted (`matches/league_views.py` ×28,
`matches/league_screens/*.py` ×17 across 15 modules, `matches/views.py` ×14,
`matches/tournament_views.py` ×13, `teams/views.py` ×11); the remaining **14 are the `core/views.py`
ArenaMap lookups, left alone**. Two adjacent `.objects.get(` sites (`matches/views.py::compare_rounds`,
`matches/league_views.py::reassign_team`) were converted too — same seam, different exception type —
while `matches/api_views.py`'s `Team.objects.get(id=tid)` inside `SimulateBatchAPIView.post` is
**left alone** because it must keep returning **400**, not 404, and the viewset-level
`IsAuthenticated` is the gate there. There are **zero** `get_list_or_404` calls in the project.
The four root list views plus `player_list` are scoped through `owned_queryset` / the two longhand
`Q` builders; `map_list` is **not** scoped.
**The Unmanaged-row rule is the keystone.** `manager IS NULL` means an **open** row — readable
**and** writable by any authenticated Account, **and listed** to all of them — not a frozen or hidden
one. That is what lets the ~1237 pre-existing view-test calls pass behind a single autouse login
fixture instead of a manager-stamping pass over 57 test files, and it keeps `score_averages` /
`game_analysis` (which run outside any request) working unchanged. It must be tightened the first
time a second Account shares a deployment. **13 stamping sites** set the Manager at creation from
`request.user` via `manager_or_none(request)`: `team_create`; `_generate_teams` and
`_create_league_and_season`, which each gain **one keyword-only `manager=None` parameter appended
last** (so all existing callers stay source-compatible and an omitted kwarg leaves the row
Unmanaged); the League's free-agent pool Team; both `member_night_setup` draw Teams;
`tournament_create`'s Tournament and `tournament_draw`'s drawn Team; the two sandbox create views'
post-hoc `stamp_manager(...)` on the `Match` / `GameRound` **returned by** `BatchSimulator` (the views
never construct those rows, and `manager` is deliberately **not** threaded through the simulator); and
the three `Season._build_*` embedded-Tournament sites. The global `"Free Agents"` singleton Team is
**deliberately never stamped** — it is a cross-Account shared pool, and stamping it would let the
first Account to run `generate_players` with `num_teams == 0` capture it permanently and 404 it for
everyone else; `_generate_free_agents` and `get_free_agents_team()` keep their signatures verbatim.
**Auth surfaces** ride the house style (Bootstrap 5.3 CDN, project-level `templates/` only, **no URL
namespaces**, every widget declaring its own DOM id): `accounts/urls.py` mounted at `/accounts/` with
the five URL names `login` / `logout` / `register` / `password_change` / `password_change_done` —
Django's own defaults, so `LOGIN_URL`, `PasswordChangeView.success_url` and `{% url %}` resolve with
no extra config — four new templates plus a `_partials/topnav_auth.html` included once from
`base.html`. Two non-obvious pins: `EmailAuthenticationForm` keeps Django's field **name**
`username` while carrying an email value (the login POST key is `username`), and **sign-out is a POST
form, never a link** (Django 5.x `LogoutView` rejects GET). **NO password reset** — none of the four
views, URLs or templates exist and nothing links to them; it is deferred with OAuth for want of a
mail provider, and recovery is `manage.py changepassword`.
**Operator command:** `python laserforce_simulator/manage.py claim_unmanaged --user <email>` stamps
every Unmanaged row on all five roots to one Account in a single `transaction.atomic()`, iterating
`Team` → `League` → `Tournament` → `Match` → `GameRound` and printing one `<Model>: N claimed` line
each plus a styled total. It uses `.update()`, so **no `save()` signals fire and no `auto_now` column
is touched**, and it is **idempotent** — a second run matches nothing and reports `0` across the board.
`CommandError` on an unknown email.
**Migrations — three files, exact names, and no `RunPython` anywhere:**
`accounts/migrations/0001_initial.py` (the `CreateModel` for `User`, carrying
`managers = [("objects", accounts.models.UserManager())]`, dep
`auth.0012_alter_user_first_name_max_length`); `teams/migrations/0015_team_manager.py` (one
`AddField`, deps `swappable_dependency(AUTH_USER_MODEL)` + `teams.0014_player_team_health_injury`);
and `matches/migrations/0062_manager_ownership_and_league_visibility.py` (deps
`swappable_dependency(AUTH_USER_MODEL)` + `matches.0061_conference_map_rotation`), a **single**
migration carrying all five matches-app changes in order — `league.manager`, `tournament.manager`,
`match.manager`, `gameround.manager`, then `league.visibility`. `core` is **untouched** and stays at
`0004_map05_cell_ranking_and_strong_spots`. **No backfill** ([ADR-0004](docs/adr/0004-simulation-data-is-disposable.md)):
a `RunPython` stamp to "the first superuser" is not merely skipped but **vacuous** — a custom user
model means a new, empty user table on every existing database, so the migration would find nobody
and stamp nothing. `claim_unmanaged` replaces it.
**⚠️ Deployment hazard (and the reason it is written up in three places).** Setting `AUTH_USER_MODEL`
after `auth` / `admin` migrations have already been applied against `auth.User` raises
`InconsistentMigrationHistory: Migration admin.0001_initial is applied before its dependency
accounts.0001_initial` — but **only on an existing database**. **CI and the test suite are
unaffected**, because the test database is created fresh every run: the suite goes green while the dev
`db.sqlite3` and the Fly.io Postgres both break. The approved recovery is a **fresh database** (ADR-0004
— simulation data is disposable): delete `db.sqlite3` locally, re-provision the Fly.io Postgres, then
`migrate` and `createsuperuser`. Explicitly **not** a data migration, a migration-history rewrite or a
`--fake` shim. Recorded in `README.md` and ADR-0038's Consequences.
**`League.visibility`** ships **dormant** on the LG-02-Part2b `schedule_format` /
LG-02-Part2c-3b `SeasonPhase.tournament_mode` dormant-column precedent: a `CharField(max_length=16, choices=(("closed","Closed"),
("open","Open")), default="closed")`, a `required=False` `CreateLeagueForm.visibility` ChoiceField
(id `league-create-visibility`) rendered on `create_advanced.html` **only**, and the create path
coercing a falsy value to `"closed"`. Dormancy is *enforced*: outside the model, the migration, the
form field, the template block and the tests, the string `visibility` occurs **zero** times — no
branch, no filter, no context key, no admin column.
**Naming hazards, both pinned in the docs:** (1) `Team.manager` (**new**, Team → Account) is **not**
`Team.managed_in_leagues` (**pre-existing**, the reverse accessor of `League.current_team`, Team →
Leagues) — near-homographs pointing in opposite directions at unrelated concepts; `Team.manager` is
set on *every* generated AI Team, so it is **not** the career seat, which stays `League.current_team`.
(2) The **Manager** (the Account FK) is **not** the **Owner** (the ADR-0026 fiction); nothing
containing `owner` was renamed, `OwnerEvaluation` gained no `manager` column (it derives via
`league`), and the word "owner" is not used for an Account anywhere in code, comments, templates, DOM
ids or docs.
**Scope-out / invariants.** **No simulation mechanic was touched** — no RNG, no ordering, no
parallelism change, `pytest.ini`'s `-n auto --dist worksteal` unchanged, `matches/simulation/*`,
`tournament_engine.py`, `owner_mood.py` and `standings.py` all untouched — so there is **no Score
Calibration re-baseline** and every seeded outcome stays byte-identical; no determinism test was
required. Also out: password reset, OAuth, League membership / joining / invitations / any *read* of
`League.visibility`, cross-Account sharing, an `is_public` flag on anything, per-object permissions /
Groups / roles, user profile screens and account-deletion self-service (Admin only). Two test-seam
notes worth carrying forward: `matches.standings.StandingsRow` is a **frozen dataclass, not a model**
— it is never persisted and has no ownership axis; and `Match.season` being `SET_NULL` means deleting
a Season silently **demotes** its Matches to Unmanaged sandbox roots, which is accepted behaviour, not
a bug. Tests: the new `accounts/tests/` package (user model, auth views, permissions, the management
command), new `matches/tests/test_ownership.py` and `teams/tests/test_ownership.py`, an **autouse
login fixture** in the root `conftest.py` (`SHARED_MANAGER_EMAIL` / `get_shared_manager()`, using
`force_login` and skipping tests with no database access), plus `force_login` calls added to the **73
locally-instantiated `Client()` / `APIClient()` sites across 5 files** the fixture cannot reach. See
the seam contract
[`.claude/worktrees/ux-01-seam-contract.md`](.claude/worktrees/ux-01-seam-contract.md),
[ADR-0038](docs/adr/0038-accounts-and-uniform-manager-ownership.md), the CONTEXT.md **Account** /
**Ownership root** / **Unmanaged row** / **League visibility** terms and the rewritten **Manager**
entry, the new
[`laserforce_simulator/accounts/CLAUDE.md`](laserforce_simulator/accounts/CLAUDE.md) app guide, and
the **UX-01** subsection in
[`laserforce_simulator/matches/CLAUDE.md`](laserforce_simulator/matches/CLAUDE.md).

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

### LG-07 · [DONE] Member night simulator (LG-07a core slice)

**[DONE — LG-07a core slice shipped (2026-06-29).]** Grilled into
[ADR-0033](docs/adr/0033-member-night-season-attached-social-play.md) and sliced
into a core slice (LG-07a, this item) plus a deferred player-stat-filter follow-up
(**LG-07b**, below). The deferred **`member_night`** `SeasonPhase.phase_type`
(declared inert in `PHASE_TYPE_CHOICES` since LG-02-Part2a) is now **LIVE**: a
casual/social play interlude run **per Site** (`Player.home_site`), embedded in a
Season's phase flow (e.g. RR → member night → Tournament). Full implementation note
in [`laserforce_simulator/matches/CLAUDE.md`](laserforce_simulator/matches/CLAUDE.md)
(**LG-07a member night (core slice)**); seam contract:
[`.claude/worktrees/lg-07-member-night-seam-contract.md`](.claude/worktrees/lg-07-member-night-seam-contract.md).

**Shipped design (resolved by ADR-0033).** A member-night game is a drawn-team
2-Round **`Match` stamped `season=<this>` AND `season_phase=<the member_night
phase>`** — **season-attached on purpose**, diverging from the playoff `season=NULL`
FK-chain precedent (ADR-0023) so games are discoverable in raw
`Match.objects.filter(season=...)` season history, while kept out of the competitive
**Standings**. The two Teams are `is_draw_team=True` and **borrow** real Players (the
LG-02x-1 / ADR-0022 posture — `PlayerRoundState` references the real Player, career
stats stay unified). A NEW Django-free pure module `matches/member_night.py`
(constants `MIN_POOL=12` / `MAX_POOL=18` / `MIN_GAMES=5` / `MAX_GAMES=9` /
`PLAYERS_PER_GAME=12`; `MemberNightGame` dataclass; `split_balanced` /
`draw_member_night_games` / `draw_site_games`; reuses
`build_random_role_assignment`) owns the balanced 6/6 split + game-count / pool
draws over a 12–18 Site pool. **Completion is DERIVED** (`_member_night_phase_complete`:
≥1 member-night Match AND all `is_completed`, OR no viable Site — no
`SeasonPhase.state`, ADR-0023 honoured); the play-loop barrier
`_tournament_barrier_ordinal` is renamed → **`_phase_barrier_ordinal`** and
generalised to also halt the RR loop on an incomplete `member_night` phase. The
single predicate `.exclude(season_phase__phase_type="member_night")` keeps member
nights out of Standings at **7 sites** (PLAY-01 live poll covered transitively). The
run is **build-then-drain** — `member_night_setup` (POST sync, 302) pre-creates the
drawn Teams + unplayed Match shells; `play_member_night` (POST async, 202) enqueues
`play_member_night_task` which drains via `simulate_scheduled_round` VERBATIM (GAME
counts, PLAY-01 cancel + `finally` cleanup), polled through the reused `play_status`.
The composer (`parse_phase_composition`) stops rejecting a bare `member_night` token
and `templates/leagues/create.html` makes the "coming soon" placeholder a live
option; the NAV-01 `Play ▾` topnav gains the setup/drain controls
(`topbar-play-member-night-setup` / `-site` / `-play`). **NO migration, NO Score
Calibration re-baseline, NON-deterministic by design** (fresh `random.Random()`
outside the SIM-07/08 seed chain). `_rr_phase_complete`, `_is_finished`, and
`next_season` carry-forward are UNCHANGED.

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
