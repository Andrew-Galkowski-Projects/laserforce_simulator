# Conferences partition a Season's Teams into intra-conference regional leagues

**Status:** Accepted (CONF-01 / SUB-01-piece-3 grill, 2026-06-29)

## Context

PLAN.md's **SUB-01 piece 3** ("first-class sub-league + per-sub-league rotating
map pools") was framed around *map* pools, with the schedule-generation
interaction — "intra-pool vs cross-pool fixtures must be a first-class scheduling
concept (a fixture between two sub-leagues has no single pool)" — flagged as the
risky core needing its own grill + an ADR + a new model.

The CONF-01 grill (2026-06-29) reframed the feature around the maintainer's
actual target: the **ZenGM "worlds" game type**. Multiple **regional leagues**
(e.g. *California*, *Nevada*) each play their own regular season; top finishers
qualify into a single cross-region **Worlds** tournament that crowns the season
champion. The canonical term is **Conference** (retiring the previously-reserved
"Sub-league"). The full target lifecycle is: per-Conference regular-season
round-robin → per-Conference **regional playoff** → qualifiers → cross-Conference
**Worlds** Tournament.

This is a multi-slice epic. **CONF-01 ships only the foundation**: the Conference
partition model, intra-Conference round-robin scheduling, and per-Conference
Standings. Regional playoffs, Worlds qualification + tournament, the create-League
Conference composer, and per-Conference map pools are sequenced as later slices.

The key insight: because Conferences play **intra-Conference only** during the
regular season (a Team meets only the Teams of its own Conference), **the
cross-pool map-resolution ambiguity that worried the original PLAN never arises** —
a regular-season fixture is *always* within exactly one Conference. The hard part
is not scheduling; it is per-Conference Standings and (later) the
top-N-per-Conference Worlds qualification seeding.

Two prior model facts framed the design:

- **The activation-snapshot pattern.** Schedule determinism rests on snapshotting
  the team set at `start_season()` (`Season.starting_team_ids_json`), so mid-cycle
  admin edits cannot drift the schedule.
- **The discriminator-FK pattern.** Part2c-2 added `Match.season_phase` (and
  Part2c-3a `Match.leg`) as explicit nullable discriminators so per-phase
  Standings / completion / find-or-create are robust rather than join-derived.

## Decision

A **Conference** is a named, Season-level partition of a Season's enrolled Teams
into a disjoint competitive group, modelled as a new `Conference` row
(`season` FK CASCADE, `name`, `ordinal`, `teams` M2M subset, and an activation
snapshot `starting_team_ids_json`). A Season has **zero** Conferences (the default —
one implicit all-Teams group, byte-identical to a flat single-table Season) or
**two or more** (each a disjoint subset).

**Conference is orthogonal to Season phase.** A `round_robin` phase generates one
round-robin **per Conference**, not one across the whole Season. The Conferences'
per-phase schedules play in **parallel** on the shared **Matchday** calendar
(California Matchday 1 and Nevada Matchday 1 are the same calendar week); within an
RR phase each Conference keeps its own `1..span` numbering on the global offset,
and the phase's span — the offset applied to the next phase — is the **largest**
Conference's span. This keeps the `start_date + (matchday-1)*7` date derivation
coherent.

**Intra-Conference only — no new map-resolution ambiguity.** There are no
cross-Conference regular-season fixtures, so every fixture's Conference is
**derivable from its teams** (both are in the same Conference). The Django-free
`ScheduleFixture` dataclass is therefore **unchanged** — `scheduled_fixtures_by_phase`
concatenates the per-Conference round-robins and the play loop resolves each
fixture's Conference via a `team_id → Conference` map. `scheduled_fixtures`,
`_fixtures_for_phase`, and `_rr_phase_complete` are unchanged: an RR phase completes
only when **every** Conference's round-robin is done (it already checks all of the
phase's fixtures).

**`Match.conference` is an explicit discriminator FK** (nullable, `SET_NULL`,
stamped at find-or-create time exactly like `Match.season_phase` / `Match.leg`),
chosen over join-derived "both teams in this Conference" scoping. Per-Conference
Standings read `Match.objects.filter(conference=conf)`; the FK future-proofs the
per-Conference regional playoffs. It is stamped on the Round-1 `Match` create and
left out of the find-or-create **key** (a pairing is already unique within a
phase — the two teams share one Conference and meet only there), so the existing
key `(season, season_phase, frozenset(teams), leg)` is unchanged.

**Per-Conference Standings.** The `season_standings` page renders one independently
ranked table per Conference (stacked, name-headered). A zero-Conference Season
renders the existing single table byte-identically.

**Multi-Conference champion is NULL until Worlds.** A multi-Conference Season
completes when every Conference's round-robin finishes, but `champion_team` stays
**NULL** — there is no legitimate cross-Conference champion until the Worlds
qualification/tournament slice lands (Conferences have disjoint schedules; a
cross-Conference record comparison is meaningless — deciding it on the field is the
whole point of Worlds). A **zero- or one-Conference** Season still crowns
`compute_standings(...)[0]` exactly as today.

**Conferences are admin-created this slice.** Mirroring the Part2a `SeasonPhase`
foundation (whose composer was deferred to Part2b), CONF-01 ships the model +
`ConferenceAdmin` + the scheduling/Standings wiring; the create-League Conference
composer is a later slice.

## Rejected alternatives

### Cross-pool fixtures with a tiebreak map rule

A full Season round-robin where some fixtures are cross-Conference, resolving the
"no single map pool" ambiguity with a fallback/home-team rule. Rejected: the
maintainer's worlds structure is intra-Conference by nature (regions play
themselves, then meet only at Worlds), so cross-pool fixtures and their ambiguity
never need to exist.

### Membership-derived Match scoping (no `Match.conference`)

Scope per-Conference Standings by `team_red__in / team_blue__in` the Conference's
ids — no Match column. Works (scheduling is intra-Conference) and avoids touching
Match, but it is join-heavier, diverges from the `season_phase` / `leg`
discriminator pattern, and is less robust for the future per-Conference playoffs.
Declined; the Conference model already requires a migration, so adding the FK to it
is marginal.

### Sequential per-Conference calendar

Nevada's round-robin plays entirely after California's on the matchday calendar.
Simpler offset math but doubles the season length and reads oddly (a region idle
for half the year). Rejected for the parallel overlay.

### Best-overall-record champion for multi-Conference Seasons

Crown the best cross-Conference record so `champion_team` is always non-null.
Rejected: disjoint schedules make it apples-to-oranges; NULL-until-Worlds is honest.

## Consequences

- New `Conference` model + `Match.conference` FK in one migration
  (`0057_conference_match_conference`), **no `RunPython` / backfill**
  ([ADR-0004](0004-simulation-data-is-disposable.md)); existing Seasons have zero
  Conferences and are byte-identical to today.
- `Season` gains `ordered_conferences()`, `_scheduled_conference_partitions()`,
  `conference_by_team_id()`; `scheduled_fixtures_by_phase()` partitions per
  Conference with the parallel-overlay calendar; `start_season()` snapshots each
  Conference's `starting_team_ids_json`; `_stamp_champion_for_final_phase` leaves
  `champion_team` NULL for a `>= 2`-Conference RR-final Season.
- `simulate_scheduled_round` gains a keyword-only `conference=None` (stamped on the
  Round-1 create); the three play-loop sites pass it via `conference_by_team_id()`.
- `season_standings` + `templates/seasons/standings.html` render per-Conference
  tables; `_build_dashboard_context` scopes its top-3 snippet to the manager's
  Conference when `>= 2` Conferences exist.
- **No Score Calibration re-baseline** — no simulation mechanic changes; the only
  shift is which `Match` a Round attaches to + a new discriminator.
- The **Conference** glossary term lands in CONTEXT.md `### League and seasons`
  (retiring the reserved "Sub-league"; the Site / Map mode cross-references are
  updated).
- **Deferred to later CONF slices:** per-Conference regional playoffs;
  top-N-per-Conference Worlds qualification + the cross-Conference Worlds
  Tournament (reusing the `round_robin_double_elim` bracket engine); the
  create-League Conference composer; per-Conference rotating map pools (the
  original PLAN map lens — "Nevada games on the Nevada map, with override").

## See also

- [ADR-0023](0023-season-phase-composable-structure.md) — the `SeasonPhase`
  ordered-typed-phase model + the schedule chokepoint
  (`scheduled_fixtures_by_phase`) this partitions; Conference is the orthogonal
  team-partition axis.
- [ADR-0015](0015-schedule-on-demand-no-fixture-rows.md) — the on-demand
  `generate_schedule` algorithm the per-Conference round-robins reuse unchanged.
- [ADR-0004](0004-simulation-data-is-disposable.md) — disposable-data /
  no-backfill posture.
- CONTEXT.md **Conference** / **Season phase** / **Standings** / **Matchday** /
  **Site**.
- PLAN.md **SUB-01 piece 3 / CONF-01 · Conference foundation**.
