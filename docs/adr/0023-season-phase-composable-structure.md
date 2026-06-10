# Season structure as an ordered list of typed phases

**Status:** Accepted (LG-02-Part2a, 2026-06-04)

## Context

PLAN.md **LG-02-Part2** ("League-create season-structure composer") was
framed as: *"replace the hardcoded `draft → round-robin → playoff`
assumption baked into `generate_schedule` with a dynamic builder that lets
the admin compose a Season flow from ordered blocks — round-robin blocks,
member nights, and one or more embedded Tournament blocks."*

The LG-02-Part2 grilling session (2026-06-04) surfaced three problems with
that framing that this ADR records and resolves.

### The framing was inaccurate

`matches/schedule_generator.py::generate_schedule` does **not** bake in a
`draft → round-robin → playoff` pipeline. It is a *pure single-round-robin
fixture generator* (circle method, mirrored for round 2) with no notion of
"draft" or "playoff". The `draft → active → completed` lifecycle is the
separate `Season.state` machine ([ADR-0014](0014-league-season-foundation.md)).
What actually encodes "a Season is one round-robin" is not one switch but a
*spread assumption* across the read path: `Season._is_finished` /
`complete_if_finished`, `play_season_task`, `play_week`,
`select_play_fixtures` / `find_next_matchday`, `season_standings`,
`season_schedule`, and the dashboards all assume the whole Season *is* a
single `generate_schedule` run keyed on `Season.schedule_format`.

### It overlaps almost entirely with LG-06

PLAN.md **LG-06** ("Phased Season lifecycle — off-season / regular /
tournament") independently proposes alternative regular-season formats and a
tournament/playoff phase that "**subsumes the LG-02 double-elim as the
canonical end-of-season closer**" feeding from regular-season **Standings**.
That is the same underlying capability LG-02-Part2 describes: a Season made
of ordered, heterogeneous phases. Building two season-structure abstractions
would be a mistake.

### "Embed the Tournament model as a block" reverses ADR-0019

[ADR-0019](0019-tournament-bracket-model.md) deliberately made **Tournament**
standalone and `season`-less ("not owned by a League"). A naive embedding
would point Tournament back at the season layer, undoing that decoupling.

## Decision

**A Season's structure is modelled as an ordered list of typed `SeasonPhase`
rows. This phase model *is* the LG-06 phased-lifecycle model** — off-season /
regular / tournament are *phase types*, not a parallel abstraction.

Concretely, the **Part2a foundation slice** (this ADR) ships:

- A new persisted model `SeasonPhase` — FK → **Season**, a 1-based `ordinal`
  (ordering within the Season), and a `phase_type` enum. **Only one phase
  type is built in Part2a: `round_robin`** (the existing `single_round_robin`
  run via `generate_schedule`). `tournament` and `member_night` are declared
  as documented-but-inert future types.
- The play loop / **Standings** / Season completion read path is retrofitted
  to iterate a Season's ordered phases instead of reading
  `Season.schedule_format` directly.
- **Defensive fallback, no backfill.** A Season with **zero** persisted
  `SeasonPhase` rows falls back to an *implicit single `round_robin` phase* —
  byte-identical to today's behaviour. New Seasons get one explicit
  `round_robin` `SeasonPhase` created at create-League / next-Season time.
  There is **no `RunPython`, no data migration** ([ADR-0004](0004-simulation-data-is-disposable.md)
  disposable-data / no-backfill precedent). Existing Seasons are untouched
  and keep playing via the fallback.
- `Season.schedule_format` is left **as-is** (legacy, still consulted by the
  `generate_schedule` call inside a `round_robin` phase). It is *not* dropped
  and *not* duplicated onto the phase in this slice — a per-phase format
  sub-field arrives only when alternative formats land (Part2b). The phase
  carries only `phase_type` + `ordinal` for now.

### Forward decision: tournament phases use a one-directional `SeasonPhase → Tournament` FK

Recorded now (though *built* in a later slice) so Part2b does not relitigate
it: a `tournament` phase will hold a **nullable FK to `Tournament`**. The
Tournament model **stays season-agnostic** — it never points back at a
Season/League, so ADR-0019's "not owned by a League" survives; the *phase*
points at the Tournament. The phase lazily creates and **seeds** its
Tournament from the *preceding* phase's **Standings** on activation, reusing
the RR→DE deferred-build pattern and the entire LG-02a–c bracket engine
verbatim.

## Rejected alternatives

### Treat LG-02-Part2 and LG-06 as distinct tasks

Build the block composer now and layer LG-06's phased lifecycle on later.
Rejected — it would yield two overlapping season-structure models competing
to own "what phases does a Season have". The grill merged them: one model,
`SeasonPhase`, with off-season/regular/tournament as phase types.

### `RunPython` backfill of one `round_robin` phase per existing Season

Gives a single source of truth (no implicit-fallback branch) but breaks the
repo-wide no-`RunPython`/no-backfill precedent and is effectively
irreversible. Rejected in favour of the defensive implicit-single-phase
fallback, which costs one cheap branch on the read path and zero data
migration.

### `Tournament → SeasonPhase` FK (tournament knows its phase)

Symmetric data, but directly reverses ADR-0019 by coupling Tournament to the
season layer. Rejected for the one-directional `SeasonPhase → Tournament` FK,
which keeps Tournament a standalone, season-agnostic model that a phase
merely *references*.

### Tournament phase inlines its own bracket

A tournament phase builds `BracketNode` rows itself without the `Tournament`
model. Rejected — it would duplicate the entire LG-02a–c bracket/engine
surface.

### Ship the full vertical (model + composer UI + multi-phase play loop + tournament embed) in one task

Rejected as contrary to the repo's slice discipline (every LG-01x / LG-02x
shipped as a tight, single-purpose PR) and an unreviewable test surface.
Part2 is sliced: **Part2a** (this — the `SeasonPhase` model + the
backward-compatible read-path retrofit), then **Part2b** (the League-create
"+" composer UI + per-phase format), then **Part2c** (the heterogeneous
multi-phase play loop + the tournament-phase lazy build / advance / hand-off).

## Consequences

- One migration creates `SeasonPhase`; **no backfill, no `RunPython`**
  ([ADR-0004](0004-simulation-data-is-disposable.md) precedent).
- **Zero user-visible change** in Part2a: a one-phase `round_robin` Season is
  exactly today's Season, and phase-less existing Seasons keep working via
  the implicit-single-phase fallback.
- The read-path retrofit (`_is_finished` / `complete_if_finished` and the
  play-loop / standings / schedule helpers) is the blast radius — each is
  generalised from "the Season is one round-robin" to "iterate the Season's
  phases (falling back to one implicit `round_robin`)".
- The existing `BatchSimulator` and `generate_schedule` are consumed
  **verbatim** — no simulator change, no RNG-contract interaction, **no Score
  Calibration re-baseline**.
- ADR-0019's season-less Tournament survives: the tournament-phase link
  (Part2b/c) is one-directional `SeasonPhase → Tournament`.
- A new PLAN.md item is opened for the deferred **member night simulator
  (sandbox mode)** phase type.

## Part2c-1 consequences (RR → single-elimination playoff embed, 2026-06-05)

The first slice of LG-02-Part2c (the RR → single-elimination playoff embed)
lands the tournament-phase build / advance / hand-off promised by the
"Forward decision" above, for the **season-ending playoff** case only. It adds
no new ADR; these are its consequences on the decisions recorded above.

- **Tournament-phase Matches stay `season=NULL`.** The playoff is built by
  wiring an existing standalone `Tournament` (consumed verbatim) into the
  `tournament` phase via the one-directional `SeasonPhase → Tournament` FK; the
  playoff's Matches are written by the tournament engine
  (`simulate_match(match_type="tournament")`), which **never sets a `season`
  FK**. So the playoff is **invisible to season-scoped history**
  (`Match.objects.filter(season=...)`), and `Season._final_standings_for_phase`
  filters `Match.objects.filter(season=self, is_completed=True)` without
  tournament Matches polluting the RR standings. **A `Match.season_phase` FK was
  deliberately NOT added this slice** — it is deferred to Part2c-2 alongside the
  multi-RR play loop (where per-phase Match scoping and a season-linked playoff
  Match-history surface need it). This is the load-bearing surprise a future
  reader will ask about: *why can't I find the playoff in the Season's Matches?*
  — because there is no Match FK yet, by design.
- **Phase completion is DERIVED, not stored; a phase cursor drives the
  read-path.** No `SeasonPhase.state` field is added. `Season.current_phase()`
  returns the first incomplete phase by ordinal (the cursor); the private
  `Season._phase_complete(phase)` is the single derivation site — RR ⇔ the
  existing `_is_finished()` all-fixtures-played check, tournament ⇔
  `phase.tournament_id is not None AND phase.tournament.state == "completed"`.
  `complete_if_finished` is rewritten to gate on the **final** phase being
  complete and to stamp the champion from that phase's type
  (`tournament.champion`, else `compute_standings(...)[0]`); a single-RR-phase
  Season stays **byte-identical** to today.
- **The play loop is split, not merged.** RR-scoped play (`play_week` /
  `play_two_months` / `play_until_end`) is behaviourally unchanged and drains the
  regular season; only the terminal label flips **"Until End of Season" → "Until
  Playoffs"** when a tournament phase follows. Two new actions drain the bracket:
  **Play Single Round** (sync, one bracket node/Match) and **Play Playoffs**
  (async Celery, drains the whole bracket to the Season champion). The build
  itself is automatic — `Season.activate_pending_tournament_phase()` fires from
  the post-round hook the moment the RR phase completes, so the bracket is
  visible before any playoff click.
- **The playoff is hardcoded single-elimination / all-teams / standings-seeded.**
  The auto-build always creates a `single_elimination` `Tournament` with one
  `TournamentParticipant` per season team seeded by Standings rank (rank 1 →
  seed 1). The **per-phase seeding-mode field** (season-ending Standings-seeded
  vs mid-season strength-/un-seeded), per-tournament-block config (format /
  top-N cut), mid-season tournaments, and non-single-elim embeds are all
  **deferred to Part2c-2**. The compose-time guard added here — *a `tournament`
  phase requires a preceding `round_robin` phase* — encodes the season-ending
  flavour's structural requirement; the mid-season flavour (no preceding
  Standings, may sit first) is not yet composable.

## Part2c-2 consequences (multi-RR play loop + Match.season_phase FK, 2026-06-06)

The second slice of LG-02-Part2c generalises the Part2c-1 single-RR-then-playoff
path into a **multi-round-robin** season (RR1→RR2, RR1→RR2→playoff). It adds no
new ADR; these are its consequences on the decisions above.

- **A `Match.season_phase` FK is finally added — and it keeps `season` set.** The
  Part2c-1 "load-bearing surprise" (no Match FK; you can't find the playoff in the
  Season's Matches) is resolved *for RR Matches only*: an RR Match now carries
  **both** `season=<season>` **and** `season_phase=<rr phase>`. The FK mirrors
  `Match.season`'s `SET_NULL` (deleting a `SeasonPhase` must not cascade-delete its
  Matches) and reuses the `related_name="matches"` label without collision
  (`Season.matches` vs `SeasonPhase.matches`). It is added by a single `AddField`
  migration (`0043_match_season_phase`) with **no `RunPython` / no backfill** — the
  same [ADR-0004](0004-simulation-data-is-disposable.md) disposable-data posture
  the `SeasonPhase` model itself shipped under. The FK is load-bearing because it
  makes the `simulate_scheduled_round` find-or-create key
  `(season, season_phase, frozenset({team ids}))` phase-aware — without it, the same
  pairing in RR1 and RR2 would collide onto one Match.
- **Tournament Matches STILL stay `season=NULL, season_phase=NULL`.** The
  tournament engine is consumed verbatim and never sets either FK, so the
  Part2c-1 statement survives: the playoff remains invisible to season-scoped
  history, and a **season-linked playoff Match-history surface stays DEFERRED**
  (now to Part2c-3). Adding the FK did not, by itself, join playoff Matches to the
  season game log.
- **Standings are cumulative across RR phases — `_final_standings_for_phase` is
  unchanged.** It keeps the whole-season filter
  `Match.objects.filter(season=self, is_completed=True)`, so a multi-RR season's
  standings aggregate every RR phase's Matches. A trailing playoff seeds from the
  cumulative leader; an RR-final-phase champion is the cumulative leader. This was
  a deliberate decision, not a deferral — per-phase standings scoping is explicitly
  rejected for this composition.
- **Phase completion becomes per-phase for RR, while standings stay cumulative.**
  `_phase_complete`'s `round_robin` branch now routes a *persisted* RR phase
  through a new per-phase `_rr_phase_complete` (scoped by
  `match__season_phase=phase`), so the cursor finishes RR1 before RR2 opens; the
  *implicit* `pk is None` fallback phase still routes through the whole-season
  `_is_finished()`, keeping phase-less and single-RR seasons byte-identical. The
  tension is intentional: completion is per-phase (which RR is done) but standings
  are cumulative (the whole season's record).
- **Matchday becomes global-continuous across RR phases via a per-phase offset.**
  A new `scheduled_fixtures_by_phase()` seam offsets phase k's fixtures by the sum
  of all prior RR phases' matchday spans, yielding one monotonic 1..N calendar;
  `scheduled_fixtures()` becomes the flat concatenation of those offset fixtures
  (byte-identical for the single-RR / phase-less case). The
  `date = start_date + (matchday-1)*7` derivation and every flat caller are
  unchanged. The pure dashboard helpers `select_play_fixtures` / `find_next_matchday`
  gain phase-awareness via **plain-int phase-ids** so `matches/season_dashboard.py`
  stays Django-free.
- **No simulator / tournament-engine change, no re-baseline.** `generate_schedule`,
  `BatchSimulator`, and the bracket engine are consumed verbatim; the per-Round RNG
  contract is untouched (only the Match a Round attaches to changes), so there is
  **no Score Calibration re-baseline and no SIM-07 / SIM-08 interaction**.

## Part2c-3a consequences (double round-robin regular-season format, 2026-06-08)

The first sub-slice of the re-sliced LG-02-Part2c-3 lands the **first alternative
regular-season `schedule_format`** — **`double_round_robin`** — as a single
`SeasonPhase` format. It adds no new ADR; these are its consequences on the
decisions above.

- **The Part2b dormant per-phase `schedule_format` column is now live
  end-to-end.** The "Forward decision" promise that wiring the read-path
  chokepoint to `phase.schedule_format` "lands in a later slice alongside the first
  alternative format" is fulfilled here. Part2c-2's `scheduled_fixtures_by_phase`
  already read `generate_schedule(team_ids, phase.schedule_format or
  self.schedule_format)`, but every value still resolved to `single_round_robin`;
  now a phase persisted with `schedule_format="double_round_robin"` produces a
  genuinely different fixture run. The Part2b composer column, the `next_season`
  carry-forward, and the per-phase read are no longer dormant for one of their two
  values.
- **`Match.leg` discriminates intra-phase repeated pairings.** A
  `double_round_robin` phase has every pair meet **twice within one phase** as
  **two distinct Matches** — but the existing Side-agnostic find-or-create key
  `(season, season_phase, frozenset({team ids}))` would collide the two legs onto
  one Match. A new `Match.leg = PositiveSmallIntegerField(default=1)` (migration
  `0044_match_leg`, single `AddField`, no `RunPython` — the same
  [ADR-0004](0004-simulation-data-is-disposable.md) posture as `0043`) extends the
  key to `(season, season_phase, frozenset({team ids}), leg)`. The same `leg`
  threads the schedule (`ScheduleFixture.leg`, with `generate_schedule` emitting
  leg-2 fixtures offset sequentially after leg 1 on one monotonic matchday
  calendar), the per-phase RR completion check (`_rr_phase_complete` now requires
  **both** legs of every pairing), and the FLAT dashboard overlays (without `leg`
  the second leg reads as already-played). `single_round_robin`, legacy phase-less
  Seasons, and all tournament/playoff Matches stay `leg=1` ⇒ byte-identical.
- **Standings stay cumulative — `_final_standings_for_phase` is unchanged.** A
  double-RR pairing is two distinct Matches, each a row in the whole-season
  `Match.objects.filter(season=self, is_completed=True)` corpus, so both legs count
  automatically with no scoping edit — consistent with the Part2c-2 decision that
  standings aggregate the whole Season's completed-Match record.
- **No simulator / tournament-engine change, no re-baseline.** `generate_schedule`
  is extended (a new format branch) but the simulator, RNG contract, and bracket
  engine are consumed verbatim; RR sims stay byte-identical per Round. So there is
  **no Score Calibration re-baseline and no SIM-07 / SIM-08 interaction**, and no
  new ADR — only this addendum.

## Part2c-3b consequences (per-phase `tournament_mode` field — dormant, 2026-06-08)

The "Forward decision" noted that a `tournament` phase comes in two flavours by
Season role (season-ending Standings-seeded vs mid-season strength-/un-seeded) and
deferred the field that captures it. This slice lands that field as a **fully
dormant** addition; it adds no new ADR — these are its consequences on the
decisions above.

- **`SeasonPhase` gains `tournament_mode`, declaring all four flavours but
  building only one.** A new `CharField(max_length=16, default="standings")` with
  `TOURNAMENT_MODE_CHOICES = {standings, strength, unseeded, random_draw}` (migration
  `0045_seasonphase_tournament_mode`, single `AddField`, no `RunPython` — the same
  [ADR-0004](0004-simulation-data-is-disposable.md) posture as `0041`/`0042`/`0043`/
  `0044`). All four values are declared now (the `member_night` declared-but-inert
  precedent), but only `standings` has build behaviour this slice — the default
  matches the hardcoded standings-seeding in `activate_pending_tournament_phase`, so
  that method is **untouched and byte-identical**. **`unseeded` ≠ `random_draw`**:
  unseeded randomly seeds the season's existing preset teams; random_draw builds
  fresh balanced teams from a player pool (reusing the LG-02x-1
  `team_assembly="random_draw"` machinery).
- **The field is threaded through the seam but the wire format is unchanged.**
  `PhaseSpec` gains a trailing defaulted `tournament_mode` (the c-3a
  `ScheduleFixture.leg` append-with-default precedent), and both `SeasonPhase`
  creation sites (`league_create` / `next_season`) stamp it so the carry-forward is
  forward-compatible. But `parse_phase_composition` does **not** parse a mode from
  the wire — a `tournament:<mode>` token still raises `"malformed phase
  composition"`, reserving the `:` syntax for the c-3c picker — so every phase this
  slice resolves to `"standings"`.
- **The compose-time validity rule is unchanged.** The `standings`-mode
  "requires a preceding `round_robin` phase" constraint is already enforced for
  every `tournament` block by the existing blanket preceding-RR guard; c-3b adds no
  new parser rule and does **not** relax the guard (relaxation for mid-season modes
  is c-3c — without the differential build, a settable mid-season mode would either
  build a standings bracket anyway or park the season cursor forever).
- **No read-path / simulator / RNG change, no re-baseline.** A dormant column add +
  a defaulted dataclass field + two creation kwargs + an admin column; nothing
  branches on the field yet. **No Score Calibration re-baseline, no SIM-07 / SIM-08
  interaction**, no new ADR — only this addendum. The mid-season *behaviour* (guard
  relaxation + strength/unseeded/random_draw build + the composer picker) is
  Part2c-3c.

## Part2c-3c consequences (mid-season tournaments — strength/unseeded build, 2026-06-09)

The "Forward decision" anticipated a mid-season `tournament` flavour that needs no
preceding Standings and may sit anywhere; Part2c-3b landed the dormant
`tournament_mode` field that names it. This slice makes that flavour BUILD for the
**`strength`** and **`unseeded`** modes (`random_draw` stays deferred). It adds no new
ADR — these are its consequences on the decisions above.

- **The c-3b dormant `tournament_mode` field goes live for two of its modes; the
  build branches on it.** `Season.activate_pending_tournament_phase` — until now
  hardcoding standings-rank seeding — gains a private
  `Season._seed_order_for_phase(phase)` that branches: `standings` →
  preceding-phase Standings rank order (byte-identical to today); `strength` →
  `bracket.default_seed_order` of `(team_id, mean active-player overall rating)` (DESC
  mean, ASC id tiebreak) over the season's starting teams; `unseeded` → a fresh
  `random.Random()` shuffle of the starting team ids. The build **tail** is
  mode-independent — a `single_elimination`/`preset` Tournament with `seed = position +
  1` (byte-identical to today's `seed=row.rank` for the dense-1..N `standings` case),
  named `"{name} Playoffs"` (standings) or `"{name} Tournament"` (mid-season), then
  `lock_and_build()`. `random_draw` remains **deferred** — the parser rejects it and the
  composer offers it only as a disabled "coming soon" option.
- **The preceding-RR compose guard is relaxed to standings-only.** The Part2c-1
  guard — *a `tournament` phase requires a preceding `round_robin` phase* — kept its
  string but now fires ONLY for a `standings`-mode tournament. A `strength` / `unseeded`
  phase may sit anywhere, including FIRST, and a mid-season `standings` tournament is
  allowed (there is no "standings-must-be-final" rule, only
  "standings-must-have-a-preceding-RR"). The ≥1-round-robin rule is unchanged. The
  generalised build gate mirrors this: a NULL preceding phase is now permitted for a
  non-`standings` first phase. The `tournament` wire token becomes `tournament[:mode]`
  (the format-part is the mode for a tournament token), with a new locked
  `ValueError("unknown tournament_mode: ...")`; every pre-existing `ValueError` string
  is preserved verbatim and the parser stays Django-free.
- **A play-loop barrier halts the RR loop at an incomplete tournament phase; the
  bracket drains through the existing playoff views.** This is the load-bearing
  structural decision of the slice. A new `Season.playable_fixtures_by_phase()` filters
  `scheduled_fixtures_by_phase()` to RR phases whose ordinal is strictly below the first
  incomplete `tournament` phase's ordinal (`_tournament_barrier_ordinal()`); the two
  RR-play-loop sites (`play_season_task` / `play_week`) swap one call onto it. So when a
  mid-season tournament is reached, no later RR phase plays until that bracket has been
  fully drained through the **existing** `play_single_round` / `play_playoffs` views
  (Part2c-1, consumed verbatim) — the slice adds **no new play action**, it gates the
  RR loop and reuses the playoff drain. Once the tournament phase completes, the barrier
  advances and the later RR phases become playable. The pure helpers
  (`select_play_fixtures` / `find_next_matchday`) and the display-path
  `scheduled_fixtures*` stay byte-unchanged; `matches/season_dashboard.py` is untouched
  (`TestNoDjangoImportsLeaked` stays green) — the barrier is a new READER over those.
- **The build fires at `start_season` for a first-phase tournament.**
  `Season.start_season` gains an `activate_pending_tournament_phase()` call inside its
  existing `@transaction.atomic` block (after the snapshot writes + `state="active"`), so
  a first-phase `strength` / `unseeded` tournament builds the instant the Season
  activates. The existing post-round hook (which already calls the same method before
  `complete_if_finished`) is unchanged and covers the mid-season-after-RR case; the
  method is idempotent, so calling it at both sites is safe. The champion is still
  stamped from the FINAL phase (`complete_if_finished` /
  `_stamp_champion_for_final_phase` unchanged) — a mid-season tournament crowns no Season
  champion.
- **No migration, no simulator/RNG/engine change, no re-baseline.** `tournament_mode`
  already exists (Part2c-3b, migration `0045`), so there is **no migration** this slice;
  the simulator, RNG contract, and bracket engine are consumed verbatim — **no Score
  Calibration re-baseline, no SIM-07 / SIM-08 interaction**, no new ADR. The `unseeded`
  shuffle uses a fresh `random.Random()`, deliberately OUTSIDE the SIM-07 deterministic
  seed chain (a mid-season draw is non-deterministic by design). The dashboard label
  split ("Until Playoffs" final vs "Until Tournament" mid-season) is label text only —
  the playoff button-group DOM ids + `play_until_end` action are unchanged.

## Part2c-3d consequences (per-tournament-block config — dormant format + live cut, 2026-06-09)

Part2c-3c made the mid-season tournament flavours build; this slice surfaces the
first **per-tournament-block configuration** on the phase, splitting it across one
dormant and one live column in the same dormant→live rhythm that carried `schedule_format`
(Part2b dormant → Part2c-3a live) and `tournament_mode` (Part2c-3b dormant → Part2c-3c
live). It adds no new ADR — these are its consequences on the decisions above.

- **The per-phase tournament config splits into a dormant `tournament_format` and a
  live `tournament_cut`.** `SeasonPhase` gains `tournament_format`
  (`CharField`, default `"single_elimination"`, choices mirroring
  `Tournament.FORMAT_CHOICES`) and `tournament_cut`
  (`PositiveSmallIntegerField`, default `0`), via migration
  `0046_seasonphase_format_cut` — two `AddField` ops, **no `RunPython` / no backfill**
  ([ADR-0004](0004-simulation-data-is-disposable.md) posture, as every `004x` phase
  migration before it). `tournament_format` is **written-but-unread** by the build this
  slice (the same declared-but-inert posture `member_night` and the four `tournament_mode`
  values shipped under): `activate_pending_tournament_phase` keeps hardcoding
  `format="single_elimination"`, so an admin-set `tournament_format="swiss"` still builds
  single-elim — a known, acceptable foot-gun until the non-single-elim **format build**
  lands in c-3e. The format choices are inlined on `SeasonPhase` rather than referencing
  `Tournament.FORMAT_CHOICES`, because `Tournament` is declared later in the file (the
  c-3b inlined-`TOURNAMENT_MODE_CHOICES` precedent).
- **`tournament_cut` is the mode-ordered top-N cut.** It goes live as a single inserted
  guard in `activate_pending_tournament_phase` —
  `if phase.tournament_cut: order = order[:phase.tournament_cut]` — applied to the OUTPUT
  of `_seed_order_for_phase` at the caller, so that method (and its standings / strength /
  unseeded branches) stays byte-identical. The cut therefore composes with **any** seeding
  mode: it keeps the top `cut` seeds of the already-ordered vector with dense seeds
  `1..cut`. `cut == 0` (the default) is byte-identical to today (full participant set);
  `cut > enrolled-team-count` is a Python no-op slice (all teams). The build tail —
  `format="single_elimination"`, `team_assembly="preset"`, `seed = position + 1`,
  `lock_and_build()` — is unchanged.
- **The wire grammar grows a trailing cut field, with a parser floor `{0} ∪ {≥4}`.** The
  `tournament` token becomes `tournament[:mode[:cut]]` (the tournament branch of
  `parse_phase_composition` switches to `split(":")`; the RR branch keeps its 2-part
  `partition(":")`); `PhaseSpec` gains a trailing defaulted `tournament_cut: int = 0`
  (the c-3a `leg` / c-3b `tournament_mode` append-with-default precedent, so every
  Part2b / c-3a / c-3c serialized value parses unchanged). A new locked
  `ValueError(f"tournament cut must be 0 or at least 4: {cut}")` rejects a `cut` that is
  neither `0` nor at least `4` at compose time; every pre-existing `ValueError` string is
  preserved verbatim and the module stays Django-free. Validation is **parser-only** — no
  `Season.clean()` / `SeasonPhase.clean()` is added; a cut leaving fewer than four
  participants at runtime is caught defence-in-depth by the existing
  `Tournament.lock_and_build` ≥4-participant `ValidationError`. The composer mirrors this
  with a tournament-rows-only cut `<input>` and a **disabled** placeholder format
  `<select>` ("Single elimination (more formats coming soon)") that serializes nothing —
  the disabled-`random_draw`-option placeholder pattern, holding the format-picker UX for
  c-3e. `next_season` carries forward both new columns verbatim.
- **The non-single-elim format build is deferred to c-3e.** This slice deliberately
  ships only the cut as live behaviour and parks the format as dormant — the same
  dormant→live split c-3b→c-3c used for `tournament_mode` — so c-3e can read
  `tournament_format` to build double-elim / RR / Swiss / RR→DE finals embeds without a
  schema change. `team_assembly` is not surfaced here (it is subsumed by the deferred
  `tournament_mode="random_draw"`).
- **No simulator / tournament-engine change, no re-baseline.** The migration adds two
  columns; the live behaviour is one slice on an already-ordered list. `play_next_node`,
  `lock_and_build`, the simulator, and the RNG contract are consumed verbatim, so there is
  **no Score Calibration re-baseline and no SIM-07 / SIM-08 interaction**, and no new ADR
  — only this addendum.

## Part2c-3e consequences (non-single-elim finals embeds — live format + sub-config, 2026-06-09)

Part2c-3d landed `tournament_format` as a **dormant** column and parked the
non-single-elim build; this slice flips it **dormant→live** — the same
dormant→live rhythm that carried `schedule_format` (Part2b dormant → Part2c-3a
live), `tournament_mode` (Part2c-3b dormant → Part2c-3c live), and now
`tournament_format`. A Season `tournament` phase now builds via **any of the five
formats** with full per-format sub-config parity with the standalone
`tournament_create` form. It adds no new ADR — these are its consequences on the
decisions above.

- **`tournament_format` flips dormant→live; seven sub-config columns join it.**
  The c-3d "written-but-unread" `tournament_format` is now **read by the build** —
  `Season.activate_pending_tournament_phase` changes its single
  `Tournament.objects.create(...)` from the hardcoded `format="single_elimination"`
  to `format=phase.tournament_format`, so a phase set to `swiss` / `double_elimination`
  / `round_robin` / `round_robin_double_elim` builds that bracket (the c-3d admin
  foot-gun is closed). Alongside it `SeasonPhase` gains **seven** sub-config columns
  mirroring `Tournament`'s same-named fields byte-for-byte — four Series-length
  tiers `final_series_length` / `semifinal_series_length` /
  `quarterfinal_series_length` / `earlier_series_length`
  (`PositiveSmallIntegerField`, choices `{1,3,5}`, default `1`) plus the RR→DE
  advancer counts `wb_advancers` / `lb_advancers` and `swiss_rounds`
  (`PositiveSmallIntegerField`, no choices, default `0`) — via migration
  `0047_seasonphase_tournament_subconfig` (7× `AddField`, **no `RunPython` / no
  backfill**, [ADR-0004](0004-simulation-data-is-disposable.md) posture as every
  `004x` phase migration before it). `tournament_format` was already migrated by
  c-3d's `0046`, so this migration touches only the seven new columns. The series
  choices are **inlined on `SeasonPhase`** (not referencing `Tournament.*` —
  `Tournament` is declared later in the file; the c-3b/c-3d inlined-choices
  precedent).
- **The slice is thin because the engine already does the work.** The standalone
  `Tournament.lock_and_build` already dispatches on `self.format` across all five
  formats and already consumes the seven sub-config fields (series tiers via
  `series_length_for_round` / `series_length_for_depth` → `_persist_elim_specs`;
  wb/lb for RR→DE via `build_de_finals_if_rr_finished`; `swiss_rounds` for Swiss).
  So this slice is pure **orchestration/config** — it passes the phase's fields
  into the create call and the engine is consumed **verbatim**. The build tail —
  the c-3d cut slice, `_seed_order_for_phase` (byte-identical), `seed = position +
  1`, `team_assembly="preset"`, the `"{name} Playoffs"` / `"{name} Tournament"`
  name, `lock_and_build()` — is otherwise unchanged, and the SE-default (series
  `1`, advancers `0`) build is **byte-identical to Part2c-1**.
- **The wire grammar grows to eleven positional fields; validation is SHAPE at the
  parser, COUNT/parity at the engine.** The `tournament` token extends from c-3d's
  `tournament[:mode[:cut]]` to a positional trailing-optional
  `tournament:mode:cut:format:fsl:ssl:qsl:esl:wb:lb:swiss` (the tournament branch's
  `split(":")` widens the c-3d `> 3` malformed check to `> 11`; the RR branch is
  unchanged); `PhaseSpec` gains eight trailing defaulted fields (the c-3a `leg` /
  c-3b `tournament_mode` / c-3d `tournament_cut` append-with-default precedent, so
  every prior serialized value parses unchanged). Three new locked `ValueError`s
  reject a bad **shape** at compose time — `unknown tournament_format` (format ∉
  the five-format set), `series length must be 1, 3, or 5` (any tier ∉ `{1,3,5}`),
  and `invalid wb/lb combo for round_robin_double_elim` (the (wb,lb) pair ∉ the six
  locked combos `{(4,0),(4,2),(8,0),(8,4),(16,0),(16,8)}`, checked **only** for
  RR→DE; for other formats wb/lb are inert, mirroring `Tournament`). Every
  pre-existing `ValueError` is preserved verbatim and the module stays Django-free.
  Validation is **parser-only for shape**; the **count/parity** rules that depend on
  participant count — `< 4` participants, `wb > n`, `wb + lb > n`, Swiss odd-N —
  are left to the **existing** `Tournament.lock_and_build` `ValidationError`s
  (defence-in-depth, as the c-3d cut-floor was), so **no new `Season.clean()` /
  `SeasonPhase.clean()` / form cross-field guard** is added. The composer mirrors
  this: the c-3d disabled placeholder format select goes live (five options) and
  per-format sub-config controls (four Series selects, an RR→DE wb/lb combo select,
  a Swiss-rounds input) show/hide by a type+format toggle, with `serialize()`
  emitting the full eleven-field token; `next_season` carries all eight new fields
  forward verbatim.
- **No simulator / tournament-engine change, no re-baseline — and the
  non-determinism is the reason.** The migration adds seven columns; the live
  behaviour is one changed create call feeding an engine consumed verbatim. The
  simulator, RNG contract, and the entire bracket engine are untouched, so there is
  **no Score Calibration re-baseline and no SIM-07 / SIM-08 interaction**, and no
  new ADR — only this addendum. Tournament sims are **non-deterministic by design**
  (the c-3c `unseeded`-shuffle / mid-season-draw precedent), so embedding a
  double-elim / RR / Swiss / RR→DE finals stage changes which bracket is built, not
  any per-Round scoring distribution that a calibration baseline would track.
- **Still deferred to c-3f.** A **season-linked playoff Match-history surface**
  (the Part2c-1 "load-bearing surprise" that tournament Matches stay
  `season=NULL, season_phase=NULL` and are invisible to season-scoped history) and
  **weekly playoff pacing** remain out of scope; the mid-season `random_draw` build
  stays deferred (the parser still rejects it).

## See also

- [ADR-0014](0014-league-season-foundation.md) — the League/Season model and
  `draft → active → completed` lifecycle this phase model sits on top of.
- [ADR-0015](0015-schedule-on-demand-no-fixture-rows.md) — the deterministic,
  fixture-less `generate_schedule` a `round_robin` phase consumes verbatim.
- [ADR-0019](0019-tournament-bracket-model.md) — the standalone, season-less
  Tournament model whose decoupling the `SeasonPhase → Tournament` FK
  preserves.
- [ADR-0004](0004-simulation-data-is-disposable.md) — disposable-data /
  no-backfill precedent for the defensive implicit-single-phase fallback.
- CONTEXT.md `### League and seasons` — the **Season phase** glossary entry.
- PLAN.md LG-02-Part2 (Part2a foundation; Part2b composer UI; Part2c
  multi-phase play loop + tournament embed) and LG-06 (phased lifecycle,
  merged into this phase model).
