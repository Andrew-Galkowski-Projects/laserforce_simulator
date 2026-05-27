# Schedule computed on demand, no `ScheduleEntry` rows

**Status:** Accepted (LG-01, 2026-05-26)

## Context

The LG-01 grilling session locked **round-keyed scheduling** for the new
single-player league mode: each pair of enrolled Teams plays exactly one
**Match** (per [ADR-0014](0014-league-season-foundation.md)), but the two
**Rounds** of that Match are scheduled separately in time — round 1 in
the first half of the Season, round 2 in the second half, so the team
roster can change between them.

For an N=8 single-round-robin Season this means:

- 28 unique Match pairings × 2 Rounds each = **56 fixtures**
- Distributed across **14 matchdays** (every team plays once per matchday)
- Matchdays 1–7 host every pair's round 1; matchdays 8–14 host every
  pair's round 2.

The schedule itself — *which* fixture happens on *which* matchday — has
to be encoded somewhere. Three shapes were considered:

1. **Pre-created `Match` rows** — the moment a Season transitions `draft
   → active`, write N·(N−1)/2 empty `Match` rows with `is_completed=
   False` and a new `Match.scheduled_round1_date` / `_round2_date` field.
   The schedule page is `Match.objects.filter(season=X)`.
2. **Separate `ScheduleEntry` model** — one row per fixture (matchday,
   round_number, team_a, team_b, scheduled_date), with a nullable
   `match` FK that gets populated when the Round is actually played.
   Schedule page is `ScheduleEntry.objects.filter(season=X)`.
3. **Compute on demand** — no persisted fixtures at all. The fixture
   list is a pure function of `(enrolled_team_ids, schedule_format)` and
   is re-derived on every render of the schedule page or every
   resolution of "what is the next Round to play?". `Match` rows are
   find-or-created at the moment a Round is simulated.

The user explicitly chose shape (3): *"B3 is closest, the compute on
every render can be mitigated somewhat with celery and other worker
based distribution of work."*

This ADR records the decision and the constraints it imposes on the
rest of the LG-01 foundation.

## Decision

The fixture list is **computed on demand** from the enrolled team ids
and the Season's `schedule_format`. No `ScheduleEntry` table, no
pre-created empty `Match` rows. `Match` rows are find-or-created the
moment a Round is simulated.

### Pure module

A new pure module `matches/schedule_generator.py` exposes one function:

```python
def generate_schedule(
    team_ids: list[int],
    schedule_format: str = "single_round_robin",
) -> list[ScheduleFixture]:
    """Return the deterministic fixture list for these enrolled teams."""
```

`ScheduleFixture` is a frozen dataclass:

```python
@dataclass(frozen=True)
class ScheduleFixture:
    matchday: int        # 1-based
    round_number: int    # 1 or 2
    team_a_id: int       # the team in the team_red slot of the underlying Match
    team_b_id: int       # the team in the team_blue slot
```

The pure module is **pure Python, no Django imports, no RNG, no I/O** —
the standard LG-00 / LG-00b / HX-01..04 / RES-04 pure-module
discipline, including a defensive `TestNoDjangoImportsLeaked` subprocess
check.

The output is **sorted deterministically** by `(matchday, team_a_id)`
and the input `team_ids` are **sorted ascending** before running the
circle/polygon scheduling algorithm — so the fixture list is a function
of the *set* of team ids, not of insertion order.

### Algorithm — single round-robin, first-half / second-half split

- N teams. If N is odd, append a phantom "bye" id; teams paired with
  the phantom skip that matchday (standard sports-league handling).
- Run the **circle method**: fix one team, rotate the others; matchday
  k pairs the fixed team with `rotating[0]` and pairs the rest
  symmetrically. Produces N−1 matchdays of round-1 fixtures, each
  matchday with N/2 (or (N−1)/2 with one bye) fixtures.
- Mirror: matchdays N..2N−2 replay the same pairings as round 2 in the
  same matchday-relative order.

For N=8: 14 matchdays, 4 fixtures per matchday, 56 fixtures total.
Matchdays 1–7 are every pair's round 1; matchdays 8–14 are every pair's
round 2.

### Dates are a derived display layer

The pure module emits **no dates**. The view layer derives display
dates as `season.start_date + (matchday - 1) * 7 days` (hard-coded
7-day cadence for v1; a `matchday_cadence_days` field can be added
later when "season options" expand).

### `Match` find-or-create at Play-Round time

The simulator entry point `BatchSimulator.simulate_scheduled_round(
season, team_a, team_b, round_number, *, arena_map=None)` (locked in
ADR-0014) is the only path that materialises a Season Match row:

- Round 1 call: `Match.objects.create(season=…, team_red=team_a,
  team_blue=team_b, is_completed=False)` then simulate round 1.
- Round 2 call: `Match.objects.get(season=…, …)` — Side-agnostic
  lookup by `frozenset({team_red_id, team_blue_id})` — then simulate
  round 2 with args reversed.

### Schedule page rendering

The schedule page (`/seasons/<id>/schedule/`) renders by:

1. Calling `generate_schedule(season.starting_team_ids,
   season.schedule_format)` to get the fixture list.
2. Querying the persisted `GameRound`s for this Season and indexing them
   by `(team_red_id, team_blue_id, round_number)`.
3. Overlaying — each fixture is either *unplayed* (no matching
   `GameRound`) or *played* (link to the `GameRound` detail page with
   final score).

### "Next unplayed Round" resolution

The "Play Next Round" button (LG-01d) resolves the next fixture by
iterating `generate_schedule(...)` in order and returning the first
fixture whose `GameRound` doesn't exist yet. Same overlay logic as the
schedule page; one extra step to find the head of the queue.

### Season completion detection

The `simulate_scheduled_round` writer, after persisting each Round,
checks whether **every** fixture in `generate_schedule(...)` now has a
matching `GameRound`. If yes, it flips `state=active → completed`,
computes Standings, and stamps `champion_team_fk`. This is the
auto-completion rule from ADR-0014, made concrete here.

## Rejected alternatives

### Pre-created `Match` rows at Season activation

The earliest framing — when a Season transitions `draft → active`,
write all N·(N−1)/2 `Match` rows up front, each with two new
`scheduled_round1_date` / `_round2_date` `DateField`s. Schedule page
is one `Match.objects.filter()` query.

Pros: schedule page is trivially a DB query; no re-compute cost; the
"what was supposed to be played" is an immutable audit trail.

Cons:
- Adds two new columns to the heavily-used `Match` model just to carry
  schedule metadata that's a deterministic function of two other
  fields.
- Pre-creates 56-ish empty rows per Season that may never be played
  (if the user abandons the Season). The pollution is mild but real.
- Locks in a particular `schedule_format` at activation time. If a
  future version wants to *re-derive* the format (e.g. "rerun the
  schedule generator under a different format starting next matchday"
  — a feature the user hinted at: "Eventually we want to have options
  for how the seasons are run with multiple options where stuff would
  function differently"), the persisted rows are wrong and need
  migration.
- Forces every fixture's `(team_a, team_b)` to be persisted as
  `Match.team_red` / `team_blue` — but the Match's "red" assignment is
  meaningful only at round-1 time. The fixture's team-pair is
  Side-agnostic; the Match's red/blue is Side-specific. Conflating
  them in one row is an awkward fit.

Rejected.

### Separate `ScheduleEntry` model

A new model `ScheduleEntry(season, matchday, round_number, team_a,
team_b, scheduled_date, match)` — one row per fixture, with the
`match` FK nullable until the Round is played.

Pros: separates "what's supposed to happen" from "what did happen";
audit trail is preserved; `match` is null for unplayed fixtures.

Cons:
- A second persisted view of the same deterministic computation. The
  schedule is a *function* of the team list — storing it is redundant
  unless we're prepared for the team list to drift (and ADR-0014
  freezes the team list via `starting_team_ids_json`, so it doesn't).
- Adds a model + a migration + a serializer + admin registration
  burden for data that's a pure derivation.
- Bigger blast radius for "options for how seasons are run" — every
  format change is a `ScheduleEntry` migration / regeneration step.

Rejected.

### Pure module emits dates directly

The pure module takes `season.start_date` + cadence and returns
fixtures with concrete `date` fields populated.

Pros: one-stop shop; view layer doesn't have to derive dates.

Cons: requires the pure module to import `datetime` and do date
arithmetic — which is fine — but couples the pure module to the
project's *display* concerns (the 7-day cadence is a UX choice, not a
scheduling-algorithm choice). Better separation: pure module owns
*order*, view owns *display*.

Rejected. The pure module returns matchday-indexed fixtures with no
dates; the view derives display dates.

### Interleaved round 1 / round 2 within the Season

Instead of "all round 1s in first half, all round 2s in second half",
interleave so the round 2 of pair (A, B) happens roughly N−1 matchdays
after round 1 — same gap, just smeared.

Cons: complicates the algorithm. The user's stated reason for split
scheduling — "the lineups may change between then" — is best served
by the *maximum* gap, which is the strict first-half / second-half
split. Interleaving compresses the gap for some pairs (the ones
scheduled near the half-way mark) and expands it for others.

Rejected. First-half / second-half split.

### Cache the schedule between requests

The schedule is a pure function of one JSON list + one string, both
read off the same Season row. A Django cache (`cache.get_or_set(...)`)
or a request-level memoisation could trivially avoid recomputing on
back-to-back hits.

Decision: **not yet**. The pure module is fast (microseconds for
N=8..16; the circle method is O(N²)). The user explicitly waved off
the compute cost ("can be mitigated somewhat with celery and other
worker based distribution of work"). If a perf measurement later
shows the recompute dominating a hot path, add a cache then; it's a
reversible add. Don't pay the cache-invalidation tax now.

## Consequences

- One new pure module `matches/schedule_generator.py`. Zero schema
  changes for the schedule itself.
- The Match `season` FK (ADR-0014) is the only persistence link
  between a Season and its Matches; the schedule is reconstructed by
  joining the pure module's output against `GameRound` queries.
- The schedule page and "Play Next" resolution both re-derive the
  fixture list on each request. For N up to ~32 this is sub-millisecond
  pure Python; deferred caching is an option if a future format is
  expensive enough to warrant it.
- The `schedule_format` extensibility surface is one CharField + one
  new branch in `generate_schedule(...)`. Adding a future format is
  one PR with the algorithm, the choices update, and tests — no
  migrations of historical data, no schedule-entry table to rewrite.
- Tests pin the algorithm purely (no DB). The two-tier check —
  "fixture list is internally consistent" and "every persisted
  `GameRound` matches a fixture" — is mechanical to write.

## See also

- [ADR-0014](0014-league-season-foundation.md) — the League/Season
  model decision that this ADR rests on; ADR-0014 locks the model,
  this ADR locks the algorithm surface.
- HX-03 `matches/h2h_stats.py`, HX-04 `matches/player_h2h_stats.py`,
  LG-00 `teams/player_generator.py`, LG-00b `teams/roster_importer.py`
  — pure-module precedents (no Django imports, defensive
  `TestNoDjangoImportsLeaked` subprocess check).
- PLAN.md LG-01 (this task) and LG-01a..g (UX sub-tasks consuming the
  schedule).
