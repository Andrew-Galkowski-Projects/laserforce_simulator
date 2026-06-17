# Development Plan

Organized by phase. Phases 0–2 are prerequisites for later phases; don't skip ahead.
Story IDs from `sm5_user_stories_v2.html` are referenced where applicable.

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

All Part-2 slices through **Part2c-3e** are **shipped** and their full
implementation notes have moved to [`PLAN-completed.md`](PLAN-completed.md)
(under the LG-02 · Part 2 section): the `SeasonPhase` foundation
(Part2a), the create-League composer UI + per-phase format / Tournament
columns (Part2b), and the Part2c embed sequence — RR →
single-elimination playoff (Part2c-1), the multi-RR play loop +
`Match.season_phase` FK (Part2c-2), `double_round_robin` + `Match.leg`
(Part2c-3a), the dormant `tournament_mode` field (Part2c-3b), mid-season
tournaments (Part2c-3c), per-tournament-block config — live
`tournament_cut` + dormant `tournament_format` (Part2c-3d), and
non-single-elim finals embeds — the five-format build + per-format
sub-config (Part2c-3e). **Part2c-3f** (season-linked playoff Match
history — the Team History Overall-tab corpus widening + the
`playoff_appearances` counter — plus weekly playoff pacing —
`play_next_bracket_round` + phase-aware `play_week`/`play_season_task`) is
now **DONE** and stays below (its impl note has not yet moved to
PLAN-completed.md); the mid-season `random_draw` re-draw is the only
remaining Part2c follow-up.

- **LG-02-Part2c-3f · [DONE] Season-linked playoff Match history + weekly
  playoff pacing.** The final Part2c-3 slice — a **thin view/engine-layer**
  orchestration with **NO model change, NO migration, NO simulator/engine
  mechanics change, NO Score Calibration re-baseline**. (A) **Season-linked
  playoff history:** widened the Team History **Overall tab** corpus
  (`matches/league_screens/team_history.py::_build_overall_context`) from
  regular-season-only to a `.distinct()` UNION of regular-season rounds +
  **season-embedded playoff rounds** via the FK chain
  `match__series_match__node__tournament__season_phases__isnull=False` (the
  `season_phases` reverse-set guard separates a season playoff from a standalone
  *sandbox* Tournament — both carry `season=NULL` per Part2c-1 decision #3), and
  filled `playoff_appearances = Tournament.objects.filter(season_phases__isnull=
  False, participants__team=team).distinct().count()` into the existing
  `compute_overall_record(..., playoff_appearances=…)` kwarg (the pure
  `team_history_logic.py` + the `team_history.html` render slot already supported
  it — **assert-only, unchanged**). Players tab already counts playoff
  `PlayerRoundState` rows (one accepted limit: their `season_year` is `None`);
  Seasons-tab rank stays regular-season-only. (B) **Weekly playoff pacing:** new
  `matches/tournament_engine.py::play_next_bracket_round(tournament) -> int`
  (drain the lowest incomplete `(bracket_type, bracket_round)` STAGE to clinch via
  the VERBATIM per-Match-atomic `play_next_node`; return the nodes-clinched count)
  + phase-aware `play_week` (tournament cursor → one bracket STAGE +
  `complete_if_finished()` + 302; else the RR matchday path byte-identical) +
  phase-aware `play_season_task` tail (drain bracket STAGES on the shared
  `max_matchdays − rr_weeks_played` budget, `None` = drain to champion; PROGRESS
  switches to `stage_progress` STAGE-counts at the boundary). The **Playoffs
  League screen + registry/sidebar/url/re-export stay byte-unchanged** (embedded
  bracket, no game-log reshape); `play_single_round` / `play_playoffs` /
  `play_playoffs_task` untouched. The `member_night` phase type stays inert (see
  its own PLAN item below). Seam contract:
  [`.claude/worktrees/lg-02-part2c-3f-seam-contract.md`](.claude/worktrees/lg-02-part2c-3f-seam-contract.md);
  impl notes in [`matches/CLAUDE.md`](laserforce_simulator/matches/CLAUDE.md).

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

### FIN-03 · [DONE] Wire the *scouting* budget into player potential (LG-05)

Follow-up to FIN-01 (which ships the scouting budget as a cost-only line-item). Wire the team's effective
**scouting** level into **LG-05** `compute_potential`'s scouting-noise band — better scouting tightens the
potential estimate, neglected scouting widens it. Requires mapping the 1–100 ZenGM level onto LG-05's
`scouting_budget` `[0,100]` seam, which **FIN-01 leaves fixed at `DEFAULT_SCOUTING_BUDGET = 50`** (the
LG-05 / CAR-01 deferral). **Finance-OFF leagues keep `scouting_budget = 50`** (byte-identical to LG-05
today). Changes the seeded potential estimate for finance-ON leagues (read-only to the simulator, **no
Score Calibration re-baseline**). Depends on **FIN-01**.

**Status: DONE.** Lights up the **scouting** budget FIN-01 shipped cost-only — the **structural mirror of
FIN-02** (coaching→development): a Team's effective scouting level now sets the **width of its players'
LG-05 Potential-estimate band** at each `next_season` rollover (and at founding) — better scouting
**tightens** the estimate around the noise-free projection, neglected scouting **widens** it — while a
**finance-OFF (or `scouting_budget=50`) league stays byte-identical to LG-05** and **development (LG-04) is
left untouched** (scouting never touches realised Stats; FIN-02 owns coaching→development). **Mapping
(`finance.py`, NOT `development.py`).** NEW pure fn `matches/finance.py::scouting_budget(level: int) ->
float` + constants `NEUTRAL_SCOUTING_BUDGET = 50.0` / `MAX_SCOUTING_BUDGET = 100.0`, reusing FIN-01's
`_bound` + `DEFAULT_LEVEL` (34) / `MAX_LEVEL` (100). Unlike FIN-02's dual-slope neutral-pivot
`coaching_effect`, this is **single-slope** anchored at `DEFAULT_LEVEL`→neutral / `MAX_LEVEL`→max — yields
**level 1 → 25.0, 34 → 50.0, 100 → 100.0**; `NEUTRAL_SCOUTING_BUDGET` just **equals** LG-05's
`DEFAULT_SCOUTING_BUDGET = 50` by value (**`finance.py` MUST NOT import `development`**; the frozen
no-Django allowlist holds). `compute_potential` / `DEFAULT_SCOUTING_BUDGET` / `POTENTIAL_MAX_SD = 8.0` are
consumed **verbatim** — the band `sd = POTENTIAL_MAX_SD * (1 − scouting_budget/100)` and its
**one-`rng.gauss`-draw-always** guarantee are LG-05's, unperturbed; FIN-03 only threads a different float
into the existing keyword arg. **Wiring (`league_views.py`).** NEW `_scouting_budget_by_team(league,
latest_completed) -> dict[int, float]` (the twin of FIN-02's `_coaching_effect_by_team`; **gated on
`finance_enabled`, OFF ⇒ `{}`**; per developing Team the games-weighted mean of `budget_scouting` over the
last ≤3 completed-Season `TeamSeasonFinance` rows with a current-`Team.budget_scouting` fallback, mapped
via `finance.scouting_budget`), threaded into CHANGED `_develop_league_for_new_season` as
`scouting_budget=scouting_by_team.get(player.team_id, development.DEFAULT_SCOUTING_BUDGET)` (pool players →
default 50); CHANGED `_write_baseline_ratings` builds a per-team **current-level** band map (founding pass,
no completed Season) and threads the same kwarg (finance-ON only). Finance OFF ⇒ `{}` / no band map ⇒ every
`.get` yields 50 ⇒ **byte-identical to LG-05** (regression-pinned).

**Scope addition (user-decided): strength-seed AI + manager budgets at create.** NEW
`_seed_team_budgets_by_strength(teams) -> None` (`league_views.py`) at `league_create`, **finance-ON only,
BEFORE `_write_baseline_ratings`** (so the baseline reads seeded levels): ranks enrolled teams by **mean
active-roster `overall_rating` desc** (tie-break `team_id` asc), assigns a **rank-linear** level across the
band `[SEED_BUDGET_MIN, SEED_BUDGET_MAX] = [20, 90]` (strongest → 90, weakest → 20; **single team →
`SEED_BUDGET_SINGLE = 55`**; int), and sets the **SAME** level on **all three** budget fields
(`budget_scouting` / `budget_coaching` / `budget_facilities`) for **every** team **including
`League.current_team`** (the manager edits theirs later), persisted via `bulk_update`. Seeded **ONCE at
create, frozen forever** — `next_season` carries Team rows forward untouched (NO re-seed). CPU teams never
adjust their budgets this slice; a future "CPU teams adjust budgets" feature is explicitly **deferred**, the
manager's own Team stays editable on the Team Finances screen.

**Scope-out (locked).** **Development untouched** (LG-04 `develop_player_stats` / `develop_stat`
byte-unchanged; coaching→development = FIN-02). **Estimate-precision only** — scouting changes the *seeded
Potential estimate*, never a realised Stat, read-only to the simulator. **The level→float map is in
`finance.py`, never `development.py`** (`NEUTRAL_SCOUTING_BUDGET` only *equals* LG-05's
`DEFAULT_SCOUTING_BUDGET` by value). **NO migration** (`TeamSeasonFinance.budget_scouting` / `.games_played`
+ the `Team.budget_*` fields already exist), **NO simulator change**, **NO Score Calibration re-baseline**
(estimate-only, finance-ON only; finance-OFF byte-identical). **No new ADR** (FIN-03 Consequences addendum
on [ADR-0027](docs/adr/0027-team-finance-subsystem.md)). **No new CONTEXT.md term beyond the already-written
Scouting estimate edge** entry (+ the Potential pointer CAR-01→FIN-03 / Budget avoid-line edits). Seam
contract: [`.claude/worktrees/fin-03-seam-contract.md`](.claude/worktrees/fin-03-seam-contract.md); impl
note in [`matches/CLAUDE.md`](laserforce_simulator/matches/CLAUDE.md) `## FIN-03 scouting budget into player
potential`. Tests (all **extended**, not new files): `matches/tests/test_finance.py` (`scouting_budget`
mapping 1→25.0 / 34→50.0 / 100→100.0 + clamps) + `test_league_create.py`
(`_seed_team_budgets_by_strength` — rank-linear `[20, 90]`, single→55, all three fields equal,
`current_team` included, finance-ON-before-baseline) + `test_league_next_season.py`
(`_scouting_budget_by_team` games-weighted ≤3-Season mean + fallback, **OFF ⇒ `{}`**, byte-identical-OFF
Potential rows, AI-budget-frozen-across-rollover).

### FIN-04 · [DONE] Health budget + injury/availability system

The **health** budget category FIN-01 deferred (ZenGM: health spending shortens injuries). Introduces a
minimal **injury / availability** model so the fourth budget category buys a real edge (fewer / shorter
unavailabilities), then wires the **health** level into it. This is the only one of the four ZenGM
categories with **no existing seam** in our domain (we have no injuries today), so it carries the most new
surface — its own grill. Depends on **FIN-01** (the budget-level + finance-toggle infrastructure) and is
sequenced after FIN-02 / FIN-03.

**Status: DONE.** Lands the **fourth ZenGM budget** — a per-Team **health budget** (a cost line plus a
ratings edge expressed as injury *duration*) backed by a minimal **injury / availability** model.
Architecture (LOCKED): injuries roll **OUTSIDE the tick loop**, are resolved **in-memory at fixture time**
(auto-substitute or play-hurt), and a per-Player availability counter is decremented once per fixture the
Team plays. **Finance-gated** on `_is_career_league(season.league) AND season.league.finance_enabled` —
OFF (or sandbox / multiplayer) ⇒ no rolls, no roster mutation, no `games_unavailable` change ⇒
**byte-identical to today**; the simulator is **byte-untouched** (no signature change, no
`before_round_hook`) ⇒ **NO Score Calibration re-baseline**. **Per-fixture, regular-season round-robin
fixtures only** (tournaments/playoffs untouched). **Subjects = the 6 active-roster STARTERS only** (bench /
free-agent fill-ins never roll, never tracked — no orphan injuries); a healthy fielding starter rolls a new
injury, an already-unavailable starter decrements without re-rolling, and each now-unavailable starter is
resolved by the Team's `injury_policy` — **`auto_sub`** (rewrite the in-memory `slot_*` FK to a substitute,
priority bench → League free-agent pool) or **`play_hurt`** (subtract the per-stat penalty from the injured
Player's 19 stats), with **`play_hurt` as the universal no-sub fallback** so the roster always resolves to a
valid 6. The health edge reads `finance.health_effect(Team.budget_health)` from the **LIVE current
`budget_health`** at fixture time (NOT the games-weighted ≤3-Season smoothing FIN-02/03 use) and scales the
drawn injury **DURATION** down only — frequency is a fixed base rate × age. **No re-baseline.**

**Model + modules.** NEW fields `teams.Player.games_unavailable` (`PositiveSmallIntegerField(default=0)`,
the availability counter — reset to 0 at `next_season` rollover), `teams.Team.budget_health`
(`PositiveSmallIntegerField(default=34)`, the fourth budget level), `teams.Team.injury_policy`
(`CharField(choices=INJURY_POLICY_CHOICES, default="auto_sub")` — `auto_sub` / `play_hurt`), and
`matches.TeamSeasonFinance.health_cost` (`FloatField(default=0.0)`, after `min_payroll_penalty`). Migrations
`teams/migrations/0014_player_team_health_injury.py` (dep `0013`; 3× `AddField`) +
`matches/migrations/0052_teamseasonfinance_health_cost.py` (dep `0051`; 1× `AddField`) — both AddField-only,
**NO `RunPython`/backfill** (ADR-0004 disposable-data posture). NEW pure module `matches/injury.py`
(Django-free, frozen import allowlist `dataclasses`/`typing`/`random`/`collections`, RNG **injected**
per-fixture never the SIM seed chain): fns `age_factor` / `injury_probability` (flat base × age, **no Stat
input**) / `roll_injury` / `draw_duration` (health-edge-scaled duration) / `play_hurt_penalty`, with the
RNG-consumption order pinned (`roll_injury` then, if hit, `draw_duration`). NEW `finance.py::health_effect`
+ `MAX_HEALTH_EFFECT = 0.5`, a trailing `ExpenseLines.health` field, and a keyword-only `health_level`
threaded through `season_expenses` / `compute_team_finance` (the seventh expense line). NEW
`matches.league_views.resolve_injuries_for_fixture(season, team_red, team_blue) -> dict` /
`restore_after_fixture(token) -> None` (in-memory mutate-then-restore — **never `.save()`** the temporary
roster; only `games_unavailable` is persisted), wrapped around the `simulate_scheduled_round(...)` call in
`play_season_task` / `play_week` / `play_week_live` (the RR branch only — the playoff branch is untouched),
plus the `games_unavailable = 0` reset in `_develop_league_for_new_season` and the `health_level=` /
`"health_cost"` thread inside the gated `_ensure_team_finances`. UI: the Team Finances screen gains DOM ids
`team-finances-budget-health` / `team-finances-injury-policy` / `team-finances-availability` (+
`-availability-empty`) for the manager-editable level, policy toggle, and unavailable-players display.

**Scope-out (locked).** tournaments/playoffs untouched, no injury-type taxonomy, no in-sim injuries, no
frequency-from-health, no Stat-driven probability, FIN-05 deferred, **NO Score Calibration re-baseline**.
Decision: [ADR-0028](docs/adr/0028-health-budget-injury-availability.md). Seam contract:
[`.claude/worktrees/fin-04-health-injury-seam-contract.md`](.claude/worktrees/fin-04-health-injury-seam-contract.md);
impl note in [`matches/CLAUDE.md`](laserforce_simulator/matches/CLAUDE.md) `## FIN-04 health budget +
injury/availability`. Tests: `matches/tests/test_injury.py` (NEW, pure-unit incl. `TestNoDjangoImportsLeaked`)
+ `test_finance.py` (EXTEND — `health_effect` mapping, the seventh expense) + `test_injury_resolution.py`
(NEW, DB — starter-only subjects, roll/decrement, auto_sub + play_hurt + no-sub fallback, never-`.save()`
restore, rollover reset, byte-identical-OFF, `health_cost`→`profit`) + `test_finance_screens.py` (EXTEND —
the `budget_health` / `injury_policy` POST + the new DOM ids + the availability display).

### FIN-05 · Luxury-tax challenge-mode firing

Re-opens the ZenGM **luxury-tax challenge-mode firing** CAR-02 and FIN-01 both deferred: an optional
per-League rule that fires the Manager outright whenever they pay the luxury tax in a completed Season
(independent of cumulative owner mood). FIN-01 ships the luxury-tax **expense line** that makes the trigger
computable but **not** the firing rule itself. Mirrors ZenGM's `challengeFiredLuxuryTax` game attribute
(default off). Depends on **FIN-01** (the payroll + luxury-tax model) and on the CAR-02 firing lifecycle.

### SUB-01 · Sub-leagues + per-sub-league rotating map pools

Introduce **sub-leagues** as a first-class domain concept: an
optional partition of a `Season`'s enrolled Teams into named groups
(conferences / divisions / pools), modelled as a new `SubLeague`
container under `Season` with its own `teams` M2M and an ordered
list of `ArenaMap`s. Each Round's map is then resolved from the
sub-league's pool by matchday (`maps[matchday % len(maps)]`), giving
the deterministic-rotation third mode that LG-01j originally
listed but had no domain referent for. Carved out of LG-01j on
2026-05-28 because no `SubLeague` model existed at LG-01j time and
the user wanted to defer the introduction until the career-mode
slice was in place. Depends on **LG-01j** (the per-Season map
config UI + `play_season_task` `arena_map` thread it adds — SUB-01
extends both with a sub-league-aware map-resolver branch) and on
**CAR-03** (sub-league grouping is most useful once manager-mode
career play is driving the Season). Adds the **SubLeague** term
to CONTEXT.md and ships an ADR for the new model + the
schedule-generation interaction (a sub-league partition implies
intra-pool vs cross-pool fixtures, a sequencing decision LG-02
will also lean on).

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

### MECH-07 · Role-aware goal-selection rework (MAP-05 follow-up)

Make changes to role-aware goal selection (MAP-05). Shape is **still being worked out** — scope and
acceptance criteria are deliberately deferred.

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
