# Worlds is a derived Season phase of its own, and the bracket floor drops to two for it

Status: accepted

CONF-03 settled *who* goes to Worlds and handed over a single seam —
`Season.worlds_qualifiers() -> list[WorldsQualifier]`, derived on demand,
ordered tier-first, seeded 1..M, and all-or-`[]` so an empty list means "not
ready" rather than a partial field. CONF-04 has to put a bracket behind it,
drain that bracket, and finally resolve the NULL-champion rule that CONF-01,
CONF-02 and CONF-03 each deliberately left open for a ≥2-Conference Season.

We decided that **Worlds is a `SeasonPhase` of its own**, appended after the
last per-Conference `tournament` phase, carrying `phase_type="tournament"` and a
fifth `SeasonPhase.tournament_mode` value, `"worlds"`. Its bracket hangs off the
`SeasonPhase → Tournament` embed pointer with **both CONF-02 linkage columns
NULL** (`season_phase` and `conference` unset on the Tournament, and
`qualifier_stage` left at `""`) — structurally identical to the closing playoff
of a flat, zero-Conference Season. Because of that shape, three existing
behaviours carry it with no change at all: `tournaments_for_phase` returns
`[phase.tournament]`, `_tournament_phase_complete` requires that single bracket
be `"completed"`, and `_stamp_champion_for_final_phase`'s existing
no-regional-rows branch stamps `Season.champion_team` from
`phase.tournament.champion`.

The phase is **derived, never authored**. It has no composer wire token and no
UI control; `Season.start_season()` appends it, once the Conference snapshots
are frozen, for exactly those Seasons that have **≥2 Conferences and at least
one non-`worlds` `tournament` phase**. Because every input it reads is frozen at
activation, calling the same idempotent helper later produces the identical row,
so `activate_pending_tournament_phase()` calls it too — the same
recovery-hook role CONF-03 gave that method for unseeded Last-chance brackets.
A Season already in flight when this ships therefore gains its Worlds phase on
its next scheduled Round rather than needing a data migration. The next-Season rollover, which otherwise
copies every phase forward verbatim, **skips** it — the rollover carries no
Conferences forward, so a copied Worlds phase would land on a flat Season whose
`worlds_qualifiers()` returns `[]` forever and strand it at `active`.

`Season._final_tournament_phase()` — CONF-03's "which phase does qualification
read?" lookup — is **narrowed to exclude `worlds`-mode phases**. Without that
one line, appending Worlds silently redirects qualification at Worlds' own
empty bracket.

Finally, the pure bracket builders' `len(participants) < 4` guard becomes a
keyword-only `minimum` defaulting to `4`, and the Worlds build alone passes
`minimum=2`.

## Considered options

**Where the Worlds Tournament lives.** Hanging a third Tournament row off the
*existing* final tournament phase (alongside the regional and Last-chance rows,
discriminated by a new `qualifier_stage="worlds"`) needs no new phase and no
ordinal placement. It was rejected on two counts. It lands inside
`phase.regional_tournaments`, which `tournaments_for_phase` orders by
`conference__ordinal` — and the Worlds row's `conference` is NULL, which sorts
first on SQLite and last on PostgreSQL, making the bracket's drain and render
position **backend-dependent** in a project whose two supported backends are
exactly those (ADR-0025). It also forces a rewrite of
`_stamp_champion_for_final_phase`, whose `if regional: … return` is the very
line that encodes "a Regional playoff crowns no Season champion" (ADR-0035).

A fourth `SeasonPhase.phase_type` value, `"worlds"`, is the strongest possible
discriminator — a Worlds phase could never be mistaken for a regional one. It
was rejected as too broad: every site keyed on `phase_type == "tournament"`
(phase completion, phase activation, the Play Single Round and Play Playoffs
entry points, both drain loops, the Playoffs screen and the dashboard helper)
would need a parallel branch, and each of those branches is a place for the two
kinds of tournament phase to drift apart. `tournament_mode` already exists as
the "what flavour of tournament phase is this?" axis and follows the
declared-then-lit rhythm of `standings` / `strength` / `unseeded`.

**When the phase is created.** Author-composing it in the create-League builder
was rejected because the Conference count is not settled at create time —
CONF-05's Manage Conferences page can clear the partition entirely after the
fact, leaving a composed Worlds phase stranded on a flat Season. Creating it
lazily, at the moment `worlds_qualifiers()` first returns a field, was rejected
for a race: `complete_if_finished()` gates on `ordered_phases()[-1]`, so at the
instant the regional phase completes it *is* the final phase, the Season flips
to `completed` with a NULL champion, and the Worlds phase would appear after the
Season had already closed. Start Season is the earliest moment at which every
input — the Conference partition and the phase composition — is frozen.

**Non-power-of-two and small fields.** `M` is the sum of per-Conference
qualifier counts (1, 2 or 3 each) and is routinely 5, 7 or 9. PLAN.md framed
this as an open choice between byes and a play-in round, but the choice was
already made: `bracket.build_bracket` has handled arbitrary `N ≥ 4` since LG-02a
by rounding up to the next power of two and giving the top `size − N` seeds
pre-resolved round-one bye nodes. Worlds inherits that for free, and a play-in
round would have been a second, redundant mechanism.

What PLAN.md did *not* anticipate is a field **below** four. Two Conferences of
2–4 Teams send one qualifier each, so `M = 2` — and an 8-Team, 2-Conference
league is precisely what CONF-05's create form produces by default. Skipping
Worlds for such a Season (leaving it byte-identical to CONF-03, completed and
championless) was rejected: the most common Conference setup a user can create
would silently never crown anyone. Declaring a bracket-less walkover, stamping
`worlds_qualifiers()[0]` as champion, was rejected because it invents a second
champion-stamping path outside `_stamp_champion_for_final_phase` and plays no
Worlds Match at all, leaving the Playoffs screen with a Worlds panel and nothing
behind it.

Lowering the floor instead costs one keyword-only argument. The pure builders
are already correct below four — `n = 2` yields a size-2 bracket with a single
node, which *is* the Worlds final, and `n = 3` yields a size-4 bracket in which
seed 1 byes into it. The `< 4` guard is inherited sandbox-form policy, not a
property of the maths. Every existing caller keeps the default of `4`, so the
sandbox `/tournaments/` surface, the regional builds and the Last-chance builds
are unchanged.

**Crossing the phase boundary.** Making Worlds its own phase puts it behind a
boundary the drain loops do not cross. `play_season_task` and
`play_playoffs_task` each resolve `season.current_phase()` and cache
`tournaments_for_phase(phase)` once — a comment in both explicitly forbids
re-reading cached state without re-resolving — and bracket Matches run through
`tournament_engine.play_next_node`, not `simulate_scheduled_round`, so the
`activate_pending_tournament_phase` hook that fires after every scheduled Round
goes quiet the moment the regular season ends. Left alone, a "Play whole season"
run would stop with the regionals drained and Worlds unbuilt, and the next Play
Single Round click would fall through to the round-robin path and do nothing.

We follow CONF-03's seed-then-continue precedent rather than restructuring the
loops. A public `Season.build_pending_worlds_bracket() -> bool` is called at the
same hook sites as `seed_pending_last_chance_brackets`; in the two task loops it
joins the existing stall branch, and when it returns `True` the loop
**re-resolves** `phase` and `tournaments` and continues instead of breaking. An
outer per-phase loop would fix this and any future multi-tournament-phase
composition in one move, but it would re-thread PLAY-01's cooperative cancel
checks and `play_season_task`'s stage-budget arithmetic through a new level of
nesting — a rewrite of the two functions whose caching is most load-bearing, for
a generality nothing yet needs.

**Owner mood.** `_classify_playoffs_for_team` picks the first `tournament`
phase whose `tournament_id` is set. A regional phase leaves that NULL, so a
≥2-Conference career League has always scored `("none", 0, 0)` on the
owner-mood playoff axis — and the Worlds phase, which does set it, would have
switched that axis on by accident, classifying every Team that failed to
qualify as `"missed"` and taking the full penalty regardless of how far it got
in its own region.

We classify **two-tier** instead, and do it entirely in the classifier:
`num_rounds` becomes the Team's whole possible path — the rounds in its own
Conference's Regional playoff plus the rounds in the Worlds bracket — and
`rounds_won` counts the distinct bracket rounds it won across both. Winning
Worlds still returns `"champion"`; being cut from the regional bracket still
returns `"missed"`. `owner_mood.py` is **not touched**: its `"seeded"` branch is
already depth-proportional, so feeding it the longer path yields the intended
ladder — Worlds champion above Worlds finalist above Conference champion above
regional finalist above first-round exit — with no new result values and no new
constants. Inventing per-tier constants was rejected because CAR-02's existing
values are faithful to ZenGM's `updateOwnerMood` and a new ladder would have no
such source; summing two independent per-bracket deltas was rejected because it
doubles the axis range in both directions against a `FIRE_THRESHOLD` tuned for
a single bracket. The Last-chance bracket is deliberately excluded from both
the numerator and the denominator, so it cannot push a Team past the maximum
path its Conference offers.

## Consequences

A ≥2-Conference Season now crowns a Season champion, closing the rule CONF-01
opened and CONF-02 and CONF-03 each carried forward. Every surface that renders
a champion may keep tolerating NULL — a Season with no `tournament` phase to
qualify from still ends championless — but the common case now resolves.

A Season already active when this slice ships gains its Worlds phase through
the activation recovery hook on its next scheduled Round — no `RunPython`, no
backfill migration, ADR-0004 intact. A Season that had already completed stays
completed and championless.

`SeasonPhase.tournament_mode` gains a choices-only value, which Django
materialises as an `AlterField` migration with no database-level effect. No
`Tournament` column is added: CONF-03's locked read rule — every read tests
`qualifier_stage == "last_chance"` and nothing else — survives untouched,
because the Playoffs screen identifies the Worlds bracket from
`phase.tournament_mode` rather than from anything on the Tournament row.
