# `PlayerRoundState` counters are deliberate, not denormalised

**Status:** Accepted (2026-05-28).

## Context

`/improve-codebase-architecture` on 2026-05-28 surfaced candidate #5 in
`docs/architecture-improvements.md`: "Collapse `PlayerRoundState` counters into
a derived view." The framing was that the 18 counter fields on
`PlayerRoundState` (`tags_made`, `times_tagged`, `points_scored`,
`missiles_landed`, `specials_used`, `own_specials_cancelled`,
`times_missiled`, `times_tagged_in_reset_window`, …) are "denormalised
duplicates of `GameEvent` aggregates" — two interfaces over one fact, invariant
enforced by convention, classic denormalisation smell. The proposed fix was a
`RoundStatsView` materialised from events at round close, with
`PlayerRoundState` keeping only structural fields (role, team_color,
`final_lives` sentinel).

The framing is wrong, and re-pitching it on every future architecture pass
wastes a review cycle each time. This ADR records why the duplication is
deliberate and pre-empts the suggestion.

The decision matters because:

- The candidate is plausible on a static read of `matches/models.py` — the
  counter fields *do* look like a cached aggregate of `GameEvent` rows.
- The reason they aren't a cached aggregate is non-obvious: it requires
  reading `BatchSimulator`'s tick loop, the in-memory `PlayerState`
  dataclass, `_flush_to_db`, and the per-tick read sites in
  `score_calculator.py` / `simulation.py` / `event_log.py` together.
- The aggregate-vs-directional split between `PlayerRoundState` and
  `GameEvent` is already documented in CONTEXT.md as a load-bearing pattern,
  not an accident.
- Acting on the candidate would be the largest refactor on the
  architecture-improvements list (the candidate itself flags this), and the
  complexity it claims to remove does not actually vanish — it moves.

## Decision

**The 18 counter fields on `PlayerRoundState` stay where they are.** They are
not denormalised cache of `GameEvent` aggregates; they are tick-loop state
that the simulator reads every tick to drive behaviour, written to DB once
per round at `_flush_to_db` time. The aggregate-vs-directional split between
`PlayerRoundState` counters and `GameEvent` rows is the canonical interface
shape going forward: `PlayerRoundState` owns per-round aggregates (cheap O(1)
reads — `prs.tags_made`); `GameEvent` owns directional / temporal /
per-opponent splits (per-pair `SUM ... GROUP BY` reads).

Load-bearing arguments:

1. **`BatchSimulator` is in-memory.** Per `laserforce_simulator/matches/CLAUDE.md`:
   "pure in-memory, no DB writes during the tick loop." Counters on
   `PlayerState` dataclasses and events in the `EventLog` are co-products of
   the same tick loop; both are flushed atomically by `_flush_to_db` at round
   close. There is no source-of-truth conflict at write time — the
   "denormalised duplicate" framing assumes a write order (events first,
   counters derived from events) that doesn't exist. At the tick loop both
   are written in lockstep from the same resolution step.

2. **Counters are load-bearing during simulation, not just analytics.**
   Per-tick read sites today:
   - `matches/sim_helpers/score_calculator.py` lines 35-92 — `calculate_mvp`
     reads `points_scored`, `missiles_landed`, `specials_used`,
     `own_specials_cancelled`, `times_missiled` to compute MVP, which drives
     behaviour (HX-03 "most impactful player", weight tuning).
   - `matches/simulation.py` lines 237-238 and 1644-1645 — team points sum
     `p.points_scored` every tick to evaluate scoring-burst Highlights and
     broadcast triggers.
   - `matches/sim_helpers/event_log.py` lines 51, 62 — every event snapshots
     `actor.points_scored` / `target.points_scored` into metadata (the
     RES-02 SP-snapshot pattern depends on the counter being fresh *now*).
   Re-deriving these from the event log per-tick (or even per-second) would
   dominate the round's ~200 ms cost and has nowhere natural to land in a
   "RoundStatsView materialised at round close" — the snapshot doesn't
   exist yet at the moment the read happens.

3. **`PlayerRoundState` is in-game state, not pure analytics.** The model also
   carries `starting_lives` / `starting_shots` / `starting_special` /
   `starting_missiles`, `final_lives` / `final_shots` / `final_special` /
   `final_missiles` / `medic_hits`, `was_eliminated_at`, `last_downed_time`,
   `shields`, `cell_row` / `cell_col`, `is_hiding`, `special_active_until`,
   `specific_tags`, `last_tagged_id`, `neutral_base_destroyed`,
   `opposing_base_destroyed`, `zone_fallback`, etc. — none of which derive
   cleanly from events. Stripping the model to "structural fields only"
   leaves an awkward residue: half the fields stay because they aren't
   derivable, half move to a derived view, and every consumer has to know
   which is which.

4. **The aggregate-vs-directional split is already documented and
   load-bearing.** CONTEXT.md's **Player head-to-head record** entry says
   explicitly:
   > deriving the per-Round tag counts from `PlayerRoundState.tags_made`
   > (that counter aggregates Tags against **all** enemies; the directional
   > A→B / B→A split lives on `GameEvent.actor`/`target`)

   That is not an invariant-by-convention. It is a documented split: PRS
   owns per-round aggregates (cheap O(1) reads — `prs.tags_made`), GameEvent
   owns directional splits (per-pair `SUM ... GROUP BY` reads). They serve
   different query patterns. The candidate's "two interfaces over one fact"
   framing collapses two genuinely different facts ("how many tags this
   player made in this round" vs. "how many tags A made against B in this
   round") into one.

5. **Deletion test.** Delete the 18 PRS counters and re-derive from events:
   `_flush_to_db` shrinks marginally; but the per-tick reads still need a
   `PlayerCounters` accumulator (you cannot run MVP off DB-flushed events
   when nothing is flushed yet); analytics readers gain a `SUM ... GROUP
   BY` per query that today is a single-row scan; 30+ tests that build
   `PlayerRoundState(tags_made=…)` fixtures need rewrites; the
   `score_averages` management command goes from `p.points_scored` to a
   per-player event aggregation. Complexity moves, doesn't vanish.

## Rejected alternatives

- **Drop the counter columns, derive at read time via `SUM` over
  `GameEvent`.** Rejected per points 3-5 above. Per-tick reads cannot use
  `SUM` because nothing is flushed yet; analytics readers regress from
  single-row scans to `GROUP BY` per query; the model still needs the
  non-derivable structural fields (starting / final resources, eliminated_at,
  positions, hiding state) so the shrink is partial; ~30 fixture-building
  tests get rewritten for no observable behaviour change.

- **Drop the counter columns, recompute at round close into a JSON snapshot
  on `GameRound` ("RoundStatsView").** Rejected — solves the read-time cost
  for analytics callers but breaks per-tick simulator reads (the snapshot
  doesn't exist yet at tick time; MVP / scoring-burst / event-metadata
  snapshotting all happen mid-loop). And the snapshot is just the same
  counters in a different table — moving the same data into a JSONField
  doesn't make the duplication argument any stronger, it just adds a
  serialisation hop.

- **Keep DB columns but force all callers through a derived-view interface.**
  Rejected — adds indirection for zero locality win. The current
  `prs.tags_made` access is the canonical interface; a `RoundStatsView(prs)`
  wrapper around it would be a pass-through with no new invariant to
  enforce.

## Consequences

- **`/improve-codebase-architecture` passes that resurface "collapse
  counters into a derived view" should be answered with a link to this
  ADR.** The candidate's framing (two interfaces over one fact, denormalised
  cache) is foreclosed; future passes that want to revisit must engage with
  the load-bearing arguments above (per-tick reads, in-memory write order,
  documented aggregate-vs-directional split) rather than re-pitching the
  shallow form.

- **`docs/architecture-improvements.md` candidate #5 marked REJECTED** with
  a one-paragraph pointer to this ADR. Candidates #1-#4 stay completed;
  candidate #6 (split `simulation.py`) is unaffected.

- **Aggregate-vs-directional split is the locked pattern for new
  counters.** When a new per-round count is added (e.g. a hypothetical
  `times_shielded`), the default placement is a `PlayerRoundState` field
  (cheap O(1) read at analytics time, written by the tick loop, flushed
  with everything else). Directional / per-opponent / per-second splits
  stay on `GameEvent`. A new counter that *only* needs a directional split
  (no aggregate read site) is the one case where adding a PRS field is
  wrong — but no current counter is in that category.

- **A separate deepening — extracting an in-memory `PlayerCounters`
  dataclass that bundles the 18 counter fields on `PlayerState`** — is a
  real locality win (writers say `player.counters.tags_made += 1`;
  `_flush_to_db` becomes a `**asdict(p.counters)` splat) and is being
  pursued in a separate PR (`arch/player-counters-dataclass`). That
  refactor is **compatible with this ADR**: it changes the simulator's
  in-memory shape without changing the DB schema, the read interface
  (`prs.tags_made`), or the "counters are deliberate" principle. The DB
  columns stay; only the `PlayerState` dataclass's internal grouping
  changes.
