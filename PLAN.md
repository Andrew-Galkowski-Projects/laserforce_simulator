# Development Plan

Organized by phase. Phases 0–2 are prerequisites for later phases; don't skip ahead.
Story IDs from `sm5_user_stories_v2.html` are referenced where applicable.

---

## Phase 5 — Infrastructure & League System

### LG-02 · Tournament formats

**Status: PART 1 sandbox formats all DONE; LG-02x-2 (Duos / Trios) deferred; Part 2 foundation (Part2a) DONE; Part2b (create-League composer + dormant phase columns) DONE; Part2c-1 (RR → single-elimination playoff embed) DONE; Part2c-2 SPINE (multi-RR play loop + `Match.season_phase` FK + cross-phase matchday offsetting) DONE; Part2c-3a (first alternative regular-season format — `double_round_robin` + `Match.leg`, wiring the Part2b dormant per-phase `schedule_format` column end-to-end) DONE; Part2c-3b (dormant per-phase `SeasonPhase.tournament_mode` field) DONE; Part2c-3c (mid-season tournaments — `strength` + `unseeded` build, the `tournament:<mode>` wire token, the standings-only compose-guard relaxation, and the play-loop barrier) DONE; Part2c-3 remainder (c-3d per-tournament-block config; c-3e non-single-elim finals embeds; c-3f season-linked playoff Match history + weekly playoff pacing) NOT STARTED, and the mid-season `random_draw` build is DEFERRED.**
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

Bracket rendered as a visual tree; results auto-advance winners (look at the
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

- **LG-02-Part2a · [DONE] `SeasonPhase` foundation slice.** Ships the persisted
  **`SeasonPhase`** model (FK → **Season** with `related_name="phases"`, a
  1-based `ordinal`, a `phase_type` enum whose `PHASE_TYPE_CHOICES` declares all
  three of `round_robin` / `tournament` / `member_night` now though only
  `round_robin` has behaviour, `uniq_season_phase_ordinal` on `(season,
  ordinal)`), migration `0041_season_phase` (`CreateModel`-only, dep
  `0040_tournament_random_draw`, **no `RunPython` / no backfill** — the
  [ADR-0004](docs/adr/0004-simulation-data-is-disposable.md) disposable-data
  precedent), and a **single chokepoint on `Season`**
  (`ordered_phases() -> list[SeasonPhase]` / `scheduled_fixtures() ->
  list[ScheduleFixture]`) that the whole Season read-path now routes through
  instead of inline `generate_schedule(...)` calls (`_is_finished`,
  `play_season_task`, `season_schedule`, `_build_dashboard_context`,
  `league_history` Play-Week preview, `team_schedule`). A Season with **zero**
  persisted phases falls back to an **implicit single `round_robin` phase** (a
  real but unsaved `SeasonPhase`, `pk is None`) — byte-identical to today; a new
  Season gets one explicit `round_robin` phase created inside the atomic block
  at `league_create` / `next_season`. `Season.schedule_format` stays as-is
  (legacy; the RR phase reads it). **Zero user-visible change**, **no simulator
  change / no RNG / no Score Calibration re-baseline**. Admin: `SeasonPhaseAdmin`.
  Seam contract:
  [`.claude/worktrees/lg-02-part2a-seam-contract.md`](.claude/worktrees/lg-02-part2a-seam-contract.md);
  app guide: `matches/CLAUDE.md` "LG-02-Part2a season phase foundation". Tests:
  `matches/tests/test_season_phase.py` (NEW) + extensions to
  `test_league_create.py` / `test_league_next_season.py` / `views_tests.py` /
  `test_league_play.py`.

- **LG-02-Part2b · [DONE] League-create "+" composer UI + per-phase format.**
  The create-League surface gained a vanilla-JS "**+** Add block" composer
  (LG-01d inline-`<script>` precedent) that writes **multiple ordered
  `SeasonPhase` rows** — the admin picks/orders `round_robin` / `tournament`
  blocks (e.g. RR → Tournament) instead of the single auto-created `round_robin`
  phase Part2a wrote. Landed **two dormant `SeasonPhase` columns**: a per-phase
  **`schedule_format`** (`CharField(32, null=True, blank=True)` — an RR phase
  copies `Season.schedule_format`, a tournament phase is `NULL`) so alternative
  regular-season formats can later land on the phase rather than the Season, and
  the forward **`SeasonPhase → Tournament` FK** (`SET_NULL`,
  `related_name="season_phases"`) — the column only, **ALWAYS NULL this slice**;
  the build / hand-off is Part2c. A **NEW pure module**
  `matches/phase_composer.py` (frozen `dataclasses` / `typing` allowlist,
  `TestNoDjangoImportsLeaked`-defended) parses the composer's comma-separated
  phase-type wire format into ordered `PhaseSpec(ordinal, phase_type,
  schedule_format)` via `parse_phase_composition(raw, *,
  season_schedule_format)` — empty input ⇒ a single RR default, ≥ 1 RR required,
  `member_night` not selectable, three exact `ValueError` strings. The
  `CreateLeagueForm` gained a hidden `phases` field whose `clean()` calls the
  parser and stashes `cleaned_data["phase_specs"]`; both creation sites loop over
  the specs — `league_create` (~553) from the composer, `next_season` (~1942)
  by **carrying the previous Season's composition forward** verbatim (with
  `tournament=None`). Migration `0042_seasonphase_format_tournament` (dep
  `0041_season_phase`, two `AddField`, no `RunPython` —
  [ADR-0004](docs/adr/0004-simulation-data-is-disposable.md)).
  `SeasonPhaseAdmin.list_display` extended with the two new columns. **Read-path
  UNCHANGED** — the Part2a chokepoint still plays the **first `round_robin`
  phase** via `Season.schedule_format` and ignores the rest; **no simulator
  change / no RNG / no Score Calibration re-baseline**. Seam contract:
  [`.claude/worktrees/lg-02-part2b-seam-contract.md`](.claude/worktrees/lg-02-part2b-seam-contract.md);
  app guide: `matches/CLAUDE.md` "LG-02-Part2b create-league phase composer";
  [ADR-0023](docs/adr/0023-season-phase-composable-structure.md). Tests:
  `matches/tests/test_phase_composer.py` (NEW) + extensions to
  `test_season_phase.py` / `test_league_create.py` / `test_league_next_season.py`.

- **LG-02-Part2c-1 · [DONE] RR → single-elimination playoff embed.** The first
  slice of Part2c — a thin orchestration layer that takes a Season composed of an
  ordered `round_robin` phase then a `tournament` phase, plays the regular season,
  **auto-builds** a standings-seeded single-elimination playoff bracket the moment
  the RR phase completes (matchups visible **before** any playoff click), then
  drains the bracket to crown the **Season champion**. Replaces Part2b's
  "play the first `round_robin` phase only" read-path with a **phase cursor** +
  two **lifecycle hooks** on `Season`. **Cursor / completion:**
  `Season.current_phase() -> SeasonPhase | None` returns the first INCOMPLETE
  phase by ordinal (`None` when all complete); completion is **derived**, not
  stored (no `SeasonPhase.state`) via the private `Season._phase_complete(phase)`
  — RR ⇔ the existing `_is_finished()` all-fixtures-played check, tournament ⇔
  `phase.tournament_id is not None AND phase.tournament.state == "completed"`.
  **Auto-build:** `Season.activate_pending_tournament_phase()`
  (`@transaction.atomic`, idempotent) fires when the cursor reaches an unbuilt
  `tournament` phase whose preceding RR phase is complete — it creates a
  `Tournament(format="single_elimination", team_assembly="preset", state="setup",
  name=f"{season.name} Playoffs")`, seeds **one `TournamentParticipant` per season
  team from the preceding phase's Standings** (`seed = StandingsRow.rank`, rank 1 →
  seed 1), wires `phase.tournament`, and calls `tournament.lock_and_build()`
  (setup → active; bracket built). **Completion rewrite:**
  `Season.complete_if_finished()` (REWRITTEN, `@transaction.atomic`) now gates on
  the **FINAL phase** (last ordinal) being complete and stamps the champion from
  that phase's type — `phase.tournament.champion` for a tournament final,
  `compute_standings(...)[0]` for an RR final (via
  `_stamp_champion_for_final_phase`, which supersedes the removed `_stamp_champion`);
  a single-RR-phase Season (and the implicit phase-less fallback) stays
  **byte-identical** to today. **Post-round hook:**
  `simulate_scheduled_round` calls `season.activate_pending_tournament_phase()`
  **then** `season.complete_if_finished()` after persistence in both the Round-1
  and Round-2 branches (ordering load-bearing — build before complete-check so the
  Season doesn't prematurely complete the instant the last RR fixture lands).
  **Play actions:** RR-scoped play (`play_week` / `play_two_months` /
  `play_until_end`) is behaviourally **UNCHANGED**; only the terminal play-dropdown
  label flips **"Until End of Season" → "Until Playoffs"** when a tournament phase
  follows (`has_following_tournament_phase`, label text only). Two NEW views drain
  the bracket: `play_single_round` (sync POST, one bracket node/Match via
  `play_next_node`, 302 redirect) and `play_playoffs` (async POST → 202 `{job_id,
  season_id}`, 409 / 405) backed by Celery task `play_playoffs_task`
  (`@shared_task(bind=True, name="matches.play_playoffs")`, returns
  `{"completed", "total"}` STAGE counts from `matches.bracket.stage_progress`,
  drains via `while play_next_node(...) is not None`); polling **reuses** the
  LG-01d `play_status` view / `_build_play_status_response` verbatim. **Compose
  guard:** `parse_phase_composition` gains one rule — a `tournament` phase requires
  a **preceding** `round_robin` phase (`ValueError("a tournament phase requires a
  preceding round-robin phase")`, fired after the zero-RR check). **Dashboard /
  template:** `_build_dashboard_context` gains four keys (`playoff_phase_active` /
  `playoff_tournament_id` / `playoff_completed` / `has_following_tournament_phase`)
  computed from `current_phase()`; both the season and league dashboards render a
  playoff button group (Play Single Round + Play Playoffs, only when
  `playoff_phase_active`) and a **"View bracket"** link to the existing
  `/tournaments/<id>/` page (when `playoff_tournament_id is not None`, do NOT embed
  the bracket). **Tournament Matches stay `season=NULL`** (the tournament engine is
  consumed verbatim — decision #3): **NO `Match.season_phase` FK, NO Match
  migration, no re-baseline, no simulator/engine change** this slice. Seam
  contract:
  [`.claude/worktrees/lg-02-part2c-1-seam-contract.md`](.claude/worktrees/lg-02-part2c-1-seam-contract.md);
  app guide: `matches/CLAUDE.md` "LG-02-Part2c-1 RR → single-elimination playoff
  embed"; [ADR-0023](docs/adr/0023-season-phase-composable-structure.md) (extended
  with a "Part2c-1 consequences" addendum). Tests: `matches/tests/test_season_playoff.py`
  (NEW) + extensions to `test_phase_composer.py` / `test_season_phase.py`.

- **LG-02-Part2c-2 · [DONE] Multi-RR play loop + `Match.season_phase` FK +
  cross-phase matchday offsetting (the Part2c SPINE).** Generalises the Part2c-1
  single-RR-then-single-elim path into a **multi-round-robin** Season: the
  supported + tested composition is **one-or-more `round_robin` phases then an
  OPTIONAL trailing `tournament`** (RR1→RR2, RR1→RR2→playoff). A thin orchestration
  slice — no simulator mechanics change, no tournament-engine change, no
  composer/form/template change, **no Score Calibration re-baseline**; legacy
  phase-less and single-RR Seasons stay **byte-identical**. **`Match.season_phase`
  FK + migration `0043`:** a new optional FK on `Match`
  (`models.ForeignKey("matches.SeasonPhase", null=True, blank=True,
  on_delete=models.SET_NULL, related_name="matches")`) mirroring `Match.season`;
  RR Matches now carry **both** `season=<season>` **and** `season_phase=<rr phase>`
  while tournament/playoff Matches (and legacy phase-less Seasons) stay
  `season_phase=NULL`. Migration `0043_match_season_phase` (dep
  `0042_seasonphase_format_tournament`) is a **single `AddField`, NO `RunPython` /
  NO backfill** ([ADR-0004](docs/adr/0004-simulation-data-is-disposable.md)
  posture). **By-phase fixture seam + global-continuous matchday offset:** NEW
  `Season.scheduled_fixtures_by_phase() -> list[tuple[SeasonPhase,
  list[ScheduleFixture]]]` offsets phase k's fixtures by the sum of all prior RR
  phases' matchday spans (one monotonic 1..N calendar);
  `Season.scheduled_fixtures()` is REWRITTEN as the flat concatenation of those
  offset fixtures (byte-identical for single-RR / phase-less). **Per-phase RR
  completion:** `Season._phase_complete` routes a *persisted* RR phase through a NEW
  `_rr_phase_complete` (scoped `match__season_phase=phase`) while the *implicit*
  `pk is None` fallback keeps the whole-season `_is_finished()` path — so the cursor
  finishes RR1 before RR2 opens; `_final_standings_for_phase` stays whole-season so
  **Standings are cumulative across RR phases** (a trailing playoff seeds from the
  cumulative leader). **Phase-aware find-or-create:** `simulate_scheduled_round`
  gains keyword-only `season_phase=None`; the Side-agnostic key becomes
  `(season, season_phase, frozenset({team ids}))` so identical pairings in different
  RR phases are distinct Matches (post-round hooks UNCHANGED). **Phase-aware
  Django-free helpers:** `select_play_fixtures` / `find_next_matchday` carry
  `(phase_id, fixture)` pairs + 3-tuple `(phase_id, frozenset, round_number)` keys
  via PLAIN INT phase-ids (`TestNoDjangoImportsLeaked` still passes);
  `find_next_fixture` / `round_progress` stay on the flat 2-tuple dashboard shape.
  **Play-loop wiring:** `play_season_task` (`matches/tasks.py`) and `play_week`
  (`matches/league_views.py`) iterate by-phase, build phase-aware `played_keys`, and
  pass `season_phase=phase_by_id.get(phase_id)`; `play_two_months` /
  `play_until_end` UNCHANGED. **Composer UNCHANGED** (`parse_phase_composition`
  already permits multiple `round_robin` tokens; the Part2c-1
  tournament-must-follow-RR guard stays). Seam contract:
  [`.claude/worktrees/lg-02-part2c-2-seam-contract.md`](.claude/worktrees/lg-02-part2c-2-seam-contract.md);
  app guide: `matches/CLAUDE.md` "LG-02-Part2c-2 multi-round-robin season";
  [ADR-0023](docs/adr/0023-season-phase-composable-structure.md) (extended with a
  "Part2c-2 consequences" addendum). CONTEXT.md **Matchday** / **Season phase**
  entries carry the behavioural touch-ups (no new domain term).

- **LG-02-Part2c-3a · [DONE] First alternative regular-season format —
  `double_round_robin` + `Match.leg` (Part2b `schedule_format` column wired
  end-to-end).** The first sub-slice of the re-sliced Part2c-3. Lands the **first
  alternative regular-season `schedule_format`** — **`double_round_robin`** — as a
  single `SeasonPhase` format, wiring the Part2b dormant per-phase `schedule_format`
  column **end-to-end** for the first time. A `double_round_robin` phase has every
  enrolled pair meet **twice within one phase** as **two distinct Matches**,
  discriminated by a NEW **`Match.leg`** field; `single_round_robin`, legacy
  phase-less Seasons, and all tournament Matches stay **`leg=1` ⇒ byte-identical**.
  A **thin orchestration slice** — no simulator mechanics change, no RNG change, no
  tournament-engine change, **no Score Calibration re-baseline**. **`Match.leg`
  field + migration `0044`:** `leg = models.PositiveSmallIntegerField(default=1)` on
  `Match` (after `season_phase`); migration `0044_match_leg` (dep
  `0043_match_season_phase`) is a **single `AddField`, NO `RunPython` / NO backfill**
  ([ADR-0004](docs/adr/0004-simulation-data-is-disposable.md) posture — existing rows
  take `default=1`). **Schedule generation:** `ScheduleFixture` gains a trailing
  `leg: int = 1` (appended LAST, keyword-constructed everywhere ⇒ equality-identical
  to existing constructions when defaulted); `SCHEDULE_FORMATS = ("single_round_robin",
  "double_round_robin")`; `generate_schedule(team_ids, "double_round_robin")` returns
  the single-RR fixture list (`leg=1`, matchdays `1..2*(n-1)`) **CONCATENATED** with
  the same fixtures re-emitted at **`leg=2`** with matchday **offset by `2*(n-1)`**
  (one monotonic `1..4*(n-1)` calendar, leg 2 sequentially after leg 1), final-sorted
  by `(matchday, team_a_id)`; the module stays Django-free, `single_round_robin`
  output byte-identical. **Phase-aware find-or-create:** `simulate_scheduled_round`
  gains keyword-only **`leg: int = 1`** (appended LAST) and the key becomes
  **`(season, season_phase, frozenset({team ids}), leg)`** so the two legs of a
  pairing are distinct Matches (post-round hooks UNCHANGED; `leg=1` collapses to
  today's key plus a constant). **Leg threading:** `_is_finished` /
  `_rr_phase_complete` played-keys + fixture-compare keys gain `leg` (a double-RR
  phase now requires **both** legs of every pairing before completing);
  `_final_standings_for_phase` UNCHANGED (cumulative — both legs are distinct Matches
  in the whole-season corpus); the Django-free pure helpers gain `leg`
  (`select_play_fixtures` / `find_next_matchday` → 4-tuple
  `(phase_id, frozenset, round_number, leg)`; FLAT `find_next_fixture` /
  `round_progress` → 3-tuple `(frozenset, round_number, leg)`, REQUIRED because a
  double-RR phase holds the same `(pair, round_number)` twice); the play-loop wiring
  (`play_season_task` / `play_week`) and the three FLAT overlay sites
  (`_build_dashboard_context` / `season_schedule` / `team_schedule`) build
  leg-bearing `played_keys` from `gr.match.leg` and pass `leg=fixture.leg`;
  `scheduled_fixtures_by_phase`'s offset re-construction carries `leg=f.leg` through.
  **Composer:** the per-token wire format extends from phase-**TYPE** tokens to
  **`type[:format]`** tokens (`"round_robin:double_round_robin,tournament"`); a bare
  `round_robin` defaults to `single_round_robin` (Part2b serialized values parse
  unchanged); `tournament` carries no format (`PhaseSpec.schedule_format=None`);
  `parse_phase_composition` reads the per-token format into `PhaseSpec.schedule_format`
  and raises a NEW `ValueError(f"unknown schedule_format: {fmt!r}")` for an
  unsupported format (existing `ValueError` strings preserved verbatim; `PhaseSpec`
  shape unchanged). The composer template gains a `double_round_robin` `<select>`
  option and serializes each RR row as `round_robin:<format>`; **all Part2b DOM ids
  unchanged**. **`next_season` is a NO-OP** (its Part2b carry-forward already copies
  each phase's `schedule_format` verbatim). **Backward-compat:** `single_round_robin`
  / legacy / tournament / playoff all stay `leg=1`, byte-identical; bare
  `round_robin` token ⇒ `single_round_robin`. **No re-baseline** — extend
  [ADR-0023](docs/adr/0023-season-phase-composable-structure.md) (Part2c-3a
  consequences addendum, no new ADR). **Scope-out (the c-3b…c-3f remainder below):**
  per-phase seeding-mode field; mid-season tournaments; per-tournament-block config;
  non-single-elim finals embeds; season-linked playoff Match history; weekly playoff
  pacing. Seam contract:
  [`.claude/worktrees/lg-02-part2c-3a-seam-contract.md`](.claude/worktrees/lg-02-part2c-3a-seam-contract.md);
  app guide: `matches/CLAUDE.md` "LG-02-Part2c-3a double round-robin regular-season
  format". Tests: extensions to `test_schedule_generator.py` / `test_phase_composer.py`
  / `test_league_play.py` / `test_season_multi_rr.py` / `test_league_create.py` /
  `test_season_dashboard_logic.py`.

- **LG-02-Part2c-3b · [DONE] Per-phase `tournament_mode` field on `SeasonPhase`
  (dormant).** Carried over from the LG-02-Part2b grill (2026-06-05). Part2b
  captures ordered phase *types* only; Part2c-1/Part2c-2/Part2c-3a hardcode
  standings-rank-seeded, season-ending. A `tournament` phase has **two flavours by
  Season role**: a **season-ending tournament** (playoff / closer) is **seeded from
  the preceding phase's Standings** and *requires* a preceding fixture-producing
  phase (the only flavour built so far); a **mid-season tournament** needs **no
  preceding Standings** — seeded by *expected team strength*, by a *random seed* of
  the preset teams, or drawn from a *player pool* — and may sit anywhere, including
  first. This slice lands the field that captures the distinction as a **fully
  dormant** addition (the `member_night` declared-but-inert precedent): a NEW
  **`SeasonPhase.tournament_mode`** `CharField(max_length=16, default="standings")`
  whose `TOURNAMENT_MODE_CHOICES` declares all four values now —
  **`standings`** (season-ending: from Standings), **`strength`** (mid-season: by
  team strength), **`unseeded`** (mid-season: random seed of the preset teams), and
  **`random_draw`** (mid-season: drawn pool → RR→DE, reusing the LG-02x-1
  `team_assembly="random_draw"` machinery). **`unseeded` ≠ `random_draw`** —
  unseeded randomly seeds the season's *existing preset teams*, random_draw builds
  *fresh balanced teams from a pool*. Migration `0045_seasonphase_tournament_mode`
  (dep `0044_match_leg`, single `AddField`, **no `RunPython` / no backfill** —
  [ADR-0004](docs/adr/0004-simulation-data-is-disposable.md); existing
  standings-playoff phases inherit `default="standings"`). The field is **threaded
  through the seam** but **always `"standings"` this slice**: the pure
  `PhaseSpec` (matches/phase_composer.py) gains a trailing
  **`tournament_mode: str = "standings"`** (appended LAST with a default ⇒ existing
  keyword constructions stay equality-identical, the c-3a `ScheduleFixture.leg`
  precedent) — but the **wire format is UNCHANGED** (the mode is **not** parsed
  from the wire; a `tournament:<mode>` token still raises `"malformed phase
  composition"`, reserving the `:` syntax for the c-3c picker); both
  `SeasonPhase`-creation sites (`league_create` / `next_season`) stamp
  `tournament_mode=spec.tournament_mode` / `=src.tournament_mode` so the
  carry-forward is **forward-compatible for c-3c** (a non-default mode set on a
  source phase reproduces across seasons). **Compose-time validity rule
  UNCHANGED** — the `standings`-requires-a-preceding-RR rule is already enforced
  for every `tournament` block by the existing blanket `parse_phase_composition`
  preceding-RR guard. **`activate_pending_tournament_phase` UNCHANGED** (still
  hardcodes standings-seeding; the default already matches, so byte-identical);
  read-path / simulator / RNG UNCHANGED, **no Score Calibration re-baseline**.
  `SeasonPhaseAdmin.list_display` gains `tournament_mode`. Extends
  [ADR-0023](docs/adr/0023-season-phase-composable-structure.md) (Part2c-3b
  consequences addendum, no new ADR); CONTEXT.md **Season phase** entry carries the
  `tournament_mode` vocabulary (+ the stale Part2c-2 → Part2c-3b fix). **Scope-out
  (→ c-3c):** the composer picker / `tournament:<mode>` wire token, the guard
  relaxation that lets a mid-season tournament sit anywhere, and the differential
  strength/unseeded/random_draw build. Seam contract:
  [`.claude/worktrees/lg-02-part2c-3b-seam-contract.md`](.claude/worktrees/lg-02-part2c-3b-seam-contract.md);
  app guide: `matches/CLAUDE.md` "LG-02-Part2c-3b per-phase tournament_mode field".
  Tests: extensions to `test_season_phase.py` / `test_phase_composer.py` /
  `test_league_create.py` / `test_league_next_season.py`.

- **LG-02-Part2c-3c · [DONE] Mid-season tournaments.** A `tournament` phase that
  sits **between two `round_robin` phases** (or first), not as the season closer —
  the mid-season flavour the c-3b seeding-mode field unlocks (no preceding
  Standings; may sit anywhere). Ships the **`strength`** + **`unseeded`** mid-season
  build (**`random_draw` still DEFERRED** — see the follow-up below); turns the c-3b
  dormant `tournament_mode` field live for those two modes. **Wire token:** the
  `tournament` composer token becomes **`tournament[:mode]`** (`parse_phase_composition`
  splits each token on the first `:`; for a `tournament` token the format-part is the
  **mode**, defaulting to `standings`) with a NEW locked
  `ValueError(f"unknown tournament_mode: {mode!r}")` for `random_draw` or any unknown
  string (every pre-existing `ValueError` string preserved verbatim). **Guard
  relaxation:** the **≥1-round-robin** rule is kept verbatim; the
  `"a tournament phase requires a preceding round-robin phase"` string is preserved
  but now fires **ONLY** for a `standings`-mode tournament — a `strength` / `unseeded`
  phase may sit **anywhere, including first**, and a mid-season `standings`
  tournament is allowed (there is no "standings-must-be-final" rule). **Build
  differential** (`Season.activate_pending_tournament_phase` generalises the gate; a
  NEW private `Season._seed_order_for_phase(phase) -> list[int]` branches on
  `tournament_mode`): `standings` → preceding-phase Standings rank order (byte-identical
  to today); `strength` → `bracket.default_seed_order([(tid, mean_overall_rating)])`
  over the season's starting teams (DESC mean, ASC id tiebreak); `unseeded` → a fresh
  `random.Random()` shuffle of the starting team ids (non-deterministic, NOT the
  SIM-07 seed chain). The **shared build tail is mode-independent** — `seed = position
  + 1` (byte-identical to today's `seed=row.rank` for `standings`), name
  `f"{self.name} Playoffs"` for `standings` else `f"{self.name} Tournament"`, then
  `lock_and_build()`. **Build trigger:** `Season.start_season` gains an
  `activate_pending_tournament_phase()` call **inside** its existing
  `@transaction.atomic` block (after the snapshot writes + `state="active"`), so a
  FIRST-phase mid-season tournament builds the instant the Season activates; the
  existing post-round hook still covers the mid-season-after-RR case (the method is
  idempotent). **Play-loop barrier:** a NEW
  `Season.playable_fixtures_by_phase()` (filters `scheduled_fixtures_by_phase()` to RR
  phases whose ordinal is strictly **less** than the first incomplete `tournament`
  phase's ordinal, via a NEW private `_tournament_barrier_ordinal()`) halts the RR
  loop at an incomplete mid-season tournament phase so the bracket — built by the hook
  — drains through the EXISTING `play_single_round` / `play_playoffs` views before
  later RR phases play; once the tournament completes the barrier advances and the
  later RR phases become playable. Two one-line play-loop swaps
  (`tasks.py::play_season_task`, `league_views.py::play_week`:
  `scheduled_fixtures_by_phase()` → `playable_fixtures_by_phase()`); `play_two_months`
  / `play_until_end` enqueue `play_season_task` UNCHANGED. **Dashboard label split:**
  the terminal play button reads **"Until Playoffs"** when the following tournament is
  the FINAL phase, **"Until Tournament"** when it is mid-season (a new
  `following_tournament_is_final` context bool computed alongside `_playoff_cursor_keys`,
  touching both `templates/seasons/dashboard.html` and `templates/leagues/dashboard.html`);
  the playoff button-group DOM ids + `play_until_end` action are UNCHANGED (visible
  label text only). **Composer:** a tournament composer row gains a mode `<select>`
  with locked DOM id **`league-create-phase-mode-{i}`** (options `standings` /
  `strength` / `unseeded`; `random_draw` a DISABLED "coming soon" — the `member_night`
  precedent), shown for `tournament` rows only, with `serialize()` emitting
  `tournament:<mode>`. **NO migration** (`tournament_mode` exists from c-3b);
  read-path purity preserved (`matches/season_dashboard.py` untouched —
  `TestNoDjangoImportsLeaked` stays green); simulator / RNG / tournament engine
  consumed verbatim, **no Score Calibration re-baseline**. Extends
  [ADR-0023](docs/adr/0023-season-phase-composable-structure.md) (Part2c-3c
  consequences addendum, no new ADR); the CONTEXT.md **Season phase** + **Matchday**
  entries carry the build-now / barrier-drain domain language. **Follow-up
  (deferred):** the mid-season **`random_draw`** build (drawn pool → RR→DE, reusing
  the LG-02x-1 `team_assembly="random_draw"` machinery) — the parser rejects it with a
  ValueError and the composer offers it as a disabled "coming soon" option only. Seam
  contract:
  [`.claude/worktrees/lg-02-part2c-3c-seam-contract.md`](.claude/worktrees/lg-02-part2c-3c-seam-contract.md);
  app guide: `matches/CLAUDE.md` "LG-02-Part2c-3c mid-season tournaments". Tests:
  extensions to `test_phase_composer.py` / `test_season_phase.py` /
  `test_season_playoff.py` / `test_league_create.py` / `test_season_dashboard_logic.py`.

- **LG-02-Part2c-3d · [NOT STARTED] Per-tournament-block configuration.** Format /
  `team_assembly` / seeding / top-N cut surfaced per `tournament` block when the
  multi-format build / hand-off is implemented — Part2b's composer places bare
  `tournament` blocks only, and Part2c-1/Part2c-2/Part2c-3a hardcode full-field
  single-elimination.

- **LG-02-Part2c-3e · [NOT STARTED] Non-single-elim finals embeds.** Double-elim /
  RR / Swiss / RR→DE as a Season finals stage — beyond the hardcoded
  single-elimination playoff Part2c-1 builds; needs the c-3d per-tournament-block
  config to select the embedded format.

- **LG-02-Part2c-3f · [NOT STARTED] Season-linked playoff Match history + weekly
  playoff pacing.** A **season-linked playoff Match-history surface** (a Season
  game-log surface for playoff Matches — playoff Matches still carry
  `season=NULL, season_phase=NULL` after Part2c-2/Part2c-3a, so this needs its own
  wiring); and **weekly playoff pacing** (a per-week tournament cadence — today one
  Match plays per "Play Single Round" click or the whole bracket per "Play
  Playoffs"). The `member_night` phase type stays inert (see its own PLAN item
  below).

  **(Deferred — own slice, post-Part2c-3)** A pre-selected per-League option to
  **randomize the mid-season tournaments per season**: the non-season-ending
  `tournament` phases that sit before the main `round_robin` + the end-of-year
  tournament are re-drawn (format / seeding) each cycle by `next_season` instead of
  carried forward verbatim. Selected beforehand as a League-level toggle; only
  meaningful once the seeding-mode field + per-tournament-block config above exist.

### LG-03 · Season-end awards

Computed from `PlayerRoundState` aggregates: Most Points, Highest K/D by role, Best Medic, 
Most Efficient Nuke, Best Accuracy. Awards page at `/seasons/<id>/awards/`. Award badge on player profile.

Also surface the headline **season MVP** (and, once LG-02 playoffs land, a
**Finals MVP**) on the **League History** table (LG-01f) — the reference product
puts both in its history row next to Champion / Runner-up, and ours currently has
no awards column. See
[`docs/zengm-comparison/season-lifecycle.md`](docs/zengm-comparison/season-lifecycle.md).

### LG-04 · Season-end stat updates

At the end of each season, all players (on active teams or otherwise) receive a stat update.
The update factors in:
- **New experience** — games played this season
- **Player age** — older players improve more slowly
- **Prior experience** — players with more historical games have a smaller update magnitude

Default weights for these three factors are fixed in code but overridable per season by the league admin.

### LG-05 · Player potential

Each player carries a `potential` attribute: a dynamically computed estimate of their likely stat ceiling.

- Computed at each season-end stat update, not on demand.
- Derived from current player stats + the team's seasonal scouting budget allocation.
- **Scouting budget** is a per-season allocation on the team. Higher budget = more accurate `potential`
  estimate. Lower budget = noisier estimate with added randomness.
- `potential` has a floor of `overall_rating` — it can never predict a player will regress below
  their current average.
- `potential` is not exposed in the UI until this phase is complete.

### INFRA-01 · Migrate from SQLite to PostgreSQL

**Status: NOT STARTED.** Motivated by recurring `OperationalError: database is
locked` errors during long "Play Until End of Season" runs — SQLite is a
single-writer database and the app now has genuinely concurrent writers (the
Celery "Play …" tasks run a loop of per-Round write transactions while the
dashboard polls `play_status`). A WAL + busy-timeout mitigation
([commit on `lg-fix-sqlite-database-locked`]) buys headroom, but the durable
fix is a concurrent-writer database.

- **Why now-ish:** the Celery/Redis async executor (ADR-0013) and the per-Round
  commit play loop (ADR-0016) made background-writer-vs-web-request contention a
  permanent part of the architecture. SQLite's single-writer lock will keep
  surfacing under load (long seasons, multiple leagues, future multiplayer).
- **Low-friction switch:** the app already reads its DB config from
  `DATABASE_URL` via `dj_database_url` (`settings.py`), so the runtime change is
  a single env var (`DATABASE_URL=postgres://…`). The work is in provisioning +
  parity, not code.
- **Scope:**
  - Add `psycopg[binary]>=3.1` (or `psycopg2-binary`) to `requirements.txt`.
  - Provision a local Postgres for dev (Docker Compose service) and a hosted
    instance for deploy; document the `DATABASE_URL` for each.
  - Make the SQLite-only hardening conditional, not load-bearing: the
    `OPTIONS` block in `settings.py` (`timeout` / `transaction_mode`) and the
    `core/db_pragmas.py` WAL `connection_created` hook are already guarded on
    `ENGINE == sqlite3` / `connection.vendor == "sqlite"`, so they no-op on
    Postgres — verify this holds after the switch (no Postgres should ever run
    the SQLite PRAGMAs).
  - Audit for SQLite-specific assumptions: JSONField behaviour (Postgres has
    native `jsonb` — generally an upgrade), any raw SQL, case-sensitivity of
    text lookups (Postgres is case-sensitive by default where SQLite was not),
    and `dbshell`/management-command docs in `CLAUDE.md`.
  - CI: decide whether to run the suite against Postgres (closer to prod) or
    keep SQLite for test speed; the conftest already forces
    `CELERY_TASK_ALWAYS_EAGER`, so no broker is needed either way.
  - Data: dev/test data is disposable (ADR-0004), so **no migration of existing
    rows** — a fresh `migrate` on Postgres is sufficient. Only production data
    (if any exists by then) would need a `dumpdata`/`loaddata` or `pg`-level
    transfer plan.
- **Done when:** the app runs end-to-end on Postgres locally and in deploy, the
  full `pytest` suite passes, and a full "Play Until End of Season" run on a
  multi-team league completes with no lock errors and no SQLite PRAGMAs executed.

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
