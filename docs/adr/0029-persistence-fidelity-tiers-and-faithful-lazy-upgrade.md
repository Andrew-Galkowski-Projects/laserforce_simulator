# Persistence-fidelity tiers + faithful lazy upgrade via roster-stat snapshot

**Status:** Accepted (GEN-01, 2026-06-26)

## Context

GEN-01 asks for generating a game at one of **three persistence-fidelity tiers**
off the **same RNG seed**, choosing the cheapest tier sufficient for the surface
that requested it:

1. `scores` — `GameRound` / `PlayerRoundState` only (final scoreboard).
2. `combat` — `+` the who-hit-who combat `GameEvent` log (tag / missile / resupply
   / down / elimination) `+` `highlights_json`, but **not** movement.
3. `full` — `+` movement events `+` per-Advance `route` `+` `cell_occupancy_json`
   for round-playback.

The driving motivation is **write volume**: a `full` round writes thousands of
movement `GameEvent` rows, and bulk season play (`play_season_task` / `play_week`)
persists hundreds of rounds nobody may ever open. The vast majority of those
rounds are never replayed — only their final scoreboard feeds Standings.

Two facts about the existing engine shape the design:

- **Movement drives combat.** On the map path, LOS / positioning gate who can tag
  whom (MOVE-01..04). So `BatchSimulator._simulate_round` **must** run the full
  per-tick loop to produce *any* scores — there is no cheaper "scores-only"
  compute path that would still match the full-fidelity scoreboard.
- **`rng_seed` is already persisted** (SIM-07), and replay (`replay_round` /
  `_simulate_round`) is faithful **only while rosters, map config, and Orientation
  are unchanged** — the seed captures randomness, not world state.

The second fact collides with **LG-04 player development**, which **mutates the
live `Player` stat fields in place at every `next_season` rollover**. So
re-simulating a stored season round's seed *after* a rollover reproduces a
**different** game than its stored scoreboard — silently. Any lazy "upgrade a
`scores` round to `full` by re-simulating its seed" scheme has to confront this.

Resolved in the GEN-01 grilling session (2026-06-25 / 2026-06-26).

## Decisions

1. **Persistence tiers, NOT compute tiers — same seed reproduces the identical
   game at every tier.** The three tiers differ **only in what `flush_to_db`
   writes**, never in what the tick loop computes. The tick loop always runs in
   full. The speed/space win is from **skipping DB writes** (and, at `scores`,
   skipping event-buffer collection), not from a cheaper simulation. A genuinely
   fast non-spatial "scores estimator" (a separate statistical model that would
   *not* match full-fidelity scores) is **explicitly out of scope** — it would
   break the same-seed-same-scores guarantee and is a different piece of work.

2. **`GameRound.fidelity` records the persisted tier.** A `CharField` with choices
   `scores` / `combat` / `full`, `default="full"`. No backfill ([ADR-0004](0004-simulation-data-is-disposable.md)
   precedent) — legacy rows already hold events + movement, so `full` is *true*
   for them. Surfaces read it to decide whether an upgrade is needed. Domain term:
   **Persistence fidelity** (CONTEXT.md).

3. **Lazy upgrade in place, made faithful by a roster-stat snapshot — NOT
   verify-then-degrade, NOT re-create.** Every persisted round (every tier) also
   stores `GameRound.roster_snapshot_json`: the per-side, per-player **boosted
   simulation stats** (the `_PlayerData` inputs `_make_players` bakes via
   `stat_for_simulation`) plus `player_id` / `name` / `role` / `team_color`. The
   upgrade primitive `ensure_fidelity(game_round, target)` re-simulates from
   `(rng_seed + roster_snapshot_json + arena_map)` — **reading the snapshot, not
   the live `Team.active_roster`** — so the re-sim is **exact regardless of LG-04
   development or any later roster edit**, and **backfills** the missing higher-tier
   rows onto the existing row (never rewriting the faithful scoreboard) and bumps
   `fidelity`. Idempotent: a no-op when the row already meets the target.

   *Alternatives rejected:* (a) **verify-then-degrade** (re-sim, compare to the
   stored scoreboard, render scores-only on mismatch) — after a single rollover
   *every* prior season round mismatches, so season history would lose its event
   logs wholesale; (b) **re-sim is authoritative** (overwrite the scoreboard from
   the fresh re-sim) — would retroactively shift completed-season Standings when an
   old game is clicked.

4. **Residual: map edits still drift.** The snapshot freezes *roster* inputs, not
   the full map context (sight-line / zone data per cell is far too large to store
   per round). A round's map context is still re-derived from its persisted
   `arena_map` FK at upgrade time, so re-painting an arena's zones/sight-lines
   *after* a round was played can still drift its replay. This stays under the
   **pre-existing `rng_seed` "map config unchanged" caveat** — it is rare
   (editing happens in the map editor, not during play) and snapshotting full map
   context is deferred.

5. **Surface → tier defaults.** **LG-01i live watch** (`play_week_live` RR branch
   + the live playoff `play_specific_node` → `simulate_match`) ships **`full`** —
   you must see the game you are watching, in the same request. **Every other
   path defaults `scores`**: sandbox `simulate_match` / `simulate_single_round_detailed`,
   bulk season `simulate_scheduled_round`, and `save_games`. The view surfaces
   upgrade on demand — `game_round_events` (log + charts + playback) and
   `movement_heatmap` call `ensure_fidelity(gr, "full")`; `missile_log` calls
   `ensure_fidelity(gr, "combat")`; `game_round_detail` stays `scores` (scoreboard
   / MVP already live there). First click pays the re-sim + backfill once; later
   clicks are no-ops.

6. **No Score Calibration re-baseline.** GEN-01 is persistence-only: it changes no
   simulation mechanic, consumes no new RNG inside the tick loop, and the snapshot
   re-runs the *same* loop. The SIM-07/08 internal-determinism contract holds; the
   calibration targets are untouched.

## Consequences

- **Bulk season play stops writing movement.** A season's hundreds of rounds
  persist at `scores` (scoreboard rows only); the movement / event bloat is paid
  only when a human actually opens a round — and only once (the backfill bumps
  `fidelity`, so re-opens are no-ops).

- **Load-bearing invariant (the equivalence test):** a round simulated directly at
  `full` and a round simulated at `scores` then `ensure_fidelity(…, "full")` must
  produce **byte-identical** combat / movement / occupancy / highlight rows (same
  seed + same snapshot). This is the single property that makes the lazy upgrade
  trustworthy and is pinned by an explicit test.

- **Test blast radius (accepted).** Defaulting the sandbox create paths to `scores`
  breaks every test that calls `simulate_match` / `simulate_single_round_detailed`
  directly and then asserts on `GameEvent` / movement / `cell_occupancy_json` /
  `highlights_json` rows. Those tests are updated to call `ensure_fidelity` (or to
  assert through the upgrading view). This is the honest cost of "most everything
  off `scores`."

- **`flush_to_db` gains a `fidelity` gate**, and its event / movement / highlights
  / occupancy write-blocks are factored so both the fresh flush and the backfill
  call one source. At `scores`, `_simulate_round` runs with `event_log=None` (no
  buffer collection); at `combat` / `full` it collects the buffer.

- **A `scores` round with no snapshot cannot be upgraded.** Only legacy rounds lack
  a snapshot, and they are `fidelity="full"` so never reach the re-sim branch; a
  defensive guard renders scores-only if the impossible (`fidelity<full` with
  `roster_snapshot_json=None`) is ever hit.
