# League/Season foundation (single-player league mode)

**Status:** Accepted (LG-01, 2026-05-26)

## Context

PLAN.md LG-01 was originally scoped as "new `Season` model + match→season FK
+ Standings page". During the grilling session the user reframed the task:
LG-01 is **the foundation for a single-player league simulator**, not a
standalone analytics page. The reframing surfaced three load-bearing
decisions that PLAN.md did not settle and that are hard to reverse once
schema lands:

1. **Container model.** PLAN.md implied a flat `Season` model with M2M to
   `Team`. But "user manages indefinitely" in the single-player career mode
   (CAR-01..03, Phase 5.5) requires Seasons to chain — a user lives across
   *many* Seasons of the same competition with the same enrolled Teams. A
   user can also play *multiple* such competitions simultaneously. PLAN.md
   does not name the container that owns the Season chain.

2. **Standings unit (Match vs Round).** PLAN.md wrote "W/L/T, points
   (3W/1T/0L), round wins, total score" — ambiguous between Match-keyed
   (each completed Match counts once, 3/1/0) and Round-keyed (each Round in
   each Match counts, up to 6 points per Match). Wiring the wrong one
   bakes a different ORM aggregate into the codebase and the templates.

3. **Round-keyed scheduling.** PLAN.md framed schedule generation around
   "Matches linked to season via FK", but the user described a competition
   in which the two **Rounds** of one **Match** are scheduled *separately
   in time* — round 1 early in the Season, round 2 later — explicitly so
   the team roster can change between them. The existing
   `BatchSimulator.simulate_match` runs both Rounds atomically and persists
   the full Match in one transaction. Round-keyed scheduling forces a new
   simulator entry point and a `Match` model semantically capable of
   partial completion.

## Decision

Adopt a three-layer model — **League → Season → Match → Round** — with a
nullable `Match.season` FK, **partial-completable Matches**, and a new
simulator entry point `simulate_scheduled_round` that runs one Round at a
time.

### Layer 1 — `League` (the persistent container)

`League(name, mode, state, created_at)` is the new persistent container.

- `mode` ∈ {`sandbox`, `league`, `multiplayer`}. `sandbox` is reserved for
  legacy / pre-LG-01 flows (never written by LG-01 itself; available as a
  forward-compatibility marker for the LG-01a mode picker). `league` is the
  single-player league mode shipped here. `multiplayer` is deferred to
  Phase 6.
- `state` ∈ {`active`, `archived`}. Archive is reversible (a UI hide, not a
  destructive operation).
- **No FK to a User or Team owner** — there is no User model yet (UX-01 is
  Phase 6). Leagues are global for now; ownership is a forward-compatible
  story added in UX-01 + CAR-01.

A League owns many Seasons in temporal order. Teams are **not** owned by
a League — `Team` stays global so existing sandbox flows (LG-00 generation,
LG-00b roster import, per-team pages) keep working unchanged. A Team can
be enrolled in multiple Leagues' Seasons simultaneously, with enrollment
tracked on each Season's M2M.

### Layer 2 — `Season` (one cycle within a League)

`Season(league_fk, name, start_date, teams_m2m, state, schedule_format,
starting_team_ids_json, champion_team_fk, created_at)`.

- `league_fk` → `League`, `on_delete=CASCADE` (deleting a League cascades
  all its Seasons; the League is the unit of "throw the whole thing away").
- `teams_m2m` → `Team` — the enrolled roster for this cycle.
- `state` ∈ {`draft`, `active`, `completed`}.
- `schedule_format` `CharField(choices=…)` — only `single_round_robin`
  ships in v1; the field is extensible (the user explicitly asked for
  pluggable formats).
- `starting_team_ids_json: JSONField` — frozen ordered list of `team.id`
  snapshotted the moment `draft → active` fires. The schedule algorithm
  reads from this snapshot, **not** from the live M2M, so the fixture list
  is deterministic even if a developer or admin later edits the M2M
  directly. Defence in depth alongside the M2M-frozen rule.
- `champion_team_fk` → `Team`, nullable. Denormalised at `active →
  completed` so the League history page renders without recomputing
  Standings.

### Layer 3 — `Match` (partial-completable)

The existing `Match` model gains exactly two additions:

- `season` `ForeignKey(Season, null=True, blank=True,
  on_delete=models.SET_NULL)`. Sandbox Matches stay `season=NULL` —
  ADR-0004 (disposable-data) precedent — and pre-LG-01 Matches are not
  backfilled.
- **No new columns for partial completion.** The semantic change rides on
  the existing fields: `is_completed` flips to `True` only after the
  *second* Round persists. Round 1 alone leaves the Match with
  `*_round1_*` populated, `*_round2_*` at default 0, and `is_completed=
  False`. `Match.calculate_winner` already gates on `is_completed=True`
  via `save()`, so the existing tiebreaker logic (rounds-won → total
  points → tie) runs only on the round-2 persist, unchanged.

A Season Match is uniquely identified by `(season_id, frozenset({
team_red_id, team_blue_id}))` — Side-agnostic Team-id pairing, mirroring
the RV-01 / HX-03 precedent. The round-2 simulator call finds the Match
by this key after the round-1 call created it.

### State machine

- `draft → active` — explicit "Start Season" action. Validates ≥ 2
  enrolled Teams; snapshots the M2M into `starting_team_ids_json`; locks
  the M2M (subsequent edits forbidden until completion). Returns the
  Season to the user as an active competition.
- `active → completed` — **auto-transition** the moment the last
  unplayed Round persists. The `simulate_scheduled_round` writer
  detects "every fixture from the pure schedule generator now has a
  persisted `GameRound`", flips `state=completed`, computes the
  Standings, and stamps `champion_team_fk` to the top row.
- `completed → ???` — no further transitions. Season is read-only.
- **"Start Next Season" chain** — on `completed`, a League dashboard
  action creates a fresh Season in the same League with `state=draft`,
  inheriting the previous Season's enrolled team list verbatim. The
  user can edit the new Season's draft before activating. This is the
  chain that makes "indefinitely manage" work.

### Standings — Match-keyed, 3/1/0

Standings is **Match-keyed** (each completed Match contributes one outcome
per team; `Match.winner_id == team.id` → W, `IS NULL` → T, else → L), with
**3 W / 1 T / 0 L** league points. Tiebreak ladder when teams tie on
league points: (1) round wins (sum of `Match.red_rounds_won` /
`blue_rounds_won` over the team's Season Matches); (2) total score (the
team's side of `Match.red_total_points` / `blue_total_points`, which
already includes the team-elim bonus); (3) alphabetical by Team name.

Aggregation lives in a pure module `matches/standings.py` —
`compute_standings(…) -> list[StandingsRow]`. Pure Python, no Django
imports, no RNG, no I/O. Mirrors the HX-03 `h2h_stats.py` / HX-04
`player_h2h_stats.py` / HX-01 `career_stats.py` precedent, including the
defensive `TestNoDjangoImportsLeaked` subprocess check.

### Simulator surface

A new entry point `BatchSimulator.simulate_scheduled_round(season, team_a,
team_b, round_number, *, arena_map=None) -> GameRound` is the sole writer
for Season Matches. Behaviour:

- Round 1 call: find-or-create the `Match` row (`season=season`,
  `team_red=team_a`, `team_blue=team_b`, `is_completed=False`); run the
  existing round-1 simulation; persist one `GameRound(round_number=1)` and
  populate `match.*_round1_*`. Match stays `is_completed=False`.
- Round 2 call: find the Match by `(season_id, frozenset({team_a_id,
  team_b_id}))`; run the existing round-2 simulation **with args
  reversed** (preserving the per-Match colour swap verbatim from
  `simulate_match` — `team_red` physically plays blue in round 2); persist
  one `GameRound(round_number=2)` and populate `match.*_round2_*`; set
  `match.is_completed=True` and save (triggering `calculate_winner`).
- `@transaction.atomic`. Each call is one Round; the Match is persisted
  across two calls.

The existing `simulate_match` (both Rounds atomic) is **kept** as the
sandbox-Match entry point (`/matches/create/` POST handler unchanged) —
sandbox Matches with `season=NULL` use it; Season Matches must use
`simulate_scheduled_round`.

## Rejected alternatives

### Single flat `Season` model with no `League` container

The earliest framing — `Season` carries the M2M of Teams directly, no
container above it. Rejected because the single-player career mode
(CAR-01..03) explicitly assumes a user "manages indefinitely" across many
Seasons of the same competition. Without a container, the cross-Season
chain has to be reconstructed by ad-hoc heuristics (matching team lists,
adjacent dates, names) — fragile, undefined behaviour when the M2M drifts
between cycles. A `League` row is one column and a FK; the cost is
trivial and the multi-Season story falls out for free.

### `Team` owned by `League` (FK from Team to League)

zengm-style: each League creates its own Teams. Rejected because it
breaks every existing sandbox flow — LG-00 generation, LG-00b roster
import, per-team pages, the entire `teams` app — all of which assume
Teams are global. A Team being enrollable in multiple Leagues is also
forward-compatible with a future multiplayer / cross-league sharing
story.

### Round-keyed Standings (3 points per Round)

Each Round in a Match counts independently for league points — a 2-0
Match earns 6 points, a 1-1 split earns 2 points each. Rejected because
the user explicitly chose Match-keyed, and because the 3W/1T/0L number
set is universally a *match*-level scheme outside this codebase
(football, hockey, esports league tables). The Round-record-as-secondary
column is preserved (it's the first tiebreaker) without making it the
primary points source.

### Atomic Match simulation with both Rounds back-to-back

The existing `simulate_match` model: each Match is two Rounds run
together in one transaction. Rejected because the user explicitly wants
round 2 of a Match to be schedulable *later* than round 1, on the
grounds that the team roster may change between them. Atomic simulation
forecloses that — round 2 would run against whatever roster existed at
round 1 time, even if weeks of game-time have passed. The new
`simulate_scheduled_round` keeps atomicity at the per-Round granularity,
where it matters.

### Explicit `Match.state` field for partial completion

A `Match.state` `CharField(choices=["scheduled", "round1_played",
"completed"])` to make partial completion explicit. Rejected because
`is_completed` plus the always-zero `*_round2_*` fields already encode
the three states unambiguously, and adding a column to a heavily-used
model (every save, every form, every serializer) is a much bigger blast
radius than the semantic change actually requires. The behavioural rule
"flip `is_completed=True` only when round 2 persists" is enforced in
exactly one new simulator method; the rest of the codebase reads
`is_completed` as it always did.

### `Season.teams` mutable while `active`

Add-only mid-season enrollment: admin can add new Teams to an active
Season. Rejected because adding a Team mid-Season either (a) recomputes
the deterministic schedule, scrambling everyone's already-played
matchdays, or (b) leaves the new Team with no fixtures. Both are bad
UX. The clean rule — M2M frozen at activation — means "to change the
roster, finish/abandon the Season and edit the next Season's draft"
which is consistent with the "Start Next Season" chain and how zengm
itself handles roster shifts (between seasons, not within).

### Auto-activate Season when ≥ 2 Teams enrolled

Skip the `draft` state — Seasons go straight to `active`. Rejected
because the user needs a window to configure (set name, dates, pick
teams from a larger candidate pool, choose schedule_format) before
locking the schedule. The `draft` state is cheap; the early-locking
is surprising.

## Consequences

- One migration creates `League` + `Season`; a second adds `Match.season`.
  Two migrations in one PR, both `AddField`/`CreateModel`, no backfill.
- The existing `simulate_match` flow is untouched; sandbox `/matches/create/`
  POSTs to it as before, with `season=NULL`.
- The new `simulate_scheduled_round` method is the *only* writer for
  Season Matches; `_flush_to_db` is extended to accept `season` + `round_
  number` kwargs so the Match find-or-create is single-source.
- Standings + schedule pages are testable from the pure modules without
  any DB at all — same testing pattern as HX-03 / HX-04 / RES-04.
- LG-01a..g sub-tasks (mode picker, create-league flow, dashboard, Play
  Next, history, team game log) build on this foundation without further
  schema changes.
- CAR-01..03 (manager identity, performance-based firing) layer on top —
  the manager is a separate concept from the League, owning a Team
  *within* a League.
- LG-02 (tournament formats) lives *inside* a Season — a Season with
  `schedule_format=tournament_round_robin_then_double_elim` chains a flat
  league phase into a bracket. The `schedule_format` enum is the
  extension point.
- LG-03 (awards), LG-04 (stat updates between Seasons), LG-05 (player
  potential) all key off Season boundaries — the chain established here
  is the trigger surface for those features.

## See also

- [ADR-0004](0004-simulation-data-is-disposable.md) — disposable-data
  precedent for the no-backfill rule on `Match.season`.
- [ADR-0015](0015-schedule-on-demand-no-fixture-rows.md) — the schedule
  is computed on demand, not persisted; this ADR locks the model,
  ADR-0015 locks the algorithm surface.
- CONTEXT.md `League`, `Season`, `Standings` glossary entries.
- PLAN.md LG-01 (this task), LG-01a..g (UX sub-tasks), CAR-01..03
  (manager identity), LG-02 (tournaments), LG-03 (awards), LG-04 (stat
  updates), LG-05 (potential).
