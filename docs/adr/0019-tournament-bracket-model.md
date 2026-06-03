# Tournament as a persisted, results-contingent Bracket model

**Status:** Accepted (LG-02a, 2026-06-02)

## Context

PLAN.md LG-02 ("Tournament formats") was scoped as a single monolithic
task spanning eight formats (single/double elimination, round-robin,
round-robin→double-elim, Swiss, Random Draw, Duos, Trios) plus a
`TournamentSubGroup` model and a visual bracket. The LG-02 grilling
session (2026-06-02) reframed and sliced it, and in doing so hit a hard
collision with the existing League/Season foundation that this ADR records.

### The framing collision

The pre-LG-02 glossary and [ADR-0014](0014-league-season-foundation.md)
had already taken a position: *"a Tournament is a bracketed format that
runs **inside a Season**, not its own container"* — i.e. a Season with a
`schedule_format` like `tournament_round_robin_then_double_elim` would
chain a flat league phase into a bracket, with the `schedule_format` enum
as the extension point.

But two things the user wanted do not fit that framing:

1. **A standalone sandbox tournament.** The user wants a "Tournaments"
   tab in **sandbox mode** (alongside Batch Sim) — pick a format,
   import/select/generate Teams, run the bracket game-by-game or all at
   once, with eventual "batch the whole tournament N times to see win-%".
   This is a self-contained competition with *its own* Teams, **not** a
   phase of an existing League Season.
2. **Results-contingent fixtures.** A knockout bracket's later matchups
   depend on earlier *results* — the semifinal's participants are unknown
   until the quarterfinals are played.

### The architectural collision

[ADR-0015](0015-schedule-on-demand-no-fixture-rows.md) makes a Season's
schedule a **pure deterministic function of the enrolled `team_ids`** with
**no persisted fixture rows** — `generate_schedule(team_ids,
schedule_format)` returns the entire fixture list up front, recomputed on
every render, and Match rows are find-or-created at play time. That model
works precisely *because* a round-robin fixture list is fully knowable from
the team set alone.

A single-elimination bracket violates that premise. The fixture list cannot
be enumerated from `team_ids` up front — it is built round by round as
winners **Advance**. The `generate_schedule` "no fixture rows, deterministic
from the set" approach therefore cannot model a bracket. ADR-0015 itself
anticipated this ("the `schedule_format` extensibility surface is one branch
in `generate_schedule`") but never confronted results-contingency.

## Decision

**A Tournament is a standalone, persisted, results-contingent Bracket
model in the `matches` app, decoupled from League/Season** — *not* a Season
`schedule_format` value, and *not* routed through `generate_schedule`.

Concretely (LG-02a, single-elimination first slice):

- Three new persisted models — `Tournament`, `TournamentParticipant`
  (a Team + its **Bracket seed**), and `BracketNode` (one structural slot
  in the **Bracket**, holding two nullable Team slots, a nullable `Match`
  FK, an `advances_to` self-pointer, and an `is_bye` flag).
- The **Bracket is persisted as `BracketNode` rows** the moment the
  Tournament is locked (`Tournament.lock_and_build()`, state
  `setup → active`). First-round slots are filled by **Seeding**; later
  slots start NULL and are filled by **Advancement** as each node's `Match`
  is played. This is the deliberate inverse of ADR-0015's no-fixture-rows
  Season decision — a bracket *must* persist its structure because that
  structure is the only place the contingent tree lives.
- **Node = one existing `Match`** (two game **Rounds**, colour swap,
  winner by the existing `Match.calculate_winner`). A tied Match
  (`winner_id IS NULL`) is broken **best single-Round score → higher
  Bracket seed** so a node always yields a decisive advancer — a Standings
  tie cannot stand in a bracket.
- Bracket **structure, Seeding, bye placement, and the tie-break math**
  live in a pure module `matches/bracket.py` (no Django imports, the
  `standings.py` / `schedule_generator.py` precedent + `TestNoDjango
  ImportsLeaked`). Django objects are converted to plain ints/dicts at the
  view/model boundary.

### Why persisted, not ephemeral (the session-blob alternative)

Game-by-game play across HTTP requests needs the bracket to *remember* who
won the previous node and to have advanced them. A queryable tree is also
what the visual bracket render reads, and the eventual "batch the tournament
N times" feature wants a reusable Tournament *definition*. A `request.session`
blob (the Batch Sim model) would make the tree-render fragile, reconstructing
"which Match was the semifinal?" guesswork, and the future batching feature
much harder. The persistence cost is one migration and three forward-only
models.

## Rejected alternatives

### Tournament as a Season `schedule_format` (the ADR-0014 framing)

Model the bracket as another `generate_schedule` branch on a Season.
Rejected because (a) a sandbox tournament has no League/Season to attach to,
and (b) `generate_schedule` is contractually a pure function of `team_ids`
that returns the *whole* fixture list — it structurally cannot express
results-contingent advancement without being rewritten into a stateful,
results-aware thing, which would corrupt the clean LG-01 Season model. The
in-Season *embedding* of a tournament (a Season whose flow includes a bracket
block) is deferred to **LG-02 Part 2** and will compose this same
`Tournament`/`BracketNode` model as a block, rather than forcing the bracket
through `generate_schedule`.

### Ephemeral session-only bracket (the Batch Sim model)

Keep the bracket in `request.session`, persist only the played Matches.
Rejected — see "Why persisted" above: fragile tree render, no reusable
definition for batching, awkward deep-linking. The disposability ADR-0004
grants (sim data can be thrown away, no backfill) is satisfied by a
forward-only model just as well as by a session blob.

### `winner_to`-less bracket reconstructed from Matches

Persist only `Match` rows tagged with a tournament id + round, and rebuild
the tree topology at render time. Rejected because the topology
(which node feeds which) is exactly the contingent structure that is
expensive to reconstruct and easy to get wrong; persisting it explicitly as
`BracketNode.advances_to` makes Advancement a single pointer-write.

### Re-simulate to break a tied node

When a node's `Match` ties, run another Match until someone wins. Rejected
in favour of the deterministic **best-Round-score → higher seed** tiebreak:
re-simulation is non-deterministic relative to a single replay, costs extra
CPU, and a sudden-death extra **Round** would be a Round with no colour-swap
pair (breaking the Match invariant). The deterministic rule is defined for
every case and replay-faithful.

## Consequences

- One migration creates `Tournament` + `TournamentParticipant` +
  `BracketNode`; no backfill, no `RunPython` ([ADR-0004](0004-simulation-data-is-disposable.md)
  precedent). Sandbox-only — no League/Season schema touched.
- `matches/bracket.py` joins `standings.py` / `schedule_generator.py` /
  `season_dashboard.py` as a pure, DB-free, `TestNoDjangoImportsLeaked`-guarded
  module. Bracket structure + Seeding + byes + tie-break are unit-testable
  with zero DB.
- The existing `BatchSimulator.simulate_match` is consumed **verbatim** —
  no simulator change, no RNG-contract interaction, **no Score Calibration
  re-baseline**.
- The `schedule_format` extension point on `Season` is **not** used for
  tournaments and stays single-valued (`single_round_robin`) until LG-02
  Part 2 composes Tournament blocks into a Season's flow.
- LG-02 is sliced: **LG-02a** (this — sandbox single-elimination, persisted
  bracket, game-by-game sync play, select/generate teams, arbitrary-N + byes,
  overall-rating + manual Seeding) is the foundation every later format and
  the Part 2 in-Season embedding build on. Deferred follow-ups (CSV import,
  async play-all, best-of-N series, double-elim / round-robin / Swiss,
  Random Draw / Duos / Trios + `TournamentSubGroup`, batch-N, Part 2
  composer) inherit this model.

## See also

- [ADR-0014](0014-league-season-foundation.md) — the League/Season model
  whose "Tournament runs inside a Season" framing this ADR revises for the
  standalone sandbox case.
- [ADR-0015](0015-schedule-on-demand-no-fixture-rows.md) — the deterministic,
  fixture-less Season schedule this ADR deliberately diverges from for the
  results-contingent Bracket.
- [ADR-0004](0004-simulation-data-is-disposable.md) — disposable-data /
  no-backfill precedent for the new models.
- CONTEXT.md `### Tournaments` — the Tournament / Bracket / Bracket round /
  Bracket node / Bracket seed / Seeding / Advancement / Bye glossary.
- PLAN.md LG-02 (Part 1 sandbox tournaments — LG-02a + follow-ups; Part 2
  in-Season composable structure).
- Seam contract `.claude/worktrees/lg-02a-seam-contract.md`.
