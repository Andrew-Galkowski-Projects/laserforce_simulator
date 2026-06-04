# Double-elimination as a two-bracket extension of the single-elim node tree

**Status:** Accepted (LG-02c, 2026-06-03)

## Context

PLAN.md **LG-02c+** bundles four additional bracket formats (double
elimination, round robin, round-robin→double-elim, Swiss) and states each
"is its own grill rather than a variant of single-elim." The LG-02c grilling
session (2026-06-03) scoped this first slice to **double elimination only**.

The existing tournament model ([ADR-0019](0019-tournament-bracket-model.md),
[ADR-0020](0020-best-of-n-series-bracket-nodes.md)) is built for a single
tree:

- `BracketNode` is a tree node — `(bracket_round, position)`, two seeded
  Team slots, `is_bye`, a single `advances_to` self-FK + `advances_to_slot`
  (where the **winner** goes), and a `series_length` resolved at lock by
  depth-from-final.
- `Tournament.format` is an extensible-but-single enum
  (`single_elimination`), exactly so a new format slots in as a new enum
  value + a new pure builder without re-architecting.
- The pure `matches/bracket.py` owns structure/seeding/byes/series math
  (`build_bracket`, `find_next_node`, `advance_winner`, `resolve_bye_chain`,
  `series_length_for_round`); the LG-02b `SeriesMatch` through-model + the
  per-Match-atomic `tournament_engine.play_next_node` drive play.

Double elimination breaks the single-tree premise in three ways, each a
genuine design fork resolved in the grill:

1. **A node's *loser* now has a destination.** In single-elim a loser is
   simply eliminated. In double-elim a Winners-bracket loser **Drops** into
   a specific Losers-bracket slot; only a Losers-bracket loss eliminates.
2. **The field is two coupled trees, not one.** A Winners bracket and a
   Losers bracket joined by a Grand final. The `(bracket_round, position)`
   coordinate is no longer unique on its own.
3. **The Grand final is conditional.** The grill chose **bracket reset (true
   double-elim)**: the Winners-bracket champion must be beaten twice, so a
   second Grand final (GF2) exists only if the Losers-bracket champion wins
   GF1.

## Decision

**Model double elimination as a second `format` enum value driven by a new
pure builder, hosting both sub-brackets in the *existing* `BracketNode`
table — extended with a sub-bracket tag and a loser-destination pointer —
rather than a new model or a separate Losers-bracket table.**

Concretely (LG-02c first slice):

- **`Tournament.format` gains `("double_elimination", "Double elimination")`.**
  Single-elim Tournaments are byte-unchanged.
- **`BracketNode` gains a sub-bracket discriminator** (winners / losers /
  grand_final) and a **loser-destination** pointer (`loser_advances_to`
  self-FK + `loser_advances_to_slot`) paralleling the existing winner
  `advances_to` / `advances_to_slot`. Winners-bracket nodes set both
  pointers; Losers-bracket nodes set only `advances_to` (their loser is
  eliminated, `loser_advances_to` is NULL). The node identity becomes
  (sub-bracket, `bracket_round`, position) — the uniqueness constraint is
  widened to include the sub-bracket tag.
- **A new pure `build_double_elim_bracket(participants)`** in
  `matches/bracket.py` emits the full two-tree node-spec list for arbitrary
  **N ≥ 4 with byes**: the Winners bracket is the existing single-elim tree
  (size = next power of two ≥ N, top `(size − N)` seeds get WB byes); the
  Losers bracket consumes WB-round losers via a **naive same-position drop
  map** (loser of WB-round-*r* position *i* drops into the matching LB slot
  by position — no anti-rematch folding this slice, a known limitation
  deferred to a follow-up); GF1 and GF2 are emitted at build time. The pure module stays Django-free
  (`TestNoDjangoImportsLeaked`), crossing the view/model boundary as
  ints/dicts only.
- **Loser Drop reuses the winner-advance machinery.** `advance_winner`'s
  parent-slot-mutation shape is generalised so the engine writes the loser
  into its `loser_advances_to` slot the same way it writes the winner into
  `advances_to`. The bye cascade `resolve_bye_chain` is generalised to also
  collapse **Drop byes** (an empty LB slot whose feeding WB node was a Bye,
  so produced no loser).
- **GF2 is built but conditionally inert.** Both GF1 and GF2 persist at lock.
  When the WB champion wins GF1 the champion is stamped immediately and GF2
  auto-resolves without ever being played (the `is_bye`-style auto-resolution
  precedent); GF2 plays only when the LB champion wins GF1.
- **Series escalation reuses the four depth slots, anchored to the Grand
  final.** Each DE node's depth is its distance to GF1: GF1/GF2 = depth 0
  (Final slot), the WB final + LB final = depth 1 (Semifinals slot), and so
  on. The depth→slot dispatch is factored out of `series_length_for_round`
  into a shared `series_length_for_depth(depth, *, final, semifinal,
  quarterfinal, earlier)` consumed by both formats.
- **`tournament_engine.play_next_node` is generalised, not forked.** The
  same per-Match-atomic clinch/advance loop drives both formats; on a node's
  clinch it Advances the winner *and* (for a WB node) Drops the loser, then
  stamps champion on a resolved Grand final.

## Rejected alternatives

### A separate `LoserBracketNode` model / second table

Give the Losers bracket its own model. Rejected — the LB node is
structurally identical to a WB node (two seeded slots, a Series, a
winner-advance pointer); the only new thing is *who feeds it* (a WB loser),
which is a pointer on the **WB** node, not a new shape on the LB node. A
second table would duplicate every column, every constraint, and force the
engine/render to branch on node type at every step. One table + a
sub-bracket tag keeps `find_next_node`, the `_node_to_dict` flatten, the
`SeriesMatch` join, and the tree render single-sourced.

### Single Grand final, no reset

One winner-takes-all Grand final node. Rejected by the grill — it discards
the Winners-bracket champion's earned advantage (an unbeaten team could exit
on a single loss), which is the defining fairness property of double
elimination. The conditional GF2 is the cost of getting that right, and it
reuses the existing auto-resolution machinery.

### Power-of-two-only field (defer byes)

Restrict the first slice to N ∈ {4, 8, 16, 32} so the Losers bracket is
fully populated with no byes. Rejected by the grill in favour of matching
single-elim's arbitrary-N flexibility. The cost is the **Drop-bye cascade**
(an LB slot whose feeding WB node was a Bye has no incoming loser and must
collapse) — explicitly flagged as the highest-risk area of this slice and
the place to concentrate test coverage.

### Per-node arbitrary Series-length override for DE

Let each DE node pick its own Series length, sidestepping the depth mapping.
Rejected — it abandons the LG-02b-2 escalation contract users just learned;
reusing the four depth slots keeps one mental model across both formats. The
only new work is defining DE depth as distance-to-Grand-final.

## Consequences

- One migration: the `format` enum choice, the `BracketNode` sub-bracket tag
  + loser-destination fields, and the widened uniqueness constraint. **No
  `RunPython`, no backfill** ([ADR-0004](0004-simulation-data-is-disposable.md)
  precedent); existing single-elim Tournaments are unaffected (sub-bracket
  defaults to winners, loser pointer NULL).
- `matches/bracket.py` gains `build_double_elim_bracket` +
  `series_length_for_depth`; `advance_winner` / `resolve_bye_chain` are
  generalised to carry the loser path and Drop-bye collapse. Purity guard
  unchanged.
- `tournament_engine.play_next_node`, the `SeriesMatch` clinch engine, the
  async play-all task, and `BatchSimulator.simulate_match` are consumed with
  minimal generalisation — **non-deterministic per-Match sims, no SIM-07/08
  interaction, no Score Calibration re-baseline** (the LG-02a/b precedent).
- The bracket-tree render gains a second column-group (the Losers bracket)
  and the Grand-final stage; the detail view exposes both sub-brackets.
- The remaining LG-02c+ formats (round robin, RR→double-elim, Swiss) stay
  deferred and now have a precedent for "new format = new `format` enum value
  + new pure builder," reusing this loser-destination + two-tree generalisation
  where they overlap (RR→DE composes this DE bracket as its finals phase).
- The Grand-final **Bracket reset** is the first conditionally-built stage in
  the tournament model; the "build both, auto-resolve the unused one" pattern
  is reusable for any future contingent node.

## Extension — Round robin (LG-02c round-robin slice, 2026-06-03)

The second LG-02c+ format. Where double elimination *added* edges (the loser
Drop) to the tree, **round robin removes them entirely**: every Team plays
every other, there is no Advancement, and the champion is the **Standings**
leader after the last game — not a bracket final. This bends the ADR-0019
premise that a Bracket is a *results-contingent tree*, so it is recorded here
rather than treated as a routine new builder.

**Decisions (grill, 2026-06-03):**

- **`Tournament.format` gains `("round_robin", "Round robin")`.** Single- and
  double-elimination Tournaments are byte-unchanged.
- **Reuse the existing `BracketNode` table as a *flat, edge-less* graph.**
  One node per pairing, both team-slots **fixed at lock time** (not filled by
  Advancement), `advances_to = NULL` / `loser_advances_to = NULL`,
  `is_bye = False`, `series_length = 1`. A fourth `bracket_type` value
  `"round_robin"` discriminates the nodes (with a `_BRACKET_RANK` entry); the
  `format` field is the primary switch the engine/detail branches read.
- **Reuse `generate_schedule` for the pairings, NOT a new bracket builder.**
  The grill chose the **double round-robin** (each pair meets twice) — the
  full `generate_schedule(team_ids)` fixture list verbatim, one `BracketNode`
  per fixture (leg `round_number==1` → crosstable cell [a][b], leg 2 →
  [b][a]). This deliberately reuses the **Season**'s deterministic pairing
  generator. It does **not** violate the CONTEXT "Bracket-is-not-a-schedule"
  rule: that rule forbids routing *results-contingent advancement* through the
  deterministic path, and round-robin has no advancement. The prohibition is
  hereby scoped to *advancement formats* (the CONTEXT Tournament/Bracket
  _Avoid_ notes were revised to say so).
- **Champion + ranking reuse `matches/standings.py::compute_standings`.** The
  same `league_points → round_wins → total_score → team-name` ladder a Season
  uses; the view builds its 9-key match dicts + `season_rounds` from the
  played nodes' Matches/GameRounds. Standings ties break on **team name**
  (the `compute_standings` final tiebreaker) — the grill rejected a
  seed-aware tiebreak for the first slice (Bracket seed handoff to seeding is
  an RR→DE concern, deferred).
- **Completion is an RR branch in `play_next_node`, parallel to
  `Season.complete_if_finished`.** The elimination "final node decided" rule
  (`advances_to_id is None ⇒ crown`) is wrong for round-robin (every node has
  `advances_to = None`); instead the Tournament completes only once **every**
  node is resolved, at which point the champion is stamped from
  `compute_standings`. `find_next_node` is unchanged — its predicate (both
  slots filled, not bye, Series unclinched) already picks the next unplayed
  RR node, and its sort collapses to `(bracket_round, position)` for a single
  `bracket_type`.
- **Series escalation does not apply.** No final, no depth ⇒ every node is
  Bo1; the four create-time Series-length selects are hidden/ignored when
  `format = round_robin`.
- **The detail page renders an N×N crosstable + a live Standings table, not a
  tree.** `tournament_detail` branches on `tournament.format`.

**Rejected (RR slice):** a separate `RoundRobinGame` model (rejected — an RR
pairing *is* a `BracketNode` with no edges; a second table would fork the
engine, the `SeriesMatch` join, and the play loop for no structural gain); a
single round-robin (rejected in favour of the double, for standings robust to
single-game variance); a seed-aware standings tiebreak (deferred to RR→DE).

**Consequences (RR slice):** one migration widening the `format` enum and the
`bracket_type` choices (**no `RunPython`, no backfill**); `matches/bracket.py`
gains a thin round-robin builder (or the lock path consumes `generate_schedule`
directly — a code-time seam decision) and the `_BRACKET_RANK["round_robin"]`
entry; non-deterministic per-Match sims ⇒ **no SIM-07/08 interaction, no Score
Calibration re-baseline**. The remaining LG-02c+ formats (RR→double-elim,
Swiss) stay deferred; RR→DE will compose this round-robin as its seeding phase
feeding the ADR-0021 DE bracket as its finals phase.

## Consequences / follow-up — RR→double-elim deferred Finals build (LG-02c RR→DE slice, 2026-06-03)

The third LG-02c+ format, **`("round_robin_double_elim", "Round robin → Double
elimination")`**, composes the round-robin **Seeding stage** (the LG-02c
round-robin extension above) with this DE bracket as its **Finals stage**. It
introduces one decision worth recording against this ADR:

- **The Finals bracket is built *lazily*, not at `lock_and_build`.** Every other
  format — single-elim, double-elim, round-robin — builds its full bracket (or
  flat node set) at lock, because its participants are known the moment the
  Tournament locks. The RR→DE Finals are **results-contingent**: the WB starters
  and LB pre-seeds are the *top of the round-robin Standings*, which do not exist
  until the Seeding stage finishes. So the RR→DE lock builds **only** the
  round-robin Seeding nodes (byte-identically to the round-robin format), and a new
  **`Tournament.build_de_finals_if_rr_finished()`** (`@transaction.atomic`,
  guarded + idempotent) builds the Finals **when the last Seeding node resolves** —
  seeding the WB/LB from `round_robin_standings()` rank. This is the second
  conditionally-built stage in the tournament model (the first being the Grand-final
  **Bracket reset** above), and it reuses the same "build when the trigger fires,
  leave the Tournament `active`" discipline — here the trigger is "all RR nodes
  resolved" rather than "GF1 won by the LB champ". The Tournament stays
  `state="active"` across the seeding→finals transition; the champion is still
  crowned by the DE Grand final via the unchanged `play_next_node` crown block.

- **A configurable WB/LB/eliminated split, not the whole field.** Unlike a plain DE
  (which admits all N participants, with byes), the RR→DE Finals take only the
  top-ranked teams: `wb ∈ {4, 8, 16}` advance to the Winners bracket and
  `lb ∈ {0, wb//2}` pre-seed the Losers bracket (six locked combos —
  `4/0, 4/2, 8/0, 8/4, 16/0, 16/8`), the rest of the round-robin field eliminated.
  Two new `Tournament.PositiveSmallIntegerField`s (`wb_advancers`, `lb_advancers`,
  create-time only) carry the resolved counts; the **SHAPE** is enforced at the
  create form and the **COUNT fit** (`wb <= n`, `wb + lb <= n`) at `lock_and_build`
  (`ValidationError`).

- **Reuses the DE persist/wire machinery, adds one fused pure builder.** A new pure
  **`build_rr_de_finals_bracket(upper_specs, lower_specs)`** emits the Finals
  spec-list: with no LB pre-seeds it **delegates directly to
  `build_double_elim_bracket`** (a plain top-`wb` DE); with `lb = wb//2` it re-tags
  the WB as `winners` and pre-fills LB round 1's slot "a" with the RR-ranked
  pre-seeds (each WB-R1 loser Drops into the matching LB-R1 slot "b") — same LB
  topology as a power-of-two DE, **no byes**, no new import. The persist loop + the
  two advance-edge wiring passes + the `resolve_bye_chain` cascade are **extracted**
  from `lock_and_build` into a shared `Tournament._persist_elim_specs(...)` so both
  the single/double-elim lock path and the deferred Finals build reuse them verbatim
  (single/double-elim `lock_and_build` stays byte-identical). The depth→`series_length`
  escalation, the loser-Drop machinery, and the Grand-final Bracket reset are all
  consumed **unchanged** from this ADR. No new ADR and **no new CONTEXT.md term** (the
  **Round robin → double elimination** term was written at grilling time). Seam
  contract:
  [`.claude/worktrees/lg-02c-rr-de-seam-contract.md`](../../.claude/worktrees/lg-02c-rr-de-seam-contract.md).

## Consequences / follow-up — Swiss per-round deferred build + Buchholz re-rank (LG-02c Swiss slice, 2026-06-04)

The fourth and final LG-02c bracket format, **`("swiss", "Swiss")`**, applies the
"new format = new enum value + reused/new pure seam" precedent yet again (a fifth
`Tournament.format` value, a fifth `BracketNode.bracket_type`,
`_BRACKET_RANK["swiss"] = 4`, two new pure functions in `matches/bracket.py`, one
migration widening the two `choices` tuples + adding `swiss_rounds` — **no
`RunPython`, no backfill**). It records three decisions worth pinning against this
ADR:

- **The build is DEFERRED *per round*, not one-shot.** RR→DE introduced a single
  results-contingent deferred build (the Finals, built when the *last* Seeding node
  resolves). Swiss generalises that: the round-1 fold is built at lock, but **every
  later round is built lazily**, each time the *current round's last node resolves*,
  via **`Tournament.advance_swiss_if_round_finished()`** (`@transaction.atomic`,
  guarded). Where RR→DE's deferred build fires once, Swiss's fires up to
  `swiss_rounds - 1` times — pairings for round *r+1* are computed from the live
  Standings after round *r*, since Swiss pairing is **results-contingent by
  construction**. The Tournament stays `state="active"` across every round boundary;
  the champion is crowned not by a final node (there is none — Swiss is flat and
  edge-less) but by **stamping the Standings leader** when the last round resolves.
  The same `play_next_node` discipline applies: a Swiss node carries
  `advances_to=None`, so the elim "crown when `advances_to is None`" rule would
  wrongly crown on the *first* resolved node — the engine therefore routes Swiss
  nodes to `advance_swiss_if_round_finished()` and `return`s **before** the elim
  advance/crown block (a guard on `node.bracket_type == "swiss"`, alongside the RR
  guard).

- **Even-N only, no byes — and pairing is fold-then-greedy.** Unlike single/double-elim
  (which pad to a power of two with byes), Swiss admits any **even** field and **never
  creates a bye** — an odd count raises `ValidationError("Swiss requires an even
  number of participants.")` at `lock_and_build`. Round 1 is the standard seed
  **fold** (`seed[i]` vs `seed[i + N/2]`); later rounds use a **greedy ranked sweep**
  over the current Standings, pairing each unpaired team with the next it has not yet
  played, with an **allow-rematch fallback** for the trailing teams (no backtracking).
  The round count is `swiss_rounds or ⌈log₂(N)⌉` clamped to `[1, N-1]`, resolved and
  **frozen** at lock.

- **Buchholz is a Swiss-only re-rank layer over the FROZEN `compute_standings`.** The
  shared `matches/standings.py::compute_standings` ladder (`league_points →
  round_wins → total_score → team_name`) is **not modified**. Instead a pure
  **`swiss_buchholz_rerank(rows, opponents_by_team)`** re-sorts its rows on the Swiss
  ladder `league_points → Buchholz → round_wins → total_score → team_name`, where a
  team's Buchholz = the sum of its opponents' final `league_points` across played
  pairings (a rematch counts twice). It is **ORDERING-ONLY** — Buchholz is never a
  displayed/stored column; the re-rank renumbers `rank` 1-based dense and copies every
  other `StandingsRow` field verbatim, using a **stable sort** so the input's
  `team_name asc` survives as the final tiebreak without a name lookup crossing the
  pure int/dataclass seam. The `compute_standings`-input assembly is **extracted** from
  `round_robin_standings()` into a shared `Tournament._standings_over_nodes(node_qs)`
  (RR's behaviour stays byte-identical) and reused by `swiss_standings()`. **Non-
  deterministic** per-Match sims ⇒ **no SIM-07/08 interaction, no Score Calibration
  re-baseline**. No new ADR and **no new CONTEXT.md term** (the **Swiss** + **Buchholz**
  terms were written at grilling time). Seam contract:
  [`.claude/worktrees/lg-02c-swiss-seam-contract.md`](../../.claude/worktrees/lg-02c-swiss-seam-contract.md).

## See also

- [ADR-0015](0015-schedule-on-demand-no-fixture-rows.md) — the `generate_schedule`
  circle method reused for round-robin pairings.
- [ADR-0019](0019-tournament-bracket-model.md) — the persisted,
  results-contingent single-elim Bracket model this ADR extends to a second
  coupled tree.
- [ADR-0020](0020-best-of-n-series-bracket-nodes.md) — the best-of-N Series /
  per-Match-atomic engine reused verbatim for DE nodes, and the
  depth-from-final escalation generalised here to depth-from-Grand-final.
- [ADR-0004](0004-simulation-data-is-disposable.md) — disposable-data /
  no-backfill precedent for the migration.
- CONTEXT.md `### Tournaments` — the revised Bracket / Bracket round /
  Advancement / Bye entries and the new Winners bracket / Losers bracket /
  Drop / Grand final / Bracket reset terms.
- PLAN.md LG-02c+ — the four-format bundle this slices to double elimination.
