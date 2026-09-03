# Worlds qualification is size-tiered, and the third slot is decided by a sequential last-chance bracket

Status: accepted

A ≥2-Conference Season ends in a single cross-Conference **Worlds** Tournament
(CONF-04), and something has to decide who is in it. CONF-02 shipped exactly one
**Conference champion** per Conference and — per ADR-0035 — deliberately refused
to rank a bracket's losers, since a single-elimination tree carries no meaningful
ordering below its winner. So "top N per Conference" had no source for `N > 1`.

We decided that **how many qualifiers a Conference sends is a function of its
size**, and that **each slot has its own fixed provenance**:

| Conference size (activation snapshot) | Qualifiers | Provenance of each slot |
| --- | --- | --- |
| 2–4 Teams | 1 | Conference champion |
| 5–8 Teams | 2 | Conference champion; best regular-season finisher not already qualified |
| 9+ Teams | 3 | the above two; plus the winner of a **Last-chance qualifier** bracket |

Size is read from the Conference's `starting_team_ids_json` activation snapshot,
not from the Regional playoff's field — so a `tournament_cut` cannot change how
many Teams a region sends to Worlds. A Conference of 2–3 Teams is too small to
field a Regional playoff at all (`MIN_BRACKET_PARTICIPANTS = 4`, skipped in
`Season._build_tournament_for_phase`) and therefore has no champion; it still
sends its Standings rank-1 Team, recorded with regular-season provenance but
seeded in the champion tier, so **no Conference is ever unrepresented at
Worlds**.

The unioned field is ordered **tier-first** — every Conference champion outranks
every regular-season qualifier, which outranks every last-chance winner — and
**within a tier by regular-season rate** (league points per match played, then
round wins per match, then total score per match, then team id). Rate rather
than raw totals because Conferences differ in size and therefore play different
numbers of games: a 12-team Conference's 11-game total is not comparable to a
5-team Conference's 4-game total.

## Considered options

**Where the second and third slots come from.** Taking a Conference's top N
straight from its regular-season Standings was rejected: it lets a Conference
champion that finished mid-table miss Worlds entirely, contradicting the
Conference lifecycle CONTEXT.md already states. Adding a placement /
elimination-depth ranking API to `Tournament` was rejected as a large new
surface on the bracket engine that ADR-0035 had already scoped out, and because
elimination depth is a poor ranking signal anyway (two teams knocked out in the
same round are not comparable).

**When the last-chance bracket runs.** The tempting design is to fix its field
as regular-season ranks 3–6 the moment the regular season ends, so it drains
**in parallel** with the Regional playoff — which would match the parallel
overlay CONF-01 and CONF-02 give every other Conference-scoped fixture. We
rejected it: a Team in ranks 3–6 that goes on to win the Regional playoff would
already be qualified while still occupying a last-chance slot, wasting it. The
field is therefore **the 4 highest-ranked not-already-qualified Teams**, which
cannot be known until the champion is, making this bracket the one **strictly
sequential** stage in an otherwise fully parallel Conference model. That
sequencing is the single most surprising thing about this slice and is the main
reason this ADR exists.

**How the sequential stage is represented.** Building the last-chance
`Tournament` row lazily, at the moment its Conference's Regional playoff drains,
was rejected because both drain loops (`play_playoffs_task` and
`play_season_task`'s tournament tail) resolve `season.tournaments_for_phase(phase)`
**once and cache it**; a row created mid-drain would be invisible to the loop
that needs to play it, and `Season._tournament_phase_complete` — "every bracket
of this phase has drained" — would report the phase complete before the
last-chance bracket existed, advancing the Season past a stage it never ran.

Instead the row is **created eagerly at phase activation, unseeded**, alongside
the Regional playoff, and seeded later. This is deliberately a state the bracket
engine has not previously had to hold: a `Tournament` in `state="setup"` with no
participants and no nodes. It works because both properties we need fall out for
free — an unseeded bracket is not `state="completed"`, so the phase-completion
gate already refuses to advance; and `find_next_playable_node` returns `None` on
a node-less bracket, so the drain loop already skips it harmlessly. The two
kinds of bracket are told apart by a stamped `Tournament.qualifier_stage`
discriminator (`""` / `"regional_playoff"` / `"last_chance"`) rather than by row
order, following the house precedent of `Match.conference`, `Match.leg` and
`SeasonPhase.tournament_mode`.

## Consequences

- A Conference of 9+ Teams plays **two** brackets in its `tournament` phase, so
  `Season.tournaments_for_phase(phase)` can return more than one Tournament per
  Conference. The Playoffs screen's `key` DOM-id discriminator gains a stage
  suffix for the last-chance entry only, leaving every CONF-02 id unchanged.
- An unseeded last-chance `Tournament` is visible in the standalone
  `/tournaments/` list in `setup` state before its field is known. Accepted:
  the list already shows CONF-02's regional rows.
- The Worlds field size `M` is the sum of per-Conference qualifier counts and is
  routinely **not** a power of two (5, 7, 9 …). Handling that — byes or a
  play-in — is CONF-04's problem, not this slice's.
- The Worlds participant list itself is **derived on demand**, not persisted.
  Only `Tournament.qualifier_stage` is new state; the qualification rule reads
  from Standings and bracket champions that already exist.
- `Season.champion_team` still stays NULL for a ≥2-Conference Season. This slice
  decides who plays for it; CONF-04 crowns it.
