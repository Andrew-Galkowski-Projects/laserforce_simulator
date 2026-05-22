# Server-emitted highlight-source events: `nuke_cancelled` + `medic_reset`

**Status:** Accepted (RV-02, 2026-05-21)

## Context

RV-02 surfaces an auto-flagged **Highlights** tab on the events page. Two of its
highlight kinds describe moments the simulator already produces *mechanically*
but does **not** record in the event log, so a downstream builder has no
single-source signal to flag:

1. **Nuke cancellation** — a nuke fired but dropped without detonating because
   its firing Commander was **Down**ed (**Shields** to zero) or eliminated
   during the **Fuse window** (the MECH-05 rule). A Down clears the Commander's
   `special_active_until` at `matches/simulation.py:2080`, which disarms the
   pending nuke (`nuke_armed = special_active_until >= complete_time` → False);
   the nuke then drains at `~L1481` and the implicit `else` of the arm check
   silently declines to detonate. The only log trace of the whole episode is
   the earlier activation row (`event_type="special"`, "… activates nuke").

2. **Medic reset chain** — a **Medic** Downed two or more times in one unbroken
   recovery (re-Downed while still in the **Respawn cooldown**, before returning
   to fully active). The simulator tracks `last_downed_time` and
   `times_tagged_in_reset_window` but emits no event marking the chain, and the
   per-chain count is **not** the round-cumulative counter.

Both are the shape RES-03 / [ADR-0011](0011-missile-event-split.md) faced for a
fired-but-cancelled **Missile**: the fact lives in the simulator at a specific
tick and must be emitted there, never inferred downstream from the absence (or
co-incidence) of other rows.

## Decision

Emit **two new server-emitted event types** at the simulator's shared life-loss
chokepoint, `BatchSimulator._record_down` (already the structural hook every
tag / follow-up / reaction / missile / nuke life-loss site calls). Both feed the
RV-02 highlights builder, which reads them single-source from the event log.

### 1. `event_type="nuke_cancelled"`

Emitted at the **Down/disarm tick** — the dramatic "nuke stopped" moment — with
the firing Commander as `actor`, when a Commander with a pending nuke is Downed
or eliminated during its fuse.

- **The cancelled nuke is left in `pending_nukes`.** A transient
  `PendingNuke.cancel_logged` flag is set so the existing drain-`else` at
  `~L1485` does **not** emit a duplicate. The queue entry is *not* removed at the
  Down tick: `_apply_nuke_reaction_flags(all_alive, pending_nukes)` reads the
  queue, so removing it early would stop enemy nuke-reaction sooner and **drift
  seeded games**. RV-02 is emit-only — zero mechanical change, no re-baseline.
- The drain-`else` remains a defensive fallback: it emits `nuke_cancelled` only
  when `not n.cancel_logged` (covers any disarm path that doesn't pass through a
  life-loss site, should one ever exist).

### 2. `event_type="medic_reset"`

Emitted the moment a Medic's unbroken down-chain reaches **2 downs** (the first
re-Down before recovery), with the Medic as `actor`.

- A transient `PlayerState` chain counter is incremented in `_record_down`
  **iff** the player was not fully active at the Down tick (`not
  is_active_at(second)` — checked **before** `last_downed_time` is stamped, since
  stamping changes `is_active_at`). The counter resets to 0 when the player
  returns to fully active. One `medic_reset` highlight per chain.

### Shared mechanics

3. **One choices migration.** Both values are added to `GameEvent.EVENT_TYPES`,
   producing a `choices`-only migration (no schema change beyond the choices
   list) — the same kind RES-03 produced at `0025`. The RV-02
   `GameRound.highlights_json` `AddField` rides the same migration.

4. **Highlights read the log + result, single-source.** Every RV-02 highlight
   kind is a single read: nuke detonations (`special`, "nuke detonates"), nuke
   cancellations (`nuke_cancelled`), medic reset chains (`medic_reset`), first
   elimination (first `elimination`), the scoring burst (scan of point-bearing
   rows — base captures included in the sum), and the team-elimination moment
   (from `GameRound.red_team_eliminated` / `blue_team_eliminated` +
   `eliminated_at`; the long-dead `team_elimination` event choice is **not**
   resurrected). **Base captures are not a highlight kind** — they are routine
   point-grabs left to the events-log timeline (a "Base Capture" type filter
   was added so they render); their points still feed the scoring burst.

## Rejected alternatives

**Infer cancellation / reset from the log in the builder.** Pair activations
with detonations (cancellation) or reconstruct each player's down/cooldown
timeline from tag→down sequences (reset chain). No simulator change, no
migration. Rejected: fragile (fuse-window length, concurrent nukes, duplicating
the simulator's `is_active_at` / arm-check logic) and contrary to the
single-source / server-emitted convention from RES-02 (`metadata["sp"]`) and
RES-03 (`metadata["friendly_fire"]`). The simulator knows the truth at the tick;
downstream code must not re-derive it.

**Emit `nuke_cancelled` at the resolution tick** (in the drain-`else` at
`complete_time`). Single clean site, no down-site involvement. Rejected by the
feature owner: the highlight should land at the moment the Commander falls and
the nuke dies, not a few ticks later when it would have detonated — the former
is the dramatic beat a highlight reel wants.

**Remove the cancelled nuke from `pending_nukes` at the Down tick.** Tidier
(the drain loop never re-sees it). Rejected: `_apply_nuke_reaction_flags` reads
`pending_nukes`, so early removal stops enemy nuke-reaction during
`[down_tick, complete_time)`, changing goal selection and **drifting seeded
games** — converting an emit-only analytics feature into a behavioural change
with a re-baseline obligation. The `cancel_logged` flag achieves de-duplication
without touching the queue.

**Detonations only / cumulative `times_tagged_in_reset_window` for the medic
highlight.** Drop cancellations (smallest change) or flag once when the
round-total reset-window tag count hits 2. Both rejected by the feature owner:
the cancelled nuke and the spawn-camped medic are exactly the dramatic moments
the tab exists to surface, and "2 times before coming back up" is explicitly a
*per-recovery chain*, not a round total.

**Reuse an existing event type with a metadata discriminator** (e.g.
`special` + `metadata["nuke"]="cancelled"`). Avoids new strings. Rejected for
the same reason ADR-0011 rejected a `metadata["phase"]` discriminator: the
event-type-keyed filter UI, chart scanners, and tests reward distinct event
types; a discriminator on a shared type is the harder shape to filter and
maintain.

## Consequences

**Persisted-event delta: zero rows backfilled.** Per
[ADR-0004](0004-simulation-data-is-disposable.md), simulation data is
disposable. Rounds simulated before RV-02 carry neither new event and show no
nuke-cancellation / medic-reset highlight; rounds simulated after RV-02 get them
from their first tick. Old rows are neither rewritten nor deleted.

**Seeded games are unchanged in mechanics.** Both emits happen at events that
already occur (the Down, the disarm); they draw no RNG, change no
tag/hit resolution, move no timer, and (per the leave-in-queue decision above)
do not alter `pending_nukes` membership or the reset-window chain's effect on
any decision (the chain counter is read only to emit). The SIM-07 / SIM-08
*internal* determinism contract holds in form (same seed + Orientation +
rosters + map ⇒ identical game; serial == parallel; faithful **Replay**). **No
Score Calibration re-baseline** is triggered (unlike MOVE-02 / MOVE-03 / MOVE-04).

**`_record_down` gains emit access.** To emit at the chokepoint, `_record_down`
(today a `@staticmethod` stamping `last_downed_time` / clearing `_path_cache` /
`is_holding` / action-driven `_committed_goal`) needs access to the event log
and the pending-nuke queue. The exact signature (extra params vs. instance
method vs. per-run state) is settled in the RV-02 seam contract, not here; the
invariant is that every life-loss site routes through it so "every cancellation
/ reset is logged" stays structural rather than per-site review.

**Highlights are computed once at round completion.** The RV-02 highlight set is
built in `_flush_to_db` from the in-memory `events` list + the `result` dict and
persisted to `GameRound.highlights_json`, so the tab is a cheap read (RES-04
`cell_occupancy_json` precedent, not RV-01 view-time derivation). The
persist-vs-derive trade-off and the `highlights_json` shape are settled in the
RV-02 PLAN.md note and seam contract.
