# Best-of-N series bracket nodes

**Status:** Accepted (LG-02b, 2026-06-03)

## Context

[ADR-0019](0019-tournament-bracket-model.md) shipped the standalone sandbox
single-elimination Tournament and locked one load-bearing simplification:
**a Bracket node = exactly one `Match`**. A node held a single nullable
`Match` FK; the node resolved the moment that one 2-Round Match was played,
and **Advancement** + the deterministic **best-Round-score → higher seed**
tie-break were proven against a *single* decisive result. That "one Match per
node" floor was deliberate — it let the advancement engine, the tie-break
math, and the game-by-game sync/async play loops be built and tested without
any series-resolution semantics in the way.

LG-02b lifts that floor. The user wants a node to hold a **best-of-N Series**
of Matches — Bo1 / Bo3 / Bo5 — where the node resolves only when one Team
**clinches** the majority of Match wins, then Advances. This re-opens exactly
the semantics ADR-0019 set aside: per-Match records, a derived win tally,
clinch detection, the tie-break's now-per-Match role, and the question of what
the play loop's transaction boundary is when a node spans multiple Matches.

The forcing questions LG-02b had to answer:

1. **Where does the Series live?** A node now maps to 1–N Matches, but `Match`
   is a generic model shared with the League/Season and sandbox-batch surfaces
   — it must not learn about tournaments.
2. **Stored or derived tally?** Counters on the node (`wins_a` / `wins_b`)
   would drift against the underlying Match rows on any partial write.
3. **What is the play step?** ADR-0016 made the League play loop per-Round
   atomic and LG-02a-2 made the Tournament loop per-node atomic. A multi-Match
   node forces a choice: resolve the whole Series in one step, or one Match per
   step?
4. **Does Bo1 stay byte-equivalent to LG-02a?** The default must not perturb
   any existing single-Match Tournament behaviour.

## Decision

**A Bracket node holds a best-of-N Series of Matches via a new `SeriesMatch`
through-model, with a derived (never stored) win tally and a single
per-Tournament Series length; the node Advances only on clinch, and play is
per-Match-atomic.**

Concretely (LG-02b):

- **`SeriesMatch` through-model** (`matches` app, after `BracketNode`):
  `node` FK CASCADE (`related_name="series_matches"`), `match` FK to the
  generic `matches.Match` SET_NULL/nullable, a 1-based `game_number`, and a
  `winner` FK to `teams.Team` SET_NULL — one row per played Series Match.
  `Meta.ordering=["game_number"]` + `UniqueConstraint(node, game_number)`
  (`uniq_seriesmatch_node_game`) pin one row per (node, game) in play order.
  This is the **inverse of ADR-0019's node-holds-the-Match** pointer: the
  per-Match link moves off `BracketNode` onto its own row so a node can carry
  many Matches without the `Match` model learning a tournament concept.
- **The win tally is DERIVED**, never stored. `wins_a` / `wins_b` are computed
  by counting `SeriesMatch.winner` rows per team-slot at read time
  (`_node_to_dict` derives them, the engine recomputes them per step). No
  counter field exists to drift; `SeriesMatch` rows are the single source.
  `node.winner` is stamped only on clinch.
- **A single per-Tournament `series_length`** (`PositiveSmallIntegerField`,
  choices `1`/`3`/`5`, `default=1`), set at create-time and frozen on the
  setup→active `lock_and_build` transition. **`series_length == 1` (Bo1) is
  byte-equivalent to LG-02a** — one Match, clinch threshold 1, identical
  Advancement.
- **Clinch threshold = `(series_length // 2) + 1`** Match wins (Bo1→1, Bo3→2,
  Bo5→3), computed by the pure `matches.bracket.clinch_threshold`. A node is
  playable iff both slots are filled, it is not a bye, and
  `series_winner_slot(wins_a, wins_b, series_length) is None` (Series not yet
  clinched) — this pure predicate **replaces** ADR-0019's
  `winner_id IS NULL AND match_id IS NULL` checks in `find_next_node`.
- **Tie-break stays per-Match.** The unchanged `break_tie` resolves each
  Match's decisive `SeriesMatch.winner` when `Match.winner is None`; odd N
  means a Series can never end level, so there is **no Series-level
  tiebreaker**. **Dead-rubber Matches are never simulated** — the Series stops
  the instant a Team clinches.
- **Play is per-Match-atomic.** `play_next_node` resolves **one Match per
  step** (sim one Match → per-Match `break_tie` → create the `SeriesMatch` row
  → recompute the tally → clinch ⇒ stamp `node.winner` + `advance_winner` +
  champion/completed-on-final, else return the node with no Advancement). This
  **extends [ADR-0016](0016-play-season-job-execution-model.md)** from
  per-node-atomic (LG-02a-2) to per-Match-atomic: the `@transaction.atomic`
  boundary is now a single Series Match, so a mid-Series failure leaves every
  already-played Match committed and the Series resumable.
- **Sides are fixed across the Series** — every Match is
  `simulate_match(node.team_a, node.team_b, match_type="tournament")` with a
  constant argument order (the per-Match red/blue colour swap inside
  `simulate_match` already balances Side within each Match).
- **Non-deterministic.** `simulate_match` draws fresh per-Round seeds, so a
  Series is not master-seed-replayable. **No SIM-07 / SIM-08 interaction and NO
  Score Calibration re-baseline** — no simulation mechanic changed.
- **`BracketNode.match` is dropped with no backfill.** Migration
  `0034_*` runs `AddField(Tournament.series_length)` →
  `CreateModel(SeriesMatch)` → `RemoveField(BracketNode.match)` as pure schema
  ops — **no `RunPython`** ([ADR-0004](0004-simulation-data-is-disposable.md)
  disposable-sandbox precedent; existing sandbox tournaments are regenerable).

## Consequences

- **A node now spans 1–N Matches (2–10 game Rounds).** A Bo5 node is up to 5
  Matches = 10 Rounds; the bracket tree topology, Seeding, byes, and
  `advances_to` wiring from ADR-0019 are untouched — only the node's internal
  resolution changed.
- **The async play-all loop drains per-Match.** `play_tournament_task`'s
  `while play_next_node(...) is not None` loop now iterates once per Match, so
  a Bo3/Bo5 Tournament simply takes more steps to reach a champion. The
  per-Match-atomic commit makes the loop resumable at Match granularity (a
  mid-Series worker death keeps every committed Match).
- **Existing Bo1 Tournaments behave identically.** The `default=1` floor and
  the `series_winner_slot(1, 0, 1) == "a"` clinch-on-first-Match equivalence
  mean every LG-02a-shaped Tournament resolves in exactly one
  `play_next_node` call per node — the only structural difference is the played
  Match lives on a `SeriesMatch` row instead of `BracketNode.match`.
- **`stage_progress` stays Bracket-round-level.** Clinching a Series stamps
  `node.winner`, which is what `stage_progress` already reads, so the
  Bracket-round completion reporting (and the 5-key play-status JSON) needs no
  change.
- **The `matches.bracket.py` purity guard holds.** `clinch_threshold` and
  `series_winner_slot` add no import to the frozen
  `dataclasses`/`typing`/`math`/`collections` allowlist —
  `TestNoDjangoImportsLeaked` still passes.

## Rejected alternatives

### Per-Bracket-round series length (Bo1 early → Bo5 final)

Let the Series length vary by Bracket round so finals are longer than early
rounds. **Deferred to LG-02b-2**, not built here — LG-02b ships a single
per-Tournament `series_length` applied to every node to build the Series engine
(clinch, `SeriesMatch`, per-Match play) once against the simplest config. Per-round
escalation is then a clean config-resolution + UI layer on top of the proven
engine, not a re-open of node-resolution semantics.

### Stored `wins_a` / `wins_b` counters on `BracketNode`

Persist the running tally as two integer fields on the node, incremented as
each Match resolves. Rejected — derive-from-`SeriesMatch` is single-source and
cannot drift; stored counters would have to be kept in lockstep with the
`SeriesMatch` rows on every write path (and re-backfilled if a Match row is
ever corrected), reintroducing exactly the consistency burden the derived tally
avoids. Counting rows is cheap with the `series_matches` prefetch.

### FK-on-`Match` instead of a through model

Add a `tournament_series` / `series_node` FK directly to the generic
`matches.Match` model. Rejected — `Match` is shared with the League/Season and
sandbox-batch surfaces and must stay ignorant of tournament concepts. A
`SeriesMatch` through-model keeps the coupling on the tournament side; `Match`
is consumed verbatim.

### Whole-Series-per-step play

Resolve an entire best-of-N Series in one `play_next_node` call (loop Matches
internally until clinch). Rejected — one-Match-per-step preserves the sandbox
**game-by-game watch UX** (the user can step through and watch each Match of a
Series resolve), keeps the transaction boundary a single Match (extending
ADR-0016 cleanly), and makes a mid-Series failure resumable at Match
granularity rather than re-running the whole Series.

### Home/away (side) alternation across the Series

Alternate which Team plays red/blue per Match of the Series for fairness.
Rejected — the existing per-Match red/blue colour swap inside `simulate_match`
already balances **Side** within each Match, so Series-level alternation adds no
fairness and would complicate the fixed `team_a`/`team_b` argument order the
engine relies on.

### `RunPython` backfill of existing `BracketNode.match` into `SeriesMatch`

Migrate each existing node's single `match` into a `game_number=1` `SeriesMatch`
row before dropping the field. Rejected — sandbox tournament data is disposable
([ADR-0004](0004-simulation-data-is-disposable.md)) and regenerable, so the
`RemoveField` runs as a pure schema op with no data-migration step, matching the
no-backfill precedent ADR-0019's models were created under.

## See also

- [ADR-0019](0019-tournament-bracket-model.md) — the persisted standalone
  single-elimination Bracket model this ADR generalises; its "node = exactly
  one Match" lock is what LG-02b lifts to a best-of-N Series.
- [ADR-0016](0016-play-season-job-execution-model.md) — the per-Round atomic
  play-job precedent this ADR extends from per-node (LG-02a-2) to per-Match.
- [ADR-0004](0004-simulation-data-is-disposable.md) — disposable-data /
  no-backfill precedent for the `RemoveField(BracketNode.match)` schema op.
- CONTEXT.md `### Tournaments` — the **Series** / **Series length** glossary
  terms (added at grilling time) on top of the LG-02a Bracket vocabulary.
- PLAN.md LG-02 Part 1 (LG-02b done; LG-02b-2 per-round escalation deferred).
- Seam contract `.claude/worktrees/lg-02b-seam-contract.md`.
