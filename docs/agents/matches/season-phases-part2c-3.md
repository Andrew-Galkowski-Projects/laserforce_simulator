# matches: Season phases (Part2c-3)

Double round robin, tournament_mode, mid-season tournaments, per-tournament config, non-single-elim finals, playoff history + weekly pacing.
Moved verbatim from `laserforce_simulator/matches/CLAUDE.md`; the app index there points here.

## LG-02-Part2c-3a double round-robin regular-season format

The first sub-slice of the re-sliced LG-02-Part2c-3 — a **thin orchestration
layer** that lands the **first alternative regular-season `schedule_format`**,
**`double_round_robin`**, as a single `SeasonPhase` format, wiring the Part2b
**dormant per-phase `schedule_format` column end-to-end** for the first time. A
`double_round_robin` phase has every enrolled pair meet **twice within one phase**
as **two distinct Matches**, discriminated by a NEW `Match.leg` field;
`generate_schedule` gains the format, `simulate_scheduled_round` gains a `leg`
find-or-create dimension, and `leg` threads through per-phase RR completion / the
play loops / the Django-free pure helpers / the FLAT dashboard overlays. The
simulator, RNG, and tournament engine are consumed **VERBATIM**; legacy
phase-less, single-RR, and tournament/playoff Matches stay **`leg=1` ⇒
byte-identical**. **No simulator mechanics change, no RNG change, no
tournament-engine change, no Score Calibration re-baseline.** The
[ADR-0023](../../adr/0023-season-phase-composable-structure.md) decision
(extended with a "Part2c-3a consequences" addendum, no new ADR) and the
**Matchday** / **Season phase** GLOSSARY.md glossary touch-ups carry the domain
language (`double_round_robin` is a `schedule_format` value, not a new domain
term). Seam contract:
[`.claude/worktrees/lg-02-part2c-3a-seam-contract.md`](../../../.claude/worktrees/lg-02-part2c-3a-seam-contract.md).

**`Match.leg` field + migration `0044` (`matches/models.py`).** A new
`leg = models.PositiveSmallIntegerField(default=1)` on `Match`, declared
immediately after `Match.season_phase` (no `db_index`, no choices, no
`null`/`blank`). It **discriminates the two legs of a `double_round_robin`
pairing**; `single_round_robin`, legacy phase-less, and tournament/playoff Matches
stay `leg=1` (the default) ⇒ byte-identical. Migration
`matches/migrations/0044_match_leg.py` (dep `0043_match_season_phase`) is a
**single `AddField`** — **NO `RunPython`, NO backfill, NO data migration** (the
[ADR-0004](../../adr/0004-simulation-data-is-disposable.md) disposable-data
posture, same as `0029` / `0041` / `0042` / `0043`; existing rows take
`default=1`). `leg` is the load-bearing reason this slice exists: it makes the
find-or-create key distinguish the same pairing's two legs as DISTINCT Matches.

**Schedule generation (`matches/schedule_generator.py`).** The module stays
**Django-free** (frozen `dataclasses` / `typing` allowlist; no new import).
`ScheduleFixture` gains a trailing **`leg: int = 1`** (appended LAST, default `1`,
constructed by keyword everywhere) ⇒ a no-`leg` construction is equality-identical
to every existing test construction and to the Part2c-2 offset re-construction
once it carries `leg=f.leg`. `SCHEDULE_FORMATS = ("single_round_robin",
"double_round_robin")`. `generate_schedule(team_ids, "double_round_robin")`
returns the `single_round_robin` fixture list for `team_ids` (every fixture
`leg=1`, the existing circle-method output — round_number 1 on matchdays
`1..n-1`, round_number 2 on `n..2*(n-1)`) **CONCATENATED** with the **same**
fixtures re-emitted with **`leg=2`** and matchday **offset by `2*(n-1)`** (same
`round_number` / `team_a_id` / `team_b_id`), then final-**sorted by
`(matchday, team_a_id)`** (the same key the single-RR path uses; leg is implied by
matchday since the two legs occupy disjoint contiguous matchday ranges). So leg 2
plays **sequentially after** leg 1 on one monotonic `1..4*(n-1)` matchday calendar
within the phase (`n` = the existing bye-padded even slot count `len(slots)`, so
the offset is the existing `2 * (n - 1)`). The `single_round_robin` path is
**byte-identical** (its fixtures already carry the `leg=1` default); the existing
`ValueError` on `schedule_format not in SCHEDULE_FORMATS` and on
`len(team_ids) < 2` is unchanged.

**Phase-aware find-or-create key (`matches/simulation/entrypoints.py`).**
`simulate_scheduled_round` gains a **keyword-only `leg: int = 1`** appended after
`season_phase=None` (`def simulate_scheduled_round(self, season, team_a, team_b,
round_number, *, arena_map=None, season_phase=None, leg: int = 1) -> GameRound`).
**Default `1` = byte-identical to every existing caller** (sandbox, season play,
tests passing no `leg`). The Side-agnostic find-or-create key becomes
**`(season, season_phase, frozenset({team_a_id, team_b_id}), leg)`** — both
`.filter(...)` lookups gain `leg=leg` and the Round-1 `Match.objects.create(...)`
stamps `leg=leg`. So the two legs of a pairing become DISTINCT Matches; `leg=1`
collapses the key to `(season, season_phase, frozenset, 1)` — byte-identical to
the Part2c-2 `(season, season_phase, frozenset)` plus a constant. The
`season_phase.pk is None ⇒ None` coercion, the round_number guards, the per-round
colour swap, the post-round hooks (`activate_pending_tournament_phase()` **then**
`complete_if_finished()`), and the RNG draw are **UNCHANGED**.

**Leg threading on `Season` (`matches/models.py`).** **`_is_finished`**
(whole-season RR check, legacy/implicit-phase path) — its played-keys set gains
`leg` from `gr.match.leg` (`(frozenset({team ids}), round_number, leg)`) and the
per-fixture compare key gains `fixture.leg` (for a phase-less / single-RR Season
every `leg == 1` ⇒ byte-identical). **`_rr_phase_complete`** (per-phase RR
completion, `match__season_phase=phase` scoped) — same change: played-keys become
`(frozenset({team ids}), round_number, gr.match.leg)` and the fixture compare key
`(frozenset({fixture.team_a_id, fixture.team_b_id}), fixture.round_number,
fixture.leg)` — this is what makes a double-RR phase require **both** legs of every
pairing before the phase completes. **`_final_standings_for_phase` is UNCHANGED** —
standings stay cumulative whole-season; a double-RR pairing is two distinct Matches,
each a row in `Match.objects.filter(season=self, is_completed=True)`, so both legs
count automatically. `scheduled_fixtures_by_phase` / `_fixtures_for_phase` /
`scheduled_fixtures` need no leg-specific edit **beyond carrying `leg=f.leg`
through the offset re-construction** (else leg-2 fixtures would collapse to leg-1
and break the key + completion).

**Leg on the Django-free pure helpers (`matches/season_dashboard.py`).** The
module stays **Django-free** (`leg` is a plain int read off the duck-typed fixture;
no new import; `TestNoDjangoImportsLeaked` must keep passing). The play-loop
helpers `select_play_fixtures` / `find_next_matchday` gain `leg` in their
per-fixture key: **OLD** `(phase_id, frozenset({team ids}), round_number)` →
**NEW** `(phase_id, frozenset({team ids}), round_number, leg)` (`leg = fixture.leg`);
`played_keys` becomes `set[tuple[int | None, frozenset[int], int, int]]`, the
`fixtures` arg shape `list[tuple[int | None, ScheduleFixture]]` is UNCHANGED (the
`ScheduleFixture` now carries `.leg`). The FLAT dashboard helpers `find_next_fixture`
/ `round_progress` gain `leg` in their 2-tuple key: **OLD**
`(frozenset({team ids}), round_number)` → **NEW** `(frozenset({team ids}),
round_number, leg)` — **REQUIRED** because a double-RR phase now holds the same
`(pair, round_number)` **twice** in the flat list, so without `leg` the second leg
would be treated as already-played. `compute_leaders` / `LeaderRow` UNCHANGED. (See
the §3 key-tuple table in the seam contract for all OLD → NEW shapes.)

**Play-loop wiring + FLAT overlay sites (`matches/tasks.py`,
`matches/league_views.py`).** **`play_season_task`** (`tasks.py`) and **`play_week`**
(`league_views.py`) build leg-bearing `played_keys` entries — `(gr.match.season_phase_id,
frozenset({gr.match.team_red_id, gr.match.team_blue_id}), gr.round_number,
gr.match.leg)` — and pass **`leg=fixture.leg`** into `simulate_scheduled_round`;
`play_two_months` / `play_until_end` are **UNCHANGED** (they enqueue
`play_season_task`; the `max_matchdays` window carries through). The three FLAT
2-tuple overlay sites that feed `find_next_fixture` / `round_progress` each gain
`leg` (reading `match.leg` off the `GameRound.match` already in scope /
`select_related("match")`): **`_build_dashboard_context`** (`played_keys` entries
+ fixture compare key), **`season_schedule`** (`played_by_key` dict key + per-fixture
lookup), **`team_schedule`** (`played_keys` set + `fixture_by_key` dict +
per-fixture/per-round lookup). Each becomes `(..., round_number, leg)` so a
double-RR phase's two legs are distinct.

**Composer (`matches/phase_composer.py`) — per-token `type[:format]` wire format.**
The wire format **extends** from comma-separated phase-**TYPE** tokens to
comma-separated **`type[:format]`** tokens, e.g.
`"round_robin:double_round_robin,tournament"`. A **bare `round_robin`** token (no
colon) defaults to **`single_round_robin`** (Part2b backward-compat — existing
serialized values parse identically); a **`tournament`** token carries **no format**
⇒ `PhaseSpec.schedule_format=None` (a `tournament:anything` token is malformed).
`parse_phase_composition` splits each non-empty token on the **first** `:` only
into `(type_part, format_part)`, rejects an empty `type_part` (malformed),
validates `type_part` against `{round_robin, tournament}` (unknown-type
`ValueError`), and for `round_robin` resolves `schedule_format = format_part or
"single_round_robin"` then validates it against the valid set
`{"single_round_robin", "double_round_robin"}`, raising a **NEW pure
`ValueError(f"unknown schedule_format: {fmt!r}")`** (LOCKED string; module stays
Django-free; the form re-wraps as a `forms.ValidationError` on `phases`). All
existing `ValueError` strings are **preserved verbatim** (`"malformed phase
composition"`, `f"unknown phase type: {token!r}"`, `"composition must contain at
least one round-robin phase"`, `"a tournament phase requires a preceding
round-robin phase"`); the `PhaseSpec` shape (`ordinal, phase_type,
schedule_format`) is **UNCHANGED**. The form's `clean()` call site is unchanged
(per-token format wins; `season_schedule_format` stays the fallback for a bare
`round_robin` token, semantics still "bare ⇒ single_round_robin"). **Template
(`templates/leagues/create.html`):** the per-phase `<select>` gains a
`double_round_robin` option and `serialize()` emits each RR row as
`round_robin:<format>` (a `tournament` row emits the bare token); **all Part2b DOM
ids are UNCHANGED** (`league-create-phases-composer`, `league-create-add-block`,
`league-create-phases`, `league-create-phase-row-{i}`,
`league-create-phase-type-{i}`, `league-create-phase-format-{i}`,
`league-create-member-night-note`, class substring `phase-tournament-pending`).

**`next_season` carry-forward — NO-OP.** `next_season`
(`matches/league_views.py`) already copies each source phase's `schedule_format`
**verbatim** into the new draft Season's phases (the Part2b carry-forward loop), so
a `double_round_robin` phase reproduces automatically with **no edit**. Admin is
also unchanged (`SeasonPhaseAdmin.list_display` already includes `schedule_format`;
`Match.leg` auto-appears on the default change form, not load-bearing).

**Backward-compat invariants ("stays byte-identical").** `single_round_robin` —
every fixture `leg=1`, `generate_schedule(..., "single_round_robin")` output
identical to today. Legacy / phase-less Season — find-or-create key
`(season, None, frozenset, 1)` collapses to today's `(season, None, frozenset)`
plus a constant; `_is_finished` played-keys all `leg=1`. Tournament / playoff
Matches — `simulate_match` never sets `leg` ⇒ default `1`; `season=NULL,
season_phase=NULL` unchanged. Bare `round_robin` wire token ⇒ `single_round_robin`.
`ScheduleFixture(...)` without `leg` ⇒ `leg == 1`.

**Determinism.** RR sims are byte-identical per Round (no mechanics change). Tests
assert schema-level outcomes — Match counts, `leg` values, completion flags,
standings ORDER, the generated fixture list (count / matchday spans / leg values /
sort) — **NEVER** raw simulated point totals. **No re-baseline / extend ADR-0023**
(no new ADR).

**Locked names (quick index):**
- **Model + migration (`matches/models.py`):** `Match.leg =
  models.PositiveSmallIntegerField(default=1)` (after `Match.season_phase`);
  migration `matches/migrations/0044_match_leg.py` (dep `0043_match_season_phase`,
  single `AddField`, NO `RunPython`).
- **Schedule generator (`matches/schedule_generator.py`):** `ScheduleFixture.leg:
  int = 1` (trailing); `SCHEDULE_FORMATS = ("single_round_robin",
  "double_round_robin")`; `generate_schedule(team_ids, "double_round_robin")` =
  leg-1 list ⊕ leg-2 list offset by `2*(n-1)`, sorted `(matchday, team_a_id)`,
  monotonic `1..4*(n-1)`.
- **Find-or-create key (`matches/simulation/entrypoints.py`):**
  `(season, season_phase, frozenset({team_a_id, team_b_id}), leg)`; signature gains
  keyword-only `leg: int = 1` (`simulate_scheduled_round(self, season, team_a,
  team_b, round_number, *, arena_map=None, season_phase=None, leg: int = 1) ->
  GameRound`); post-round hooks UNCHANGED.
- **`Season` methods (`matches/models.py`):** `_is_finished` /
  `_rr_phase_complete` played-keys + fixture-compare keys gain `leg`
  (`gr.match.leg` / `fixture.leg`); `_final_standings_for_phase` UNCHANGED
  (cumulative); `scheduled_fixtures_by_phase` offset re-construction carries
  `leg=f.leg`.
- **Pure helpers (`matches/season_dashboard.py`) — Django-free:**
  `select_play_fixtures` / `find_next_matchday` key → 4-tuple `(phase_id,
  frozenset, round_number, leg)`, `played_keys: set[tuple[int | None,
  frozenset[int], int, int]]`; `find_next_fixture` / `round_progress` key →
  3-tuple `(frozenset, round_number, leg)`. UNCHANGED: `compute_leaders`,
  `LeaderRow`. `TestNoDjangoImportsLeaked` must keep passing.
- **Play-loop + FLAT overlay sites:** `tasks.play_season_task` /
  `league_views.play_week` (leg-bearing `played_keys`, `leg=fixture.leg`);
  `league_views._build_dashboard_context` / `season_schedule` / `team_schedule`
  (FLAT `played` keys gain `leg` from `gr.match.leg` / `fixture.leg`).
  `play_two_months` / `play_until_end` UNCHANGED.
- **Composer (`matches/phase_composer.py`):** per-token `type[:format]` wire
  format; bare `round_robin` ⇒ `single_round_robin`; `tournament` ⇒
  `schedule_format=None`; NEW `ValueError(f"unknown schedule_format: {fmt!r}")`;
  `PhaseSpec` shape unchanged. Template `templates/leagues/create.html` gains a
  `double_round_robin` option + `round_robin:<format>` serialize; **all Part2b DOM
  ids unchanged**.
- **No-op / no-change:** `next_season` (carries `schedule_format` forward
  verbatim), `SeasonPhaseAdmin`, `_final_standings_for_phase`, the simulator / RNG
  / tournament engine.
- **ADR:** extend [ADR-0023](../../adr/0023-season-phase-composable-structure.md)
  with a "Part2c-3a consequences" addendum (no new ADR). **No new GLOSSARY.md domain
  term** (`double_round_robin` is a `schedule_format` value), no re-baseline.

See the seam contract
[`.claude/worktrees/lg-02-part2c-3a-seam-contract.md`](../../../.claude/worktrees/lg-02-part2c-3a-seam-contract.md)
for the authoritative names + behaviours.

## LG-02-Part2c-3b per-phase tournament_mode field

A **fully dormant** slice that adds the per-phase **`SeasonPhase.tournament_mode`**
field — the season-ending (`standings`) vs mid-season (`strength` / `unseeded` /
`random_draw`) flavour selector — and threads it through the compose/creation/
carry-forward seam **always as `"standings"` this slice**. No composer picker, no
wire-format mode token, no compose-guard relaxation, no build branch — all
deferred to Part2c-3c. No simulator / RNG change → **no Score Calibration
re-baseline**. Extends
[ADR-0023](../../adr/0023-season-phase-composable-structure.md) (Part2c-3b
consequences addendum, no new ADR); the GLOSSARY.md **Season phase** entry carries
the `tournament_mode` vocabulary (+ the stale Part2c-2 → Part2c-3b fix). Seam
contract:
[`.claude/worktrees/lg-02-part2c-3b-seam-contract.md`](../../../.claude/worktrees/lg-02-part2c-3b-seam-contract.md).

**Model (`matches/models.py`, `SeasonPhase`).** Class attr
**`TOURNAMENT_MODE_CHOICES`** declares all four values now (the `member_night`
declared-but-inert precedent — only `standings` has build behaviour this slice):
`("standings", "Season-ending: from Standings")`, `("strength", "Mid-season: by
team strength")`, `("unseeded", "Mid-season: random seed")`, `("random_draw",
"Mid-season: drawn pool -> RR->DE")`. The field
**`tournament_mode = models.CharField(max_length=16, choices=TOURNAMENT_MODE_CHOICES,
default="standings")`** is declared immediately after the `tournament` FK — no
constraint, no `db_index`. Meaningful only for `tournament` phases (`round_robin`
phases carry the inert default). **`unseeded` ≠ `random_draw`**: `unseeded`
randomly seeds the season's *existing preset teams*; `random_draw` builds *fresh
balanced teams from a player pool* (reuses the LG-02x-1 `team_assembly="random_draw"`
+ `format="round_robin_double_elim"` machinery; *ANG-01f renamed that `team_assembly` value
`"player_pool"` — `tournament_mode="random_draw"` itself is unchanged*). **Only `standings` requires a
preceding `round_robin` phase** — already enforced for every `tournament` block by
the existing blanket `parse_phase_composition` preceding-RR guard (the compose-time
validity rule is UNCHANGED this slice).

**Migration `0045_seasonphase_tournament_mode`** (dep `0044_match_leg`): a single
`AddField`, **NO `RunPython` / NO backfill** ([ADR-0004](../../adr/0004-simulation-data-is-disposable.md)
posture — existing standings-playoff phases inherit `default="standings"`).

**Pure module (`matches/phase_composer.py`).** `PhaseSpec` gains a trailing
**`tournament_mode: str = "standings"`** (appended LAST with a default ⇒ existing
keyword constructions stay equality-identical, the c-3a `ScheduleFixture.leg`
precedent). **Wire format UNCHANGED** — `tournament_mode` is **not** parsed from
the wire this slice; `parse_phase_composition` leaves every spec at the
`"standings"` default and a `tournament:<mode>` token still raises
`"malformed phase composition"` (the existing tournament-takes-no-format rule),
reserving the `:` syntax for the c-3c picker. The frozen import allowlist
(`dataclasses` / `typing`) + `TestNoDjangoImportsLeaked` are unchanged.

**Creation / carry-forward (`matches/league_views.py`).** Both
`SeasonPhase.objects.create(...)` loops stamp the field — `league_create` adds
`tournament_mode=spec.tournament_mode` and `next_season` adds
`tournament_mode=src.tournament_mode` (verbatim carry-forward, mirroring the
`schedule_format` copy) so a future non-`standings` mode (c-3c) reproduces across
seasons. Both edits sit inside the existing `@transaction.atomic` blocks.

**Admin (`matches/admin.py`).** `SeasonPhaseAdmin.list_display` appends
`"tournament_mode"`.

**UNCHANGED (→ c-3c).** `Season.activate_pending_tournament_phase` still hardcodes
standings-seeding (the default already matches, so byte-identical); the composer
template / wire format / guard; the read-path, simulator, RNG, and `Match` model.
**No re-baseline.**

**Tests.** `test_season_phase.py` (field default / choices / `max_length==16` /
all-four-persist), `test_phase_composer.py` (`PhaseSpec` default + parser stamps
`"standings"` + `tournament:<mode>` still malformed + purity), `test_league_create.py`
(every composed phase persists `"standings"`), `test_league_next_season.py`
(**hand-set a source phase's `tournament_mode="strength"` via ORM, assert the
carry-forward preserves it** — the load-bearing forward-compat guard for c-3c).

## LG-02-Part2c-3c mid-season tournaments

The slice that makes a `tournament` **Season phase** sitting **mid-season** (between
two `round_robin` phases, or **first**) actually BUILD — turning the c-3b dormant
`tournament_mode` field live for **`strength`** + **`unseeded`** (`random_draw` stays
**DEFERRED**, rejected by the parser + offered as a disabled "coming soon" composer
option). It relaxes the standings-only flavour of the Part2c-1 preceding-RR compose
guard, branches the build by seeding mode, fires the build at `start_season` for a
first-phase tournament, and adds a play-loop **barrier** so the RR loop halts at an
incomplete mid-season tournament and the bracket drains through the EXISTING
`play_single_round` / `play_playoffs` views before later RR phases play. **NO
migration** (`tournament_mode` exists from c-3b). The simulator / RNG / tournament
engine are consumed **VERBATIM**; the Django-free pure helpers + `scheduled_fixtures*`
+ `matches/season_dashboard.py` are **UNTOUCHED** (`TestNoDjangoImportsLeaked` stays
green) → **no Score Calibration re-baseline**. Extends
[ADR-0023](../../adr/0023-season-phase-composable-structure.md) (Part2c-3c
consequences addendum, no new ADR); the GLOSSARY.md **Season phase** + **Matchday**
glossary touch-ups carry the build-now / barrier-drain domain language. Seam contract:
[`.claude/worktrees/lg-02-part2c-3c-seam-contract.md`](../../../.claude/worktrees/lg-02-part2c-3c-seam-contract.md).

**Locked names (NEW / CHANGED).**
- `Season._seed_order_for_phase(self, phase) -> list[int]` (`matches/models.py`) —
  **NEW private**; the mode-branching seed-vector builder (see below).
- `Season.activate_pending_tournament_phase(self) -> None` `@transaction.atomic`
  (`matches/models.py`) — **CHANGED**: generalised gate (prior-is-None now permitted
  for a non-`standings` first phase) + mode branch via `_seed_order_for_phase`.
- `Season.start_season(self) -> None` `@transaction.atomic` (`matches/models.py`) —
  **CHANGED**: adds an `activate_pending_tournament_phase()` call INSIDE the atomic
  block, after the snapshot writes + `state="active"` + `save()`.
- `Season._tournament_barrier_ordinal(self) -> int | None` (`matches/models.py`) —
  **NEW private** (may inline): the ordinal of the first incomplete `tournament` phase,
  else `None`.
- `Season.playable_fixtures_by_phase(self) -> list[tuple[SeasonPhase, list[ScheduleFixture]]]`
  (`matches/models.py`) — **NEW**: `scheduled_fixtures_by_phase()` filtered to RR phases
  whose `ordinal < barrier`.
- `parse_phase_composition(raw, *, season_schedule_format) -> list[PhaseSpec]`
  (`matches/phase_composer.py`) — **CHANGED**: parses the `tournament[:mode]` token.
- `play_season_task` (`matches/tasks.py`) / `play_week` (`matches/league_views.py`) —
  **CHANGED**: one-line swap `scheduled_fixtures_by_phase()` →
  `playable_fixtures_by_phase()`.
- `_build_dashboard_context` / `_playoff_cursor_keys` (`matches/league_views.py`) —
  **CHANGED**: add the `following_tournament_is_final: bool` context for the terminal
  label split.

**Wire format + ValueError (`matches/phase_composer.py`).** The `tournament` token
becomes **`tournament[:mode]`** — each token splits on the FIRST `:` into
`(type_part, format_part)`; for a `tournament` token `format_part` is the **MODE**
(versus an RR token where it is the schedule format). Bare `tournament` ⇒
`tournament_mode="standings"`. Valid modes this slice: `standings`, `strength`,
`unseeded`. `random_draw` **AND any unknown string** ⇒ NEW locked
`ValueError(f"unknown tournament_mode: {mode!r}")`. A `tournament` token sets
`schedule_format=None`, `tournament_mode=mode`. Every pre-existing `ValueError` string
is preserved VERBATIM (`"malformed phase composition"`,
`f"unknown phase type: {token!r}"` — `member_night` still rejected at the **type**
level, `f"unknown schedule_format: {fmt!r}"`,
`"composition must contain at least one round-robin phase"`,
`"a tournament phase requires a preceding round-robin phase"`). The module stays
**Django-free** (frozen `dataclasses` / `typing` allowlist); the form layer re-wraps
the plain `ValueError` as a `forms.ValidationError` on `phases`. `PhaseSpec` shape
UNCHANGED (`(ordinal, phase_type, schedule_format, tournament_mode)`).

**Compose-guard relaxation (`matches/phase_composer.py`).** The **≥1-round-robin** rule
is kept VERBATIM. The `"a tournament phase requires a preceding round-robin phase"`
string is preserved but now fires **ONLY** when
`spec.phase_type == "tournament" AND spec.tournament_mode == "standings" AND not
seen_round_robin` — so `strength` / `unseeded` may sit **anywhere, including first**,
and a mid-season `standings` tournament is ALLOWED (there is no
"standings-must-be-final" guard, only "standings-must-have-a-preceding-RR").

**Build differential (`Season.activate_pending_tournament_phase` +
`_seed_order_for_phase`).** The existing idempotency guards (`phase is None` /
`phase.phase_type != "tournament"` / `phase.tournament_id is not None` /
`phase.pk is None`) are kept. The gate generalises: for `standings`, `prior is None or
not _phase_complete(prior)` returns (unchanged); for `strength` / `unseeded`, a NULL
prior is PERMITTED, and a present prior must still be `_phase_complete` (the barrier
guarantees this). `_seed_order_for_phase` branches on `tournament_mode`:
- **`standings`** → `[row.team_id for row in self._final_standings_for_phase(prior)]`
  (rank order; `prior` non-None per the gate) — byte-identical to today's ordering.
- **`strength`** → `bracket.default_seed_order([(tid, mean_overall_rating) for tid in
  team_ids])` where `team_ids = self.starting_team_ids_json or []` and
  `mean_overall_rating = mean(p.overall_rating for p in Team(tid).active_players)`
  (`Team.active_players` property; `Player.overall_rating` property; mean of an empty
  active-players list ⇒ `0.0`, guarding `ZeroDivisionError`). `default_seed_order` sorts
  mean DESC then `team_id` ASC.
- **`unseeded`** → a fresh `random.Random()` shuffle of `team_ids` (`random` imported
  locally; **NOT** the SIM-07 seed chain — non-deterministic, no contract interaction).

The **shared build tail is mode-independent**: create a `single_elimination` Tournament
(`team_assembly="preset"`, `state="setup"`), one `TournamentParticipant` per ordered
team with **`seed = position + 1`** (byte-identical to today's `seed=row.rank` for
`standings`, since `_final_standings_for_phase` returns dense 1..N ranks), set
`phase.tournament` + `save(update_fields=["tournament"])`, then `lock_and_build()`. The
tournament **name** is `f"{self.name} Playoffs"` for `standings` else
`f"{self.name} Tournament"`.

**Build trigger (`Season.start_season`).** Gains an
`activate_pending_tournament_phase()` call INSIDE the existing `@transaction.atomic`
block, AFTER the snapshot writes (`starting_team_ids_json` /
`starting_map_pool_ids_json`) and `state="active"` + `save()` — so a first-phase
`strength` / `unseeded` tournament builds the instant the Season activates. The existing
post-`simulate_scheduled_round` hook (which already calls
`activate_pending_tournament_phase()` before `complete_if_finished()`) is UNCHANGED — it
covers the mid-season-after-RR case; the method is idempotent so calling it at both sites
is safe.

**Barrier (`playable_fixtures_by_phase` + two swaps).**
`playable_fixtures_by_phase()` = `scheduled_fixtures_by_phase()` filtered to RR phases
whose `ordinal` is strictly LESS than `_tournament_barrier_ordinal()` (the first
incomplete `tournament` phase's ordinal); when no tournament phase is incomplete it
returns the full output. `_tournament_barrier_ordinal()` walks `ordered_phases()` and
returns the ordinal of the first `tournament` phase that is NOT `_phase_complete`, else
`None`. Two one-line play-loop swaps —
`matches/tasks.py::play_season_task` and `matches/league_views.py::play_week` swap
`scheduled_fixtures_by_phase()` → `playable_fixtures_by_phase()`; everything else in
both loops (`phase_by_id`, the flat `[(phase.id, fixture)]` build, the leg-bearing
`played_keys`, `select_play_fixtures`, offsets, `arena_map` resolution) is UNCHANGED.
`play_two_months` / `play_until_end` enqueue `play_season_task` — UNCHANGED. **Why:**
with a mid-season tournament the RR loop must halt before later RR phases so the bracket
(built by the hook) drains through `play_single_round` / `play_playoffs` first; once the
tournament phase completes, the barrier advances past it and the later RR phases become
playable.

**Dashboard label split (`_build_dashboard_context` / templates).** A new
`following_tournament_is_final: bool` (computed in / alongside `_playoff_cursor_keys`)
drives the terminal play button: when the next tournament phase after the current RR
phase is the FINAL phase (last ordinal) → label **"Until Playoffs"**; when it is
mid-season (not last ordinal) → **"Until Tournament"**. `has_following_tournament_phase`
still gates whether the tournament-aware label shows at all. Touches both
`templates/seasons/dashboard.html` and `templates/leagues/dashboard.html`. **LOCKED:**
the playoff button-group DOM ids + the `play_until_end` action are UNCHANGED (visible
label text only); the Part2c-1 playoff context keys + Play Single Round / Play Playoffs /
View bracket controls are UNCHANGED — they key off `current_phase()` being a built
tournament phase, so a mid-season bracket drains through them with no structural change.

**Composer (`templates/leagues/create.html`).** A tournament composer row gains a mode
`<select>` with locked DOM id **`league-create-phase-mode-{i}`** (`{i}` = the existing
0-based JS `rowSeq` index). Options `standings` / `strength` / `unseeded` selectable;
**`random_draw` a DISABLED "coming soon" option** (the `member_night` deferral pattern).
Shown for `tournament` rows only (hidden for `round_robin`, mirroring how
`phase-format-select` is RR-only via `applyType()`). `serialize()` emits
`tournament:<mode>` for a tournament row (reading the row's `.phase-mode-select`,
default `"standings"`); RR rows still emit `round_robin:<format>`. The
`phase-tournament-pending` note is PRESERVED; all existing Part2b / c-3a DOM ids
unchanged.

**UNCHANGED (→ later slices).** `random_draw` build (DEFERRED); the pure
`select_play_fixtures` / `find_next_matchday` / `find_next_fixture` / `round_progress` /
`compute_leaders` helpers + `scheduled_fixtures` / `scheduled_fixtures_by_phase` (the
DISPLAY path); `bracket.default_seed_order` body, `Tournament.lock_and_build`,
`TournamentParticipant`, `simulate_match` / `simulate_scheduled_round`, the tournament
engine; `complete_if_finished` / `_stamp_champion_for_final_phase` (champion still from
the FINAL phase). **No migration, no re-baseline.**

**Tests.** `test_phase_composer.py` (bare `tournament` ⇒ `standings`;
`tournament:strength` / `:unseeded` / `:standings` parse + stamp; `tournament:random_draw`
/ `:bogus` ⇒ `unknown tournament_mode`; `member_night` still `unknown phase type`; guard
relaxation — `tournament:standings,round_robin` raises, `tournament:strength,round_robin`
/ `:unseeded,...` / `round_robin,tournament:standings` /
`round_robin,tournament:standings,round_robin` do NOT; purity); seed-vector unit
(`unseeded` ⇒ valid permutation with dense seeds 1..N, NOT an exact order; `strength` ⇒
`default_seed_order` of `(team_id, mean rating)`); DB `TestCase` (build at `start_season`
for a first-phase `strength`/`unseeded`; build post-round mid-season-after-RR; barrier
excludes post-barrier RR fixtures then re-admits after the tournament completes —
assert on the **set** of excluded fixtures, NOT point totals; mid-season `standings`
seeds from cumulative standings-so-far; champion still from final phase; dashboard label
split). **Assertion discipline:** schema-level outcomes only (participant seeds, excluded
fixtures, state flips, champion id, label string) — NEVER raw simulated point totals
(tournament sims are non-deterministic).

## LG-02-Part2c-3d per-tournament-block configuration

Surfaces **per-`tournament`-block config** on `SeasonPhase` — a **dormant**
`tournament_format` column (written-but-unread by the build) and a **live**
`tournament_cut` top-N participant cut applied to the seeded order before the
bracket builds. A **pure orchestration/config slice** — NO simulator mechanics
change, NO RNG change, NO tournament-engine change (`play_next_node` /
`lock_and_build` consumed VERBATIM), **no Score Calibration re-baseline**;
`tournament_cut=0` (the default) is **byte-identical to today** (full participant
set). Extends
[ADR-0023](../../adr/0023-season-phase-composable-structure.md) (Part2c-3d
consequences addendum, no new ADR); the GLOSSARY.md **Season phase** entry carries
the config-split vocabulary. Seam contract:
[`.claude/worktrees/lg-02-part2c-3d-seam-contract.md`](../../../.claude/worktrees/lg-02-part2c-3d-seam-contract.md).

**Model (`matches/models.py`, `SeasonPhase`).** Two new columns, appended
immediately after the existing `tournament_mode` field (before `class Meta`):
- **`tournament_format = models.CharField(max_length=32,
  choices=TOURNAMENT_FORMAT_CHOICES, default="single_elimination")`** — **DORMANT**
  (written-but-unread by the build this slice). Class attr
  **`TOURNAMENT_FORMAT_CHOICES`** is INLINED on `SeasonPhase` (it must NOT reference
  `Tournament.FORMAT_CHOICES` — `Tournament` is declared LATER in the file, so a
  class-body reference fails at eval; the c-3b `TOURNAMENT_MODE_CHOICES`-inlined
  precedent). Its **5 tuples mirror `Tournament.FORMAT_CHOICES` byte-for-byte**:
  `("single_elimination", "Single elimination")`, `("double_elimination", "Double
  elimination")`, `("round_robin", "Round robin")`, `("round_robin_double_elim",
  "Round robin → Double elimination")` (the `→` is U+2192), `("swiss", "Swiss")`.
- **`tournament_cut = models.PositiveSmallIntegerField(default=0)`** — **LIVE**
  (`0` = no cut = all enrolled teams). No `db_index`, no `null`/`blank`, no
  constraint on either column.

**Migration `0046_seasonphase_format_cut`** (dep `0045_seasonphase_tournament_mode`):
exactly **2× `AddField`** in order — `tournament_format` THEN `tournament_cut` —
**NO `RunPython` / NO backfill** ([ADR-0004](../../adr/0004-simulation-data-is-disposable.md)
posture — existing tournament phases inherit `tournament_format="single_elimination"`
+ `tournament_cut=0`).

**Build cut slice (`Season.activate_pending_tournament_phase`,
`@transaction.atomic`).** The method is UNCHANGED except for **ONE inserted guard**,
placed AFTER `order = self._seed_order_for_phase(phase)` and BEFORE the existing
`if not order: return`:

```python
        order = self._seed_order_for_phase(phase)
        if phase.tournament_cut:
            order = order[:phase.tournament_cut]
        if not order:
            return
```

It keeps the top `cut` seeds of the already-ordered (any-mode) seed vector — dense
seeds `1..cut`. **Cut semantics:** `tournament_cut == 0` ⇒ slice not applied (full
set, byte-identical to today); `cut > 0` ⇒ `order[:cut]`; `cut > len(order)` ⇒ a
Python no-op slice past end (all teams). `Season._seed_order_for_phase` is
**BYTE-IDENTICAL / NOT edited** — the cut applies to its OUTPUT at the caller, never
inside it. The build tail STAYS hardcoded: `Tournament.objects.create(...,
format="single_elimination", team_assembly="preset", state="setup")`, the
participant loop `seed = position + 1`, `phase.tournament` /
`save(update_fields=["tournament"])`, `lock_and_build()`. **`tournament_format` is
written-but-unread** — an admin who sets `tournament_format="swiss"` STILL gets a
single-elim bracket (a known, ACCEPTABLE admin foot-gun this slice; the live format
build is c-3e).

**Pure module (`matches/phase_composer.py`).** `PhaseSpec` gains a trailing
**`tournament_cut: int = 0`** (appended LAST with a default ⇒ existing keyword
constructions stay equality-identical, the c-3a `leg` / c-3b `tournament_mode`
precedent). The frozen import allowlist (`dataclasses` / `typing` ONLY) is
UNCHANGED — the new code adds **NO import**, so `TestNoDjangoImportsLeaked` stays
green.

**Wire grammar `tournament[:mode[:cut]]` (`matches/phase_composer.py`).** The
**tournament branch only** switches from `token.partition(":")` to
**`parts = token.split(":")`**: `parts[0]` = type, `parts[1]` = mode (default
`"standings"`), `parts[2]` = cut string (default `"0"`). The **RR branch grammar is
UNCHANGED** — still `round_robin[:schedule_format]` (max 2 parts via `partition`; a
3rd part → malformed). Locked tournament-token rules:
- bare `tournament` ⇒ mode `"standings"`, cut `0`.
- `tournament:strength` ⇒ mode `"strength"`, cut `0`.
- `tournament:standings:8` ⇒ mode `"standings"`, cut `8`.
- `len(parts) > 3` ⇒ EXISTING `ValueError("malformed phase composition")`.
- empty cut (e.g. `tournament:standings:`) / non-int cut ⇒ EXISTING
  `"malformed phase composition"`.
- mode not in `_VALID_TOURNAMENT_MODES` ⇒ EXISTING
  `ValueError(f"unknown tournament_mode: {mode!r}")` (verbatim; the tuple
  `("standings", "strength", "unseeded")` is UNCHANGED).
- parsed `cut != 0 and cut < 4` ⇒ NEW LOCKED
  **`ValueError(f"tournament cut must be 0 or at least 4: {cut}")`** (`cut` is the
  parsed int, no `!r`) — the floor `{0} ∪ {≥4}`.

Validation order on the tournament branch (LOCKED): split → reject `len(parts) > 3`
(malformed) → mode-membership check → parse cut (empty / non-int → malformed) →
cut-floor check. `member_night` is still rejected at the phase-**type** level by the
existing `f"unknown phase type: {token!r}"`. Every pre-existing `ValueError` string
is preserved VERBATIM (`"malformed phase composition"`,
`f"unknown schedule_format: {schedule_format!r}"`,
`f"unknown tournament_mode: {mode!r}"`, `f"unknown phase type: {token!r}"`,
`"composition must contain at least one round-robin phase"`,
`"a tournament phase requires a preceding round-robin phase"`). The module stays
**Django-free**; the form layer re-wraps the plain `ValueError` as a
`forms.ValidationError` on `phases`. **Validation is PARSER-ONLY** — NO
`Season.clean()` / `SeasonPhase.clean()` guard is added; a `cut` leaving `< 4`
participants at runtime is caught defence-in-depth by the EXISTING
`Tournament.lock_and_build` ≥4-participant `ValidationError`.

**Creation / carry-forward (`matches/league_views.py`).** Both
`SeasonPhase.objects.create(...)` loops sit inside their existing
`@transaction.atomic` blocks:
- `league_create` (~L559) adds **`tournament_cut=spec.tournament_cut`** and does
  **NOT** set `tournament_format` (there is no `PhaseSpec.tournament_format`; the
  column default `"single_elimination"` applies).
- `next_season` (~L2137) carries forward **BOTH**
  `tournament_cut=src.tournament_cut` AND `tournament_format=src.tournament_format`
  verbatim (the persisted source `SeasonPhase` row has both real columns).

**Composer (`templates/leagues/create.html`).** A tournament composer row gains two
new tournament-rows-only controls (same `applyType()` show/hide rule as the mode
`<select>`):
- **Cut input** — `<input type="number" min="0">` with DOM id
  **`league-create-phase-cut-{i}`** (`{i}` = the existing `rowSeq` index), class hook
  **`phase-cut-input`**, **default value `0`**, wired to `serialize()` on `change`.
- **Disabled tournament-format `<select>`** — DOM id
  **`league-create-phase-tournament-format-{i}`** (DISTINCT from the RR
  `league-create-phase-format-{i}`), single visible option text **"Single
  elimination (more formats coming soon)"**, `disabled` attribute present. It
  serializes **NOTHING** (`serialize()` must NOT read it — the build hardcodes
  `format="single_elimination"`, the column default covers persistence; a visual
  placeholder, the `phase-tournament-pending` / disabled-`random_draw`-option
  precedent).

`serialize()` emits **`tournament:<mode>:<cut>`** for a tournament row (reading the
row's `.phase-mode-select` then `.phase-cut-input`, cut defaulting to `"0"`); RR rows
still emit `round_robin:<format>` UNCHANGED. The default `value="0"` on the cut input
guarantees a parseable `"0"` for an untouched tournament row. All existing Part2b /
c-3a / c-3c DOM ids unchanged (`league-create-phases-composer`,
`league-create-add-block`, `league-create-phases`, `league-create-phase-row-{i}`,
`league-create-phase-type-{i}`, `league-create-phase-format-{i}`,
`league-create-phase-mode-{i}`, `league-create-member-night-note`, the
`phase-tournament-pending` class).

**Admin (`matches/admin.py`).** `SeasonPhaseAdmin.list_display` appends
`"tournament_format"`, `"tournament_cut"` (now 8 entries).

**Backward-compat invariants.** `tournament_cut == 0` ⇒ byte-identical to today
(full participant set; `order[:cut]` slice not applied). Bare `tournament` /
`tournament:strength` wire tokens parse identically to c-3c (mode resolved, cut `0`);
every Part2b / c-3a / c-3c serialized value parses unchanged. `cut > enrolled-team
count` ⇒ no-op slice (all teams). `tournament_format` is written-but-unread (the
acceptable admin foot-gun above).

**UNCHANGED (→ c-3e).** The LIVE format picker + per-format sub-config + the
non-single-elim build that READS `tournament_format` (DEFERRED to c-3e — the c-3b→c-3c
dormant→live rhythm); `team_assembly` (subsumed by the deferred
`tournament_mode="random_draw"`); `Season._seed_order_for_phase` body; the tournament
engine (`play_next_node` / `lock_and_build` / `TournamentParticipant`); the simulator,
RNG, and `Match` model; the read-path / barrier / dashboard label split (c-3c).
**No re-baseline.**

**Tests.** `test_phase_composer.py` (cut grammar — `tournament:standings:8` ⇒ cut 8,
`tournament:strength` ⇒ cut 0, bare `tournament` ⇒ cut 0; floor —
`tournament:standings:3` ⇒ the new cut-floor `ValueError`, `:0` accepted; malformed —
`len(parts) > 3` / empty cut / non-int cut ⇒ `"malformed phase composition"`;
back-compat — every c-3c serialized value parses unchanged; `PhaseSpec` default
`tournament_cut == 0`; purity `TestNoDjangoImportsLeaked`),
`test_season_phase.py` (`tournament_format` default `"single_elimination"` +
`TOURNAMENT_FORMAT_CHOICES` 5 tuples + `max_length==32`; `tournament_cut` default `0`
+ `PositiveSmallIntegerField`), `test_season_playoffs.py` (NOTE the trailing `s` —
the build cut: participant COUNT == `cut` + dense seeds `1..N` + champion stamped +
the built tournament's `format` stays `"single_elimination"`; `cut=0` ⇒ full set;
`cut > enrolled` ⇒ all teams), `test_league_create.py` (a composed tournament phase
persists its `tournament_cut`; `tournament_format` defaults to
`"single_elimination"`), `test_league_next_season.py` (**hand-set a source phase's
`tournament_cut=8` + `tournament_format` e.g. `"swiss"` via ORM, assert the
carry-forward preserves BOTH**). **Assertion discipline:** schema-level outcomes only
(participant seeds/count, built `format`, champion id, parsed cut) — NEVER raw
simulated point totals (tournament sims are non-deterministic).

## LG-02-Part2c-3e non-single-elim finals embeds

Flips the **dormant** c-3d `tournament_format` column **dormant→live** so a
Season `tournament` phase builds via **ANY of the five formats**
(`single_elimination` / `double_elimination` / `round_robin` /
`round_robin_double_elim` / `swiss`), and surfaces **full per-format sub-config
parity** with the standalone `tournament_create` form (4 Series-length tiers +
RR→DE winners-/losers-bracket advancers + Swiss rounds). A **thin
orchestration/config slice** — the standalone Tournament engine already builds +
drains all five formats and is consumed VERBATIM, so NO simulator mechanics
change, NO RNG change, NO tournament-engine change (`lock_and_build` /
`play_next_node` untouched). Tournament sims are **non-deterministic** ⇒ **no
Score Calibration re-baseline** (the c-3c `unseeded`-shuffle precedent); the
SE-default build (series `1`, advancers `0`) is **byte-identical to Part2c-1**.
Extends [ADR-0023](../../adr/0023-season-phase-composable-structure.md)
(Part2c-3e consequences addendum, no new ADR); the GLOSSARY.md **Season phase**
entry carries the five-format + sub-config vocabulary. Seam contract:
[`.claude/worktrees/lg-02-part2c-3e-seam-contract.md`](../../../.claude/worktrees/lg-02-part2c-3e-seam-contract.md).

**Model (`matches/models.py`, `SeasonPhase`).** Seven new columns, appended
**after** `tournament_cut` (the c-3d field), before `class Meta`. Each mirrors
`Tournament`'s same-named field byte-for-byte; the series choices are **INLINED
on `SeasonPhase`** (NOT referencing `Tournament.*` — `Tournament` is declared
LATER in the file, so a class-body reference fails at eval; the c-3b/c-3d
inlined-choices precedent):
- **`final_series_length` / `semifinal_series_length` /
  `quarterfinal_series_length` / `earlier_series_length`** —
  `PositiveSmallIntegerField(choices=((1,"Best of 1"),(3,"Best of 3"),(5,"Best of
  5")), default=1)`. The four bracket-depth Series tiers (final / semifinal /
  quarterfinal / earlier-rounds), consumed by the engine's
  `series_length_for_round` / `series_length_for_depth`.
- **`wb_advancers` / `lb_advancers` / `swiss_rounds`** —
  `PositiveSmallIntegerField(default=0)`, **no choices**. `wb_advancers` /
  `lb_advancers` are the RR→DE winners-/losers-bracket advancer counts; `swiss_rounds`
  is the Swiss round count.

No `db_index`, no `null`/`blank`, no constraint on any of the seven.
**`tournament_format`** (the c-3d `CharField(max_length=32,
choices=TOURNAMENT_FORMAT_CHOICES, default="single_elimination")`) and its inlined
5-tuple **`TOURNAMENT_FORMAT_CHOICES`** are **UNCHANGED** — only their *consumption*
in the build flips (dormant→live). No schema change to `tournament_format`.

**Migration `0047_seasonphase_tournament_subconfig`** (dep
`0046_seasonphase_format_cut`): exactly **7× `AddField`** in declaration order
(`final_series_length`, `semifinal_series_length`, `quarterfinal_series_length`,
`earlier_series_length`, `wb_advancers`, `lb_advancers`, `swiss_rounds`) — **NO
`RunPython` / NO backfill** ([ADR-0004](../../adr/0004-simulation-data-is-disposable.md)
posture — existing tournament phases inherit series tiers `1`, wb/lb `0`, swiss
`0`). `tournament_format` was already migrated by c-3d's `0046` — **no
`AlterField`**.

**Build (`Season.activate_pending_tournament_phase`, `@transaction.atomic`).**
The method is UNCHANGED except for **ONE changed** `Tournament.objects.create(...)`
call — `format` flips from the hardcoded `"single_elimination"` to
`phase.tournament_format`, and the 7 sub-config kwargs are passed from the phase:

```python
        tournament = Tournament.objects.create(
            name=name,
            format=phase.tournament_format,
            team_assembly="preset",
            state="setup",
            final_series_length=phase.final_series_length,
            semifinal_series_length=phase.semifinal_series_length,
            quarterfinal_series_length=phase.quarterfinal_series_length,
            earlier_series_length=phase.earlier_series_length,
            wb_advancers=phase.wb_advancers,
            lb_advancers=phase.lb_advancers,
            swiss_rounds=phase.swiss_rounds,
        )
```

Everything else is UNCHANGED: the idempotency / gate guards, the c-3d cut slice
(`order = order[:phase.tournament_cut]`), `Season._seed_order_for_phase`
(**BYTE-IDENTICAL**, NOT edited — the cut still applies to its OUTPUT at the
caller), the participant loop `seed = position + 1`, the name (`"{name} Playoffs"`
for `standings` else `"{name} Tournament"`), `team_assembly="preset"`,
`state="setup"`, `phase.tournament` / `save(update_fields=["tournament"])`,
`lock_and_build()`. `lock_and_build` already dispatches on `self.format` across
all five formats and consumes the 7 sub-config fields — series tiers via
`series_length_for_round` / `series_length_for_depth` → `_persist_elim_specs`;
wb/lb (RR→DE) via `build_de_finals_if_rr_finished`; `swiss_rounds` (Swiss) — so
**no engine edit**. The SE-default (series `1`, advancers `0`) build is
byte-identical to Part2c-1. A `random_draw` `team_assembly` is NOT introduced
(`tournament_mode="random_draw"` stays parser-rejected per c-3c; the build
hardcodes `team_assembly="preset"`).

**Pure module (`matches/phase_composer.py`).** `PhaseSpec` gains **8 trailing
defaulted fields**, appended LAST (the c-3a `leg` / c-3b `tournament_mode` / c-3d
`tournament_cut` append-with-default precedent ⇒ every existing keyword
construction stays equality-identical):

```python
    tournament_format: str = "single_elimination"
    final_series_length: int = 1
    semifinal_series_length: int = 1
    quarterfinal_series_length: int = 1
    earlier_series_length: int = 1
    wb_advancers: int = 0
    lb_advancers: int = 0
    swiss_rounds: int = 0
```

The frozen import allowlist (`dataclasses` / `typing` ONLY) is UNCHANGED — the
new code adds **NO import** (no `json`, no Django), so `TestNoDjangoImportsLeaked`
stays green.

**Wire grammar `tournament:mode:cut:format:fsl:ssl:qsl:esl:wb:lb:swiss`
(`matches/phase_composer.py`).** The **tournament branch only** extends the c-3d
`tournament[:mode[:cut]]` (3-way `split(":")`) to a **positional
trailing-optional 11-field** layout. The **RR branch grammar is UNCHANGED** (still
`round_robin[:schedule_format]` via `partition`). Positions:

```
parts[0]  = "tournament"
parts[1]  = mode            default "standings"
parts[2]  = cut             default "0"
parts[3]  = format          default "single_elimination"
parts[4]  = fsl  (final)            default "1"
parts[5]  = ssl  (semifinal)       default "1"
parts[6]  = qsl  (quarterfinal)    default "1"
parts[7]  = esl  (earlier)         default "1"
parts[8]  = wb   (wb_advancers)    default "0"
parts[9]  = lb   (lb_advancers)    default "0"
parts[10] = swiss (swiss_rounds)   default "0"
```

`parts = token.split(":")`; `len(parts) > 11` ⇒ the existing
`ValueError("malformed phase composition")` (the c-3d `> 3` check widens to
`> 11`). Each new field is trailing-optional with its default
(`parts[k].strip() if len(parts) > k else <default>`); an empty field (`""` after
strip — a trailing `:` or a gap) ⇒ the existing `"malformed phase composition"`
(same as the c-3d cut). Three **NEW LOCKED** `ValueError`s:
- **`ValueError(f"unknown tournament_format: {fmt!r}")`** — format (parts[3]) ∉
  the SeasonPhase 5-format embeddable set (`single_elimination`,
  `double_elimination`, `round_robin`, `round_robin_double_elim`, `swiss`).
- **`ValueError(f"series length must be 1, 3, or 5: {n}")`** — any series tier
  (parts[4..7]) parses to an int ∉ `{1, 3, 5}` (`n` the parsed int; empty ⇒
  default `"1"`; non-int ⇒ `"malformed phase composition"`).
- **`ValueError(f"invalid wb/lb combo for round_robin_double_elim: {wb}/{lb}")`**
  — the (wb, lb) combo (parts[8]/parts[9]) validated **ONLY** when
  `format == "round_robin_double_elim"`, against the six locked combos
  `{(4,0), (4,2), (8,0), (8,4), (16,0), (16,8)}`. For any **non-RR→DE** format
  wb/lb are parsed-and-stored inert (no combo check), mirroring how `Tournament`
  carries `0/0` for non-RR→DE.

Swiss (parts[10]) is parse-int only (empty ⇒ default `"0"`; non-int ⇒
`"malformed phase composition"`); no range check at the parser — the engine's
Swiss resolve/clamp/freeze owns it. **Validation ORDER (tournament branch,
locked):** split → reject `len(parts) > 11` (malformed) → mode membership vs
`_VALID_TOURNAMENT_MODES` (`("standings","strength","unseeded")`, UNCHANGED) →
cut parse + the c-3d floor `{0} ∪ {≥4}` → **format membership** → **series tiers
parse + `{1,3,5}`** → **wb/lb parse + RR→DE-only combo** → swiss parse. **SHAPE
only at the parser** — NO COUNT/parity check that depends on participant count
(`wb ≤ n`, `wb + lb ≤ n`, even-N for Swiss). Every pre-existing `ValueError`
string is preserved VERBATIM (`"malformed phase composition"`,
`f"unknown phase type: {token!r}"` — `member_night` still rejected at the type
level, `f"unknown schedule_format: {schedule_format!r}"`,
`f"unknown tournament_mode: {mode!r}"`,
`f"tournament cut must be 0 or at least 4: {cut}"`,
`"composition must contain at least one round-robin phase"`,
`"a tournament phase requires a preceding round-robin phase"`). The module stays
**Django-free**; the form layer re-wraps the plain `ValueError` as a
`forms.ValidationError` on `phases`. Bare `tournament` and every c-3d/c-3c
serialized token (`tournament`, `tournament:strength`, `tournament:standings:8`)
parse identically — the missing trailing fields take their defaults.

**Validation posture (mirrors c-3d).** **SHAPE at the parser** (format ∈
embeddable set, series ∈ `{1,3,5}`, wb/lb ∈ the six combos); **COUNT/parity at
`lock_and_build`** (defence-in-depth). The existing `Tournament.lock_and_build`
`ValidationError`s — `< 4` participants
(`"A tournament requires at least 4 participants."`),
`wb_advancers > n` (`"wb_advancers exceeds participant count."`),
`wb_advancers + lb_advancers > n`
(`"wb_advancers + lb_advancers exceeds participant count."`), Swiss odd-N
(`"Swiss requires an even number of participants."`) — fire when the post-cut
seeded order doesn't fit the chosen format. The build caller
(`@transaction.atomic`) lets the `ValidationError` propagate (a degenerate config,
not a happy path). **NO new `Season.clean()` / `SeasonPhase.clean()` / form
cross-field guard.**

**Creation / carry-forward (`matches/league_views.py`).** Both
`SeasonPhase.objects.create(...)` loops sit inside their existing
`@transaction.atomic` blocks:
- `league_create` (~L559) sets **all 8 new fields from `spec`**
  (`tournament_format=spec.tournament_format` plus the 4 series tiers + wb/lb +
  swiss). `tournament_format` now comes from `spec` — there IS a
  `PhaseSpec.tournament_format` this slice, so the c-3d "left to column default"
  note no longer applies.
- `next_season` (~L2141) carries **all 8 forward from `src` verbatim** (the c-3d
  call already carried `tournament_cut` + `tournament_format`; this adds the 7
  sub-config fields).

**Composer (`templates/leagues/create.html`).** The c-3d **disabled** placeholder
format `<select>` goes **LIVE**, and new per-format sub-config controls appear,
all show/hidden by a type+format toggle mirroring the standalone
`tournamentCreateToggle`:
- **Format select** — `league-create-phase-tournament-format-{i}` (now ENABLED),
  class `phase-tournament-format-select`, **5 options** matching
  `SeasonPhase.TOURNAMENT_FORMAT_CHOICES` (`single_elimination` selected /
  `double_elimination` / `round_robin` / `round_robin_double_elim` / `swiss`).
- **4 series-length selects** — DOM ids `league-create-phase-final-sl-{i}` /
  `-semifinal-sl-{i}` / `-quarterfinal-sl-{i}` / `-earlier-sl-{i}`; class
  `phase-series-length-select`; each Bo1/Bo3/Bo5 (`1`/`3`/`5`), Bo1 selected;
  shown for `single_elimination` / `double_elimination` / `round_robin_double_elim`.
- **wb/lb combo select** — DOM id `league-create-phase-rrde-combo-{i}`; class
  `phase-rrde-combo-select`; six locked combo value-strings (`4/0`, `4/2`, `8/0`,
  `8/4`, `16/0`, `16/8` — the exact value-string format mirrors the standalone
  form's rrde-combo options); shown for `round_robin_double_elim` ONLY.
  `serialize()` splits the combo on `/` into wb + lb.
- **swiss-rounds input** — DOM id `league-create-phase-swiss-rounds-{i}`; class
  `phase-swiss-rounds-input`; `<input type="number" min="0" value="0">`; shown for
  `swiss` ONLY.

`applyType()` + the format-change toggle: an RR row hides format/series/combo/swiss
and shows the schedule-format select (c-3a); a tournament row shows the format
select, the four series selects for SE/DE/RR→DE, the rrde-combo for RR→DE only, and
the swiss-rounds input for Swiss only — the format select's `change` re-runs the
sub-config show/hide. The existing mode select
(`league-create-phase-mode-{i}`, c-3c), cut input
(`league-create-phase-cut-{i}`, c-3d), and `phase-tournament-pending` note are
**PRESERVED**. `serialize()` reads `.phase-mode-select` (default `standings`),
`.phase-cut-input` (default `"0"`), the now-enabled `.phase-tournament-format-select`
(default `single_elimination`), the 4 `.phase-series-length-select` tiers (default
`1`), `.phase-rrde-combo-select` (split into wb/lb, default `0/0`), and
`.phase-swiss-rounds-input` (default `0`), then emits
**`tournament:<mode>:<cut>:<format>:<fsl>:<ssl>:<qsl>:<esl>:<wb>:<lb>:<swiss>`**;
RR rows still emit `round_robin:<format>` UNCHANGED. All prior Part2b / c-3a /
c-3c / c-3d DOM ids unchanged (`league-create-phases-composer`,
`league-create-add-block`, `league-create-phases`, `league-create-phase-row-{i}`,
`league-create-phase-type-{i}`, `league-create-phase-format-{i}`,
`league-create-phase-mode-{i}`, `league-create-phase-cut-{i}`,
`league-create-member-night-note`, the `phase-tournament-pending` class).

**Admin (`matches/admin.py`).** `SeasonPhaseAdmin.list_display` appends the 7
sub-config columns after `tournament_cut` (`tournament_format` already present
from c-3d).

**Backward-compat invariants.** Bare `tournament` / `tournament:strength` /
`tournament:standings:8` and every c-3d/c-3c serialized value parse identically
(the missing trailing fields take their defaults). Existing tournament phases
inherit `single_elimination` + series `1` + advancers `0`. The SE-default build is
byte-identical to Part2c-1.

**UNCHANGED (→ c-3f).** Completion `Season._phase_complete` (a tournament phase
is complete ⇔ `phase.tournament_id is not None AND
phase.tournament.state == "completed"`; all five formats reach `state="completed"`
via the engine); champion `Season._stamp_champion_for_final_phase`; the drain views
`play_single_round` / `play_playoffs` + `play_playoffs_task` (they drain via
`play_next_node`, which dispatches per format); `Season._seed_order_for_phase`
body; the c-3d cut slice; the full tournament engine (`lock_and_build`,
`build_bracket`, `build_double_elim_bracket`, `build_rr_de_finals_bracket`,
`build_swiss_round`, `find_next_node`, `advance_winner`, `advance_loser`,
`resolve_bye_chain`, `series_length_for_round`, `series_length_for_depth`,
`_persist_elim_specs`, `build_de_finals_if_rr_finished`,
`advance_swiss_if_round_finished`); the simulator / RNG / `Match` model; the
standalone `tournament_create.html`. The season-linked playoff Match-history
surface + weekly playoff pacing are DEFERRED to c-3f; the mid-season `random_draw`
build stays DEFERRED. **No re-baseline.**

**Tests.** `test_phase_composer.py` (the 8 new `PhaseSpec` fields — full token
round-trips per format + defaults when trailing fields omitted; the 3 new
`ValueError`s — `unknown tournament_format`, `series length must be 1, 3, or 5`,
`invalid wb/lb combo for round_robin_double_elim` validated ONLY for RR→DE;
`len(parts) > 11` ⇒ `"malformed phase composition"`; every pre-existing `ValueError`
preserved; back-compat of every c-3d/c-3c serialized token; purity
`TestNoDjangoImportsLeaked`), `test_season_phase.py` (the 7 new columns' defaults —
series tiers `1`, wb/lb/swiss `0` — and choices — series tiers `{1,3,5}`,
wb/lb/swiss none; `tournament_format` still defaults `single_elimination` with the
5-tuple choices), `test_season_playoffs.py` (NOTE the trailing `s` — the built
`Tournament`'s `format` + all 7 sub-config fields match the phase for each of the
five formats; the non-single-elim brackets ACTUALLY build — DE ⇒
winners/losers/grand_final `BracketNode`s, RR ⇒ `round_robin` nodes, Swiss ⇒
`swiss` nodes, RR→DE ⇒ RR seeding nodes then deferred DE finals; drain-to-champion
via the play loop crowns the Season champion; `cut` interacts — post-cut count fits
the format or raises the EXISTING `lock_and_build` `ValidationError`),
`test_league_create.py` (a composed tournament phase persists all 8 new fields from
`spec`), `test_league_next_season.py` (**hand-set a source phase's sub-config via
ORM, assert `next_season` carries all 8 forward verbatim**). **Assertion
discipline:** schema-level outcomes only (built `format`, sub-config field values,
`BracketNode` shapes per format, champion id, parsed spec fields, `ValueError`s,
DOM ids) — **NEVER raw simulated point totals** (tournament sims are
non-deterministic).

## LG-02-Part2c-3f season-linked playoff history + weekly playoff pacing

The **final Part2c-3 slice**. Two view/engine-layer concerns, **NO model change,
NO migration**: (A) widen the **Team History Overall tab** so its round corpus
includes **season-embedded playoff rounds** + fill the `playoff_appearances`
counter, and (B) **weekly playoff pacing** — a phase-aware Play dropdown that
drains **one playoff STAGE per "One Week" click** (and per shared-budget tail
unit) instead of one Match or the whole bracket. A **thin orchestration slice**:
the simulator / tournament engine are consumed **VERBATIM** (`simulate_match`,
`simulate_scheduled_round`, `play_next_node` untouched), the pure modules are
Django-free + **assert-only**, and this slice consumes **no RNG of its own** ⇒
**no Score Calibration re-baseline** (tournament sims are non-deterministic, the
c-3c/c-3d/c-3e precedent). Extends
[ADR-0023](../../adr/0023-season-phase-composable-structure.md) (Part2c-3f
consequences addendum, no new ADR); the GLOSSARY.md **Season phase** / **Matchday**
entries carry the season-embedded-playoff-history + per-week-stage-drain
vocabulary. Seam contract:
[`.claude/worktrees/lg-02-part2c-3f-seam-contract.md`](../../../.claude/worktrees/lg-02-part2c-3f-seam-contract.md).

**Locked names (NEW / CHANGED).**
- `matches/league_screens/team_history.py::_build_overall_context(team)` —
  **CHANGED** (the ONLY code edit for sub-feature A): widen the round corpus, fill
  `playoff_appearances`, add the `Tournament` import (see below). *(ANG-01e deleted the
  three `team_history._build_{overall,seasons,players}_context` helpers; the same logic is
  now `league_screen_services._team_overall_record` / `_team_season_rows` /
  `build_team_history_players`, behind `build_team_history`.)*
- `matches/tournament_engine.py::play_next_bracket_round(tournament: Tournament) -> int`
  — **NEW**: drain the lowest incomplete `(bracket_type, bracket_round)` STAGE to
  clinch; return the NODES-clinched count; `0` when nothing playable.
- `matches/league_views.py::play_week(request, season_id) -> HttpResponse` —
  **CHANGED**: phase-aware (tournament cursor → `play_next_bracket_round` once +
  `complete_if_finished()` + 302; else RR matchday path BYTE-IDENTICAL).
- `matches/tasks.py::play_season_task(self, season_id, max_matchdays=None) -> dict`
  — **CHANGED**: phase-aware tail draining bracket STAGES on the shared
  `max_matchdays − rr_weeks_played` budget; PROGRESS switches to `stage_progress`
  counts at the boundary.

**Sub-feature A — include playoff rounds in Team History (Overall tab).**
Season-embedded playoff Matches keep `season=NULL` (Part2c-1 decision #3), so the
Overall tab cannot reach them by flipping `season__isnull` (a **standalone sandbox**
Tournament also has `season=NULL`). The discriminator is the **FK chain** off
`GameRound`:
`match__series_match__node__tournament__season_phases__isnull=False` — a
`SeasonPhase.tournament` FK pointing at the Tournament makes its reverse
`season_phases` set non-empty (season-embedded), whereas a sandbox Tournament's
`season_phases` is empty. Three edits inside the ONE function
`_build_overall_context(team)`:
- **Edit 1 — widen the round corpus.** The regular-season-only filter
  (`GameRound.objects.filter(match__season__isnull=False)`) becomes a **single
  query** UNION of regular-season rounds + season-embedded playoff rounds:
  ```python
  rounds = (
      GameRound.objects.filter(
          Q(match__season__isnull=False)
          | Q(match__series_match__node__tournament__season_phases__isnull=False),
          match__is_completed=True,
      )
      .filter(Q(team_red=team) | Q(team_blue=team))
      .only("team_red_id", "team_blue_id", "red_points", "blue_points")
      .distinct()
  )
  ```
  **`.distinct()` is load-bearing** — the to-many `series_match` join can
  duplicate a `GameRound` row when a node holds multiple `SeriesMatch` rows. The
  per-Round W/L/T fold below (`round_outcome(...)` keyed on `gr.team_red_id ==
  team.id` / `gr.team_blue_id == team.id`) is **UNCHANGED**.
- **Edit 2 — fill `playoff_appearances`** = count of DISTINCT season-embedded
  playoff Tournaments the team was seeded into (team-global, all leagues, mirroring
  Overall's all-time scope):
  ```python
  playoff_appearances = (
      Tournament.objects.filter(
          season_phases__isnull=False, participants__team=team
      )
      .distinct()
      .count()
  )
  ```
  `season_phases__isnull=False` excludes sandbox Tournaments; `participants__team`
  keeps it to brackets the team was seeded into; `.distinct()` collapses the
  to-many `participants` join. Passed through verbatim:
  `compute_overall_record(outcomes, championships=championships,
  playoff_appearances=playoff_appearances)`.
- **Edit 3 — add the import.** `Tournament` to
  `from matches.models import GameRound, League, PlayerRoundState, Season, Tournament`
  (only its presence is load-bearing).

**Pure module + template UNCHANGED (assert-only).**
`matches/team_history_logic.py` ALREADY supports the arg —
`compute_overall_record(round_outcomes, *, championships, playoff_appearances:
int = 0) -> OverallRecord` and `OverallRecord.playoff_appearances` — so the pure
module is **NOT edited** (`TestNoDjangoImportsLeaked` stays green); the view does
ALL ORM work and the seam carries only a `list[str]` of `"W"`/`"L"`/`"T"` plus two
ints. `templates/leagues/team_history.html` already renders
`{{ overall_record.playoff_appearances }}` inside `#team-history-overall`; it read
`0` only because the view omitted the kwarg — **NO template change, no new DOM id**.

**KEPT UNCHANGED (sub-feature A).** **Players tab**
(`team_history.py::_build_players_context`) — its `PlayerRoundState` corpus filters
ONLY by team membership (no season filter), so playoff `PlayerRoundState` rows
**already** count toward player rollups; the **ONE accepted limitation**: a playoff
Match's `season_id is None`, so its `season_year` resolves to `None` — playoff
games count toward `games_played`/stats but do NOT set "last season played"
(accepted, out of scope). **Seasons tab** (`_build_seasons_context`) — per-Season
rank via `compute_standings` scoped to `season.matches.filter(is_completed=True)`
stays **regular-season-only** (playoff Matches have `season_id=None`, so rank is
**unaffected**).

**Sub-feature B — weekly playoff pacing.** New engine helper
`play_next_bracket_round(tournament) -> int` built on the **VERBATIM** per-Match
atomic `play_next_node`. **"One week" = one STAGE** = the lowest incomplete
`(bracket_type, bracket_round)` group (the unit `matches/bracket.py::stage_progress`
counts — for single-elim, one bracket depth: all quarterfinals, then semifinals,
then the final). It locates the lowest incomplete stage via the existing
`find_next_node` ordering (`target_stage = (target["bracket_type"],
target["bracket_round"])` of the first returned node), then loops `play_next_node`
**while** the next playable node is still in `target_stage`, counting a node
**once on clinch** (when its `winner_id` is now set — a Bo3/Bo5 node takes multiple
`play_next_node` calls to clinch but counts once), and STOPS without starting the
next stage. **Returns the NODES-clinched count** this call; `0` when nothing
playable (the deterministic-structure quantity tests assert on). **No outer
`@transaction.atomic`** — each `play_next_node` is its own per-Match atomic commit
(ADR-0016), so a mid-stage failure leaves every clinched node committed and is
resumable.

**`play_week` (phase-aware).** Signature UNCHANGED; the POST-only 405 guard,
`last_league_id` write, and the non-`active` `_render_season_dashboard_error`
branch stay **BYTE-IDENTICAL**. The change: branch on the cursor BEFORE the RR
matchday path — when `season.current_phase()` is a built tournament phase
(`phase.phase_type == "tournament" and phase.tournament_id is not None`), call
`play_next_bracket_round(phase.tournament)` once, `season.complete_if_finished()`,
and `redirect("season_dashboard", …)`; **else the existing RR matchday path runs
byte-for-byte** (the `transaction.atomic` block, by-phase fixtures, `played_keys`,
`select_play_fixtures(..., 1)`, the per-fixture loop). The playoff branch needs no
`transaction.atomic` wrapper (`play_next_bracket_round` is per-Match atomic).

**`play_season_task` (phase-aware tail).** Signature UNCHANGED (drives Play Two
Months `max_matchdays=8` and Play Until End `max_matchdays=None`); the RR fixture
loop is UNCHANGED. **APPEND a tail** AFTER the RR loop: if the cursor is a built
tournament phase, **drain bracket STAGES on the SHARED budget** —
`rr_weeks_played = len({fixture.matchday for the fixtures simulated this run})`
(matchday is global-continuous post-Part2c-2, so a bare count is the distinct-week
count); `max_matchdays is None` ⇒ unbounded drain via `play_next_bracket_round`
until it returns `0` (champion crowned) — **but only when the run started ON the
tournament phase**: UXP-05 makes a `None` run that simulated any RR fixtures return
right after the RR loop, so "Play Until Playoffs / Tournament" leaves the freshly
built bracket unplayed as its own step — else
`bracket_budget = max(0, max_matchdays − rr_weeks_played)` calls (stop early on a
`0` return). Then `season.complete_if_finished()`. **PROGRESS `update_state`
`{completed, total}` switches meaning at the boundary (LOCKED):** Round-counts
during the RR loop (unchanged), then **STAGE-counts** via
`matches/bracket.stage_progress` over the tournament's nodes (the
`play_playoffs_task` precedent) once the bracket drain begins; the final return is
stage-counts when a tail ran, else the RR shape. `_build_play_status_response`
reads `{completed, total}` generically and is **UNCHANGED**. No outer
`@transaction.atomic`; the `finally` stays (CI-01: no task closes its own DB connection; Celery's worker fixup does).

**KEPT UNCHANGED (sub-feature B + invariants).** `play_single_round` (one playoff
Match), `play_playoffs` (async drain-all), `play_playoffs_task`,
`_build_play_status_response`, `play_two_months`, `play_until_end`,
`simulate_match`, `simulate_scheduled_round`, `play_next_node`. The **Playoffs
League screen STAYS AS-IS** — `matches/league_screens/playoffs.py` +
`templates/leagues/playoffs.html` (embedded bracket) are byte-unchanged invariants
(NO game-log rewrite, NO `_playoff_log_rows` / `matches/playoff_log.py` / new
`playoffs-*` DOM ids). The registry / sidebar / url / re-export are already live
(`"league_playoffs" not in _FEATURE_REGISTRY`, the `_build_league_sidebar_links`
Playoffs entry resolves to `_cs("league_playoffs")`, the `league_playoffs` URL,
the `__init__.py` re-export) — **assert-unchanged only**. The Play dropdown already
carries the Part2c-3c terminal "Until Playoffs"/"Until Tournament" relabel — **no
new dashboard/template change, NO new DOM ids**; this slice only changes what those
views DO on a playoff cursor.

**Scope-out (LOCKED).** NO migration; NO `Match` / `GameRound` / `SeasonPhase` /
`Tournament` field change; NO `team_history_logic.py` / `team_history.html` change;
NO Players-tab / Seasons-tab change; NO playoffs-screen reshape; NO
`simulate_match` / `simulate_scheduled_round` / `play_next_node` mechanics change;
NO Score Calibration re-baseline; NO registry/sidebar/url/re-export work; NO new
dashboard DOM ids. Mid-season **`random_draw`** tournament build still **DEFERRED**
(parser-rejected); **`member_night`** phase **inert**. **NO ADR / GLOSSARY.md edit.**

**Tests.** `test_team_history_logic.py` (assert-only — the existing purity test
stays green + the `playoff_appearances` field flows through
`compute_overall_record`); the existing **Team History view test file** (EXTENDED
in place — hand-build a Season with a built single-elim playoff embedded via the
FULL FK chain `SeasonPhase(tournament=…) → Tournament → BracketNode → SeriesMatch →
Match(season=NULL) → GameRound`: the Overall W/L/T total rises by exactly the
number of playoff `GameRound`s the team played; `playoff_appearances` equals the
DISTINCT season-embedded playoff Tournament count; a **standalone sandbox**
Tournament's rounds are NOT counted and do NOT bump `playoff_appearances`; the
Seasons-tab rank is unaffected; the Players-tab corpus rule still holds);
`test_tournament_engine.py` (EXTENDED — `play_next_bracket_round` drains one stage
and returns its node-clinch count, leaves the next stage unresolved, returns `0`
when nothing playable, a Bo3 stage counts each node once on clinch);
`test_league_play.py` / the league-play view tests (EXTENDED, under
`CELERY_TASK_ALWAYS_EAGER` — `play_week` drains exactly ONE bracket STAGE on a
tournament cursor then 302s and runs the RR path unchanged on an RR cursor, 405 on
GET, non-`active` → `play_error`; `play_season_task` with `max_matchdays=None`
drains RR then bracket to a champion, with `max_matchdays=8` subtracts
`rr_weeks_played` from the bracket budget and resumes on a second call, PROGRESS
`meta` switches to stage-counts during the drain). **Assertion discipline:**
STRUCTURE / COUNTS / champion id / state / record deltas / DOM ids — **NEVER raw
simulated point totals** (tournament sims draw fresh per-round seeds).
