# Member nights are season-attached social play, excluded from Standings

**Status:** Accepted (LG-07 grill, 2026-06-29)

## Context

PLAN.md **LG-07** revives the `member_night` **Season phase** type — declared
but inert in `SeasonPhase.PHASE_TYPE_CHOICES` since LG-02-Part2a
([ADR-0023](0023-season-phase-composable-structure.md)), rejected by the composer
parser, and returning `False` from `Season._phase_complete` (so a composed
member-night phase would **park the season cursor forever**). A member night is
the real-world casual/social session a laser-tag venue runs between competitive
fixtures — ad-hoc games among *whoever shows up*, organised per **Site**
(`Player.home_site`), not a structured round-robin or bracket.

The LG-07 grill (2026-06-29) resolved how member-night games relate to the
Season they sit inside. Two prior decisions framed the choice:

- **The playoff precedent (Part2c-1 #3 / Part2c-3f).** Embedded-tournament
  Matches were deliberately left `season=NULL, season_phase=NULL` and surfaced in
  history through the `SeasonPhase → Tournament` FK chain — **specifically to
  avoid** stamping the season FK and then excluding those Matches from every
  season-scoped query (`_final_standings_for_phase`, `_rr_phase_complete`,
  `_is_finished`, the LG-06g Standings-form / side-split corpora). That blast
  radius is named verbatim in the Part2c-3f addendum as the reason the
  attribute-and-exclude path was rejected.
- **Derived completion (ADR-0023).** Phase completion is *derived, never stored*
  — there is deliberately no `SeasonPhase.state` field. RR ⇔ all fixtures played;
  tournament ⇔ `tournament.state == "completed"`.

## Decision

A `member_night` phase's games are **Season-attached and Standings-excluded**:
each game's `Match` carries **`season=<this>` AND `season_phase=<member-night
phase>`**, and the Standings / phase-completion / per-Season-player-stat corpora
**exclude** member-night Matches with the single predicate
`.exclude(season_phase__phase_type="member_night")`.

This **diverges from the playoff `season=NULL` FK-chain precedent on purpose.**
The maintainer wants member-night games to be first-class season-scoped Matches —
discoverable in raw `Match.objects.filter(season=...)` listings and team game
logs as *"yes, this happened in Season N"* — while being kept out of the
competitive **Standings** table specifically. The FK-chain alternative
(`season=NULL`, discover via `phase.matches`) was offered twice during the grill
and declined: it keeps the games out of season history entirely, which is the
opposite of the intent here.

The exclusion blast radius is **smaller than the playoff case feared**:

- `_rr_phase_complete` scopes `match__season_phase=<a specific RR phase>`, so
  member-night Matches (whose `season_phase` is the member-night phase) never
  match — **safe by construction, no edit**.
- `_is_finished` only checks that each RR `ScheduleFixture` key is present in the
  played-keys set; extra member-night rounds in the set are harmless — **no edit
  required for correctness** (an exclusion may be added for cleanliness).
- The real exclusion sites are `_final_standings_for_phase` (and its
  `compute_standings` callers — dashboards, PLAY-01 live polling) and the LG-06g
  Standings-form / Side-split corpora.

**Completion stays derived (ADR-0023 honoured — no `SeasonPhase.state`).** A
`member_night` phase is complete **iff** at least one member-night Match exists
for it **AND** every member-night Match for it is `is_completed`. Before the user
runs the night there are zero member-night Matches → the first clause is false →
the phase is incomplete → the cursor **parks** on it (correct: the user must run
the member night). After the run, all generated games are complete → the phase
completes → the cursor advances. This supports **play-time** Site selection
(pick one Site, or all Sites present) with no stored marker.

**The games reuse the LG-02x-1 drawn-team machinery, not `compute_draw`.** A
member-night run gathers a Site's pool from the Season's enrolled-Team Players
plus the League's free-agent pool, filtered to `home_site == site`. It draws
**5–9** games; each game splits **12** drawn players into two *attempt-balanced*
6-player Teams with randomized **Roles**, creating `is_draw_team` Teams that
**borrow** the real Players (so `PlayerRoundState` references the real Player and
career stats stay unified). `compute_draw` is **not** reusable — it requires
`len(pool) % 6 == 0` and `≥ 24`; the member-night split is its own balanced
2-team draw over a 12–18 pool. The role shuffle reuses
`build_random_role_assignment`. The pool-size (12–18), game-count (5–9), and
balanced-split draws consume a **fresh `random.Random()`** per run — member
nights are **non-deterministic by design** (the unseeded mid-season-draw
precedent, Part2c-3c) and sit **outside** the SIM-07/08 seed chain ⇒ **no Score
Calibration re-baseline**.

**The phase is a play-loop barrier.** A `member_night` phase between
`round_robin` phases halts the season (the `_tournament_barrier_ordinal`
mechanism, generalised to also halt on an incomplete `member_night` phase) until
it is run and complete, then the later RR phases become playable.

**The per-Season player-stat screens gain a member-nights filter.** Rather than
unconditionally excluding member-night `PlayerRoundState` rows from the
season-scoped player-stat screens (Player Stats, League Leaders, Statistical
Feats, Team-History Players tab), those screens carry a *member-nights* selector
(include / exclude / only — the LG-06d `?provenance=` filter precedent) so the
viewer chooses whether casual games appear. **Standings** are unconditionally
excluded (a member night never moves the table).

## Rejected alternatives

### `season=NULL` + FK-chain history (the playoff precedent)

Leave member-night Matches `season=NULL`, discover them via `phase.matches`. Zero
exclusion blast radius and the proven Part2c-3f pattern — but it keeps the games
out of `Match.objects.filter(season=...)` season history, which is the opposite
of the maintainer's intent ("these games happened in this Season"). Declined
twice during the grill in favour of the season-attached choice above.

### `season=<this>` + flip Standings to read RR-only positively

Stamp `season=<this>`, then invert the standings/completion corpora to read
`season_phase__phase_type="round_robin"` positively instead of `season=self`.
One conceptual change across the same ~5 sites; equivalent in effect to the
`.exclude(...member_night)` predicate but a larger semantic rewrite of working
queries. The narrower `.exclude` predicate was preferred (it also leaves room for
*other* future non-RR phase types without re-touching every site).

### Pure marker phase (no games)

Make `member_night` a documented-but-inert completable slot with no play surface.
Smallest slice, but a member night that plays no games is pointless — rejected.

### Stored completion flag

A `SeasonPhase.member_night_done` boolean the user sets. Simplest cursor story
but reintroduces the per-phase state field ADR-0023 deliberately refused; the
derived `≥1 game AND all complete` rule makes it unnecessary.

## Consequences

- **No new model field is strictly required for the core** — `Match.season` /
  `Match.season_phase` (Part2c-2) and `Team.is_draw_team` (LG-02x-1) already
  exist; a member-night game is an existing-shaped Match stamped with both FKs
  and played by drawn Teams. (A small per-Match Site marker may be added for
  display/filter convenience; the completion derivation needs none.)
- `Season._phase_complete` gains a `member_night` branch (`≥1` member-night Match
  for the phase **AND** all `is_completed`); the play-loop barrier generalises to
  halt on an incomplete `member_night` phase; the composer parser stops rejecting
  `member_night` and the create-League composer offers it (the "coming soon"
  placeholder goes live).
- The Standings / completion corpora gain `.exclude(season_phase__phase_type=
  "member_night")`; `_rr_phase_complete` and `_is_finished` need no change.
- The season-scoped player-stat screens gain a member-nights include/exclude/only
  filter; the global all-time career page (HX-01) is unaffected (league-agnostic).
- A new pure module (`matches/member_night.py`, Django-free) owns the balanced
  2-team split + game-count / pool-size draws; `build_random_role_assignment`
  (LG-02x-1) is reused for roles.
- **Non-deterministic, no Score Calibration re-baseline** — the run consumes a
  fresh `random.Random()` outside the SIM-07/08 seed chain and changes no
  simulation mechanic.
- A new **Member night** + **Site** glossary pair lands in CONTEXT.md
  `### League and seasons`.

## See also

- [ADR-0023](0023-season-phase-composable-structure.md) — the `SeasonPhase`
  ordered-typed-phase model + the derived-completion / no-state-field rule this
  builds on (and the playoff `season=NULL` FK-chain decision this deliberately
  diverges from).
- [ADR-0022](0022-random-draw-player-pool-tournament.md) — the LG-02x-1
  drawn-team / `is_draw_team` / random-role machinery the member-night games
  reuse.
- [ADR-0004](0004-simulation-data-is-disposable.md) — disposable-data /
  no-backfill posture.
- CONTEXT.md **Member night** / **Site** / **Season phase** / **Standings**.
- PLAN.md **LG-07 · Member night simulator**.
