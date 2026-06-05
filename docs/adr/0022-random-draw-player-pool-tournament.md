# Random Draw player-pool tournament — tier-balanced draw, per-Round dynamic roles, borrowed-Player draw Teams

**Status:** Accepted (LG-02x-1, 2026-06-04)

## Context

PLAN.md **LG-02x** ("Player-pool formats — Random Draw / Duos / Trios") was
carved off the LG-02 tournament monolith as "needs its own grill" precisely
because it breaks the assumption every prior tournament slice rests on:
[ADR-0019](0019-tournament-bracket-model.md) made a Tournament's participants
**existing `Team`s**, enrolled at create time, and every format since —
single-elim, the LG-02b best-of-N Series, double-elim, round-robin, RR→DE, and
Swiss ([ADR-0021](0021-double-elimination-bracket.md)) — consumed that
participant-is-a-Team premise verbatim. A Random Draw tournament has **no
pre-set teams**: a pool of individual Players registers, and the *system*
assembles the teams.

The original PLAN.md description was a single line — *"randomize team
assignments once the pool is full, admin reviews/edits, then locks; runs as
Round Robin → Double Elimination."* The LG-02x-1 grilling session
(2026-06-04) superseded that flat-shuffle sketch. It established the domain
language (CONTEXT.md gained **Player pool**, **Drawn-team membership**,
**Random Draw**, **Tier**, and **Role assignment mode** at grilling time) and
forced four design decisions that are load-bearing and surprising without the
session context. This ADR records them. The grill also **deferred** Duos /
Trios and the `TournamentSubGroup` model to **LG-02x-2** — this slice is
single-Player pool only.

Four genuine forks emerged:

1. **How to split the pool into teams** — a flat shuffle, or something that
   balances strength.
2. **How and when roles are assigned** — once, or re-drawn; per what unit of
   play.
3. **What a drawn Team *is* relative to the Players it holds** — does it own
   them, copy them, or borrow them?
4. **Where the new mode lives in the model** — a new `format`, or an
   orthogonal axis.

## Decision

### (a) Tier-balanced draw (straight-tiers + greedy-balance), not a flat shuffle

The draw is **deterministic** and **balances team strength** rather than
shuffling the pool into arbitrary teams. With a pool of `N` Players
(`N % 6 == 0`, `N >= 24`, i.e. `T = N / 6` teams of six):

1. Sort the pool by `overall_rating` **descending**, `player_id` ascending as
   the tiebreak.
2. Form **6 contiguous Tiers** of `T` Players each — Tier 1 is the strongest
   band (the first `T`), Tier 6 the weakest. Every team ends up with exactly
   one Player from each Tier.
3. Within each Tier, in Tier order, assign the strongest-remaining Tier Player
   to the **currently-weakest team** (lowest running total `overall_rating`
   across the Tiers processed so far; `team_index` ascending tiebreak). This
   greedy snake-style sweep keeps the running team totals as close as the pool
   allows.

The draw **consumes no RNG** — same pool, identical split — so a "re-roll" is
idempotent. The variation mechanism is an explicit **admin hand-edit** of a
drawn entry's Tier or team, not re-randomisation. The draw math lives in a new
pure module `matches/draw.py` (`compute_draw`, the frozen `DrawnTeamPlan`
dataclass), Django-free and guarded by `TestNoDjangoImportsLeaked`, on the
`matches/bracket.py` / `standings.py` precedent.

*Why:* a flat shuffle can deal one team five Tier-1 Players and another five
Tier-6 Players, producing lopsided brackets that make the RR→DE results
meaningless as a competitive read. The grill chose balanced teams as the whole
point of a *draw* (the real-venue use case is "split a drop-in crowd into fair
teams"). Determinism makes the result reviewable and reproducible; making the
hand-edit — not a re-roll — the variation knob keeps the admin in control of
any deviation from the balanced split.

### (b) Roles re-assigned every Round; the slot FKs are transient, not the truth

A drawn Team's six `slot_*` role FKs are **re-drawn before every game Round**
— the two Rounds of a single Match get **independent** role assignments — via
a `before_round_hook` the engine passes into `BatchSimulator.simulate_match`.
Two **Role assignment modes** are offered (`Tournament.role_assignment_mode`):

- **`random`** — each team independently shuffles its six Tier-Players into the
  six role slots (two independent shuffles per Round).
- **`per_tier`** — one Tier→slot bijection is drawn per Round and applied to
  **both** teams, so equal-Tier Players always play the same role on both
  sides.

Crucially, the slot FKs are **not the source of truth** for who is on a team —
the hook rewrites them **in memory only** (no `.save()`), and the durable
`(player, tier, drawn_team)` truth lives on `TournamentPlayerEntry`. The
per-Round role draw is **non-deterministic** (a fresh `random.Random()` per
call, **not** the SIM-07 seed chain).

*Why:* a Random Draw competition is about how a *team of mixed-Tier strangers*
performs, and rotating roles each Round both averages out the luck of any one
role-to-Player pairing and exercises Players across positions. Re-drawing
per-Round (rather than per-Match or per-Tournament) is the finest grain the
existing Match→Round structure exposes without changing the simulator's round
loop. Keeping the slot FKs transient — and putting the real membership on
`TournamentPlayerEntry` — means a re-draw is a cheap in-memory swap with no
write amplification and no risk of a half-saved roster, and it lets the draw
result survive any number of role re-rolls untouched.

### (c) Drawn Teams reference borrowed Players; `Player.team` is never reassigned

The draw creates new `Team` rows marked `is_draw_team = True` and points their
slot FKs at **existing Players without ever reassigning `Player.team`**. A
Player drawn into a tournament team is *borrowed*, not moved — the Player still
belongs to their real Team, and `PlayerRoundState` references the real Player
so career stats stay unified across normal play and draw tournaments. This
forces two adjustments:

- **`Team.roster_errors` is relaxed for draw Teams only.** The "all players
  belong to this team" ownership check (`player.team_id != self.pk`) is skipped
  when `is_draw_team` is set. **Every other roster validation still fires** for
  draw Teams — all six slots filled, no duplicate Player, and the
  role-distribution (Scout-only-twice) rule. A non-draw Team with a foreign
  Player still errors exactly as before.
- **Cross-tournament sharing rule.** A Player may be a member of drawn Teams
  across **different** Tournaments simultaneously, but **never two drawn Teams
  in the same Tournament**. This is enforced structurally by a
  `UniqueConstraint(tournament, player)` on `TournamentPlayerEntry`.

`is_draw_team` carries **no FK to the Tournament or Match** — the durable link
lives on `TournamentPlayerEntry` — to avoid a `teams → matches` dependency
inversion (the `teams` app must not import `matches`).

*Why:* copying Players (decision rejected below) would fork career history and
double every roster; reassigning `Player.team` would rip a Player out of their
real Team for the duration of a sandbox draw. Borrowing keeps one Player
identity, one career-stats stream, and a clean teams→matches dependency
direction. The relaxation is surgical — only the ownership rule is unsafe for a
borrowed roster; the duplicate and role-distribution rules are exactly as
load-bearing for a draw Team as for any other, so they stay.

### (d) An orthogonal `team_assembly` field, not a new `format`

`Tournament` gains a new field **`team_assembly`** (`"preset"` default /
`"random_draw"`) **orthogonal** to `format`. A Random Draw Tournament keeps
`format == "round_robin_double_elim"` and runs the **shipped LG-02c RR→DE
bracket unchanged** — `lock_and_build`, `_persist_elim_specs`,
`round_robin_standings`, `build_de_finals_if_rr_finished`, `play_next_node`,
`stage_progress`, and the detail crosstable / cut-labels / DE-finals surfaces
are all byte-unchanged. Pool intake, the draw, the relaxed roster rule, and the
per-Round role hook **all key off `team_assembly == "random_draw"`**.

*Why:* "how the teams were assembled" and "what bracket the teams play" are
genuinely independent questions. Every prior format was added as a new `format`
enum value precisely because each *changed the bracket*; Random Draw changes
**neither** the bracket nor the simulator mechanics — it only changes *who
fills the team slots* before an otherwise-normal RR→DE runs. Modelling it as a
second `format` value would have meant re-deriving the entire RR→DE pipeline
for a "format" that is structurally identical to the existing one, and would
have made the (future) cross-product of assembly × bracket combinatorial in the
`format` enum. An orthogonal axis keeps every RR→DE path single-sourced and
leaves room for Random Draw to compose with other brackets later.

## Rejected alternatives

### A new `format` enum value for Random Draw

Add `("random_draw", "Random draw")` to `Tournament.FORMAT_CHOICES` like every
prior format. Rejected — see (d): Random Draw does not change the bracket or
the sim, only team assembly, so a new `format` would duplicate the RR→DE
pipeline for no structural gain and conflate two orthogonal axes. The
orthogonal `team_assembly` field keeps the RR→DE paths byte-unchanged.

### A flat shuffle of the pool

Randomly partition the pool into teams of six. Rejected — see (a): a flat
shuffle can produce wildly unbalanced teams, which makes the bracket results an
artefact of the deal rather than of play. The straight-tiers + greedy-balance
draw guarantees one Player per Tier per team and near-equal team strength, and
being deterministic it is reviewable and reproducible.

### An ephemeral, non-`Team` draw model

Represent a drawn team as a lightweight non-`Team` structure (a list of Player
ids, or a `TournamentParticipant` with no `Team`). Rejected — the entire RR→DE
engine, the bracket render, `compute_standings`, and `simulate_match` consume
`Team` objects and `team.active_roster`. A non-`Team` draw result would force a
shim at every one of those seams. Reusing `Team` (flagged `is_draw_team`) lets
the bracket run **unchanged**; the only cost is the surgical `roster_errors`
relaxation.

### Copy the Players into fresh per-tournament Player rows

Snapshot each drawn Player as a new `Player` owned by the draw Team. Rejected —
copying forks career history (`PlayerRoundState` would reference the copy, not
the real Player, so a draw tournament's games would not roll up into the
Player's career stats), doubles every roster on every draw, and creates an
orphan-cleanup burden. Borrowing the real Player keeps one identity and one
career-stats stream.

### Reassign `Player.team` (or only move "free agents") into the draw Team

Set `Player.team` to the drawn Team for the tournament's duration. Rejected —
it rips a Player out of their real Team, breaks the moment two tournaments draw
the same Player, and would need an unwind on tournament deletion. A
"move-free-agents-only" variant (only pool Players with no real Team get moved)
was also rejected: it makes the rules differ by Player provenance for no
benefit, and still leaves the borrowed-vs-owned ambiguity for everyone else.
Never reassigning `Player.team` and tracking membership on
`TournamentPlayerEntry` is uniform and reversible.

### Assign roles once (per-Tournament) or per-Match

Draw each Player's role once at draw time and keep it for the whole tournament,
or re-draw per Match instead of per Round. Rejected — a fixed role discards the
"rotate strangers through positions" property that makes a Random Draw
interesting and lets one unlucky role-to-Player pairing decide the whole run.
Per-Match (rather than per-Round) was rejected because the Match already
colour-swaps its two Rounds; re-drawing per-Round is the finest grain the
existing round loop exposes and matches the swap cadence, with no simulator
loop change.

## Consequences

- **Two migrations, no `RunPython`, no backfill**
  ([ADR-0004](0004-simulation-data-is-disposable.md) precedent):
  `matches/0040_tournament_random_draw` adds `Tournament.team_assembly` +
  `Tournament.role_assignment_mode` and creates `TournamentPlayerEntry` (with
  its `unique(tournament, player)` constraint), depending on the new
  `teams/00XX_team_is_draw_team` (a single `AddField`) as a cross-app
  dependency. Existing Tournaments default to `team_assembly="preset"` and are
  byte-unchanged.
- `matches/draw.py` joins `bracket.py` / `standings.py` /
  `schedule_generator.py` as a pure, DB-free, `TestNoDjangoImportsLeaked`-
  guarded module — `compute_draw` (no RNG) plus the two role-bijection builders
  (injected `random.Random`), all unit-testable with zero DB.
- `BatchSimulator.simulate_match` gains one additive keyword-only
  `before_round_hook` and two hook-invocation lines; with the default `None`
  every existing caller (preset tournaments, sandbox, season play) is
  byte-unchanged. **No simulation *mechanics* change** — the hook only swaps
  which Player occupies each role slot before an otherwise-normal Round — so
  there is **no SIM-07 / SIM-08 interaction and no Score Calibration
  re-baseline**.
- `tournament_engine.play_next_node` gains a `team_assembly`-keyed branch and a
  `_build_role_hook` helper; the `preset` path is byte-identical, so every
  RR→DE / RR / Swiss / elim path is untouched.
- New player-pool intake surface mirrors the LG-02a/a-2 Team intake at **Player**
  granularity (select existing, generate via LG-00, CSV via LG-00b's
  `parse_roster_csv` with team-grouping ignored), plus draw / re-roll /
  hand-edit views — all setup-only, all reusing the existing `tournament_lock`
  unchanged to reach `active`.
- **Non-deterministic** (the per-Round role draw and the per-Match sims both
  use fresh RNG); draw-tournament tests assert champion-stamped /
  `state="completed"` and persisted row/constraint shapes, **never** exact
  point totals.
- **Duos / Trios + `TournamentSubGroup` (per-subgroup stat tracking) are
  deferred to LG-02x-2** and will compose this single-Player draw model.

## See also

- [ADR-0019](0019-tournament-bracket-model.md) — the persisted, standalone
  sandbox Tournament / Bracket model whose participant-is-a-Team premise this
  ADR relaxes to a player pool (without touching the bracket).
- [ADR-0021](0021-double-elimination-bracket.md) — the double-elim + RR→DE
  bracket this Random Draw mode runs **unchanged** as its competition format
  (`format` stays `"round_robin_double_elim"`).
- [ADR-0004](0004-simulation-data-is-disposable.md) — disposable-data /
  no-backfill precedent for the two new migrations.
- CONTEXT.md — the **Player pool**, **Drawn-team membership**, **Random Draw**,
  **Tier**, and **Role assignment mode** terms, finalised at the LG-02x-1
  grilling session.
- PLAN.md LG-02x-1 (completed) / LG-02x-2 (deferred Duos / Trios +
  `TournamentSubGroup`).
- Seam contract
  [`.claude/worktrees/lg-02x-1-seam-contract.md`](../../.claude/worktrees/lg-02x-1-seam-contract.md).
