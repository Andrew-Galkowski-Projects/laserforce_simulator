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

### LG-03 · [DONE] Season-end awards

Computed from `PlayerRoundState` aggregates: Most Points, Highest K/D by role, Best Medic, 
Most Efficient Nuke, Best Accuracy. Awards page at `/seasons/<id>/awards/`. Award badge on player profile.

Also surface the headline **season MVP** (and, once LG-02 playoffs land, a
**Finals MVP**) on the **League History** table (LG-01f) — the reference product
puts both in its history row next to Champion / Runner-up, and ours currently has
no awards column. See
[`docs/zengm-comparison/season-lifecycle.md`](docs/zengm-comparison/season-lifecycle.md).

**Status: DONE.** A **read-only / derived** league screen — every award recomputed
**on render (transient)** from frozen `PlayerRoundState` rows, with **NO model field,
NO migration, NO simulator change, NO Score Calibration re-baseline, NO persisted award
rows**. A new Django-free pure module `matches/season_awards.py` (allowlist
`dataclasses` / `typing` / `collections`, guarded by `TestNoDjangoImportsLeaked`) exposes
`compute_season_awards(player_rounds, *, min_games)` and `pick_finals_mvp(final_round_dicts)`
over frozen `AwardWinner` / `AwardSet` dataclasses; the view does ALL ORM work and feeds the
pure fn a flat `list[dict]`. **Corpus split:** the regular-season awards read
`PlayerRoundState.objects.filter(game_round__match__season=season)` — season-embedded
**playoff** Matches carry `season=NULL` (Part2c-1 #3) and are naturally excluded — while
**Finals MVP** is computed separately over the championship bracket node's rounds and is set
**only on a bracket-format playoff** (`single/double_elimination`, `round_robin_double_elim`;
`None` for `round_robin`/`swiss`/no playoff). The award set is the **6 regular-season awards**
— **Most Points**, **Best Accuracy**, **K/D by role** (5 winners, one per role), **Best
Medic**, **Most Efficient Nuke**, **Season MVP** (mean of `get_mvp`) — **plus the separate
Finals MVP**. **Qualifier:** the rate/mean awards (Season MVP, Best Accuracy, Most Efficient
Nuke) require `games(player) >= ceil(max_games_any_player / 2)`; the total/count awards (Most
Points, Best Medic, K/D) are ungated; ties break by metric → games desc → `player_id` asc.
**Three surfaces:** the new **awards page** (`season_awards` view / `/seasons/<id>/awards/`,
league-sidebar shell, GET-only); two new **League History** columns (Season MVP / Finals MVP —
`_build_history_row` grows 11 → 13 keys, reusing the same shared regular-season-dicts +
finals-corpus helper); and the **player profile** awards badge (the
`league-player-awards-stub` placeholder becomes the live `league-player-awards` block fed by a
new `player_awards` context list). Seam contract:
[`.claude/worktrees/lg-03-season-awards-seam-contract.md`](.claude/worktrees/lg-03-season-awards-seam-contract.md);
impl notes in [`matches/CLAUDE.md`](laserforce_simulator/matches/CLAUDE.md).

### LG-04 · [DONE] Season-end stat updates

At the end of each season, all players (on active teams or otherwise) receive a stat update.
The original framing factored in **new experience** (games played this season), **player age**,
and **prior experience** (historical games), with default weights fixed in code but overridable
per season — but the LG-04 grill (2026-06-10) confirmed the system is modeled on **ZenGM**,
whose `developSeason` is driven **purely by an age curve** (in-game production never moves
ratings). That framing is therefore **superseded**: LG-04 follows ZenGM — **age-driven**;
**games-played is cosmetic** (it ticks a counter but is never a develop input), per
[ADR-0024](docs/adr/0024-zengm-player-development-ratings-history.md).

**Status: DONE.** Development is a **ZenGM-faithful age curve** (young trend up, peak
mid-to-late 20s, older decline increasingly fast; per-stat age modifiers + change limits +
random noise, coaching fixed at 0), run **league-scoped at each `next_season` rollover** (the
preseason analogue) over the rolling League's **developing set** — its snapshot Teams' players
(active slots + bench) plus the `free_agent_pool` players: each Player is aged `+1`, its 19 live
`Player` stat fields are **mutated in place** (the first persisted `Player`-stat mutation in the
league flow), its `total_games` is **cosmetically ticked** (active player by their exact
regular-season appearance count in the just-completed Season — playoff rounds carry
`season=NULL` and are excluded; free-agent by a smaller random amount), and one immutable
**`PlayerSeasonRating`** snapshot row (19 stats + age + `overall_rating` + a reserved nullable
`potential`) is written for the new Season. A **baseline** `PlayerSeasonRating` row (as-generated
stats, no development) is written for every founding Player at `league_create`; the live `Player`
fields stay the Simulator's source of truth and the rating rows are a read-only audit trail. The
develop math lives in a **Django-free pure module `matches/development.py`** (allowlist
`dataclasses`/`typing`/`random`/`collections`, RNG **injected**, guarded by
`TestNoDjangoImportsLeaked`); production builds a **fresh `random.Random()` per rollover and
stores no seed** (the row is the audit trail). A migration ships
(`0048_playerseasonrating.py`, one `CreateModel`, no backfill). The LG-06h
`league-player-ratings-history-stub` becomes the live `league-player-ratings-history` block — a
Chart.js overall-rating-over-time trend + per-Season stat table (Potential renders `—`).
**NO Score Calibration re-baseline** (Stat *inputs* change, no simulation *mechanic*).
**Deferred:** the per-team **coaching/scouting budget** knob (no per-(team, season) state yet —
coaching effect fixed at 0, deferred to a slice designed with LG-05's scouting budget),
**retirement / replacement intake**, and **`potential`** (reserved nullable column, computed in
**LG-05**). See [ADR-0024](docs/adr/0024-zengm-player-development-ratings-history.md), seam
contract
[`.claude/worktrees/lg-04-player-development-seam-contract.md`](.claude/worktrees/lg-04-player-development-seam-contract.md),
and impl notes in [`matches/CLAUDE.md`](laserforce_simulator/matches/CLAUDE.md).

### LG-05 · [DONE] Player potential

Each player carries a `potential` attribute: a dynamically computed estimate of their likely stat ceiling.

The original framing tied the scouting-noise band to a **per-season scouting budget allocation on
the team**. That framing is **superseded** the same way LG-04's "experience" framing was: LG-05
ships the noise band off a **FIXED `DEFAULT_SCOUTING_BUDGET = 50` constant** (no per-(team, season)
state exists yet), and **CAR-01** later promotes the budget to a per-team field — exactly the
deferral ADR-0024 recorded for the coaching/scouting knob.

**Status: DONE.** `potential` is a per-`Player` **projected peak overall** (a `FloatField`),
computed at each **season-end** — the `league_create` baseline AND every per-League `next_season`
rollover — alongside LG-04 development, never on demand. The compute is a **noise-free
forward-projection** of the LG-04 age curve (`matches/development.py::_project_peak_overall`):
the LG-04 per-stat curve is rolled forward from the player's current age to age 40 with **zero
noise** (a `0.9` midpoint multiplier in place of LG-04's `rng.uniform(0.4, 1.4)`), tracking the
**running-max overall** across the path — that running max is the ceiling, **floored at the
player's current overall** (it can never predict regression below the present average) and
**capped at 100**. `compute_potential(stats, age, rng, *, scouting_budget=DEFAULT_SCOUTING_BUDGET)`
then lays a **scouting-noise band** over that ceiling: `sd = POTENTIAL_MAX_SD * (1 - budget/100)`
(budget 0 → max sd, 100 → 0), exactly **one `rng.gauss(0, sd)` draw**, re-clamped to
`[current_overall, 100]`. Both functions are **pure** (Django-free, no new import —
`TestNoDjangoImportsLeaked` stays green), and LG-04's `develop_stat` / `develop_player_stats` are
left **byte-unchanged**.

The value lands in a **new live `Player.potential` FloatField** (nullable, default `None`;
migration `teams/migrations/0012_player_potential.py`, single `AddField`, dep
`0011_team_is_draw_team`, no backfill) AND fills the **`PlayerSeasonRating.potential`** column LG-04
reserved-but-always-`None`. Two write sites in `matches/league_views.py` set it:
`_write_baseline_ratings` (founding baseline) and `_develop_league_for_new_season` (rollover, on
the POST-development stats + already-incremented age). Each rollover builds a **SEPARATE fresh
`random.Random()`** for the gauss draw, INDEPENDENT of LG-04's develop RNG — so LG-04's pinned
1-gauss-then-19-uniform sequence and its seeded develop output stay **byte-identical**. Players
outside any league flow keep `potential = None`.

**UI:** `potential` becomes a **sortable `Pot` column** on `player_ratings` + `free_agents`
(nulls-last in both directions via `F("potential").desc/asc(nulls_last=True)`), a **render-only**
cell on `team_roster`, a **live card** on the LG-06h player page (`#league-player-potential`,
replacing the "Arrives with LG-05" stub), and the LG-04 ratings-history `Pot` column now lights up
for rows written after LG-05. **NO Score Calibration re-baseline** — `potential` is **read-only to
the simulator** (never a sim input), so no simulation mechanic changes and **no new ADR** (the
column is a reversible nullable add, recomputed every rollover). MMR / Rank stay non-sortable `—`
placeholders (STAT-PROXY-01); the global HX-01 career page and the LG-00c `/players/` list are
untouched. The **Potential** CONTEXT.md term is already written. See
[ADR-0024](docs/adr/0024-zengm-player-development-ratings-history.md) (the LG-05 consequences
addendum), seam contract
[`.claude/worktrees/lg-05-player-potential-seam-contract.md`](.claude/worktrees/lg-05-player-potential-seam-contract.md),
and impl notes in [`matches/CLAUDE.md`](laserforce_simulator/matches/CLAUDE.md) `## LG-05 player
potential`.

### INFRA-01 · PostgreSQL/SQLite Parity Hardening

**Status: DONE.** **Reframed on contact:** Postgres was **already canonical** —
the Docker/CI/Fly deploy work had landed it (`psycopg2-binary` in
`requirements.txt`, a `postgres:16` service in `docker-compose.yml`, CI
(`.github/workflows/ci.yml`) running both the full pytest suite **and** a docker
smoke job against `postgres:16`, Fly.io deploying off that image, and
`settings.py` reading `DATABASE_URL` via `dj_database_url`). INFRA-01 therefore
became a **HARDEN + VERIFY + DOCUMENT-parity** slice, **not** a migration: the
original "switch to Postgres" framing was obsolete before the task started.

**SQLite stays the guarded dev-only default** when `DATABASE_URL` is unset. The
SQLite write-contention hardening **stays in place, guarded**: the `OPTIONS`
block in `settings.py` (`timeout` / `transaction_mode`) and the
`core/db_pragmas.py` WAL `connection_created` hook both early-return / no-op on
Postgres (`connection.vendor != "sqlite"`).

**Production-code surface: NONE.** `settings.py` and `core/db_pragmas.py` are
**byte-unchanged**; **no model field change → no migration**; **no Score
Calibration re-baseline** (nothing touches a simulation input). The only
artifacts that land are **two pure guards in `core/tests.py`**: **(A)**
`set_sqlite_pragmas` early-returns on a non-sqlite (`vendor="postgresql"`)
connection so the WAL PRAGMAs **never run on Postgres** (asserts `cursor` not
called, no DB hit, backend-agnostic); **(B)** a `MapZoneConfig.zone_data`
nested-payload round-trip (2D int `zones` + `wall_meta` dict + 2D float
`elevation`) that deep-equals after `refresh_from_db()`, covering **SQLite-text
vs Postgres-jsonb** `JSONField` serialization parity (passes on SQLite locally,
Postgres in CI).

**SQLite-assumption audit came back clean:** no raw SQL / `.extra()` / `.raw()`
except the guarded PRAGMA; **zero `icontains` / `iexact`** case-insensitive
lookups (so no Postgres case-sensitivity break); the only residual delta is an
`order_by("name")` collation difference, which is **cosmetic**.

**Acceptance:** lock-freedom is the documented **Postgres MVCC** property (no
single-writer lock — the `database is locked` class of error cannot arise);
**CI proves the full suite green on Postgres**; the "Play Until End of Season on
compose-Postgres, no lock errors" end-to-end smoke is a **DEFERRED manual
check** (documented as manual — **not** claimed as run).

See [ADR-0025](docs/adr/0025-postgresql-canonical-sqlite-dev-only.md) (full
rationale) and the seam contract
[`.claude/worktrees/infra-01-postgres-sqlite-parity-seam-contract.md`](.claude/worktrees/infra-01-postgres-sqlite-parity-seam-contract.md);
this PLAN note is the impl note (no app-level `CLAUDE.md` change — the task is
tests + docs only).

---

---

## Phase 5.5 — Single-Player Career Mode

A single-user play mode where the user acts as a team manager navigating a league season. This phase
sits between the League system (Phase 5) and full multiplayer (Phase 6).

### CAR-01 · Manager role and team assignment

In single-player career mode, the user is a team manager (not a player in the simulation).
The user is assigned to a team at the start of a career league. Each season the user manages their
team through the league schedule.

### LG-01i · Season "One Week (Live)" replay UI

Per-Round live replay surface invoked from the Play dropdown — a
"One Week (Live)" entry that plays the next matchday tick-by-tick in
the browser rather than committing the Rounds straight to the DB.
User watches each tag/down/elimination as it happens with a
play/pause/scrub control, then commits or discards the run at the
end. Depends on **CAR-01** + the new Season-replay engine (the
tick-stream surface the manager-mode career UI also consumes).
Deferred from LG-01d. Re-sequenced from Phase 5 to Phase 5.5
(post-CAR-01) on 2026-05-28 because CAR-01 owns the Season-replay
tick-stream engine LG-01i consumes.

### CAR-02 · Performance-based firing

The system tracks manager performance metrics (win rate, standings position, point differential).
When a manager's performance falls below a configurable threshold, the system fires them automatically.
After being fired, the manager can apply for or be assigned to another team in the league.

### CAR-03 · Career isolation from multiplayer

The firing mechanic and team-switching only apply in single-user career mode. In multiplayer leagues,
each user is locked to their team for the full duration of the league — no transfers, no firing.

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

### MOVE-05 · Simulation engine de-duplication (refactor)

`simulation.py` is heavily bloated and contains duplicated logic. Continue the consolidation already
begun.

**Status:** partially done — `ResourceBasedSimulator` was removed (SIM-09). Several areas still
**duplicate the tagging-and-related-checks code** (a player tag plus all the associated checks appears
in more than one place). Extract the shared tag/check path into a single helper so there is one
implementation. No behavioural change intended; fold any incidental delta into the existing pending
Score Calibration re-baseline.

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
