# A regional playoff is one Tournament per Conference, and crowns no Season champion

**Status:** Accepted (CONF-02 grill, 2026-09-02)

## Context

[ADR-0034](0034-conference-partition.md) established the **Conference** as a
Season-level partition of a Season's enrolled Teams into disjoint competitive
groups that play **intra-Conference only**. CONF-01 shipped the foundation — the
partition model, per-Conference round-robin scheduling, and per-Conference
Standings — and deliberately future-proofed the next slice by adding
`Match.conference` as an explicit discriminator FK, noting that it "future-proofs
the per-Conference regional playoffs".

CONF-02 is that slice: after each Conference's regular-season round-robin
completes, seed **its own** playoff bracket from that Conference's final
Standings.

Three prior facts framed the design, and one of them is a live contradiction.

- **A `tournament` phase holds exactly one Tournament.** `SeasonPhase.tournament`
  is a single nullable FK. `Season.activate_pending_tournament_phase` builds one
  `Tournament`, creates its `TournamentParticipant` rows from a single seeded
  order, and calls `lock_and_build()` once.
- **The seeded order is Season-wide.** `Season._final_standings_for_phase`
  computes `compute_standings` over all of `starting_team_ids_json`, with no
  Conference scoping, and `activate_pending_tournament_phase` carries no
  Conference guard. **In a ≥2-Conference Season this currently builds a single
  cross-Conference playoff** — a direct contradiction of the intra-Conference
  invariant ADR-0034 established. CONF-02 does not merely add regional playoffs;
  it corrects this.
- **A Tournament exposes only its winner.** The model carries `champion` and
  nothing else — no placement, elimination-depth, or ranking API. A
  single-elimination bracket cannot meaningfully rank its losers.

CONF-01 also locked a completion rule worth restating: a ≥2-Conference Season
flips to `completed` when every Conference's round-robin finishes but leaves
`Season.champion_team` **NULL**, because there is no legitimate cross-Conference
champion until the Worlds slice.

## Decision

**A regional playoff is N first-class Tournaments — one per Conference — built
from a single `tournament` Season phase.** Each is a real `Tournament` with its
own participants, seeding, bracket, and champion, so the existing bracket engine
(`lock_and_build`, the drain path, all five formats) is reused verbatim with no
structural change.

**Each bracket seeds from its own Conference's Standings.** The Match corpus is
scoped by the `Match.conference` FK ADR-0034 provided, not by the Season-wide
table. This is what corrects the contradiction above.

**All three seeding modes split.** In a ≥2-Conference Season every `tournament`
phase builds per-Conference brackets regardless of `tournament_mode` —
`standings` seeds from that Conference's Standings, `strength` ranks that
Conference's teams by mean rating, `unseeded` shuffles that Conference's teams.
There is no cross-Conference fixture of any kind before Worlds, so the
intra-Conference invariant needs no exception clause.

**The linkage is two additive nullable FKs on `Tournament`**, mirroring the
`Match.conference` discriminator precedent rather than deriving the relationship
from a join:

- `Tournament.season_phase` — FK → `SeasonPhase`, nullable,
  `related_name="regional_tournaments"`, so a phase can enumerate its own
  brackets (which is what makes "all N drained" a clean query).
- `Tournament.conference` — FK → `Conference`, nullable, `SET_NULL`.

`SeasonPhase.tournament` keeps its exact current meaning and is left untouched:
a zero- or one-Conference Season still builds one Season-wide bracket and stores
it there, so every existing row, read path, and query — including the Part2c-3f
Team-History chain `match__series_match__node__tournament__season_phases__isnull=False`
— is byte-identical. One additive migration, both columns nullable, no backfill
([ADR-0004](0004-simulation-data-is-disposable.md) disposable-data posture).

**Completion and the champion.** The phase does not advance until **every** one
of its N regional brackets has drained, mirroring how an RR phase completes only
when every Conference's round-robin is done. `Season.champion_team` **stays
NULL** through CONF-02 — CONF-01's rule survives intact, because a regional
champion is not a Season champion. Only Worlds (CONF-04) crowns one.

**A regional playoff produces exactly one Conference champion**, being its
Tournament's `champion`. The plural "regional qualifiers" wording in PLAN.md is
retired here: how many Teams per Conference reach Worlds is CONF-03's question,
and CONF-03 is free to define it from whatever source it needs. CONF-02 does not
invent a placement ranking the engine cannot supply.

## Alternatives considered

**One Tournament with per-Conference sub-brackets.** Would have kept
`SeasonPhase.tournament` a single FK with no new linkage. Rejected: the bracket
engine has no notion of disjoint sub-brackets within one Tournament — building
one would be the deferred `TournamentSubGroup` work from LG-02x-2 — and each
Conference's winner would stop being a Tournament champion, losing the natural
handle CONF-03 needs.

**N sibling `tournament` phases, one per Conference.** Would have needed no model
change at all, reusing the existing single FK N times. Rejected: the play loop
advances phases strictly in `ordinal` order, so N regional playoffs would
serialise — Nevada's bracket only starting after California's finished. That
contradicts the parallel-on-a-shared-Matchday-calendar rule the per-Conference
round-robins already follow.

**Adding only `Tournament.conference`, with `SeasonPhase.tournament` pointing at
the first bracket.** The smallest migration. Rejected: the phase could not
enumerate its own brackets, leaving "all N drained" without a clean query and
making the completion gate fragile.

## Consequences

- A ≥2-Conference Season now runs genuinely regional playoffs, and can no longer
  build a cross-Conference bracket by accident. This is a **behaviour change for
  multi-Conference Seasons with a `tournament` phase** — previously one
  Season-wide bracket, now N regional ones.
- Zero- and one-Conference Seasons are untouched in every respect: same single
  bracket, same `SeasonPhase.tournament` link, same champion stamping.
- One additive migration with two nullable FK columns; no backfill, no data
  migration.
- `Season.champion_team` remains NULL for completed multi-Conference Seasons —
  now through the playoff phase as well as the round-robin phase. Any surface
  that renders a Season champion must keep tolerating NULL until CONF-04.
- Per-Conference Standings logic, which CONF-01 placed in the `season_standings`
  view, is now needed on the model too (for seeding). CONF-02 adds it as an
  additive `conference=None` parameter on the existing model-side derivation
  rather than refactoring the shipped view, so the two Conference-scoped queries
  coexist for now; consolidating them is left as a follow-up rather than
  widening this slice's blast radius into the CONF-01 standings surface.
- CONF-03 inherits a clean input: one `Conference champion` per Conference, each
  reachable as `tournament.champion` off the phase's `regional_tournaments`.

## See also

- [ADR-0034](0034-conference-partition.md) — the Conference partition, the
  intra-Conference invariant, and the `Match.conference` discriminator this
  slice consumes.
- [ADR-0023](0023-season-phase-composable-structure.md) — the `SeasonPhase`
  ordered-typed-phase model and the tournament-phase build path being generalised.
- [ADR-0019](0019-tournament-bracket-model.md) — the persisted standalone-sandbox
  Tournament model whose engine the regional brackets reuse unchanged.
- [ADR-0004](0004-simulation-data-is-disposable.md) — disposable-data /
  no-backfill posture for the additive nullable columns.
- CONTEXT.md **Regional playoff** / **Conference champion** / **Conference** /
  **Season phase** / **Standings**.
- PLAN.md **CONF-02 · Per-Conference regional playoffs** (and CONF-03 / CONF-04,
  which consume this slice's output).
