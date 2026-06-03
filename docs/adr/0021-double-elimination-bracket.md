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

## See also

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
