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
