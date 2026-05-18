# Hold action with pre-emptive Overwatch fire

**Status:** accepted

MOVE-03 adds a 9th **Action**, `hold`, that puts a player in **Overwatch**: it
anchors to its current **Cell** and automatically fires an **Overwatch shot** at
any enemy that enters — or **Advances** through — its **Line of sight**, with no
prior enemy **Shot** against it. This was split out of the original MOVE-02
scope because it requires action-selection, event, and combat-trigger changes
unrelated to the path cache. The plan mandated its own ADR and a Score
Calibration re-baseline (it is a behavioural change).

## Decisions

**1. Overwatch shot is a full Shot, but is *not* a Reaction shot.** It consumes
one **Shot**, rolls the normal hit chance (elevation/LOS as usual), can
**Tag**/**Down**, chains a **Follow-up shot**, and provokes a **Reaction shot**
from its victim — i.e. it routes through the same tag-resolution path as a
deliberate `tag`. It is nonetheless terminologically distinct from a **Reaction
shot**: a Reaction shot *requires a prior enemy Shot at the defender*; an
Overwatch shot is *pre-emptive* — it fires first, triggered purely by an enemy
entering LoS. CONTEXT.md gets a new **Overwatch shot** term and the **Reaction
shot** entry is amended to state the contrast.
- *Rejected — "no chains" (Overwatch hits but never spawns Follow-up/Reaction):*
  simpler and lower-RNG, but an arbitrary carve-out from the Shot ladder for one
  trigger path; harder to reason about than "it's just a Shot."
- *Rejected — "Hit-only, never Downs":* adds a special case to the shield/down
  path and makes Overwatch a weak deterrent.

**2. Hold carries over, and a Down/respawn force-clears it.** Mirroring
`is_hiding`, a new transient `is_holding` is set on a `hold` roll and stays set
(the player remains in **Overwatch**, **Stationary**) until the player rolls a
non-Hold Action *or* loses a life. The life-loss clear is structural: it hangs
off the existing `BatchSimulator._record_down` helper (which already centralises
`last_downed_time` + `_path_cache = None`), so "every life-loss site clears the
hold" needs no per-site review.
- *Rejected — per-tick only (Overwatch active only on the tick `hold` was
  rolled):* a holder would watch its sightline for ~1 tick at a time, making
  Overwatch a rare twitch rather than a posture and the "moved through LoS"
  guarantee nearly unreachable.

**3. Hold is Stationary.** A holding player does not **Advance**; `is_holding`
joins `is_hiding` / `chosen == "capture_base"` in the `_advance_player`
stationary predicate. A holder that drifts off its sightline is not holding;
`choose_goal_cell` is moot during Hold (as during Hide).

**4. The "moved through LoS" trigger reads the BatchSim path-commitment cache;
Overwatch resolution is `BatchSimulator`-only.** The edge case requires that an
enemy crossing the holder's LoS *between* its Advance start and end cells still
draws ≥1 Overwatch shot, even when neither endpoint is visible. But MOVE-01 /
[ADR-0007](0007-movement-decoupled-from-action.md) deliberately discards the
intermediate route — `movement_trail` stores only `(start, end, tick)` and the
exact path is recomputed at replay. The traversed cells are therefore taken from
the cells `astar_advance_cached` pops off its committed route this tick
([ADR-0008](0008-path-commitment-via-goal-keyed-cache.md), **Path
commitment**). That cache exists in `BatchSimulator` only, so Overwatch
*resolution* is BatchSim-only. The 9-slot Action array and the per-role weight
redistribution are shared by both simulators (all weights stay ≥ 0;
`random.choices` rejects negatives), but RBS treats `hold` as a Stationary
no-op. This mirrors the MOVE-02 scoping precedent exactly: RBS is DB-bound dead
code removed by the immediately-following SIM-09, so investing in RBS Overwatch
is wasted, and the brief RBS/BatchSim divergence breaks no existing guarantee
(no RBS≡BatchSim identity contract exists).
- *Rejected — recompute a deterministic A* segment for the LoS check:* a clean
  read on "route is derivable from start+end" and would also work in RBS, but
  pays a second full A* per moving player per tick on the perf-critical engine,
  partly undoing the MOVE-02 win.
- *Rejected — endpoint + midpoint sampling:* cheap but provably misses a fast
  mover that clips a sightline between samples — fails the stated ≥1-shot
  guarantee for long Advances.
- *Rejected — implement Overwatch in RBS too (recomputed segment):* contradicts
  the MOVE-02 precedent and invests in an engine SIM-09 deletes within one task.

## Consequences

Seeded games differ from pre-MOVE-03 (a new Action slot reweights every role's
distribution; Overwatch shots add Tags/Downs and consume RNG through the tag
path). No backfill ([ADR-0004](0004-simulation-data-is-disposable.md)). This
delta is **absorbed by the post-MOVE-01 Score Calibration re-baseline that is
already pending** (see `matches/CLAUDE.md`) — MOVE-03 creates **no new**
re-baseline obligation. The contract MOVE-03 must hold is the unchanged
SIM-07/SIM-08 *internal* determinism (same seed + Orientation + rosters + map ⇒
identical game, serial == parallel, faithful **Replay**): the Overwatch
LoS-cross check and `is_holding` carry-over consume no RNG; only the resulting
Overwatch shot does, and it does so through the existing deterministic tag path.

Hard to reverse: removing the slot later again reweights every role and
re-moves every seeded outcome, re-invalidating the calibrated targets — the same
irreversibility as
[ADR-0007](0007-movement-decoupled-from-action.md)/[ADR-0008](0008-path-commitment-via-goal-keyed-cache.md).
Domain terms **Hold**, **Overwatch**, **Overwatch shot** (and the **Reaction
shot** contrast) are defined in CONTEXT.md.
