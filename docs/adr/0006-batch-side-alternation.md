# Batch side alternation: orientation paired with the seed, team-position-keyed aggregates

**Status:** accepted

SIM-08 alternates which **Team** plays the red **Side** across the games of a
`BatchSimulator` **Batch run**, so neither team's aggregated stats are biased by
any map-side advantage. Game `k`'s **Orientation** (`flipped`) is a deterministic
function of its game index (`k` odd ⇒ flipped); the RNG is never consumed by the
choice. The reproducible unit of a batch game becomes the pair **(RNG seed,
orientation)**: `round_seeds` entries, the `avg_seeds` / `outlier_seeds` lists,
the session-stashed seeds, `save_games`, and `replay_round` all carry `flipped`
alongside the seed. `_flush_to_db` persists the **actual** sides — a flipped
game's `GameRound.team_red` is the team that really played red, keeping
`PlayerRoundState.team_color` consistent with the round. `run()`'s aggregate keys
are redefined as **team-position keyed**: `red_*` / `blue_*` mean the team passed
as the `team_red` / `team_blue` argument regardless of the side it played, with a
separate `side_advantage` sub-dict exposing the raw red/blue-side signal.

**Considered options:**
(a) *Derive orientation from the seed* (`flipped = seed & 1` or a hash bit) —
rejected: `getrandbits(63)` parity is only ~50/50, never an exact split, and the
avg/outlier subset is non-random so its parity is arbitrary; "even alternation"
becomes impossible to guarantee.
(b) *Persist-only, canonical in-memory replay* — rejected: a replayed/saved
flipped game would no longer reproduce the `run()` result, silently violating the
SIM-07 faithful-replay contract.
(c) *Force a balanced persisted subset* (re-derive orientation from the
save-list index) — rejected: the seed was scored under `run()`'s orientation, so
replaying it under a different one yields a game whose score/events differ from
what `run()` reported — SIM-07 "same seed ⇒ identical game" broken again.
(d) *Keep color-keyed aggregates* — rejected: after alternation `red_*` would be
a 50/50 mix of both teams, breaking the existing per-team win% view/template.

**Consequence:** the SIM-07 replay contract extends from "same seed + rosters +
map" to **"same seed + orientation + rosters + map ⇒ identical game"**;
[ADR-0005](0005-rng-seed-not-state-for-replay.md) and CONTEXT.md (RNG seed,
Replay, Batch run) are amended accordingly. Even alternation is guaranteed at the
**`run()` level** over the full ordered game sequence (even n ⇒ exact 50/50,
odd ⇒ ±1); `save_games` does **not** re-alternate — it replays each carried
`(seed, flipped)` pair faithfully, so the avg/outlier subset may be slightly
side-skewed, which does **not** bias league/team stats because every saved round
records its true sides and all aggregates are team-position keyed. Scope is
`BatchSimulator.run` / `_run_parallel` / `save_games` and the batch view only:
RBS `simulate_match` is untouched (its per-Match colour swap between the two
Rounds of one Match is a separate, already-correct mechanism, and RBS is removed
in SIM-09), and `score_averages` / `score_round_worker` stay out of scope by the
same precedent as SIM-07 (role-keyed aggregates are already self-averaging for
symmetric teams).
